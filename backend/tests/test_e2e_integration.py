"""End-to-end integration test: MQTT → Backend → WebSocket.

Validates the full data pipeline by publishing binary CSI packets to a
local MQTT broker (Mosquitto) and verifying that TrackingFrame payloads
appear on the WebSocket endpoint.  Measures per-hop and total latency.

Requirements:
    - Mosquitto (or compatible MQTT broker) running on localhost:1883
    - No real hardware needed — this script mimics firmware output

Run:
    pytest backend/tests/test_e2e_integration.py -v -s
    # Or standalone:
    python -m backend.tests.test_e2e_integration
"""

from __future__ import annotations

import asyncio
import json
import math
import struct
import time
from typing import Any

import numpy as np
import paho.mqtt.client as mqtt
import pytest
import websockets

from backend.collector.csi_packet import (
    NUM_SUBCARRIERS,
    PACKET_SIZE,
    CsiPacket,
)

# ── Configuration ─────────────────────────────────────────────────

MQTT_BROKER_HOST = "localhost"
MQTT_BROKER_PORT = 1883
BACKEND_WS_URL = "ws://localhost:8000/ws/tracking"
BACKEND_HTTP_URL = "http://localhost:8000"

# Firmware-matching sensor MACs (from sensors.yaml)
TX_MAC = "aa:bb:cc:dd:01:00"
RX_MAC = "aa:bb:cc:dd:01:01"
FLOOR_ID = 1
MQTT_TOPIC = f"csi/{FLOOR_ID}/{RX_MAC}"

# Timing
PACKET_RATE_HZ = 100
PACKET_INTERVAL_S = 1.0 / PACKET_RATE_HZ
WARMUP_PACKETS = 50  # Let pipeline settle before measuring
MEASURE_PACKETS = 200  # Packets to send during measurement
WEBSOCKET_TIMEOUT_S = 10.0  # Max time to wait for first WS frame


# ── Synthetic packet generation ───────────────────────────────────


def make_firmware_packet(
    timestamp_us: int,
    breathing_freq: float = 0.25,
    base_amplitude: float = 50.0,
    seed: int | None = None,
) -> bytes:
    """Build a 478-byte binary CSI packet matching the firmware format.

    This produces the exact byte layout the ESP32-S3 firmware sends over
    MQTT: <Q6s6sbB228h> (little-endian).
    """
    rng = np.random.default_rng(seed)
    t_s = timestamp_us / 1e6

    # Simulate CSI amplitude with breathing modulation
    base = base_amplitude + 3.0 * np.sin(2.0 * math.pi * breathing_freq * t_s)
    noise = rng.normal(0, 1.5, NUM_SUBCARRIERS)
    amplitude = np.maximum(base + noise, 1.0)

    # Small random phase offsets
    phase = rng.uniform(-0.1, 0.1, NUM_SUBCARRIERS)
    I = (amplitude * np.cos(phase)).astype(np.int16)
    Q = (amplitude * np.sin(phase)).astype(np.int16)

    # Interleave I/Q
    iq_flat = np.empty(NUM_SUBCARRIERS * 2, dtype=np.int16)
    iq_flat[0::2] = I
    iq_flat[1::2] = Q

    # Pack header
    header = struct.pack(
        "<Q6s6sbB",
        timestamp_us,
        bytes(int(x, 16) for x in TX_MAC.split(":")),
        bytes(int(x, 16) for x in RX_MAC.split(":")),
        -40,  # RSSI
        FLOOR_ID,
    )
    iq_bytes = struct.pack(f"<{NUM_SUBCARRIERS * 2}h", *iq_flat)

    packet = header + iq_bytes
    assert len(packet) == PACKET_SIZE, f"Bad packet size: {len(packet)}"
    return packet


# ── MQTT publisher (firmware simulator) ───────────────────────────


class FirmwareSimulator:
    """Publishes binary CSI packets to MQTT at 100 Hz, mimicking firmware."""

    def __init__(self) -> None:
        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2, client_id="e2e-firmware-sim"
        )
        self.connected = False
        self.packets_sent = 0
        self.publish_timestamps: list[float] = []

    def connect(self) -> None:
        self.client.on_connect = self._on_connect
        self.client.connect(MQTT_BROKER_HOST, MQTT_BROKER_PORT, keepalive=60)
        self.client.loop_start()
        # Wait for connection
        deadline = time.monotonic() + 5.0
        while not self.connected and time.monotonic() < deadline:
            time.sleep(0.05)
        if not self.connected:
            raise ConnectionError(
                f"Could not connect to MQTT broker at "
                f"{MQTT_BROKER_HOST}:{MQTT_BROKER_PORT}"
            )

    def _on_connect(self, client, userdata, flags, rc, properties=None) -> None:
        self.connected = rc == 0

    def publish_burst(self, count: int, rate_hz: float = PACKET_RATE_HZ) -> None:
        """Publish `count` packets at the given rate."""
        interval = 1.0 / rate_hz
        t0_us = int(time.time() * 1e6)

        for i in range(count):
            timestamp_us = t0_us + int(i * interval * 1e6)
            packet = make_firmware_packet(timestamp_us, seed=i)
            wall_time = time.monotonic()
            self.client.publish(MQTT_TOPIC, packet, qos=0)
            self.publish_timestamps.append(wall_time)
            self.packets_sent += 1

            # Pace to real-time
            elapsed = time.monotonic() - wall_time
            sleep_time = interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

    def disconnect(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()


# ── WebSocket consumer ────────────────────────────────────────────


async def collect_ws_frames(
    url: str, count: int, timeout: float = WEBSOCKET_TIMEOUT_S
) -> list[dict[str, Any]]:
    """Connect to the backend WebSocket and collect `count` TrackingFrames."""
    frames: list[dict[str, Any]] = []
    recv_times: list[float] = []

    async with websockets.connect(url) as ws:
        deadline = asyncio.get_event_loop().time() + timeout + (count * 0.2)
        while len(frames) < count:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                break
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=remaining)
                recv_times.append(time.monotonic())
                data = json.loads(msg)
                data["_recv_time"] = recv_times[-1]
                frames.append(data)
            except asyncio.TimeoutError:
                break

    return frames


# ── Latency analysis ─────────────────────────────────────────────


def analyze_latency(
    publish_timestamps: list[float],
    ws_frames: list[dict[str, Any]],
    warmup_packets: int,
) -> dict[str, float]:
    """Compute end-to-end latency statistics.

    Since the backend broadcasts at 10 Hz (aggregating ~10 CSI packets per
    frame), we measure from the first publish timestamp in each broadcast
    window to the WebSocket receive time.
    """
    if not ws_frames:
        return {"error": "No WebSocket frames received"}

    # Each WS frame covers ~100ms of CSI data (10 packets at 100 Hz).
    # The first publish in that window is the "start" of processing.
    # We estimate latency as: ws_recv_time - publish_time_of_window_start.
    packets_per_frame = max(1, PACKET_RATE_HZ // 10)  # 10
    latencies_ms: list[float] = []

    for i, frame in enumerate(ws_frames):
        # Map this WS frame to the corresponding publish window
        pub_idx = warmup_packets + (i * packets_per_frame)
        if pub_idx >= len(publish_timestamps):
            break
        pub_time = publish_timestamps[pub_idx]
        recv_time = frame["_recv_time"]
        latency_ms = (recv_time - pub_time) * 1000.0
        if latency_ms > 0:  # Ignore negative (clock sync artifacts)
            latencies_ms.append(latency_ms)

    if not latencies_ms:
        return {"error": "Could not compute latencies"}

    arr = np.array(latencies_ms)
    return {
        "count": len(arr),
        "min_ms": float(np.min(arr)),
        "max_ms": float(np.max(arr)),
        "mean_ms": float(np.mean(arr)),
        "median_ms": float(np.median(arr)),
        "p95_ms": float(np.percentile(arr, 95)),
        "p99_ms": float(np.percentile(arr, 99)),
    }


# ── Pytest fixtures ───────────────────────────────────────────────


def _mqtt_available() -> bool:
    """Check if MQTT broker is reachable."""
    try:
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="e2e-probe")
        c.connect(MQTT_BROKER_HOST, MQTT_BROKER_PORT, keepalive=5)
        c.disconnect()
        return True
    except Exception:
        return False


def _backend_available() -> bool:
    """Check if the backend WebSocket is reachable."""
    import urllib.request

    try:
        urllib.request.urlopen(f"{BACKEND_HTTP_URL}/health", timeout=2)
        return True
    except Exception:
        return False


requires_mqtt = pytest.mark.skipif(
    not _mqtt_available(),
    reason=f"MQTT broker not available at {MQTT_BROKER_HOST}:{MQTT_BROKER_PORT}",
)

requires_backend = pytest.mark.skipif(
    not _backend_available(),
    reason=f"Backend not available at {BACKEND_HTTP_URL}",
)


# ── Tests ─────────────────────────────────────────────────────────


class TestPacketFormat:
    """Verify firmware packet format (no infrastructure needed)."""

    def test_packet_format_matches_firmware(self) -> None:
        """Binary packet is exactly 478 bytes with correct header layout."""
        ts = int(time.time() * 1e6)
        packet = make_firmware_packet(ts, seed=0)
        assert len(packet) == 478

        # Verify round-trip through CsiPacket
        csi = CsiPacket.from_bytes(packet)
        assert csi.tx_mac == TX_MAC
        assert csi.rx_mac == RX_MAC
        assert csi.floor_id == FLOOR_ID
        assert csi.rssi == -40
        assert len(csi.iq_pairs) == NUM_SUBCARRIERS * 2

    def test_packet_amplitude_realistic(self) -> None:
        """Synthetic packets have realistic CSI amplitude range."""
        ts = int(time.time() * 1e6)
        packet = make_firmware_packet(ts, base_amplitude=50.0, seed=0)
        csi = CsiPacket.from_bytes(packet)
        amp = csi.amplitude_array
        assert amp.min() > 0, "Amplitude should be positive"
        assert 30.0 < amp.mean() < 70.0, f"Mean amplitude {amp.mean():.1f} out of range"


@requires_mqtt
class TestMqttPublish:
    """Verify we can publish firmware-format packets to MQTT."""

    def test_publish_to_broker(self) -> None:
        """Publish 10 packets and verify they arrive via a subscriber."""
        received: list[bytes] = []
        sub = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="e2e-sub")

        def on_message(client, userdata, msg):
            received.append(msg.payload)

        sub.on_message = on_message
        sub.connect(MQTT_BROKER_HOST, MQTT_BROKER_PORT)
        sub.subscribe(MQTT_TOPIC, qos=0)
        sub.loop_start()
        time.sleep(0.5)  # Let subscription settle

        sim = FirmwareSimulator()
        sim.connect()
        try:
            sim.publish_burst(10, rate_hz=50)
            time.sleep(1.0)  # Wait for delivery
        finally:
            sim.disconnect()
            sub.loop_stop()
            sub.disconnect()

        assert len(received) >= 8, (
            f"Expected >=8 packets (allowing QoS 0 drops), got {len(received)}"
        )
        # Verify deserialization
        for payload in received:
            pkt = CsiPacket.from_bytes(payload)
            assert pkt.floor_id == FLOOR_ID


@requires_mqtt
@requires_backend
class TestEndToEnd:
    """Full pipeline: firmware sim → MQTT → backend → WebSocket."""

    @pytest.fixture(autouse=True)
    def setup_simulator(self):
        self.sim = FirmwareSimulator()
        self.sim.connect()
        yield
        self.sim.disconnect()

    def test_data_flows_to_websocket(self) -> None:
        """Packets published to MQTT appear as TrackingFrames on WebSocket."""
        # Send warmup burst to prime the pipeline
        self.sim.publish_burst(WARMUP_PACKETS, rate_hz=PACKET_RATE_HZ)

        # Collect WS frames while sending more packets
        async def _run():
            # Start WS collection
            ws_task = asyncio.create_task(
                collect_ws_frames(BACKEND_WS_URL, count=5, timeout=10.0)
            )
            # Give WS a moment to connect
            await asyncio.sleep(0.5)
            # Send measurement burst in a thread
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: self.sim.publish_burst(MEASURE_PACKETS, rate_hz=PACKET_RATE_HZ),
            )
            return await ws_task

        frames = asyncio.get_event_loop().run_until_complete(_run())

        assert len(frames) > 0, "No TrackingFrames received on WebSocket"

        # Validate frame structure
        frame = frames[0]
        assert "timestamp" in frame
        assert "floor" in frame
        assert "people" in frame
        assert "occupancy_estimate" in frame

    def test_latency_under_50ms(self) -> None:
        """End-to-end latency (MQTT publish → WS receive) is under 50ms."""
        # Warmup
        self.sim.publish_burst(WARMUP_PACKETS, rate_hz=PACKET_RATE_HZ)
        time.sleep(0.5)

        # Record publish timestamps during measurement
        self.sim.publish_timestamps.clear()

        async def _run():
            ws_task = asyncio.create_task(
                collect_ws_frames(BACKEND_WS_URL, count=15, timeout=15.0)
            )
            await asyncio.sleep(0.3)
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: self.sim.publish_burst(MEASURE_PACKETS, rate_hz=PACKET_RATE_HZ),
            )
            return await ws_task

        frames = asyncio.get_event_loop().run_until_complete(_run())
        stats = analyze_latency(
            self.sim.publish_timestamps, frames, warmup_packets=0
        )

        assert "error" not in stats, stats.get("error")
        print(f"\n=== Latency Stats ===")
        for k, v in stats.items():
            print(f"  {k}: {v:.1f}" if isinstance(v, float) else f"  {k}: {v}")

        # Acceptance criteria: median < 50ms
        assert stats["median_ms"] < 50.0, (
            f"Median latency {stats['median_ms']:.1f}ms exceeds 50ms target"
        )


# ── Standalone runner ─────────────────────────────────────────────


def main() -> None:
    """Run the integration test as a standalone script with detailed output."""
    import sys

    print("=" * 60)
    print("WiFi CSI End-to-End Integration Test")
    print("=" * 60)

    # Check prerequisites
    print("\n[1/4] Checking MQTT broker...", end=" ")
    if not _mqtt_available():
        print(f"FAILED — no broker at {MQTT_BROKER_HOST}:{MQTT_BROKER_PORT}")
        print("  Start Mosquitto: brew services start mosquitto")
        sys.exit(1)
    print("OK")

    print("[2/4] Checking backend...", end=" ")
    if not _backend_available():
        print(f"FAILED — no backend at {BACKEND_HTTP_URL}")
        print("  Start backend: python -m backend.main --host 0.0.0.0 --port 8000")
        sys.exit(1)
    print("OK")

    # Publish test
    print("[3/4] Publishing firmware packets to MQTT...")
    sim = FirmwareSimulator()
    sim.connect()
    try:
        total_packets = WARMUP_PACKETS + MEASURE_PACKETS
        print(f"  Sending {total_packets} packets at {PACKET_RATE_HZ} Hz...")
        sim.publish_burst(WARMUP_PACKETS, rate_hz=PACKET_RATE_HZ)
        sim.publish_timestamps.clear()  # Only measure the measurement burst

        # Collect WS frames
        print("[4/4] Collecting WebSocket frames...")

        async def _run():
            ws_task = asyncio.create_task(
                collect_ws_frames(BACKEND_WS_URL, count=15, timeout=15.0)
            )
            await asyncio.sleep(0.3)
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: sim.publish_burst(
                    MEASURE_PACKETS, rate_hz=PACKET_RATE_HZ
                ),
            )
            return await ws_task

        frames = asyncio.run(_run())
    finally:
        sim.disconnect()

    # Results
    print(f"\n  Packets sent: {sim.packets_sent}")
    print(f"  WS frames received: {len(frames)}")

    if frames:
        print("\n  Sample TrackingFrame:")
        sample = {k: v for k, v in frames[0].items() if k != "_recv_time"}
        print(f"    {json.dumps(sample, indent=2, default=str)[:500]}")

        stats = analyze_latency(sim.publish_timestamps, frames, warmup_packets=0)
        print("\n  === Latency Results ===")
        if "error" in stats:
            print(f"    Error: {stats['error']}")
        else:
            for k, v in stats.items():
                label = k.replace("_", " ").title()
                if isinstance(v, float):
                    print(f"    {label}: {v:.1f} ms")
                else:
                    print(f"    {label}: {v}")

            passed = stats["median_ms"] < 50.0
            print(f"\n  Acceptance (<50ms median): {'PASS' if passed else 'FAIL'}")
            sys.exit(0 if passed else 1)
    else:
        print("\n  FAIL — No WebSocket frames received")
        sys.exit(1)


if __name__ == "__main__":
    main()
