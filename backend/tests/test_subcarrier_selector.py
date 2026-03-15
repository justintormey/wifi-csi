"""Tests for subcarrier_selector module — top-K selection by variance."""

import numpy as np
import pytest

from backend.processor.subcarrier_selector import (
    DEFAULT_K,
    DEFAULT_WINDOW_SAMPLES,
    SubcarrierSelection,
    compute_variance,
    select_top_k,
)


# ── Helpers ──────────────────────────────────────────────────────────────


def _make_amplitude_data(
    n_samples: int = 200,
    n_subcarriers: int = 114,
    seed: int = 42,
) -> tuple[np.ndarray, np.ndarray]:
    """Create synthetic amplitude data with known variance distribution.

    Returns (amplitudes, expected_variances) where each subcarrier has
    well-separated variance. We use exponentially spaced stds so that
    even with finite samples, the ranking is unambiguous.
    """
    rng = np.random.default_rng(seed)
    # Exponential spacing ensures large gaps between neighboring variances,
    # making the ranking robust to sampling noise.
    stds = np.exp(np.linspace(0, 5, n_subcarriers))
    amplitudes = rng.normal(loc=50.0, scale=stds, size=(n_samples, n_subcarriers))
    expected_variances = stds**2
    return amplitudes, expected_variances


# ── compute_variance tests ───────────────────────────────────────────────


class TestComputeVariance:
    """Test per-subcarrier variance computation."""

    def test_basic_variance(self):
        """Variance of known data should match numpy reference."""
        rng = np.random.default_rng(99)
        data = rng.normal(0, 5, (200, 10))
        result = compute_variance(data, window_samples=200)
        expected = np.var(data, axis=0, ddof=0)
        np.testing.assert_allclose(result, expected)

    def test_window_uses_tail(self):
        """Only the last window_samples are used."""
        # First 100 samples: low variance; last 100: high variance
        data = np.zeros((200, 5))
        data[:100, :] = 0.01 * np.random.default_rng(1).normal(size=(100, 5))
        data[100:, :] = 10.0 * np.random.default_rng(2).normal(size=(100, 5))

        var_tail = compute_variance(data, window_samples=100)
        var_all = compute_variance(data, window_samples=200)

        # Tail-only variance should be much higher than full-range
        assert np.all(var_tail > var_all * 0.5)

    def test_short_data_uses_all(self):
        """When T < window_samples, all samples are used."""
        data = np.random.default_rng(7).normal(size=(50, 10))
        result = compute_variance(data, window_samples=200)
        expected = np.var(data, axis=0, ddof=0)
        np.testing.assert_allclose(result, expected)

    def test_rejects_1d(self):
        with pytest.raises(ValueError, match="2-D"):
            compute_variance(np.zeros(100))

    def test_rejects_3d(self):
        with pytest.raises(ValueError, match="2-D"):
            compute_variance(np.zeros((10, 10, 10)))


# ── select_top_k tests ──────────────────────────────────────────────────


class TestSelectTopK:
    """Test top-K subcarrier selection."""

    def test_selects_highest_variance_subcarriers(self):
        """With known variance ordering, top-K should pick the noisiest."""
        amplitudes, _ = _make_amplitude_data(n_samples=500, seed=123)
        result = select_top_k(amplitudes, k=30, window_samples=500)

        assert isinstance(result, SubcarrierSelection)
        assert len(result.indices) == 30
        assert result.data.shape == (500, 30)
        assert len(result.scores) == 30

        # The top-30 should be subcarriers 84..113 (highest variance)
        expected_top = set(range(84, 114))
        actual_top = set(result.indices.tolist())
        assert actual_top == expected_top

    def test_scores_descending(self):
        """Scores should be in descending order."""
        amplitudes, _ = _make_amplitude_data(n_samples=300)
        result = select_top_k(amplitudes, k=20, window_samples=300)
        assert np.all(result.scores[:-1] >= result.scores[1:])

    def test_k_equals_n(self):
        """K = N should return all subcarriers."""
        data = np.random.default_rng(5).normal(size=(100, 10))
        result = select_top_k(data, k=10)
        assert len(result.indices) == 10
        assert result.data.shape == (100, 10)

    def test_k_equals_1(self):
        """K = 1 should return the single highest-variance subcarrier."""
        amplitudes, _ = _make_amplitude_data(n_samples=300)
        result = select_top_k(amplitudes, k=1, window_samples=300)
        assert len(result.indices) == 1
        # Subcarrier 113 has the highest variance
        assert result.indices[0] == 113

    def test_default_k(self):
        """Default K should be 30."""
        assert DEFAULT_K == 30

    def test_default_window(self):
        """Default window should be 100 samples (1s at 100Hz)."""
        assert DEFAULT_WINDOW_SAMPLES == 100

    def test_invalid_k_zero(self):
        data = np.zeros((100, 10))
        with pytest.raises(ValueError, match="k must be"):
            select_top_k(data, k=0)

    def test_invalid_k_too_large(self):
        data = np.zeros((100, 10))
        with pytest.raises(ValueError, match="k must be"):
            select_top_k(data, k=11)

    def test_rejects_1d(self):
        with pytest.raises(ValueError, match="2-D"):
            select_top_k(np.zeros(100), k=5)

    def test_data_columns_match_indices(self):
        """Returned data columns should correspond to selected indices."""
        amplitudes, _ = _make_amplitude_data(n_samples=200)
        result = select_top_k(amplitudes, k=15, window_samples=200)

        for i, idx in enumerate(result.indices):
            np.testing.assert_array_equal(result.data[:, i], amplitudes[:, idx])


# ── SNR weighting tests ─────────────────────────────────────────────────


class TestSNRWeighting:
    """Test optional SNR-weighted selection."""

    def test_snr_changes_selection(self):
        """High SNR should promote a low-variance subcarrier."""
        rng = np.random.default_rng(77)
        n_sub = 20
        # All subcarriers have similar variance
        data = rng.normal(0, 1, (300, n_sub))
        # Make subcarrier 0 slightly lower variance
        data[:, 0] *= 0.5

        # Without SNR: subcarrier 0 should NOT be in top-5
        result_no_snr = select_top_k(data, k=5, window_samples=300)

        # With SNR: give subcarrier 0 a massive SNR boost
        snr = np.ones(n_sub)
        snr[0] = 100.0
        result_snr = select_top_k(data, k=5, window_samples=300, snr=snr)

        assert 0 not in result_no_snr.indices
        assert 0 in result_snr.indices

    def test_snr_wrong_shape_raises(self):
        data = np.zeros((100, 10))
        with pytest.raises(ValueError, match="snr shape"):
            select_top_k(data, k=5, snr=np.ones(5))

    def test_snr_scores_are_weighted(self):
        """Scores should equal variance * snr when SNR is provided."""
        rng = np.random.default_rng(33)
        data = rng.normal(0, 1, (200, 10))
        snr = rng.uniform(0.5, 2.0, 10)

        result = select_top_k(data, k=10, window_samples=200, snr=snr)
        variances = np.var(data, axis=0, ddof=0)
        expected_scores = variances * snr

        # All subcarriers selected, scores should match
        for i, idx in enumerate(result.indices):
            np.testing.assert_allclose(result.scores[i], expected_scores[idx], rtol=1e-10)
