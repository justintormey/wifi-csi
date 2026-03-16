"""Tests for the fingerprint database module."""

from pathlib import Path

import numpy as np
import pytest

from backend.tracker.fingerprint_db import (
    Fingerprint,
    FingerprintDB,
    FloorDB,
    compute_sensor_config_hash,
)


# ---------------------------------------------------------------------------
# FloorDB unit tests
# ---------------------------------------------------------------------------

class TestFloorDB:
    def _make_fp(self, x: float, y: float, floor: int = 1, dim: int = 52) -> Fingerprint:
        rng = np.random.default_rng(seed=int(x * 1000 + y * 100))
        return Fingerprint(x=x, y=y, floor=floor, feature_vector=rng.random(dim))

    def test_add_and_size(self):
        db = FloorDB(floor=1)
        assert db.size == 0
        idx = db.add(self._make_fp(1.0, 2.0))
        assert idx == 0
        assert db.size == 1
        idx2 = db.add(self._make_fp(3.0, 4.0))
        assert idx2 == 1
        assert db.size == 2

    def test_add_wrong_floor_raises(self):
        db = FloorDB(floor=1)
        with pytest.raises(ValueError, match="does not match"):
            db.add(self._make_fp(0, 0, floor=2))

    def test_add_mismatched_feature_length_raises(self):
        db = FloorDB(floor=1)
        db.add(self._make_fp(0, 0, dim=52))
        fp = Fingerprint(x=1, y=1, floor=1, feature_vector=np.ones(10))
        with pytest.raises(ValueError, match="length"):
            db.add(fp)

    def test_add_empty_vector_raises(self):
        db = FloorDB(floor=1)
        fp = Fingerprint(x=0, y=0, floor=1, feature_vector=np.array([]))
        with pytest.raises(ValueError, match="non-empty"):
            db.add(fp)

    def test_update(self):
        db = FloorDB(floor=1)
        db.add(self._make_fp(1.0, 2.0))
        new_fp = self._make_fp(5.0, 6.0)
        db.update(0, new_fp)
        np.testing.assert_array_almost_equal(db.positions[0], [5.0, 6.0])
        np.testing.assert_array_almost_equal(db.features[0], new_fp.feature_vector)

    def test_update_out_of_range_raises(self):
        db = FloorDB(floor=1)
        with pytest.raises(IndexError):
            db.update(0, self._make_fp(0, 0))

    def test_delete(self):
        db = FloorDB(floor=1)
        db.add(self._make_fp(1.0, 2.0))
        db.add(self._make_fp(3.0, 4.0))
        db.add(self._make_fp(5.0, 6.0))
        db.delete(1)  # remove middle
        assert db.size == 2
        np.testing.assert_array_almost_equal(db.positions[0], [1.0, 2.0])
        np.testing.assert_array_almost_equal(db.positions[1], [5.0, 6.0])

    def test_delete_out_of_range_raises(self):
        db = FloorDB(floor=1)
        with pytest.raises(IndexError):
            db.delete(0)

    def test_knn_returns_nearest(self):
        """Build a grid of fingerprints, query near a known point, verify
        the closest point is returned first."""
        db = FloorDB(floor=1)
        rng = np.random.default_rng(42)
        dim = 20

        # Create fingerprints at grid positions where feature = position-seeded
        grid_points = [(float(x), float(y)) for x in range(5) for y in range(5)]
        features = {}
        for x, y in grid_points:
            vec = rng.random(dim)
            # Make feature vector correlate with position: inject position signal
            vec[0] = x / 5.0
            vec[1] = y / 5.0
            features[(x, y)] = vec.copy()
            db.add(Fingerprint(x=x, y=y, floor=1, feature_vector=vec))

        # Query with vector identical to (2, 3) — should return (2, 3) first
        query = features[(2.0, 3.0)].copy()
        result = db.query_knn(query, k=3)

        assert result.positions.shape == (3, 2)
        assert result.distances.shape == (3,)
        # Exact match → distance ~0
        np.testing.assert_array_almost_equal(result.positions[0], [2.0, 3.0])
        assert result.distances[0] < 1e-10

    def test_knn_k_larger_than_db(self):
        db = FloorDB(floor=1)
        db.add(Fingerprint(x=0, y=0, floor=1, feature_vector=np.ones(5)))
        db.add(Fingerprint(x=1, y=1, floor=1, feature_vector=np.ones(5) * 2))
        result = db.query_knn(np.ones(5), k=10)
        assert result.positions.shape[0] == 2  # capped at DB size

    def test_knn_empty_db_raises(self):
        db = FloorDB(floor=1)
        with pytest.raises(ValueError, match="empty"):
            db.query_knn(np.ones(5))

    def test_knn_zero_query_raises(self):
        db = FloorDB(floor=1)
        db.add(Fingerprint(x=0, y=0, floor=1, feature_vector=np.ones(5)))
        with pytest.raises(ValueError, match="non-zero"):
            db.query_knn(np.zeros(5))

    def test_knn_wrong_dim_raises(self):
        db = FloorDB(floor=1)
        db.add(Fingerprint(x=0, y=0, floor=1, feature_vector=np.ones(5)))
        with pytest.raises(ValueError, match="length"):
            db.query_knn(np.ones(10))

    def test_knn_distances_sorted(self):
        db = FloorDB(floor=1)
        rng = np.random.default_rng(99)
        for i in range(20):
            db.add(Fingerprint(
                x=float(i), y=float(i),
                floor=1, feature_vector=rng.random(10),
            ))
        result = db.query_knn(rng.random(10), k=5)
        # Distances should be monotonically non-decreasing
        assert all(
            result.distances[i] <= result.distances[i + 1]
            for i in range(len(result.distances) - 1)
        )

    def test_metadata(self):
        db = FloorDB(floor=2, grid_resolution=0.5, sensor_config_hash="abc123")
        db.add(Fingerprint(x=0, y=0, floor=2, feature_vector=np.ones(5)))
        meta = db.get_metadata()
        assert meta.floor == 2
        assert meta.grid_resolution == 0.5
        assert meta.sensor_config_hash == "abc123"
        assert meta.num_fingerprints == 1


# ---------------------------------------------------------------------------
# FingerprintDB (multi-floor, persistence) tests
# ---------------------------------------------------------------------------

class TestFingerprintDB:
    def test_save_load_roundtrip(self, tmp_path: Path):
        db = FingerprintDB(tmp_path)
        f1 = db.get_floor(1, grid_resolution=0.5, sensor_config_hash="hash1")
        f1.add(Fingerprint(x=1.0, y=2.0, floor=1, feature_vector=np.array([0.1, 0.2, 0.3])))
        f1.add(Fingerprint(x=3.0, y=4.0, floor=1, feature_vector=np.array([0.4, 0.5, 0.6])))

        f2 = db.get_floor(2, grid_resolution=1.0, sensor_config_hash="hash2")
        f2.add(Fingerprint(x=10.0, y=20.0, floor=2, feature_vector=np.array([0.7, 0.8, 0.9])))

        db.save()

        # Load into a fresh DB
        db2 = FingerprintDB(tmp_path)
        db2.load()

        assert db2.floor_numbers == [1, 2]
        assert db2.floors[1].size == 2
        assert db2.floors[2].size == 1

        np.testing.assert_array_almost_equal(
            db2.floors[1].positions, [[1.0, 2.0], [3.0, 4.0]]
        )
        np.testing.assert_array_almost_equal(
            db2.floors[1].features, [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
        )
        assert db2.floors[1].grid_resolution == 0.5
        assert db2.floors[1].sensor_config_hash == "hash1"
        assert db2.floors[2].sensor_config_hash == "hash2"

    def test_save_single_floor(self, tmp_path: Path):
        db = FingerprintDB(tmp_path)
        db.get_floor(1).add(Fingerprint(x=0, y=0, floor=1, feature_vector=np.ones(3)))
        db.get_floor(2).add(Fingerprint(x=0, y=0, floor=2, feature_vector=np.ones(3)))
        db.save(floor=1)

        assert (tmp_path / "floor_1.npz").exists()
        assert not (tmp_path / "floor_2.npz").exists()

    def test_save_nonexistent_floor_raises(self, tmp_path: Path):
        db = FingerprintDB(tmp_path)
        with pytest.raises(KeyError):
            db.save(floor=99)

    def test_load_missing_file_raises(self, tmp_path: Path):
        db = FingerprintDB(tmp_path)
        with pytest.raises(FileNotFoundError):
            db.load(floor=1)

    def test_load_corrupted_npz_raises(self, tmp_path: Path):
        # Save an npz with wrong structure
        np.savez(tmp_path / "floor_1.npz", garbage=np.array([1, 2, 3]))
        db = FingerprintDB(tmp_path)
        with pytest.raises(ValueError, match="missing keys"):
            db.load(floor=1)

    def test_load_shape_mismatch_raises(self, tmp_path: Path):
        # positions wrong shape
        np.savez(
            tmp_path / "floor_1.npz",
            positions=np.array([1.0, 2.0]),  # should be (N, 2)
            features=np.array([[0.1, 0.2]]),
            metadata=np.array([1, 0.0, 1.0]),
            sensor_config_hash=np.array(["x"]),
        )
        db = FingerprintDB(tmp_path)
        with pytest.raises(ValueError, match="positions"):
            db.load(floor=1)

    def test_load_row_count_mismatch_raises(self, tmp_path: Path):
        np.savez(
            tmp_path / "floor_1.npz",
            positions=np.array([[0.0, 0.0], [1.0, 1.0]]),
            features=np.array([[0.1, 0.2]]),  # 1 row vs 2 positions
            metadata=np.array([1, 0.0, 1.0]),
            sensor_config_hash=np.array(["x"]),
        )
        db = FingerprintDB(tmp_path)
        with pytest.raises(ValueError, match="mismatch"):
            db.load(floor=1)

    def test_knn_after_roundtrip(self, tmp_path: Path):
        """Verify KNN works correctly on a loaded DB."""
        db = FingerprintDB(tmp_path)
        f = db.get_floor(1)

        # Create distinct, orthogonal-ish fingerprints
        vecs = np.eye(5)
        for i in range(5):
            f.add(Fingerprint(x=float(i), y=0.0, floor=1, feature_vector=vecs[i]))
        db.save()

        db2 = FingerprintDB(tmp_path)
        db2.load()
        result = db2.floors[1].query_knn(vecs[3], k=1)
        np.testing.assert_array_almost_equal(result.positions[0], [3.0, 0.0])
        assert result.distances[0] < 1e-10

    def test_summary(self, tmp_path: Path):
        db = FingerprintDB(tmp_path)
        f1 = db.get_floor(1)
        f1.add(Fingerprint(x=0, y=0, floor=1, feature_vector=np.ones(3)))
        f2 = db.get_floor(2)
        f2.add(Fingerprint(x=0, y=0, floor=2, feature_vector=np.ones(3)))
        f2.add(Fingerprint(x=1, y=1, floor=2, feature_vector=np.ones(3) * 2))

        summary = db.summary()
        assert summary[1].num_fingerprints == 1
        assert summary[2].num_fingerprints == 2

    def test_load_empty_dir(self, tmp_path: Path):
        db = FingerprintDB(tmp_path)
        db.load()  # should not raise
        assert db.floor_numbers == []

    def test_load_nonexistent_dir(self, tmp_path: Path):
        db = FingerprintDB(tmp_path / "does_not_exist")
        db.load()  # should not raise
        assert db.floor_numbers == []


# ---------------------------------------------------------------------------
# Utility tests
# ---------------------------------------------------------------------------

class TestSensorConfigHash:
    def test_deterministic(self):
        config = {"s1": (0, 0, 2.5), "s2": (3.0, 1.0, 2.5)}
        h1 = compute_sensor_config_hash(config)
        h2 = compute_sensor_config_hash(config)
        assert h1 == h2
        assert len(h1) == 16

    def test_different_configs_differ(self):
        h1 = compute_sensor_config_hash({"s1": (0, 0, 0)})
        h2 = compute_sensor_config_hash({"s1": (1, 0, 0)})
        assert h1 != h2

    def test_order_independent(self):
        h1 = compute_sensor_config_hash({"a": (1,), "b": (2,)})
        h2 = compute_sensor_config_hash({"b": (2,), "a": (1,)})
        assert h1 == h2


# ---------------------------------------------------------------------------
# Coverage gap tests — update validation and npz corruption paths
# ---------------------------------------------------------------------------


class TestFloorDBUpdateValidation:
    """Cover fingerprint_db.py lines 103, 109 — wrong floor and wrong dim in update."""

    def test_update_wrong_floor_raises(self):
        db = FloorDB(floor=1)
        db.add(Fingerprint(x=0, y=0, floor=1, feature_vector=np.ones(5)))
        wrong_floor_fp = Fingerprint(x=1, y=1, floor=2, feature_vector=np.ones(5))
        with pytest.raises(ValueError, match="does not match"):
            db.update(0, wrong_floor_fp)

    def test_update_wrong_feature_dim_raises(self):
        db = FloorDB(floor=1)
        db.add(Fingerprint(x=0, y=0, floor=1, feature_vector=np.ones(5)))
        bad_dim_fp = Fingerprint(x=1, y=1, floor=1, feature_vector=np.ones(10))
        with pytest.raises(ValueError, match="length"):
            db.update(0, bad_dim_fp)


class TestNpzValidationEdgeCases:
    """Cover fingerprint_db.py lines 273, 284, 288 — npz corruption paths."""

    def test_load_non2d_features_raises(self, tmp_path: Path):
        np.savez(
            tmp_path / "floor_1.npz",
            positions=np.array([[0.0, 0.0]]),
            features=np.array([0.1, 0.2, 0.3]),  # 1-D, should be 2-D
            metadata=np.array([1, 0.0, 1.0]),
            sensor_config_hash=np.array(["x"]),
        )
        db = FingerprintDB(tmp_path)
        with pytest.raises(ValueError, match="features must be 2-D"):
            db.load(floor=1)

    def test_load_bad_metadata_shape_raises(self, tmp_path: Path):
        np.savez(
            tmp_path / "floor_1.npz",
            positions=np.array([[0.0, 0.0]]),
            features=np.array([[0.1, 0.2]]),
            metadata=np.array([1, 0.0]),  # only 2 elements, need 3
            sensor_config_hash=np.array(["x"]),
        )
        db = FingerprintDB(tmp_path)
        with pytest.raises(ValueError, match="metadata must have 3 elements"):
            db.load(floor=1)

    def test_load_floor_tag_mismatch_raises(self, tmp_path: Path):
        np.savez(
            tmp_path / "floor_1.npz",
            positions=np.array([[0.0, 0.0]]),
            features=np.array([[0.1, 0.2]]),
            metadata=np.array([99, 0.0, 1.0]),  # floor tag 99 != 1
            sensor_config_hash=np.array(["x"]),
        )
        db = FingerprintDB(tmp_path)
        with pytest.raises(ValueError, match="metadata floor tag"):
            db.load(floor=1)
