"""SpotFi phase sanitization — removes linear phase offset from CSI data.

WiFi CSI phase measurements contain a linear offset across subcarrier indices
caused by carrier frequency offset (CFO) and sampling frequency offset (SFO).
SpotFi removes this by fitting phase[k] = a*k + b via least-squares and
subtracting the fitted line.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray


def sanitize_phase(
    phase: NDArray[np.floating],
    subcarrier_indices: NDArray[np.integer] | None = None,
) -> NDArray[np.floating]:
    """Remove linear phase offset from a CSI phase vector.

    Parameters
    ----------
    phase : array of shape (N,)
        Raw phase values in radians for N subcarriers.
    subcarrier_indices : array of shape (N,), optional
        Subcarrier index for each element. If None, uses 0..N-1.

    Returns
    -------
    sanitized : array of shape (N,)
        Phase with linear component removed, in radians.
    """
    phase = np.asarray(phase, dtype=np.float64)
    if phase.ndim != 1:
        raise ValueError(f"phase must be 1-D, got shape {phase.shape}")
    n = len(phase)
    if n < 2:
        return phase.copy()

    if subcarrier_indices is None:
        k = np.arange(n, dtype=np.float64)
    else:
        k = np.asarray(subcarrier_indices, dtype=np.float64)
        if k.shape != phase.shape:
            raise ValueError(
                f"subcarrier_indices shape {k.shape} != phase shape {phase.shape}"
            )

    # Unwrap to remove 2π discontinuities before fitting
    unwrapped = np.unwrap(phase)

    # Least-squares fit: unwrapped[i] = a * k[i] + b
    a, b = np.polyfit(k, unwrapped, 1)

    # Subtract the linear component
    sanitized = unwrapped - (a * k + b)

    return sanitized


def sanitize_phase_batch(
    phases: NDArray[np.floating],
    subcarrier_indices: NDArray[np.integer] | None = None,
) -> NDArray[np.floating]:
    """Sanitize a batch of phase vectors (one per row).

    Parameters
    ----------
    phases : array of shape (M, N)
        M frames, each with N subcarrier phase values.
    subcarrier_indices : array of shape (N,), optional
        Shared subcarrier indices for all rows.

    Returns
    -------
    sanitized : array of shape (M, N)
    """
    phases = np.asarray(phases, dtype=np.float64)
    if phases.ndim != 2:
        raise ValueError(f"phases must be 2-D, got shape {phases.shape}")

    m, n = phases.shape
    if n < 2:
        return phases.copy()

    if subcarrier_indices is None:
        k = np.arange(n, dtype=np.float64)
    else:
        k = np.asarray(subcarrier_indices, dtype=np.float64)

    # Unwrap each row independently
    unwrapped = np.unwrap(phases, axis=1)

    # Vectorized least-squares: fit a line per row
    # Design matrix [k, 1]
    A = np.column_stack([k, np.ones(n)])
    # Solve for all rows at once: coeffs shape (2, M)
    coeffs, *_ = np.linalg.lstsq(A, unwrapped.T, rcond=None)
    # coeffs[0] = slopes, coeffs[1] = intercepts
    fitted = coeffs[0][:, np.newaxis] * k[np.newaxis, :] + coeffs[1][:, np.newaxis]

    return unwrapped - fitted
