"""Tests for vitals/windowed_fft.py — known-frequency sinusoid verification."""

import numpy as np
import pytest

from backend.vitals.windowed_fft import (
    WindowType,
    windowed_fft,
    morlet_cwt,
    dominant_frequency,
    compute_snr,
)


SAMPLE_RATE = 100.0  # Hz


def make_sinusoid(freq: float, duration: float = 10.0, amplitude: float = 1.0) -> np.ndarray:
    t = np.arange(0, duration, 1.0 / SAMPLE_RATE)
    return amplitude * np.sin(2 * np.pi * freq * t)


def make_multi_sinusoid(freqs: list, amplitudes: list, duration: float = 10.0) -> np.ndarray:
    t = np.arange(0, duration, 1.0 / SAMPLE_RATE)
    signal = np.zeros_like(t)
    for f, a in zip(freqs, amplitudes):
        signal += a * np.sin(2 * np.pi * f * t)
    return signal


class TestWindowedFFT:
    def test_single_frequency_detection(self):
        """FFT should find a 5 Hz sinusoid."""
        signal = make_sinusoid(5.0)
        results = windowed_fft(signal, SAMPLE_RATE, window_size=len(signal),
                               freq_range=(1.0, 20.0))
        assert len(results) == 1
        assert len(results[0].peaks) >= 1
        assert abs(results[0].peaks[0].frequency_hz - 5.0) < 0.5

    def test_windowed_with_overlap(self):
        """Windowed FFT with overlap should still find the peak."""
        signal = make_sinusoid(3.0, duration=20.0)
        results = windowed_fft(signal, SAMPLE_RATE, window_size=512,
                               overlap=256, freq_range=(1.0, 10.0))
        assert len(results) > 1
        # Each window should find the 3 Hz peak
        for r in results:
            assert len(r.peaks) >= 1
            assert abs(r.peaks[0].frequency_hz - 3.0) < 1.0

    def test_hamming_window(self):
        """Hamming window should also work."""
        signal = make_sinusoid(7.0)
        results = windowed_fft(signal, SAMPLE_RATE, window_size=len(signal),
                               window_type=WindowType.HAMMING,
                               freq_range=(1.0, 20.0))
        assert len(results[0].peaks) >= 1
        assert abs(results[0].peaks[0].frequency_hz - 7.0) < 0.5

    def test_overlap_too_large_raises(self):
        signal = make_sinusoid(1.0)
        with pytest.raises(ValueError, match="overlap"):
            windowed_fft(signal, SAMPLE_RATE, window_size=100, overlap=100)

    def test_empty_result_for_short_signal(self):
        """Signal shorter than window_size should return empty list."""
        signal = make_sinusoid(5.0, duration=0.5)
        results = windowed_fft(signal, SAMPLE_RATE, window_size=1000)
        assert results == []


class TestMorletCWT:
    def test_breathing_frequency(self):
        """CWT should detect a 0.3 Hz breathing signal."""
        signal = make_sinusoid(0.3, duration=30.0)
        result = morlet_cwt(signal, SAMPLE_RATE, freq_range=(0.1, 0.5))
        assert result.peak is not None
        assert abs(result.peak.frequency_hz - 0.3) < 0.05

    def test_heartrate_frequency(self):
        """CWT should detect a 1.2 Hz heart rate signal."""
        signal = make_sinusoid(1.2, duration=30.0)
        result = morlet_cwt(signal, SAMPLE_RATE, freq_range=(0.8, 2.0))
        assert result.peak is not None
        assert abs(result.peak.frequency_hz - 1.2) < 0.1

    def test_result_has_time_axis(self):
        signal = make_sinusoid(1.0, duration=5.0)
        result = morlet_cwt(signal, SAMPLE_RATE, freq_range=(0.5, 2.0))
        assert len(result.time) == len(signal)
        assert result.coefficients.shape[1] == len(signal)


class TestDominantFrequency:
    def test_finds_dominant_peak(self):
        signal = make_sinusoid(4.0)
        peak = dominant_frequency(signal, SAMPLE_RATE, freq_range=(1.0, 10.0))
        assert peak is not None
        assert abs(peak.frequency_hz - 4.0) < 0.5

    def test_snr_is_positive_for_clean_signal(self):
        signal = make_sinusoid(4.0)
        peak = dominant_frequency(signal, SAMPLE_RATE, freq_range=(1.0, 10.0))
        assert peak is not None
        assert peak.snr_db > 10.0

    def test_freq_band_filtering(self):
        """Peaks outside the specified band should be excluded."""
        signal = make_multi_sinusoid([2.0, 8.0], [1.0, 1.0])
        peak = dominant_frequency(signal, SAMPLE_RATE, freq_range=(5.0, 15.0))
        assert peak is not None
        assert peak.frequency_hz >= 5.0

    def test_no_peaks_returns_none(self):
        signal = np.zeros(1000)
        peak = dominant_frequency(signal, SAMPLE_RATE, freq_range=(1.0, 10.0))
        assert peak is None


class TestSNR:
    def test_high_snr_for_clean_tone(self):
        signal = make_sinusoid(5.0, duration=10.0)
        results = windowed_fft(signal, SAMPLE_RATE, window_size=len(signal),
                               freq_range=(1.0, 20.0))
        peak = results[0].peaks[0]
        assert peak.snr_db > 20.0

    def test_low_snr_for_noisy_signal(self):
        np.random.seed(42)
        noise = np.random.randn(1000)
        freqs = np.fft.rfftfreq(1000, d=1.0 / SAMPLE_RATE)
        power = np.abs(np.fft.rfft(noise)) ** 2
        snr = compute_snr(power, len(power) // 2)
        assert snr < 10.0
