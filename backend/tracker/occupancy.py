"""Multi-person occupancy detection via Non-negative Matrix Factorization.

Decomposes a rolling window of CSI amplitude snapshots (time × subcarriers)
into independent signal sources, where each source approximates one person's
perturbation pattern.  The number of sources is estimated by comparing NMF
reconstruction errors across candidate counts and selecting the best via a
residual-ratio elbow test.

Known limitation: reliable for 1-2 people in separate rooms; accuracy degrades
when people are in close proximity (< ~2m) because their CSI perturbations
overlap and cannot be cleanly separated.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
from numpy.typing import NDArray


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OccupancyResult:
    """Output of an occupancy detection step."""

    occupancy_estimate: int  # best-guess number of people
    occupancy_confidence: float  # [0, 1] — higher means cleaner separation
    occupancy_min: int  # low end of plausible range (ambiguity)
    occupancy_max: int  # high end of plausible range
    components: NDArray[np.float64]  # (k, n_subcarriers) — per-person CSI signatures


# ---------------------------------------------------------------------------
# Configurable defaults
# ---------------------------------------------------------------------------

DEFAULT_MAX_PEOPLE: int = 6
DEFAULT_WINDOW_SIZE: int = 100  # number of CSI snapshots in the sliding window
DEFAULT_NMF_MAX_ITER: int = 200
DEFAULT_NMF_TOL: float = 1e-4
DEFAULT_ELBOW_THRESHOLD: float = 0.10  # min relative error reduction to add a component
DEFAULT_CONFIDENCE_DECAY_RATE: float = 3.0  # controls how fast confidence drops with ambiguity
DEFAULT_MIN_SNAPSHOTS: int = 10  # minimum snapshots before running NMF
DEFAULT_MIN_INTERVAL_S: float = 2.0  # minimum seconds between NMF detections
DEFAULT_SAMPLE_RATE: float = 100.0  # Hz — CSI sample rate


# ---------------------------------------------------------------------------
# NMF core (multiplicative update rules — Lee & Seung 2001)
# ---------------------------------------------------------------------------

_EPSILON = 1e-12  # prevent division by zero


def _nmf(
    V: NDArray[np.float64],
    k: int,
    max_iter: int = DEFAULT_NMF_MAX_ITER,
    tol: float = DEFAULT_NMF_TOL,
    rng: np.random.Generator | None = None,
) -> tuple[NDArray[np.float64], NDArray[np.float64], float]:
    """Run NMF via multiplicative updates: V ≈ W @ H.

    Args:
        V: Non-negative matrix (m, n) — m time steps × n subcarriers.
        k: Number of components (sources).
        max_iter: Maximum iterations.
        tol: Convergence tolerance on relative Frobenius norm change.
        rng: Random number generator for reproducibility.

    Returns:
        (W, H, reconstruction_error) where:
            W: (m, k) temporal activation matrix
            H: (k, n) subcarrier signature matrix
            reconstruction_error: ||V - W@H||_F / ||V||_F (relative)
    """
    if rng is None:
        rng = np.random.default_rng()

    m, n = V.shape

    # Initialize W, H with small positive random values scaled by sqrt(mean(V)/k)
    scale = np.sqrt(np.mean(V) / max(k, 1)) + _EPSILON
    W = rng.uniform(0.01, scale, size=(m, k)).astype(np.float64)
    H = rng.uniform(0.01, scale, size=(k, n)).astype(np.float64)

    v_norm = np.linalg.norm(V) + _EPSILON
    prev_error = float("inf")

    for _ in range(max_iter):
        # Update H: H *= (W^T V) / (W^T W H + eps)
        numerator_h = W.T @ V
        denominator_h = W.T @ W @ H + _EPSILON
        H *= numerator_h / denominator_h

        # Update W: W *= (V H^T) / (W H H^T + eps)
        numerator_w = V @ H.T
        denominator_w = W @ H @ H.T + _EPSILON
        W *= numerator_w / denominator_w

        # Check convergence
        residual = np.linalg.norm(V - W @ H)
        rel_error = residual / v_norm

        if abs(prev_error - rel_error) < tol:
            break
        prev_error = rel_error

    return W, H, float(rel_error)


# ---------------------------------------------------------------------------
# Source count estimation
# ---------------------------------------------------------------------------


def _estimate_source_count(
    V: NDArray[np.float64],
    max_k: int,
    elbow_threshold: float,
    nmf_max_iter: int,
    nmf_tol: float,
    rng: np.random.Generator,
) -> tuple[int, list[float], float]:
    """Estimate the number of independent sources using residual-ratio elbow test.

    Runs NMF for k = 1..max_k and looks for the point where adding another
    component yields diminishing returns (relative error reduction < threshold).

    Returns:
        (best_k, errors_per_k, confidence) where confidence reflects how
        clearly the elbow separates the best k from alternatives.
    """
    errors: list[float] = []

    for k in range(1, max_k + 1):
        _, _, err = _nmf(V, k, max_iter=nmf_max_iter, tol=nmf_tol, rng=rng)
        errors.append(err)

        # Early exit: if error is already very small, no need to try more
        if err < 0.01:
            break

    # Find elbow: first k where adding k+1 doesn't improve enough
    best_k = 1
    for i in range(1, len(errors)):
        improvement = (errors[i - 1] - errors[i]) / (errors[i - 1] + _EPSILON)
        if improvement >= elbow_threshold:
            best_k = i + 1  # k is 1-indexed
        else:
            break

    # Confidence: how distinct is the elbow?
    # Higher when: (1) low reconstruction error at best_k, (2) clear elbow
    base_confidence = 1.0 - min(errors[best_k - 1], 1.0)

    return best_k, errors, base_confidence


# ---------------------------------------------------------------------------
# Occupancy detector (stateful)
# ---------------------------------------------------------------------------


class OccupancyDetector:
    """Detects multi-person occupancy from a rolling window of CSI amplitudes.

    Maintains a sliding window of CSI amplitude snapshots. On each update,
    if enough snapshots have accumulated, runs NMF with varying source counts
    and selects the best via residual-ratio elbow detection.

    Args:
        max_people: Maximum number of people to detect (default 6).
        window_size: Number of CSI snapshots in the rolling window (default 100).
        min_snapshots: Minimum snapshots required before running NMF (default 10).
        elbow_threshold: Minimum relative error improvement to add a component.
        nmf_max_iter: Maximum NMF iterations per candidate k.
        nmf_tol: NMF convergence tolerance.
        confidence_decay_rate: Controls how fast confidence drops with
            occupancy range width. Higher = faster decay.
        seed: Optional RNG seed for reproducibility.
        min_interval_s: Minimum seconds between NMF detections (rate limiting).
        sample_rate: CSI sample rate in Hz (used for rate limiting).
    """

    def __init__(
        self,
        max_people: int = DEFAULT_MAX_PEOPLE,
        window_size: int = DEFAULT_WINDOW_SIZE,
        min_snapshots: int = DEFAULT_MIN_SNAPSHOTS,
        elbow_threshold: float = DEFAULT_ELBOW_THRESHOLD,
        nmf_max_iter: int = DEFAULT_NMF_MAX_ITER,
        nmf_tol: float = DEFAULT_NMF_TOL,
        confidence_decay_rate: float = DEFAULT_CONFIDENCE_DECAY_RATE,
        seed: Optional[int] = None,
        min_interval_s: float = DEFAULT_MIN_INTERVAL_S,
        sample_rate: float = DEFAULT_SAMPLE_RATE,
    ) -> None:
        self._max_people = max_people
        self._window_size = window_size
        self._min_snapshots = min_snapshots
        self._elbow_threshold = elbow_threshold
        self._nmf_max_iter = nmf_max_iter
        self._nmf_tol = nmf_tol
        self._confidence_decay_rate = confidence_decay_rate
        self._rng = np.random.default_rng(seed)
        self._min_interval_s = min_interval_s
        self._sample_rate = sample_rate

        # Rolling buffer: list of 1-D amplitude arrays
        self._buffer: list[NDArray[np.float64]] = []
        self._n_subcarriers: int | None = None

        # Rate limiting
        self._snapshots_since_detect: int = 0
        self._last_result: Optional[OccupancyResult] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def buffer_size(self) -> int:
        """Number of CSI snapshots currently in the buffer."""
        return len(self._buffer)

    @property
    def is_ready(self) -> bool:
        """Whether enough snapshots have accumulated to run detection."""
        return len(self._buffer) >= self._min_snapshots

    def push(self, amplitude: NDArray[np.float64]) -> None:
        """Add a CSI amplitude snapshot to the rolling buffer.

        Args:
            amplitude: 1-D array of subcarrier amplitudes (non-negative).
                Length must be consistent across calls.
        """
        amp = np.asarray(amplitude, dtype=np.float64).ravel()
        if amp.size == 0:
            raise ValueError("Amplitude array must be non-empty")

        if self._n_subcarriers is None:
            self._n_subcarriers = amp.size
        elif amp.size != self._n_subcarriers:
            raise ValueError(
                f"Expected {self._n_subcarriers} subcarriers, got {amp.size}"
            )

        # Ensure non-negative (CSI amplitudes should already be ≥ 0)
        amp = np.maximum(amp, 0.0)

        self._buffer.append(amp)
        self._snapshots_since_detect += 1

        # Trim to window size
        if len(self._buffer) > self._window_size:
            self._buffer = self._buffer[-self._window_size :]

    def detect(self) -> OccupancyResult:
        """Run occupancy detection on the current buffer.

        Returns:
            OccupancyResult with estimate, confidence, range, and per-person
            CSI component signatures.

        Raises:
            RuntimeError: If fewer than min_snapshots are in the buffer.
        """
        if not self.is_ready:
            raise RuntimeError(
                f"Need at least {self._min_snapshots} snapshots, "
                f"have {len(self._buffer)}"
            )

        # Build the amplitude matrix: (time, subcarriers)
        V = np.vstack(self._buffer)

        # Cap max_k to be meaningful given the matrix dimensions
        # NMF k must be ≤ min(m, n) and ≤ max_people
        max_k = min(self._max_people, V.shape[0], V.shape[1])
        max_k = max(max_k, 1)

        # Estimate source count
        best_k, errors, base_confidence = _estimate_source_count(
            V,
            max_k=max_k,
            elbow_threshold=self._elbow_threshold,
            nmf_max_iter=self._nmf_max_iter,
            nmf_tol=self._nmf_tol,
            rng=self._rng,
        )

        # Run final NMF at best_k to get the component signatures
        _, H, final_error = _nmf(
            V, best_k, max_iter=self._nmf_max_iter, tol=self._nmf_tol, rng=self._rng
        )

        # Determine plausible range (ambiguity)
        occ_min, occ_max = self._compute_range(best_k, errors)

        # Confidence: combine reconstruction quality with range tightness
        range_width = occ_max - occ_min
        range_penalty = float(np.exp(-range_width / self._confidence_decay_rate))
        confidence = float(np.clip(base_confidence * range_penalty, 0.0, 1.0))

        return OccupancyResult(
            occupancy_estimate=best_k,
            occupancy_confidence=confidence,
            occupancy_min=occ_min,
            occupancy_max=occ_max,
            components=H.copy(),
        )

    def update(self, amplitude: NDArray[np.float64]) -> OccupancyResult | None:
        """Convenience: push a snapshot and detect if ready.

        Rate-limited: skips NMF if fewer than ``min_interval_s`` worth of
        snapshots have arrived since the last detection, returning the
        cached result instead.

        Returns OccupancyResult if enough data, None otherwise.
        """
        self.push(amplitude)
        if not self.is_ready:
            return None

        # Rate limiting: only run NMF after enough new snapshots
        min_snapshots_interval = int(self._min_interval_s * self._sample_rate)
        if (
            self._last_result is not None
            and self._snapshots_since_detect < min_snapshots_interval
        ):
            return self._last_result

        result = self.detect()
        self._snapshots_since_detect = 0
        self._last_result = result
        return result

    def reset(self) -> None:
        """Clear the buffer and reset state."""
        self._buffer.clear()
        self._n_subcarriers = None
        self._snapshots_since_detect = 0
        self._last_result = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _compute_range(
        self, best_k: int, errors: list[float]
    ) -> tuple[int, int]:
        """Compute plausible occupancy range from reconstruction errors.

        Neighbors of best_k whose error is within a relative margin of best_k's
        error are considered plausible alternatives.
        """
        if len(errors) <= 1:
            return (best_k, best_k)

        best_error = errors[best_k - 1]
        margin = 0.05  # 5% relative margin

        occ_min = best_k
        occ_max = best_k

        for k_idx in range(len(errors)):
            k = k_idx + 1
            if abs(errors[k_idx] - best_error) / (best_error + _EPSILON) <= margin:
                occ_min = min(occ_min, k)
                occ_max = max(occ_max, k)

        return (occ_min, occ_max)
