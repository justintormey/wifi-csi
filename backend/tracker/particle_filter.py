"""Particle filter for trajectory smoothing.

Uses a set of weighted particles to maintain a probability distribution over
position. Each particle represents a hypothesis about where the person is.
The filter combines noisy KNN position estimates with a physics-based motion
model (velocity-constrained random walk) and floor-plan boundary constraints
to produce smooth, physically plausible trajectories.

Predict → Update → Resample cycle runs once per localization estimate (~10 Hz).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
from numpy.typing import NDArray


@dataclass(frozen=True)
class ParticleFilterResult:
    """Output of a particle filter update step."""

    x: float
    y: float
    convergence: float  # [0, 1] — tighter particle spread → higher convergence
    heading_rad: float  # estimated heading from weighted velocity (radians)


@dataclass(frozen=True)
class FloorBounds:
    """Rectangular boundary constraints for a single floor.

    Particles that leave these bounds are clamped back to the edge.
    Loaded from house.yaml floor dimensions.
    """

    x_min: float
    x_max: float
    y_min: float
    y_max: float

    @classmethod
    def from_house_config(cls, house_config: dict, floor: int) -> FloorBounds:
        """Create bounds from a house.yaml config dict for a given floor."""
        floor_cfg = house_config["floors"][floor]
        dims = floor_cfg["dimensions"]
        return cls(
            x_min=0.0,
            x_max=dims["width_m"],
            y_min=0.0,
            y_max=dims["depth_m"],
        )


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_NUM_PARTICLES: int = 200
DEFAULT_MAX_SPEED_MS: float = 1.5  # max human walking speed (m/s)
DEFAULT_PROCESS_NOISE_MS: float = 0.3  # velocity std for random walk (m/s)
DEFAULT_RESAMPLE_THRESHOLD: float = 0.5  # fraction of N_eff / N to trigger
DEFAULT_CONVERGENCE_HALF_LIFE_M: float = 2.0  # spread where convergence = 0.5


class ParticleFilter:
    """Sequential Monte Carlo tracker for indoor position smoothing.

    Particles carry position (x, y) and velocity (vx, vy). The prediction
    step applies a random-walk velocity model clamped to max walking speed.
    The update step weights particles by Gaussian likelihood centered on
    the KNN position estimate, with std proportional to the KNN uncertainty.
    Systematic resampling fires when effective particle count drops below
    a threshold fraction of total particles.

    Args:
        num_particles: Number of particles (default 200).
        bounds: Floor boundary constraints. Particles are clamped to these.
        max_speed_ms: Maximum walking speed in m/s (default 1.5).
        process_noise_ms: Velocity noise std in m/s (default 0.3).
        resample_threshold: Fraction of N_eff/N below which resampling
            triggers (default 0.5).
        seed: Optional RNG seed for reproducibility.
    """

    def __init__(
        self,
        bounds: FloorBounds,
        num_particles: int = DEFAULT_NUM_PARTICLES,
        max_speed_ms: float = DEFAULT_MAX_SPEED_MS,
        process_noise_ms: float = DEFAULT_PROCESS_NOISE_MS,
        resample_threshold: float = DEFAULT_RESAMPLE_THRESHOLD,
        seed: Optional[int] = None,
    ) -> None:
        self._bounds = bounds
        self._n = num_particles
        self._max_speed = max_speed_ms
        self._process_noise = process_noise_ms
        self._resample_threshold = resample_threshold
        self._rng = np.random.default_rng(seed)

        # State: (N, 2) positions, (N, 2) velocities, (N,) weights
        self._positions: NDArray[np.float64] = np.empty((num_particles, 2))
        self._velocities: NDArray[np.float64] = np.zeros((num_particles, 2))
        self._weights: NDArray[np.float64] = np.full(num_particles, 1.0 / num_particles)

        self._initialized = False
        self._step_count = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def is_initialized(self) -> bool:
        """Whether the filter has received its first observation."""
        return self._initialized

    @property
    def particles(self) -> NDArray[np.float64]:
        """Current particle positions (N, 2). Read-only copy."""
        return self._positions.copy()

    @property
    def weights(self) -> NDArray[np.float64]:
        """Current particle weights (N,). Read-only copy."""
        return self._weights.copy()

    def update(
        self,
        observed_x: float,
        observed_y: float,
        uncertainty_m: float,
        dt: float,
    ) -> ParticleFilterResult:
        """Run one predict → update → resample cycle.

        Args:
            observed_x: KNN-estimated x position (meters).
            observed_y: KNN-estimated y position (meters).
            uncertainty_m: KNN uncertainty radius (meters). Used as the
                std of the Gaussian likelihood for weighting particles.
            dt: Time elapsed since last update (seconds).

        Returns:
            ParticleFilterResult with smoothed position and convergence.
        """
        if not self._initialized:
            self._initialize(observed_x, observed_y, uncertainty_m)
            return self._compute_result()

        # 1. Predict — propagate particles forward
        self._predict(dt)

        # 2. Update — re-weight by observation likelihood
        self._update_weights(observed_x, observed_y, uncertainty_m)

        # 3. Resample — if effective particle count is too low
        n_eff = self._effective_particle_count()
        if n_eff < self._resample_threshold * self._n:
            self._systematic_resample()

        self._step_count += 1
        return self._compute_result()

    def reset(self) -> None:
        """Reset the filter to uninitialized state."""
        self._weights[:] = 1.0 / self._n
        self._velocities[:] = 0.0
        self._initialized = False
        self._step_count = 0

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    def _initialize(self, x: float, y: float, uncertainty_m: float) -> None:
        """Spread particles uniformly around the first observation.

        Uses a Gaussian spread proportional to the observation uncertainty,
        clamped to floor bounds. This lets the filter converge quickly
        on the first few observations.
        """
        spread = max(uncertainty_m, 1.0)  # at least 1m spread
        self._positions[:, 0] = self._rng.normal(x, spread, size=self._n)
        self._positions[:, 1] = self._rng.normal(y, spread, size=self._n)
        self._velocities[:] = 0.0
        self._weights[:] = 1.0 / self._n

        self._clamp_to_bounds()
        self._initialized = True
        self._step_count = 1

    # ------------------------------------------------------------------
    # Predict step
    # ------------------------------------------------------------------

    def _predict(self, dt: float) -> None:
        """Propagate particles with velocity-constrained random walk.

        Each particle's velocity receives Gaussian noise, then gets clamped
        to max walking speed. Position updates by v * dt.
        """
        # Add process noise to velocities
        noise = self._rng.normal(0.0, self._process_noise, size=(self._n, 2))
        self._velocities += noise

        # Clamp speed to max walking speed
        speeds = np.linalg.norm(self._velocities, axis=1)
        too_fast = speeds > self._max_speed
        if np.any(too_fast):
            scale = self._max_speed / speeds[too_fast]
            self._velocities[too_fast] *= scale[:, np.newaxis]

        # Update positions
        self._positions += self._velocities * dt

        # Enforce floor boundaries
        self._clamp_to_bounds()

    # ------------------------------------------------------------------
    # Update step
    # ------------------------------------------------------------------

    def _update_weights(
        self,
        observed_x: float,
        observed_y: float,
        uncertainty_m: float,
    ) -> None:
        """Re-weight particles by Gaussian likelihood of the observation.

        Particles closer to the KNN estimate get higher weight. The std
        of the Gaussian is the KNN uncertainty radius (floored at 0.5m
        to avoid particle collapse on overconfident estimates).
        """
        sigma = max(uncertainty_m, 0.5)

        dx = self._positions[:, 0] - observed_x
        dy = self._positions[:, 1] - observed_y
        dist_sq = dx * dx + dy * dy

        # Gaussian likelihood (unnormalized — normalization constant cancels)
        log_likelihood = -0.5 * dist_sq / (sigma * sigma)
        # Shift for numerical stability before exp
        log_likelihood -= np.max(log_likelihood)
        likelihood = np.exp(log_likelihood)

        self._weights *= likelihood

        # Normalize
        w_sum = np.sum(self._weights)
        if w_sum > 0:
            self._weights /= w_sum
        else:
            # Degenerate case — reset to uniform
            self._weights[:] = 1.0 / self._n

    # ------------------------------------------------------------------
    # Resampling
    # ------------------------------------------------------------------

    def _effective_particle_count(self) -> float:
        """Effective sample size: 1 / sum(w_i^2).

        Ranges from 1 (all weight on one particle) to N (uniform weights).
        """
        return 1.0 / float(np.sum(self._weights**2))

    def _systematic_resample(self) -> None:
        """Systematic resampling — low-variance, O(N).

        Draws N samples using a single random offset and evenly spaced
        points through the cumulative weight distribution. Particles
        with high weight get duplicated; low-weight particles die off.
        Velocities are preserved through resampling.
        """
        cumsum = np.cumsum(self._weights)
        cumsum[-1] = 1.0  # ensure exact sum for numerical safety

        u0 = self._rng.uniform(0.0, 1.0 / self._n)
        u = u0 + np.arange(self._n) / self._n

        indices = np.searchsorted(cumsum, u)
        indices = np.clip(indices, 0, self._n - 1)

        self._positions = self._positions[indices].copy()
        self._velocities = self._velocities[indices].copy()
        self._weights[:] = 1.0 / self._n

    # ------------------------------------------------------------------
    # Boundary constraints
    # ------------------------------------------------------------------

    def _clamp_to_bounds(self) -> None:
        """Clamp particle positions to floor boundaries.

        Particles that hit a wall get their velocity zeroed in that axis
        (they "stop" at the wall rather than bouncing).
        """
        b = self._bounds

        # X axis
        hit_x_min = self._positions[:, 0] < b.x_min
        hit_x_max = self._positions[:, 0] > b.x_max
        self._positions[:, 0] = np.clip(self._positions[:, 0], b.x_min, b.x_max)
        self._velocities[hit_x_min | hit_x_max, 0] = 0.0

        # Y axis
        hit_y_min = self._positions[:, 1] < b.y_min
        hit_y_max = self._positions[:, 1] > b.y_max
        self._positions[:, 1] = np.clip(self._positions[:, 1], b.y_min, b.y_max)
        self._velocities[hit_y_min | hit_y_max, 1] = 0.0

    # ------------------------------------------------------------------
    # Output computation
    # ------------------------------------------------------------------

    def _compute_result(self) -> ParticleFilterResult:
        """Compute weighted mean position, convergence, and heading."""
        # Weighted mean position
        x = float(np.sum(self._weights * self._positions[:, 0]))
        y = float(np.sum(self._weights * self._positions[:, 1]))

        # Convergence: exponential decay of weighted spread
        spread = self._weighted_spread()
        half_life = DEFAULT_CONVERGENCE_HALF_LIFE_M
        convergence = float(np.exp(-np.log(2.0) / half_life * spread))
        convergence = float(np.clip(convergence, 0.0, 1.0))

        # Heading from weighted mean velocity
        vx = float(np.sum(self._weights * self._velocities[:, 0]))
        vy = float(np.sum(self._weights * self._velocities[:, 1]))
        heading = float(np.arctan2(vy, vx))

        return ParticleFilterResult(
            x=x,
            y=y,
            convergence=convergence,
            heading_rad=heading,
        )

    def _weighted_spread(self) -> float:
        """Weighted RMS distance of particles from weighted mean.

        Measures how tightly the particle cloud has converged.
        Tight spread → filter is confident; wide spread → uncertain.
        """
        mean_x = np.sum(self._weights * self._positions[:, 0])
        mean_y = np.sum(self._weights * self._positions[:, 1])

        dx = self._positions[:, 0] - mean_x
        dy = self._positions[:, 1] - mean_y
        dist_sq = dx * dx + dy * dy

        weighted_var = float(np.sum(self._weights * dist_sq))
        return float(np.sqrt(weighted_var))
