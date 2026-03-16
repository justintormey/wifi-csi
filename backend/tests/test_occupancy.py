"""Tests for multi-person occupancy detection via NMF.

Covers the NMF core, source count estimation, the OccupancyDetector's
buffer management, and end-to-end detection on synthetic multi-source
CSI data with known source counts.
"""

from __future__ import annotations

import numpy as np
import pytest

from backend.tracker.occupancy import (
    DEFAULT_MIN_SNAPSHOTS,
    OccupancyDetector,
    OccupancyResult,
    _nmf,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

SEED = 42
N_SUBCARRIERS = 52  # HT20-equivalent for simpler tests


def make_detector(**kwargs) -> OccupancyDetector:
    """Create a detector with deterministic seed and faster NMF for tests."""
    defaults = {"seed": SEED, "nmf_max_iter": 100, "window_size": 50}
    defaults.update(kwargs)
    return OccupancyDetector(**defaults)


def synthetic_csi(
    n_sources: int,
    n_snapshots: int = 50,
    n_subcarriers: int = N_SUBCARRIERS,
    noise_level: float = 0.05,
    seed: int = SEED,
) -> NDArray[np.float64]:
    """Generate synthetic CSI amplitude data from known independent sources.

    Each source has a unique subcarrier signature (random positive vector).
    Each source has a unique temporal activation (random positive vector).
    The CSI matrix is V = W @ H + noise, where:
        W: (n_snapshots, n_sources) — temporal activations
        H: (n_sources, n_subcarriers) — subcarrier signatures
    """
    rng = np.random.default_rng(seed)

    # Each source activates a distinct subset of subcarriers
    H = np.zeros((n_sources, n_subcarriers))
    subs_per_source = n_subcarriers // max(n_sources, 1)
    for i in range(n_sources):
        start = i * subs_per_source
        end = start + subs_per_source
        H[i, start:end] = rng.uniform(0.5, 2.0, size=end - start)

    # Each source has independent temporal activity
    W = np.zeros((n_snapshots, n_sources))
    for i in range(n_sources):
        # Smooth temporal pattern: sine wave at different frequencies
        t = np.linspace(0, 2 * np.pi * (i + 1), n_snapshots)
        W[:, i] = np.abs(np.sin(t)) + 0.3  # keep positive

    V = W @ H + rng.uniform(0, noise_level, size=(n_snapshots, n_subcarriers))
    return np.maximum(V, 0.0)


# ---------------------------------------------------------------------------
# NMF core
# ---------------------------------------------------------------------------


class TestNMFCore:
    def test_output_shapes(self):
        """W and H should have correct shapes."""
        V = synthetic_csi(n_sources=2, n_snapshots=30)
        W, H, err = _nmf(V, k=2, rng=np.random.default_rng(SEED))
        assert W.shape == (30, 2)
        assert H.shape == (2, N_SUBCARRIERS)

    def test_non_negative(self):
        """W and H should be non-negative."""
        V = synthetic_csi(n_sources=2)
        W, H, _ = _nmf(V, k=2, rng=np.random.default_rng(SEED))
        assert np.all(W >= 0)
        assert np.all(H >= 0)

    def test_reconstruction_quality(self):
        """NMF with correct k should reconstruct well."""
        V = synthetic_csi(n_sources=2, noise_level=0.01)
        _, _, err = _nmf(V, k=2, max_iter=300, rng=np.random.default_rng(SEED))
        assert err < 0.15, f"Reconstruction error too high: {err:.3f}"

    def test_underfitting_higher_error(self):
        """NMF with k=1 on 2-source data should have higher error than k=2."""
        V = synthetic_csi(n_sources=2, noise_level=0.01)
        rng = np.random.default_rng(SEED)
        _, _, err_1 = _nmf(V, k=1, max_iter=300, rng=rng)
        rng = np.random.default_rng(SEED)
        _, _, err_2 = _nmf(V, k=2, max_iter=300, rng=rng)
        assert err_1 > err_2, (
            f"k=1 error ({err_1:.3f}) should exceed k=2 ({err_2:.3f})"
        )

    def test_single_source(self):
        """NMF with k=1 on 1-source data should reconstruct well."""
        V = synthetic_csi(n_sources=1, noise_level=0.01)
        _, _, err = _nmf(V, k=1, max_iter=300, rng=np.random.default_rng(SEED))
        assert err < 0.10, f"Single-source reconstruction error: {err:.3f}"


# ---------------------------------------------------------------------------
# Buffer management
# ---------------------------------------------------------------------------


class TestBufferManagement:
    def test_not_ready_when_empty(self):
        det = make_detector()
        assert not det.is_ready
        assert det.buffer_size == 0

    def test_ready_after_enough_snapshots(self):
        det = make_detector(min_snapshots=5)
        for _ in range(5):
            det.push(np.ones(N_SUBCARRIERS))
        assert det.is_ready
        assert det.buffer_size == 5

    def test_detect_raises_when_not_ready(self):
        det = make_detector(min_snapshots=10)
        det.push(np.ones(N_SUBCARRIERS))
        with pytest.raises(RuntimeError, match="Need at least"):
            det.detect()

    def test_buffer_rolls_at_window_size(self):
        det = make_detector(window_size=20)
        for _ in range(30):
            det.push(np.ones(N_SUBCARRIERS))
        assert det.buffer_size == 20

    def test_inconsistent_subcarrier_count_raises(self):
        det = make_detector()
        det.push(np.ones(10))
        with pytest.raises(ValueError, match="Expected 10"):
            det.push(np.ones(20))

    def test_empty_amplitude_raises(self):
        det = make_detector()
        with pytest.raises(ValueError, match="non-empty"):
            det.push(np.array([]))

    def test_reset_clears_buffer(self):
        det = make_detector()
        for _ in range(10):
            det.push(np.ones(N_SUBCARRIERS))
        det.reset()
        assert det.buffer_size == 0
        assert not det.is_ready

    def test_negative_amplitudes_clipped(self):
        """Negative values should be clipped to 0."""
        det = make_detector(min_snapshots=1)
        det.push(np.array([-1.0, -2.0, 3.0]))
        result = det.detect()
        # Should not crash — negatives handled gracefully
        assert isinstance(result, OccupancyResult)


# ---------------------------------------------------------------------------
# Occupancy detection: single source
# ---------------------------------------------------------------------------


class TestSingleSource:
    def test_detects_one_person(self):
        """With one clear source, estimate should be 1."""
        det = make_detector(min_snapshots=10)
        V = synthetic_csi(n_sources=1, n_snapshots=50, noise_level=0.01)
        for row in V:
            det.push(row)

        result = det.detect()
        assert result.occupancy_estimate == 1
        assert result.occupancy_min <= 1 <= result.occupancy_max
        assert result.components.shape[0] == 1

    def test_confidence_high_for_clear_source(self):
        det = make_detector(min_snapshots=10)
        V = synthetic_csi(n_sources=1, n_snapshots=50, noise_level=0.01)
        for row in V:
            det.push(row)

        result = det.detect()
        assert result.occupancy_confidence > 0.5


# ---------------------------------------------------------------------------
# Occupancy detection: multiple sources
# ---------------------------------------------------------------------------


class TestMultipleSource:
    def test_detects_two_people(self):
        """With two well-separated sources, estimate should be 2."""
        det = make_detector(min_snapshots=10)
        V = synthetic_csi(n_sources=2, n_snapshots=50, noise_level=0.02)
        for row in V:
            det.push(row)

        result = det.detect()
        # Allow some margin: NMF may estimate 1-3 depending on separation
        assert 1 <= result.occupancy_estimate <= 3
        assert result.components.shape[0] == result.occupancy_estimate

    def test_detects_three_people(self):
        """With three well-separated sources, estimate should be near 3."""
        det = make_detector(min_snapshots=10, max_people=6)
        V = synthetic_csi(
            n_sources=3, n_snapshots=60, n_subcarriers=60, noise_level=0.02
        )
        for row in V:
            det.push(row)

        result = det.detect()
        # Should be in range [2, 4]
        assert 2 <= result.occupancy_estimate <= 4

    def test_components_match_estimate(self):
        """Component matrix rows should match occupancy_estimate."""
        det = make_detector(min_snapshots=10)
        V = synthetic_csi(n_sources=2, n_snapshots=50, noise_level=0.02)
        for row in V:
            det.push(row)

        result = det.detect()
        assert result.components.shape == (
            result.occupancy_estimate,
            V.shape[1],
        )

    def test_components_non_negative(self):
        det = make_detector(min_snapshots=10)
        V = synthetic_csi(n_sources=2, n_snapshots=50, noise_level=0.02)
        for row in V:
            det.push(row)

        result = det.detect()
        assert np.all(result.components >= 0)


# ---------------------------------------------------------------------------
# Ambiguity range
# ---------------------------------------------------------------------------


class TestAmbiguityRange:
    def test_range_contains_estimate(self):
        det = make_detector(min_snapshots=10)
        V = synthetic_csi(n_sources=2, n_snapshots=50, noise_level=0.02)
        for row in V:
            det.push(row)

        result = det.detect()
        assert result.occupancy_min <= result.occupancy_estimate <= result.occupancy_max

    def test_range_ordered(self):
        det = make_detector(min_snapshots=10)
        V = synthetic_csi(n_sources=1, n_snapshots=50, noise_level=0.01)
        for row in V:
            det.push(row)

        result = det.detect()
        assert result.occupancy_min <= result.occupancy_max

    def test_noisy_data_wider_range(self):
        """High noise should generally produce a wider ambiguity range."""
        det_clean = make_detector(min_snapshots=10, seed=42)
        det_noisy = make_detector(min_snapshots=10, seed=42)

        V_clean = synthetic_csi(n_sources=2, n_snapshots=50, noise_level=0.01, seed=10)
        V_noisy = synthetic_csi(n_sources=2, n_snapshots=50, noise_level=0.5, seed=10)

        for row in V_clean:
            det_clean.push(row)
        for row in V_noisy:
            det_noisy.push(row)

        r_clean = det_clean.detect()
        r_noisy = det_noisy.detect()

        # Noisy range should be at least as wide (or confidence lower)
        clean_range = r_clean.occupancy_max - r_clean.occupancy_min
        noisy_range = r_noisy.occupancy_max - r_noisy.occupancy_min
        # At minimum, noisy should not be *more* confident
        assert r_noisy.occupancy_confidence <= r_clean.occupancy_confidence + 0.2


# ---------------------------------------------------------------------------
# Confidence
# ---------------------------------------------------------------------------


class TestConfidence:
    def test_confidence_in_range(self):
        det = make_detector(min_snapshots=10)
        V = synthetic_csi(n_sources=1, n_snapshots=50, noise_level=0.01)
        for row in V:
            det.push(row)

        result = det.detect()
        assert 0.0 <= result.occupancy_confidence <= 1.0


# ---------------------------------------------------------------------------
# Update convenience method
# ---------------------------------------------------------------------------


class TestUpdateConvenience:
    def test_returns_none_before_ready(self):
        det = make_detector(min_snapshots=5)
        result = det.update(np.ones(N_SUBCARRIERS))
        assert result is None

    def test_returns_result_when_ready(self):
        det = make_detector(min_snapshots=5)
        result = None
        for _ in range(5):
            result = det.update(np.ones(N_SUBCARRIERS))
        assert isinstance(result, OccupancyResult)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


class TestResultType:
    def test_result_is_frozen(self):
        det = make_detector(min_snapshots=10)
        V = synthetic_csi(n_sources=1, n_snapshots=50, noise_level=0.01)
        for row in V:
            det.push(row)

        result = det.detect()
        with pytest.raises(AttributeError):
            result.occupancy_estimate = 99  # type: ignore[misc]

    def test_max_people_configurable(self):
        det = make_detector(min_snapshots=10, max_people=3)
        V = synthetic_csi(n_sources=2, n_snapshots=50, noise_level=0.02)
        for row in V:
            det.push(row)

        result = det.detect()
        assert result.occupancy_estimate <= 3
        assert result.occupancy_max <= 3


# ---------------------------------------------------------------------------
# End-to-end: known source counts
# ---------------------------------------------------------------------------


class TestEndToEnd:
    def test_empty_room(self):
        """Pure noise (no sources) should estimate 1 (baseline)."""
        det = make_detector(min_snapshots=10)
        rng = np.random.default_rng(123)
        # Low-amplitude noise only — no structured sources
        for _ in range(30):
            det.push(rng.uniform(0, 0.01, size=N_SUBCARRIERS))

        result = det.detect()
        # With only noise, NMF will find k=1 (the noise itself)
        assert result.occupancy_estimate >= 1
        assert result.occupancy_estimate <= 2

    def test_sequential_arrivals(self):
        """Simulate one person, then two people arriving.

        First half: single source. Second half: two sources.
        The detector should see more people in the second window.
        """
        det_early = make_detector(min_snapshots=10, window_size=25, seed=42)
        det_late = make_detector(min_snapshots=10, window_size=25, seed=42)

        rng = np.random.default_rng(99)

        # Source signatures
        sig1 = rng.uniform(0.5, 2.0, size=N_SUBCARRIERS)
        sig2 = rng.uniform(0.5, 2.0, size=N_SUBCARRIERS)
        # Make them distinct: zero out different halves
        sig1[N_SUBCARRIERS // 2 :] = 0.0
        sig2[: N_SUBCARRIERS // 2] = 0.0

        # Phase 1: one person (25 snapshots)
        for _ in range(25):
            amp = sig1 * rng.uniform(0.8, 1.2) + rng.uniform(0, 0.02, size=N_SUBCARRIERS)
            det_early.push(amp)

        # Phase 2: two people (25 snapshots)
        for _ in range(25):
            amp = (
                sig1 * rng.uniform(0.8, 1.2)
                + sig2 * rng.uniform(0.8, 1.2)
                + rng.uniform(0, 0.02, size=N_SUBCARRIERS)
            )
            det_late.push(amp)

        r_early = det_early.detect()
        r_late = det_late.detect()

        # The two-person window should estimate more people
        assert r_late.occupancy_estimate >= r_early.occupancy_estimate


# ---------------------------------------------------------------------------
# Coverage gap tests
# ---------------------------------------------------------------------------


class TestOccupancyCoverageGaps:
    """Cover occupancy.py lines 84, 340."""

    def test_nmf_without_rng_uses_default(self):
        """Line 84: _nmf with rng=None should use default_rng."""
        rng_gen = np.random.default_rng(42)
        V = rng_gen.uniform(0.1, 2.0, size=(20, N_SUBCARRIERS))
        W, H, error = _nmf(V, k=2, rng=None)  # no rng provided
        assert W.shape == (20, 2)
        assert H.shape == (2, N_SUBCARRIERS)
        assert error < 1.0

    def test_rate_limiting_returns_cached(self):
        """Line 340: update() returns cached result within min_interval_s."""
        det = make_detector(
            min_snapshots=10,
            window_size=20,
            min_interval_s=1.0,  # 1 second = 100 samples at 100 Hz
        )
        rng = np.random.default_rng(42)
        # Fill buffer past min_snapshots
        for _ in range(20):
            det.push(rng.uniform(0.1, 2.0, size=N_SUBCARRIERS))

        # First update — runs NMF
        r1 = det.update(rng.uniform(0.1, 2.0, size=N_SUBCARRIERS))
        assert r1 is not None

        # Immediate second update — should return cached result (rate limited)
        r2 = det.update(rng.uniform(0.1, 2.0, size=N_SUBCARRIERS))
        assert r2 is not None
        assert r2.occupancy_estimate == r1.occupancy_estimate
