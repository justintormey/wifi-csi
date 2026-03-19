"""Tests for the zone recalibration module."""

import numpy as np
import pytest

from backend.calibration.collector import CollectedPoint
from backend.calibration.zone_recal import (
    ZoneBounds,
    ZoneRecalResult,
    find_fingerprints_in_zone,
    generate_zone_grid,
    recalibrate_zone,
    remove_zone_fingerprints,
)
from backend.tracker.fingerprint_db import Fingerprint, FloorDB


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_floor_db_with_grid(
    n_x: int = 5,
    n_y: int = 5,
    spacing: float = 2.0,
    feat_dim: int = 120,
) -> FloorDB:
    """Create a FloorDB with a grid of fingerprints."""
    rng = np.random.default_rng(42)
    db = FloorDB(floor=1, grid_resolution=spacing)
    for ix in range(n_x):
        for iy in range(n_y):
            x = ix * spacing
            y = iy * spacing
            vec = rng.random(feat_dim)
            vec /= np.linalg.norm(vec)  # L2 normalize
            db.add(Fingerprint(x=x, y=y, floor=1, feature_vector=vec))
    return db


def _make_collected_point(
    x: float,
    y: float,
    n_frames: int = 100,
    n_subcarriers: int = 114,
    seed: int = 42,
) -> CollectedPoint:
    """Create a synthetic CollectedPoint with realistic CSI data."""
    rng = np.random.default_rng(seed + int(x * 100 + y * 10))
    base_amp = 50.0 + 10.0 * rng.random(n_subcarriers)
    amp_matrix = np.tile(base_amp, (n_frames, 1)) + rng.normal(0, 3.0, (n_frames, n_subcarriers))
    amp_matrix = np.maximum(amp_matrix, 1.0)
    phase_offset = rng.uniform(-0.5, 0.5, n_subcarriers)
    phase_matrix = np.tile(phase_offset, (n_frames, 1)) + rng.normal(0, 0.1, (n_frames, n_subcarriers))
    return CollectedPoint(
        x=x, y=y,
        amplitude_matrix=amp_matrix,
        phase_matrix=phase_matrix,
        feature_vector=rng.random(120),
        frame_count=n_frames,
        timestamp=0.0,
    )


# ---------------------------------------------------------------------------
# ZoneBounds tests
# ---------------------------------------------------------------------------


class TestZoneBounds:
    def test_valid_zone(self):
        z = ZoneBounds(x_min=2.0, x_max=6.0, y_min=1.0, y_max=4.0)
        assert z.width == 4.0
        assert z.height == 3.0

    def test_invalid_x_raises(self):
        with pytest.raises(ValueError, match="x_min"):
            ZoneBounds(x_min=6.0, x_max=2.0, y_min=0, y_max=5)

    def test_invalid_y_raises(self):
        with pytest.raises(ValueError, match="y_min"):
            ZoneBounds(x_min=0, x_max=5, y_min=5.0, y_max=1.0)

    def test_equal_bounds_raises(self):
        with pytest.raises(ValueError):
            ZoneBounds(x_min=3.0, x_max=3.0, y_min=0, y_max=5)

    def test_contains_inside(self):
        z = ZoneBounds(x_min=2.0, x_max=6.0, y_min=1.0, y_max=4.0)
        assert z.contains(3.0, 2.0)
        assert z.contains(2.0, 1.0)  # on boundary
        assert z.contains(6.0, 4.0)  # on boundary

    def test_contains_outside(self):
        z = ZoneBounds(x_min=2.0, x_max=6.0, y_min=1.0, y_max=4.0)
        assert not z.contains(1.0, 2.0)
        assert not z.contains(7.0, 2.0)
        assert not z.contains(3.0, 0.0)
        assert not z.contains(3.0, 5.0)


# ---------------------------------------------------------------------------
# find_fingerprints_in_zone tests
# ---------------------------------------------------------------------------


class TestFindFingerprintsInZone:
    def test_finds_fingerprints_inside_zone(self):
        db = _make_floor_db_with_grid(n_x=5, n_y=5, spacing=2.0)
        zone = ZoneBounds(x_min=1.5, x_max=4.5, y_min=1.5, y_max=4.5)
        indices = find_fingerprints_in_zone(db, zone)
        # Should find points at (2,2), (2,4), (4,2), (4,4)
        assert len(indices) == 4
        # Indices are sorted descending
        assert indices == sorted(indices, reverse=True)

    def test_no_fingerprints_in_empty_zone(self):
        db = _make_floor_db_with_grid(n_x=3, n_y=3, spacing=2.0)
        zone = ZoneBounds(x_min=10.0, x_max=15.0, y_min=10.0, y_max=15.0)
        indices = find_fingerprints_in_zone(db, zone)
        assert len(indices) == 0

    def test_all_fingerprints_in_large_zone(self):
        db = _make_floor_db_with_grid(n_x=3, n_y=3, spacing=1.0)
        zone = ZoneBounds(x_min=0.0, x_max=10.0, y_min=0.0, y_max=10.0)
        indices = find_fingerprints_in_zone(db, zone)
        assert len(indices) == 9

    def test_empty_db(self):
        db = FloorDB(floor=1)
        zone = ZoneBounds(x_min=0.0, x_max=10.0, y_min=0.0, y_max=10.0)
        indices = find_fingerprints_in_zone(db, zone)
        assert len(indices) == 0


# ---------------------------------------------------------------------------
# remove_zone_fingerprints tests
# ---------------------------------------------------------------------------


class TestRemoveZoneFingerprints:
    def test_removes_correct_count(self):
        db = _make_floor_db_with_grid(n_x=5, n_y=5, spacing=2.0)
        original_size = db.size
        zone = ZoneBounds(x_min=1.5, x_max=4.5, y_min=1.5, y_max=4.5)
        removed = remove_zone_fingerprints(db, zone)
        assert removed == 4
        assert db.size == original_size - 4

    def test_remaining_fingerprints_outside_zone(self):
        db = _make_floor_db_with_grid(n_x=5, n_y=5, spacing=2.0)
        zone = ZoneBounds(x_min=1.5, x_max=4.5, y_min=1.5, y_max=4.5)
        remove_zone_fingerprints(db, zone)
        # All remaining should be outside zone
        for i in range(db.size):
            x, y = db.positions[i]
            assert not zone.contains(float(x), float(y))

    def test_removes_nothing_from_empty_zone(self):
        db = _make_floor_db_with_grid(n_x=3, n_y=3, spacing=2.0)
        zone = ZoneBounds(x_min=20.0, x_max=25.0, y_min=20.0, y_max=25.0)
        removed = remove_zone_fingerprints(db, zone)
        assert removed == 0
        assert db.size == 9


# ---------------------------------------------------------------------------
# generate_zone_grid tests
# ---------------------------------------------------------------------------


class TestGenerateZoneGrid:
    def test_generates_points_within_bounds(self):
        zone = ZoneBounds(x_min=2.0, x_max=6.0, y_min=1.0, y_max=4.0)
        points = generate_zone_grid(zone, resolution_m=1.0)
        assert len(points) > 0
        for x, y in points:
            assert zone.contains(x, y)

    def test_serpentine_pattern(self):
        zone = ZoneBounds(x_min=0.0, x_max=4.0, y_min=0.0, y_max=3.0)
        points = generate_zone_grid(zone, resolution_m=1.0)
        rows: dict[float, list[float]] = {}
        for x, y in points:
            rows.setdefault(y, []).append(x)
        sorted_ys = sorted(rows.keys())
        # First row left-to-right
        assert rows[sorted_ys[0]] == sorted(rows[sorted_ys[0]])
        # Second row right-to-left
        if len(sorted_ys) > 1:
            assert rows[sorted_ys[1]] == sorted(rows[sorted_ys[1]], reverse=True)

    def test_invalid_resolution_raises(self):
        zone = ZoneBounds(x_min=0, x_max=5, y_min=0, y_max=5)
        with pytest.raises(ValueError, match="positive"):
            generate_zone_grid(zone, resolution_m=0)

    def test_point_count_scales_with_resolution(self):
        zone = ZoneBounds(x_min=0, x_max=10, y_min=0, y_max=10)
        fine = generate_zone_grid(zone, resolution_m=0.5)
        coarse = generate_zone_grid(zone, resolution_m=2.0)
        assert len(fine) > len(coarse)


# ---------------------------------------------------------------------------
# recalibrate_zone tests
# ---------------------------------------------------------------------------


class TestRecalibrateZone:
    def test_replaces_zone_fingerprints(self):
        db = _make_floor_db_with_grid(n_x=5, n_y=5, spacing=2.0)
        original_size = db.size  # 25
        zone = ZoneBounds(x_min=1.5, x_max=4.5, y_min=1.5, y_max=4.5)

        # Collect new data for the zone
        new_points = [
            _make_collected_point(2.0, 2.0, seed=100),
            _make_collected_point(4.0, 2.0, seed=101),
            _make_collected_point(2.0, 4.0, seed=102),
            _make_collected_point(4.0, 4.0, seed=103),
        ]

        result = recalibrate_zone(db, zone, new_points)
        assert isinstance(result, ZoneRecalResult)
        assert result.points_removed == 4
        assert result.points_added == 4
        assert result.total_fingerprints == original_size  # same count

    def test_skips_points_outside_zone(self):
        db = _make_floor_db_with_grid(n_x=3, n_y=3, spacing=2.0)
        zone = ZoneBounds(x_min=1.5, x_max=4.5, y_min=1.5, y_max=4.5)

        # One point inside, one outside
        new_points = [
            _make_collected_point(2.0, 2.0, seed=100),  # inside
            _make_collected_point(0.0, 0.0, seed=101),  # outside
        ]

        result = recalibrate_zone(db, zone, new_points)
        assert result.points_added == 1
        assert result.skipped >= 1

    def test_zone_with_no_existing_fingerprints(self):
        db = _make_floor_db_with_grid(n_x=3, n_y=3, spacing=2.0)
        original_size = db.size
        # Zone that doesn't overlap any existing points
        zone = ZoneBounds(x_min=10.0, x_max=12.0, y_min=10.0, y_max=12.0)

        new_points = [
            _make_collected_point(10.0, 10.0, seed=200),
            _make_collected_point(12.0, 12.0, seed=201),
        ]

        result = recalibrate_zone(db, zone, new_points)
        assert result.points_removed == 0
        assert result.points_added == 2
        assert result.total_fingerprints == original_size + 2

    def test_empty_collected_data(self):
        db = _make_floor_db_with_grid(n_x=3, n_y=3, spacing=2.0)
        original_size = db.size
        zone = ZoneBounds(x_min=1.5, x_max=4.5, y_min=1.5, y_max=4.5)

        result = recalibrate_zone(db, zone, [])
        # Should remove old points but add nothing
        # Points in zone: (2,2), (2,4), (4,2), (4,4) = 4
        assert result.points_removed == 4
        assert result.points_added == 0
        assert result.total_fingerprints == original_size - result.points_removed
