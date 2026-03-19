"""Tests for backend.calibration.builder — fingerprint DB builder from calibration data.

Covers HAL-158 requirements:
- Process raw calibration data into fingerprint feature vectors
- Build .npz fingerprint DB (one per floor)
- Quality metrics per grid point (SNR, variance, confidence)
- Flag low-quality points for recalibration
- Cross-validation: leave-one-out accuracy estimate
- Report expected localization accuracy
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from backend.calibration.builder import (
    CalibrationPoint,
    BuildResult,
    FingerprintBuilder,
    PointQuality,
)
from backend.collector.csi_packet import CsiPacket, NUM_SUBCARRIERS
from backend.tracker.fingerprint_db import FingerprintDB


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_packet(
    floor_id: int = 0,
    rssi: int = -40,
    rng: np.random.Generator | None = None,
    amplitude_base: float = 50.0,
    noise_std: float = 2.0,
) -> CsiPacket:
    """Create a synthetic CsiPacket with controllable amplitude characteristics."""
    if rng is None:
        rng = np.random.default_rng(42)

    amplitudes = amplitude_base + rng.normal(0, noise_std, NUM_SUBCARRIERS)
    phases = rng.uniform(-np.pi, np.pi, NUM_SUBCARRIERS)

    iq_pairs = []
    for a, p in zip(amplitudes, phases):
        i_val = int(a * np.cos(p))
        q_val = int(a * np.sin(p))
        iq_pairs.append(i_val)
        iq_pairs.append(q_val)

    return CsiPacket(
        timestamp_us=rng.integers(0, 10**9),
        tx_mac="aa:bb:cc:dd:01:00",
        rx_mac="aa:bb:cc:dd:01:01",
        rssi=rssi,
        floor_id=floor_id,
        iq_pairs=iq_pairs,
    )


def _make_calibration_point(
    x: float,
    y: float,
    floor: int = 1,
    n_packets: int = 100,
    amplitude_base: float = 50.0,
    noise_std: float = 2.0,
    seed: int = 42,
) -> CalibrationPoint:
    """Create a synthetic calibration point with n_packets of CSI data."""
    rng = np.random.default_rng(seed)
    packets = [
        _make_packet(
            floor_id=floor - 1,  # floor_id is 0-based in packets
            rng=rng,
            amplitude_base=amplitude_base,
            noise_std=noise_std,
        )
        for _ in range(n_packets)
    ]
    return CalibrationPoint(x=x, y=y, floor=floor, packets=packets)


def _make_grid_points(
    nx: int = 4,
    ny: int = 3,
    floor: int = 1,
    spacing: float = 1.0,
    n_packets: int = 100,
) -> list[CalibrationPoint]:
    """Create a grid of calibration points with spatially varying signals."""
    points = []
    for ix in range(nx):
        for iy in range(ny):
            x = ix * spacing
            y = iy * spacing
            # Vary amplitude base by position so fingerprints are distinguishable
            amp_base = 40.0 + ix * 5.0 + iy * 3.0
            seed = ix * 1000 + iy
            points.append(
                _make_calibration_point(
                    x=x,
                    y=y,
                    floor=floor,
                    n_packets=n_packets,
                    amplitude_base=amp_base,
                    noise_std=2.0,
                    seed=seed,
                )
            )
    return points


SENSOR_POSITIONS = {
    "aa:bb:cc:dd:01:01": (0.5, 0.5, 2.7),
    "aa:bb:cc:dd:01:02": (17.5, 0.5, 2.7),
    "aa:bb:cc:dd:01:03": (0.5, 10.0, 2.7),
}


# ── Test: Basic construction ────────────────────────────────────────────────


class TestFingerprintBuilderConstruction:
    def test_creates_builder_with_defaults(self, tmp_path: Path):
        builder = FingerprintBuilder(
            db_dir=tmp_path,
            floor=1,
            sensor_positions=SENSOR_POSITIONS,
        )
        assert builder.floor == 1
        assert builder.k_subcarriers == 30
        assert builder.grid_resolution == 1.0

    def test_creates_builder_with_custom_params(self, tmp_path: Path):
        builder = FingerprintBuilder(
            db_dir=tmp_path,
            floor=2,
            grid_resolution=0.5,
            sensor_positions=SENSOR_POSITIONS,
            k_subcarriers=20,
            min_packets_per_point=50,
        )
        assert builder.floor == 2
        assert builder.k_subcarriers == 20
        assert builder.grid_resolution == 0.5


# ── Test: Adding calibration points ────────────────────────────────────────


class TestAddingPoints:
    def test_add_single_point(self, tmp_path: Path):
        builder = FingerprintBuilder(
            db_dir=tmp_path, floor=1, sensor_positions=SENSOR_POSITIONS
        )
        point = _make_calibration_point(x=1.0, y=2.0, floor=1)
        builder.add_point(point)
        assert builder.num_points == 1

    def test_add_multiple_points(self, tmp_path: Path):
        builder = FingerprintBuilder(
            db_dir=tmp_path, floor=1, sensor_positions=SENSOR_POSITIONS
        )
        points = _make_grid_points(nx=3, ny=3)
        builder.add_points(points)
        assert builder.num_points == 9

    def test_rejects_wrong_floor(self, tmp_path: Path):
        builder = FingerprintBuilder(
            db_dir=tmp_path, floor=1, sensor_positions=SENSOR_POSITIONS
        )
        point = _make_calibration_point(x=1.0, y=2.0, floor=2)
        with pytest.raises(ValueError, match="floor"):
            builder.add_point(point)

    def test_rejects_too_few_packets(self, tmp_path: Path):
        builder = FingerprintBuilder(
            db_dir=tmp_path,
            floor=1,
            sensor_positions=SENSOR_POSITIONS,
            min_packets_per_point=50,
        )
        point = _make_calibration_point(x=1.0, y=2.0, floor=1, n_packets=10)
        with pytest.raises(ValueError, match="packets"):
            builder.add_point(point)

    def test_rejects_duplicate_position(self, tmp_path: Path):
        builder = FingerprintBuilder(
            db_dir=tmp_path, floor=1, sensor_positions=SENSOR_POSITIONS
        )
        point1 = _make_calibration_point(x=1.0, y=2.0, floor=1, seed=1)
        point2 = _make_calibration_point(x=1.0, y=2.0, floor=1, seed=2)
        builder.add_point(point1)
        with pytest.raises(ValueError, match="[Dd]uplicate"):
            builder.add_point(point2)


# ── Test: Build and save ────────────────────────────────────────────────────


class TestBuildAndSave:
    def test_builds_npz_file(self, tmp_path: Path):
        builder = FingerprintBuilder(
            db_dir=tmp_path, floor=1, sensor_positions=SENSOR_POSITIONS
        )
        points = _make_grid_points(nx=3, ny=3)
        builder.add_points(points)
        result = builder.build_and_save()

        assert (tmp_path / "floor_1.npz").exists()
        assert isinstance(result, BuildResult)
        assert result.floor == 1
        assert result.num_fingerprints == 9

    def test_build_result_has_correct_feature_dim(self, tmp_path: Path):
        k = 20
        builder = FingerprintBuilder(
            db_dir=tmp_path,
            floor=1,
            sensor_positions=SENSOR_POSITIONS,
            k_subcarriers=k,
        )
        points = _make_grid_points(nx=3, ny=3)
        builder.add_points(points)
        result = builder.build_and_save()

        assert result.feature_dim == 4 * k
        assert result.k_subcarriers == k
        assert len(result.subcarrier_indices) == k

    def test_saved_db_is_loadable(self, tmp_path: Path):
        builder = FingerprintBuilder(
            db_dir=tmp_path, floor=1, sensor_positions=SENSOR_POSITIONS
        )
        points = _make_grid_points(nx=4, ny=3)
        builder.add_points(points)
        result = builder.build_and_save()

        # Load it back and verify
        db = FingerprintDB(tmp_path)
        db.load(floor=1)
        floor_db = db.floors[1]

        assert floor_db.size == 12
        assert floor_db.features.shape == (12, result.feature_dim)
        assert floor_db.positions.shape == (12, 2)

    def test_sensor_config_hash_stored(self, tmp_path: Path):
        builder = FingerprintBuilder(
            db_dir=tmp_path, floor=1, sensor_positions=SENSOR_POSITIONS
        )
        points = _make_grid_points(nx=2, ny=2)
        builder.add_points(points)
        result = builder.build_and_save()

        db = FingerprintDB(tmp_path)
        db.load(floor=1)
        assert db.floors[1].sensor_config_hash == result.sensor_config_hash
        assert len(result.sensor_config_hash) == 16  # sha256[:16]

    def test_raises_on_empty_build(self, tmp_path: Path):
        builder = FingerprintBuilder(
            db_dir=tmp_path, floor=1, sensor_positions=SENSOR_POSITIONS
        )
        with pytest.raises(ValueError, match="[Nn]o calibration"):
            builder.build_and_save()

    def test_grid_resolution_stored(self, tmp_path: Path):
        builder = FingerprintBuilder(
            db_dir=tmp_path,
            floor=1,
            sensor_positions=SENSOR_POSITIONS,
            grid_resolution=0.5,
        )
        points = _make_grid_points(nx=2, ny=2, spacing=0.5)
        builder.add_points(points)
        builder.build_and_save()

        db = FingerprintDB(tmp_path)
        db.load(floor=1)
        assert db.floors[1].grid_resolution == 0.5


# ── Test: Quality metrics per grid point ────────────────────────────────────


class TestQualityMetrics:
    def test_build_result_includes_quality(self, tmp_path: Path):
        builder = FingerprintBuilder(
            db_dir=tmp_path, floor=1, sensor_positions=SENSOR_POSITIONS
        )
        points = _make_grid_points(nx=3, ny=3)
        builder.add_points(points)
        result = builder.build_and_save()

        assert result.point_qualities is not None
        assert len(result.point_qualities) == 9

    def test_quality_has_required_fields(self, tmp_path: Path):
        builder = FingerprintBuilder(
            db_dir=tmp_path, floor=1, sensor_positions=SENSOR_POSITIONS
        )
        points = _make_grid_points(nx=2, ny=2)
        builder.add_points(points)
        result = builder.build_and_save()

        q = result.point_qualities[0]
        assert isinstance(q, PointQuality)
        assert hasattr(q, "x")
        assert hasattr(q, "y")
        assert hasattr(q, "snr")
        assert hasattr(q, "variance")
        assert hasattr(q, "confidence")
        assert hasattr(q, "flagged_for_recalibration")

    def test_high_noise_point_has_lower_confidence(self, tmp_path: Path):
        builder = FingerprintBuilder(
            db_dir=tmp_path, floor=1, sensor_positions=SENSOR_POSITIONS
        )
        good_point = _make_calibration_point(
            x=0.0, y=0.0, floor=1, amplitude_base=50.0, noise_std=2.0, seed=1
        )
        noisy_point = _make_calibration_point(
            x=1.0, y=0.0, floor=1, amplitude_base=50.0, noise_std=20.0, seed=2
        )
        builder.add_points([good_point, noisy_point])
        result = builder.build_and_save()

        good_q = result.point_qualities[0]
        noisy_q = result.point_qualities[1]
        assert good_q.confidence > noisy_q.confidence


# ── Test: Flagging low-quality points ───────────────────────────────────────


class TestLowQualityFlagging:
    def test_flags_very_noisy_points(self, tmp_path: Path):
        builder = FingerprintBuilder(
            db_dir=tmp_path, floor=1, sensor_positions=SENSOR_POSITIONS
        )
        # 3 good points and 1 very noisy point
        good_points = [
            _make_calibration_point(
                x=float(i), y=0.0, floor=1,
                amplitude_base=50.0, noise_std=2.0, seed=i,
            )
            for i in range(3)
        ]
        bad_point = _make_calibration_point(
            x=3.0, y=0.0, floor=1,
            amplitude_base=50.0, noise_std=40.0, seed=99,
        )
        builder.add_points(good_points + [bad_point])
        result = builder.build_and_save()

        flagged = [q for q in result.point_qualities if q.flagged_for_recalibration]
        assert len(flagged) >= 1
        # The bad point at x=3.0 should be flagged
        flagged_positions = [(q.x, q.y) for q in flagged]
        assert (3.0, 0.0) in flagged_positions

    def test_build_result_reports_flagged_count(self, tmp_path: Path):
        builder = FingerprintBuilder(
            db_dir=tmp_path, floor=1, sensor_positions=SENSOR_POSITIONS
        )
        points = _make_grid_points(nx=3, ny=3)
        builder.add_points(points)
        result = builder.build_and_save()

        assert result.num_flagged >= 0
        assert result.num_flagged == sum(
            1 for q in result.point_qualities if q.flagged_for_recalibration
        )


# ── Test: Cross-validation ──────────────────────────────────────────────────


class TestCrossValidation:
    def test_loo_accuracy_returned(self, tmp_path: Path):
        builder = FingerprintBuilder(
            db_dir=tmp_path, floor=1, sensor_positions=SENSOR_POSITIONS
        )
        points = _make_grid_points(nx=4, ny=3)
        builder.add_points(points)
        result = builder.build_and_save()

        assert result.loo_accuracy is not None
        assert 0.0 <= result.loo_accuracy <= 1.0

    def test_loo_mean_error_returned(self, tmp_path: Path):
        builder = FingerprintBuilder(
            db_dir=tmp_path, floor=1, sensor_positions=SENSOR_POSITIONS
        )
        points = _make_grid_points(nx=4, ny=3)
        builder.add_points(points)
        result = builder.build_and_save()

        assert result.loo_mean_error_m is not None
        assert result.loo_mean_error_m >= 0.0

    def test_well_separated_grid_has_good_accuracy(self, tmp_path: Path):
        """A grid with well-separated signals should have reasonable LOO accuracy."""
        builder = FingerprintBuilder(
            db_dir=tmp_path, floor=1, sensor_positions=SENSOR_POSITIONS
        )
        # Use widely spaced points with very different amplitudes
        points = _make_grid_points(nx=4, ny=3, spacing=2.0, n_packets=200)
        builder.add_points(points)
        result = builder.build_and_save()

        # With well-separated synthetic signals, LOO error should be reasonable
        # (not necessarily perfect — synthetic data doesn't perfectly model real CSI)
        assert result.loo_mean_error_m < 5.0  # generous upper bound


# ── Test: KNN accuracy with built DB ────────────────────────────────────────


class TestKNNAccuracy:
    def test_query_returns_nearest_position(self, tmp_path: Path):
        """Build DB from synthetic data, query a known position, verify KNN finds it."""
        builder = FingerprintBuilder(
            db_dir=tmp_path, floor=1, sensor_positions=SENSOR_POSITIONS
        )
        points = _make_grid_points(nx=4, ny=3, spacing=2.0, n_packets=200)
        builder.add_points(points)
        result = builder.build_and_save()

        # Load the built DB and query it
        db = FingerprintDB(tmp_path)
        db.load(floor=1)
        floor_db = db.floors[1]

        # Use the first point's feature vector as a query — should match itself
        query_vec = floor_db.features[0]
        knn = floor_db.query_knn(query_vec, k=1)
        assert knn.indices[0] == 0
        assert knn.distances[0] < 0.01  # near-zero cosine distance

    def test_positions_match_grid(self, tmp_path: Path):
        """Verify that saved positions match the original grid coordinates."""
        builder = FingerprintBuilder(
            db_dir=tmp_path, floor=1, sensor_positions=SENSOR_POSITIONS
        )
        points = _make_grid_points(nx=3, ny=2, spacing=1.0)
        builder.add_points(points)
        result = builder.build_and_save()

        db = FingerprintDB(tmp_path)
        db.load(floor=1)
        floor_db = db.floors[1]

        expected_positions = {(float(ix), float(iy)) for ix in range(3) for iy in range(2)}
        actual_positions = {(round(p[0], 1), round(p[1], 1)) for p in floor_db.positions}
        assert expected_positions == actual_positions


# ── Test: Global subcarrier selection ───────────────────────────────────────


class TestGlobalSubcarrierSelection:
    def test_subcarrier_indices_in_result(self, tmp_path: Path):
        builder = FingerprintBuilder(
            db_dir=tmp_path,
            floor=1,
            sensor_positions=SENSOR_POSITIONS,
            k_subcarriers=25,
        )
        points = _make_grid_points(nx=3, ny=3)
        builder.add_points(points)
        result = builder.build_and_save()

        assert result.subcarrier_indices is not None
        assert len(result.subcarrier_indices) == 25
        # Indices should be valid subcarrier indices [0, 113]
        assert all(0 <= idx < NUM_SUBCARRIERS for idx in result.subcarrier_indices)

    def test_all_fingerprints_have_same_dimension(self, tmp_path: Path):
        builder = FingerprintBuilder(
            db_dir=tmp_path,
            floor=1,
            sensor_positions=SENSOR_POSITIONS,
            k_subcarriers=30,
        )
        points = _make_grid_points(nx=4, ny=3)
        builder.add_points(points)
        result = builder.build_and_save()

        db = FingerprintDB(tmp_path)
        db.load(floor=1)
        floor_db = db.floors[1]

        # All feature vectors must be (4 * 30) = 120
        assert floor_db.features.shape == (12, 120)


# ── Test: JSON loading ──────────────────────────────────────────────────────


class TestFromJSON:
    def test_load_from_json_file(self, tmp_path: Path):
        # Create a calibration JSON file
        rng = np.random.default_rng(42)
        points_data = []
        for ix in range(3):
            for iy in range(2):
                packets = []
                for _ in range(50):
                    amp = 40.0 + ix * 5.0 + iy * 3.0
                    amplitudes = amp + rng.normal(0, 2.0, NUM_SUBCARRIERS)
                    phases = rng.uniform(-np.pi, np.pi, NUM_SUBCARRIERS)
                    iq_pairs = []
                    for a, p in zip(amplitudes, phases):
                        iq_pairs.append(int(a * np.cos(p)))
                        iq_pairs.append(int(a * np.sin(p)))
                    packets.append({
                        "timestamp_us": int(rng.integers(0, 10**9)),
                        "tx_mac": "aa:bb:cc:dd:01:00",
                        "rx_mac": "aa:bb:cc:dd:01:01",
                        "rssi": -40,
                        "floor_id": 0,
                        "iq_pairs": iq_pairs,
                    })
                points_data.append({
                    "x": float(ix),
                    "y": float(iy),
                    "packets": packets,
                })

        cal_data = {
            "floor": 1,
            "grid_resolution": 1.0,
            "sensor_positions": {
                "aa:bb:cc:dd:01:01": [0.5, 0.5, 2.7],
                "aa:bb:cc:dd:01:02": [17.5, 0.5, 2.7],
            },
            "points": points_data,
        }

        json_path = tmp_path / "calibration_floor1.json"
        json_path.write_text(json.dumps(cal_data))

        db_dir = tmp_path / "db"
        result = FingerprintBuilder.from_json(json_path, db_dir)

        assert isinstance(result, BuildResult)
        assert result.floor == 1
        assert result.num_fingerprints == 6
        assert (db_dir / "floor_1.npz").exists()
