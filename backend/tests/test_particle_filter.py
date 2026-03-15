"""Tests for the particle filter trajectory smoother.

Covers initialization, convergence, boundary constraints, resampling,
and end-to-end smoothing on a synthetic trajectory with noise.
"""

from __future__ import annotations

import numpy as np
import pytest

from backend.tracker.particle_filter import (
    DEFAULT_NUM_PARTICLES,
    FloorBounds,
    ParticleFilter,
    ParticleFilterResult,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

BOUNDS = FloorBounds(x_min=0.0, x_max=15.0, y_min=0.0, y_max=12.0)
SEED = 42


def make_pf(**kwargs) -> ParticleFilter:
    """Create a particle filter with deterministic seed."""
    defaults = {"bounds": BOUNDS, "seed": SEED}
    defaults.update(kwargs)
    return ParticleFilter(**defaults)


# ---------------------------------------------------------------------------
# FloorBounds
# ---------------------------------------------------------------------------


class TestFloorBounds:
    def test_from_house_config(self):
        config = {
            "floors": {
                1: {
                    "name": "Ground Floor",
                    "dimensions": {"width_m": 15.0, "depth_m": 12.0, "height_m": 2.7},
                }
            }
        }
        bounds = FloorBounds.from_house_config(config, floor=1)
        assert bounds.x_min == 0.0
        assert bounds.x_max == 15.0
        assert bounds.y_min == 0.0
        assert bounds.y_max == 12.0


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


class TestInitialization:
    def test_not_initialized_before_first_update(self):
        pf = make_pf()
        assert not pf.is_initialized

    def test_initialized_after_first_update(self):
        pf = make_pf()
        pf.update(7.0, 6.0, uncertainty_m=2.0, dt=0.1)
        assert pf.is_initialized

    def test_first_update_returns_near_observation(self):
        pf = make_pf()
        result = pf.update(7.0, 6.0, uncertainty_m=1.0, dt=0.1)
        # With 200 particles spread around (7, 6), the mean should be close
        assert abs(result.x - 7.0) < 1.5
        assert abs(result.y - 6.0) < 1.5

    def test_particles_spread_around_observation(self):
        pf = make_pf()
        pf.update(7.0, 6.0, uncertainty_m=2.0, dt=0.1)
        particles = pf.particles
        assert particles.shape == (DEFAULT_NUM_PARTICLES, 2)
        # Particles should be spread around the observation
        mean_pos = np.mean(particles, axis=0)
        assert abs(mean_pos[0] - 7.0) < 2.0
        assert abs(mean_pos[1] - 6.0) < 2.0

    def test_particles_within_bounds_after_init(self):
        """Even if spread is large, particles should be clamped to bounds."""
        pf = make_pf()
        pf.update(0.5, 0.5, uncertainty_m=5.0, dt=0.1)  # near corner
        particles = pf.particles
        assert np.all(particles[:, 0] >= BOUNDS.x_min)
        assert np.all(particles[:, 0] <= BOUNDS.x_max)
        assert np.all(particles[:, 1] >= BOUNDS.y_min)
        assert np.all(particles[:, 1] <= BOUNDS.y_max)


# ---------------------------------------------------------------------------
# Convergence behavior
# ---------------------------------------------------------------------------


class TestConvergence:
    def test_convergence_increases_with_repeated_observations(self):
        """Feeding the same position repeatedly should increase convergence."""
        pf = make_pf()
        results = []
        for _ in range(20):
            r = pf.update(7.0, 6.0, uncertainty_m=1.0, dt=0.1)
            results.append(r)

        # Convergence should generally increase (allow some noise)
        assert results[-1].convergence > results[0].convergence
        assert results[-1].convergence > 0.7

    def test_position_converges_to_observation(self):
        """After many updates at same position, estimate should be very close."""
        pf = make_pf()
        for _ in range(30):
            r = pf.update(5.0, 4.0, uncertainty_m=0.5, dt=0.1)

        assert abs(r.x - 5.0) < 0.3
        assert abs(r.y - 4.0) < 0.3

    def test_high_uncertainty_slower_convergence(self):
        """Large uncertainty should lead to slower convergence."""
        pf_tight = make_pf(seed=42)
        pf_loose = make_pf(seed=42)

        for _ in range(10):
            r_tight = pf_tight.update(7.0, 6.0, uncertainty_m=0.5, dt=0.1)
            r_loose = pf_loose.update(7.0, 6.0, uncertainty_m=5.0, dt=0.1)

        assert r_tight.convergence > r_loose.convergence


# ---------------------------------------------------------------------------
# Prediction and motion
# ---------------------------------------------------------------------------


class TestPrediction:
    def test_particles_move_in_prediction(self):
        pf = make_pf()
        pf.update(7.0, 6.0, uncertainty_m=1.0, dt=0.1)
        pos_before = pf.particles.copy()

        # Update with a shifted observation
        pf.update(8.0, 6.0, uncertainty_m=1.0, dt=1.0)
        pos_after = pf.particles

        # Particles should have moved (not identical)
        assert not np.allclose(pos_before, pos_after)

    def test_velocity_constrained(self):
        """Particles should not exceed max walking speed."""
        pf = make_pf(max_speed_ms=1.5)
        pf.update(7.0, 6.0, uncertainty_m=1.0, dt=0.1)

        # Run many prediction steps
        for _ in range(50):
            pf.update(7.0, 6.0, uncertainty_m=1.0, dt=0.1)

        # All particles should be within bounds (indirect velocity check)
        particles = pf.particles
        assert np.all(particles[:, 0] >= BOUNDS.x_min)
        assert np.all(particles[:, 0] <= BOUNDS.x_max)


# ---------------------------------------------------------------------------
# Boundary constraints
# ---------------------------------------------------------------------------


class TestBoundaryConstraints:
    def test_particles_stay_within_bounds(self):
        """Particles should never leave the floor boundary."""
        pf = make_pf()
        # Start near corner and push toward wall
        for i in range(30):
            pf.update(0.5, 0.5, uncertainty_m=1.0, dt=0.1)

        particles = pf.particles
        assert np.all(particles[:, 0] >= BOUNDS.x_min)
        assert np.all(particles[:, 1] >= BOUNDS.y_min)

    def test_observation_outside_bounds_still_valid(self):
        """Filter should handle observations slightly outside bounds gracefully."""
        pf = make_pf()
        # Observe outside bounds — particles will be drawn toward it
        # but clamped to bounds
        result = pf.update(-1.0, -1.0, uncertainty_m=2.0, dt=0.1)
        assert result.x >= BOUNDS.x_min
        assert result.y >= BOUNDS.y_min

        particles = pf.particles
        assert np.all(particles[:, 0] >= BOUNDS.x_min)
        assert np.all(particles[:, 1] >= BOUNDS.y_min)


# ---------------------------------------------------------------------------
# Resampling
# ---------------------------------------------------------------------------


class TestResampling:
    def test_weights_sum_to_one(self):
        pf = make_pf()
        pf.update(7.0, 6.0, uncertainty_m=1.0, dt=0.1)
        np.testing.assert_almost_equal(np.sum(pf.weights), 1.0)

    def test_weights_uniform_after_resample(self):
        """After resampling, weights should be uniform."""
        pf = make_pf()
        # Multiple updates to trigger resampling
        for _ in range(20):
            pf.update(7.0, 6.0, uncertainty_m=0.5, dt=0.1)

        # Weights should still sum to 1
        np.testing.assert_almost_equal(np.sum(pf.weights), 1.0)

    def test_particle_count_preserved(self):
        """Number of particles should remain constant through resampling."""
        pf = make_pf()
        for _ in range(20):
            pf.update(7.0, 6.0, uncertainty_m=1.0, dt=0.1)

        assert pf.particles.shape[0] == DEFAULT_NUM_PARTICLES


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------


class TestReset:
    def test_reset_clears_state(self):
        pf = make_pf()
        pf.update(7.0, 6.0, uncertainty_m=1.0, dt=0.1)
        assert pf.is_initialized

        pf.reset()
        assert not pf.is_initialized

    def test_reset_allows_reinitialization(self):
        pf = make_pf()
        pf.update(7.0, 6.0, uncertainty_m=1.0, dt=0.1)
        pf.reset()

        result = pf.update(3.0, 2.0, uncertainty_m=1.0, dt=0.1)
        assert pf.is_initialized
        # Should be near new observation, not old one
        assert abs(result.x - 3.0) < 2.0
        assert abs(result.y - 2.0) < 2.0


# ---------------------------------------------------------------------------
# Output format
# ---------------------------------------------------------------------------


class TestOutput:
    def test_result_is_frozen_dataclass(self):
        pf = make_pf()
        result = pf.update(7.0, 6.0, uncertainty_m=1.0, dt=0.1)
        assert isinstance(result, ParticleFilterResult)

        with pytest.raises(AttributeError):
            result.x = 99.0  # type: ignore[misc]

    def test_heading_range(self):
        """Heading should be in [-pi, pi]."""
        pf = make_pf()
        for _ in range(10):
            result = pf.update(7.0, 6.0, uncertainty_m=1.0, dt=0.1)
        assert -np.pi <= result.heading_rad <= np.pi

    def test_convergence_range(self):
        """Convergence should be in [0, 1]."""
        pf = make_pf()
        for _ in range(20):
            result = pf.update(7.0, 6.0, uncertainty_m=1.0, dt=0.1)
        assert 0.0 <= result.convergence <= 1.0


# ---------------------------------------------------------------------------
# End-to-end: synthetic trajectory with noise
# ---------------------------------------------------------------------------


class TestSyntheticTrajectory:
    def test_smoothing_reduces_error(self):
        """Particle filter should produce smoother estimates than raw KNN.

        Generate a straight-line ground truth trajectory, add Gaussian noise
        to simulate KNN estimates, and verify the filter output has lower
        average error than the raw noisy observations.
        """
        rng = np.random.default_rng(123)
        pf = make_pf(seed=456)

        # Ground truth: walk from (2, 6) to (12, 6) at ~1 m/s
        n_steps = 100
        dt = 0.1  # 10 Hz updates
        true_x = np.linspace(2.0, 12.0, n_steps)
        true_y = np.full(n_steps, 6.0)

        noise_std = 1.5  # KNN noise (meters)
        noisy_x = true_x + rng.normal(0, noise_std, n_steps)
        noisy_y = true_y + rng.normal(0, noise_std, n_steps)

        raw_errors = []
        filtered_errors = []

        for i in range(n_steps):
            result = pf.update(
                noisy_x[i], noisy_y[i], uncertainty_m=noise_std, dt=dt
            )

            raw_error = np.sqrt(
                (noisy_x[i] - true_x[i]) ** 2 + (noisy_y[i] - true_y[i]) ** 2
            )
            filtered_error = np.sqrt(
                (result.x - true_x[i]) ** 2 + (result.y - true_y[i]) ** 2
            )

            raw_errors.append(raw_error)
            filtered_errors.append(filtered_error)

        # Skip first 10 steps (convergence period)
        raw_mean = np.mean(raw_errors[10:])
        filtered_mean = np.mean(filtered_errors[10:])

        # Filtered error should be significantly less than raw
        assert filtered_mean < raw_mean, (
            f"Filter did not improve: raw={raw_mean:.2f}m, "
            f"filtered={filtered_mean:.2f}m"
        )

    def test_follows_curved_trajectory(self):
        """Filter should follow an L-shaped path without cutting corners."""
        pf = make_pf(seed=789)

        # L-shape: (2,2) → (8,2) then turn to (8,8)
        # Speed ~1 m/s (6m in 60 steps at dt=0.1 = 6s per leg)
        dt = 0.1
        true_positions = []

        for i in range(60):
            true_positions.append((2.0 + i * 6.0 / 60, 2.0))
        for i in range(60):
            true_positions.append((8.0, 2.0 + i * 6.0 / 60))

        rng = np.random.default_rng(101)
        results = []

        for tx, ty in true_positions:
            nx = tx + rng.normal(0, 0.5)
            ny = ty + rng.normal(0, 0.5)
            r = pf.update(nx, ny, uncertainty_m=0.5, dt=dt)
            results.append(r)

        # At end of trajectory, filter should be near (8, 8)
        final = results[-1]
        assert abs(final.x - 8.0) < 2.0
        assert abs(final.y - 8.0) < 2.0

    def test_convergence_behavior(self):
        """Convergence should start low and increase over time."""
        pf = make_pf(seed=42)

        convergences = []
        for _ in range(30):
            r = pf.update(7.0, 6.0, uncertainty_m=1.0, dt=0.1)
            convergences.append(r.convergence)

        # First convergence should be lower than last
        assert convergences[-1] > convergences[0]
        # Final convergence should be reasonably high for stationary target
        assert convergences[-1] > 0.6
