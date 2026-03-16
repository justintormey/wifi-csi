"""Tests for feature_extractor module — fingerprint feature vector construction."""

import numpy as np
import pytest

from backend.processor.feature_extractor import (
    DEFAULT_NORM,
    DEFAULT_WINDOW_SAMPLES,
    FeatureVector,
    NormMethod,
    extract_features,
)


# ── Helpers ──────────────────────────────────────────────────────────────


def _make_csi_data(
    n_samples: int = 200,
    n_subcarriers: int = 30,
    seed: int = 42,
) -> tuple[np.ndarray, np.ndarray]:
    """Create synthetic amplitude and phase data."""
    rng = np.random.default_rng(seed)
    amplitudes = rng.uniform(10.0, 100.0, (n_samples, n_subcarriers))
    phases = rng.uniform(-np.pi, np.pi, (n_samples, n_subcarriers))
    return amplitudes, phases


# ── Basic extraction tests ───────────────────────────────────────────────


class TestExtractFeatures:
    """Test feature vector shape, content, and defaults."""

    def test_output_shape(self):
        """Feature vector should be 4*K for K subcarriers."""
        amp, phase = _make_csi_data(n_samples=200, n_subcarriers=30)
        result = extract_features(amp, phase)
        assert isinstance(result, FeatureVector)
        assert result.vector.shape == (120,)  # 4 * 30
        assert result.n_subcarriers == 30
        assert result.n_samples == 100  # default window

    def test_output_shape_different_k(self):
        """Works with arbitrary subcarrier counts."""
        amp, phase = _make_csi_data(n_samples=100, n_subcarriers=10)
        result = extract_features(amp, phase)
        assert result.vector.shape == (40,)  # 4 * 10
        assert result.n_subcarriers == 10

    def test_window_uses_tail(self):
        """Only the last window_samples should be used."""
        k = 5
        # First 100 samples: low amplitude; last 100: high amplitude
        amp = np.ones((200, k)) * 10.0
        amp[100:, :] = 100.0
        phase = np.zeros((200, k))

        result = extract_features(amp, phase, window_samples=100)
        # mean_amp should be ~100, not ~55
        mean_amp = result.vector[:k]
        # After L2 norm, check relative magnitudes are consistent
        assert result.n_samples == 100

        # Use no normalization for exact check
        result_raw = extract_features(amp, phase, window_samples=100, norm="none")
        np.testing.assert_allclose(result_raw.vector[:k], 100.0)

    def test_short_data_uses_all(self):
        """When T < window_samples, all samples are used."""
        amp, phase = _make_csi_data(n_samples=50, n_subcarriers=5)
        result = extract_features(amp, phase, window_samples=200)
        assert result.n_samples == 50

    def test_feature_layout(self):
        """Verify [mean_amp, var_amp, mean_phase, std_phase] layout."""
        rng = np.random.default_rng(99)
        k = 4
        n = 100
        amp = rng.uniform(10, 50, (n, k))
        phase = rng.uniform(-1, 1, (n, k))

        result = extract_features(amp, phase, window_samples=n, norm="none")

        expected_mean_amp = np.mean(amp, axis=0)
        expected_var_amp = np.var(amp, axis=0, ddof=0)
        expected_mean_phase = np.mean(phase, axis=0)
        expected_std_phase = np.std(phase, axis=0, ddof=0)

        np.testing.assert_allclose(result.vector[:k], expected_mean_amp)
        np.testing.assert_allclose(result.vector[k : 2 * k], expected_var_amp)
        np.testing.assert_allclose(result.vector[2 * k : 3 * k], expected_mean_phase)
        np.testing.assert_allclose(result.vector[3 * k :], expected_std_phase)

    def test_defaults(self):
        """Default window and normalization."""
        assert DEFAULT_WINDOW_SAMPLES == 100
        assert DEFAULT_NORM == NormMethod.L2


# ── Normalization tests ──────────────────────────────────────────────────


class TestNormalization:
    """Test L2, z-score, and no normalization."""

    def test_l2_unit_norm(self):
        """L2-normalized vector should have unit norm."""
        amp, phase = _make_csi_data()
        result = extract_features(amp, phase, norm="l2")
        np.testing.assert_allclose(np.linalg.norm(result.vector), 1.0, atol=1e-12)

    def test_zscore_zero_mean_unit_std(self):
        """Z-score normalized vector should have ~0 mean, ~1 std."""
        amp, phase = _make_csi_data()
        result = extract_features(amp, phase, norm="zscore")
        np.testing.assert_allclose(np.mean(result.vector), 0.0, atol=1e-12)
        np.testing.assert_allclose(np.std(result.vector), 1.0, atol=1e-12)

    def test_none_preserves_raw(self):
        """No normalization should preserve raw values."""
        amp, phase = _make_csi_data(n_samples=100, n_subcarriers=5)
        result = extract_features(amp, phase, window_samples=100, norm="none")

        expected_mean_amp = np.mean(amp, axis=0)
        np.testing.assert_allclose(result.vector[:5], expected_mean_amp)

    def test_norm_method_enum(self):
        """Should accept NormMethod enum values."""
        amp, phase = _make_csi_data()
        result = extract_features(amp, phase, norm=NormMethod.L2)
        assert result.norm_method == NormMethod.L2

    def test_norm_method_string(self):
        """Should accept string values."""
        amp, phase = _make_csi_data()
        result = extract_features(amp, phase, norm="l2")
        assert result.norm_method == NormMethod.L2

    def test_constant_input_l2(self):
        """Constant input → zero variance → L2 should still work."""
        amp = np.ones((100, 5)) * 42.0
        phase = np.zeros((100, 5))
        result = extract_features(amp, phase, norm="l2")
        # Should not NaN or error
        assert not np.any(np.isnan(result.vector))

    def test_constant_input_zscore(self):
        """Constant input → all-same features → zscore handles zero std."""
        amp = np.ones((100, 1)) * 42.0
        phase = np.zeros((100, 1))
        result = extract_features(amp, phase, norm="zscore")
        assert not np.any(np.isnan(result.vector))


# ── Fingerprint DB compatibility ─────────────────────────────────────────


class TestFingerprintDBCompat:
    """Verify feature vectors work with the fingerprint database."""

    def test_vector_is_1d(self):
        """Fingerprint DB requires 1-D feature vectors."""
        amp, phase = _make_csi_data()
        result = extract_features(amp, phase)
        assert result.vector.ndim == 1
        assert result.vector.size > 0

    def test_consistent_dimensionality(self):
        """Same K subcarriers should always produce same-length vectors."""
        k = 20
        amp1, phase1 = _make_csi_data(n_samples=100, n_subcarriers=k, seed=1)
        amp2, phase2 = _make_csi_data(n_samples=300, n_subcarriers=k, seed=2)

        r1 = extract_features(amp1, phase1)
        r2 = extract_features(amp2, phase2)
        assert r1.vector.shape == r2.vector.shape == (4 * k,)

    def test_different_locations_differ(self):
        """Feature vectors from different data should not be identical."""
        amp1, phase1 = _make_csi_data(seed=1)
        amp2, phase2 = _make_csi_data(seed=2)

        r1 = extract_features(amp1, phase1, norm="l2")
        r2 = extract_features(amp2, phase2, norm="l2")

        # Vectors should differ (not identical)
        assert not np.allclose(r1.vector, r2.vector)


# ── Input validation ─────────────────────────────────────────────────────


class TestValidation:
    """Test error handling for invalid inputs."""

    def test_rejects_1d_amplitudes(self):
        with pytest.raises(ValueError, match="2-D"):
            extract_features(np.zeros(100), np.zeros((100, 5)))

    def test_rejects_1d_phases(self):
        with pytest.raises(ValueError, match="2-D"):
            extract_features(np.zeros((100, 5)), np.zeros(100))

    def test_rejects_shape_mismatch(self):
        with pytest.raises(ValueError, match="Shape mismatch"):
            extract_features(np.zeros((100, 5)), np.zeros((100, 3)))

    def test_rejects_empty(self):
        with pytest.raises(ValueError, match="non-empty"):
            extract_features(np.zeros((0, 5)), np.zeros((0, 5)))

    def test_rejects_zero_subcarriers(self):
        with pytest.raises(ValueError, match="non-empty"):
            extract_features(np.zeros((100, 0)), np.zeros((100, 0)))


# ── Zero-input edge cases ───────────────────────────────────────────────


class TestZeroInputEdgeCases:
    """Test degenerate inputs that exercise zero-norm and zero-std paths."""

    def test_all_zero_input_l2(self):
        """All-zero amplitudes and phases → zero feature vector → L2 returns unchanged."""
        amp = np.zeros((100, 5))
        phase = np.zeros((100, 5))
        result = extract_features(amp, phase, norm="l2")
        # All features (mean, var, mean_phase, std_phase) are 0
        np.testing.assert_array_equal(result.vector, 0.0)
        assert not np.any(np.isnan(result.vector))

    def test_uniform_features_zscore(self):
        """Features with zero std → zscore returns zero-centered (all equal → all zero)."""
        # Constant amp & phase → mean_amp=C, var_amp=0, mean_phase=C2, std_phase=0
        # With 1 subcarrier: vector=[C, 0, C2, 0], std may or may not be 0
        # All zero: vector=[0,0,0,0], std=0
        amp = np.zeros((100, 3))
        phase = np.zeros((100, 3))
        result = extract_features(amp, phase, norm="zscore")
        assert not np.any(np.isnan(result.vector))
        # All features are 0 → after zscore with zero std, result should be 0
        np.testing.assert_allclose(result.vector, 0.0, atol=1e-12)
