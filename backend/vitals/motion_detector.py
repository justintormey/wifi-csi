"""Static vs moving classification from CSI amplitude variance.

Classifies a person as stationary or moving by computing the normalized
variance of CSI amplitudes over a short sliding window.  When the variance
(normalized to [0, 1]) exceeds a configurable threshold the person is
considered moving; otherwise they are stationary.

The module tracks how long a person has been continuously stationary
(``stationary_duration_s``), which downstream modules — especially heart
rate extraction — use as a gate (typically requiring > 30 s of stillness).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
from numpy.typing import NDArray


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MotionResult:
    """Output of a motion classification step."""

    is_stationary: bool  # True when motion_level < threshold
    stationary_duration_s: float  # seconds continuously stationary (resets on movement)
    motion_level: float  # [0, 1] — 0 = perfectly still, 1 = maximum motion


# ---------------------------------------------------------------------------
# Configurable defaults
# ---------------------------------------------------------------------------

DEFAULT_MOTION_THRESHOLD: float = 0.15  # motion_level above this → moving
DEFAULT_WINDOW_SIZE: int = 50  # CSI snapshots in the variance window
DEFAULT_MIN_SNAPSHOTS: int = 5  # minimum snapshots before classifying
DEFAULT_SAMPLE_RATE: float = 100.0  # Hz — default CSI sample rate


# ---------------------------------------------------------------------------
# Motion detector (stateful)
# ---------------------------------------------------------------------------


class MotionDetector:
    """Classifies a person as stationary or moving from CSI amplitude variance.

    Maintains a sliding window of CSI amplitude snapshots.  On each
    classification call, computes the mean per-subcarrier variance across the
    window and normalizes it to a 0-1 motion level using an adaptive baseline.

    Args:
        motion_threshold: Motion level above which the person is classified
            as moving (default 0.15).
        window_size: Number of CSI snapshots in the sliding window.
        min_snapshots: Minimum snapshots before classification is possible.
        sample_rate: CSI sample rate in Hz (used to compute durations).
        baseline_ema_alpha: Exponential moving average smoothing factor for
            the adaptive baseline (lower = smoother).  The baseline tracks
            the ambient CSI variance when the environment is empty / still.
    """

    def __init__(
        self,
        motion_threshold: float = DEFAULT_MOTION_THRESHOLD,
        window_size: int = DEFAULT_WINDOW_SIZE,
        min_snapshots: int = DEFAULT_MIN_SNAPSHOTS,
        sample_rate: float = DEFAULT_SAMPLE_RATE,
        baseline_ema_alpha: float = 0.01,
    ) -> None:
        if motion_threshold < 0 or motion_threshold > 1:
            raise ValueError("motion_threshold must be in [0, 1]")
        if window_size < 2:
            raise ValueError("window_size must be >= 2")
        if min_snapshots < 2:
            raise ValueError("min_snapshots must be >= 2")
        if sample_rate <= 0:
            raise ValueError("sample_rate must be > 0")

        self._motion_threshold = motion_threshold
        self._window_size = window_size
        self._min_snapshots = min_snapshots
        self._sample_rate = sample_rate
        self._baseline_ema_alpha = baseline_ema_alpha

        # Rolling buffer: list of 1-D amplitude arrays
        self._buffer: list[NDArray[np.float64]] = []
        self._n_subcarriers: int | None = None

        # Adaptive baseline for normalization (learned from data)
        self._baseline_variance: float | None = None

        # Stationarity tracking
        self._stationary_snapshots: int = 0  # count of consecutive stationary snapshots
        self._total_snapshots: int = 0  # total snapshots pushed (for duration calc)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def buffer_size(self) -> int:
        """Number of CSI snapshots currently in the buffer."""
        return len(self._buffer)

    @property
    def is_ready(self) -> bool:
        """Whether enough snapshots have accumulated to classify."""
        return len(self._buffer) >= self._min_snapshots

    def push(self, amplitude: NDArray[np.float64]) -> None:
        """Add a CSI amplitude snapshot to the rolling buffer.

        Args:
            amplitude: 1-D array of subcarrier amplitudes.
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

        self._buffer.append(amp)
        self._total_snapshots += 1

        # Trim to window size
        if len(self._buffer) > self._window_size:
            self._buffer = self._buffer[-self._window_size:]

    def classify(self) -> MotionResult:
        """Classify the current window as stationary or moving.

        Returns:
            MotionResult with is_stationary, stationary_duration_s,
            and motion_level.

        Raises:
            RuntimeError: If fewer than min_snapshots are in the buffer.
        """
        if not self.is_ready:
            raise RuntimeError(
                f"Need at least {self._min_snapshots} snapshots, "
                f"have {len(self._buffer)}"
            )

        # Build matrix: (time, subcarriers)
        M = np.vstack(self._buffer)

        # Compute per-subcarrier variance across time, then take the mean
        raw_variance = float(np.mean(np.var(M, axis=0)))

        # Update adaptive baseline (EMA of raw variance)
        if self._baseline_variance is None:
            self._baseline_variance = raw_variance
        else:
            alpha = self._baseline_ema_alpha
            self._baseline_variance = (
                alpha * raw_variance + (1 - alpha) * self._baseline_variance
            )

        # Normalize to [0, 1] using the baseline
        # motion_level = raw / (raw + baseline) — sigmoid-like mapping
        # When raw ≈ baseline → ~0.5; when raw >> baseline → ~1.0
        # We subtract the baseline contribution to center stationary around 0
        baseline = max(self._baseline_variance, 1e-12)
        ratio = raw_variance / baseline

        # Map ratio to [0, 1]: ratio=1 (at baseline) → ~0, ratio=large → ~1
        # Using: motion_level = 1 - 1/(1 + max(ratio - 1, 0))
        # This gives 0 when ratio ≤ 1 and approaches 1 for large ratios
        motion_level = 1.0 - 1.0 / (1.0 + max(ratio - 1.0, 0.0))
        motion_level = float(np.clip(motion_level, 0.0, 1.0))

        is_stationary = motion_level < self._motion_threshold

        # Update stationarity counter
        if is_stationary:
            self._stationary_snapshots += 1
        else:
            self._stationary_snapshots = 0

        stationary_duration_s = self._stationary_snapshots / self._sample_rate

        return MotionResult(
            is_stationary=is_stationary,
            stationary_duration_s=stationary_duration_s,
            motion_level=motion_level,
        )

    def update(self, amplitude: NDArray[np.float64]) -> MotionResult | None:
        """Convenience: push a snapshot and classify if ready.

        Returns MotionResult if enough data, None otherwise.
        """
        self.push(amplitude)
        if self.is_ready:
            return self.classify()
        return None

    def reset(self) -> None:
        """Clear the buffer and reset all state."""
        self._buffer.clear()
        self._n_subcarriers = None
        self._baseline_variance = None
        self._stationary_snapshots = 0
        self._total_snapshots = 0
