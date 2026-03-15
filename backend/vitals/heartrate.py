"""Heart rate extraction from CSI amplitude data (experimental).

Extracts heart rate by removing breathing harmonics, bandpass-filtering
in the 0.8–2.0 Hz band (48–120 bpm), and running CWT (Morlet wavelet)
peak detection.

Heart rate from WiFi CSI is inherently noisy (~0.1 mm chest displacement
vs ~5 mm for breathing).  The module enforces strict **display gating**:
the result's ``display`` flag is True only when ALL conditions are met:

1. ``position_confidence > 0.6``
2. ``is_stationary`` for > 30 continuous seconds
3. In-band SNR exceeds a configurable threshold

When any gate fails, ``display=False`` and ``rate_bpm=None``.

Expected accuracy: ±8–10 bpm, ~50–60% usable readings.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
from numpy.typing import NDArray

from backend.processor.amplitude_filter import butterworth_bandpass, BAND_HEARTRATE
from backend.processor.subcarrier_selector import select_top_k
from backend.vitals.windowed_fft import (
    morlet_cwt,
    frequency_to_bpm,
)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HeartRateResult:
    """Output of a heart rate estimation step.

    When any gating condition fails, ``display`` is False and
    ``rate_bpm`` is None.  The ``confidence`` and ``snr_db`` fields
    are always populated when estimation succeeds (even if gated).
    """

    rate_bpm: Optional[float]   # estimated heart rate, or None if gated
    confidence: float           # [0, 1] — derived from CWT peak SNR
    snr_db: float               # raw signal-to-noise ratio in dB
    display: bool               # True only when ALL gates pass


# ---------------------------------------------------------------------------
# Configurable defaults
# ---------------------------------------------------------------------------

DEFAULT_SAMPLE_RATE: float = 100.0          # Hz — CSI sample rate
DEFAULT_WINDOW_SECONDS: float = 30.0        # seconds of data per CWT window
DEFAULT_TOP_K: int = 10                     # subcarriers to average
DEFAULT_MIN_BPM: float = 40.0              # reject rates below this
DEFAULT_MAX_BPM: float = 120.0             # reject rates above this
DEFAULT_MIN_SNR_DB: float = 3.0            # minimum SNR for display gate
DEFAULT_SNR_SATURATION_DB: float = 15.0    # SNR at which confidence = 1.0
DEFAULT_FILTER_ORDER: int = 4              # Butterworth bandpass order
DEFAULT_MIN_SNAPSHOTS: int = 500           # minimum snapshots before estimating
DEFAULT_CWT_NUM_FREQS: int = 64            # frequency bins in CWT
DEFAULT_CWT_W: float = 6.0                # Morlet omega0 parameter
DEFAULT_POSITION_CONFIDENCE_THRESHOLD: float = 0.6
DEFAULT_STATIONARY_SECONDS_THRESHOLD: float = 30.0
DEFAULT_BREATHING_HARMONICS: int = 3       # number of breathing harmonics to remove


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
# Breathing harmonic removal
# ---------------------------------------------------------------------------

def _remove_breathing_harmonics(
    signal: NDArray[np.float64],
    sample_rate: float,
    breathing_freq_hz: Optional[float] = None,
    n_harmonics: int = DEFAULT_BREATHING_HARMONICS,
    notch_width_hz: float = 0.05,
) -> NDArray[np.float64]:
    """Remove breathing frequency and its harmonics from the signal.

    If ``breathing_freq_hz`` is None, estimates it from the signal's
    dominant low-frequency component (0.1–0.5 Hz).

    Uses spectral notching in the frequency domain: zero out bins
    within ``notch_width_hz`` of each harmonic.
    """
    n = len(signal)
    if n < 4:
        return signal.copy()

    freqs = np.fft.rfftfreq(n, d=1.0 / sample_rate)
    spectrum = np.fft.rfft(signal)

    # Estimate breathing frequency if not provided
    if breathing_freq_hz is None:
        breath_mask = (freqs >= 0.1) & (freqs <= 0.5)
        if not breath_mask.any():
            return signal.copy()
        power = np.abs(spectrum) ** 2
        breath_power = power.copy()
        breath_power[~breath_mask] = 0.0
        peak_idx = int(np.argmax(breath_power))

        # Only proceed if the breathing peak is significantly above the
        # median in-band power — avoids notching out heartbeat harmonics
        # when no real breathing signal exists.
        band_median = float(np.median(power[breath_mask]))
        if band_median <= 0 or breath_power[peak_idx] < 5.0 * band_median:
            return signal.copy()

        breathing_freq_hz = float(freqs[peak_idx])

    if breathing_freq_hz <= 0:
        return signal.copy()

    # Notch out each harmonic
    for h in range(1, n_harmonics + 1):
        harmonic_freq = breathing_freq_hz * h
        notch_mask = (freqs >= harmonic_freq - notch_width_hz) & \
                     (freqs <= harmonic_freq + notch_width_hz)
        spectrum[notch_mask] = 0.0

    return np.fft.irfft(spectrum, n=n).astype(np.float64)


# ---------------------------------------------------------------------------
# Heart rate extractor (stateful)
# ---------------------------------------------------------------------------


class HeartRateExtractor:
    """Extracts heart rate from a stream of CSI amplitude snapshots.

    Uses CWT (Morlet wavelet) for better time-frequency localization
    of the weak heartbeat signal.  Breathing harmonics are removed
    before analysis.

    Args:
        sample_rate: CSI sample rate in Hz (default 100).
        window_seconds: Length of the CWT analysis window in seconds.
        top_k: Number of top-variance subcarriers to average.
        min_bpm: Minimum valid heart rate (bpm).
        max_bpm: Maximum valid heart rate (bpm).
        min_snr_db: Minimum SNR for the display gate.
        snr_saturation_db: SNR at which confidence reaches 1.0.
        filter_order: Butterworth bandpass filter order.
        min_snapshots: Minimum snapshots before estimation is possible.
        cwt_num_freqs: Number of frequency bins in CWT.
        cwt_w: Morlet wavelet omega0 parameter.
        position_confidence_threshold: Minimum position confidence for display.
        stationary_seconds_threshold: Minimum continuous stationary seconds.
        breathing_harmonics: Number of breathing harmonics to remove.
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
        filter_order: int = DEFAULT_FILTER_ORDER,
        min_snapshots: int = DEFAULT_MIN_SNAPSHOTS,
        cwt_num_freqs: int = DEFAULT_CWT_NUM_FREQS,
        cwt_w: float = DEFAULT_CWT_W,
        position_confidence_threshold: float = DEFAULT_POSITION_CONFIDENCE_THRESHOLD,
        stationary_seconds_threshold: float = DEFAULT_STATIONARY_SECONDS_THRESHOLD,
        breathing_harmonics: int = DEFAULT_BREATHING_HARMONICS,
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

        self._sample_rate = sample_rate
        self._window_samples = int(window_seconds * sample_rate)
        self._top_k = top_k
        self._min_bpm = min_bpm
        self._max_bpm = max_bpm
        self._min_snr_db = min_snr_db
        self._snr_saturation_db = snr_saturation_db
        self._filter_order = filter_order
        self._min_snapshots = min_snapshots
        self._cwt_num_freqs = cwt_num_freqs
        self._cwt_w = cwt_w
        self._position_confidence_threshold = position_confidence_threshold
        self._stationary_seconds_threshold = stationary_seconds_threshold
        self._breathing_harmonics = breathing_harmonics

        # Frequency band for CWT (derived from bpm limits)
        self._freq_lo = min_bpm / 60.0   # 40 bpm → 0.667 Hz
        self._freq_hi = max_bpm / 60.0   # 120 bpm → 2.0 Hz

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

    def estimate(
        self,
        position_confidence: float = 1.0,
        is_stationary: bool = True,
        stationary_duration_s: float = 60.0,
        breathing_freq_hz: Optional[float] = None,
    ) -> Optional[HeartRateResult]:
        """Estimate heart rate from the current buffer.

        Args:
            position_confidence: Current position confidence [0, 1].
            is_stationary: Whether the person is currently stationary.
            stationary_duration_s: How long they've been stationary (seconds).
            breathing_freq_hz: Known breathing frequency for harmonic
                removal.  If None, estimated from the signal.

        Returns:
            HeartRateResult if a valid heart rate signal is detected,
            None if CWT finds no peak in the valid band.

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

        # Bandpass filter in the heartrate band
        filtered = butterworth_bandpass(
            selected,
            self._sample_rate,
            *BAND_HEARTRATE,
            order=self._filter_order,
        )

        # Average across selected subcarriers → single waveform
        waveform = np.mean(filtered, axis=1)  # (T,)

        # Remove breathing harmonics
        cleaned = _remove_breathing_harmonics(
            waveform,
            self._sample_rate,
            breathing_freq_hz=breathing_freq_hz,
            n_harmonics=self._breathing_harmonics,
        )

        # CWT with Morlet wavelet
        cwt_result = morlet_cwt(
            cleaned,
            self._sample_rate,
            freq_range=(self._freq_lo, self._freq_hi),
            num_freqs=self._cwt_num_freqs,
            w=self._cwt_w,
        )

        if cwt_result.peak is None:
            return None

        peak = cwt_result.peak
        bpm = frequency_to_bpm(peak.frequency_hz)

        # Validate range
        if bpm < self._min_bpm or bpm > self._max_bpm:
            return None

        snr_db = peak.snr_db
        confidence = _snr_to_confidence(
            snr_db, self._min_snr_db, self._snr_saturation_db,
        )

        # Gating: ALL conditions must be true for display
        gates_pass = (
            position_confidence > self._position_confidence_threshold
            and is_stationary
            and stationary_duration_s > self._stationary_seconds_threshold
            and snr_db >= self._min_snr_db
        )

        if gates_pass:
            return HeartRateResult(
                rate_bpm=round(bpm, 1),
                confidence=round(confidence, 3),
                snr_db=round(snr_db, 1),
                display=True,
            )
        else:
            return HeartRateResult(
                rate_bpm=None,
                confidence=round(confidence, 3),
                snr_db=round(snr_db, 1),
                display=False,
            )

    def update(
        self,
        amplitude: NDArray[np.float64],
        position_confidence: float = 1.0,
        is_stationary: bool = True,
        stationary_duration_s: float = 60.0,
        breathing_freq_hz: Optional[float] = None,
    ) -> Optional[HeartRateResult]:
        """Convenience: push a snapshot and estimate if ready.

        Returns HeartRateResult if enough data, None otherwise.
        """
        self.push(amplitude)
        if self.is_ready:
            return self.estimate(
                position_confidence=position_confidence,
                is_stationary=is_stationary,
                stationary_duration_s=stationary_duration_s,
                breathing_freq_hz=breathing_freq_hz,
            )
        return None

    def reset(self) -> None:
        """Clear the buffer and reset all state."""
        self._buffer.clear()
        self._n_subcarriers = None
