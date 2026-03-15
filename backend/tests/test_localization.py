"""Tests for the KNN localization module."""

import numpy as np
import pytest

from backend.tracker.fingerprint_db import Fingerprint, FloorDB
from backend.tracker.localization import (
    LocalizationResult,
    _compute_confidence,
    _position_spread,
    _softmax,
    localize,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_grid_db(
    floor: int = 1,
    grid_size: int = 5,
    dim: int = 20,
    seed: int = 42,
) -> FloorDB:
    """Build a floor DB with fingerprints on a grid.

    Feature vectors encode position so that nearby points have similar vectors.
    """
    db = FloorDB(floor=floor)
    rng = np.random.default_rng(seed)

    for gx in range(grid_size):
        for gy in range(grid_size):
            x, y = float(gx), float(gy)
            # Base random vector + position signal for spatial correlation
            vec = rng.random(dim) * 0.1
            vec[0] = x / grid_size
            vec[1] = y / grid_size
            # L2-normalize (matches feature extractor default)
            vec = vec / np.linalg.norm(vec)
            db.add(Fingerprint(x=x, y=y, floor=floor, feature_vector=vec))

    return db


def _query_at(db: FloorDB, x: float, y: float, grid_size: int = 5, dim: int = 20) -> np.ndarray:
    """Build a query vector that mimics position (x, y)."""
    rng = np.random.default_rng(int(x * 1000 + y * 100 + 7))
    vec = rng.random(dim) * 0.1
    vec[0] = x / grid_size
    vec[1] = y / grid_size
    vec = vec / np.linalg.norm(vec)
    return vec


# ---------------------------------------------------------------------------
# localize() integration tests
# ---------------------------------------------------------------------------


class TestLocalize:
    def test_exact_match_returns_correct_position(self):
        """Query with a vector identical to a known fingerprint."""
        db = FloorDB(floor=1)
        dim = 50
        # Use near-orthogonal basis vectors so exact match clearly dominates
        positions = [(1.0, 2.0), (3.0, 4.0), (5.0, 6.0), (7.0, 8.0), (9.0, 10.0)]
        vecs = {}
        for i, (x, y) in enumerate(positions):
            v = np.zeros(dim)
            v[i * 10 : (i + 1) * 10] = 1.0  # orthogonal blocks
            v = v / np.linalg.norm(v)
            vecs[(x, y)] = v
            db.add(Fingerprint(x=x, y=y, floor=1, feature_vector=v))

        # Query with exact match to (3, 4) — orthogonal to others
        result = localize(db, vecs[(3.0, 4.0)], k=5, similarity_threshold=0.01)

        assert isinstance(result, LocalizationResult)
        # Exact match should dominate: position close to (3, 4)
        assert abs(result.x - 3.0) < 0.5
        assert abs(result.y - 4.0) < 0.5
        assert result.position_confidence > 0.3
        assert result.uncertainty_radius_m >= 0.0

    def test_nearby_query_returns_reasonable_position(self):
        """Query near a known grid point should estimate close to it."""
        db = _build_grid_db(grid_size=5, dim=20)

        # Query at position (2, 3) — matches a grid point
        query = _query_at(db, 2.0, 3.0)
        result = localize(db, query, k=5)

        # Should be within ~1m of the target
        dist = np.sqrt((result.x - 2.0) ** 2 + (result.y - 3.0) ** 2)
        assert dist < 1.5, f"Position estimate too far: ({result.x}, {result.y}), dist={dist}"

    def test_confidence_higher_for_exact_match(self):
        """Exact match should produce higher confidence than a novel position."""
        db = _build_grid_db(grid_size=5, dim=20)

        # Exact grid point
        exact_vec = db.features[12].copy()  # center of 5x5 grid
        exact_result = localize(db, exact_vec, k=5)

        # Random query (dissimilar to any fingerprint)
        rng = np.random.default_rng(999)
        random_vec = rng.random(20)
        random_vec = random_vec / np.linalg.norm(random_vec)
        random_result = localize(db, random_vec, k=5)

        assert exact_result.position_confidence > random_result.position_confidence

    def test_k_equals_1(self):
        """K=1 should return the nearest neighbor position exactly."""
        db = FloorDB(floor=1)
        v1 = np.array([1.0, 0.0, 0.0])
        v2 = np.array([0.0, 1.0, 0.0])
        v3 = np.array([0.0, 0.0, 1.0])
        db.add(Fingerprint(x=10.0, y=20.0, floor=1, feature_vector=v1))
        db.add(Fingerprint(x=30.0, y=40.0, floor=1, feature_vector=v2))
        db.add(Fingerprint(x=50.0, y=60.0, floor=1, feature_vector=v3))

        # Query close to v1
        query = np.array([0.95, 0.05, 0.0])
        query = query / np.linalg.norm(query)
        result = localize(db, query, k=1)

        assert abs(result.x - 10.0) < 0.01
        assert abs(result.y - 20.0) < 0.01
        assert result.uncertainty_radius_m == 0.0  # single point → no spread

    def test_below_threshold_returns_low_confidence(self):
        """When no neighbors exceed similarity threshold, confidence should be 0."""
        db = FloorDB(floor=1)
        # Orthogonal vectors — low similarity to any query
        db.add(Fingerprint(x=0, y=0, floor=1, feature_vector=np.array([1.0, 0.0, 0.0])))
        db.add(Fingerprint(x=5, y=5, floor=1, feature_vector=np.array([0.0, 1.0, 0.0])))

        # Query orthogonal to both
        query = np.array([0.0, 0.0, 1.0])
        result = localize(db, query, k=2, similarity_threshold=0.5)

        assert result.position_confidence == 0.0

    def test_single_fingerprint_db(self):
        """Localization with only one fingerprint in the DB."""
        db = FloorDB(floor=1)
        vec = np.array([0.5, 0.5, 0.5])
        vec = vec / np.linalg.norm(vec)
        db.add(Fingerprint(x=7.0, y=3.0, floor=1, feature_vector=vec))

        result = localize(db, vec, k=5)
        assert abs(result.x - 7.0) < 0.01
        assert abs(result.y - 3.0) < 0.01
        assert result.uncertainty_radius_m == 0.0

    def test_result_fields_are_floats(self):
        """All result fields should be plain floats, not numpy scalars."""
        db = _build_grid_db()
        query = _query_at(db, 2.0, 2.0)
        result = localize(db, query, k=3)

        assert isinstance(result.x, float)
        assert isinstance(result.y, float)
        assert isinstance(result.position_confidence, float)
        assert isinstance(result.uncertainty_radius_m, float)

    def test_confidence_in_range(self):
        """Confidence must always be in [0, 1]."""
        db = _build_grid_db(grid_size=10, dim=20, seed=77)

        rng = np.random.default_rng(123)
        for _ in range(20):
            query = rng.random(20)
            query = query / np.linalg.norm(query)
            result = localize(db, query, k=5)
            assert 0.0 <= result.position_confidence <= 1.0
            assert result.uncertainty_radius_m >= 0.0


# ---------------------------------------------------------------------------
# _softmax unit tests
# ---------------------------------------------------------------------------


class TestSoftmax:
    def test_sums_to_one(self):
        vals = np.array([1.0, 2.0, 3.0])
        w = _softmax(vals, temperature=1.0)
        assert abs(np.sum(w) - 1.0) < 1e-10

    def test_higher_value_gets_higher_weight(self):
        vals = np.array([0.1, 0.9])
        w = _softmax(vals, temperature=1.0)
        assert w[1] > w[0]

    def test_high_temperature_gives_uniform(self):
        vals = np.array([0.1, 0.9])
        w = _softmax(vals, temperature=100.0)
        # Should be nearly uniform
        assert abs(w[0] - w[1]) < 0.01

    def test_low_temperature_sharpens(self):
        vals = np.array([0.1, 0.9])
        w = _softmax(vals, temperature=0.01)
        # Largest value should dominate
        assert w[1] > 0.99

    def test_single_element(self):
        w = _softmax(np.array([5.0]), temperature=1.0)
        assert abs(w[0] - 1.0) < 1e-10

    def test_numerical_stability_large_values(self):
        vals = np.array([1000.0, 1001.0, 1002.0])
        w = _softmax(vals, temperature=1.0)
        assert abs(np.sum(w) - 1.0) < 1e-10
        assert not np.any(np.isnan(w))


# ---------------------------------------------------------------------------
# _position_spread unit tests
# ---------------------------------------------------------------------------


class TestPositionSpread:
    def test_single_point_is_zero(self):
        assert _position_spread(np.array([[1.0, 2.0]])) == 0.0

    def test_identical_points_is_zero(self):
        pts = np.array([[3.0, 4.0], [3.0, 4.0], [3.0, 4.0]])
        assert _position_spread(pts) < 1e-10

    def test_known_spread(self):
        """Two points 2m apart → RMS from centroid = 1m."""
        pts = np.array([[0.0, 0.0], [2.0, 0.0]])
        spread = _position_spread(pts)
        assert abs(spread - 1.0) < 1e-10

    def test_larger_spread(self):
        """Four corners of a 2×2 square → spread = sqrt(2) ≈ 1.414."""
        pts = np.array([[0.0, 0.0], [2.0, 0.0], [0.0, 2.0], [2.0, 2.0]])
        spread = _position_spread(pts)
        assert abs(spread - np.sqrt(2.0)) < 1e-10


# ---------------------------------------------------------------------------
# _compute_confidence unit tests
# ---------------------------------------------------------------------------


class TestComputeConfidence:
    def test_perfect_inputs_give_high_confidence(self):
        c = _compute_confidence(mean_similarity=1.0, spread_m=0.0, fraction_valid=1.0)
        assert c > 0.95

    def test_zero_similarity_gives_zero(self):
        c = _compute_confidence(mean_similarity=0.0, spread_m=0.0, fraction_valid=1.0)
        assert c == 0.0

    def test_large_spread_reduces_confidence(self):
        tight = _compute_confidence(mean_similarity=0.8, spread_m=0.5, fraction_valid=1.0)
        wide = _compute_confidence(mean_similarity=0.8, spread_m=10.0, fraction_valid=1.0)
        assert tight > wide

    def test_low_fraction_reduces_confidence(self):
        full = _compute_confidence(mean_similarity=0.8, spread_m=1.0, fraction_valid=1.0)
        partial = _compute_confidence(mean_similarity=0.8, spread_m=1.0, fraction_valid=0.2)
        assert full > partial

    def test_output_always_in_range(self):
        """Fuzz test — confidence is always [0, 1]."""
        rng = np.random.default_rng(42)
        for _ in range(100):
            sim = rng.uniform(0, 1)
            spread = rng.uniform(0, 50)
            frac = rng.uniform(0, 1)
            c = _compute_confidence(sim, spread, frac)
            assert 0.0 <= c <= 1.0
