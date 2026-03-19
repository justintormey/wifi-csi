"""Guided calibration walk data collector for WiFi CSI fingerprinting.

Manages a calibration session for a single floor: generates a serpentine grid
of points, collects CSI frames at each point, runs the signal-processing
pipeline (phase sanitization → subcarrier selection → feature extraction),
and populates the ``FingerprintDB`` with the resulting fingerprints.

State machine::

    IDLE ──start()──▶ COLLECTING ──pause()──▶ PAUSED
      ▲                  │   ▲                  │
      │               finish()  resume()────────┘
      │                  │
      │                  ▼
      └──cancel()──── COMPLETE

At each grid point the user calls ``start_point()`` to activate collection,
then feeds CSI frames via ``add_frame()``.  When ``frames_per_point`` frames
are received the point auto-completes, features are extracted, and a
fingerprint is added to the floor database.

Usage::

    session = CalibrationSession.from_house_config(floor_id=1, house_config=cfg)
    session.start()
    session.start_point()            # activate first grid point
    for packet in csi_stream:
        done = session.add_frame(packet.amplitude_array, packet.phase_array)
        if done:
            session.start_point()    # advance to next point
    session.finish()
    session.save(db_dir="/data/fingerprints")
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

import numpy as np
from numpy.typing import NDArray

from backend.processor.feature_extractor import extract_features
from backend.processor.phase_sanitizer import sanitize_phase_batch
from backend.processor.subcarrier_selector import select_top_k
from backend.tracker.fingerprint_db import (
    Fingerprint,
    FingerprintDB,
    FloorDB,
    compute_sensor_config_hash,
)

logger = logging.getLogger(__name__)


# ── Enums ────────────────────────────────────────────────────────


class SessionState(Enum):
    """Calibration session lifecycle states."""

    IDLE = "idle"
    COLLECTING = "collecting"
    POINT_ACTIVE = "point_active"
    PAUSED = "paused"
    COMPLETE = "complete"


# ── Data classes ─────────────────────────────────────────────────


@dataclass
class GridPoint:
    """A calibration grid point with collection state."""

    x: float
    y: float
    index: int
    collected: bool = False
    skipped: bool = False
    frame_count: int = 0
    amplitudes: list[NDArray[np.float64]] = field(default_factory=list)
    phases: list[NDArray[np.float64]] = field(default_factory=list)


@dataclass
class CollectedPoint:
    """Finalized data for a single grid point after collection."""

    x: float
    y: float
    amplitude_matrix: NDArray[np.float64]  # (T, S) — T frames, S subcarriers
    phase_matrix: NDArray[np.float64]  # (T, S)
    feature_vector: NDArray[np.float64]  # (4*K,)
    frame_count: int
    timestamp: float


@dataclass
class CalibrationProgress:
    """Snapshot of calibration progress for REST API responses."""

    state: str
    floor: int
    total_points: int
    completed_points: int
    skipped_points: int
    progress_pct: float
    current_point: Optional[dict]  # {x, y, index, frame_count, frames_required}
    elapsed_s: float
    estimated_remaining_s: float


# ── Grid generation ──────────────────────────────────────────────


def generate_grid(
    width_m: float,
    depth_m: float,
    resolution_m: float = 1.0,
    margin_m: float = 0.5,
) -> list[tuple[float, float]]:
    """Generate a serpentine calibration grid within floor bounds.

    Points are generated in a back-and-forth (boustrophedon) pattern that
    minimizes walking distance during calibration.

    Args:
        width_m: Floor width in meters.
        depth_m: Floor depth in meters.
        resolution_m: Distance between grid points.
        margin_m: Inset from floor edges (sensors are near walls).

    Returns:
        List of (x, y) grid coordinates in walk order.
    """
    if width_m <= 0 or depth_m <= 0:
        raise ValueError(f"Floor dimensions must be positive: {width_m}x{depth_m}")
    if resolution_m <= 0:
        raise ValueError(f"Grid resolution must be positive: {resolution_m}")

    x_start = margin_m
    x_end = width_m - margin_m
    y_start = margin_m
    y_end = depth_m - margin_m

    if x_start >= x_end or y_start >= y_end:
        raise ValueError(
            f"Margin {margin_m}m too large for {width_m}x{depth_m}m floor"
        )

    xs = np.arange(x_start, x_end + resolution_m * 0.01, resolution_m)
    ys = np.arange(y_start, y_end + resolution_m * 0.01, resolution_m)

    points: list[tuple[float, float]] = []
    for i, y in enumerate(ys):
        row_xs = xs if i % 2 == 0 else xs[::-1]  # serpentine
        for x in row_xs:
            points.append((round(float(x), 2), round(float(y), 2)))

    return points


# ── Collector ────────────────────────────────────────────────────


class CalibrationSession:
    """Manages a single-floor calibration walk with fingerprint extraction.

    Args:
        floor_id: Floor number (1-indexed).
        width_m: Floor width in meters.
        depth_m: Floor depth in meters.
        grid_resolution_m: Distance between calibration points.
        frames_per_point: CSI frames to collect at each point (300 = 3s at 100Hz).
        margin_m: Inset from floor edges.
        top_k_subcarriers: Number of subcarriers for feature extraction.
        sensor_positions: Sensor position dict for config hash (optional).
    """

    def __init__(
        self,
        floor_id: int,
        width_m: float,
        depth_m: float,
        grid_resolution_m: float = 1.0,
        frames_per_point: int = 300,
        margin_m: float = 0.5,
        top_k_subcarriers: int = 30,
        sensor_positions: Optional[dict] = None,
    ) -> None:
        self.floor_id = floor_id
        self.grid_resolution_m = grid_resolution_m
        self.frames_per_point = frames_per_point
        self.top_k_subcarriers = top_k_subcarriers
        self._sensor_config_hash = (
            compute_sensor_config_hash(sensor_positions)
            if sensor_positions
            else ""
        )

        # Generate grid
        coords = generate_grid(width_m, depth_m, grid_resolution_m, margin_m)
        self.grid_points: list[GridPoint] = [
            GridPoint(x=x, y=y, index=i) for i, (x, y) in enumerate(coords)
        ]

        self.state = SessionState.IDLE
        self._current_index: int = -1
        self.started_at: Optional[float] = None
        self.completed_at: Optional[float] = None

        # Collected data with feature vectors
        self._collected: list[CollectedPoint] = []

        # Fingerprint database (initialized on first save or explicit init)
        self._floor_db: Optional[FloorDB] = None

    @classmethod
    def from_house_config(
        cls,
        floor_id: int,
        house_config: dict,
        **kwargs,
    ) -> CalibrationSession:
        """Create a session from house.yaml configuration.

        Args:
            floor_id: Floor number (1-indexed).
            house_config: Parsed house.yaml dict.
            **kwargs: Additional arguments passed to __init__.
        """
        floors = house_config.get("floors", {})
        floor_cfg = floors.get(floor_id) or floors.get(str(floor_id))
        if not floor_cfg:
            raise ValueError(f"Floor {floor_id} not found in house config")

        dims = floor_cfg["dimensions"]
        return cls(
            floor_id=floor_id,
            width_m=dims["width_m"],
            depth_m=dims["depth_m"],
            **kwargs,
        )

    # ── Properties ───────────────────────────────────────────────

    @property
    def total_points(self) -> int:
        return len(self.grid_points)

    @property
    def collected_points(self) -> int:
        return sum(1 for p in self.grid_points if p.collected)

    @property
    def num_skipped(self) -> int:
        return sum(1 for p in self.grid_points if p.skipped)

    @property
    def progress_pct(self) -> float:
        if self.total_points == 0:
            return 0.0
        return round(100.0 * self.collected_points / self.total_points, 1)

    @property
    def current_point(self) -> Optional[GridPoint]:
        if 0 <= self._current_index < len(self.grid_points):
            return self.grid_points[self._current_index]
        return None

    @property
    def is_active(self) -> bool:
        return self.state in (
            SessionState.COLLECTING,
            SessionState.POINT_ACTIVE,
        )

    # ── State transitions ────────────────────────────────────────

    def start(self) -> GridPoint:
        """Start the calibration session. Returns the first grid point."""
        if self.state != SessionState.IDLE:
            raise RuntimeError(f"Cannot start session in state {self.state.value}")

        self.started_at = time.time()
        self.state = SessionState.COLLECTING
        self._current_index = 0
        logger.info(
            "Calibration started: floor=%d, points=%d, frames/point=%d",
            self.floor_id,
            self.total_points,
            self.frames_per_point,
        )
        return self.grid_points[0]

    def pause(self) -> None:
        """Pause calibration. Retains all progress and buffers."""
        if self.state not in (SessionState.COLLECTING, SessionState.POINT_ACTIVE):
            raise RuntimeError(f"Cannot pause in state {self.state.value}")

        self.state = SessionState.PAUSED
        logger.info(
            "Calibration paused: %d/%d points complete",
            self.collected_points,
            self.total_points,
        )

    def resume(self) -> None:
        """Resume calibration from paused state."""
        if self.state != SessionState.PAUSED:
            raise RuntimeError(f"Cannot resume from state {self.state.value}")

        # Restore to the appropriate active sub-state
        point = self.current_point
        if point and point.frame_count > 0 and not point.collected:
            self.state = SessionState.POINT_ACTIVE
        else:
            self.state = SessionState.COLLECTING

        logger.info(
            "Calibration resumed: %d/%d points complete",
            self.collected_points,
            self.total_points,
        )

    def cancel(self) -> None:
        """Cancel calibration and discard all collected data."""
        if self.state == SessionState.IDLE:
            raise RuntimeError("Cannot cancel: calibration not active")

        old_collected = self.collected_points
        self.state = SessionState.IDLE
        self._current_index = -1
        self._collected.clear()
        self._floor_db = None
        for p in self.grid_points:
            p.collected = False
            p.skipped = False
            p.frame_count = 0
            p.amplitudes.clear()
            p.phases.clear()

        logger.info(
            "Calibration cancelled: discarded %d collected points", old_collected
        )

    # ── Point collection ─────────────────────────────────────────

    def start_point(self, point_index: Optional[int] = None) -> GridPoint:
        """Begin collecting data at the current (or specified) grid point.

        Args:
            point_index: Optional specific point index. If None, uses the
                next uncollected point in sequence.

        Returns:
            The grid point where collection is now active.
        """
        if self.state == SessionState.IDLE:
            raise RuntimeError("Session not started. Call start() first.")
        if self.state == SessionState.COMPLETE:
            raise RuntimeError("Session already complete.")
        if self.state == SessionState.PAUSED:
            raise RuntimeError("Session is paused. Call resume() first.")

        if point_index is not None:
            if point_index < 0 or point_index >= len(self.grid_points):
                raise IndexError(
                    f"Point index {point_index} out of range "
                    f"[0, {len(self.grid_points)})"
                )
            self._current_index = point_index
        elif self.state == SessionState.COLLECTING:
            # Find next uncollected, unskipped point
            for i in range(len(self.grid_points)):
                if not self.grid_points[i].collected and not self.grid_points[i].skipped:
                    self._current_index = i
                    break

        point = self.grid_points[self._current_index]
        point.amplitudes.clear()
        point.phases.clear()
        point.frame_count = 0
        point.collected = False
        self.state = SessionState.POINT_ACTIVE
        return point

    def add_frame(
        self,
        amplitude: NDArray[np.floating],
        phase: NDArray[np.floating],
    ) -> bool:
        """Add a CSI frame to the current grid point.

        When enough frames are collected, automatically extracts features
        and creates a fingerprint.

        Args:
            amplitude: 1-D array of subcarrier amplitudes.
            phase: 1-D array of subcarrier phases (radians).

        Returns:
            True if the point is now complete (enough frames collected).

        Raises:
            RuntimeError: If no point is currently active.
        """
        if self.state != SessionState.POINT_ACTIVE:
            raise RuntimeError("No active point. Call start_point() first.")

        point = self.grid_points[self._current_index]
        amp = np.asarray(amplitude, dtype=np.float64).ravel()
        ph = np.asarray(phase, dtype=np.float64).ravel()

        if amp.shape != ph.shape:
            raise ValueError(
                f"Amplitude/phase shape mismatch: {amp.shape} vs {ph.shape}"
            )

        point.amplitudes.append(amp)
        point.phases.append(ph)
        point.frame_count += 1

        if point.frame_count >= self.frames_per_point:
            point.collected = True
            self._extract_and_store(point)
            self.state = SessionState.COLLECTING
            return True

        return False

    def _extract_and_store(self, point: GridPoint) -> None:
        """Run the signal processing pipeline and store the fingerprint."""
        amp_matrix = np.vstack(point.amplitudes)  # (T, S)
        phase_matrix = np.vstack(point.phases)  # (T, S)

        # Phase sanitization (vectorized batch)
        sanitized_phases = sanitize_phase_batch(phase_matrix)

        # Subcarrier selection (top-K by variance)
        k = min(self.top_k_subcarriers, amp_matrix.shape[1])
        selection = select_top_k(amp_matrix, k=k)
        selected_phases = sanitized_phases[:, selection.indices]

        # Feature extraction
        fv = extract_features(
            amplitudes=selection.data,
            phases=selected_phases,
            window_samples=self.frames_per_point,
            norm="l2",
        )

        # Initialize floor DB lazily
        if self._floor_db is None:
            self._floor_db = FloorDB(
                floor=self.floor_id,
                grid_resolution=self.grid_resolution_m,
                sensor_config_hash=self._sensor_config_hash,
            )

        # Add fingerprint to database
        fp = Fingerprint(
            x=point.x,
            y=point.y,
            floor=self.floor_id,
            feature_vector=fv.vector,
        )
        self._floor_db.add(fp)

        # Store collected point data
        self._collected.append(
            CollectedPoint(
                x=point.x,
                y=point.y,
                amplitude_matrix=amp_matrix,
                phase_matrix=phase_matrix,
                feature_vector=fv.vector.copy(),
                frame_count=point.frame_count,
                timestamp=time.time(),
            )
        )

        logger.info(
            "Point %d complete at (%.1f, %.1f) — %d frames → %d-dim feature",
            point.index,
            point.x,
            point.y,
            point.frame_count,
            fv.vector.shape[0],
        )

    def skip_point(self) -> Optional[GridPoint]:
        """Skip the current point and advance to the next uncollected one.

        Returns:
            The next uncollected point, or None if all points are done.
        """
        if self.state not in (SessionState.COLLECTING, SessionState.POINT_ACTIVE):
            raise RuntimeError(f"Cannot skip in state {self.state.value}")

        if 0 <= self._current_index < len(self.grid_points):
            self.grid_points[self._current_index].skipped = True
            self.grid_points[self._current_index].amplitudes.clear()
            self.grid_points[self._current_index].phases.clear()

        self.state = SessionState.COLLECTING

        for i in range(self._current_index + 1, len(self.grid_points)):
            if not self.grid_points[i].collected and not self.grid_points[i].skipped:
                self._current_index = i
                return self.grid_points[i]

        return None

    # ── Data access ──────────────────────────────────────────────

    def get_collected_data(self) -> list[CollectedPoint]:
        """Return all collected grid points with extracted features."""
        return list(self._collected)

    def get_floor_db(self) -> Optional[FloorDB]:
        """Return the populated FloorDB, or None if nothing collected."""
        return self._floor_db

    def finish(self) -> list[CollectedPoint]:
        """Finalize the calibration session.

        Returns collected data for all points that were successfully sampled.
        """
        if self.state == SessionState.IDLE:
            raise RuntimeError("Session was never started.")

        self.completed_at = time.time()
        self.state = SessionState.COMPLETE

        logger.info(
            "Calibration finished: floor=%d, collected=%d, skipped=%d, "
            "total=%d, elapsed=%.1fs",
            self.floor_id,
            self.collected_points,
            self.num_skipped,
            self.total_points,
            (self.completed_at - (self.started_at or self.completed_at)),
        )

        return self.get_collected_data()

    def save(self, db_dir: str | Path) -> Path:
        """Save the fingerprint database to disk.

        Args:
            db_dir: Directory for fingerprint .npz files.

        Returns:
            Path to the saved .npz file.

        Raises:
            RuntimeError: If no fingerprints have been collected.
        """
        if self._floor_db is None or self._floor_db.size == 0:
            raise RuntimeError("No fingerprint data to save")

        fp_db = FingerprintDB(db_dir)
        fp_db.floors[self.floor_id] = self._floor_db
        fp_db.save(floor=self.floor_id)

        save_path = fp_db._floor_path(self.floor_id)
        logger.info(
            "Fingerprint DB saved: floor=%d, fingerprints=%d, path=%s",
            self.floor_id,
            self._floor_db.size,
            save_path,
        )
        return save_path

    # ── Progress & overlay ───────────────────────────────────────

    def get_progress(self) -> CalibrationProgress:
        """Get current calibration progress for REST API responses."""
        elapsed = 0.0
        if self.started_at:
            end = self.completed_at or time.time()
            elapsed = end - self.started_at

        # ETA: use elapsed time per collected point
        remaining_points = self.total_points - self.collected_points - self.num_skipped
        if self.collected_points > 0 and remaining_points > 0:
            avg_per_point = elapsed / self.collected_points
            estimated_remaining = avg_per_point * remaining_points
        elif remaining_points > 0:
            # Default estimate: 3s collection + 1s transition
            estimated_remaining = remaining_points * 4.0
        else:
            estimated_remaining = 0.0

        current = None
        point = self.current_point
        if point and self.state in (
            SessionState.COLLECTING,
            SessionState.POINT_ACTIVE,
        ):
            current = {
                "x": point.x,
                "y": point.y,
                "index": point.index,
                "frame_count": point.frame_count,
                "frames_required": self.frames_per_point,
            }

        return CalibrationProgress(
            state=self.state.value,
            floor=self.floor_id,
            total_points=self.total_points,
            completed_points=self.collected_points,
            skipped_points=self.num_skipped,
            progress_pct=self.progress_pct,
            current_point=current,
            elapsed_s=round(elapsed, 1),
            estimated_remaining_s=round(estimated_remaining, 1),
        )

    def get_grid_overlay(self) -> list[dict]:
        """Get grid state for dashboard floor plan overlay.

        Returns a list of dicts suitable for JSON serialization and
        rendering as a visual grid on the SVG floor plan.
        """
        return [
            {
                "x": p.x,
                "y": p.y,
                "index": p.index,
                "status": (
                    "complete"
                    if p.collected
                    else "skipped"
                    if p.skipped
                    else "active"
                    if (p.index == self._current_index and self.state == SessionState.POINT_ACTIVE)
                    else "pending"
                ),
                "frame_count": p.frame_count,
            }
            for p in self.grid_points
        ]
