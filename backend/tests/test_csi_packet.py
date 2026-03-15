"""Unit tests for CsiPacket dataclass."""

import json
import math

import numpy as np
import pytest

from backend.collector.csi_packet import (
    PACKET_SIZE,
    NUM_SUBCARRIERS,
    CsiPacket,
    MalformedPacketError,
)


# ── Helpers ──────────────────────────────────────────────────────────────

def _make_iq_pairs(i_val: int = 100, q_val: int = 0) -> list[int]:
    """Create a flat I/Q list with uniform values for all subcarriers."""
    return [i_val, q_val] * NUM_SUBCARRIERS


def _make_packet(**overrides) -> CsiPacket:
    defaults = dict(
        timestamp_us=1_000_000,
        tx_mac="aa:bb:cc:dd:ee:01",
        rx_mac="aa:bb:cc:dd:ee:02",
        rssi=-45,
        floor_id=1,
        iq_pairs=_make_iq_pairs(),
    )
    defaults.update(overrides)
    return CsiPacket(**defaults)


# ── Construction & validation ────────────────────────────────────────────

class TestConstruction:
    def test_basic_creation(self):
        pkt = _make_packet()
        assert pkt.timestamp_us == 1_000_000
        assert pkt.tx_mac == "aa:bb:cc:dd:ee:01"
        assert pkt.rssi == -45
        assert pkt.floor_id == 1
        assert len(pkt.iq_pairs) == NUM_SUBCARRIERS * 2

    def test_wrong_iq_count_raises(self):
        with pytest.raises(ValueError, match="Expected 228"):
            CsiPacket(
                timestamp_us=0,
                tx_mac="00:00:00:00:00:00",
                rx_mac="00:00:00:00:00:00",
                rssi=0,
                floor_id=0,
                iq_pairs=[1, 2, 3],
            )

    def test_frozen(self):
        pkt = _make_packet()
        with pytest.raises(AttributeError):
            pkt.rssi = -50  # type: ignore[misc]


# ── Binary serialization round-trip ──────────────────────────────────────

class TestBinarySerialization:
    def test_round_trip(self):
        original = _make_packet()
        raw = original.to_bytes()
        assert len(raw) == PACKET_SIZE
        restored = CsiPacket.from_bytes(raw)
        assert restored.timestamp_us == original.timestamp_us
        assert restored.tx_mac == original.tx_mac
        assert restored.rx_mac == original.rx_mac
        assert restored.rssi == original.rssi
        assert restored.floor_id == original.floor_id
        assert restored.iq_pairs == original.iq_pairs

    def test_wrong_size_raises(self):
        with pytest.raises(MalformedPacketError, match="Expected.*bytes"):
            CsiPacket.from_bytes(b"\x00" * 10)

    def test_negative_rssi_preserved(self):
        pkt = _make_packet(rssi=-80)
        restored = CsiPacket.from_bytes(pkt.to_bytes())
        assert restored.rssi == -80

    def test_negative_iq_values(self):
        iq = [-100, -200] * NUM_SUBCARRIERS
        pkt = _make_packet(iq_pairs=iq)
        restored = CsiPacket.from_bytes(pkt.to_bytes())
        assert restored.iq_pairs == iq


# ── JSON serialization round-trip ────────────────────────────────────────

class TestJsonSerialization:
    def test_round_trip(self):
        original = _make_packet()
        j = original.to_json()
        restored = CsiPacket.from_json(j)
        assert restored.timestamp_us == original.timestamp_us
        assert restored.iq_pairs == original.iq_pairs

    def test_dict_round_trip(self):
        original = _make_packet()
        d = original.to_dict()
        assert isinstance(d, dict)
        restored = CsiPacket.from_dict(d)
        assert restored.tx_mac == original.tx_mac

    def test_json_is_valid(self):
        pkt = _make_packet()
        parsed = json.loads(pkt.to_json())
        assert parsed["rssi"] == -45
        assert len(parsed["iq_pairs"]) == NUM_SUBCARRIERS * 2


# ── Amplitude & phase conversion ─────────────────────────────────────────

class TestAmplitudePhase:
    def test_pure_real_amplitude(self):
        """I=100, Q=0 → amplitude=100 for all subcarriers."""
        pkt = _make_packet(iq_pairs=_make_iq_pairs(100, 0))
        amp = pkt.amplitude_array
        assert amp.shape == (NUM_SUBCARRIERS,)
        np.testing.assert_allclose(amp, 100.0)

    def test_pure_imaginary_amplitude(self):
        """I=0, Q=50 → amplitude=50."""
        pkt = _make_packet(iq_pairs=_make_iq_pairs(0, 50))
        np.testing.assert_allclose(pkt.amplitude_array, 50.0)

    def test_known_3_4_5_triangle(self):
        """I=3, Q=4 → amplitude=5 (classic 3-4-5 right triangle)."""
        pkt = _make_packet(iq_pairs=_make_iq_pairs(3, 4))
        np.testing.assert_allclose(pkt.amplitude_array, 5.0)

    def test_phase_pure_real(self):
        """I=100, Q=0 → phase=0."""
        pkt = _make_packet(iq_pairs=_make_iq_pairs(100, 0))
        np.testing.assert_allclose(pkt.phase_array, 0.0)

    def test_phase_pure_imaginary(self):
        """I=0, Q=100 → phase=π/2."""
        pkt = _make_packet(iq_pairs=_make_iq_pairs(0, 100))
        np.testing.assert_allclose(pkt.phase_array, math.pi / 2)

    def test_phase_negative_real(self):
        """I=-100, Q=0 → phase=π."""
        pkt = _make_packet(iq_pairs=_make_iq_pairs(-100, 0))
        np.testing.assert_allclose(pkt.phase_array, math.pi)

    def test_phase_45_degrees(self):
        """I=100, Q=100 → phase=π/4."""
        pkt = _make_packet(iq_pairs=_make_iq_pairs(100, 100))
        np.testing.assert_allclose(pkt.phase_array, math.pi / 4)

    def test_mixed_subcarrier_values(self):
        """Verify per-subcarrier accuracy with varying I/Q."""
        iq = [0] * (NUM_SUBCARRIERS * 2)
        # Subcarrier 0: I=3, Q=4 → amp=5, phase=atan2(4,3)
        iq[0], iq[1] = 3, 4
        # Subcarrier 1: I=0, Q=7 → amp=7, phase=π/2
        iq[2], iq[3] = 0, 7
        # Subcarrier 2: I=5, Q=0 → amp=5, phase=0
        iq[4], iq[5] = 5, 0

        pkt = _make_packet(iq_pairs=iq)
        amp = pkt.amplitude_array
        phase = pkt.phase_array

        assert abs(amp[0] - 5.0) < 1e-10
        assert abs(amp[1] - 7.0) < 1e-10
        assert abs(amp[2] - 5.0) < 1e-10
        assert abs(phase[0] - math.atan2(4, 3)) < 1e-10
        assert abs(phase[1] - math.pi / 2) < 1e-10
        assert abs(phase[2] - 0.0) < 1e-10

    def test_complex_array(self):
        """Verify complex_array returns I + jQ."""
        pkt = _make_packet(iq_pairs=_make_iq_pairs(3, 4))
        c = pkt.complex_array
        assert c.shape == (NUM_SUBCARRIERS,)
        np.testing.assert_allclose(c.real, 3.0)
        np.testing.assert_allclose(c.imag, 4.0)
        np.testing.assert_allclose(np.abs(c), 5.0)


# ── Edge cases ───────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_zero_iq(self):
        """All zeros → amplitude=0, phase=0."""
        pkt = _make_packet(iq_pairs=_make_iq_pairs(0, 0))
        np.testing.assert_allclose(pkt.amplitude_array, 0.0)
        np.testing.assert_allclose(pkt.phase_array, 0.0)

    def test_max_int16_values(self):
        """I/Q at int16 limits should not overflow."""
        pkt = _make_packet(iq_pairs=_make_iq_pairs(32767, -32768))
        amp = pkt.amplitude_array
        expected = math.sqrt(32767**2 + 32768**2)
        np.testing.assert_allclose(amp[0], expected, rtol=1e-10)

    def test_large_timestamp(self):
        """uint64 timestamps should survive round-trip."""
        pkt = _make_packet(timestamp_us=2**63)
        restored = CsiPacket.from_bytes(pkt.to_bytes())
        assert restored.timestamp_us == 2**63
