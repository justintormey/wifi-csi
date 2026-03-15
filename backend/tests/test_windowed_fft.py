"""Tests for backend.vitals.windowed_fft — synthetic sinusoid verification."""

import numpy as np
import pytest

from backend.vitals.windowed_fft import (
    FFTResult,
    SpectralPeak,
    WindowType,
    dominant_frequency,
    morlet_cwt,
    windowed_fft,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sine(freq_hz: float, duration_s: float, sample_rate: float,
          amplitude: float = 1.0) -> np.ndarray:
    t = np.arange(int(duration_s * sample_rate)) / sample_rate
    return amplitude * np.sin(2 * np.pi * freq_hz * t)


# ---------------------------------------------------------------------------
# windowed_fft
# ---------------------------------------------------------------------------

class TestWindowedFFT:
    def test_single_sinusoid_detected(self):
        """A pure 1 Hz sine at 100 Hz sample rate should produce a peak at 1 Hz."""
        fs = 100.0
        sig = _sine(1.0, 10.0, fs)
        results = windowed_fft(sig, fs, window_size=len(sig), freq_range=(0.5, 5.0))
        assert len(results) == 1
        peak = results[0].peaks[0]
        assert abs(peak.frequency_hz - 1.0) < 0.15

    def test_two_sinusoids(self):
        """Two frequencies should both appear as peaks."""
        fs = 100.0
        sig = _sine(2.0, 10.0, fs) + 0.5 * _sine(4.0, 10.0, fs)
        results = windowed_fft(sig, fs, window_size=len(sig),
                               freq_range=(1.0, 6.0), max_peaks=3)
        freqs_found = sorted(p.frequency_hz for p in results[0].peaks)
        assert any(abs(f - 2.0) < 0.15 for f in freqs_found)
        assert any(abs(f - 4.0) < 0.15 for f in freqs_found)

    def test_overlap_produces_multiple_windows(self):
        fs = 100.0
        sig = _sine(1.0, 10.0, fs)
        results = windowed_fft(sig, fs, window_size=500, overlap=250)
        assert len(results) >= 3  # 1000 samples, step=250 -> 3 windows

    def test_hamming_window(self):
        fs = 100.0
        sig = _sine(1.0, 10.0, fs)
        results = windowed_fft(sig, fs, window_size=len(sig),
                               window_type=WindowType.HAMMING,
                               freq_range=(0.5, 5.0))
        assert abs(results[0].peaks[0].frequency_hz - 1.0) < 0.15

    def test_bad_overlap_raises(self):
        with pytest.raises(ValueError):
            windowed_fft(np.zeros(100), 100.0, window_size=50, overlap=50)

    def test_snr_positive_for_strong_signal(self):
        fs = 100.0
        sig = _sine(2.0, 10.0, fs)
        results = windowed_fft(sig, fs, window_size=len(sig),
                               freq_range=(1.0, 5.0))
        assert results[0].peaks[0].snr_db > 10.0

    def test_freq_range_filters_correctly(self):
        """A 10 Hz signal should NOT produce peaks in a 1-4 Hz band."""
        fs = 100.0
        sig = _sine(10.0, 5.0, fs)
        results = windowed_fft(sig, fs, window_size=len(sig),
                               freq_range=(1.0, 4.0))
        # Any leakage peaks should have negligible power vs the true peak
        for p in results[0].peaks:
            assert p.power < 1e-4


# ---------------------------------------------------------------------------
# morlet_cwt
# ---------------------------------------------------------------------------

class TestMorletCWT:
    def test_single_frequency_detected(self):
        """CWT should find a 1.2 Hz signal in the 0.8-2.0 Hz band."""
        fs = 100.0
        sig = _sine(1.2, 30.0, fs)
        result = morlet_cwt(sig, fs, freq_range=(0.8, 2.0), num_freqs=64)
        assert result.peak is not None
        assert abs(result.peak.frequency_hz - 1.2) < 0.1

    def test_coefficient_shape(self):
        fs = 100.0
        sig = _sine(1.0, 10.0, fs)
        result = morlet_cwt(sig, fs, freq_range=(0.8, 2.0), num_freqs=32)
        assert result.coefficients.shape == (32, len(sig))
        assert len(result.time) == len(sig)
        assert len(result.frequencies) == 32

    def test_snr_positive(self):
        fs = 100.0
        sig = _sine(1.5, 30.0, fs)
        result = morlet_cwt(sig, fs, freq_range=(0.8, 2.0))
        assert result.peak.snr_db > 5.0


# ---------------------------------------------------------------------------
# dominant_frequency
# ---------------------------------------------------------------------------

class TestDominantFrequency:
    def test_breathing_band(self):
        """Detect 0.25 Hz (15 breaths/min) in the breathing band."""
        fs = 100.0
        sig = _sine(0.25, 30.0, fs)
        peak = dominant_frequency(sig, fs, freq_range=(0.1, 0.5))
        assert peak is not None
        assert abs(peak.frequency_hz - 0.25) < 0.05

    def test_no_signal_in_band(self):
        """White noise shouldn't reliably produce a dominant peak with high SNR."""
        rng = np.random.default_rng(42)
        sig = rng.normal(size=3000)
        peak = dominant_frequency(sig, 100.0, freq_range=(0.1, 0.5))
        # Peak may exist but SNR should be low
        if peak is not None:
            assert peak.snr_db < 15.0
