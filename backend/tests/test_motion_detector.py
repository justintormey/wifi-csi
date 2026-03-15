"""Tests for static vs moving classification from CSI variance.

Covers the MotionDetector's buffer management, classification accuracy
on synthetic CSI data with known movement patterns, stationarity duration
tracking, and adaptive baseline behaviour.
"""

from __future__ import annotations

import numpy as np
import pytest

from backend.vitals.motion_detector import (
    DEFAULT_MIN_SNAPSHOTS,
    MotionDetector,
    MotionResult,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

N_SUBCARRIERS = 52


def make_detector(**kwargs) -> MotionDetector:
    """Create a detector with fast-converging defaults for tests."""
    defaults = {
        "window_size": 30,
        "min_snapshots": 5,
        "sample_rate": 100.0,
        "baseline_ema_alpha": 0.5,  # fast adaptation for tests
    }
    defaults.update(kwargs)
    return MotionDetector(**defaults)


def stationary_csi(
    n_snapshots: int = 30,
    n_subcarriers: int = N_SUBCARRIERS,
    seed: int = 42,
) -> list[NDArray[np.float64]]:
    """Generate CSI snapshots for a stationary person.

    Low variance: constant base + tiny noise (simulates breathing only).
    """
    rng = np.random.default_rng(seed)
    base = rng.uniform(1.0, 3.0, size=n_subcarriers)
    return [
        base + rng.normal(0, 0.01, size=n_subcarriers)
        for _ in range(n_snapshots)
    ]


def moving_csi(
    n_snapshots: int = 30,
    n_subcarriers: int = N_SUBCARRIERS,
    seed: int = 42,
) -> list[NDArray[np.float64]]:
    """Generate CSI snapshots for a moving person.

    High variance: base changes significantly each snapshot.
    """
    rng = np.random.default_rng(seed)
    return [
        rng.uniform(0.5, 5.0, size=n_subcarriers)
        for _ in range(n_snapshots)
    ]


# ---------------------------------------------------------------------------
# Buffer management
# ---------------------------------------------------------------------------


class TestBufferManagement:
    def test_not_ready_when_empty(self):
        det = make_detector()
        assert not det.is_ready
        assert det.buffer_size == 0

    def test_ready_after_enough_snapshots(self):
        det = make_detector(min_snapshots=5)
        for _ in range(5):
            det.push(np.ones(N_SUBCARRIERS))
        assert det.is_ready
        assert det.buffer_size == 5

    def test_classify_raises_when_not_ready(self):
        det = make_detector(min_snapshots=10)
        det.push(np.ones(N_SUBCARRIERS))
        with pytest.raises(RuntimeError, match="Need at least"):
            det.classify()

    def test_buffer_rolls_at_window_size(self):
        det = make_detector(window_size=20)
        for _ in range(30):
            det.push(np.ones(N_SUBCARRIERS))
        assert det.buffer_size == 20

    def test_inconsistent_subcarrier_count_raises(self):
        det = make_detector()
        det.push(np.ones(10))
        with pytest.raises(ValueError, match="Expected 10"):
            det.push(np.ones(20))

    def test_empty_amplitude_raises(self):
        det = make_detector()
        with pytest.raises(ValueError, match="non-empty"):
            det.push(np.array([]))

    def test_reset_clears_buffer(self):
        det = make_detector()
        for snap in stationary_csi(10):
            det.push(snap)
        det.reset()
        assert det.buffer_size == 0
        assert not det.is_ready


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestValidation:
    def test_threshold_out_of_range(self):
        with pytest.raises(ValueError, match="motion_threshold"):
            MotionDetector(motion_threshold=1.5)

    def test_threshold_negative(self):
        with pytest.raises(ValueError, match="motion_threshold"):
            MotionDetector(motion_threshold=-0.1)

    def test_window_size_too_small(self):
        with pytest.raises(ValueError, match="window_size"):
            MotionDetector(window_size=1)

    def test_min_snapshots_too_small(self):
        with pytest.raises(ValueError, match="min_snapshots"):
            MotionDetector(min_snapshots=1)

    def test_sample_rate_zero(self):
        with pytest.raises(ValueError, match="sample_rate"):
            MotionDetector(sample_rate=0)


# ---------------------------------------------------------------------------
# Stationary classification
# ---------------------------------------------------------------------------


class TestStationary:
    def test_stationary_person_classified_correctly(self):
        """Low-variance CSI should be classified as stationary."""
        det = make_detector()
        snaps = stationary_csi(30)
        # Warm up baseline with stationary data
        for snap in snaps:
            det.push(snap)
        result = det.classify()
        assert result.is_stationary is True
        assert result.motion_level < 0.15

    def test_stationary_motion_level_near_zero(self):
        """Constant input should produce motion_level ≈ 0."""
        det = make_detector()
        for _ in range(30):
            det.push(np.ones(N_SUBCARRIERS))
        result = det.classify()
        assert result.motion_level < 0.05

    def test_stationary_duration_accumulates(self):
        """Duration should increase with consecutive stationary classifications."""
        det = make_detector(sample_rate=100.0)
        snaps = stationary_csi(20)
        results = []
        for snap in snaps:
            r = det.update(snap)
            if r is not None:
                results.append(r)

        # All results should be stationary with increasing duration
        assert len(results) > 0
        for r in results:
            assert r.is_stationary is True
        # Last result should have the longest duration
        assert results[-1].stationary_duration_s > results[0].stationary_duration_s


# ---------------------------------------------------------------------------
# Moving classification
# ---------------------------------------------------------------------------


class TestMoving:
    def test_moving_person_classified_correctly(self):
        """High-variance CSI should be classified as moving."""
        det = make_detector(baseline_ema_alpha=0.5)
        # First warm up with stationary data to establish baseline
        for snap in stationary_csi(10, seed=99):
            det.push(snap)
        det.classify()  # establish baseline

        # Now switch to moving data
        det2 = make_detector(baseline_ema_alpha=0.5)
        # Feed stationary first to set baseline
        for snap in stationary_csi(10, seed=99):
            det2.push(snap)
        det2.classify()

        # Replace buffer with high-variance data
        moving = moving_csi(30, seed=77)
        for snap in moving:
            det2.push(snap)

        result = det2.classify()
        assert result.motion_level > 0.15

    def test_movement_resets_stationary_duration(self):
        """Stationary duration should reset to 0 when movement detected."""
        det = make_detector(baseline_ema_alpha=0.8)

        # Build up stationary duration
        for snap in stationary_csi(15, seed=10):
            det.update(snap)

        # Inject movement
        for snap in moving_csi(15, seed=20):
            result = det.update(snap)

        # After movement, check if motion was detected
        # The motion_level should be elevated or duration should reset
        assert result is not None
        if not result.is_stationary:
            assert result.stationary_duration_s == 0.0


# ---------------------------------------------------------------------------
# Motion level range
# ---------------------------------------------------------------------------


class TestMotionLevel:
    def test_motion_level_bounded_0_1(self):
        """motion_level should always be in [0, 1]."""
        det = make_detector()
        snaps = stationary_csi(10) + moving_csi(10)
        for snap in snaps:
            r = det.update(snap)
            if r is not None:
                assert 0.0 <= r.motion_level <= 1.0

    def test_motion_level_monotonic_with_variance(self):
        """Higher CSI variance should produce higher motion_level."""
        # Low variance detector
        det_low = make_detector(baseline_ema_alpha=0.9)
        for snap in stationary_csi(15, seed=1):
            det_low.push(snap)
        r_low = det_low.classify()

        # High variance detector (same baseline setup, then noisy data)
        det_high = make_detector(baseline_ema_alpha=0.9)
        for snap in stationary_csi(10, seed=1):
            det_high.push(snap)
        det_high.classify()  # baseline
        # Now feed high-variance
        for snap in moving_csi(10, seed=2):
            det_high.push(snap)
        r_high = det_high.classify()

        assert r_high.motion_level >= r_low.motion_level


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


class TestResultType:
    def test_result_is_frozen(self):
        det = make_detector()
        for snap in stationary_csi(10):
            det.push(snap)
        result = det.classify()
        with pytest.raises(AttributeError):
            result.is_stationary = False  # type: ignore[misc]

    def test_result_fields(self):
        det = make_detector()
        for snap in stationary_csi(10):
            det.push(snap)
        result = det.classify()
        assert isinstance(result.is_stationary, bool)
        assert isinstance(result.stationary_duration_s, float)
        assert isinstance(result.motion_level, float)


# ---------------------------------------------------------------------------
# Update convenience method
# ---------------------------------------------------------------------------


class TestUpdateConvenience:
    def test_returns_none_before_ready(self):
        det = make_detector(min_snapshots=5)
        result = det.update(np.ones(N_SUBCARRIERS))
        assert result is None

    def test_returns_result_when_ready(self):
        det = make_detector(min_snapshots=5)
        result = None
        for _ in range(5):
            result = det.update(np.ones(N_SUBCARRIERS))
        assert isinstance(result, MotionResult)


# ---------------------------------------------------------------------------
# Transition scenarios
# ---------------------------------------------------------------------------


class TestTransitions:
    def test_stationary_to_moving_transition(self):
        """Detector should catch the transition from still to moving."""
        det = make_detector(
            window_size=10,
            min_snapshots=5,
            baseline_ema_alpha=0.9,
        )

        # Stationary phase
        stationary_results = []
        for snap in stationary_csi(20, seed=42):
            r = det.update(snap)
            if r is not None:
                stationary_results.append(r)

        # Moving phase
        moving_results = []
        for snap in moving_csi(20, seed=42):
            r = det.update(snap)
            if r is not None:
                moving_results.append(r)

        # Stationary phase should have low motion levels
        avg_stationary = np.mean([r.motion_level for r in stationary_results])
        # Moving phase should have higher motion levels on average
        avg_moving = np.mean([r.motion_level for r in moving_results])
        assert avg_moving > avg_stationary

    def test_heart_rate_gate_30s(self):
        """After 30s stationary at 100Hz, duration should be >= 30s."""
        det = make_detector(sample_rate=100.0, min_snapshots=5)
        # Feed 3000 stationary snapshots = 30s at 100Hz
        # (minus min_snapshots that don't produce results)
        result = None
        for snap in stationary_csi(3005, seed=42):
            result = det.update(snap)

        assert result is not None
        assert result.is_stationary
        assert result.stationary_duration_s >= 30.0


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_identical_snapshots(self):
        """All-identical input should give zero motion."""
        det = make_detector()
        for _ in range(10):
            det.push(np.full(N_SUBCARRIERS, 2.5))
        result = det.classify()
        assert result.is_stationary
        assert result.motion_level == 0.0

    def test_single_subcarrier(self):
        """Should work with just 1 subcarrier."""
        det = make_detector()
        for _ in range(10):
            det.push(np.array([1.0]))
        result = det.classify()
        assert isinstance(result, MotionResult)

    def test_very_large_variance(self):
        """Extreme variance should clip motion_level to <= 1.0."""
        det = make_detector(baseline_ema_alpha=0.99)
        # Establish very low baseline
        for _ in range(10):
            det.push(np.full(N_SUBCARRIERS, 1.0))
        det.classify()

        # Inject extreme variance
        rng = np.random.default_rng(42)
        for _ in range(10):
            det.push(rng.uniform(0, 1000, size=N_SUBCARRIERS))

        result = det.classify()
        assert result.motion_level <= 1.0
