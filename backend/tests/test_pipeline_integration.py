"""End-to-end integration test for the CSI pipeline.

Tests the full data flow: synthetic CSI packet → pipeline processing →
TrackingFrame output, without requiring MQTT or real hardware.
"""

from __future__ import annotations

import asyncio
import math
import time

import numpy as np
import pytest

from backend.collector.csi_packet import CsiPacket, NUM_SUBCARRIERS
from backend.collector.mqtt_listener import MqttListener
from backend.main import (
    FloorPipeline,
    Pipeline,
    SyntheticCSIGenerator,
    _load_config,
)
from backend.server.schemas import TrackingFrame


# ── Helpers ────────────────────────────────────────────────────────


def _make_synthetic_packet(
    floor_id: int = 1,
    timestamp_us: int = 0,
    breathing_freq: float = 0.25,
    base_amplitude: float = 50.0,
    seed: int = 42,
) -> CsiPacket:
    """Create a synthetic CSI packet with realistic amplitude patterns."""
    rng = np.random.default_rng(seed)
    t_s = timestamp_us / 1e6

    # Base + breathing modulation + noise
    base = base_amplitude + 3.0 * np.sin(2.0 * math.pi * breathing_freq * t_s)
    noise = rng.normal(0, 1.5, NUM_SUBCARRIERS)
    amplitude = base + noise
    amplitude = np.maximum(amplitude, 1.0)

    phase = rng.uniform(-0.1, 0.1, NUM_SUBCARRIERS)
    I = (amplitude * np.cos(phase)).astype(np.int16)
    Q = (amplitude * np.sin(phase)).astype(np.int16)

    iq_pairs = []
    for i in range(NUM_SUBCARRIERS):
        iq_pairs.append(int(I[i]))
        iq_pairs.append(int(Q[i]))

    return CsiPacket(
        timestamp_us=timestamp_us,
        tx_mac="aa:bb:cc:dd:01:00",
        rx_mac="aa:bb:cc:dd:01:01",
        rssi=-40,
        floor_id=floor_id - 1,  # 0-based in packet
        iq_pairs=iq_pairs,
    )


# ── SyntheticCSIGenerator tests ───────────────────────────────────


class TestSyntheticCSIGenerator:
    def test_generates_valid_packets(self):
        gen = SyntheticCSIGenerator(floor_id=1)
        packet = gen.generate()

        assert isinstance(packet, CsiPacket)
        assert len(packet.iq_pairs) == NUM_SUBCARRIERS * 2
        assert packet.floor_id == 0  # 0-based
        assert packet.tx_mac == "aa:bb:cc:dd:01:00"

    def test_sequential_timestamps(self):
        gen = SyntheticCSIGenerator(floor_id=1)
        p1 = gen.generate()
        p2 = gen.generate()
        assert p2.timestamp_us > p1.timestamp_us

    def test_amplitude_has_breathing_pattern(self):
        """Verify the synthetic generator produces a breathing-band signal."""
        gen = SyntheticCSIGenerator(floor_id=1, seed=42)
        amplitudes = []
        for _ in range(500):  # 5 seconds at 100 Hz
            packet = gen.generate()
            amplitudes.append(np.mean(packet.amplitude_array))

        # Check variance — should have some signal, not just noise
        arr = np.array(amplitudes)
        assert np.std(arr) > 0.5, "Synthetic signal should have visible variation"

    def test_different_seeds_produce_different_data(self):
        gen1 = SyntheticCSIGenerator(seed=1)
        gen2 = SyntheticCSIGenerator(seed=2)
        p1 = gen1.generate()
        p2 = gen2.generate()
        assert p1.iq_pairs != p2.iq_pairs


# ── FloorPipeline tests ──────────────────────────────────────────


class TestFloorPipeline:
    @pytest.fixture
    def house_config(self):
        return _load_config("house.yaml")

    @pytest.fixture
    def floor_pipeline(self, house_config):
        return FloorPipeline(floor_id=1, house_config=house_config)

    def test_process_single_packet(self, floor_pipeline):
        packet = _make_synthetic_packet(floor_id=1)
        floor_pipeline.process_packet(packet)

        assert floor_pipeline.last_update_time > 0
        assert floor_pipeline._frame_count == 1

    def test_process_multiple_packets(self, floor_pipeline):
        for i in range(50):
            packet = _make_synthetic_packet(
                floor_id=1,
                timestamp_us=i * 10000,
                seed=i,
            )
            floor_pipeline.process_packet(packet)

        assert floor_pipeline._frame_count == 50
        assert len(floor_pipeline._amplitude_buffer) == 50

    def test_build_tracking_frame_after_warmup(self, floor_pipeline):
        """After enough packets, we should get a valid TrackingFrame."""
        # Push enough data to warm up the stateful modules
        for i in range(100):
            packet = _make_synthetic_packet(
                floor_id=1,
                timestamp_us=i * 10000,
                seed=i + 100,
            )
            floor_pipeline.process_packet(packet)

        frame = floor_pipeline.build_tracking_frame()

        assert isinstance(frame, TrackingFrame)
        assert frame.floor == 1
        assert frame.timestamp > 0
        assert frame.occupancy_estimate >= 0
        assert 0.0 <= frame.occupancy_confidence <= 1.0

    def test_build_tracking_frame_has_people(self, floor_pipeline):
        """After warmup, the frame should contain person data."""
        for i in range(200):
            packet = _make_synthetic_packet(
                floor_id=1,
                timestamp_us=i * 10000,
                seed=i + 200,
            )
            floor_pipeline.process_packet(packet)

        frame = floor_pipeline.build_tracking_frame()

        assert len(frame.people) >= 1
        person = frame.people[0]
        assert person.id == "p1"
        assert -1.0 <= person.x <= 16.0
        assert -1.0 <= person.y <= 13.0
        assert 0.0 <= person.position_confidence <= 1.0
        assert person.uncertainty_radius_m >= 0.0

    def test_build_tracking_frame_breathing_data(self, floor_pipeline):
        """After sufficient data, breathing should produce estimates."""
        for i in range(600):  # 6 seconds at 100 Hz
            packet = _make_synthetic_packet(
                floor_id=1,
                timestamp_us=i * 10000,
                seed=i + 300,
            )
            floor_pipeline.process_packet(packet)

        frame = floor_pipeline.build_tracking_frame()
        person = frame.people[0]

        # Breathing data should be populated (may or may not have valid rate)
        assert person.breathing is not None
        assert 0 <= person.breathing.rate_bpm <= 60
        assert 0.0 <= person.breathing.confidence <= 1.0

    def test_zone_signal_quality(self, floor_pipeline):
        """Zone signal quality should reflect data freshness."""
        for i in range(10):
            packet = _make_synthetic_packet(
                floor_id=1,
                timestamp_us=i * 10000,
                seed=i,
            )
            floor_pipeline.process_packet(packet)

        frame = floor_pipeline.build_tracking_frame()

        # Floor 1 has Kitchen, Living Room, Garage
        assert len(frame.zone_signal_quality) > 0
        for zone, quality in frame.zone_signal_quality.items():
            assert 0.0 <= quality <= 1.0

    def test_amplitude_buffer_trim(self, floor_pipeline):
        """Buffer should not grow unbounded."""
        for i in range(300):
            packet = _make_synthetic_packet(
                floor_id=1,
                timestamp_us=i * 10000,
                seed=i,
            )
            floor_pipeline.process_packet(packet)

        assert len(floor_pipeline._amplitude_buffer) <= 200


# ── MqttListener tests ────────────────────────────────────────────


class TestMqttListener:
    def test_init_defaults(self):
        listener = MqttListener()
        assert listener.packets_received == 0
        assert listener.packets_dropped == 0
        assert not listener.is_connected

    def test_queue_not_available_before_start(self):
        listener = MqttListener()
        with pytest.raises(RuntimeError, match="not started"):
            _ = listener.queue


# ── Pipeline tests ────────────────────────────────────────────────


class TestPipeline:
    @pytest.fixture
    def configs(self):
        return _load_config("house.yaml"), _load_config("sensors.yaml")

    def test_init_simulate_mode(self, configs):
        house, sensors = configs
        pipeline = Pipeline(
            house_config=house,
            sensors_config=sensors,
            simulate=True,
        )
        assert pipeline.simulate
        assert len(pipeline.floor_pipelines) >= 1

    def test_floor_pipelines_created(self, configs):
        house, sensors = configs
        pipeline = Pipeline(
            house_config=house,
            sensors_config=sensors,
            simulate=True,
        )
        # house.yaml has 3 floors
        assert 1 in pipeline.floor_pipelines
        assert 2 in pipeline.floor_pipelines
        assert 3 in pipeline.floor_pipelines

    @pytest.mark.asyncio
    async def test_pipeline_start_stop(self, configs):
        """Pipeline should start and stop cleanly in simulate mode."""
        house, sensors = configs
        pipeline = Pipeline(
            house_config=house,
            sensors_config=sensors,
            simulate=True,
        )

        await pipeline.start()
        assert pipeline._running

        # Let it process a few frames
        await asyncio.sleep(0.1)

        await pipeline.stop()
        assert not pipeline._running

    @pytest.mark.asyncio
    async def test_pipeline_produces_tracking_frames(self, configs):
        """After running briefly, floor pipelines should have data."""
        house, sensors = configs
        pipeline = Pipeline(
            house_config=house,
            sensors_config=sensors,
            simulate=True,
        )

        await pipeline.start()

        # Let it process enough frames for the extractors to warm up
        await asyncio.sleep(0.5)

        # Check that at least floor 1 has been updated
        fp = pipeline.floor_pipelines[1]
        assert fp.last_update_time > 0
        assert fp._frame_count > 0

        # Build a tracking frame
        frame = fp.build_tracking_frame()
        assert isinstance(frame, TrackingFrame)
        assert frame.floor == 1

        await pipeline.stop()


# ── Config loading tests ──────────────────────────────────────────


class TestConfigLoading:
    def test_load_house_config(self):
        config = _load_config("house.yaml")
        assert "floors" in config
        assert "transition_zones" in config
        assert "attenuation" in config

    def test_load_sensors_config(self):
        config = _load_config("sensors.yaml")
        assert "sensors" in config
        assert "mqtt" in config

        sensors = config["sensors"]
        assert len(sensors) == 12  # 4 per floor × 3 floors

        # Check structure
        first = sensors[0]
        assert "mac" in first
        assert "role" in first
        assert "floor" in first
        assert "channel" in first
        assert "position" in first

    def test_sensors_have_correct_roles(self):
        config = _load_config("sensors.yaml")
        sensors = config["sensors"]

        tx_count = sum(1 for s in sensors if s["role"] == "tx")
        rx_count = sum(1 for s in sensors if s["role"] == "rx")
        assert tx_count == 3  # 1 TX per floor
        assert rx_count == 9  # 3 RX per floor

    def test_mqtt_config(self):
        config = _load_config("sensors.yaml")
        mqtt = config["mqtt"]
        assert mqtt["broker_host"] == "localhost"
        assert mqtt["broker_port"] == 1883
        assert mqtt["subscribe_pattern"] == "csi/#"

    def test_missing_config_returns_empty(self):
        config = _load_config("nonexistent.yaml")
        assert config == {}
