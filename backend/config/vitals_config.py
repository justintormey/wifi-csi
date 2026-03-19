"""Load and apply vital signs tuning configuration.

Reads ``config/vitals.yaml`` and constructs pre-configured extractor
instances.  This decouples algorithm parameters from code — tuning is
a YAML edit, not a source change.

Usage::

    from backend.config.vitals_config import load_vitals_config, VitalsConfig

    cfg = load_vitals_config()              # from default path
    cfg = load_vitals_config("path/to.yaml")  # from custom path

    breathing = cfg.create_breathing_extractor()
    heartrate = cfg.create_heartrate_extractor()
    motion    = cfg.create_motion_detector()
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

from backend.vitals.breathing import BreathingExtractor
from backend.vitals.heartrate import HeartRateExtractor
from backend.vitals.motion_detector import MotionDetector


_DEFAULT_CONFIG_PATH = Path(__file__).parent / "vitals.yaml"


# ---------------------------------------------------------------------------
# Config dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BreathingConfig:
    sample_rate: float = 100.0
    window_seconds: float = 30.0
    top_k: int = 15
    min_bpm: float = 8.0
    max_bpm: float = 30.0
    min_snr_db: float = 3.0
    snr_saturation_db: float = 20.0
    min_concentration: float = 0.15
    filter_order: int = 4
    min_snapshots: int = 500


@dataclass(frozen=True)
class HeartRateGates:
    position_confidence: float = 0.6
    stationary_seconds: float = 30.0


@dataclass(frozen=True)
class HeartRateConfig:
    sample_rate: float = 100.0
    window_seconds: float = 30.0
    top_k: int = 10
    min_bpm: float = 40.0
    max_bpm: float = 120.0
    min_snr_db: float = 3.0
    snr_saturation_db: float = 15.0
    filter_order: int = 4
    min_snapshots: int = 500
    cwt_num_freqs: int = 64
    cwt_omega0: float = 6.0
    breathing_harmonics: int = 3
    min_interval_seconds: float = 1.0
    gates: HeartRateGates = field(default_factory=HeartRateGates)


@dataclass(frozen=True)
class MotionConfig:
    sample_rate: float = 100.0
    threshold: float = 0.15
    window_size: int = 50
    min_snapshots: int = 5
    baseline_ema_alpha: float = 0.01


@dataclass(frozen=True)
class VitalsConfig:
    """Complete vital signs configuration."""

    sample_rate: float = 100.0
    breathing: BreathingConfig = field(default_factory=BreathingConfig)
    heartrate: HeartRateConfig = field(default_factory=HeartRateConfig)
    motion: MotionConfig = field(default_factory=MotionConfig)

    def create_breathing_extractor(self) -> BreathingExtractor:
        b = self.breathing
        return BreathingExtractor(
            sample_rate=b.sample_rate,
            window_seconds=b.window_seconds,
            top_k=b.top_k,
            min_bpm=b.min_bpm,
            max_bpm=b.max_bpm,
            min_snr_db=b.min_snr_db,
            snr_saturation_db=b.snr_saturation_db,
            min_concentration=b.min_concentration,
            filter_order=b.filter_order,
            min_snapshots=b.min_snapshots,
        )

    def create_heartrate_extractor(self) -> HeartRateExtractor:
        h = self.heartrate
        return HeartRateExtractor(
            sample_rate=h.sample_rate,
            window_seconds=h.window_seconds,
            top_k=h.top_k,
            min_bpm=h.min_bpm,
            max_bpm=h.max_bpm,
            min_snr_db=h.min_snr_db,
            snr_saturation_db=h.snr_saturation_db,
            filter_order=h.filter_order,
            min_snapshots=h.min_snapshots,
            cwt_num_freqs=h.cwt_num_freqs,
            cwt_w=h.cwt_omega0,
            position_confidence_threshold=h.gates.position_confidence,
            stationary_seconds_threshold=h.gates.stationary_seconds,
            breathing_harmonics=h.breathing_harmonics,
            min_interval_s=h.min_interval_seconds,
        )

    def create_motion_detector(self) -> MotionDetector:
        m = self.motion
        return MotionDetector(
            motion_threshold=m.threshold,
            window_size=m.window_size,
            min_snapshots=m.min_snapshots,
            sample_rate=m.sample_rate,
            baseline_ema_alpha=m.baseline_ema_alpha,
        )


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def _parse_breathing(raw: dict[str, Any], sample_rate: float) -> BreathingConfig:
    return BreathingConfig(
        sample_rate=sample_rate,
        window_seconds=raw.get("window_seconds", 30.0),
        top_k=raw.get("top_k_subcarriers", 15),
        min_bpm=raw.get("min_bpm", 8.0),
        max_bpm=raw.get("max_bpm", 30.0),
        min_snr_db=raw.get("min_snr_db", 3.0),
        snr_saturation_db=raw.get("snr_saturation_db", 20.0),
        min_concentration=raw.get("min_concentration", 0.15),
        filter_order=raw.get("filter_order", 4),
        min_snapshots=raw.get("min_snapshots", 500),
    )


def _parse_heartrate(raw: dict[str, Any], sample_rate: float) -> HeartRateConfig:
    gates_raw = raw.get("gates", {})
    return HeartRateConfig(
        sample_rate=sample_rate,
        window_seconds=raw.get("window_seconds", 30.0),
        top_k=raw.get("top_k_subcarriers", 10),
        min_bpm=raw.get("min_bpm", 40.0),
        max_bpm=raw.get("max_bpm", 120.0),
        min_snr_db=raw.get("min_snr_db", 3.0),
        snr_saturation_db=raw.get("snr_saturation_db", 15.0),
        filter_order=raw.get("filter_order", 4),
        min_snapshots=raw.get("min_snapshots", 500),
        cwt_num_freqs=raw.get("cwt_num_freqs", 64),
        cwt_omega0=raw.get("cwt_omega0", 6.0),
        breathing_harmonics=raw.get("breathing_harmonics", 3),
        min_interval_seconds=raw.get("min_interval_seconds", 1.0),
        gates=HeartRateGates(
            position_confidence=gates_raw.get("position_confidence", 0.6),
            stationary_seconds=gates_raw.get("stationary_seconds", 30.0),
        ),
    )


def _parse_motion(raw: dict[str, Any], sample_rate: float) -> MotionConfig:
    return MotionConfig(
        sample_rate=sample_rate,
        threshold=raw.get("threshold", 0.15),
        window_size=raw.get("window_size", 50),
        min_snapshots=raw.get("min_snapshots", 5),
        baseline_ema_alpha=raw.get("baseline_ema_alpha", 0.01),
    )


def load_vitals_config(path: Optional[str | Path] = None) -> VitalsConfig:
    """Load vital signs configuration from a YAML file.

    Args:
        path: Path to the YAML config file.  Defaults to
            ``backend/config/vitals.yaml``.

    Returns:
        Fully populated VitalsConfig.

    Raises:
        FileNotFoundError: If the config file doesn't exist.
    """
    config_path = Path(path) if path else _DEFAULT_CONFIG_PATH
    if not config_path.exists():
        raise FileNotFoundError(f"Vitals config not found: {config_path}")

    with open(config_path) as f:
        raw = yaml.safe_load(f) or {}

    sample_rate = float(raw.get("sample_rate_hz", 100.0))

    return VitalsConfig(
        sample_rate=sample_rate,
        breathing=_parse_breathing(raw.get("breathing", {}), sample_rate),
        heartrate=_parse_heartrate(raw.get("heartrate", {}), sample_rate),
        motion=_parse_motion(raw.get("motion", {}), sample_rate),
    )
