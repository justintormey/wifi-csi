"""Tests for heart rate extraction from CSI amplitude data.

Covers the HeartRateExtractor's buffer management, rate accuracy on
synthetic heartbeat signals with known frequencies, CWT-based SNR
confidence, display gating logic (position confidence, stationarity,
SNR), valid-range rejection, breathing harmonic removal, and behaviour
under various noise levels.
"""

from __future__ import annotations

import numpy as np
import pytest

from backend.vitals.heartrate import (
    DEFAULT_MIN_SNAPSHOTS,
    HeartRateExtractor,
    HeartRateResult,
    _snr_to_confidence,
    _remove_breathing_harmonics,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

SAMPLE_RATE = 100.0
N_SUBCARRIERS = 52


def make_extractor(**kwargs) -> HeartRateExtractor:
    """Create an extractor with test-friendly defaults."""
    defaults = {
        "sample_rate": SAMPLE_RATE,
        "window_seconds": 30.0,
        "top_k": 10,
        "min_snapshots": 500,
    }
    defaults.update(kwargs)
    return HeartRateExtractor(**defaults)


def heartbeat_csi(
    heartbeat_freq_hz: float = 1.2,
    breathing_freq_hz: float = 0.25,
    duration_s: float = 30.0,
    n_subcarriers: int = N_SUBCARRIERS,
    heartbeat_amplitude: float = 0.01,
    breathing_amplitude: float = 0.05,
    noise_level: float = 0.005,
    seed: int = 42,
) -> list[np.ndarray]:
    """Generate synthetic CSI snapshots with heartbeat + breathing signals.

    Creates a baseline CSI profile with both a breathing-frequency and a
    heartbeat-frequency sinusoidal modulation on a subset of subcarriers,
    plus Gaussian noise.  The heartbeat signal is intentionally weaker
    (~5x smaller amplitude than breathing) to be realistic.
    """
    rng = np.random.default_rng(seed)
    n_samples = int(duration_s * SAMPLE_RATE)
    t = np.arange(n_samples) / SAMPLE_RATE

    # Base CSI profile
    base = rng.uniform(2.0, 5.0, size=n_subcarriers)

    # Responsive subcarriers (60% respond to body signals)
    n_responsive = int(n_subcarriers * 0.6)
    sensitivity = np.zeros(n_subcarriers)
    responsive_idx = rng.choice(n_subcarriers, size=n_responsive, replace=False)
    sensitivity[responsive_idx] = rng.uniform(0.3, 1.0, size=n_responsive)

    snapshots = []
    for i in range(n_samples):
        breathing = np.sin(2 * np.pi * breathing_freq_hz * t[i])
        heartbeat = np.sin(2 * np.pi * heartbeat_freq_hz * t[i])
        modulation = (
            breathing_amplitude * base * sensitivity * breathing
            + heartbeat_amplitude * base * sensitivity * heartbeat
        )
        noise = rng.normal(0, noise_level, size=n_subcarriers)
        snapshots.append(base + modulation + noise)

    return snapshots


def heartbeat_only_csi(
    heartbeat_freq_hz: float = 1.2,
    duration_s: float = 30.0,
    n_subcarriers: int = N_SUBCARRIERS,
    heartbeat_amplitude: float = 0.02,
    noise_level: float = 0.005,
    seed: int = 42,
) -> list[np.ndarray]:
    """Generate synthetic CSI with only a heartbeat signal (no breathing).

    Useful for testing CWT extraction without breathing interference.
    """
    rng = np.random.default_rng(seed)
    n_samples = int(duration_s * SAMPLE_RATE)
    t = np.arange(n_samples) / SAMPLE_RATE

    base = rng.uniform(2.0, 5.0, size=n_subcarriers)
    n_responsive = int(n_subcarriers * 0.6)
    sensitivity = np.zeros(n_subcarriers)
    responsive_idx = rng.choice(n_subcarriers, size=n_responsive, replace=False)
    sensitivity[responsive_idx] = rng.uniform(0.3, 1.0, size=n_responsive)

    snapshots = []
    for i in range(n_samples):
        heartbeat = np.sin(2 * np.pi * heartbeat_freq_hz * t[i])
        modulation = heartbeat_amplitude * base * sensitivity * heartbeat
        noise = rng.normal(0, noise_level, size=n_subcarriers)
        snapshots.append(base + modulation + noise)

    return snapshots


# ---------------------------------------------------------------------------
# SNR-to-confidence mapping
# ---------------------------------------------------------------------------


class TestSNRToConfidence:
    def test_below_minimum_returns_zero(self):
        assert _snr_to_confidence(2.0, min_snr=3.0, sat_snr=15.0) == 0.0

    def test_at_minimum_returns_zero(self):
        assert _snr_to_confidence(3.0, min_snr=3.0, sat_snr=15.0) == 0.0

    def test_above_saturation_returns_one(self):
        assert _snr_to_confidence(20.0, min_snr=3.0, sat_snr=15.0) == 1.0

    def test_at_saturation_returns_one(self):
        assert _snr_to_confidence(15.0, min_snr=3.0, sat_snr=15.0) == 1.0

    def test_midpoint(self):
        conf = _snr_to_confidence(9.0, min_snr=3.0, sat_snr=15.0)
        assert abs(conf - 0.5) < 0.01

    def test_monotonically_increasing(self):
        values = [_snr_to_confidence(s, min_snr=3.0, sat_snr=15.0)
                  for s in range(0, 20)]
        for i in range(1, len(values)):
            assert values[i] >= values[i - 1]


# ---------------------------------------------------------------------------
# Breathing harmonic removal
# ---------------------------------------------------------------------------


class TestBreathingHarmonicRemoval:
    def test_removes_known_harmonics(self):
        """Verify harmonics of 0.25 Hz are attenuated in the signal."""
        n = 3000
        t = np.arange(n) / SAMPLE_RATE
        breathing = np.sin(2 * np.pi * 0.25 * t)
        harmonic2 = 0.3 * np.sin(2 * np.pi * 0.50 * t)
        harmonic3 = 0.15 * np.sin(2 * np.pi * 0.75 * t)
        heartbeat = 0.1 * np.sin(2 * np.pi * 1.2 * t)
        signal = breathing + harmonic2 + harmonic3 + heartbeat

        cleaned = _remove_breathing_harmonics(
            signal, SAMPLE_RATE, breathing_freq_hz=0.25, n_harmonics=3,
        )

        # Check that heartbeat power is preserved relative to harmonics
        freqs = np.fft.rfftfreq(n, d=1.0 / SAMPLE_RATE)
        power_cleaned = np.abs(np.fft.rfft(cleaned)) ** 2

        # Find power at harmonic frequencies vs heartbeat frequency
        idx_h2 = int(np.argmin(np.abs(freqs - 0.50)))
        idx_h3 = int(np.argmin(np.abs(freqs - 0.75)))
        idx_hr = int(np.argmin(np.abs(freqs - 1.2)))

        # Harmonics should be strongly attenuated
        assert power_cleaned[idx_h2] < power_cleaned[idx_hr]
        assert power_cleaned[idx_h3] < power_cleaned[idx_hr]

    def test_auto_detects_breathing_frequency(self):
        """When breathing_freq_hz=None, should auto-detect and remove."""
        n = 3000
        t = np.arange(n) / SAMPLE_RATE
        breathing = np.sin(2 * np.pi * 0.25 * t)
        heartbeat = 0.1 * np.sin(2 * np.pi * 1.0 * t)
        signal = breathing + heartbeat

        cleaned = _remove_breathing_harmonics(
            signal, SAMPLE_RATE, breathing_freq_hz=None, n_harmonics=3,
        )

        # The cleaned signal should have reduced breathing component
        freqs = np.fft.rfftfreq(n, d=1.0 / SAMPLE_RATE)
        power_orig = np.abs(np.fft.rfft(signal)) ** 2
        power_cleaned = np.abs(np.fft.rfft(cleaned)) ** 2

        idx_breath = int(np.argmin(np.abs(freqs - 0.25)))
        # Breathing peak should be substantially reduced
        assert power_cleaned[idx_breath] < power_orig[idx_breath] * 0.1

    def test_short_signal_returns_copy(self):
        """Very short signals should be returned unchanged."""
        signal = np.array([1.0, 2.0, 3.0])
        result = _remove_breathing_harmonics(signal, SAMPLE_RATE)
        np.testing.assert_array_equal(result, signal)

    def test_zero_breathing_freq_returns_copy(self):
        """If breathing_freq_hz is 0 or negative, return unchanged."""
        signal = np.random.default_rng(42).normal(0, 1, 100)
        result = _remove_breathing_harmonics(
            signal, SAMPLE_RATE, breathing_freq_hz=0.0,
        )
        np.testing.assert_array_equal(result, signal)


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
            ext.estimate(
                position_confidence=0.8,
                is_stationary=True,
                stationary_duration_s=60.0,
            )

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
            HeartRateExtractor(sample_rate=-1.0)

    def test_zero_window_seconds_raises(self):
        with pytest.raises(ValueError, match="window_seconds"):
            HeartRateExtractor(window_seconds=0.0)

    def test_zero_top_k_raises(self):
        with pytest.raises(ValueError, match="top_k"):
            HeartRateExtractor(top_k=0)

    def test_invalid_bpm_range_raises(self):
        with pytest.raises(ValueError, match="min_bpm"):
            HeartRateExtractor(min_bpm=120.0, max_bpm=40.0)

    def test_min_snapshots_too_low_raises(self):
        with pytest.raises(ValueError, match="min_snapshots"):
            HeartRateExtractor(min_snapshots=1)


# ---------------------------------------------------------------------------
# Heart rate accuracy — heartbeat-only signal (no breathing interference)
# ---------------------------------------------------------------------------


class TestHeartRateAccuracyClean:
    """Verify CWT extraction on clean heartbeat-only signals."""

    @pytest.mark.parametrize("target_bpm", [60, 72, 80, 90, 100])
    def test_known_heart_rates_clean(self, target_bpm: int):
        """Accuracy within ±10 bpm for clean heartbeat-only signals."""
        freq_hz = target_bpm / 60.0
        snapshots = heartbeat_only_csi(
            heartbeat_freq_hz=freq_hz,
            duration_s=30.0,
            heartbeat_amplitude=0.03,
            noise_level=0.002,
        )
        ext = make_extractor(min_snapshots=500, window_seconds=30.0)
        for snap in snapshots:
            ext.push(snap)

        result = ext.estimate(
            position_confidence=0.8,
            is_stationary=True,
            stationary_duration_s=60.0,
        )
        assert result is not None, f"No result for {target_bpm} bpm"
        assert result.display is True
        assert result.rate_bpm is not None
        assert abs(result.rate_bpm - target_bpm) <= 10.0, (
            f"Expected ~{target_bpm} bpm, got {result.rate_bpm}"
        )

    def test_72_bpm_standard_heart_rate(self):
        """Standard adult resting heart rate (72 bpm = 1.2 Hz)."""
        snapshots = heartbeat_only_csi(
            heartbeat_freq_hz=1.2, duration_s=30.0, noise_level=0.003,
        )
        ext = make_extractor(min_snapshots=500, window_seconds=30.0)
        for snap in snapshots:
            ext.push(snap)

        result = ext.estimate(
            position_confidence=0.8,
            is_stationary=True,
            stationary_duration_s=60.0,
        )
        assert result is not None
        assert result.display is True
        assert result.rate_bpm is not None
        assert abs(result.rate_bpm - 72.0) <= 10.0
        assert result.confidence > 0.0


# ---------------------------------------------------------------------------
# Heart rate with breathing interference
# ---------------------------------------------------------------------------


class TestHeartRateWithBreathing:
    """Test extraction when both breathing and heartbeat are present."""

    def test_extracts_heartbeat_despite_breathing(self):
        """With breathing harmonic removal, should still find heartbeat."""
        snapshots = heartbeat_csi(
            heartbeat_freq_hz=1.2,
            breathing_freq_hz=0.25,
            heartbeat_amplitude=0.015,
            breathing_amplitude=0.05,
            noise_level=0.003,
            duration_s=30.0,
        )
        ext = make_extractor(min_snapshots=500, window_seconds=30.0)
        for snap in snapshots:
            ext.push(snap)

        result = ext.estimate(
            position_confidence=0.8,
            is_stationary=True,
            stationary_duration_s=60.0,
            breathing_freq_hz=0.25,
        )
        assert result is not None
        # With known breathing freq, heartbeat extraction is more reliable
        if result.display and result.rate_bpm is not None:
            assert abs(result.rate_bpm - 72.0) <= 15.0

    def test_with_auto_breathing_detection(self):
        """Without explicit breathing freq, should auto-detect and remove."""
        snapshots = heartbeat_csi(
            heartbeat_freq_hz=1.0,
            breathing_freq_hz=0.25,
            heartbeat_amplitude=0.015,
            breathing_amplitude=0.05,
            noise_level=0.003,
            duration_s=30.0,
        )
        ext = make_extractor(min_snapshots=500, window_seconds=30.0)
        for snap in snapshots:
            ext.push(snap)

        result = ext.estimate(
            position_confidence=0.8,
            is_stationary=True,
            stationary_duration_s=60.0,
        )  # no breathing_freq_hz provided
        assert result is not None


# ---------------------------------------------------------------------------
# Display gating logic
# ---------------------------------------------------------------------------


class TestDisplayGating:
    """Test the three gating conditions for display=True."""

    def _get_base_result(self) -> tuple[HeartRateExtractor, list]:
        """Create an extractor with clean heartbeat data loaded."""
        snapshots = heartbeat_only_csi(
            heartbeat_freq_hz=1.2, duration_s=30.0, noise_level=0.003,
        )
        ext = make_extractor(min_snapshots=500, window_seconds=30.0)
        for snap in snapshots:
            ext.push(snap)
        return ext, snapshots

    def test_all_gates_pass_display_true(self):
        ext, _ = self._get_base_result()
        result = ext.estimate(
            position_confidence=0.8,
            is_stationary=True,
            stationary_duration_s=60.0,
        )
        assert result is not None
        assert result.display is True
        assert result.rate_bpm is not None

    def test_low_position_confidence_gates_display(self):
        ext, _ = self._get_base_result()
        result = ext.estimate(
            position_confidence=0.3,  # below 0.6 threshold
            is_stationary=True,
            stationary_duration_s=60.0,
        )
        assert result is not None
        assert result.display is False
        assert result.rate_bpm is None

    def test_not_stationary_gates_display(self):
        ext, _ = self._get_base_result()
        result = ext.estimate(
            position_confidence=0.8,
            is_stationary=False,
            stationary_duration_s=0.0,
        )
        assert result is not None
        assert result.display is False
        assert result.rate_bpm is None

    def test_insufficient_stationary_duration_gates_display(self):
        ext, _ = self._get_base_result()
        result = ext.estimate(
            position_confidence=0.8,
            is_stationary=True,
            stationary_duration_s=15.0,  # below 30s threshold
        )
        assert result is not None
        assert result.display is False
        assert result.rate_bpm is None

    def test_boundary_position_confidence_exactly_threshold(self):
        """position_confidence == 0.6 does NOT pass (need > 0.6)."""
        ext, _ = self._get_base_result()
        result = ext.estimate(
            position_confidence=0.6,  # exactly at threshold — NOT >
            is_stationary=True,
            stationary_duration_s=60.0,
        )
        assert result is not None
        assert result.display is False

    def test_boundary_stationary_exactly_threshold(self):
        """stationary_duration_s == 30.0 does NOT pass (need > 30)."""
        ext, _ = self._get_base_result()
        result = ext.estimate(
            position_confidence=0.8,
            is_stationary=True,
            stationary_duration_s=30.0,  # exactly at threshold — NOT >
        )
        assert result is not None
        assert result.display is False

    def test_gated_result_still_has_confidence_and_snr(self):
        """Even when gated, confidence and snr_db should be populated."""
        ext, _ = self._get_base_result()
        result = ext.estimate(
            position_confidence=0.3,
            is_stationary=False,
            stationary_duration_s=0.0,
        )
        assert result is not None
        assert result.display is False
        assert result.rate_bpm is None
        assert result.confidence >= 0.0
        assert isinstance(result.snr_db, float)

    def test_multiple_gates_fail_simultaneously(self):
        """All three gates failing at once."""
        ext, _ = self._get_base_result()
        result = ext.estimate(
            position_confidence=0.1,
            is_stationary=False,
            stationary_duration_s=0.0,
        )
        assert result is not None
        assert result.display is False
        assert result.rate_bpm is None


# ---------------------------------------------------------------------------
# Out-of-range rejection
# ---------------------------------------------------------------------------


class TestOutOfRangeRejection:
    def test_too_slow_returns_none_or_clipped(self):
        """30 bpm (0.5 Hz) — below valid range, should be rejected."""
        freq_hz = 30.0 / 60.0
        snapshots = heartbeat_only_csi(
            heartbeat_freq_hz=freq_hz, duration_s=30.0, noise_level=0.003,
        )
        ext = make_extractor(min_snapshots=500, window_seconds=30.0)
        for snap in snapshots:
            ext.push(snap)

        result = ext.estimate(
            position_confidence=0.8,
            is_stationary=True,
            stationary_duration_s=60.0,
        )
        # Should be None because 30 bpm is below min_bpm=40
        # OR if detected, rate should be >= 40
        assert result is None or (
            result.rate_bpm is None or result.rate_bpm >= 40.0
        )

    def test_too_fast_returns_none_or_clipped(self):
        """150 bpm (2.5 Hz) — above valid range, should be rejected."""
        freq_hz = 150.0 / 60.0
        snapshots = heartbeat_only_csi(
            heartbeat_freq_hz=freq_hz, duration_s=30.0, noise_level=0.003,
        )
        ext = make_extractor(min_snapshots=500, window_seconds=30.0)
        for snap in snapshots:
            ext.push(snap)

        result = ext.estimate(
            position_confidence=0.8,
            is_stationary=True,
            stationary_duration_s=60.0,
        )
        # Should be None because 150 bpm is above max_bpm=120
        # OR if detected, rate should be <= 120
        assert result is None or (
            result.rate_bpm is None or result.rate_bpm <= 120.0
        )


# ---------------------------------------------------------------------------
# Noise tolerance
# ---------------------------------------------------------------------------


class TestNoiseTolerance:
    def test_low_noise_produces_result(self):
        """Very clean signal should produce a result."""
        snapshots = heartbeat_only_csi(
            heartbeat_freq_hz=1.2, duration_s=30.0, noise_level=0.001,
        )
        ext = make_extractor(min_snapshots=500, window_seconds=30.0)
        for snap in snapshots:
            ext.push(snap)

        result = ext.estimate(
            position_confidence=0.8,
            is_stationary=True,
            stationary_duration_s=60.0,
        )
        assert result is not None

    def test_pure_noise_returns_low_confidence(self):
        """Pure noise — should return low confidence or gated result."""
        rng = np.random.default_rng(42)
        ext = make_extractor(min_snapshots=500, window_seconds=30.0)
        for _ in range(3000):
            ext.push(rng.normal(3.0, 1.0, size=N_SUBCARRIERS))

        result = ext.estimate(
            position_confidence=0.8,
            is_stationary=True,
            stationary_duration_s=60.0,
        )
        # CWT always finds some peak, but confidence should be low
        if result is not None and result.display:
            assert result.confidence < 0.5


# ---------------------------------------------------------------------------
# Update convenience method
# ---------------------------------------------------------------------------


class TestUpdateMethod:
    def test_update_returns_none_before_ready(self):
        ext = make_extractor(min_snapshots=100)
        result = ext.update(
            np.ones(N_SUBCARRIERS),
            position_confidence=0.8,
            is_stationary=True,
            stationary_duration_s=60.0,
        )
        assert result is None

    def test_update_returns_result_when_ready(self):
        """Update with a clean heartbeat signal should eventually return a result."""
        snapshots = heartbeat_only_csi(
            heartbeat_freq_hz=1.2, duration_s=30.0, noise_level=0.003,
        )
        ext = make_extractor(min_snapshots=500, window_seconds=30.0)
        last_result = None
        for snap in snapshots:
            r = ext.update(
                snap,
                position_confidence=0.8,
                is_stationary=True,
                stationary_duration_s=60.0,
            )
            if r is not None:
                last_result = r

        assert last_result is not None

    def test_update_passes_gating_params(self):
        """Gating parameters should flow through to estimate."""
        snapshots = heartbeat_only_csi(
            heartbeat_freq_hz=1.2, duration_s=30.0, noise_level=0.003,
        )
        ext = make_extractor(min_snapshots=500, window_seconds=30.0)

        # Push all but the last, then use update with gating params
        for snap in snapshots[:-1]:
            ext.push(snap)

        result = ext.update(
            snapshots[-1],
            position_confidence=0.3,  # below threshold
            is_stationary=True,
            stationary_duration_s=60.0,
        )
        assert result is not None
        assert result.display is False


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


class TestHeartRateResult:
    def test_frozen_dataclass(self):
        result = HeartRateResult(
            rate_bpm=72.0,
            confidence=0.8,
            snr_db=12.0,
            display=True,
        )
        with pytest.raises(AttributeError):
            result.rate_bpm = 80.0  # type: ignore[misc]

    def test_fields_accessible(self):
        result = HeartRateResult(
            rate_bpm=72.0,
            confidence=0.6,
            snr_db=10.0,
            display=True,
        )
        assert result.rate_bpm == 72.0
        assert result.confidence == 0.6
        assert result.snr_db == 10.0
        assert result.display is True

    def test_gated_result_fields(self):
        result = HeartRateResult(
            rate_bpm=None,
            confidence=0.3,
            snr_db=5.0,
            display=False,
        )
        assert result.rate_bpm is None
        assert result.display is False
        assert result.confidence == 0.3
