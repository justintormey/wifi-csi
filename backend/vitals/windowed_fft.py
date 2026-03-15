"""FFT/CWT shared utilities for vital signs extraction.

Provides windowed FFT, CWT (Morlet wavelet), peak detection, and SNR
calculation.  Shared by the breathing and heartrate modules.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

import numpy as np
from numpy.typing import NDArray
from scipy.signal import find_peaks, fftconvolve


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

class WindowType(str, Enum):
    HANNING = "hanning"
    HAMMING = "hamming"


@dataclass(frozen=True)
class SpectralPeak:
    """Result of peak detection in the frequency domain."""
    frequency_hz: float
    power: float
    snr_db: float


@dataclass(frozen=True)
class FFTResult:
    """Full FFT result for a single window."""
    frequencies: NDArray[np.float64]
    power_spectrum: NDArray[np.float64]
    peaks: list[SpectralPeak]


@dataclass(frozen=True)
class CWTResult:
    """Continuous wavelet transform result."""
    frequencies: NDArray[np.float64]
    time: NDArray[np.float64]
    coefficients: NDArray[np.complex128]
    peak: Optional[SpectralPeak]


# ---------------------------------------------------------------------------
# Window functions
# ---------------------------------------------------------------------------

def _get_window(n: int, window_type: WindowType) -> NDArray[np.float64]:
    if window_type == WindowType.HANNING:
        return np.hanning(n)
    return np.hamming(n)


# ---------------------------------------------------------------------------
# SNR
# ---------------------------------------------------------------------------

def _snr_db(power_spectrum: NDArray[np.float64], peak_idx: int,
            guard_bins: int = 2) -> float:
    """Signal-to-noise ratio in dB.

    Peak power is the value at *peak_idx*.  Noise floor is the median of
    all bins **excluding** the peak +/- *guard_bins*.
    """
    mask = np.ones(len(power_spectrum), dtype=bool)
    lo = max(0, peak_idx - guard_bins)
    hi = min(len(power_spectrum), peak_idx + guard_bins + 1)
    mask[lo:hi] = False
    noise = np.median(power_spectrum[mask]) if mask.any() else 1e-12
    if noise <= 0:
        noise = 1e-12
    return float(10.0 * np.log10(power_spectrum[peak_idx] / noise))


# ---------------------------------------------------------------------------
# Windowed FFT
# ---------------------------------------------------------------------------

def windowed_fft(
    signal: NDArray[np.float64],
    sample_rate: float,
    window_size: int,
    overlap: int = 0,
    window_type: WindowType = WindowType.HANNING,
    freq_range: Optional[tuple[float, float]] = None,
    max_peaks: int = 3,
) -> list[FFTResult]:
    """Compute windowed FFT with optional band-limiting and peak detection.

    Parameters
    ----------
    signal : 1-D array
        Input time-series (e.g. filtered CSI amplitude).
    sample_rate : float
        Sampling rate in Hz.
    window_size : int
        Number of samples per FFT window.
    overlap : int
        Number of overlapping samples between consecutive windows.
    window_type : WindowType
        Window function applied before FFT.
    freq_range : (lo, hi) or None
        If given, restrict peak search to this frequency band (Hz).
    max_peaks : int
        Maximum number of spectral peaks to return per window.

    Returns
    -------
    list[FFTResult]
        One result per window.
    """
    step = window_size - overlap
    if step <= 0:
        raise ValueError("overlap must be less than window_size")

    n_samples = len(signal)
    window = _get_window(window_size, window_type)
    results: list[FFTResult] = []

    start = 0
    while start + window_size <= n_samples:
        segment = signal[start: start + window_size] * window

        spectrum = np.fft.rfft(segment)
        freqs = np.fft.rfftfreq(window_size, d=1.0 / sample_rate)
        power = np.abs(spectrum) ** 2

        # Band-limit for peak search
        if freq_range is not None:
            lo_hz, hi_hz = freq_range
            band_mask = (freqs >= lo_hz) & (freqs <= hi_hz)
        else:
            # Skip DC bin
            band_mask = np.ones(len(freqs), dtype=bool)
            band_mask[0] = False

        band_power = np.where(band_mask, power, 0.0)
        peak_indices, _ = find_peaks(band_power)

        # Sort by power descending
        if len(peak_indices) > 0:
            peak_indices = peak_indices[np.argsort(band_power[peak_indices])[::-1]]
            peak_indices = peak_indices[:max_peaks]

        peaks = [
            SpectralPeak(
                frequency_hz=float(freqs[idx]),
                power=float(power[idx]),
                snr_db=_snr_db(power, idx),
            )
            for idx in peak_indices
        ]

        results.append(FFTResult(
            frequencies=freqs,
            power_spectrum=power,
            peaks=peaks,
        ))
        start += step

    return results


# ---------------------------------------------------------------------------
# CWT (Morlet wavelet)
# ---------------------------------------------------------------------------

def morlet_cwt(
    signal: NDArray[np.float64],
    sample_rate: float,
    freq_range: tuple[float, float] = (0.8, 2.0),
    num_freqs: int = 64,
    w: float = 6.0,
) -> CWTResult:
    """Continuous wavelet transform using a Morlet wavelet.

    Parameters
    ----------
    signal : 1-D array
        Input time-series.
    sample_rate : float
        Sampling rate in Hz.
    freq_range : (lo, hi)
        Frequency band of interest in Hz.
    num_freqs : int
        Number of frequency bins to evaluate.
    w : float
        Morlet wavelet omega0 parameter (default 6.0, standard choice).

    Returns
    -------
    CWTResult
        Includes the coefficient matrix, frequency axis, time axis,
        and the dominant spectral peak (if any).
    """
    lo_hz, hi_hz = freq_range
    freqs = np.linspace(lo_hz, hi_hz, num_freqs)

    # Manual Morlet CWT to avoid deprecated scipy.signal.cwt/morlet2.
    # For each target frequency, build a Morlet wavelet at the appropriate
    # scale and convolve with the signal.
    n = len(signal)
    coeffs = np.empty((num_freqs, n), dtype=np.complex128)
    for i, f in enumerate(freqs):
        scale = w * sample_rate / (2.0 * np.pi * f)
        # Morlet wavelet: exp(1j*w*t/s) * exp(-t^2 / (2*s^2)) / sqrt(s)
        M = int(10 * scale)  # wavelet length (5 sigma each side)
        t_wav = np.arange(-M, M + 1, dtype=np.float64)
        norm = np.pi ** -0.25 / np.sqrt(scale)
        wavelet = norm * np.exp(1j * w * t_wav / scale) * np.exp(-t_wav ** 2 / (2 * scale ** 2))
        conv = fftconvolve(signal, np.conj(wavelet[::-1]), mode="same")
        coeffs[i, :] = conv

    time = np.arange(n) / sample_rate

    # Aggregate power per frequency (mean over time)
    mean_power = np.mean(np.abs(coeffs) ** 2, axis=1)

    peak_idx = int(np.argmax(mean_power))
    snr = _snr_db(mean_power, peak_idx)

    peak = SpectralPeak(
        frequency_hz=float(freqs[peak_idx]),
        power=float(mean_power[peak_idx]),
        snr_db=snr,
    )

    return CWTResult(
        frequencies=freqs,
        time=time,
        coefficients=coeffs,
        peak=peak,
    )


# ---------------------------------------------------------------------------
# Convenience: dominant frequency
# ---------------------------------------------------------------------------

def dominant_frequency(
    signal: NDArray[np.float64],
    sample_rate: float,
    freq_range: tuple[float, float],
    window_type: WindowType = WindowType.HANNING,
) -> Optional[SpectralPeak]:
    """Return the single strongest spectral peak in *freq_range*.

    Uses the full signal as a single FFT window.  Returns ``None`` if no
    peak is found in the band.
    """
    results = windowed_fft(
        signal,
        sample_rate,
        window_size=len(signal),
        overlap=0,
        window_type=window_type,
        freq_range=freq_range,
        max_peaks=1,
    )
    if results and results[0].peaks:
        return results[0].peaks[0]
    return None


# ---------------------------------------------------------------------------
# Unit conversion helpers
# ---------------------------------------------------------------------------

def frequency_to_bpm(freq_hz: float) -> float:
    """Convert a frequency in Hz to beats/breaths per minute.

    Parameters
    ----------
    freq_hz : float
        Frequency in Hz (e.g. 0.25 Hz breathing, 1.2 Hz heart rate).

    Returns
    -------
    bpm : float
        Equivalent rate in beats (or breaths) per minute.
    """
    return freq_hz * 60.0


def bpm_to_frequency(bpm: float) -> float:
    """Convert beats/breaths per minute to frequency in Hz.

    Parameters
    ----------
    bpm : float
        Rate in beats (or breaths) per minute.

    Returns
    -------
    freq_hz : float
        Equivalent frequency in Hz.
    """
    return bpm / 60.0


def compute_snr(
    power_spectrum: NDArray[np.float64],
    peak_idx: int,
    guard_bins: int = 2,
) -> float:
    """Compute signal-to-noise ratio in dB for a given spectral peak.

    This is the public interface to the SNR calculation.  Peak power is the
    value at *peak_idx*; noise floor is the median of all bins excluding
    the peak +/- *guard_bins*.

    Parameters
    ----------
    power_spectrum : 1-D array
        Power spectral density values.
    peak_idx : int
        Index of the spectral peak bin.
    guard_bins : int
        Number of bins on each side of the peak to exclude from
        the noise floor estimate.

    Returns
    -------
    snr : float
        Signal-to-noise ratio in dB.
    """
    return _snr_db(power_spectrum, peak_idx, guard_bins)
