"""Subcarrier selection by amplitude variance for WiFi CSI processing.

Selects the top-K most informative subcarriers from the full set of 114
(HT40 mode) based on amplitude variance over a sliding window. Subcarriers
with higher variance are more sensitive to environmental changes (motion,
breathing) and produce better features for tracking and vital signs.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray


DEFAULT_K: int = 30
DEFAULT_WINDOW_SAMPLES: int = 100  # 1s at 100 Hz


@dataclass(frozen=True)
class SubcarrierSelection:
    """Result of subcarrier selection.

    Attributes:
        indices: Selected subcarrier indices, sorted by descending score.
            Shape: (K,).
        data: Amplitude data for selected subcarriers only.
            Shape: (T, K) where T is the number of time samples.
        scores: Selection score (variance or SNR-weighted variance) for each
            selected subcarrier. Shape: (K,).
    """

    indices: NDArray[np.intp]
    data: NDArray[np.floating]
    scores: NDArray[np.floating]


def compute_variance(
    amplitudes: NDArray[np.floating],
    window_samples: int = DEFAULT_WINDOW_SAMPLES,
) -> NDArray[np.floating]:
    """Compute per-subcarrier variance over the most recent window.

    Parameters
    ----------
    amplitudes : array of shape (T, N)
        Amplitude time-series: T samples, N subcarriers.
    window_samples : int
        Number of trailing samples to use for variance computation.
        If T < window_samples, uses all available samples.

    Returns
    -------
    variances : array of shape (N,)
        Variance for each subcarrier.
    """
    amplitudes = np.asarray(amplitudes, dtype=np.float64)
    if amplitudes.ndim != 2:
        raise ValueError(f"amplitudes must be 2-D, got shape {amplitudes.shape}")

    t = amplitudes.shape[0]
    window = min(t, window_samples)
    tail = amplitudes[-window:]
    return np.var(tail, axis=0, ddof=0)


def select_top_k(
    amplitudes: NDArray[np.floating],
    k: int = DEFAULT_K,
    window_samples: int = DEFAULT_WINDOW_SAMPLES,
    snr: NDArray[np.floating] | None = None,
) -> SubcarrierSelection:
    """Select top-K subcarriers by amplitude variance.

    Parameters
    ----------
    amplitudes : array of shape (T, N)
        Amplitude time-series: T samples, N subcarriers (typically 114).
    k : int
        Number of subcarriers to select (default 30).
    window_samples : int
        Sliding window size in samples for variance computation
        (default 100 = 1s at 100 Hz).
    snr : array of shape (N,), optional
        Per-subcarrier SNR estimate. When provided, the selection score
        becomes ``variance * snr`` rather than raw variance, favoring
        subcarriers that are both dynamic and high-quality.

    Returns
    -------
    SubcarrierSelection
        Selected indices, their amplitude data, and scores.

    Raises
    ------
    ValueError
        If amplitudes has wrong dimensions, k < 1, or k > N.
    """
    amplitudes = np.asarray(amplitudes, dtype=np.float64)
    if amplitudes.ndim != 2:
        raise ValueError(f"amplitudes must be 2-D, got shape {amplitudes.shape}")

    n_subcarriers = amplitudes.shape[1]
    if k < 1 or k > n_subcarriers:
        raise ValueError(
            f"k must be in [1, {n_subcarriers}], got {k}"
        )

    variances = compute_variance(amplitudes, window_samples)

    if snr is not None:
        snr = np.asarray(snr, dtype=np.float64)
        if snr.shape != (n_subcarriers,):
            raise ValueError(
                f"snr shape {snr.shape} doesn't match subcarrier count {n_subcarriers}"
            )
        scores = variances * snr
    else:
        scores = variances

    # Top-K indices by descending score
    top_indices = np.argsort(scores)[::-1][:k]
    # Sort by index position for consistent ordering in output
    # (but scores array preserves the ranking order)
    ranked_indices = top_indices  # keep descending-score order

    return SubcarrierSelection(
        indices=ranked_indices,
        data=amplitudes[:, ranked_indices],
        scores=scores[ranked_indices],
    )
