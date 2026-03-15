"""Floor detection via CSI energy comparison across floor transmitters.

Each floor has a dedicated TX on a separate WiFi channel (1, 6, 11).  A person
on a given floor perturbs that floor's TX signal most strongly, while signals
from other floors are attenuated by ~10-15 dB per floor of building material.

The detector compares amplitude variance (CSI energy) from each floor's TX over
a sliding window and selects the floor with the highest energy.  Hysteresis
prevents noisy single-frame floor flips.  Stairwell transition zones (from
house.yaml) relax hysteresis to allow legitimate floor changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from numpy.typing import NDArray


@dataclass(frozen=True)
class FloorDetectionResult:
    """Result of a single floor detection query."""

    detected_floor: int
    floor_confidence: float  # [0, 1] — margin-based confidence
    energy_scores: dict[int, float]  # floor → mean amplitude variance


@dataclass
class TransitionZone:
    """A stairwell or passage connecting two adjacent floors."""

    name: str
    floors: tuple[int, int]
    x_min: float
    x_max: float
    y_min: float
    y_max: float

    def contains(self, x: float, y: float) -> bool:
        """Check whether (x, y) falls inside this zone's bounding box."""
        return (self.x_min <= x <= self.x_max) and (self.y_min <= y <= self.y_max)


# ---------------------------------------------------------------------------
# Configurable defaults
# ---------------------------------------------------------------------------

DEFAULT_WINDOW_SAMPLES: int = 100  # sliding window length for energy calc
DEFAULT_HYSTERESIS_COUNT: int = 3  # consecutive frames before floor switch
DEFAULT_HYSTERESIS_COUNT_TRANSITION: int = 1  # relaxed count inside a zone
DEFAULT_MIN_ENERGY_THRESHOLD: float = 0.001  # ignore floors with negligible energy


class FloorDetector:
    """Stateful floor detector with hysteresis.

    Call :meth:`update` each frame with per-floor amplitude data.  The detector
    maintains an internal history buffer and only changes its floor estimate
    when a new floor dominates for ``hysteresis_count`` consecutive frames.

    For single-floor deployments, construct with a single floor ID.  The
    detector will always return that floor with confidence 1.0.
    """

    def __init__(
        self,
        floor_ids: list[int],
        transition_zones: Optional[list[TransitionZone]] = None,
        window_samples: int = DEFAULT_WINDOW_SAMPLES,
        hysteresis_count: int = DEFAULT_HYSTERESIS_COUNT,
        hysteresis_count_transition: int = DEFAULT_HYSTERESIS_COUNT_TRANSITION,
        min_energy_threshold: float = DEFAULT_MIN_ENERGY_THRESHOLD,
    ) -> None:
        if not floor_ids:
            raise ValueError("floor_ids must contain at least one floor")

        self.floor_ids = sorted(floor_ids)
        self.transition_zones = transition_zones or []
        self.window_samples = window_samples
        self.hysteresis_count = hysteresis_count
        self.hysteresis_count_transition = hysteresis_count_transition
        self.min_energy_threshold = min_energy_threshold

        # Internal state
        self._current_floor: int = self.floor_ids[0]
        self._candidate_floor: int = self._current_floor
        self._candidate_streak: int = 0

        # Per-floor amplitude history buffers: floor → ring buffer of variances
        self._history: dict[int, list[float]] = {f: [] for f in self.floor_ids}

    @property
    def current_floor(self) -> int:
        """The most recently detected floor."""
        return self._current_floor

    @property
    def is_single_floor(self) -> bool:
        return len(self.floor_ids) == 1

    def update(
        self,
        floor_amplitudes: dict[int, NDArray[np.float64]],
        position_xy: Optional[tuple[float, float]] = None,
    ) -> FloorDetectionResult:
        """Process one frame of per-floor CSI amplitudes.

        Args:
            floor_amplitudes: Mapping from floor ID to a 1-D array of CSI
                amplitudes (across subcarriers) for this frame.  Only floors
                present in ``floor_ids`` are used; others are silently ignored.
            position_xy: Optional (x, y) estimate from the localization module.
                When provided, the detector checks whether the person is inside
                a transition zone and relaxes hysteresis accordingly.

        Returns:
            FloorDetectionResult with detected floor, confidence, and per-floor
            energy scores.
        """
        # Single-floor stub — always returns the only floor
        if self.is_single_floor:
            sole = self.floor_ids[0]
            return FloorDetectionResult(
                detected_floor=sole,
                floor_confidence=1.0,
                energy_scores={sole: 1.0},
            )

        # Compute per-floor amplitude variance for this frame
        energy_scores: dict[int, float] = {}
        for fid in self.floor_ids:
            amps = floor_amplitudes.get(fid)
            if amps is None or amps.size == 0:
                energy_scores[fid] = 0.0
                continue
            energy_scores[fid] = float(np.var(amps))

        # Append to history and trim to window size
        for fid in self.floor_ids:
            self._history[fid].append(energy_scores[fid])
            if len(self._history[fid]) > self.window_samples:
                self._history[fid] = self._history[fid][-self.window_samples :]

        # Windowed mean energy per floor
        windowed_energy: dict[int, float] = {}
        for fid in self.floor_ids:
            buf = self._history[fid]
            windowed_energy[fid] = float(np.mean(buf)) if buf else 0.0

        # Select floor with highest windowed energy
        best_floor = max(self.floor_ids, key=lambda f: windowed_energy[f])
        best_energy = windowed_energy[best_floor]

        # Confidence: margin between best and second-best, normalized
        confidence = _compute_floor_confidence(windowed_energy, best_floor)

        # Apply hysteresis
        in_transition = self._in_transition_zone(position_xy)
        threshold = self.hysteresis_count_transition if in_transition else self.hysteresis_count

        if best_floor != self._current_floor:
            if best_energy < self.min_energy_threshold:
                # Too little energy on the proposed floor — keep current
                pass
            elif best_floor == self._candidate_floor:
                self._candidate_streak += 1
            else:
                # New candidate floor — reset streak
                self._candidate_floor = best_floor
                self._candidate_streak = 1

            if self._candidate_streak >= threshold:
                self._current_floor = self._candidate_floor
                self._candidate_streak = 0
        else:
            # Still on the same floor — reset candidate tracking
            self._candidate_floor = self._current_floor
            self._candidate_streak = 0

        return FloorDetectionResult(
            detected_floor=self._current_floor,
            floor_confidence=confidence,
            energy_scores=windowed_energy,
        )

    def reset(self, floor: Optional[int] = None) -> None:
        """Reset detector state, optionally to a specific floor."""
        if floor is not None:
            if floor not in self.floor_ids:
                raise ValueError(f"Floor {floor} not in {self.floor_ids}")
            self._current_floor = floor
        else:
            self._current_floor = self.floor_ids[0]

        self._candidate_floor = self._current_floor
        self._candidate_streak = 0
        self._history = {f: [] for f in self.floor_ids}

    def _in_transition_zone(self, position_xy: Optional[tuple[float, float]]) -> bool:
        """Check if the given position is inside any transition zone for the
        current or candidate floor."""
        if position_xy is None or not self.transition_zones:
            return False
        x, y = position_xy
        for zone in self.transition_zones:
            if zone.contains(x, y):
                relevant_floors = set(zone.floors)
                if self._current_floor in relevant_floors or self._candidate_floor in relevant_floors:
                    return True
        return False


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _compute_floor_confidence(
    energy_scores: dict[int, float],
    best_floor: int,
) -> float:
    """Compute confidence as the normalized margin between best and runner-up.

    Returns a value in [0, 1].  A large gap between best and second-best
    gives high confidence; near-equal energies give low confidence.
    """
    if len(energy_scores) < 2:
        return 1.0

    best_energy = energy_scores[best_floor]
    others = [e for f, e in energy_scores.items() if f != best_floor]
    runner_up = max(others)

    total = best_energy + runner_up
    if total < 1e-12:
        return 0.0

    # Margin ∈ [0, 1]: 0 when equal, 1 when runner-up is 0
    margin = (best_energy - runner_up) / total
    return float(np.clip(margin, 0.0, 1.0))


def load_transition_zones(house_config: dict) -> list[TransitionZone]:
    """Parse transition zones from a loaded house.yaml config dict.

    Args:
        house_config: Parsed YAML dict with a ``transition_zones`` key.

    Returns:
        List of TransitionZone instances.
    """
    zones: list[TransitionZone] = []
    raw_zones = house_config.get("transition_zones", [])
    for z in raw_zones:
        floors = tuple(z["floors"])
        if len(floors) != 2:
            raise ValueError(f"Transition zone must connect exactly 2 floors, got {floors}")
        zones.append(
            TransitionZone(
                name=z.get("name", "unnamed"),
                floors=(floors[0], floors[1]),
                x_min=float(z["x_min"]),
                x_max=float(z["x_max"]),
                y_min=float(z["y_min"]),
                y_max=float(z["y_max"]),
            )
        )
    return zones


def get_floor_ids(house_config: dict) -> list[int]:
    """Extract sorted floor IDs from a house.yaml config dict."""
    return sorted(int(f) for f in house_config.get("floors", {}).keys())
