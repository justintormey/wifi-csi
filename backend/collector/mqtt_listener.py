"""MQTT listener for CSI data from ESP32-S3 receivers.

Subscribes to ``csi/#`` topics and deserializes binary payloads into
CsiPacket objects.  Runs paho-mqtt's network loop on a background thread
and pushes parsed packets into an asyncio queue for the pipeline to consume.

Topic format: ``csi/{floor_id}/{rx_mac}``
Payload: 478-byte binary (see :mod:`backend.collector.csi_packet`).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Optional

import paho.mqtt.client as mqtt

from backend.collector.csi_packet import CsiPacket, MalformedPacketError

logger = logging.getLogger(__name__)


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
    """

    def __init__(
        self,
        broker_host: str = "localhost",
        broker_port: int = 1883,
        subscribe_pattern: str = "csi/#",
        keepalive: int = 60,
        qos: int = 0,
        queue_maxsize: int = 1000,
    ) -> None:
        self._broker_host = broker_host
        self._broker_port = broker_port
        self._subscribe_pattern = subscribe_pattern
        self._keepalive = keepalive
        self._qos = qos
        self._queue_maxsize = queue_maxsize

        self._client: Optional[mqtt.Client] = None
        self._queue: Optional[asyncio.Queue[CsiPacket]] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        # Stats
        self.packets_received: int = 0
        self.packets_dropped: int = 0
        self.malformed_packets: int = 0

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

    def stop(self) -> None:
        """Disconnect and stop the background network loop."""
        if self._client is not None:
            self._client.loop_stop()
            self._client.disconnect()
            logger.info(
                "MQTT disconnected. Received: %d, Dropped: %d, Malformed: %d",
                self.packets_received,
                self.packets_dropped,
                self.malformed_packets,
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
        try:
            packet = CsiPacket.from_bytes(msg.payload)
        except MalformedPacketError as exc:
            self.malformed_packets += 1
            if self.malformed_packets <= 10:
                logger.warning(
                    "Malformed CSI packet on %s: %s", msg.topic, exc
                )
            return

        self.packets_received += 1

        # Push into the asyncio queue (thread-safe)
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
