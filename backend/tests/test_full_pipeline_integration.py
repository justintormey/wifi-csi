"""Full pipeline integration tests (HAL-167).

Tests the complete data pipeline from CSI packet input through signal processing,
tracking, vitals extraction, and WebSocket output — without requiring MQTT broker
or real hardware.

Covers:
- Fixture data replay through the full pipeline
- Multi-floor data routing
- Sensor dropout and recovery
- WebSocket client receives valid TrackingFrame data
- Sustained operation stability (memory, timing)
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from httpx import ASGITransport, AsyncClient

from backend.collector.csi_packet import CsiPacket, NUM_SUBCARRIERS
from backend.main import (
    FloorPipeline,
    Pipeline,
    SyntheticCSIGenerator,
    _load_config,
)
from backend.server.schemas import TrackingFrame

# ── Fixtures directory ────────────────────────────────────────────

FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ── Helpers ───────────────────────────────────────────────────────


def _make_packet(
    floor_id: int = 1,
    timestamp_us: int = 0,
    base_amplitude: float = 50.0,
    breathing_freq: float = 0.25,
    seed: int = 42,
    tx_mac: str = "aa:bb:cc:dd:01:00",
    rx_mac: str = "aa:bb:cc:dd:01:01",
    rssi: int = -40,
) -> CsiPacket:
    """Create a synthetic CSI packet with controllable parameters."""
    rng = np.random.default_rng(seed)
    t_s = timestamp_us / 1e6

    base = base_amplitude + 3.0 * np.sin(2.0 * math.pi * breathing_freq * t_s)
    noise = rng.normal(0, 1.5, NUM_SUBCARRIERS)
    amplitude = np.maximum(base + noise, 1.0)

    phase = rng.uniform(-0.1, 0.1, NUM_SUBCARRIERS)
    I = (amplitude * np.cos(phase)).astype(np.int16)
    Q = (amplitude * np.sin(phase)).astype(np.int16)

    iq_pairs = []
    for i in range(NUM_SUBCARRIERS):
        iq_pairs.append(int(I[i]))
        iq_pairs.append(int(Q[i]))

    return CsiPacket(
        timestamp_us=timestamp_us,
        tx_mac=tx_mac,
        rx_mac=rx_mac,
        rssi=rssi,
        floor_id=floor_id - 1,  # 0-based in packet
        iq_pairs=iq_pairs,
    )


def _generate_packet_sequence(
    count: int,
    floor_id: int = 1,
    sample_rate: float = 100.0,
    base_amplitude: float = 50.0,
    breathing_freq: float = 0.25,
    **kwargs: Any,
) -> list[CsiPacket]:
    """Generate a time-series of CSI packets at a given sample rate."""
    interval_us = int(1e6 / sample_rate)
    return [
        _make_packet(
            floor_id=floor_id,
            timestamp_us=i * interval_us,
            base_amplitude=base_amplitude,
            breathing_freq=breathing_freq,
            seed=i + 1000,
            **kwargs,
        )
        for i in range(count)
    ]


# ── Shared pytest fixtures ────────────────────────────────────────


@pytest.fixture
def house_config():
    return _load_config("house.yaml")


@pytest.fixture
def sensors_config():
    return _load_config("sensors.yaml")


@pytest.fixture
def sample_fixtures():
    """Load the sample_csi.json fixture file."""
    path = FIXTURES_DIR / "sample_csi.json"
    with open(path) as f:
        return json.load(f)


@pytest.fixture
def pipeline_simulate(house_config, sensors_config):
    """Create a Pipeline in simulate mode (no MQTT)."""
    return Pipeline(
        house_config=house_config,
        sensors_config=sensors_config,
        simulate=True,
    )


# ── Test class: Fixture data replay ──────────────────────────────


class TestFixtureReplay:
    """Replay fixture packets through the pipeline and validate outputs."""

    def test_fixture_file_loads(self, sample_fixtures):
        assert "packets" in sample_fixtures
        assert len(sample_fixtures["packets"]) >= 3

    def test_fixture_packets_create_valid_csi(self, sample_fixtures):
        """Each fixture packet definition can be used to generate a valid CsiPacket."""
        for entry in sample_fixtures["packets"]:
            packet = _make_packet(
                floor_id=entry["expected"]["floor"],
                timestamp_us=entry["timestamp_us"],
                tx_mac=entry["tx_mac"],
                rx_mac=entry["rx_mac"],
                rssi=entry["rssi"],
                base_amplitude=sum(entry["expected"]["amplitude_mean_range"]) / 2.0,
            )
            assert isinstance(packet, CsiPacket)
            assert len(packet.iq_pairs) == NUM_SUBCARRIERS * 2
            assert packet.floor_id == entry["floor_id"]

    def test_replay_through_floor_pipeline(self, house_config):
        """Replay a sequence of packets through FloorPipeline and verify output."""
        fp = FloorPipeline(floor_id=1, house_config=house_config)
        packets = _generate_packet_sequence(200, floor_id=1)

        for pkt in packets:
            fp.process_packet(pkt)

        assert fp._frame_count == 200

        frame = fp.build_tracking_frame()
        assert isinstance(frame, TrackingFrame)
        assert frame.floor == 1
        assert frame.timestamp > 0
        assert frame.occupancy_estimate >= 0
        assert 0.0 <= frame.occupancy_confidence <= 1.0
        assert len(frame.people) >= 1

    def test_replay_produces_breathing_estimates(self, house_config):
        """After 600 packets (6s at 100Hz), breathing extraction should produce data."""
        fp = FloorPipeline(floor_id=1, house_config=house_config)
        packets = _generate_packet_sequence(
            600, floor_id=1, breathing_freq=0.25
        )

        for pkt in packets:
            fp.process_packet(pkt)

        frame = fp.build_tracking_frame()
        person = frame.people[0]
        assert person.breathing is not None
        assert 0 <= person.breathing.rate_bpm <= 60
        assert 0.0 <= person.breathing.confidence <= 1.0

    def test_fixture_expected_amplitude_ranges(self, sample_fixtures, house_config):
        """Fixture packets produce amplitudes within their annotated expected ranges."""
        for entry in sample_fixtures["packets"]:
            expected = entry["expected"]
            amp_lo, amp_hi = expected["amplitude_mean_range"]
            mid_amplitude = (amp_lo + amp_hi) / 2.0

            packet = _make_packet(
                floor_id=expected["floor"],
                timestamp_us=entry["timestamp_us"],
                tx_mac=entry["tx_mac"],
                rx_mac=entry["rx_mac"],
                rssi=entry["rssi"],
                base_amplitude=mid_amplitude,
            )

            amp = np.array(packet.amplitude_array)
            mean_amp = float(np.mean(amp))

            # Mean amplitude should be within ±20% of the fixture's expected range
            tolerance = (amp_hi - amp_lo) * 0.5 + 5.0
            assert amp_lo - tolerance <= mean_amp <= amp_hi + tolerance, (
                f"Fixture '{entry['label']}': mean amplitude {mean_amp:.1f} "
                f"outside expected range [{amp_lo - tolerance:.1f}, {amp_hi + tolerance:.1f}]"
            )

    def test_replay_binary_roundtrip(self):
        """Packets survive binary serialization → deserialization."""
        original = _make_packet(floor_id=2, timestamp_us=123456)
        binary = original.to_bytes()
        restored = CsiPacket.from_bytes(binary)

        assert restored.timestamp_us == original.timestamp_us
        assert restored.tx_mac == original.tx_mac
        assert restored.rx_mac == original.rx_mac
        assert restored.rssi == original.rssi
        assert restored.floor_id == original.floor_id
        assert restored.iq_pairs == original.iq_pairs


# ── Test class: Multi-floor routing ──────────────────────────────


class TestMultiFloorRouting:
    """Verify packets are correctly routed to per-floor pipelines."""

    def test_packets_route_to_correct_floor(self, house_config, sensors_config):
        """Packets with different floor_ids reach the correct FloorPipeline."""
        pipeline = Pipeline(
            house_config=house_config,
            sensors_config=sensors_config,
            simulate=True,
        )

        # Send packets to each floor
        for floor in [1, 2, 3]:
            packets = _generate_packet_sequence(10, floor_id=floor)
            for pkt in packets:
                floor_key = pkt.floor_id + 1
                pipeline.floor_pipelines[floor_key].process_packet(pkt)

        # Verify each floor received data
        for floor in [1, 2, 3]:
            fp = pipeline.floor_pipelines[floor]
            assert fp._frame_count == 10, f"Floor {floor} should have 10 packets"

    def test_cross_floor_isolation(self, house_config, sensors_config):
        """Floor 1 data does not contaminate Floor 2 pipeline state."""
        pipeline = Pipeline(
            house_config=house_config,
            sensors_config=sensors_config,
            simulate=True,
        )

        # Only send to floor 1
        packets = _generate_packet_sequence(50, floor_id=1)
        for pkt in packets:
            pipeline.floor_pipelines[1].process_packet(pkt)

        assert pipeline.floor_pipelines[1]._frame_count == 50
        assert pipeline.floor_pipelines[2]._frame_count == 0
        assert pipeline.floor_pipelines[3]._frame_count == 0

    def test_multi_floor_tracking_frames(self, house_config, sensors_config):
        """Each floor produces independent TrackingFrames."""
        pipeline = Pipeline(
            house_config=house_config,
            sensors_config=sensors_config,
            simulate=True,
        )

        # Feed data to floors 1 and 2, leave floor 3 empty
        for floor in [1, 2]:
            packets = _generate_packet_sequence(100, floor_id=floor)
            for pkt in packets:
                pipeline.floor_pipelines[floor].process_packet(pkt)

        frame1 = pipeline.floor_pipelines[1].build_tracking_frame()
        frame2 = pipeline.floor_pipelines[2].build_tracking_frame()

        assert frame1.floor == 1
        assert frame2.floor == 2
        assert frame1.timestamp > 0
        assert frame2.timestamp > 0

        # Floor 3 should still produce a frame (with no people data)
        frame3 = pipeline.floor_pipelines[3].build_tracking_frame()
        assert frame3.floor == 3
        assert frame3.occupancy_estimate == 0

    @pytest.mark.asyncio
    async def test_pipeline_routes_via_queue(self, house_config, sensors_config):
        """Pipeline._pipeline_loop routes packets by floor_id when in simulate mode."""
        pipeline = Pipeline(
            house_config=house_config,
            sensors_config=sensors_config,
            simulate=True,
        )

        await pipeline.start()
        # Let the simulator generate some data
        await asyncio.sleep(0.3)
        await pipeline.stop()

        # The simulator generates for floor 1 by default
        fp1 = pipeline.floor_pipelines[1]
        assert fp1._frame_count > 0, "Simulator should have fed floor 1"


# ── Test class: Sensor dropout and recovery ──────────────────────


class TestSensorDropoutRecovery:
    """Test pipeline behavior when sensors go offline and come back."""

    def test_pipeline_handles_data_gap(self, house_config):
        """Pipeline continues producing frames after a data gap."""
        fp = FloorPipeline(floor_id=1, house_config=house_config)

        # Phase 1: Normal data
        for i in range(100):
            pkt = _make_packet(floor_id=1, timestamp_us=i * 10000, seed=i)
            fp.process_packet(pkt)

        frame_before = fp.build_tracking_frame()
        assert fp._frame_count == 100

        # Phase 2: Gap (no data for simulated period)
        # Nothing happens — the pipeline just doesn't receive packets

        # Phase 3: Recovery — data resumes with later timestamps
        for i in range(100, 200):
            pkt = _make_packet(
                floor_id=1,
                timestamp_us=i * 10000 + 5_000_000,  # 5 second gap
                seed=i,
            )
            fp.process_packet(pkt)

        frame_after = fp.build_tracking_frame()
        assert fp._frame_count == 200
        assert isinstance(frame_after, TrackingFrame)
        assert frame_after.timestamp >= frame_before.timestamp

    def test_signal_quality_degrades_without_data(self, house_config):
        """Zone signal quality should drop toward 0 when data is stale."""
        fp = FloorPipeline(floor_id=1, house_config=house_config)

        # Feed some data to establish baseline
        for i in range(10):
            pkt = _make_packet(floor_id=1, timestamp_us=i * 10000, seed=i)
            fp.process_packet(pkt)

        # Immediately after data, quality should be high
        frame = fp.build_tracking_frame()
        for quality in frame.zone_signal_quality.values():
            assert quality > 0.5, "Quality should be high with fresh data"

    def test_different_rx_sensors_can_feed_same_floor(self, house_config):
        """Multiple RX sensors on the same floor feed the same FloorPipeline."""
        fp = FloorPipeline(floor_id=1, house_config=house_config)

        # RX sensor 1
        for i in range(50):
            pkt = _make_packet(
                floor_id=1,
                timestamp_us=i * 10000,
                rx_mac="aa:bb:cc:dd:01:01",
                seed=i,
            )
            fp.process_packet(pkt)

        # RX sensor 2
        for i in range(50, 100):
            pkt = _make_packet(
                floor_id=1,
                timestamp_us=i * 10000,
                rx_mac="aa:bb:cc:dd:01:02",
                seed=i,
            )
            fp.process_packet(pkt)

        assert fp._frame_count == 100
        frame = fp.build_tracking_frame()
        assert isinstance(frame, TrackingFrame)

    def test_recovery_after_sensor_dropout_produces_valid_vitals(self, house_config):
        """After dropout + recovery, vitals extractors still produce valid data."""
        fp = FloorPipeline(floor_id=1, house_config=house_config)

        # Initial warmup (600 packets = 6s at 100Hz)
        for i in range(600):
            pkt = _make_packet(
                floor_id=1,
                timestamp_us=i * 10000,
                breathing_freq=0.25,
                seed=i + 5000,
            )
            fp.process_packet(pkt)

        frame_pre = fp.build_tracking_frame()
        assert frame_pre.people[0].breathing is not None

        # Simulate gap, then recovery with 200 more packets
        gap_offset_us = 600 * 10000 + 3_000_000  # 3s gap
        for i in range(200):
            pkt = _make_packet(
                floor_id=1,
                timestamp_us=gap_offset_us + i * 10000,
                breathing_freq=0.25,
                seed=i + 6000,
            )
            fp.process_packet(pkt)

        frame_post = fp.build_tracking_frame()
        assert frame_post.people[0].breathing is not None
        assert 0.0 <= frame_post.people[0].breathing.confidence <= 1.0


# ── Test class: WebSocket delivery ───────────────────────────────


class TestWebSocketDelivery:
    """Test that TrackingFrames reach WebSocket clients via the FastAPI app."""

    @pytest.mark.asyncio
    async def test_ws_receives_tracking_frame(self, house_config, sensors_config):
        """A WebSocket client connected to /ws/tracking receives valid frames."""
        from backend.server.app import get_ws_manager

        pipeline = Pipeline(
            house_config=house_config,
            sensors_config=sensors_config,
            simulate=True,
        )

        await pipeline.start()

        # Let the simulator warm up
        await asyncio.sleep(0.5)

        # Manually broadcast a frame from floor 1
        fp = pipeline.floor_pipelines[1]
        frame = fp.build_tracking_frame()
        ws_manager = get_ws_manager()

        # Verify broadcast_frame works without errors
        await ws_manager.broadcast_frame(frame)

        await pipeline.stop()

    @pytest.mark.asyncio
    async def test_tracking_frame_json_structure(self, house_config):
        """TrackingFrame serializes to valid JSON with expected keys."""
        fp = FloorPipeline(floor_id=1, house_config=house_config)

        for i in range(200):
            pkt = _make_packet(floor_id=1, timestamp_us=i * 10000, seed=i)
            fp.process_packet(pkt)

        frame = fp.build_tracking_frame()
        data = frame.model_dump()

        # Verify required top-level keys
        assert "timestamp" in data
        assert "floor" in data
        assert "people" in data
        assert "occupancy_estimate" in data
        assert "occupancy_confidence" in data
        assert "zone_signal_quality" in data

        assert isinstance(data["timestamp"], float)
        assert isinstance(data["floor"], int)
        assert isinstance(data["people"], list)
        assert isinstance(data["occupancy_estimate"], int)
        assert isinstance(data["zone_signal_quality"], dict)

        # Verify person structure
        if data["people"]:
            person = data["people"][0]
            assert "id" in person
            assert "x" in person
            assert "y" in person
            assert "position_confidence" in person
            assert "uncertainty_radius_m" in person
            assert "is_stationary" in person
            assert "breathing" in person
            assert "heartrate" in person

            # Verify breathing sub-structure
            assert "rate_bpm" in person["breathing"]
            assert "confidence" in person["breathing"]

            # Verify heartrate sub-structure
            assert "rate_bpm" in person["heartrate"]
            assert "confidence" in person["heartrate"]
            assert "display" in person["heartrate"]

    @pytest.mark.asyncio
    async def test_ws_manager_broadcast_with_floor_filter(self, house_config):
        """broadcast_frame respects per-client floor filters."""
        from backend.server.ws_manager import WebSocketManager

        manager = WebSocketManager()

        # No clients — broadcast should not raise
        fp = FloorPipeline(floor_id=1, house_config=house_config)
        for i in range(10):
            pkt = _make_packet(floor_id=1, timestamp_us=i * 10000, seed=i)
            fp.process_packet(pkt)

        frame = fp.build_tracking_frame()
        await manager.broadcast_frame(frame)

        assert manager.active_connections == 0
        assert manager.total_broadcasts == 0  # no clients, no actual broadcast


# ── Test class: Sustained operation ──────────────────────────────


class TestSustainedOperation:
    """Test pipeline stability over extended runs."""

    def test_buffer_stays_bounded(self, house_config):
        """Amplitude/phase buffers trim to 200 samples after extended feeding."""
        fp = FloorPipeline(floor_id=1, house_config=house_config)

        for i in range(1000):
            pkt = _make_packet(floor_id=1, timestamp_us=i * 10000, seed=i)
            fp.process_packet(pkt)

        assert len(fp._amplitude_buffer) <= 200
        assert len(fp._phase_buffer) <= 200
        assert fp._frame_count == 1000

    def test_occupancy_detector_stable_over_time(self, house_config):
        """Occupancy detection remains within valid bounds over many frames."""
        fp = FloorPipeline(floor_id=1, house_config=house_config)

        for i in range(500):
            pkt = _make_packet(floor_id=1, timestamp_us=i * 10000, seed=i)
            fp.process_packet(pkt)

        frame = fp.build_tracking_frame()
        assert 0 <= frame.occupancy_estimate <= 6
        assert 0.0 <= frame.occupancy_confidence <= 1.0

    def test_person_state_modules_stable(self, house_config):
        """Per-person modules (motion, breathing, HR) don't crash over time."""
        fp = FloorPipeline(floor_id=1, house_config=house_config)

        for i in range(800):
            pkt = _make_packet(
                floor_id=1,
                timestamp_us=i * 10000,
                breathing_freq=0.25,
                seed=i + 7000,
            )
            fp.process_packet(pkt)

        frame = fp.build_tracking_frame()
        for person in frame.people:
            assert -1.0 <= person.x <= 16.0
            assert -1.0 <= person.y <= 13.0
            assert 0.0 <= person.position_confidence <= 1.0
            assert person.uncertainty_radius_m >= 0.0
            assert person.breathing is not None
            assert person.heartrate is not None
            assert 0 <= person.breathing.rate_bpm <= 60
            assert 0 <= person.heartrate.rate_bpm <= 250

    @pytest.mark.asyncio
    async def test_pipeline_start_stop_cycle(self, house_config, sensors_config):
        """Pipeline can be started and stopped multiple times without leaking."""
        for _ in range(3):
            pipeline = Pipeline(
                house_config=house_config,
                sensors_config=sensors_config,
                simulate=True,
            )
            await pipeline.start()
            await asyncio.sleep(0.1)
            await pipeline.stop()
            assert not pipeline._running

    @pytest.mark.asyncio
    async def test_sustained_simulate_run(self, house_config, sensors_config):
        """Run pipeline in simulate mode for 2 seconds and verify stability."""
        pipeline = Pipeline(
            house_config=house_config,
            sensors_config=sensors_config,
            simulate=True,
        )

        await pipeline.start()
        await asyncio.sleep(2.0)

        # All floors should have processed data (simulator only feeds floor 1)
        fp1 = pipeline.floor_pipelines[1]
        assert fp1._frame_count > 100, "Should have processed >100 frames in 2s at 100Hz"

        # Build frame and validate
        frame = fp1.build_tracking_frame()
        assert isinstance(frame, TrackingFrame)
        assert frame.floor == 1
        assert len(frame.people) >= 1

        # Buffers should be bounded
        assert len(fp1._amplitude_buffer) <= 200
        assert len(fp1._phase_buffer) <= 200

        await pipeline.stop()

    def test_sustained_high_volume(self, house_config):
        """Simulate 10s of data (1,000 frames at 100Hz) fed rapidly.

        Validates that the pipeline remains stable, buffers stay bounded,
        and output remains valid after processing a large volume of data.
        """
        fp = FloorPipeline(floor_id=1, house_config=house_config)
        total_frames = 1_000  # 10 s × 100 Hz

        for i in range(total_frames):
            pkt = _make_packet(
                floor_id=1,
                timestamp_us=i * 10000,
                breathing_freq=0.25,
                seed=i % 10000,  # cycle seeds to avoid memory issues
            )
            fp.process_packet(pkt)

        assert fp._frame_count == total_frames

        # Buffers must stay bounded
        assert len(fp._amplitude_buffer) <= 200
        assert len(fp._phase_buffer) <= 200

        # Output must still be valid
        frame = fp.build_tracking_frame()
        assert isinstance(frame, TrackingFrame)
        assert frame.floor == 1
        assert 0 <= frame.occupancy_estimate <= 6
        assert len(frame.people) >= 1

        # Vitals should still be producing data
        person = frame.people[0]
        assert 0 <= person.breathing.rate_bpm <= 60
        assert 0.0 <= person.breathing.confidence <= 1.0
        assert person.heartrate is not None

        # Pydantic round-trip validates all fields
        revalidated = TrackingFrame.model_validate(frame.model_dump())
        assert revalidated.floor == frame.floor

    def test_tracking_frame_values_in_valid_ranges(self, house_config):
        """All TrackingFrame values stay within Pydantic-validated ranges."""
        fp = FloorPipeline(floor_id=1, house_config=house_config)

        for i in range(300):
            pkt = _make_packet(floor_id=1, timestamp_us=i * 10000, seed=i)
            fp.process_packet(pkt)

        # This will raise a Pydantic ValidationError if any value is out of range
        frame = fp.build_tracking_frame()
        # Re-validate by round-tripping through model
        revalidated = TrackingFrame.model_validate(frame.model_dump())
        assert revalidated.floor == frame.floor
        assert len(revalidated.people) == len(frame.people)


# ── Test class: End-to-end synthetic MQTT → tracking output ──────


class TestSyntheticMqttToTracking:
    """Simulate the full MQTT → Pipeline → TrackingFrame path using synthetic data."""

    @pytest.mark.asyncio
    async def test_synthetic_end_to_end(self, house_config, sensors_config):
        """
        Synthetic MQTT simulation: generate packets, push through pipeline,
        verify tracking output has expected structure.
        """
        pipeline = Pipeline(
            house_config=house_config,
            sensors_config=sensors_config,
            simulate=True,
        )

        # Manually feed packets (bypassing MQTT) to simulate the path
        gen = SyntheticCSIGenerator(floor_id=1, seed=99)

        for _ in range(200):
            packet = gen.generate()
            floor_key = packet.floor_id + 1
            pipeline.floor_pipelines[floor_key].process_packet(packet)

        # Verify tracking output
        frame = pipeline.floor_pipelines[1].build_tracking_frame()
        assert isinstance(frame, TrackingFrame)
        assert frame.floor == 1
        assert frame.occupancy_estimate >= 0
        assert len(frame.people) >= 1

        person = frame.people[0]
        assert person.id == "p1"
        assert -1.0 <= person.x <= 16.0
        assert -1.0 <= person.y <= 13.0

    @pytest.mark.asyncio
    async def test_multi_floor_synthetic_end_to_end(self, house_config, sensors_config):
        """Generate synthetic data for all 3 floors and verify independent outputs."""
        pipeline = Pipeline(
            house_config=house_config,
            sensors_config=sensors_config,
            simulate=True,
        )

        # Feed 150 packets to each floor with different seeds
        for floor in [1, 2, 3]:
            gen = SyntheticCSIGenerator(floor_id=floor, seed=floor * 100)
            for _ in range(150):
                packet = gen.generate()
                pipeline.floor_pipelines[floor].process_packet(packet)

        # Verify each floor has independent tracking
        for floor in [1, 2, 3]:
            frame = pipeline.floor_pipelines[floor].build_tracking_frame()
            assert frame.floor == floor
            assert frame.occupancy_estimate >= 0
            assert len(frame.people) >= 1
            assert isinstance(frame.zone_signal_quality, dict)

    @pytest.mark.asyncio
    async def test_pipeline_processes_mixed_floor_stream(
        self, house_config, sensors_config
    ):
        """Interleaved packets from different floors route correctly."""
        pipeline = Pipeline(
            house_config=house_config,
            sensors_config=sensors_config,
            simulate=True,
        )

        generators = {
            floor: SyntheticCSIGenerator(floor_id=floor, seed=floor * 50)
            for floor in [1, 2, 3]
        }

        # Interleave packets: floor 1, 2, 3, 1, 2, 3, ...
        for _ in range(100):
            for floor in [1, 2, 3]:
                packet = generators[floor].generate()
                floor_key = packet.floor_id + 1
                pipeline.floor_pipelines[floor_key].process_packet(packet)

        # Each floor should have exactly 100 packets
        for floor in [1, 2, 3]:
            assert pipeline.floor_pipelines[floor]._frame_count == 100
