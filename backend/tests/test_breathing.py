"""Tests for breathing rate extraction from CSI amplitude data.

Covers the BreathingExtractor's buffer management, rate accuracy on
synthetic breathing signals with known frequencies, SNR-based confidence,
valid-range rejection, and behaviour under various noise levels.
"""

from __future__ import annotations

import numpy as np
import pytest

from backend.vitals.breathing import (
    DEFAULT_MIN_SNAPSHOTS,
    BreathingExtractor,
    BreathingResult,
    _snr_to_confidence,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

SAMPLE_RATE = 100.0
N_SUBCARRIERS = 52


def make_extractor(**kwargs) -> BreathingExtractor:
    """Create an extractor with test-friendly defaults."""
    defaults = {
        "sample_rate": SAMPLE_RATE,
        "window_seconds": 30.0,
        "top_k": 10,
        "min_snapshots": 500,
    }
    defaults.update(kwargs)
    return BreathingExtractor(**defaults)


def breathing_csi(
    breathing_freq_hz: float = 0.25,
    duration_s: float = 30.0,
    n_subcarriers: int = N_SUBCARRIERS,
    noise_level: float = 0.05,
    seed: int = 42,
) -> list[NDArray[np.float64]]:
    """Generate synthetic CSI snapshots with a known breathing signal.

    Creates a baseline CSI profile with a breathing-frequency sinusoidal
    modulation on a subset of subcarriers (simulating a person's impact
    on nearby subcarriers), plus Gaussian noise.
    """
    rng = np.random.default_rng(seed)
    n_samples = int(duration_s * SAMPLE_RATE)
    t = np.arange(n_samples) / SAMPLE_RATE

    # Base CSI profile — different mean amplitude per subcarrier
    base = rng.uniform(2.0, 5.0, size=n_subcarriers)

    # Breathing modulation: affects top ~60% of subcarriers with varying
    # sensitivity (realistic: not all subcarriers respond equally)
    n_responsive = int(n_subcarriers * 0.6)
    sensitivity = np.zeros(n_subcarriers)
    # Responsive subcarriers get random sensitivity [0.3, 1.0]
    responsive_idx = rng.choice(n_subcarriers, size=n_responsive, replace=False)
    sensitivity[responsive_idx] = rng.uniform(0.3, 1.0, size=n_responsive)

    snapshots = []
    for i in range(n_samples):
        breathing = np.sin(2 * np.pi * breathing_freq_hz * t[i])
        # Modulation amplitude: ~5% of base (realistic chest displacement)
        modulation = 0.05 * base * sensitivity * breathing
        noise = rng.normal(0, noise_level, size=n_subcarriers)
        snapshots.append(base + modulation + noise)

    return snapshots


# ---------------------------------------------------------------------------
# SNR-to-confidence mapping
# ---------------------------------------------------------------------------


class TestSNRToConfidence:
    def test_below_minimum_returns_zero(self):
        assert _snr_to_confidence(2.0, min_snr=3.0, sat_snr=20.0) == 0.0

    def test_at_minimum_returns_zero(self):
        assert _snr_to_confidence(3.0, min_snr=3.0, sat_snr=20.0) == 0.0

    def test_above_saturation_returns_one(self):
        assert _snr_to_confidence(25.0, min_snr=3.0, sat_snr=20.0) == 1.0

    def test_at_saturation_returns_one(self):
        assert _snr_to_confidence(20.0, min_snr=3.0, sat_snr=20.0) == 1.0

    def test_midpoint(self):
        conf = _snr_to_confidence(11.5, min_snr=3.0, sat_snr=20.0)
        assert abs(conf - 0.5) < 0.01

    def test_monotonically_increasing(self):
        values = [_snr_to_confidence(s, min_snr=3.0, sat_snr=20.0)
                  for s in range(0, 25)]
        for i in range(1, len(values)):
            assert values[i] >= values[i - 1]


# ---------------------------------------------------------------------------
# Buffer management
# ---------------------------------------------------------------------------


class TestBufferManagement:
    def test_not_ready_before_min_snapshots(self):
        ext = make_extractor(min_snapshots=100)
        for _ in range(99):
            ext.push(np.ones(N_SUBCARRIERS))
        assert not ext.is_ready
        ext.push(np.ones(N_SUBCARRIERS))
        assert ext.is_ready

    def test_buffer_size_tracks_pushes(self):
        ext = make_extractor()
        assert ext.buffer_size == 0
        for i in range(10):
            ext.push(np.ones(N_SUBCARRIERS))
            assert ext.buffer_size == i + 1

    def test_buffer_trims_to_window_size(self):
        window_s = 5.0  # 500 samples at 100 Hz
        ext = make_extractor(window_seconds=window_s, min_snapshots=10)
        window_samples = int(window_s * SAMPLE_RATE)
        for _ in range(window_samples + 100):
            ext.push(np.ones(N_SUBCARRIERS))
        assert ext.buffer_size == window_samples

    def test_estimate_before_ready_raises(self):
        ext = make_extractor(min_snapshots=100)
        for _ in range(50):
            ext.push(np.ones(N_SUBCARRIERS))
        with pytest.raises(RuntimeError, match="Need at least"):
            ext.estimate()

    def test_empty_amplitude_raises(self):
        ext = make_extractor()
        with pytest.raises(ValueError, match="non-empty"):
            ext.push(np.array([]))

    def test_inconsistent_subcarrier_count_raises(self):
        ext = make_extractor()
        ext.push(np.ones(52))
        with pytest.raises(ValueError, match="Expected 52"):
            ext.push(np.ones(30))

    def test_reset_clears_state(self):
        ext = make_extractor()
        for _ in range(10):
            ext.push(np.ones(N_SUBCARRIERS))
        ext.reset()
        assert ext.buffer_size == 0
        assert not ext.is_ready


# ---------------------------------------------------------------------------
# Constructor validation
# ---------------------------------------------------------------------------


class TestConstructorValidation:
    def test_negative_sample_rate_raises(self):
        with pytest.raises(ValueError, match="sample_rate"):
            BreathingExtractor(sample_rate=-1.0)

    def test_zero_window_seconds_raises(self):
        with pytest.raises(ValueError, match="window_seconds"):
            BreathingExtractor(window_seconds=0.0)

    def test_zero_top_k_raises(self):
        with pytest.raises(ValueError, match="top_k"):
            BreathingExtractor(top_k=0)

    def test_invalid_bpm_range_raises(self):
        with pytest.raises(ValueError, match="min_bpm"):
            BreathingExtractor(min_bpm=30.0, max_bpm=10.0)

    def test_min_snapshots_too_low_raises(self):
        with pytest.raises(ValueError, match="min_snapshots"):
            BreathingExtractor(min_snapshots=1)


# ---------------------------------------------------------------------------
# Breathing rate accuracy (core tests)
# ---------------------------------------------------------------------------


class TestBreathingRateAccuracy:
    """Verify rate accuracy within ±1 bpm on synthetic breathing signals."""

    @pytest.mark.parametrize("target_bpm", [12, 15, 18, 20, 24])
    def test_known_breathing_rates(self, target_bpm: int):
        """Accuracy within ±1 bpm for clean breathing signals."""
        freq_hz = target_bpm / 60.0
        snapshots = breathing_csi(
            breathing_freq_hz=freq_hz,
            duration_s=30.0,
            noise_level=0.02,
        )
        ext = make_extractor(min_snapshots=500, window_seconds=30.0)
        for snap in snapshots:
            ext.push(snap)

        result = ext.estimate()
        assert result is not None, f"No result for {target_bpm} bpm"
        assert abs(result.breathing_rate_bpm - target_bpm) <= 1.0, (
            f"Expected ~{target_bpm} bpm, got {result.breathing_rate_bpm}"
        )

    def test_15_bpm_standard_breathing(self):
        """Standard adult breathing rate (15 bpm = 0.25 Hz)."""
        snapshots = breathing_csi(breathing_freq_hz=0.25, duration_s=30.0)
        ext = make_extractor(min_snapshots=500, window_seconds=30.0)
        for snap in snapshots:
            ext.push(snap)

        result = ext.estimate()
        assert result is not None
        assert abs(result.breathing_rate_bpm - 15.0) <= 1.0
        assert result.breathing_confidence > 0.0

    def test_slow_breathing_8_bpm(self):
        """Slow breathing at the lower boundary."""
        freq_hz = 8.0 / 60.0
        snapshots = breathing_csi(
            breathing_freq_hz=freq_hz,
            duration_s=30.0,
            noise_level=0.02,
        )
        ext = make_extractor(min_snapshots=500, window_seconds=30.0)
        for snap in snapshots:
            ext.push(snap)

        result = ext.estimate()
        assert result is not None
        assert abs(result.breathing_rate_bpm - 8.0) <= 1.0

    def test_fast_breathing_28_bpm(self):
        """Fast breathing near the upper boundary."""
        freq_hz = 28.0 / 60.0
        snapshots = breathing_csi(
            breathing_freq_hz=freq_hz,
            duration_s=30.0,
            noise_level=0.02,
        )
        ext = make_extractor(min_snapshots=500, window_seconds=30.0)
        for snap in snapshots:
            ext.push(snap)

        result = ext.estimate()
        assert result is not None
        assert abs(result.breathing_rate_bpm - 28.0) <= 1.0


# ---------------------------------------------------------------------------
# Out-of-range rejection
# ---------------------------------------------------------------------------


class TestOutOfRangeRejection:
    def test_too_slow_returns_none(self):
        """5 bpm (0.083 Hz) — below valid range, should be rejected."""
        freq_hz = 5.0 / 60.0
        snapshots = breathing_csi(
            breathing_freq_hz=freq_hz, duration_s=30.0, noise_level=0.02,
        )
        ext = make_extractor(min_snapshots=500, window_seconds=30.0)
        for snap in snapshots:
            ext.push(snap)

        result = ext.estimate()
        # Should be None because 5 bpm is below min_bpm=8
        # (the bandpass also attenuates this frequency)
        assert result is None or result.breathing_rate_bpm >= 8.0

    def test_too_fast_returns_none(self):
        """40 bpm (0.667 Hz) — above valid range, should be rejected."""
        freq_hz = 40.0 / 60.0
        snapshots = breathing_csi(
            breathing_freq_hz=freq_hz, duration_s=30.0, noise_level=0.02,
        )
        ext = make_extractor(min_snapshots=500, window_seconds=30.0)
        for snap in snapshots:
            ext.push(snap)

        result = ext.estimate()
        # Should be None because 40 bpm is above max_bpm=30
        # (the bandpass also attenuates this frequency)
        assert result is None or result.breathing_rate_bpm <= 30.0


# ---------------------------------------------------------------------------
# Noise tolerance
# ---------------------------------------------------------------------------


class TestNoiseTolerance:
    def test_low_noise_high_confidence(self):
        """Clean signal should have high confidence."""
        snapshots = breathing_csi(
            breathing_freq_hz=0.25, duration_s=30.0, noise_level=0.01,
        )
        ext = make_extractor(min_snapshots=500, window_seconds=30.0)
        for snap in snapshots:
            ext.push(snap)

        result = ext.estimate()
        assert result is not None
        assert result.breathing_confidence > 0.3

    def test_moderate_noise_still_detects(self):
        """Moderate noise — rate should still be detectable."""
        snapshots = breathing_csi(
            breathing_freq_hz=0.25, duration_s=30.0, noise_level=0.1,
        )
        ext = make_extractor(min_snapshots=500, window_seconds=30.0)
        for snap in snapshots:
            ext.push(snap)

        result = ext.estimate()
        assert result is not None
        assert abs(result.breathing_rate_bpm - 15.0) <= 2.0

    def test_high_noise_returns_none_or_low_confidence(self):
        """Very high noise — signal may be undetectable."""
        snapshots = breathing_csi(
            breathing_freq_hz=0.25, duration_s=30.0, noise_level=5.0,
        )
        ext = make_extractor(min_snapshots=500, window_seconds=30.0)
        for snap in snapshots:
            ext.push(snap)

        result = ext.estimate()
        if result is not None:
            # If detected despite noise, confidence should be low
            assert result.breathing_confidence < 0.5

    def test_pure_noise_returns_none(self):
        """Pure noise with no breathing signal — should return None."""
        rng = np.random.default_rng(42)
        ext = make_extractor(min_snapshots=500, window_seconds=30.0)
        for _ in range(3000):
            ext.push(rng.normal(3.0, 1.0, size=N_SUBCARRIERS))

        result = ext.estimate()
        # Pure noise: either None or very low confidence
        if result is not None:
            assert result.breathing_confidence < 0.3


# ---------------------------------------------------------------------------
# Update convenience method
# ---------------------------------------------------------------------------


class TestUpdateMethod:
    def test_update_returns_none_before_ready(self):
        ext = make_extractor(min_snapshots=100)
        result = ext.update(np.ones(N_SUBCARRIERS))
        assert result is None

    def test_update_returns_result_when_ready(self):
        """Update with a clean breathing signal should eventually return a result."""
        snapshots = breathing_csi(
            breathing_freq_hz=0.25, duration_s=30.0, noise_level=0.02,
        )
        ext = make_extractor(min_snapshots=500, window_seconds=30.0)
        last_result = None
        for snap in snapshots:
            r = ext.update(snap)
            if r is not None:
                last_result = r

        assert last_result is not None
        assert abs(last_result.breathing_rate_bpm - 15.0) <= 1.0


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


class TestBreathingResult:
    def test_frozen_dataclass(self):
        result = BreathingResult(
            breathing_rate_bpm=15.0,
            breathing_confidence=0.8,
            snr_db=12.0,
        )
        with pytest.raises(AttributeError):
            result.breathing_rate_bpm = 20.0  # type: ignore[misc]

    def test_fields_accessible(self):
        result = BreathingResult(
            breathing_rate_bpm=18.0,
            breathing_confidence=0.6,
            snr_db=10.0,
        )
        assert result.breathing_rate_bpm == 18.0
        assert result.breathing_confidence == 0.6
        assert result.snr_db == 10.0
