"""Zone-specific recalibration for WiFi CSI fingerprint database.

Supports quick partial updates of the fingerprint DB when the environment
changes in a localized area (furniture moved, new obstruction, etc.).
Instead of recalibrating an entire floor, the user specifies a bounding box
and only the fingerprints within that zone are replaced.

Workflow:
    1. Define zone bounds (x_min, x_max, y_min, y_max)
    2. Identify and remove existing fingerprints in the zone
    3. Generate a grid for the zone
    4. Collect new CSI data at each grid point (via CalibrationSession)
    5. Build new fingerprints and insert into the existing FloorDB
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np

from backend.calibration.builder import build_fingerprint, DEFAULT_TOP_K, DEFAULT_NORM
from backend.calibration.collector import CollectedPoint
from backend.processor.feature_extractor import NormMethod
from backend.tracker.fingerprint_db import FloorDB

logger = logging.getLogger(__name__)


@dataclass
class ZoneBounds:
    """Axis-aligned bounding box for a recalibration zone."""

    x_min: float
    x_max: float
    y_min: float
    y_max: float

    def __post_init__(self) -> None:
        if self.x_min >= self.x_max:
            raise ValueError(
                f"x_min ({self.x_min}) must be less than x_max ({self.x_max})"
            )
        if self.y_min >= self.y_max:
            raise ValueError(
                f"y_min ({self.y_min}) must be less than y_max ({self.y_max})"
            )

    @property
    def width(self) -> float:
        return self.x_max - self.x_min

    @property
    def height(self) -> float:
        return self.y_max - self.y_min

    def contains(self, x: float, y: float) -> bool:
        """Check if a point is inside the zone (inclusive bounds)."""
        return self.x_min <= x <= self.x_max and self.y_min <= y <= self.y_max


@dataclass
class ZoneRecalResult:
    """Result of a zone recalibration operation."""

    points_removed: int
    points_added: int
    total_fingerprints: int
    skipped: int


def find_fingerprints_in_zone(
    floor_db: FloorDB,
    zone: ZoneBounds,
) -> list[int]:
    """Find indices of fingerprints within a zone's bounding box.

    Args:
        floor_db: The floor's fingerprint database.
        zone: Bounding box of the zone.

    Returns:
        List of fingerprint indices within the zone, sorted descending
        (for safe sequential deletion).
    """
    indices: list[int] = []
    for i in range(floor_db.size):
        x, y = floor_db.positions[i]
        if zone.contains(float(x), float(y)):
            indices.append(i)

    # Sort descending so deletion doesn't shift indices
    return sorted(indices, reverse=True)


def remove_zone_fingerprints(
    floor_db: FloorDB,
    zone: ZoneBounds,
) -> int:
    """Remove all fingerprints within a zone from the FloorDB.

    Args:
        floor_db: The floor's fingerprint database (modified in-place).
        zone: Bounding box of fingerprints to remove.

    Returns:
        Number of fingerprints removed.
    """
    indices = find_fingerprints_in_zone(floor_db, zone)
    for idx in indices:  # descending order — safe to delete
        floor_db.delete(idx)

    if indices:
        logger.info(
            "Removed %d fingerprints from zone (%.1f,%.1f)-(%.1f,%.1f)",
            len(indices),
            zone.x_min, zone.y_min, zone.x_max, zone.y_max,
        )
    return len(indices)


def generate_zone_grid(
    zone: ZoneBounds,
    resolution_m: float = 1.0,
) -> list[tuple[float, float]]:
    """Generate grid points within a zone for recalibration.

    Args:
        zone: Bounding box for the recalibration area.
        resolution_m: Distance between grid points.

    Returns:
        List of (x, y) coordinates within the zone.
    """
    if resolution_m <= 0:
        raise ValueError(f"Grid resolution must be positive: {resolution_m}")

    xs = np.arange(zone.x_min, zone.x_max + resolution_m * 0.01, resolution_m)
    ys = np.arange(zone.y_min, zone.y_max + resolution_m * 0.01, resolution_m)

    points: list[tuple[float, float]] = []
    for i, y in enumerate(ys):
        row_xs = xs if i % 2 == 0 else xs[::-1]  # serpentine
        for x in row_xs:
            points.append((round(float(x), 2), round(float(y), 2)))

    return points


def recalibrate_zone(
    floor_db: FloorDB,
    zone: ZoneBounds,
    collected_points: list[CollectedPoint],
    top_k: int = DEFAULT_TOP_K,
    norm: NormMethod = DEFAULT_NORM,
) -> ZoneRecalResult:
    """Replace fingerprints in a zone with newly collected data.

    This is the main zone recalibration function. It:
    1. Removes existing fingerprints within the zone
    2. Builds new fingerprints from the collected data
    3. Inserts them into the FloorDB

    Args:
        floor_db: The floor's fingerprint database (modified in-place).
        zone: Bounding box of the zone being recalibrated.
        collected_points: New CSI data for the zone's grid points.
        top_k: Number of subcarriers for feature extraction.
        norm: Feature normalization method.

    Returns:
        ZoneRecalResult with counts of removed/added fingerprints.
    """
    # Step 1: Remove old fingerprints in the zone
    removed = remove_zone_fingerprints(floor_db, zone)

    # Step 2: Build and insert new fingerprints
    added = 0
    skipped = 0
    for point in collected_points:
        # Verify point is within zone bounds
        if not zone.contains(point.x, point.y):
            logger.warning(
                "Point (%.1f, %.1f) outside zone bounds, skipping",
                point.x, point.y,
            )
            skipped += 1
            continue

        fp = build_fingerprint(point, floor_db.floor, top_k=top_k, norm=norm)
        if fp is not None:
            floor_db.add(fp)
            added += 1
        else:
            skipped += 1

    logger.info(
        "Zone recalibration complete: removed %d, added %d, skipped %d, "
        "total fingerprints now %d",
        removed, added, skipped, floor_db.size,
    )

    return ZoneRecalResult(
        points_removed=removed,
        points_added=added,
        total_fingerprints=floor_db.size,
        skipped=skipped,
    )
