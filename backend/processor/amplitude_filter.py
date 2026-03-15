"""Amplitude filtering for CSI time-series data.

Provides Butterworth bandpass filtering and Hampel outlier removal for
per-subcarrier amplitude time series. Designed for the WiFi CSI pipeline
where different frequency bands isolate movement, breathing, and heartrate.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray
from scipy.signal import butter, sosfiltfilt


# ── Predefined frequency bands ──────────────────────────────────────────

BAND_MOVEMENT = (0.5, 5.0)
BAND_BREATHING = (0.1, 0.5)
BAND_HEARTRATE = (0.8, 2.0)


# ── Butterworth bandpass ─────────────────────────────────────────────────


def butterworth_bandpass(
    data: NDArray[np.floating],
    fs: float,
    low: float,
    high: float,
    order: int = 4,
) -> NDArray[np.floating]:
    """Apply a zero-phase Butterworth bandpass filter.

    Parameters
    ----------
    data : array of shape (T,) or (T, N)
        Time-series data. If 2-D, each column is filtered independently
        (T samples, N subcarriers).
    fs : float
        Sampling frequency in Hz.
    low, high : float
        Lower and upper cutoff frequencies in Hz.
    order : int
        Filter order (applied twice via sosfiltfilt, so effective order is 2x).

    Returns
    -------
    filtered : array, same shape as data
    """
    data = np.asarray(data, dtype=np.float64)
    nyq = fs / 2.0
    if low <= 0 or high <= 0 or low >= high:
        raise ValueError(f"Invalid band: ({low}, {high}) Hz")
    if high >= nyq:
        raise ValueError(f"Upper cutoff {high} Hz >= Nyquist {nyq} Hz")

    sos = butter(order, [low / nyq, high / nyq], btype="band", output="sos")
    return sosfiltfilt(sos, data, axis=0)


def bandpass_movement(
    data: NDArray[np.floating], fs: float, order: int = 4
) -> NDArray[np.floating]:
    """Bandpass for movement detection (0.5–5 Hz)."""
    return butterworth_bandpass(data, fs, *BAND_MOVEMENT, order=order)


def bandpass_breathing(
    data: NDArray[np.floating], fs: float, order: int = 4
) -> NDArray[np.floating]:
    """Bandpass for breathing rate extraction (0.1–0.5 Hz)."""
    return butterworth_bandpass(data, fs, *BAND_BREATHING, order=order)


def bandpass_heartrate(
    data: NDArray[np.floating], fs: float, order: int = 4
) -> NDArray[np.floating]:
    """Bandpass for heart rate extraction (0.8–2.0 Hz)."""
    return butterworth_bandpass(data, fs, *BAND_HEARTRATE, order=order)


# ── Hampel outlier filter ────────────────────────────────────────────────


def hampel_filter(
    data: NDArray[np.floating],
    window_size: int = 7,
    n_sigmas: float = 3.0,
) -> tuple[NDArray[np.floating], NDArray[np.bool_]]:
    """Replace outliers using the Hampel identifier.

    For each sample, computes the median and MAD (median absolute deviation)
    within a sliding window. Samples deviating more than `n_sigmas` MADs
    from the local median are replaced by that median.

    Parameters
    ----------
    data : array of shape (T,)
        1-D time series.
    window_size : int
        Half-window size (total window is 2*window_size + 1).
    n_sigmas : float
        Number of MAD-scaled deviations to flag as outlier.

    Returns
    -------
    filtered : array of shape (T,)
        Data with outliers replaced by local median.
    outlier_mask : boolean array of shape (T,)
        True where outliers were detected and replaced.
    """
    data = np.asarray(data, dtype=np.float64)
    if data.ndim != 1:
        raise ValueError(f"data must be 1-D, got shape {data.shape}")

    n = len(data)
    filtered = data.copy()
    outlier_mask = np.zeros(n, dtype=bool)

    # MAD-to-sigma scale factor (for normal distribution)
    k = 1.4826

    for i in range(n):
        lo = max(0, i - window_size)
        hi = min(n, i + window_size + 1)
        window = data[lo:hi]

        median = np.median(window)
        mad = k * np.median(np.abs(window - median))

        if mad > 0 and np.abs(data[i] - median) > n_sigmas * mad:
            filtered[i] = median
            outlier_mask[i] = True

    return filtered, outlier_mask


def hampel_filter_2d(
    data: NDArray[np.floating],
    window_size: int = 7,
    n_sigmas: float = 3.0,
) -> tuple[NDArray[np.floating], NDArray[np.bool_]]:
    """Apply Hampel filter to each column of a 2-D array.

    Parameters
    ----------
    data : array of shape (T, N)
        T samples, N subcarriers.
    window_size, n_sigmas : see hampel_filter.

    Returns
    -------
    filtered : array of shape (T, N)
    outlier_mask : boolean array of shape (T, N)
    """
    data = np.asarray(data, dtype=np.float64)
    if data.ndim != 2:
        raise ValueError(f"data must be 2-D, got shape {data.shape}")

    filtered = np.empty_like(data)
    outlier_mask = np.empty(data.shape, dtype=bool)

    for col in range(data.shape[1]):
        filtered[:, col], outlier_mask[:, col] = hampel_filter(
            data[:, col], window_size=window_size, n_sigmas=n_sigmas
        )

    return filtered, outlier_mask


# ── Filter pipeline ────────────────────────────────────────────────────


def filter_pipeline(
    data: NDArray[np.floating],
    fs: float,
    low: float,
    high: float,
    order: int = 4,
    hampel_window: int = 7,
    hampel_sigmas: float = 3.0,
) -> NDArray[np.floating]:
    """Apply the full amplitude filter pipeline: Hampel then Butterworth.

    Removes impulsive outliers first (Hampel), then isolates the target
    frequency band (Butterworth bandpass).

    Parameters
    ----------
    data : array of shape (T,) or (T, N)
        Raw amplitude time-series.  If 2-D, each column is a subcarrier
        stream processed independently.
    fs : float
        Sampling frequency in Hz (100 Hz for ESP32).
    low, high : float
        Bandpass cutoff frequencies in Hz.
    order : int
        Butterworth filter order (default 4, per research validation).
    hampel_window : int
        Half-window size for Hampel filter.
    hampel_sigmas : float
        MAD threshold multiplier for Hampel outlier detection.

    Returns
    -------
    filtered : array, same shape as data
        Cleaned and bandpass-filtered signal.
    """
    data = np.asarray(data, dtype=np.float64)

    # Step 1: Hampel outlier removal
    if data.ndim == 1:
        cleaned, _ = hampel_filter(data, window_size=hampel_window,
                                   n_sigmas=hampel_sigmas)
    elif data.ndim == 2:
        cleaned, _ = hampel_filter_2d(data, window_size=hampel_window,
                                      n_sigmas=hampel_sigmas)
    else:
        raise ValueError(f"data must be 1-D or 2-D, got shape {data.shape}")

    # Step 2: Butterworth bandpass
    return butterworth_bandpass(cleaned, fs, low, high, order=order)


def filter_breathing(
    data: NDArray[np.floating],
    fs: float = 100.0,
    order: int = 4,
    hampel_window: int = 7,
    hampel_sigmas: float = 3.0,
) -> NDArray[np.floating]:
    """Full pipeline for breathing signal: Hampel(w=7) then 0.1-0.5 Hz bandpass.

    Parameters
    ----------
    data : array of shape (T,) or (T, N)
        Raw amplitude time-series.
    fs : float
        Sampling frequency in Hz (default 100 Hz).
    order : int
        Butterworth filter order (default 4).
    hampel_window : int
        Half-window for Hampel (default 7, i.e. 70ms at 100Hz).
    hampel_sigmas : float
        MAD threshold (default 3.0).

    Returns
    -------
    filtered : array, same shape as data
    """
    return filter_pipeline(
        data, fs, *BAND_BREATHING, order=order,
        hampel_window=hampel_window, hampel_sigmas=hampel_sigmas,
    )


def filter_heartrate(
    data: NDArray[np.floating],
    fs: float = 100.0,
    order: int = 4,
    hampel_window: int = 5,
    hampel_sigmas: float = 3.0,
) -> NDArray[np.floating]:
    """Full pipeline for heart rate signal: Hampel(w=5) then 0.8-2.0 Hz bandpass.

    Parameters
    ----------
    data : array of shape (T,) or (T, N)
        Raw amplitude time-series.
    fs : float
        Sampling frequency in Hz (default 100 Hz).
    order : int
        Butterworth filter order (default 4).
    hampel_window : int
        Half-window for Hampel (default 5, i.e. 50ms at 100Hz).
    hampel_sigmas : float
        MAD threshold (default 3.0).

    Returns
    -------
    filtered : array, same shape as data
    """
    return filter_pipeline(
        data, fs, *BAND_HEARTRATE, order=order,
        hampel_window=hampel_window, hampel_sigmas=hampel_sigmas,
    )
