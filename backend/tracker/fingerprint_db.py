"""Fingerprint database for WiFi CSI indoor localization.

Stores calibration fingerprints mapping (x, y, floor) positions to CSI feature
vectors. Supports save/load as .npz files (one per floor), CRUD operations on
individual fingerprints, and K-nearest-neighbor queries using cosine distance.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
from numpy.typing import NDArray


@dataclass
class FingerprintMetadata:
    """Per-floor calibration metadata."""

    floor: int
    calibration_timestamp: float
    grid_resolution: float  # meters between calibration points
    sensor_config_hash: str  # hash of sensor placement config
    num_fingerprints: int = 0


@dataclass
class Fingerprint:
    """A single calibration fingerprint."""

    x: float
    y: float
    floor: int
    feature_vector: NDArray[np.float64]


@dataclass
class KNNResult:
    """Result of a K-nearest-neighbor query."""

    positions: NDArray[np.float64]  # (K, 2) array of (x, y)
    distances: NDArray[np.float64]  # (K,) cosine distances
    indices: NDArray[np.intp]  # (K,) indices into the floor DB


class FloorDB:
    """Fingerprint database for a single floor."""

    def __init__(
        self,
        floor: int,
        grid_resolution: float = 1.0,
        sensor_config_hash: str = "",
    ) -> None:
        self.floor = floor
        self.positions: NDArray[np.float64] = np.empty((0, 2), dtype=np.float64)
        self.features: NDArray[np.float64] = np.empty((0, 0), dtype=np.float64)
        self.calibration_timestamp: float = time.time()
        self.grid_resolution = grid_resolution
        self.sensor_config_hash = sensor_config_hash

    @property
    def size(self) -> int:
        return int(self.positions.shape[0])

    def add(self, fp: Fingerprint) -> int:
        """Add a fingerprint. Returns its index."""
        if fp.floor != self.floor:
            raise ValueError(
                f"Fingerprint floor {fp.floor} does not match DB floor {self.floor}"
            )

        vec = np.asarray(fp.feature_vector, dtype=np.float64)
        if vec.ndim != 1 or vec.size == 0:
            raise ValueError("Feature vector must be a non-empty 1-D array")

        pos = np.array([[fp.x, fp.y]], dtype=np.float64)

        if self.size == 0:
            self.positions = pos
            self.features = vec.reshape(1, -1)
        else:
            if vec.size != self.features.shape[1]:
                raise ValueError(
                    f"Feature vector length {vec.size} does not match "
                    f"existing length {self.features.shape[1]}"
                )
            self.positions = np.vstack([self.positions, pos])
            self.features = np.vstack([self.features, vec.reshape(1, -1)])

        self.calibration_timestamp = time.time()
        return self.size - 1

    def update(self, index: int, fp: Fingerprint) -> None:
        """Update the fingerprint at the given index."""
        if index < 0 or index >= self.size:
            raise IndexError(f"Index {index} out of range [0, {self.size})")
        if fp.floor != self.floor:
            raise ValueError(
                f"Fingerprint floor {fp.floor} does not match DB floor {self.floor}"
            )

        vec = np.asarray(fp.feature_vector, dtype=np.float64)
        if vec.ndim != 1 or vec.size != self.features.shape[1]:
            raise ValueError(
                f"Feature vector length must be {self.features.shape[1]}"
            )

        self.positions[index] = [fp.x, fp.y]
        self.features[index] = vec
        self.calibration_timestamp = time.time()

    def delete(self, index: int) -> None:
        """Delete the fingerprint at the given index."""
        if index < 0 or index >= self.size:
            raise IndexError(f"Index {index} out of range [0, {self.size})")

        self.positions = np.delete(self.positions, index, axis=0)
        self.features = np.delete(self.features, index, axis=0)
        self.calibration_timestamp = time.time()

    def query_knn(self, feature_vector: NDArray[np.float64], k: int = 5) -> KNNResult:
        """Find K nearest neighbors by cosine distance.

        Cosine distance = 1 - cosine_similarity. Range [0, 2].
        """
        if self.size == 0:
            raise ValueError("Cannot query an empty database")

        vec = np.asarray(feature_vector, dtype=np.float64).ravel()
        if vec.size != self.features.shape[1]:
            raise ValueError(
                f"Query vector length {vec.size} does not match "
                f"DB feature length {self.features.shape[1]}"
            )

        k = min(k, self.size)

        # Cosine similarity: dot(a, b) / (||a|| * ||b||)
        query_norm = np.linalg.norm(vec)
        if query_norm == 0:
            raise ValueError("Query vector must be non-zero")

        feature_norms = np.linalg.norm(self.features, axis=1)
        # Guard against zero-norm stored vectors
        safe_norms = np.where(feature_norms == 0, 1.0, feature_norms)

        similarities = self.features @ vec / (safe_norms * query_norm)
        distances = 1.0 - similarities

        # Partial sort for top-K (faster than full sort for large DBs)
        if k < self.size:
            top_k_indices = np.argpartition(distances, k)[:k]
        else:
            top_k_indices = np.arange(self.size)
        # Sort those K by distance
        sorted_order = np.argsort(distances[top_k_indices])
        top_k_indices = top_k_indices[sorted_order]

        return KNNResult(
            positions=self.positions[top_k_indices].copy(),
            distances=distances[top_k_indices].copy(),
            indices=top_k_indices.copy(),
        )

    def get_metadata(self) -> FingerprintMetadata:
        return FingerprintMetadata(
            floor=self.floor,
            calibration_timestamp=self.calibration_timestamp,
            grid_resolution=self.grid_resolution,
            sensor_config_hash=self.sensor_config_hash,
            num_fingerprints=self.size,
        )


class FingerprintDB:
    """Multi-floor fingerprint database backed by .npz files."""

    def __init__(self, db_dir: str | Path) -> None:
        self.db_dir = Path(db_dir)
        self.floors: dict[int, FloorDB] = {}

    def get_floor(
        self,
        floor: int,
        grid_resolution: float = 1.0,
        sensor_config_hash: str = "",
    ) -> FloorDB:
        """Get or create a floor database."""
        if floor not in self.floors:
            self.floors[floor] = FloorDB(
                floor=floor,
                grid_resolution=grid_resolution,
                sensor_config_hash=sensor_config_hash,
            )
        return self.floors[floor]

    def _floor_path(self, floor: int) -> Path:
        return self.db_dir / f"floor_{floor}.npz"

    def save(self, floor: Optional[int] = None) -> None:
        """Save floor DB(s) to .npz files. If floor is None, save all."""
        self.db_dir.mkdir(parents=True, exist_ok=True)
        floors_to_save = [floor] if floor is not None else list(self.floors.keys())

        for f in floors_to_save:
            if f not in self.floors:
                raise KeyError(f"Floor {f} not in database")
            fdb = self.floors[f]
            np.savez_compressed(
                self._floor_path(f),
                positions=fdb.positions,
                features=fdb.features,
                metadata=np.array([
                    fdb.floor,
                    fdb.calibration_timestamp,
                    fdb.grid_resolution,
                ]),
                sensor_config_hash=np.array([fdb.sensor_config_hash]),
            )

    def load(self, floor: Optional[int] = None) -> None:
        """Load floor DB(s) from .npz files. If floor is None, load all found."""
        if floor is not None:
            self._load_floor(floor)
        else:
            if not self.db_dir.exists():
                return
            for path in sorted(self.db_dir.glob("floor_*.npz")):
                floor_num = int(path.stem.split("_")[1])
                self._load_floor(floor_num)

    def _load_floor(self, floor: int) -> None:
        path = self._floor_path(floor)
        if not path.exists():
            raise FileNotFoundError(f"No database file for floor {floor}: {path}")

        data = np.load(path, allow_pickle=False)
        self._validate_npz(data, floor)

        meta = data["metadata"]
        fdb = FloorDB(
            floor=int(meta[0]),
            grid_resolution=float(meta[2]),
            sensor_config_hash=str(data["sensor_config_hash"][0]),
        )
        fdb.calibration_timestamp = float(meta[1])
        fdb.positions = data["positions"].astype(np.float64)
        fdb.features = data["features"].astype(np.float64)

        self.floors[floor] = fdb

    @staticmethod
    def _validate_npz(data: np.lib.npyio.NpzFile, floor: int) -> None:
        """Validate .npz structure and data integrity."""
        required_keys = {"positions", "features", "metadata", "sensor_config_hash"}
        missing = required_keys - set(data.files)
        if missing:
            raise ValueError(f"Floor {floor} DB missing keys: {missing}")

        positions = data["positions"]
        features = data["features"]

        if positions.ndim != 2 or positions.shape[1] != 2:
            raise ValueError(
                f"Floor {floor}: positions must be (N, 2), got {positions.shape}"
            )
        if features.ndim != 2:
            raise ValueError(
                f"Floor {floor}: features must be 2-D, got {features.ndim}-D"
            )
        if positions.shape[0] != features.shape[0]:
            raise ValueError(
                f"Floor {floor}: positions ({positions.shape[0]}) and features "
                f"({features.shape[0]}) row count mismatch"
            )

        meta = data["metadata"]
        if meta.shape != (3,):
            raise ValueError(
                f"Floor {floor}: metadata must have 3 elements, got {meta.shape}"
            )
        if int(meta[0]) != floor:
            raise ValueError(
                f"Floor {floor}: metadata floor tag is {int(meta[0])}"
            )

    @property
    def floor_numbers(self) -> list[int]:
        return sorted(self.floors.keys())

    def summary(self) -> dict[int, FingerprintMetadata]:
        """Return metadata for all loaded floors."""
        return {f: fdb.get_metadata() for f, fdb in self.floors.items()}


def compute_sensor_config_hash(sensor_positions: dict) -> str:
    """Compute a deterministic hash of sensor placement configuration.

    Args:
        sensor_positions: Dict mapping sensor IDs to (x, y, z) positions.
    """
    canonical = str(sorted(sensor_positions.items()))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]
