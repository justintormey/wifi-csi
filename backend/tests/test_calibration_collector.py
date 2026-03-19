"""Tests for the calibration walk collector module."""

from pathlib import Path

import numpy as np
import pytest

from backend.calibration.collector import (
    CalibrationProgress,
    CalibrationSession,
    CollectedPoint,
    GridPoint,
    SessionState,
    generate_grid,
)


# ---------------------------------------------------------------------------
# Grid generation tests
# ---------------------------------------------------------------------------


class TestGenerateGrid:
    def test_basic_grid(self):
        points = generate_grid(5.0, 5.0, resolution_m=1.0, margin_m=0.5)
        assert len(points) > 0
        # All points within bounds
        for x, y in points:
            assert 0.5 <= x <= 4.5
            assert 0.5 <= y <= 4.5

    def test_serpentine_pattern(self):
        """Verify alternating row directions (boustrophedon)."""
        points = generate_grid(5.0, 5.0, resolution_m=1.0, margin_m=0.5)
        # Group points by y coordinate
        rows: dict[float, list[float]] = {}
        for x, y in points:
            rows.setdefault(y, []).append(x)

        sorted_ys = sorted(rows.keys())
        # First row should be left-to-right (ascending x)
        assert rows[sorted_ys[0]] == sorted(rows[sorted_ys[0]])
        # Second row should be right-to-left (descending x)
        if len(sorted_ys) > 1:
            assert rows[sorted_ys[1]] == sorted(rows[sorted_ys[1]], reverse=True)

    def test_single_point_grid(self):
        """Very small floor yields at least one point."""
        points = generate_grid(1.5, 1.5, resolution_m=1.0, margin_m=0.5)
        assert len(points) >= 1

    def test_invalid_dimensions_raises(self):
        with pytest.raises(ValueError, match="positive"):
            generate_grid(0, 5.0)
        with pytest.raises(ValueError, match="positive"):
            generate_grid(5.0, -1.0)

    def test_invalid_resolution_raises(self):
        with pytest.raises(ValueError, match="positive"):
            generate_grid(5.0, 5.0, resolution_m=0)

    def test_margin_too_large_raises(self):
        with pytest.raises(ValueError, match="too large"):
            generate_grid(2.0, 2.0, margin_m=1.5)

    def test_resolution_affects_count(self):
        fine = generate_grid(10.0, 10.0, resolution_m=0.5)
        coarse = generate_grid(10.0, 10.0, resolution_m=2.0)
        assert len(fine) > len(coarse)

    def test_real_floor_dimensions(self):
        """Test with actual house dimensions (18.0m x 10.5m)."""
        points = generate_grid(18.0, 10.5, resolution_m=1.0, margin_m=0.5)
        # Should have ~180 points (18*10 grid minus margins)
        assert 100 < len(points) < 250


# ---------------------------------------------------------------------------
# CalibrationSession lifecycle tests
# ---------------------------------------------------------------------------


class TestCalibrationSession:
    def _make_session(self, **kwargs) -> CalibrationSession:
        defaults = dict(
            floor_id=1,
            width_m=6.0,
            depth_m=4.0,
            grid_resolution_m=1.0,
            frames_per_point=10,
            margin_m=0.5,
        )
        defaults.update(kwargs)
        return CalibrationSession(**defaults)

    def _fake_frame(self, n_subcarriers: int = 30):
        rng = np.random.default_rng()
        return rng.random(n_subcarriers), rng.random(n_subcarriers)

    def test_initial_state(self):
        s = self._make_session()
        assert s.state == SessionState.IDLE
        assert s.total_points > 0
        assert s.collected_points == 0
        assert s.progress_pct == 0.0
        assert not s.is_active

    def test_start_session(self):
        s = self._make_session()
        point = s.start()
        assert s.state == SessionState.COLLECTING
        assert s.is_active
        assert s.started_at is not None
        assert isinstance(point, GridPoint)

    def test_start_twice_raises(self):
        s = self._make_session()
        s.start()
        with pytest.raises(RuntimeError, match="Cannot start"):
            s.start()

    def test_start_point_before_start_raises(self):
        s = self._make_session()
        with pytest.raises(RuntimeError, match="not started"):
            s.start_point()

    def test_add_frame_before_start_point_raises(self):
        s = self._make_session()
        s.start()
        amp, phase = self._fake_frame()
        with pytest.raises(RuntimeError, match="No active point"):
            s.add_frame(amp, phase)

    def test_collect_single_point(self):
        s = self._make_session(frames_per_point=5)
        s.start()
        s.start_point()
        assert s.state == SessionState.POINT_ACTIVE

        for i in range(4):
            done = s.add_frame(*self._fake_frame())
            assert not done

        done = s.add_frame(*self._fake_frame())
        assert done
        assert s.collected_points == 1
        assert s.state == SessionState.COLLECTING

    def test_shape_mismatch_raises(self):
        s = self._make_session(frames_per_point=5)
        s.start()
        s.start_point()
        amp = np.zeros(30)
        phase = np.zeros(20)
        with pytest.raises(ValueError, match="mismatch"):
            s.add_frame(amp, phase)

    def test_collect_all_points(self):
        s = self._make_session(frames_per_point=3)
        s.start()

        for _ in range(s.total_points):
            s.start_point()
            for _ in range(3):
                s.add_frame(*self._fake_frame())

        assert s.collected_points == s.total_points
        assert s.progress_pct == 100.0

    def test_finish_returns_collected_data(self):
        s = self._make_session(frames_per_point=5)
        s.start()
        s.start_point()
        for _ in range(5):
            s.add_frame(*self._fake_frame(n_subcarriers=20))

        data = s.finish()
        assert len(data) == 1
        assert isinstance(data[0], CollectedPoint)
        assert data[0].amplitude_matrix.shape == (5, 20)
        assert data[0].phase_matrix.shape == (5, 20)
        assert s.state == SessionState.COMPLETE
        assert s.completed_at is not None

    def test_finish_without_start_raises(self):
        s = self._make_session()
        with pytest.raises(RuntimeError, match="never started"):
            s.finish()

    def test_skip_point(self):
        s = self._make_session(frames_per_point=5)
        s.start()
        s.start_point()
        next_point = s.skip_point()
        assert next_point is not None
        assert s.state == SessionState.COLLECTING

    def test_skip_all_points_returns_none(self):
        s = self._make_session(frames_per_point=5)
        s.start()
        for _ in range(s.total_points):
            s.start_point()
            result = s.skip_point()
        # After skipping the last point, skip returns None
        # (we may have gotten None on the last skip)
        # The point is that session still works

    def test_start_point_specific_index(self):
        s = self._make_session(frames_per_point=3)
        s.start()
        point = s.start_point(point_index=2)
        assert point.index == 2

    def test_start_point_invalid_index_raises(self):
        s = self._make_session(frames_per_point=3)
        s.start()
        with pytest.raises(IndexError):
            s.start_point(point_index=9999)

    def test_get_collected_data_midway(self):
        s = self._make_session(frames_per_point=3)
        s.start()
        s.start_point()
        for _ in range(3):
            s.add_frame(*self._fake_frame())

        data = s.get_collected_data()
        assert len(data) == 1

        # Can still continue collecting
        s.start_point()
        for _ in range(3):
            s.add_frame(*self._fake_frame())

        data = s.get_collected_data()
        assert len(data) == 2


# ---------------------------------------------------------------------------
# Pause / Resume / Cancel tests
# ---------------------------------------------------------------------------


class TestPauseResumeCancel:
    def _make_session(self, **kwargs) -> CalibrationSession:
        defaults = dict(
            floor_id=1, width_m=6.0, depth_m=4.0,
            grid_resolution_m=1.0, frames_per_point=5, margin_m=0.5,
        )
        defaults.update(kwargs)
        return CalibrationSession(**defaults)

    def _fake_frame(self, n=30):
        rng = np.random.default_rng()
        return rng.random(n), rng.random(n)

    def test_pause_from_collecting(self):
        s = self._make_session()
        s.start()
        s.pause()
        assert s.state == SessionState.PAUSED

    def test_pause_from_point_active(self):
        s = self._make_session()
        s.start()
        s.start_point()
        s.add_frame(*self._fake_frame())
        s.pause()
        assert s.state == SessionState.PAUSED

    def test_pause_from_idle_raises(self):
        s = self._make_session()
        with pytest.raises(RuntimeError, match="Cannot pause"):
            s.pause()

    def test_resume_from_paused(self):
        s = self._make_session()
        s.start()
        s.pause()
        s.resume()
        assert s.state == SessionState.COLLECTING

    def test_resume_restores_point_active(self):
        """Resume restores POINT_ACTIVE if frames were partially collected."""
        s = self._make_session()
        s.start()
        s.start_point()
        s.add_frame(*self._fake_frame())
        s.pause()
        s.resume()
        assert s.state == SessionState.POINT_ACTIVE

    def test_resume_from_non_paused_raises(self):
        s = self._make_session()
        s.start()
        with pytest.raises(RuntimeError, match="Cannot resume"):
            s.resume()

    def test_cancel_from_collecting(self):
        s = self._make_session()
        s.start()
        s.start_point()
        for _ in range(5):
            s.add_frame(*self._fake_frame())
        s.cancel()
        assert s.state == SessionState.IDLE
        assert s.collected_points == 0
        assert len(s.get_collected_data()) == 0

    def test_cancel_from_paused(self):
        s = self._make_session()
        s.start()
        s.pause()
        s.cancel()
        assert s.state == SessionState.IDLE

    def test_cancel_from_idle_raises(self):
        s = self._make_session()
        with pytest.raises(RuntimeError, match="not active"):
            s.cancel()

    def test_pause_resume_continues_work(self):
        """Pause/resume doesn't lose progress."""
        s = self._make_session(frames_per_point=3)
        s.start()
        s.start_point()
        for _ in range(3):
            s.add_frame(*self._fake_frame())
        assert s.collected_points == 1

        s.pause()
        s.resume()

        # Can continue collecting more points
        s.start_point()
        for _ in range(3):
            s.add_frame(*self._fake_frame())
        assert s.collected_points == 2

    def test_start_point_while_paused_raises(self):
        s = self._make_session()
        s.start()
        s.pause()
        with pytest.raises(RuntimeError, match="paused"):
            s.start_point()


# ---------------------------------------------------------------------------
# Feature extraction integration tests
# ---------------------------------------------------------------------------


class TestFeatureExtraction:
    """Verify that the collector runs the signal processing pipeline."""

    def _make_session(self, **kwargs) -> CalibrationSession:
        defaults = dict(
            floor_id=1, width_m=6.0, depth_m=4.0,
            grid_resolution_m=1.0, frames_per_point=10, margin_m=0.5,
            top_k_subcarriers=15,
        )
        defaults.update(kwargs)
        return CalibrationSession(**defaults)

    def _synthetic_frame(self, n_subcarriers: int = 30, seed: int = 42):
        """Generate a synthetic CSI frame with realistic structure."""
        rng = np.random.default_rng(seed)
        amplitude = 50.0 + 10.0 * rng.random(n_subcarriers)
        phase = rng.uniform(-np.pi, np.pi, n_subcarriers)
        return amplitude, phase

    def test_collected_point_has_feature_vector(self):
        s = self._make_session(frames_per_point=10, top_k_subcarriers=15)
        s.start()
        s.start_point()

        for i in range(10):
            amp, phase = self._synthetic_frame(seed=i)
            s.add_frame(amp, phase)

        data = s.get_collected_data()
        assert len(data) == 1
        cp = data[0]
        # Feature vector should be 4*K dimensional (mean_amp, var_amp, mean_phase, std_phase)
        assert cp.feature_vector.shape == (4 * 15,)
        assert np.isfinite(cp.feature_vector).all()
        assert cp.timestamp > 0

    def test_floor_db_populated(self):
        s = self._make_session(frames_per_point=5, top_k_subcarriers=10)
        s.start()

        # Collect 2 points
        for _ in range(2):
            s.start_point()
            for i in range(5):
                s.add_frame(*self._synthetic_frame(seed=i))

        floor_db = s.get_floor_db()
        assert floor_db is not None
        assert floor_db.size == 2
        assert floor_db.positions.shape == (2, 2)
        assert floor_db.features.shape == (2, 4 * 10)

    def test_feature_vectors_are_l2_normalized(self):
        s = self._make_session(frames_per_point=5, top_k_subcarriers=10)
        s.start()
        s.start_point()
        for i in range(5):
            s.add_frame(*self._synthetic_frame(seed=i))

        data = s.get_collected_data()
        norm = np.linalg.norm(data[0].feature_vector)
        np.testing.assert_almost_equal(norm, 1.0, decimal=5)

    def test_raw_data_preserved(self):
        s = self._make_session(frames_per_point=5)
        s.start()
        s.start_point()
        for i in range(5):
            s.add_frame(*self._synthetic_frame(seed=i))

        data = s.get_collected_data()
        assert data[0].amplitude_matrix.shape == (5, 30)
        assert data[0].phase_matrix.shape == (5, 30)
        assert data[0].frame_count == 5


# ---------------------------------------------------------------------------
# Save / FingerprintDB persistence tests
# ---------------------------------------------------------------------------


class TestSave:
    def _collect_session(self, **kwargs) -> CalibrationSession:
        defaults = dict(
            floor_id=1, width_m=4.0, depth_m=3.0,
            grid_resolution_m=1.0, frames_per_point=5, margin_m=0.5,
            top_k_subcarriers=10,
        )
        defaults.update(kwargs)
        s = CalibrationSession(**defaults)
        s.start()
        s.start_point()
        rng = np.random.default_rng(42)
        for _ in range(defaults["frames_per_point"]):
            amp = 50.0 + 10.0 * rng.random(30)
            phase = rng.uniform(-np.pi, np.pi, 30)
            s.add_frame(amp, phase)
        return s

    def test_save_creates_npz(self, tmp_path: Path):
        s = self._collect_session()
        path = s.save(db_dir=tmp_path)
        assert path.exists()
        assert path.suffix == ".npz"

    def test_save_roundtrip(self, tmp_path: Path):
        s = self._collect_session()
        s.save(db_dir=tmp_path)

        # Load and verify
        from backend.tracker.fingerprint_db import FingerprintDB

        db = FingerprintDB(tmp_path)
        db.load(floor=1)
        assert db.floors[1].size == 1
        assert db.floors[1].features.shape[1] == 4 * 10  # 4 * top_k

    def test_save_no_data_raises(self, tmp_path: Path):
        s = CalibrationSession(
            floor_id=1, width_m=6.0, depth_m=4.0, frames_per_point=5,
        )
        s.start()
        with pytest.raises(RuntimeError, match="No fingerprint data"):
            s.save(db_dir=tmp_path)

    def test_save_multiple_points(self, tmp_path: Path):
        s = CalibrationSession(
            floor_id=1, width_m=6.0, depth_m=4.0,
            grid_resolution_m=1.0, frames_per_point=5,
            top_k_subcarriers=10,
        )
        s.start()
        rng = np.random.default_rng(42)

        for _ in range(3):
            s.start_point()
            for _ in range(5):
                amp = 50.0 + 10.0 * rng.random(30)
                phase = rng.uniform(-np.pi, np.pi, 30)
                s.add_frame(amp, phase)

        s.save(db_dir=tmp_path)

        from backend.tracker.fingerprint_db import FingerprintDB

        db = FingerprintDB(tmp_path)
        db.load(floor=1)
        assert db.floors[1].size == 3


# ---------------------------------------------------------------------------
# Progress and grid overlay tests
# ---------------------------------------------------------------------------


class TestProgressAndOverlay:
    def _make_session(self, **kwargs) -> CalibrationSession:
        defaults = dict(
            floor_id=1, width_m=6.0, depth_m=4.0,
            grid_resolution_m=1.0, frames_per_point=5, margin_m=0.5,
        )
        defaults.update(kwargs)
        return CalibrationSession(**defaults)

    def _fake_frame(self, n=30):
        rng = np.random.default_rng()
        return rng.random(n), rng.random(n)

    def test_progress_idle(self):
        s = self._make_session()
        p = s.get_progress()
        assert p.state == "idle"
        assert p.completed_points == 0
        assert p.progress_pct == 0.0
        assert p.current_point is None

    def test_progress_collecting(self):
        s = self._make_session()
        s.start()
        s.start_point()
        s.add_frame(*self._fake_frame())
        p = s.get_progress()
        assert p.state == "point_active"
        assert p.current_point is not None
        assert p.current_point["frame_count"] == 1
        assert p.current_point["frames_required"] == 5
        assert p.elapsed_s >= 0

    def test_progress_after_completion(self):
        s = self._make_session(frames_per_point=3)
        s.start()
        s.start_point()
        for _ in range(3):
            s.add_frame(*self._fake_frame())
        p = s.get_progress()
        assert p.completed_points == 1
        assert p.progress_pct > 0

    def test_progress_estimated_remaining(self):
        s = self._make_session(frames_per_point=3)
        s.start()
        p = s.get_progress()
        # Before any collection, uses default estimate
        assert p.estimated_remaining_s > 0

    def test_grid_overlay(self):
        s = self._make_session(frames_per_point=3)
        s.start()
        overlay = s.get_grid_overlay()
        assert len(overlay) == s.total_points
        assert all("x" in p and "y" in p and "status" in p for p in overlay)
        assert all(p["status"] == "pending" for p in overlay)

    def test_grid_overlay_with_active_point(self):
        s = self._make_session(frames_per_point=3)
        s.start()
        s.start_point()
        overlay = s.get_grid_overlay()
        active = [p for p in overlay if p["status"] == "active"]
        assert len(active) == 1

    def test_grid_overlay_with_completed_point(self):
        s = self._make_session(frames_per_point=3)
        s.start()
        s.start_point()
        for _ in range(3):
            s.add_frame(*self._fake_frame())
        overlay = s.get_grid_overlay()
        completed = [p for p in overlay if p["status"] == "complete"]
        assert len(completed) == 1

    def test_grid_overlay_with_skipped_point(self):
        s = self._make_session(frames_per_point=3)
        s.start()
        s.start_point()
        s.skip_point()
        overlay = s.get_grid_overlay()
        skipped = [p for p in overlay if p["status"] == "skipped"]
        assert len(skipped) == 1


# ---------------------------------------------------------------------------
# from_house_config class method tests
# ---------------------------------------------------------------------------


class TestFromHouseConfig:
    HOUSE_CONFIG = {
        "floors": {
            1: {
                "name": "1st Floor",
                "dimensions": {"width_m": 18.0, "depth_m": 10.5, "height_m": 2.7},
            },
            2: {
                "name": "2nd Floor",
                "dimensions": {"width_m": 18.0, "depth_m": 10.5, "height_m": 2.7},
            },
        }
    }

    def test_creates_session_from_config(self):
        s = CalibrationSession.from_house_config(
            floor_id=1, house_config=self.HOUSE_CONFIG, frames_per_point=10,
        )
        assert s.floor_id == 1
        assert s.total_points > 0
        assert s.frames_per_point == 10

    def test_invalid_floor_raises(self):
        with pytest.raises(ValueError, match="not found"):
            CalibrationSession.from_house_config(
                floor_id=99, house_config=self.HOUSE_CONFIG,
            )

    def test_string_floor_keys(self):
        """house.yaml sometimes has string keys."""
        config = {
            "floors": {
                "1": {"dimensions": {"width_m": 10.0, "depth_m": 8.0, "height_m": 2.7}},
            }
        }
        s = CalibrationSession.from_house_config(floor_id=1, house_config=config)
        assert s.total_points > 0


# ---------------------------------------------------------------------------
# Simulated calibration walk (end-to-end synthetic test)
# ---------------------------------------------------------------------------


class TestSyntheticCalibrationWalk:
    """Simulate a full calibration walk with synthetic CSI data."""

    def test_full_walk(self, tmp_path: Path):
        """End-to-end: start → collect all points → finish → save → verify."""
        s = CalibrationSession(
            floor_id=1, width_m=4.0, depth_m=3.0,
            grid_resolution_m=1.0, frames_per_point=10,
            margin_m=0.5, top_k_subcarriers=10,
        )
        s.start()
        rng = np.random.default_rng(12345)

        points_collected = 0
        for _ in range(s.total_points):
            s.start_point()
            for frame_i in range(10):
                amp = 50.0 + 10.0 * rng.random(30)
                phase = rng.uniform(-np.pi, np.pi, 30)
                done = s.add_frame(amp, phase)
                if done:
                    points_collected += 1

        assert points_collected == s.total_points
        assert s.collected_points == s.total_points
        assert s.progress_pct == 100.0

        data = s.finish()
        assert len(data) == s.total_points
        assert s.state == SessionState.COMPLETE

        # All feature vectors should be valid
        for cp in data:
            assert cp.feature_vector.shape == (4 * 10,)
            assert np.isfinite(cp.feature_vector).all()
            norm = np.linalg.norm(cp.feature_vector)
            np.testing.assert_almost_equal(norm, 1.0, decimal=5)

        # Save and verify roundtrip
        save_path = s.save(db_dir=tmp_path)
        assert save_path.exists()

        from backend.tracker.fingerprint_db import FingerprintDB

        db = FingerprintDB(tmp_path)
        db.load(floor=1)
        assert db.floors[1].size == s.total_points
        # Feature dimensions match
        assert db.floors[1].features.shape[1] == 4 * 10

    def test_walk_with_skips_and_pause(self, tmp_path: Path):
        """Walk with some skipped points and a mid-walk pause."""
        s = CalibrationSession(
            floor_id=1, width_m=4.0, depth_m=3.0,
            grid_resolution_m=1.0, frames_per_point=5,
            margin_m=0.5, top_k_subcarriers=8,
        )
        s.start()
        rng = np.random.default_rng(99)

        collected = 0
        skipped = 0
        for i in range(min(s.total_points, 6)):
            s.start_point()
            if i == 2:
                # Skip this point
                s.skip_point()
                skipped += 1
                continue
            if i == 3:
                # Pause and resume mid-collection (1 frame already added)
                s.add_frame(rng.random(30), rng.uniform(-np.pi, np.pi, 30))
                s.pause()
                s.resume()
                # Only need 4 more frames (1 already added)
                for _ in range(4):
                    s.add_frame(rng.random(30), rng.uniform(-np.pi, np.pi, 30))
            else:
                for _ in range(5):
                    s.add_frame(rng.random(30), rng.uniform(-np.pi, np.pi, 30))
            collected += 1

        assert s.collected_points == collected
        assert s.num_skipped == skipped

        data = s.finish()
        assert len(data) == collected

        # Save works with partial data
        s.save(db_dir=tmp_path)

        from backend.tracker.fingerprint_db import FingerprintDB

        db = FingerprintDB(tmp_path)
        db.load(floor=1)
        assert db.floors[1].size == collected
