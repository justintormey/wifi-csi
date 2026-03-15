"""MQTT listener for CSI data from ESP32-S3 receivers.

Subscribes to ``csi/#`` topics and deserializes binary payloads into
CsiPacket objects.  Runs paho-mqtt's network loop on a background thread
and pushes parsed packets into an asyncio queue for the pipeline to consume.

Topic format: ``csi/{floor_id}/{rx_mac}``
Payload: 478-byte binary (see :mod:`backend.collector.csi_packet`).

Features:
- Topic-based routing with floor/mac extraction and packet validation
- Per-sensor metrics (packets/sec, latency, error rate)
- Out-of-order packet reordering via per-sensor timestamp buffer
- Sensor dropout detection (configurable timeout, default 5s)
"""

from __future__ import annotations

import asyncio
import heapq
import logging
import time
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import paho.mqtt.client as mqtt

from backend.collector.csi_packet import CsiPacket, MalformedPacketError

logger = logging.getLogger(__name__)


# ── Topic parsing ──────────────────────────────────────────────────


@dataclass(frozen=True)
class TopicInfo:
    """Parsed MQTT topic: ``csi/{floor_id}/{rx_mac}``."""

    floor_id: int
    rx_mac: str


def parse_topic(topic: str) -> Optional[TopicInfo]:
    """Parse ``csi/{floor_id}/{rx_mac}`` and return TopicInfo, or None."""
    parts = topic.split("/")
    if len(parts) != 3 or parts[0] != "csi":
        return None
    try:
        floor_id = int(parts[1])
    except ValueError:
        return None
    rx_mac = parts[2]
    if not rx_mac:
        return None
    return TopicInfo(floor_id=floor_id, rx_mac=rx_mac)


# ── Per-sensor metrics ────────────────────────────────────────────


@dataclass
class SensorMetrics:
    """Live metrics for a single sensor (identified by rx_mac)."""

    rx_mac: str
    floor_id: int = 0
    packets_total: int = 0
    packets_malformed: int = 0
    last_seen: float = 0.0
    first_seen: float = 0.0
    _recent_timestamps: list[float] = field(default_factory=list, repr=False)

    # Sliding window for packets/sec calculation (last N wall-clock times)
    _RATE_WINDOW_S: float = 5.0

    @property
    def packets_per_sec(self) -> float:
        """Average packets/sec over the last rate window."""
        if len(self._recent_timestamps) < 2:
            return 0.0
        window_start = self._recent_timestamps[0]
        window_end = self._recent_timestamps[-1]
        duration = window_end - window_start
        if duration <= 0:
            return 0.0
        return (len(self._recent_timestamps) - 1) / duration

    @property
    def error_rate(self) -> float:
        """Fraction of malformed packets (0.0–1.0)."""
        total = self.packets_total + self.packets_malformed
        if total == 0:
            return 0.0
        return self.packets_malformed / total

    @property
    def latency_s(self) -> float:
        """Seconds since last packet (0.0 if never seen)."""
        if self.last_seen == 0.0:
            return 0.0
        return time.monotonic() - self.last_seen

    def record_packet(self, wall_time: float) -> None:
        """Record a successfully deserialized packet."""
        self.packets_total += 1
        self.last_seen = wall_time
        if self.first_seen == 0.0:
            self.first_seen = wall_time
        # Trim rate window
        self._recent_timestamps.append(wall_time)
        cutoff = wall_time - self._RATE_WINDOW_S
        while self._recent_timestamps and self._recent_timestamps[0] < cutoff:
            self._recent_timestamps.pop(0)

    def record_malformed(self) -> None:
        """Record a malformed packet from this sensor."""
        self.packets_malformed += 1


# ── Reorder buffer (per-sensor) ──────────────────────────────────


@dataclass
class _ReorderEntry:
    """Heap entry for timestamp-ordered reordering."""

    timestamp_us: int
    seq: int  # tie-breaker for heapq stability
    packet: CsiPacket = field(compare=False)

    def __lt__(self, other: _ReorderEntry) -> bool:
        if self.timestamp_us != other.timestamp_us:
            return self.timestamp_us < other.timestamp_us
        return self.seq < other.seq


class _SensorReorderBuffer:
    """Small per-sensor buffer that reorders packets by timestamp_us.

    Packets are held until they are older than ``window_us`` relative to the
    newest packet seen for this sensor, then flushed in timestamp order.
    """

    def __init__(self, window_us: int = 50_000) -> None:
        self._window_us = window_us
        self._heap: list[_ReorderEntry] = []
        self._seq = 0
        self._max_ts: int = 0

    def push(self, packet: CsiPacket) -> list[CsiPacket]:
        """Insert a packet; return any packets ready to be flushed."""
        self._seq += 1
        entry = _ReorderEntry(
            timestamp_us=packet.timestamp_us,
            seq=self._seq,
            packet=packet,
        )
        heapq.heappush(self._heap, entry)
        self._max_ts = max(self._max_ts, packet.timestamp_us)

        # Flush everything older than (max_ts - window)
        cutoff = self._max_ts - self._window_us
        flushed: list[CsiPacket] = []
        while self._heap and self._heap[0].timestamp_us <= cutoff:
            flushed.append(heapq.heappop(self._heap).packet)
        return flushed

    def flush_all(self) -> list[CsiPacket]:
        """Drain all remaining packets in timestamp order."""
        result: list[CsiPacket] = []
        while self._heap:
            result.append(heapq.heappop(self._heap).packet)
        return result

    def __len__(self) -> int:
        return len(self._heap)


# ── Main listener ─────────────────────────────────────────────────


class MqttListener:
    """Asynchronous MQTT listener for CSI binary packets.

    Bridges paho-mqtt's threaded network loop to asyncio via a queue.

    Args:
        broker_host: MQTT broker hostname or IP.
        broker_port: MQTT broker port.
        subscribe_pattern: MQTT topic pattern (default ``csi/#``).
        keepalive: MQTT keepalive interval in seconds.
        qos: MQTT QoS level for subscriptions.
        queue_maxsize: Max items in the async packet queue.  Oldest packets
            are dropped when the queue is full (backpressure).
        sensor_timeout_s: Seconds without data before a sensor is considered
            offline and a dropout alert is emitted.
        reorder_window_us: Microsecond window for per-sensor out-of-order
            packet reordering.  Set to 0 to disable reordering.
        on_sensor_dropout: Optional callback invoked (on the MQTT thread)
            when a sensor goes offline.  Receives (rx_mac, seconds_offline).
    """

    def __init__(
        self,
        broker_host: str = "localhost",
        broker_port: int = 1883,
        subscribe_pattern: str = "csi/#",
        keepalive: int = 60,
        qos: int = 0,
        queue_maxsize: int = 1000,
        sensor_timeout_s: float = 5.0,
        reorder_window_us: int = 50_000,
        on_sensor_dropout: Optional[Callable[[str, float], None]] = None,
    ) -> None:
        self._broker_host = broker_host
        self._broker_port = broker_port
        self._subscribe_pattern = subscribe_pattern
        self._keepalive = keepalive
        self._qos = qos
        self._queue_maxsize = queue_maxsize
        self._sensor_timeout_s = sensor_timeout_s
        self._reorder_window_us = reorder_window_us
        self._on_sensor_dropout = on_sensor_dropout

        self._client: Optional[mqtt.Client] = None
        self._queue: Optional[asyncio.Queue[CsiPacket]] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        # Global stats
        self.packets_received: int = 0
        self.packets_dropped: int = 0
        self.malformed_packets: int = 0
        self.topic_parse_errors: int = 0
        self.topic_mismatch_warnings: int = 0

        # Per-sensor state (keyed by rx_mac, accessed on MQTT thread only)
        self._sensor_metrics: dict[str, SensorMetrics] = {}
        self._reorder_buffers: dict[str, _SensorReorderBuffer] = {}
        self._sensors_offline: set[str] = set()

        # Dropout detection timer
        self._dropout_timer: Optional[threading.Timer] = None

    @property
    def queue(self) -> asyncio.Queue[CsiPacket]:
        """The async queue where parsed CsiPackets are placed."""
        if self._queue is None:
            raise RuntimeError("MqttListener not started — call start() first")
        return self._queue

    @property
    def is_connected(self) -> bool:
        """Whether the MQTT client is currently connected."""
        return self._client is not None and self._client.is_connected()

    @property
    def sensor_metrics(self) -> dict[str, SensorMetrics]:
        """Per-sensor metrics (read-only snapshot safe from any thread)."""
        return dict(self._sensor_metrics)

    def start(self, loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
        """Connect to the broker and begin receiving CSI packets.

        Args:
            loop: The asyncio event loop to push packets into.
                  Defaults to the running loop.
        """
        self._loop = loop or asyncio.get_event_loop()
        self._queue = asyncio.Queue(maxsize=self._queue_maxsize)

        self._client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        )
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        self._client.on_disconnect = self._on_disconnect

        logger.info(
            "Connecting to MQTT broker at %s:%d (topic: %s)",
            self._broker_host,
            self._broker_port,
            self._subscribe_pattern,
        )

        self._client.connect_async(
            self._broker_host,
            self._broker_port,
            keepalive=self._keepalive,
        )
        self._client.loop_start()

        # Start periodic dropout checker
        self._start_dropout_checker()

    def stop(self) -> None:
        """Disconnect and stop the background network loop."""
        # Stop dropout checker
        if self._dropout_timer is not None:
            self._dropout_timer.cancel()
            self._dropout_timer = None

        # Flush remaining reorder buffers
        for mac, buf in self._reorder_buffers.items():
            for packet in buf.flush_all():
                if self._loop is not None and self._queue is not None:
                    self._loop.call_soon_threadsafe(self._enqueue, packet)

        if self._client is not None:
            self._client.loop_stop()
            self._client.disconnect()
            logger.info(
                "MQTT disconnected. Received: %d, Dropped: %d, Malformed: %d, "
                "TopicErrors: %d, Sensors: %d",
                self.packets_received,
                self.packets_dropped,
                self.malformed_packets,
                self.topic_parse_errors,
                len(self._sensor_metrics),
            )
            self._client = None

    # ── paho-mqtt callbacks (run on the network thread) ────────────

    def _on_connect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: mqtt.ConnectFlags,
        rc: mqtt.ReasonCode,
        properties: Optional[mqtt.Properties] = None,
    ) -> None:
        """Called when the client connects to the broker."""
        if rc == 0 or str(rc) == "Success":
            logger.info("MQTT connected, subscribing to %s", self._subscribe_pattern)
            client.subscribe(self._subscribe_pattern, qos=self._qos)
        else:
            logger.error("MQTT connection failed: %s", rc)

    def _on_message(
        self,
        client: mqtt.Client,
        userdata: Any,
        msg: mqtt.MQTTMessage,
    ) -> None:
        """Called for each incoming MQTT message — runs on the network thread."""
        now = time.monotonic()

        # Parse topic: csi/{floor_id}/{rx_mac}
        topic_info = parse_topic(msg.topic)
        if topic_info is None:
            self.topic_parse_errors += 1
            if self.topic_parse_errors <= 10:
                logger.warning("Unparseable CSI topic: %s", msg.topic)
            return

        rx_mac = topic_info.rx_mac

        # Deserialize binary payload
        try:
            packet = CsiPacket.from_bytes(msg.payload)
        except MalformedPacketError as exc:
            self.malformed_packets += 1
            self._get_sensor_metrics(rx_mac, topic_info.floor_id).record_malformed()
            if self.malformed_packets <= 10:
                logger.warning(
                    "Malformed CSI packet on %s: %s", msg.topic, exc
                )
            return

        # Validate topic vs packet data
        packet_floor = packet.floor_id + 1  # packet is 0-based, topic is 1-based
        if packet_floor != topic_info.floor_id:
            self.topic_mismatch_warnings += 1
            if self.topic_mismatch_warnings <= 10:
                logger.warning(
                    "Topic/packet floor mismatch: topic=%d, packet=%d (mac=%s)",
                    topic_info.floor_id,
                    packet_floor,
                    rx_mac,
                )

        self.packets_received += 1

        # Update per-sensor metrics
        metrics = self._get_sensor_metrics(rx_mac, topic_info.floor_id)
        metrics.record_packet(now)

        # Mark sensor as back online if it was offline
        if rx_mac in self._sensors_offline:
            self._sensors_offline.discard(rx_mac)
            logger.info("Sensor %s back online (floor %d)", rx_mac, topic_info.floor_id)

        # Reorder buffer
        if self._reorder_window_us > 0:
            buf = self._get_reorder_buffer(rx_mac)
            flushed = buf.push(packet)
            for p in flushed:
                if self._loop is not None and self._queue is not None:
                    self._loop.call_soon_threadsafe(self._enqueue, p)
        else:
            # No reordering — push directly
            if self._loop is not None and self._queue is not None:
                self._loop.call_soon_threadsafe(self._enqueue, packet)

    def _on_disconnect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: mqtt.DisconnectFlags,
        rc: mqtt.ReasonCode,
        properties: Optional[mqtt.Properties] = None,
    ) -> None:
        """Called when disconnected — paho-mqtt will auto-reconnect."""
        if rc != 0 and str(rc) != "Success":
            logger.warning("MQTT disconnected unexpectedly: %s (will retry)", rc)

    # ── Internal ──────────────────────────────────────────────────

    def _enqueue(self, packet: CsiPacket) -> None:
        """Put a packet on the async queue, dropping if full."""
        assert self._queue is not None
        try:
            self._queue.put_nowait(packet)
        except asyncio.QueueFull:
            self.packets_dropped += 1
            # Discard oldest to make room
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                self._queue.put_nowait(packet)
            except asyncio.QueueFull:
                pass

    def _get_sensor_metrics(self, rx_mac: str, floor_id: int = 0) -> SensorMetrics:
        """Get or create per-sensor metrics."""
        if rx_mac not in self._sensor_metrics:
            self._sensor_metrics[rx_mac] = SensorMetrics(
                rx_mac=rx_mac, floor_id=floor_id
            )
        return self._sensor_metrics[rx_mac]

    def _get_reorder_buffer(self, rx_mac: str) -> _SensorReorderBuffer:
        """Get or create per-sensor reorder buffer."""
        if rx_mac not in self._reorder_buffers:
            self._reorder_buffers[rx_mac] = _SensorReorderBuffer(
                window_us=self._reorder_window_us
            )
        return self._reorder_buffers[rx_mac]

    # ── Sensor dropout detection ─────────────────────────────────

    def _start_dropout_checker(self) -> None:
        """Schedule periodic dropout checks."""
        self._check_sensor_dropout()

    def _check_sensor_dropout(self) -> None:
        """Check all known sensors for dropout (>timeout since last packet)."""
        now = time.monotonic()
        for mac, metrics in self._sensor_metrics.items():
            if metrics.last_seen == 0.0:
                continue
            age = now - metrics.last_seen
            if age > self._sensor_timeout_s and mac not in self._sensors_offline:
                self._sensors_offline.add(mac)
                logger.warning(
                    "Sensor dropout: %s (floor %d) — no data for %.1fs",
                    mac,
                    metrics.floor_id,
                    age,
                )
                if self._on_sensor_dropout is not None:
                    try:
                        self._on_sensor_dropout(mac, age)
                    except Exception:
                        logger.exception("Error in sensor dropout callback")

        # Reschedule (check every 1s)
        self._dropout_timer = threading.Timer(1.0, self._check_sensor_dropout)
        self._dropout_timer.daemon = True
        self._dropout_timer.start()
