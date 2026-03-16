"""Tests for amplitude_filter module — bandpass and Hampel filters."""

import numpy as np
import pytest

from backend.processor.amplitude_filter import (
    BAND_BREATHING,
    BAND_HEARTRATE,
    BAND_MOVEMENT,
    bandpass_breathing,
    bandpass_heartrate,
    bandpass_movement,
    butterworth_bandpass,
    filter_breathing,
    filter_heartrate,
    filter_pipeline,
    hampel_filter,
    hampel_filter_2d,
)


# ── Helpers ──────────────────────────────────────────────────────────────


def _make_sinusoid(freq: float, fs: float, duration: float) -> np.ndarray:
    """Generate a pure sinusoid at the given frequency."""
    t = np.arange(0, duration, 1.0 / fs)
    return np.sin(2 * np.pi * freq * t)


def _dominant_freq(signal: np.ndarray, fs: float) -> float:
    """Return the dominant frequency via FFT."""
    n = len(signal)
    fft_mag = np.abs(np.fft.rfft(signal))
    freqs = np.fft.rfftfreq(n, 1.0 / fs)
    # Ignore DC
    fft_mag[0] = 0
    return freqs[np.argmax(fft_mag)]


# ── Butterworth bandpass tests ───────────────────────────────────────────


class TestButterworthBandpass:
    """Test that bandpass correctly isolates target frequencies."""

    def test_passes_in_band_signal(self):
        """A 1 Hz signal should pass through a 0.5-5 Hz bandpass."""
        fs = 100.0
        sig = _make_sinusoid(1.0, fs, 30.0)
        filtered = butterworth_bandpass(sig, fs, 0.5, 5.0)
        # Signal should be preserved (high correlation)
        corr = np.corrcoef(sig[500:-500], filtered[500:-500])[0, 1]
        assert corr > 0.95

    def test_rejects_out_of_band_signal(self):
        """A 10 Hz signal should be attenuated by a 0.5-5 Hz bandpass."""
        fs = 100.0
        sig = _make_sinusoid(10.0, fs, 30.0)
        filtered = butterworth_bandpass(sig, fs, 0.5, 5.0)
        # Power should be massively reduced
        power_ratio = np.var(filtered[500:-500]) / np.var(sig[500:-500])
        assert power_ratio < 0.01

    def test_mixed_signal_isolates_target(self):
        """From a mix of 0.3Hz + 2Hz + 8Hz, bandpass 0.5-5Hz should isolate 2Hz."""
        fs = 100.0
        duration = 60.0
        t = np.arange(0, duration, 1.0 / fs)
        mixed = (
            np.sin(2 * np.pi * 0.3 * t)  # below band
            + np.sin(2 * np.pi * 2.0 * t)  # in band
            + np.sin(2 * np.pi * 8.0 * t)  # above band
        )
        filtered = butterworth_bandpass(mixed, fs, 0.5, 5.0)
        dominant = _dominant_freq(filtered[1000:-1000], fs)
        assert abs(dominant - 2.0) < 0.1

    def test_2d_input_filters_columns(self):
        """Each column of a 2-D array should be filtered independently."""
        fs = 100.0
        duration = 30.0
        sig1 = _make_sinusoid(1.0, fs, duration)
        sig2 = _make_sinusoid(2.0, fs, duration)
        data = np.column_stack([sig1, sig2])
        filtered = butterworth_bandpass(data, fs, 0.5, 5.0, order=3)
        assert filtered.shape == data.shape
        # Both signals should survive the bandpass
        for col in range(2):
            corr = np.corrcoef(data[500:-500, col], filtered[500:-500, col])[0, 1]
            assert corr > 0.90

    def test_invalid_band_raises(self):
        """Invalid cutoff frequencies should raise ValueError."""
        sig = np.zeros(1000)
        with pytest.raises(ValueError, match="Invalid band"):
            butterworth_bandpass(sig, 100.0, 5.0, 0.5)  # low > high
        with pytest.raises(ValueError, match="Invalid band"):
            butterworth_bandpass(sig, 100.0, -1.0, 5.0)  # negative
        with pytest.raises(ValueError, match="Nyquist"):
            butterworth_bandpass(sig, 100.0, 0.5, 50.0)  # high == Nyquist


# ── Convenience band functions ───────────────────────────────────────────


class TestConvenienceBands:
    """Test the named bandpass helpers pass the correct bands."""

    def test_movement_band_passes_2hz(self):
        fs = 100.0
        sig = _make_sinusoid(2.0, fs, 30.0)
        filtered = bandpass_movement(sig, fs)
        corr = np.corrcoef(sig[500:-500], filtered[500:-500])[0, 1]
        assert corr > 0.95

    def test_breathing_band_passes_0_3hz(self):
        fs = 10.0
        sig = _make_sinusoid(0.3, fs, 120.0)
        filtered = bandpass_breathing(sig, fs)
        dominant = _dominant_freq(filtered[200:-200], fs)
        assert abs(dominant - 0.3) < 0.05

    def test_heartrate_band_passes_1_2hz(self):
        fs = 100.0
        sig = _make_sinusoid(1.2, fs, 60.0)
        filtered = bandpass_heartrate(sig, fs)
        dominant = _dominant_freq(filtered[500:-500], fs)
        assert abs(dominant - 1.2) < 0.1


# ── Hampel filter tests ─────────────────────────────────────────────────


class TestHampelFilter:
    """Test outlier detection and replacement."""

    def test_no_outliers_unchanged(self):
        """Clean data should pass through unchanged."""
        rng = np.random.default_rng(42)
        data = rng.normal(0, 1, 200)
        filtered, mask = hampel_filter(data, window_size=5, n_sigmas=5.0)
        # With high threshold, very few (if any) flags
        assert mask.sum() < 5
        # Filtered values that weren't flagged should be identical
        np.testing.assert_array_equal(filtered[~mask], data[~mask])

    def test_detects_injected_spikes(self):
        """Injected large spikes should be detected and replaced."""
        rng = np.random.default_rng(42)
        data = rng.normal(0, 1, 500)
        spike_indices = [50, 150, 300, 450]
        for idx in spike_indices:
            data[idx] = 50.0  # huge spike

        filtered, mask = hampel_filter(data, window_size=7, n_sigmas=3.0)

        # All spike positions should be flagged
        for idx in spike_indices:
            assert mask[idx], f"Spike at index {idx} not detected"

        # Replaced values should be close to 0 (the data mean)
        for idx in spike_indices:
            assert abs(filtered[idx]) < 5.0

    def test_output_shape_matches_input(self):
        data = np.arange(100, dtype=float)
        filtered, mask = hampel_filter(data)
        assert filtered.shape == data.shape
        assert mask.shape == data.shape

    def test_rejects_2d_input(self):
        with pytest.raises(ValueError, match="1-D"):
            hampel_filter(np.zeros((10, 3)))


class TestHampelFilter2D:
    """Test 2-D Hampel convenience wrapper."""

    def test_filters_each_column(self):
        # Linearly spaced data — no outliers possible without injection
        data = np.tile(np.linspace(0, 1, 200), (3, 1)).T  # shape (200, 3)
        # Inject spike in column 1 only
        data[100, 1] = 500.0

        filtered, mask = hampel_filter_2d(data, window_size=7, n_sigmas=3.0)

        assert filtered.shape == data.shape
        assert mask[100, 1], "Spike in col 1 not detected"
        # No outliers in other columns (smooth linear data)
        assert mask[:, 0].sum() == 0
        assert mask[:, 2].sum() == 0

    def test_rejects_1d_input(self):
        with pytest.raises(ValueError, match="2-D"):
            hampel_filter_2d(np.zeros(100))


# ── Filter pipeline tests ─────────────────────────────────────────────


class TestFilterPipeline:
    """Test the combined Hampel + bandpass pipeline."""

    def test_1d_pipeline_removes_spikes_and_filters(self):
        """Pipeline should remove outliers then bandpass-filter."""
        fs = 100.0
        duration = 30.0
        t = np.arange(0, duration, 1.0 / fs)
        # 0.3 Hz breathing signal + injected spikes
        sig = np.sin(2 * np.pi * 0.3 * t)
        sig[500] = 50.0
        sig[1500] = -50.0

        result = filter_pipeline(sig, fs, 0.1, 0.5)

        # Spikes should be gone, signal shape preserved
        assert np.max(np.abs(result)) < 2.0
        dominant = _dominant_freq(result[200:-200], fs)
        assert abs(dominant - 0.3) < 0.05

    def test_2d_pipeline(self):
        """Pipeline should handle 2-D input (Hampel per-column, then bandpass)."""
        fs = 100.0
        duration = 30.0
        t = np.arange(0, duration, 1.0 / fs)
        col1 = np.sin(2 * np.pi * 1.0 * t)
        col2 = np.sin(2 * np.pi * 2.0 * t)
        data = np.column_stack([col1, col2])
        # Inject spike in col 0
        data[300, 0] = 100.0

        result = filter_pipeline(data, fs, 0.5, 5.0)

        assert result.shape == data.shape
        assert np.max(np.abs(result)) < 3.0

    def test_pipeline_rejects_3d(self):
        """3-D input should raise ValueError."""
        with pytest.raises(ValueError):
            filter_pipeline(np.zeros((10, 3, 2)), 100.0, 0.5, 5.0)


class TestFilterBreathing:
    """Test the full breathing pipeline (Hampel + breathing band)."""

    def test_isolates_breathing_frequency(self):
        """A 0.3 Hz signal with spikes should come through clean."""
        fs = 100.0
        duration = 60.0
        t = np.arange(0, duration, 1.0 / fs)
        sig = np.sin(2 * np.pi * 0.3 * t)
        sig[1000] = 40.0  # spike

        result = filter_breathing(sig, fs)

        dominant = _dominant_freq(result[500:-500], fs)
        assert abs(dominant - 0.3) < 0.05
        assert np.max(np.abs(result)) < 2.0

    def test_2d_breathing(self):
        """2-D input should work for multi-subcarrier breathing extraction."""
        fs = 100.0
        t = np.arange(0, 60.0, 1.0 / fs)
        data = np.column_stack([
            np.sin(2 * np.pi * 0.25 * t),
            np.sin(2 * np.pi * 0.35 * t),
        ])
        result = filter_breathing(data, fs)
        assert result.shape == data.shape


class TestFilterHeartrate:
    """Test the full heartrate pipeline (Hampel + heartrate band)."""

    def test_isolates_heartrate_frequency(self):
        """A 1.2 Hz signal with spikes should come through clean."""
        fs = 100.0
        duration = 60.0
        t = np.arange(0, duration, 1.0 / fs)
        sig = np.sin(2 * np.pi * 1.2 * t)
        sig[800] = 30.0  # spike

        result = filter_heartrate(sig, fs)

        dominant = _dominant_freq(result[500:-500], fs)
        assert abs(dominant - 1.2) < 0.1
        assert np.max(np.abs(result)) < 2.0

    def test_2d_heartrate(self):
        """2-D input should work for multi-subcarrier heartrate extraction."""
        fs = 100.0
        t = np.arange(0, 60.0, 1.0 / fs)
        data = np.column_stack([
            np.sin(2 * np.pi * 1.0 * t),
            np.sin(2 * np.pi * 1.5 * t),
        ])
        result = filter_heartrate(data, fs)
        assert result.shape == data.shape
