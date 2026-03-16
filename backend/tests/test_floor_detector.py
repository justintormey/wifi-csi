"""Tests for the floor detection module."""

from __future__ import annotations

import numpy as np
import pytest

from backend.tracker.floor_detector import (
    FloorDetectionResult,
    FloorDetector,
    TransitionZone,
    _compute_floor_confidence,
    get_floor_ids,
    load_transition_zones,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_amplitudes(
    floor_ids: list[int],
    dominant_floor: int,
    n_subcarriers: int = 52,
    dominant_var: float = 1.0,
    attenuated_var: float = 0.1,
    seed: int = 42,
) -> dict[int, np.ndarray]:
    """Generate synthetic per-floor amplitude arrays.

    The dominant floor gets high-variance amplitudes (simulating strong body
    perturbation), while other floors get low-variance amplitudes (attenuated
    cross-floor signal).
    """
    rng = np.random.default_rng(seed)
    result: dict[int, np.ndarray] = {}
    for fid in floor_ids:
        if fid == dominant_floor:
            result[fid] = rng.normal(1.0, np.sqrt(dominant_var), n_subcarriers)
        else:
            result[fid] = rng.normal(1.0, np.sqrt(attenuated_var), n_subcarriers)
    return result


def _run_frames(
    detector: FloorDetector,
    dominant_floor: int,
    n_frames: int,
    position_xy: tuple[float, float] | None = None,
    seed_base: int = 0,
) -> FloorDetectionResult:
    """Feed n_frames of synthetic data with a given dominant floor.

    Returns the result of the last frame.
    """
    result = None
    for i in range(n_frames):
        amps = _make_amplitudes(
            detector.floor_ids,
            dominant_floor=dominant_floor,
            seed=seed_base + i,
        )
        result = detector.update(amps, position_xy=position_xy)
    assert result is not None
    return result


# ---------------------------------------------------------------------------
# FloorDetector — core detection
# ---------------------------------------------------------------------------


class TestFloorDetector:
    def test_single_floor_always_returns_that_floor(self):
        """Single-floor stub always returns floor 1 with confidence 1.0."""
        det = FloorDetector(floor_ids=[1])
        amps = {1: np.random.default_rng(0).normal(1.0, 0.5, 52)}
        result = det.update(amps)

        assert result.detected_floor == 1
        assert result.floor_confidence == 1.0
        assert det.is_single_floor is True

    def test_dominant_floor_detected_after_hysteresis(self):
        """Detector should pick the floor with highest CSI energy after
        enough consecutive frames exceed the hysteresis threshold."""
        det = FloorDetector(floor_ids=[1, 2, 3], hysteresis_count=3)

        # Feed 10 frames where floor 2 dominates
        result = _run_frames(det, dominant_floor=2, n_frames=10)

        assert result.detected_floor == 2
        assert result.floor_confidence > 0.3

    def test_hysteresis_prevents_premature_switch(self):
        """A single noisy frame should not flip the floor."""
        det = FloorDetector(floor_ids=[1, 2, 3], hysteresis_count=5)

        # Establish floor 1
        _run_frames(det, dominant_floor=1, n_frames=10, seed_base=0)
        assert det.current_floor == 1

        # Send 2 frames favoring floor 3 (below hysteresis of 5)
        _run_frames(det, dominant_floor=3, n_frames=2, seed_base=100)
        assert det.current_floor == 1  # should NOT have switched

    def test_floor_switches_after_sustained_change(self):
        """Floor should switch once the new floor dominates for hysteresis_count frames."""
        det = FloorDetector(floor_ids=[1, 2], hysteresis_count=3, window_samples=5)

        # Establish floor 1 (fills the short window)
        _run_frames(det, dominant_floor=1, n_frames=6, seed_base=0)
        assert det.current_floor == 1

        # Sustained floor 2 dominance — enough to flush window and pass hysteresis
        _run_frames(det, dominant_floor=2, n_frames=10, seed_base=200)
        assert det.current_floor == 2

    def test_energy_scores_reflect_variance(self):
        """Energy scores should be higher for the dominant floor."""
        det = FloorDetector(floor_ids=[1, 2, 3])
        result = _run_frames(det, dominant_floor=2, n_frames=5)

        assert result.energy_scores[2] > result.energy_scores[1]
        assert result.energy_scores[2] > result.energy_scores[3]

    def test_missing_floor_amplitude_treated_as_zero(self):
        """If a floor's amplitude data is missing, its energy should be 0."""
        det = FloorDetector(floor_ids=[1, 2])
        # Only provide data for floor 1
        result = det.update({1: np.ones(52) * 2.0})
        assert result.energy_scores[2] == 0.0

    def test_empty_floor_ids_raises(self):
        with pytest.raises(ValueError, match="at least one floor"):
            FloorDetector(floor_ids=[])

    def test_result_fields_are_correct_types(self):
        """All result fields should be plain Python types."""
        det = FloorDetector(floor_ids=[1, 2])
        result = _run_frames(det, dominant_floor=1, n_frames=5)

        assert isinstance(result.detected_floor, int)
        assert isinstance(result.floor_confidence, float)
        assert isinstance(result.energy_scores, dict)
        for k, v in result.energy_scores.items():
            assert isinstance(k, int)
            assert isinstance(v, float)


# ---------------------------------------------------------------------------
# Hysteresis and transition zones
# ---------------------------------------------------------------------------


class TestTransitionZones:
    def _make_detector_with_zone(self) -> FloorDetector:
        zone = TransitionZone(
            name="stairwell",
            floors=(1, 2),
            x_min=7.0, x_max=9.0,
            y_min=5.0, y_max=7.0,
        )
        return FloorDetector(
            floor_ids=[1, 2, 3],
            transition_zones=[zone],
            hysteresis_count=5,
            hysteresis_count_transition=1,
            window_samples=5,
        )

    def test_transition_zone_relaxes_hysteresis(self):
        """Inside a stairwell zone, floor should switch with relaxed hysteresis."""
        det = self._make_detector_with_zone()

        # Establish floor 1 (fills short window)
        _run_frames(det, dominant_floor=1, n_frames=6, seed_base=0)
        assert det.current_floor == 1

        # Switch to floor 2 while in the stairwell (8.0, 6.0 is inside zone)
        # Enough frames to flush window + pass relaxed hysteresis (1 frame)
        _run_frames(
            det,
            dominant_floor=2,
            n_frames=8,
            position_xy=(8.0, 6.0),
            seed_base=300,
        )
        assert det.current_floor == 2

    def test_outside_transition_zone_uses_full_hysteresis(self):
        """Outside the zone, full hysteresis_count=5 applies."""
        det = self._make_detector_with_zone()

        # Establish floor 1
        _run_frames(det, dominant_floor=1, n_frames=10, seed_base=0)

        # 3 frames of floor 2 dominance outside zone (position far from stairwell)
        _run_frames(
            det,
            dominant_floor=2,
            n_frames=3,
            position_xy=(1.0, 1.0),
            seed_base=400,
        )
        assert det.current_floor == 1  # NOT switched — need 5 frames

    def test_zone_contains_boundary(self):
        zone = TransitionZone(
            name="test", floors=(1, 2),
            x_min=0.0, x_max=2.0, y_min=0.0, y_max=2.0,
        )
        assert zone.contains(0.0, 0.0) is True  # min corner
        assert zone.contains(2.0, 2.0) is True  # max corner
        assert zone.contains(1.0, 1.0) is True  # interior
        assert zone.contains(3.0, 1.0) is False  # outside

    def test_no_position_means_no_relaxation(self):
        """Without position_xy, transition zone relaxation is not applied."""
        det = self._make_detector_with_zone()

        _run_frames(det, dominant_floor=1, n_frames=10, seed_base=0)
        # 2 frames without position — should NOT switch with hysteresis=5
        _run_frames(
            det,
            dominant_floor=2,
            n_frames=2,
            position_xy=None,
            seed_base=500,
        )
        assert det.current_floor == 1


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------


class TestReset:
    def test_reset_clears_state(self):
        det = FloorDetector(floor_ids=[1, 2, 3])
        _run_frames(det, dominant_floor=2, n_frames=10)
        assert det.current_floor == 2

        det.reset()
        assert det.current_floor == 1  # first floor
        assert all(len(buf) == 0 for buf in det._history.values())

    def test_reset_to_specific_floor(self):
        det = FloorDetector(floor_ids=[1, 2, 3])
        det.reset(floor=3)
        assert det.current_floor == 3

    def test_reset_invalid_floor_raises(self):
        det = FloorDetector(floor_ids=[1, 2])
        with pytest.raises(ValueError, match="Floor 5"):
            det.reset(floor=5)


# ---------------------------------------------------------------------------
# _compute_floor_confidence
# ---------------------------------------------------------------------------


class TestComputeFloorConfidence:
    def test_single_floor_is_always_confident(self):
        assert _compute_floor_confidence({1: 5.0}, best_floor=1) == 1.0

    def test_equal_energy_gives_zero_confidence(self):
        c = _compute_floor_confidence({1: 1.0, 2: 1.0}, best_floor=1)
        assert c == 0.0

    def test_dominant_floor_gives_high_confidence(self):
        c = _compute_floor_confidence({1: 10.0, 2: 0.5, 3: 0.3}, best_floor=1)
        assert c > 0.8

    def test_zero_energy_everywhere_gives_zero(self):
        c = _compute_floor_confidence({1: 0.0, 2: 0.0}, best_floor=1)
        assert c == 0.0

    def test_confidence_always_in_range(self):
        """Fuzz test — confidence is always [0, 1]."""
        rng = np.random.default_rng(42)
        for _ in range(100):
            scores = {f: rng.uniform(0, 10) for f in [1, 2, 3]}
            best = max(scores, key=lambda f: scores[f])
            c = _compute_floor_confidence(scores, best)
            assert 0.0 <= c <= 1.0


# ---------------------------------------------------------------------------
# Config loaders
# ---------------------------------------------------------------------------


class TestConfigLoaders:
    def test_load_transition_zones(self):
        config = {
            "transition_zones": [
                {
                    "name": "Main Stairwell",
                    "floors": [1, 2],
                    "x_min": 7.0, "x_max": 9.0,
                    "y_min": 5.0, "y_max": 7.0,
                },
            ],
        }
        zones = load_transition_zones(config)
        assert len(zones) == 1
        assert zones[0].name == "Main Stairwell"
        assert zones[0].floors == (1, 2)

    def test_load_empty_zones(self):
        assert load_transition_zones({}) == []

    def test_invalid_zone_floor_count_raises(self):
        config = {
            "transition_zones": [
                {"floors": [1, 2, 3], "x_min": 0, "x_max": 1, "y_min": 0, "y_max": 1},
            ],
        }
        with pytest.raises(ValueError, match="exactly 2 floors"):
            load_transition_zones(config)

    def test_get_floor_ids(self):
        config = {"floors": {3: {}, 1: {}, 2: {}}}
        assert get_floor_ids(config) == [1, 2, 3]

    def test_get_floor_ids_empty(self):
        assert get_floor_ids({}) == []


# ---------------------------------------------------------------------------
# Coverage gap tests
# ---------------------------------------------------------------------------


class TestFloorDetectorCoverageGaps:
    """Cover floor_detector.py line 167: energy below min_energy_threshold."""

    def test_low_energy_candidate_stays_on_current_floor(self):
        """When candidate floor energy is below min threshold, don't switch.
        Covers line 167: best_energy < min_energy_threshold → pass (keep current)."""
        det = FloorDetector(
            floor_ids=[1, 2],
            min_energy_threshold=10.0,  # very high threshold
            hysteresis_count=1,
            window_samples=5,
        )
        rng = np.random.default_rng(42)

        # First, establish floor 1 as current with strong energy (high variance)
        for _ in range(5):
            det.update({
                1: rng.uniform(0.0, 20.0, size=52),  # high variance
                2: rng.uniform(4.9, 5.1, size=52),    # low variance
            })

        # Now present floor 2 with slightly higher variance than floor 1,
        # but both below the high min_energy_threshold
        for _ in range(10):
            result = det.update({
                1: rng.uniform(4.99, 5.01, size=52),   # very low variance ~0
                2: rng.uniform(4.95, 5.05, size=52),    # slightly more variance but still tiny
            })

        # Should stay on floor 1 because even though floor 2 might edge ahead,
        # its energy is below the 10.0 threshold
        assert result.detected_floor == 1
