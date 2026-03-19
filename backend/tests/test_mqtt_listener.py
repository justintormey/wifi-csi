"""Tests for the enhanced MQTT listener.

Covers topic parsing, per-sensor metrics, out-of-order reordering,
sensor dropout detection, and the full message handling pipeline.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock, patch

import pytest

from backend.collector.csi_packet import CsiPacket, NUM_SUBCARRIERS, PACKET_SIZE
from backend.collector.mqtt_listener import (
    MqttListener,
    SensorMetrics,
    TopicInfo,
    _SensorReorderBuffer,
    parse_topic,
)


# ── Helpers ──────────────────────────────────────────────────────


def _make_iq_pairs(i_val: int = 100, q_val: int = 0) -> list[int]:
    return [i_val, q_val] * NUM_SUBCARRIERS


def _make_packet(**overrides) -> CsiPacket:
    defaults = dict(
        timestamp_us=1_000_000,
        tx_mac="aa:bb:cc:dd:ee:01",
        rx_mac="aa:bb:cc:dd:ee:02",
        rssi=-45,
        floor_id=0,  # 0-based in packet (floor 1)
        iq_pairs=_make_iq_pairs(),
    )
    defaults.update(overrides)
    return CsiPacket(**defaults)


def _make_mqtt_message(topic: str, payload: bytes) -> MagicMock:
    """Create a mock paho MQTTMessage."""
    msg = MagicMock()
    msg.topic = topic
    msg.payload = payload
    return msg


# ── Topic parsing ────────────────────────────────────────────────


class TestParseTopic:
    def test_valid_topic(self):
        result = parse_topic("csi/1/aa:bb:cc:dd:01:01")
        assert result == TopicInfo(floor_id=1, rx_mac="aa:bb:cc:dd:01:01")

    def test_valid_floor_2(self):
        result = parse_topic("csi/2/aa:bb:cc:dd:02:03")
        assert result is not None
        assert result.floor_id == 2
        assert result.rx_mac == "aa:bb:cc:dd:02:03"

    def test_floor_3(self):
        result = parse_topic("csi/3/ff:ee:dd:cc:bb:aa")
        assert result is not None
        assert result.floor_id == 3

    def test_wrong_prefix(self):
        assert parse_topic("data/1/aa:bb:cc:dd:01:01") is None

    def test_too_few_parts(self):
        assert parse_topic("csi/1") is None

    def test_too_many_parts(self):
        assert parse_topic("csi/1/mac/extra") is None

    def test_non_integer_floor(self):
        assert parse_topic("csi/abc/aa:bb:cc:dd:01:01") is None

    def test_empty_mac(self):
        assert parse_topic("csi/1/") is None

    def test_empty_string(self):
        assert parse_topic("") is None

    def test_just_csi(self):
        assert parse_topic("csi") is None


# ── SensorMetrics ────────────────────────────────────────────────


class TestSensorMetrics:
    def test_initial_state(self):
        m = SensorMetrics(rx_mac="aa:bb:cc:dd:01:01")
        assert m.packets_total == 0
        assert m.packets_per_sec == 0.0
        assert m.malformed_rate == 0.0
        assert m.latency_s == 0.0

    def test_record_packet_increments(self):
        m = SensorMetrics(rx_mac="test")
        m.record_packet(100.0)
        assert m.packets_total == 1
        assert m.last_seen == 100.0
        assert m.first_seen == 100.0

    def test_packets_per_sec(self):
        m = SensorMetrics(rx_mac="test")
        # 10 packets over 1 second
        for i in range(10):
            m.record_packet(100.0 + i * 0.1)
        rate = m.packets_per_sec
        # Should be ~10 pps (9 intervals over 0.9s)
        assert 9.0 < rate < 11.0

    def test_malformed_rate(self):
        m = SensorMetrics(rx_mac="test")
        m.record_packet(1.0)
        m.record_packet(2.0)
        m.record_malformed()
        # 2 good + 1 bad = 1/3 malformed rate
        assert abs(m.malformed_rate - 1 / 3) < 0.01

    def test_malformed_rate_no_packets(self):
        m = SensorMetrics(rx_mac="test")
        assert m.malformed_rate == 0.0

    def test_deque_maxlen_eviction(self):
        m = SensorMetrics(rx_mac="test")
        # Record more packets than the deque maxlen (500)
        for i in range(600):
            m.record_packet(100.0 + i * 0.1)
        # deque(maxlen=500) auto-evicts oldest entries
        assert len(m._recent_timestamps) == 500

    def test_first_seen_not_overwritten(self):
        m = SensorMetrics(rx_mac="test")
        m.record_packet(100.0)
        m.record_packet(200.0)
        assert m.first_seen == 100.0
        assert m.last_seen == 200.0


# ── Reorder buffer ───────────────────────────────────────────────


class TestReorderBuffer:
    def test_in_order_passthrough(self):
        buf = _SensorReorderBuffer(window_us=50_000)
        results = []
        for ts in [100_000, 200_000, 300_000]:
            pkt = _make_packet(timestamp_us=ts)
            results.extend(buf.push(pkt))
        # With 50ms window, first packet flushes when ts reaches 150k+
        assert len(results) >= 1
        # Verify order
        timestamps = [p.timestamp_us for p in results]
        assert timestamps == sorted(timestamps)

    def test_out_of_order_reordered(self):
        buf = _SensorReorderBuffer(window_us=100_000)
        # Send packets out of order
        p1 = _make_packet(timestamp_us=300_000)
        p2 = _make_packet(timestamp_us=100_000)
        p3 = _make_packet(timestamp_us=200_000)

        results = []
        results.extend(buf.push(p1))
        results.extend(buf.push(p2))
        results.extend(buf.push(p3))
        # Flush remaining
        results.extend(buf.flush_all())

        timestamps = [p.timestamp_us for p in results]
        assert timestamps == sorted(timestamps)
        assert len(results) == 3

    def test_flush_all_empties_buffer(self):
        buf = _SensorReorderBuffer(window_us=1_000_000)
        buf.push(_make_packet(timestamp_us=100))
        buf.push(_make_packet(timestamp_us=200))
        result = buf.flush_all()
        assert len(result) == 2
        assert len(buf) == 0

    def test_window_zero_immediate_flush(self):
        """Window of 0 should flush everything immediately."""
        buf = _SensorReorderBuffer(window_us=0)
        result = buf.push(_make_packet(timestamp_us=100))
        assert len(result) == 1
        assert result[0].timestamp_us == 100

    def test_large_gap_flushes_old(self):
        """A packet far in the future should flush all buffered packets."""
        buf = _SensorReorderBuffer(window_us=50_000)
        buf.push(_make_packet(timestamp_us=100_000))
        buf.push(_make_packet(timestamp_us=110_000))
        # Jump far ahead
        result = buf.push(_make_packet(timestamp_us=500_000))
        assert len(result) == 2  # first two flushed
        timestamps = [p.timestamp_us for p in result]
        assert timestamps == [100_000, 110_000]


# ── MqttListener: message handling ───────────────────────────────


class TestMqttListenerMessageHandling:
    """Test _on_message directly by calling it with mock MQTT messages."""

    def _make_listener(self, **kwargs) -> MqttListener:
        listener = MqttListener(reorder_window_us=0, **kwargs)
        listener._loop = MagicMock()
        listener._queue = asyncio.Queue(maxsize=100)
        # Make call_soon_threadsafe execute the callback immediately
        listener._loop.call_soon_threadsafe = lambda fn, *args: fn(*args) if not args else fn(args[0])
        return listener

    def test_valid_message_enqueued(self):
        listener = self._make_listener()
        pkt = _make_packet(floor_id=0)  # 0-based = floor 1
        msg = _make_mqtt_message("csi/1/aa:bb:cc:dd:ee:02", pkt.to_bytes())

        listener._on_message(None, None, msg)

        assert listener.packets_received == 1
        assert listener._queue.qsize() == 1

    def test_bad_topic_rejected(self):
        listener = self._make_listener()
        pkt = _make_packet()
        msg = _make_mqtt_message("invalid/topic", pkt.to_bytes())

        listener._on_message(None, None, msg)

        assert listener.packets_received == 0
        assert listener.topic_parse_errors == 1
        assert listener._queue.qsize() == 0

    def test_malformed_payload_rejected(self):
        listener = self._make_listener()
        msg = _make_mqtt_message("csi/1/aa:bb:cc:dd:01:01", b"too short")

        listener._on_message(None, None, msg)

        assert listener.packets_received == 0
        assert listener.malformed_packets == 1

    def test_per_sensor_metrics_updated(self):
        listener = self._make_listener()
        pkt = _make_packet(floor_id=0)
        msg = _make_mqtt_message("csi/1/aa:bb:cc:dd:ee:02", pkt.to_bytes())

        listener._on_message(None, None, msg)

        metrics = listener.sensor_metrics
        assert "aa:bb:cc:dd:ee:02" in metrics
        assert metrics["aa:bb:cc:dd:ee:02"].packets_total == 1

    def test_floor_mismatch_warning(self):
        listener = self._make_listener()
        # Packet says floor 0 (1-based = 1), topic says floor 2
        pkt = _make_packet(floor_id=0)
        msg = _make_mqtt_message("csi/2/aa:bb:cc:dd:ee:02", pkt.to_bytes())

        listener._on_message(None, None, msg)

        assert listener.topic_mismatch_warnings == 1
        # Packet should still be enqueued (warning, not rejection)
        assert listener.packets_received == 1

    def test_floor_match_no_warning(self):
        listener = self._make_listener()
        pkt = _make_packet(floor_id=0)  # 0-based = floor 1
        msg = _make_mqtt_message("csi/1/aa:bb:cc:dd:ee:02", pkt.to_bytes())

        listener._on_message(None, None, msg)

        assert listener.topic_mismatch_warnings == 0

    def test_multiple_sensors_tracked(self):
        listener = self._make_listener()

        for mac_suffix in ["01", "02", "03"]:
            mac = f"aa:bb:cc:dd:01:{mac_suffix}"
            pkt = _make_packet(rx_mac=mac, floor_id=0)
            msg = _make_mqtt_message(f"csi/1/{mac}", pkt.to_bytes())
            listener._on_message(None, None, msg)

        assert len(listener.sensor_metrics) == 3
        assert listener.packets_received == 3

    def test_malformed_packet_counted_per_sensor(self):
        listener = self._make_listener()
        msg = _make_mqtt_message("csi/1/aa:bb:cc:dd:01:01", b"bad")

        listener._on_message(None, None, msg)

        metrics = listener.sensor_metrics
        assert "aa:bb:cc:dd:01:01" in metrics
        assert metrics["aa:bb:cc:dd:01:01"].packets_malformed == 1


# ── MqttListener: reordering integration ──────────────────────────


class TestMqttListenerReordering:
    def _make_listener(self, **kwargs) -> MqttListener:
        defaults = {"reorder_window_us": 100_000}
        defaults.update(kwargs)
        listener = MqttListener(**defaults)
        listener._loop = MagicMock()
        listener._queue = asyncio.Queue(maxsize=100)
        # Track enqueued packets
        listener._enqueued: list[CsiPacket] = []
        original_enqueue = listener._enqueue

        def tracking_enqueue(pkt):
            listener._enqueued.append(pkt)
            original_enqueue(pkt)

        listener._loop.call_soon_threadsafe = lambda fn, pkt: tracking_enqueue(pkt)
        return listener

    def test_reordering_enabled(self):
        listener = self._make_listener(reorder_window_us=100_000)
        mac = "aa:bb:cc:dd:01:01"

        # Send 3 packets out of order
        for ts in [300_000, 100_000, 200_000]:
            pkt = _make_packet(timestamp_us=ts, rx_mac=mac, floor_id=0)
            msg = _make_mqtt_message(f"csi/1/{mac}", pkt.to_bytes())
            listener._on_message(None, None, msg)

        # Force flush
        for remaining in listener._reorder_buffers.values():
            for pkt in remaining.flush_all():
                listener._enqueued.append(pkt)

        timestamps = [p.timestamp_us for p in listener._enqueued]
        assert timestamps == sorted(timestamps)


# ── MqttListener: dropout detection ──────────────────────────────


class TestMqttListenerDropout:
    def test_sensor_marked_offline_after_timeout(self):
        listener = MqttListener(sensor_timeout_s=0.1, reorder_window_us=0)
        listener._loop = MagicMock()
        listener._queue = asyncio.Queue(maxsize=100)
        listener._loop.call_soon_threadsafe = lambda fn, *a: fn(a[0]) if a else fn()

        mac = "aa:bb:cc:dd:01:01"
        pkt = _make_packet(rx_mac=mac, floor_id=0)
        msg = _make_mqtt_message(f"csi/1/{mac}", pkt.to_bytes())
        listener._on_message(None, None, msg)

        assert mac not in listener._sensors_offline

        # Simulate time passing and check dropout
        listener._sensor_metrics[mac].last_seen = time.monotonic() - 1.0
        listener._check_sensor_dropout()

        assert mac in listener._sensors_offline

    def test_sensor_back_online(self):
        listener = MqttListener(sensor_timeout_s=0.1, reorder_window_us=0)
        listener._loop = MagicMock()
        listener._queue = asyncio.Queue(maxsize=100)
        listener._loop.call_soon_threadsafe = lambda fn, *a: fn(a[0]) if a else fn()

        mac = "aa:bb:cc:dd:01:01"

        # First packet
        pkt = _make_packet(rx_mac=mac, floor_id=0)
        msg = _make_mqtt_message(f"csi/1/{mac}", pkt.to_bytes())
        listener._on_message(None, None, msg)

        # Simulate dropout
        listener._sensor_metrics[mac].last_seen = time.monotonic() - 1.0
        listener._check_sensor_dropout()
        assert mac in listener._sensors_offline

        # Cancel the timer that _check_sensor_dropout scheduled
        if listener._dropout_timer:
            listener._dropout_timer.cancel()

        # New packet arrives — should go back online
        listener._on_message(None, None, msg)
        assert mac not in listener._sensors_offline

    def test_dropout_callback_invoked(self):
        callback_calls = []

        def on_dropout(mac, age):
            callback_calls.append((mac, age))

        listener = MqttListener(
            sensor_timeout_s=0.1,
            reorder_window_us=0,
            on_sensor_dropout=on_dropout,
        )
        listener._loop = MagicMock()
        listener._queue = asyncio.Queue(maxsize=100)
        listener._loop.call_soon_threadsafe = lambda fn, *a: fn(a[0]) if a else fn()

        mac = "aa:bb:cc:dd:01:01"
        pkt = _make_packet(rx_mac=mac, floor_id=0)
        msg = _make_mqtt_message(f"csi/1/{mac}", pkt.to_bytes())
        listener._on_message(None, None, msg)

        # Simulate timeout
        listener._sensor_metrics[mac].last_seen = time.monotonic() - 1.0
        listener._check_sensor_dropout()

        # Cancel scheduled timer
        if listener._dropout_timer:
            listener._dropout_timer.cancel()

        assert len(callback_calls) == 1
        assert callback_calls[0][0] == mac
        assert callback_calls[0][1] > 0.1


# ── MqttListener: backpressure ────────────────────────────────────


class TestBackpressure:
    def test_queue_full_drops_oldest(self):
        listener = MqttListener(queue_maxsize=2, reorder_window_us=0)
        listener._loop = MagicMock()
        listener._queue = asyncio.Queue(maxsize=2)
        listener._loop.call_soon_threadsafe = lambda fn, *a: fn(a[0]) if a else fn()

        mac = "aa:bb:cc:dd:01:01"
        for ts in [100_000, 200_000, 300_000]:
            pkt = _make_packet(timestamp_us=ts, rx_mac=mac, floor_id=0)
            msg = _make_mqtt_message(f"csi/1/{mac}", pkt.to_bytes())
            listener._on_message(None, None, msg)

        assert listener.packets_received == 3
        assert listener.packets_dropped == 1
        assert listener._queue.qsize() == 2
