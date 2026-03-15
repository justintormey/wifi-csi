"""Tests for SpotFi phase sanitization."""

import numpy as np
import pytest

from backend.processor.phase_sanitizer import sanitize_phase, sanitize_phase_batch


class TestSanitizePhase:
    """Tests for single-vector phase sanitization."""

    def test_known_linear_offset_removed(self):
        """Synthetic phase with known linear offset — verify removal within tolerance."""
        n = 114
        k = np.arange(n, dtype=np.float64)
        # True signal: small random perturbation
        rng = np.random.default_rng(42)
        true_signal = rng.normal(0, 0.1, n)
        # Add linear offset: phase[k] = 0.05*k + 1.2
        raw_phase = true_signal + 0.05 * k + 1.2

        sanitized = sanitize_phase(raw_phase)

        # After removing the linear fit, result should be zero-mean
        # and close to the original signal (up to a constant shift)
        residual = sanitized - (true_signal - np.mean(true_signal))
        # The residual should be nearly constant (close to zero after de-meaning)
        assert np.std(residual) < 0.02, f"Residual std too high: {np.std(residual)}"

    def test_phase_wrapping_handled(self):
        """Phase with 2π wraps should still have linear component removed."""
        n = 52
        k = np.arange(n, dtype=np.float64)
        # Large slope causes multiple wraps
        slope = 0.3
        intercept = 2.0
        linear_phase = slope * k + intercept
        # Wrap to [-π, π)
        wrapped = np.angle(np.exp(1j * linear_phase))

        sanitized = sanitize_phase(wrapped)

        # After sanitization, should be approximately constant (near zero)
        assert np.std(sanitized) < 0.05, (
            f"After removing linear offset from wrapped phase, std should be small, "
            f"got {np.std(sanitized)}"
        )

    def test_zero_slope_unchanged(self):
        """Constant phase should remain ~zero after sanitization."""
        phase = np.full(52, 1.5)
        sanitized = sanitize_phase(phase)
        np.testing.assert_allclose(sanitized, 0.0, atol=1e-10)

    def test_single_element_passthrough(self):
        """Single-element phase returned as-is."""
        phase = np.array([2.5])
        sanitized = sanitize_phase(phase)
        np.testing.assert_array_equal(sanitized, phase)

    def test_two_elements(self):
        """Two elements: linear fit is exact, so sanitized should be zero."""
        phase = np.array([1.0, 3.0])
        sanitized = sanitize_phase(phase)
        np.testing.assert_allclose(sanitized, 0.0, atol=1e-10)

    def test_custom_subcarrier_indices(self):
        """Non-contiguous subcarrier indices should work correctly."""
        k = np.array([0, 2, 4, 6, 8, 10], dtype=np.int32)
        # Linear phase on those indices
        phase = 0.1 * k.astype(np.float64) + 0.5
        sanitized = sanitize_phase(phase, subcarrier_indices=k)
        np.testing.assert_allclose(sanitized, 0.0, atol=1e-10)

    def test_rejects_2d_input(self):
        """2-D input should raise ValueError."""
        with pytest.raises(ValueError, match="1-D"):
            sanitize_phase(np.zeros((3, 4)))

    def test_mismatched_indices_shape(self):
        """Mismatched subcarrier_indices shape should raise."""
        with pytest.raises(ValueError, match="shape"):
            sanitize_phase(np.zeros(10), subcarrier_indices=np.arange(5))

    def test_large_offset_with_noise(self):
        """Large linear offset + noise — verify offset removed, noise preserved."""
        n = 114
        k = np.arange(n, dtype=np.float64)
        rng = np.random.default_rng(123)
        noise = rng.normal(0, 0.05, n)
        # Very large slope and intercept
        raw = noise + 2.5 * k + 100.0

        sanitized = sanitize_phase(raw)

        # Sanitized should be centered around zero with same noise level
        assert abs(np.mean(sanitized)) < 0.01
        assert abs(np.std(sanitized) - np.std(noise)) < 0.01


class TestSanitizePhaseBatch:
    """Tests for batch phase sanitization."""

    def test_batch_matches_single(self):
        """Batch result should match row-by-row single calls."""
        rng = np.random.default_rng(7)
        m, n = 10, 52
        phases = rng.uniform(-np.pi, np.pi, (m, n))
        # Add different linear offsets per row
        k = np.arange(n, dtype=np.float64)
        slopes = rng.uniform(-0.2, 0.2, m)
        for i in range(m):
            phases[i] += slopes[i] * k + rng.uniform(-1, 1)

        batch_result = sanitize_phase_batch(phases)

        for i in range(m):
            single_result = sanitize_phase(phases[i])
            np.testing.assert_allclose(
                batch_result[i], single_result, atol=1e-8,
                err_msg=f"Mismatch at row {i}",
            )

    def test_batch_wrapping(self):
        """Batch handles phase wrapping correctly."""
        n = 52
        k = np.arange(n, dtype=np.float64)
        row1 = 0.3 * k + 2.0
        row2 = -0.2 * k + 1.0
        wrapped = np.angle(np.exp(1j * np.vstack([row1, row2])))

        sanitized = sanitize_phase_batch(wrapped)

        for i in range(2):
            assert np.std(sanitized[i]) < 0.05

    def test_rejects_1d(self):
        """1-D input should raise ValueError."""
        with pytest.raises(ValueError, match="2-D"):
            sanitize_phase_batch(np.zeros(10))

    def test_custom_indices_batch(self):
        """Batch with custom subcarrier indices."""
        k = np.array([0, 5, 10, 15, 20], dtype=np.int32)
        phases = np.vstack([
            0.1 * k.astype(np.float64) + 1.0,
            0.2 * k.astype(np.float64) - 0.5,
        ])
        sanitized = sanitize_phase_batch(phases, subcarrier_indices=k)
        np.testing.assert_allclose(sanitized, 0.0, atol=1e-10)
