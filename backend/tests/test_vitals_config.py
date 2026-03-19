"""Tests for backend.config.vitals_config — YAML-based vital signs tuning."""

import tempfile
from pathlib import Path

import pytest
import yaml

from backend.config.vitals_config import (
    BreathingConfig,
    HeartRateConfig,
    HeartRateGates,
    MotionConfig,
    VitalsConfig,
    load_vitals_config,
)
from backend.vitals.breathing import BreathingExtractor
from backend.vitals.heartrate import HeartRateExtractor
from backend.vitals.motion_detector import MotionDetector


# ── Default config loading ─────────────────────────────────────────


class TestLoadVitalsConfig:
    """Test loading from the actual vitals.yaml file."""

    def test_load_default_config(self):
        """Default vitals.yaml loads without error."""
        cfg = load_vitals_config()
        assert isinstance(cfg, VitalsConfig)
        assert cfg.sample_rate == 100.0

    def test_load_breathing_section(self):
        cfg = load_vitals_config()
        assert cfg.breathing.window_seconds == 30.0
        assert cfg.breathing.top_k == 15
        assert cfg.breathing.min_bpm == 8.0
        assert cfg.breathing.max_bpm == 30.0
        assert cfg.breathing.min_snr_db == 3.0

    def test_load_heartrate_section(self):
        cfg = load_vitals_config()
        assert cfg.heartrate.min_bpm == 40.0
        assert cfg.heartrate.max_bpm == 120.0
        assert cfg.heartrate.cwt_num_freqs == 64
        assert cfg.heartrate.gates.position_confidence == 0.6
        assert cfg.heartrate.gates.stationary_seconds == 30.0

    def test_load_motion_section(self):
        cfg = load_vitals_config()
        assert cfg.motion.threshold == 0.15
        assert cfg.motion.window_size == 50
        assert cfg.motion.baseline_ema_alpha == 0.01

    def test_sample_rate_propagates_to_sections(self):
        """Global sample_rate_hz flows into each section config."""
        cfg = load_vitals_config()
        assert cfg.breathing.sample_rate == cfg.sample_rate
        assert cfg.heartrate.sample_rate == cfg.sample_rate
        assert cfg.motion.sample_rate == cfg.sample_rate

    def test_missing_file_raises(self):
        with pytest.raises(FileNotFoundError):
            load_vitals_config("/nonexistent/path/vitals.yaml")


# ── Custom config loading ──────────────────────────────────────────


class TestCustomConfig:
    """Test loading from custom YAML with overridden values."""

    def _write_config(self, data: dict) -> Path:
        f = tempfile.NamedTemporaryFile(suffix=".yaml", delete=False, mode="w")
        yaml.dump(data, f)
        f.close()
        return Path(f.name)

    def test_custom_sample_rate(self):
        path = self._write_config({"sample_rate_hz": 50.0})
        cfg = load_vitals_config(path)
        assert cfg.sample_rate == 50.0
        assert cfg.breathing.sample_rate == 50.0

    def test_custom_breathing_params(self):
        path = self._write_config({
            "breathing": {
                "window_seconds": 20.0,
                "top_k_subcarriers": 10,
                "min_snr_db": 5.0,
            }
        })
        cfg = load_vitals_config(path)
        assert cfg.breathing.window_seconds == 20.0
        assert cfg.breathing.top_k == 10
        assert cfg.breathing.min_snr_db == 5.0
        # Unspecified fields get defaults
        assert cfg.breathing.filter_order == 4

    def test_custom_heartrate_gates(self):
        path = self._write_config({
            "heartrate": {
                "gates": {
                    "position_confidence": 0.8,
                    "stationary_seconds": 45.0,
                }
            }
        })
        cfg = load_vitals_config(path)
        assert cfg.heartrate.gates.position_confidence == 0.8
        assert cfg.heartrate.gates.stationary_seconds == 45.0

    def test_empty_yaml_uses_all_defaults(self):
        path = self._write_config({})
        cfg = load_vitals_config(path)
        assert cfg.sample_rate == 100.0
        assert cfg.breathing.top_k == 15
        assert cfg.heartrate.cwt_omega0 == 6.0
        assert cfg.motion.threshold == 0.15


# ── Factory methods ────────────────────────────────────────────────


class TestFactoryMethods:
    """Test that factory methods create properly configured extractors."""

    def test_create_breathing_extractor(self):
        cfg = VitalsConfig()
        ext = cfg.create_breathing_extractor()
        assert isinstance(ext, BreathingExtractor)
        assert ext._sample_rate == 100.0
        assert ext._top_k == 15

    def test_create_heartrate_extractor(self):
        cfg = VitalsConfig()
        ext = cfg.create_heartrate_extractor()
        assert isinstance(ext, HeartRateExtractor)
        assert ext._sample_rate == 100.0
        assert ext._cwt_num_freqs == 64
        assert ext._position_confidence_threshold == 0.6

    def test_create_motion_detector(self):
        cfg = VitalsConfig()
        det = cfg.create_motion_detector()
        assert isinstance(det, MotionDetector)
        assert det._motion_threshold == 0.15
        assert det._window_size == 50

    def test_custom_config_propagates_to_extractor(self):
        cfg = VitalsConfig(
            breathing=BreathingConfig(top_k=5, min_snr_db=6.0),
        )
        ext = cfg.create_breathing_extractor()
        assert ext._top_k == 5
        assert ext._min_snr_db == 6.0

    def test_heartrate_gates_propagate(self):
        cfg = VitalsConfig(
            heartrate=HeartRateConfig(
                gates=HeartRateGates(
                    position_confidence=0.9,
                    stationary_seconds=60.0,
                )
            ),
        )
        ext = cfg.create_heartrate_extractor()
        assert ext._position_confidence_threshold == 0.9
        assert ext._stationary_seconds_threshold == 60.0
