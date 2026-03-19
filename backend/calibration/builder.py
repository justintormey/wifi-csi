"""Fingerprint database builder from calibration data.

Two APIs are provided:

**Legacy function-based API** (``build_fingerprint``, ``build_floor_db``,
``build_and_save``, ``compute_coverage``):
    Per-point subcarrier selection from ``CollectedPoint`` objects produced by
    ``CalibrationSession``.

**Class-based API** (``FingerprintBuilder``):
    Global subcarrier selection across all calibration points, quality metrics,
    low-quality flagging, leave-one-out cross-validation, JSON loading, and
    expected localization accuracy reporting.  Preferred for new code.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

import numpy as np
from numpy.typing import NDArray

from backend.calibration.collector import CollectedPoint
from backend.collector.csi_packet import CsiPacket, NUM_SUBCARRIERS
from backend.processor.feature_extractor import (
    NormMethod,
    extract_features,
)
from backend.processor.phase_sanitizer import sanitize_phase, sanitize_phase_batch
from backend.processor.subcarrier_selector import select_top_k
from backend.tracker.fingerprint_db import (
    Fingerprint,
    FingerprintDB,
    FloorDB,
    compute_sensor_config_hash,
)

logger = logging.getLogger(__name__)

# Default processing parameters — match the tracking pipeline
DEFAULT_TOP_K = 30
DEFAULT_NORM = NormMethod.L2


def build_fingerprint(
    point: CollectedPoint,
    floor_id: int,
    top_k: int = DEFAULT_TOP_K,
    norm: NormMethod = DEFAULT_NORM,
) -> Optional[Fingerprint]:
    """Build a single Fingerprint from a collected grid point.

    Runs the same subcarrier selection and feature extraction pipeline used
    at tracking time to ensure consistent feature representations.

    Args:
        point: Collected CSI data for one grid position.
        floor_id: Floor number (1-indexed).
        top_k: Number of subcarriers to select by variance.
        norm: Feature vector normalization method.

    Returns:
        A Fingerprint, or None if the data was too noisy to produce
        a valid feature vector (e.g., all-zero amplitudes).
    """
    amp = point.amplitude_matrix  # (T, S)
    phase = point.phase_matrix  # (T, S)

    if amp.shape[0] < 10:
        logger.warning(
            "Point (%.1f, %.1f): only %d frames, skipping",
            point.x, point.y, amp.shape[0],
        )
        return None

    # Phase sanitization (SpotFi linear offset removal)
    sanitized_phase = np.vstack([sanitize_phase(row) for row in phase])

    # Subcarrier selection: top-K by variance
    k = min(top_k, amp.shape[1])
    selection = select_top_k(amp, k=k)
    selected_phase = sanitized_phase[:, selection.indices]

    # Feature extraction: mean_amp, var_amp, mean_phase, std_phase per subcarrier
    features = extract_features(selection.data, selected_phase, norm=norm)

    # Reject all-zero or all-NaN features
    if np.all(features.vector == 0) or np.any(np.isnan(features.vector)):
        logger.warning(
            "Point (%.1f, %.1f): degenerate feature vector, skipping",
            point.x, point.y,
        )
        return None

    return Fingerprint(
        x=point.x,
        y=point.y,
        floor=floor_id,
        feature_vector=features.vector,
    )


def build_floor_db(
    floor_id: int,
    collected_points: list[CollectedPoint],
    grid_resolution_m: float = 1.0,
    sensor_config: Optional[dict] = None,
    top_k: int = DEFAULT_TOP_K,
    norm: NormMethod = DEFAULT_NORM,
) -> FloorDB:
    """Build a FloorDB from collected calibration data.

    Args:
        floor_id: Floor number (1-indexed).
        collected_points: List of grid points with collected CSI data.
        grid_resolution_m: Calibration grid spacing.
        sensor_config: Sensor positions dict for config hash computation.
        top_k: Number of subcarriers to select.
        norm: Feature normalization method.

    Returns:
        Populated FloorDB ready to be saved.

    Raises:
        ValueError: If no valid fingerprints could be built.
    """
    config_hash = ""
    if sensor_config:
        config_hash = compute_sensor_config_hash(sensor_config)

    floor_db = FloorDB(
        floor=floor_id,
        grid_resolution=grid_resolution_m,
        sensor_config_hash=config_hash,
    )

    skipped = 0
    for point in collected_points:
        fp = build_fingerprint(point, floor_id, top_k=top_k, norm=norm)
        if fp is not None:
            floor_db.add(fp)
        else:
            skipped += 1

    if floor_db.size == 0:
        raise ValueError(
            f"Floor {floor_id}: no valid fingerprints from "
            f"{len(collected_points)} collected points"
        )

    logger.info(
        "Floor %d: built %d fingerprints (%d skipped) from %d points",
        floor_id,
        floor_db.size,
        skipped,
        len(collected_points),
    )
    return floor_db


def build_and_save(
    floor_id: int,
    collected_points: list[CollectedPoint],
    db_dir: str | Path,
    grid_resolution_m: float = 1.0,
    sensor_config: Optional[dict] = None,
    top_k: int = DEFAULT_TOP_K,
    norm: NormMethod = DEFAULT_NORM,
) -> FingerprintDB:
    """Build a FloorDB and save it to disk.

    Convenience function that combines build_floor_db() with FingerprintDB
    persistence.

    Args:
        floor_id: Floor number (1-indexed).
        collected_points: Collected calibration data.
        db_dir: Directory for .npz fingerprint files.
        grid_resolution_m: Calibration grid spacing.
        sensor_config: Sensor positions for config hash.
        top_k: Number of subcarriers to select.
        norm: Feature normalization method.

    Returns:
        FingerprintDB with the floor loaded and saved.
    """
    floor_db = build_floor_db(
        floor_id=floor_id,
        collected_points=collected_points,
        grid_resolution_m=grid_resolution_m,
        sensor_config=sensor_config,
        top_k=top_k,
        norm=norm,
    )

    db = FingerprintDB(db_dir)
    db.floors[floor_id] = floor_db
    db.save(floor=floor_id)

    logger.info(
        "Floor %d: saved %d fingerprints to %s",
        floor_id,
        floor_db.size,
        db.db_dir / f"floor_{floor_id}.npz",
    )
    return db


def compute_coverage(
    floor_db: FloorDB,
    width_m: float,
    depth_m: float,
    cell_size_m: float = 2.0,
) -> float:
    """Compute what percentage of the floor area has fingerprint coverage.

    Divides the floor into cells and checks which cells have at least one
    fingerprint within them.

    Args:
        floor_db: The fingerprint database for this floor.
        width_m: Floor width in meters.
        depth_m: Floor depth in meters.
        cell_size_m: Size of coverage grid cells.

    Returns:
        Coverage percentage (0-100).
    """
    if floor_db.size == 0:
        return 0.0

    n_cols = max(1, int(np.ceil(width_m / cell_size_m)))
    n_rows = max(1, int(np.ceil(depth_m / cell_size_m)))
    total_cells = n_cols * n_rows

    # Mark cells that contain at least one fingerprint
    covered = set()
    for i in range(floor_db.size):
        x, y = floor_db.positions[i]
        col = min(int(x / cell_size_m), n_cols - 1)
        row = min(int(y / cell_size_m), n_rows - 1)
        covered.add((col, row))

    return round(100.0 * len(covered) / total_cells, 1)


# ── Class-based builder with quality metrics and cross-validation ────────


@dataclass
class CalibrationPoint:
    """Raw data for one grid position during calibration walk."""

    x: float
    y: float
    floor: int
    packets: list[CsiPacket]


@dataclass
class PointQuality:
    """Quality metrics for a single calibration grid point."""

    x: float
    y: float
    snr: float  # mean amplitude / std amplitude (across selected subcarriers)
    variance: float  # mean amplitude variance across selected subcarriers
    confidence: float  # combined quality score in [0, 1]
    flagged_for_recalibration: bool


@dataclass
class BuildResult:
    """Summary of a completed fingerprint DB build."""

    floor: int
    num_fingerprints: int
    feature_dim: int
    k_subcarriers: int
    subcarrier_indices: NDArray[np.intp]
    grid_resolution: float
    sensor_config_hash: str
    db_path: Path
    point_qualities: list[PointQuality]
    num_flagged: int
    loo_accuracy: float | None  # fraction of LOO queries within grid resolution
    loo_mean_error_m: float | None  # mean Euclidean error in meters


class FingerprintBuilder:
    """Builds a fingerprint DB from calibration walk data with global subcarrier
    selection, quality metrics, and leave-one-out cross-validation.

    Unlike the legacy function-based API which selects subcarriers per-point,
    this class pools all calibration amplitude data to select a single set of
    top-K subcarrier indices used uniformly across all fingerprints.  This
    ensures the feature space is consistent for cosine-distance KNN.

    Usage::

        builder = FingerprintBuilder(db_dir="db", floor=1,
                                     sensor_positions={...})
        builder.add_points(calibration_points)
        result = builder.build_and_save()
        print(result.loo_mean_error_m, result.num_flagged)
    """

    def __init__(
        self,
        db_dir: str | Path,
        floor: int,
        grid_resolution: float = 1.0,
        sensor_positions: dict | None = None,
        k_subcarriers: int = 30,
        window_samples: int = 100,
        norm: NormMethod | str = NormMethod.L2,
        min_packets_per_point: int = 30,
        quality_threshold: float = 0.3,
    ) -> None:
        self.db_dir = Path(db_dir)
        self.floor = floor
        self.grid_resolution = grid_resolution
        self.sensor_positions = sensor_positions or {}
        self.k_subcarriers = k_subcarriers
        self.window_samples = window_samples
        self.norm = NormMethod(norm) if isinstance(norm, str) else norm
        self.min_packets_per_point = min_packets_per_point
        self.quality_threshold = quality_threshold

        self._points: list[CalibrationPoint] = []
        self._positions_set: set[tuple[float, float]] = set()

    @property
    def num_points(self) -> int:
        return len(self._points)

    def add_point(self, point: CalibrationPoint) -> None:
        """Add a calibration point.  Validates floor and packet count."""
        if point.floor != self.floor:
            raise ValueError(
                f"Point floor {point.floor} does not match builder floor {self.floor}"
            )
        if len(point.packets) < self.min_packets_per_point:
            raise ValueError(
                f"Point at ({point.x}, {point.y}) has {len(point.packets)} packets, "
                f"minimum is {self.min_packets_per_point}"
            )
        pos_key = (point.x, point.y)
        if pos_key in self._positions_set:
            raise ValueError(
                f"Duplicate position ({point.x}, {point.y}) — already added"
            )
        self._positions_set.add(pos_key)
        self._points.append(point)

    def add_points(self, points: Sequence[CalibrationPoint]) -> None:
        for p in points:
            self.add_point(p)

    def build_and_save(self) -> BuildResult:
        """Process all points, build the DB, and save to disk."""
        if not self._points:
            raise ValueError("No calibration points added — cannot build")

        # Step 1: Extract amplitude and phase matrices per point
        point_data = self._extract_matrices()

        # Step 2: Global subcarrier selection — pool all amplitudes
        global_indices = self._select_global_subcarriers(point_data)

        # Step 3: Build feature vectors and quality metrics
        sensor_hash = compute_sensor_config_hash(self.sensor_positions)
        db = FingerprintDB(self.db_dir)
        floor_db = db.get_floor(
            self.floor,
            grid_resolution=self.grid_resolution,
            sensor_config_hash=sensor_hash,
        )

        features_list: list[NDArray[np.float64]] = []
        positions_list: list[tuple[float, float]] = []
        qualities: list[PointQuality] = []

        for i, point in enumerate(self._points):
            amp_matrix, phase_matrix = point_data[i]

            # Phase sanitization (vectorized batch)
            sanitized_phases = sanitize_phase_batch(phase_matrix)

            # Select using global subcarrier indices
            selected_amp = amp_matrix[:, global_indices]
            selected_phase = sanitized_phases[:, global_indices]

            # Extract feature vector
            fv = extract_features(
                selected_amp,
                selected_phase,
                window_samples=self.window_samples,
                norm=self.norm,
            )

            # Quality metrics
            quality = self._compute_quality(point, selected_amp)
            qualities.append(quality)

            # Add to DB
            fp = Fingerprint(
                x=point.x, y=point.y, floor=self.floor, feature_vector=fv.vector
            )
            floor_db.add(fp)
            features_list.append(fv.vector)
            positions_list.append((point.x, point.y))

        db.save(self.floor)

        # Step 4: Leave-one-out cross-validation
        loo_accuracy, loo_mean_error = self._leave_one_out_cv(
            features_list, positions_list
        )

        num_flagged = sum(1 for q in qualities if q.flagged_for_recalibration)

        return BuildResult(
            floor=self.floor,
            num_fingerprints=len(self._points),
            feature_dim=4 * self.k_subcarriers,
            k_subcarriers=self.k_subcarriers,
            subcarrier_indices=global_indices,
            grid_resolution=self.grid_resolution,
            sensor_config_hash=sensor_hash,
            db_path=self.db_dir / f"floor_{self.floor}.npz",
            point_qualities=qualities,
            num_flagged=num_flagged,
            loo_accuracy=loo_accuracy,
            loo_mean_error_m=loo_mean_error,
        )

    def _extract_matrices(
        self,
    ) -> list[tuple[NDArray[np.float64], NDArray[np.float64]]]:
        """Extract (amplitude_matrix, phase_matrix) per point from raw packets."""
        result = []
        for point in self._points:
            amps = np.array(
                [p.amplitude_array for p in point.packets], dtype=np.float64
            )
            phases = np.array(
                [p.phase_array for p in point.packets], dtype=np.float64
            )
            result.append((amps, phases))
        return result

    def _select_global_subcarriers(
        self,
        point_data: list[tuple[NDArray[np.float64], NDArray[np.float64]]],
    ) -> NDArray[np.intp]:
        """Pool all amplitude data and select top-K subcarrier indices globally."""
        all_amps = np.vstack([amp for amp, _ in point_data])
        selection = select_top_k(
            all_amps,
            k=self.k_subcarriers,
            window_samples=all_amps.shape[0],
        )
        return selection.indices

    def _compute_quality(
        self,
        point: CalibrationPoint,
        selected_amp: NDArray[np.float64],
    ) -> PointQuality:
        """Compute quality metrics for a single calibration point."""
        mean_amp = np.mean(selected_amp)
        std_amp = np.std(selected_amp)
        snr = mean_amp / std_amp if std_amp > 0 else float("inf")

        per_sc_var = np.var(selected_amp, axis=0)
        variance = float(np.mean(per_sc_var))

        # Confidence: linear mapping of SNR to [0, 1], clamped
        confidence = float(np.clip(snr / 15.0, 0.0, 1.0))
        flagged = confidence < self.quality_threshold

        return PointQuality(
            x=point.x,
            y=point.y,
            snr=float(snr),
            variance=variance,
            confidence=confidence,
            flagged_for_recalibration=flagged,
        )

    def _leave_one_out_cv(
        self,
        features: list[NDArray[np.float64]],
        positions: list[tuple[float, float]],
    ) -> tuple[float | None, float | None]:
        """Leave-one-out cross-validation using cosine-distance KNN (k=1).

        Returns (accuracy, mean_error_meters).  Accuracy is the fraction of
        queries where the nearest neighbor is within grid_resolution distance.
        """
        n = len(features)
        if n < 3:
            return None, None

        feat_array = np.array(features)  # (N, D)
        pos_array = np.array(positions)  # (N, 2)

        errors: list[float] = []
        correct = 0

        for i in range(n):
            mask = np.ones(n, dtype=bool)
            mask[i] = False
            train_feats = feat_array[mask]
            train_pos = pos_array[mask]
            query = feat_array[i]

            query_norm = np.linalg.norm(query)
            if query_norm == 0:
                continue
            feat_norms = np.linalg.norm(train_feats, axis=1)
            safe_norms = np.where(feat_norms == 0, 1.0, feat_norms)
            similarities = train_feats @ query / (safe_norms * query_norm)
            best_idx = int(np.argmax(similarities))

            predicted = train_pos[best_idx]
            actual = pos_array[i]
            error = float(np.linalg.norm(predicted - actual))
            errors.append(error)

            if error <= self.grid_resolution:
                correct += 1

        if not errors:
            return None, None

        return correct / len(errors), float(np.mean(errors))

    @classmethod
    def from_json(
        cls, json_path: str | Path, db_dir: str | Path, **kwargs
    ) -> BuildResult:
        """Load calibration data from JSON file and build DB directly.

        Expected JSON format::

            {
                "floor": 1,
                "grid_resolution": 1.0,
                "sensor_positions": {"mac": [x, y, z], ...},
                "points": [
                    {"x": 1.0, "y": 2.0, "packets": [{CsiPacket dict}, ...]},
                    ...
                ]
            }
        """
        json_path = Path(json_path)
        with open(json_path) as f:
            data = json.load(f)

        floor = data["floor"]
        grid_resolution = data.get("grid_resolution", 1.0)
        sensor_positions = {
            k: tuple(v) for k, v in data.get("sensor_positions", {}).items()
        }

        builder = cls(
            db_dir=db_dir,
            floor=floor,
            grid_resolution=grid_resolution,
            sensor_positions=sensor_positions,
            **kwargs,
        )

        for pt in data["points"]:
            packets = [CsiPacket.from_dict(p) for p in pt["packets"]]
            builder.add_point(
                CalibrationPoint(x=pt["x"], y=pt["y"], floor=floor, packets=packets)
            )

        return builder.build_and_save()
