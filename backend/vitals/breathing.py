"""Breathing rate extraction from CSI amplitude data.

Extracts respiratory rate by bandpass-filtering CSI amplitudes in the
0.1–0.5 Hz band (6–30 bpm), selecting the top-K most responsive
subcarriers, averaging them into a single breathing waveform, and
running FFT peak detection on a sliding window.

The module is stateful: push CSI amplitude snapshots one at a time and
call ``estimate`` when enough data has accumulated.  Typical usage at
100 Hz CSI rate with a 30-second window (3000 samples).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
from numpy.typing import NDArray

from backend.processor.amplitude_filter import butterworth_bandpass, BAND_BREATHING
from backend.processor.subcarrier_selector import select_top_k
from backend.vitals.windowed_fft import (
    windowed_fft,
    frequency_to_bpm,
    SpectralPeak,
)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BreathingResult:
    """Output of a breathing rate estimation step."""

    breathing_rate_bpm: float       # estimated breaths per minute
    breathing_confidence: float     # [0, 1] — derived from SNR of the FFT peak
    snr_db: float                   # raw signal-to-noise ratio in dB


# ---------------------------------------------------------------------------
# Configurable defaults
# ---------------------------------------------------------------------------

DEFAULT_SAMPLE_RATE: float = 100.0          # Hz — CSI sample rate
DEFAULT_WINDOW_SECONDS: float = 30.0        # seconds of data per FFT window
DEFAULT_TOP_K: int = 15                     # subcarriers to average
DEFAULT_MIN_BPM: float = 8.0               # reject rates below this
DEFAULT_MAX_BPM: float = 30.0              # reject rates above this
DEFAULT_MIN_SNR_DB: float = 3.0            # minimum SNR to consider valid
DEFAULT_SNR_SATURATION_DB: float = 20.0    # SNR at which confidence = 1.0
DEFAULT_MIN_CONCENTRATION: float = 0.15    # minimum spectral concentration
DEFAULT_FILTER_ORDER: int = 4              # Butterworth bandpass order
DEFAULT_MIN_SNAPSHOTS: int = 500           # minimum snapshots before estimating
                                           # (5 s at 100 Hz — need enough for
                                           # the bandpass to settle)


# ---------------------------------------------------------------------------
# Confidence mapping
# ---------------------------------------------------------------------------

def _snr_to_confidence(
    snr_db: float,
    min_snr: float = DEFAULT_MIN_SNR_DB,
    sat_snr: float = DEFAULT_SNR_SATURATION_DB,
) -> float:
    """Map SNR (dB) to a [0, 1] confidence score.

    Below *min_snr* → 0.  Above *sat_snr* → 1.  Linear ramp between.
    """
    if snr_db <= min_snr:
        return 0.0
    if snr_db >= sat_snr:
        return 1.0
    return (snr_db - min_snr) / (sat_snr - min_snr)


# ---------------------------------------------------------------------------
# Breathing extractor (stateful)
# ---------------------------------------------------------------------------


class BreathingExtractor:
    """Extracts breathing rate from a stream of CSI amplitude snapshots.

    Args:
        sample_rate: CSI sample rate in Hz (default 100).
        window_seconds: Length of the FFT analysis window in seconds.
        top_k: Number of top-variance subcarriers to average.
        min_bpm: Minimum valid breathing rate (bpm).
        max_bpm: Maximum valid breathing rate (bpm).
        min_snr_db: Minimum SNR to produce a result (below → None).
        snr_saturation_db: SNR at which confidence reaches 1.0.
        min_concentration: Minimum fraction of in-band power in the peak
            bin (+ neighbours).  Rejects flat-spectrum noise.
        filter_order: Butterworth bandpass filter order.
        min_snapshots: Minimum snapshots before estimation is possible.
    """

    def __init__(
        self,
        sample_rate: float = DEFAULT_SAMPLE_RATE,
        window_seconds: float = DEFAULT_WINDOW_SECONDS,
        top_k: int = DEFAULT_TOP_K,
        min_bpm: float = DEFAULT_MIN_BPM,
        max_bpm: float = DEFAULT_MAX_BPM,
        min_snr_db: float = DEFAULT_MIN_SNR_DB,
        snr_saturation_db: float = DEFAULT_SNR_SATURATION_DB,
        min_concentration: float = DEFAULT_MIN_CONCENTRATION,
        filter_order: int = DEFAULT_FILTER_ORDER,
        min_snapshots: int = DEFAULT_MIN_SNAPSHOTS,
    ) -> None:
        if sample_rate <= 0:
            raise ValueError("sample_rate must be > 0")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be > 0")
        if top_k < 1:
            raise ValueError("top_k must be >= 1")
        if min_bpm < 0 or max_bpm <= min_bpm:
            raise ValueError("Need 0 <= min_bpm < max_bpm")
        if min_snapshots < 2:
            raise ValueError("min_snapshots must be >= 2")
        if min_concentration >= 0.5:
            raise ValueError("min_concentration must be < 0.5 to avoid division by zero in confidence formula")

        self._sample_rate = sample_rate
        self._window_samples = int(window_seconds * sample_rate)
        self._top_k = top_k
        self._min_bpm = min_bpm
        self._max_bpm = max_bpm
        self._min_snr_db = min_snr_db
        self._snr_saturation_db = snr_saturation_db
        self._min_concentration = min_concentration
        self._filter_order = filter_order
        self._min_snapshots = min_snapshots

        # Frequency band for peak search (derived from bpm limits)
        self._freq_lo = min_bpm / 60.0     # 8 bpm → 0.133 Hz
        self._freq_hi = max_bpm / 60.0     # 30 bpm → 0.500 Hz

        # Rolling buffer: list of 1-D amplitude arrays (one per CSI snapshot)
        self._buffer: list[NDArray[np.float64]] = []
        self._n_subcarriers: int | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def buffer_size(self) -> int:
        """Number of CSI snapshots currently in the buffer."""
        return len(self._buffer)

    @property
    def is_ready(self) -> bool:
        """Whether enough snapshots have accumulated to estimate."""
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

        # Trim to window size
        if len(self._buffer) > self._window_samples:
            self._buffer = self._buffer[-self._window_samples:]

    def estimate(self) -> Optional[BreathingResult]:
        """Estimate breathing rate from the current buffer.

        Returns:
            BreathingResult if a valid breathing signal is detected,
            None if SNR is too low or the peak falls outside the valid
            bpm range.

        Raises:
            RuntimeError: If fewer than min_snapshots are in the buffer.
        """
        if not self.is_ready:
            raise RuntimeError(
                f"Need at least {self._min_snapshots} snapshots, "
                f"have {len(self._buffer)}"
            )

        # Build matrix: (time, subcarriers)
        matrix = np.vstack(self._buffer)  # (T, N)

        # Select top-K subcarriers by variance
        k = min(self._top_k, matrix.shape[1])
        selection = select_top_k(matrix, k=k)
        selected = selection.data  # (T, K)

        # Bandpass filter in the breathing band
        filtered = butterworth_bandpass(
            selected,
            self._sample_rate,
            *BAND_BREATHING,
            order=self._filter_order,
        )

        # Average across selected subcarriers → single breathing waveform
        waveform = np.mean(filtered, axis=1)  # (T,)

        # FFT with full window
        results = windowed_fft(
            waveform,
            self._sample_rate,
            window_size=len(waveform),
            freq_range=(self._freq_lo, self._freq_hi),
            max_peaks=1,
        )

        if not results or not results[0].peaks:
            return None

        peak = results[0].peaks[0]
        freqs = results[0].frequencies
        power = results[0].power_spectrum

        # Compute in-band SNR (only within the breathing band).
        # The default peak.snr_db uses the full spectrum which is misleading
        # after bandpass filtering (out-of-band bins are near-zero).
        band_mask = (freqs >= self._freq_lo) & (freqs <= self._freq_hi)
        band_indices = np.where(band_mask)[0]
        band_power = power[band_indices]

        if len(band_power) < 3:
            return None

        peak_idx_global = int(np.argmin(np.abs(freqs - peak.frequency_hz)))
        # Map to band-local index
        peak_local = int(np.argmin(np.abs(band_indices - peak_idx_global)))

        # In-band SNR: peak power vs median of non-peak band bins
        guard = 2
        band_mask_local = np.ones(len(band_power), dtype=bool)
        lo_g = max(0, peak_local - guard)
        hi_g = min(len(band_power), peak_local + guard + 1)
        band_mask_local[lo_g:hi_g] = False
        noise_bins = band_power[band_mask_local]
        noise_floor = float(np.median(noise_bins)) if len(noise_bins) > 0 else 1e-12
        if noise_floor <= 0:
            noise_floor = 1e-12
        inband_snr_db = float(10.0 * np.log10(band_power[peak_local] / noise_floor))

        if inband_snr_db < self._min_snr_db:
            return None

        # Spectral concentration: fraction of in-band power in peak ± 1 bin.
        # A real breathing signal concentrates power; noise spreads it flat.
        total_band = float(np.sum(band_power))
        if total_band <= 0:
            return None
        lo_c = max(0, peak_local - 1)
        hi_c = min(len(band_power), peak_local + 2)
        concentration = float(np.sum(band_power[lo_c:hi_c])) / total_band

        if concentration < self._min_concentration:
            return None

        # Convert to bpm and validate range
        bpm = frequency_to_bpm(peak.frequency_hz)
        if bpm < self._min_bpm or bpm > self._max_bpm:
            return None

        # Confidence combines in-band SNR and spectral concentration
        snr_conf = _snr_to_confidence(
            inband_snr_db, self._min_snr_db, self._snr_saturation_db,
        )
        conc_conf = min(1.0, max(0.0,
            (concentration - self._min_concentration)
            / (0.5 - self._min_concentration)))
        confidence = snr_conf * conc_conf

        return BreathingResult(
            breathing_rate_bpm=round(bpm, 1),
            breathing_confidence=round(confidence, 3),
            snr_db=round(inband_snr_db, 1),
        )

    def update(
        self, amplitude: NDArray[np.float64]
    ) -> Optional[BreathingResult]:
        """Convenience: push a snapshot and estimate if ready.

        Returns BreathingResult if enough data and valid signal, None otherwise.
        """
        self.push(amplitude)
        if self.is_ready:
            return self.estimate()
        return None

    def reset(self) -> None:
        """Clear the buffer and reset all state."""
        self._buffer.clear()
        self._n_subcarriers = None
