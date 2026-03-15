"""Weighted KNN localization with confidence estimation.

Given a CSI feature vector, queries the fingerprint database for K nearest
neighbors and computes a weighted position estimate with confidence metrics.
Weights are derived from softmax of cosine similarity (1 - cosine_distance).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from backend.tracker.fingerprint_db import FloorDB, KNNResult


@dataclass(frozen=True)
class LocalizationResult:
    """Result of a localization query."""

    x: float
    y: float
    position_confidence: float  # [0, 1] — higher is better
    uncertainty_radius_m: float  # meters — spread of K nearest positions


# ---------------------------------------------------------------------------
# Configurable defaults
# ---------------------------------------------------------------------------

DEFAULT_K: int = 5
DEFAULT_SIMILARITY_THRESHOLD: float = 0.3  # min cosine similarity to consider
DEFAULT_TEMPERATURE: float = 5.0  # softmax temperature (higher → more uniform)


def localize(
    floor_db: FloorDB,
    feature_vector: NDArray[np.float64],
    k: int = DEFAULT_K,
    similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    temperature: float = DEFAULT_TEMPERATURE,
) -> LocalizationResult:
    """Estimate position from a CSI feature vector using weighted KNN.

    Args:
        floor_db: Fingerprint database for a single floor.
        feature_vector: Query feature vector (1-D, same dim as DB).
        k: Number of nearest neighbors.
        similarity_threshold: Minimum cosine similarity for a neighbor to be
            considered a valid match. Neighbors below this are discarded.
        temperature: Softmax temperature for weight computation. Higher values
            produce more uniform weights; lower values sharpen toward the
            nearest neighbor.

    Returns:
        LocalizationResult with estimated (x, y), confidence, and uncertainty.
    """
    knn: KNNResult = floor_db.query_knn(feature_vector, k=k)

    # Cosine similarity = 1 - cosine_distance
    similarities = 1.0 - knn.distances

    # Filter by similarity threshold
    mask = similarities >= similarity_threshold
    if not np.any(mask):
        # No neighbors above threshold — return centroid with low confidence
        centroid = np.mean(knn.positions, axis=0)
        spread = _position_spread(knn.positions)
        return LocalizationResult(
            x=float(centroid[0]),
            y=float(centroid[1]),
            position_confidence=0.0,
            uncertainty_radius_m=float(spread),
        )

    valid_positions = knn.positions[mask]
    valid_similarities = similarities[mask]

    # Softmax weights from similarities
    weights = _softmax(valid_similarities, temperature=temperature)

    # Weighted position estimate
    position = weights @ valid_positions  # (2,)

    # Confidence: combines match quality and spatial agreement
    mean_similarity = float(np.mean(valid_similarities))
    spread = _position_spread(valid_positions)
    fraction_valid = float(np.sum(mask)) / len(mask)

    confidence = _compute_confidence(mean_similarity, spread, fraction_valid)

    return LocalizationResult(
        x=float(position[0]),
        y=float(position[1]),
        position_confidence=float(confidence),
        uncertainty_radius_m=float(spread),
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _softmax(values: NDArray[np.float64], temperature: float) -> NDArray[np.float64]:
    """Numerically stable softmax with temperature scaling.

    Returns a 1-D array of weights summing to 1.
    """
    scaled = values / temperature
    shifted = scaled - np.max(scaled)  # numerical stability
    exp_vals = np.exp(shifted)
    return exp_vals / np.sum(exp_vals)


def _position_spread(positions: NDArray[np.float64]) -> float:
    """RMS distance of positions from their centroid (meters).

    Measures how tightly clustered the neighbor positions are.
    A tight cluster means the DB agrees on where the query is.
    """
    if positions.shape[0] <= 1:
        return 0.0
    centroid = np.mean(positions, axis=0)
    diffs = positions - centroid
    distances = np.sqrt(np.sum(diffs**2, axis=1))
    return float(np.sqrt(np.mean(distances**2)))


def _compute_confidence(
    mean_similarity: float,
    spread_m: float,
    fraction_valid: float,
) -> float:
    """Combine similarity quality and spatial tightness into [0, 1] confidence.

    Components:
        - similarity_score: mean cosine similarity of valid neighbors [0, 1]
        - spatial_score: how tightly neighbors cluster (exponential decay)
        - fraction_valid: what fraction of K neighbors passed the threshold

    The final confidence is a weighted geometric mean, clipped to [0, 1].
    """
    # Similarity component — already in [0, 1] for L2-normalized vectors
    sim_score = float(np.clip(mean_similarity, 0.0, 1.0))

    # Spatial component — exponential decay with 3m half-life
    # spread=0 → 1.0, spread=3 → 0.5, spread=6 → 0.25
    spatial_score = float(np.exp(-0.231 * spread_m))  # ln(2)/3 ≈ 0.231

    # Weighted geometric mean: similarity matters most
    confidence = (sim_score ** 0.5) * (spatial_score ** 0.3) * (fraction_valid ** 0.2)

    return float(np.clip(confidence, 0.0, 1.0))
