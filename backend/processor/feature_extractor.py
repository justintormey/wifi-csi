"""Feature extraction for WiFi CSI fingerprinting.

Builds fingerprint feature vectors from processed CSI amplitude and phase data.
Each feature vector concatenates per-subcarrier statistics over a configurable
time window, then normalizes for use with the cosine-distance KNN in
``tracker.fingerprint_db``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np
from numpy.typing import NDArray


class NormMethod(Enum):
    """Normalization method for feature vectors."""

    L2 = "l2"
    ZSCORE = "zscore"
    NONE = "none"


DEFAULT_WINDOW_SAMPLES: int = 100  # 1s at 100 Hz
DEFAULT_NORM: NormMethod = NormMethod.L2


@dataclass(frozen=True)
class FeatureVector:
    """Extracted fingerprint feature vector.

    Attributes:
        vector: 1-D feature vector of shape (4*K,) where K is the number of
            selected subcarriers. Layout: [mean_amp (K), var_amp (K),
            mean_phase (K), std_phase (K)].
        n_subcarriers: Number of subcarriers (K) used.
        n_samples: Number of time samples used from the window.
        norm_method: Normalization method applied.
    """

    vector: NDArray[np.float64]
    n_subcarriers: int
    n_samples: int
    norm_method: NormMethod


def _normalize(
    vec: NDArray[np.float64], method: NormMethod
) -> NDArray[np.float64]:
    """Apply normalization to a feature vector."""
    if method is NormMethod.NONE:
        return vec

    if method is NormMethod.L2:
        norm = np.linalg.norm(vec)
        if norm == 0:
            return vec
        return vec / norm

    if method is NormMethod.ZSCORE:
        std = np.std(vec)
        if std == 0:
            return vec - np.mean(vec)
        return (vec - np.mean(vec)) / std

    raise ValueError(f"Unknown normalization method: {method}")


def extract_features(
    amplitudes: NDArray[np.floating],
    phases: NDArray[np.floating],
    window_samples: int = DEFAULT_WINDOW_SAMPLES,
    norm: NormMethod | str = DEFAULT_NORM,
) -> FeatureVector:
    """Build a fingerprint feature vector from CSI amplitude and phase data.

    Parameters
    ----------
    amplitudes : array of shape (T, K)
        Amplitude time-series for K selected subcarriers over T samples.
    phases : array of shape (T, K)
        Phase time-series (radians) for the same K subcarriers.
    window_samples : int
        Number of trailing samples to use (default 100 = 1s at 100 Hz).
        If T < window_samples, all samples are used.
    norm : NormMethod or str
        Normalization: ``"l2"``, ``"zscore"``, or ``"none"``.

    Returns
    -------
    FeatureVector
        Feature vector of shape (4*K,) with metadata.

    Raises
    ------
    ValueError
        If inputs have wrong dimensions or mismatched shapes.
    """
    amplitudes = np.asarray(amplitudes, dtype=np.float64)
    phases = np.asarray(phases, dtype=np.float64)

    if amplitudes.ndim != 2:
        raise ValueError(
            f"amplitudes must be 2-D (T, K), got shape {amplitudes.shape}"
        )
    if phases.ndim != 2:
        raise ValueError(
            f"phases must be 2-D (T, K), got shape {phases.shape}"
        )
    if amplitudes.shape != phases.shape:
        raise ValueError(
            f"Shape mismatch: amplitudes {amplitudes.shape} vs phases {phases.shape}"
        )
    if amplitudes.shape[0] == 0 or amplitudes.shape[1] == 0:
        raise ValueError("Input arrays must be non-empty")

    if isinstance(norm, str):
        norm = NormMethod(norm)

    t, k = amplitudes.shape
    n = min(t, window_samples)

    amp_window = amplitudes[-n:]
    phase_window = phases[-n:]

    # Per-subcarrier statistics
    mean_amp = np.mean(amp_window, axis=0)       # (K,)
    var_amp = np.var(amp_window, axis=0, ddof=0)  # (K,)
    mean_phase = np.mean(phase_window, axis=0)    # (K,)
    std_phase = np.std(phase_window, axis=0, ddof=0)  # (K,)

    raw = np.concatenate([mean_amp, var_amp, mean_phase, std_phase])
    normalized = _normalize(raw, norm)

    return FeatureVector(
        vector=normalized,
        n_subcarriers=k,
        n_samples=n,
        norm_method=norm,
    )
