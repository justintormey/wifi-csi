"""Round-trip serialization/deserialization tests for server schemas."""

import json
import time

import pytest
from pydantic import ValidationError

from backend.server.schemas import (
    BreathingData,
    CalibrationStatus,
    HeartrateData,
    PersonPosition,
    SensorStatus,
    SystemStatus,
    TrackingFrame,
)


# ── Fixtures ────────────────────────────────────────────────────


def _make_person(id_: str = "p1", **overrides) -> dict:
    base = {
        "id": id_,
        "x": 5.2,
        "y": 3.8,
        "position_confidence": 0.85,
        "uncertainty_radius_m": 0.6,
        "is_stationary": True,
        "stationary_duration_s": 45.0,
        "breathing": {"rate_bpm": 16, "confidence": 0.82},
        "heartrate": {"rate_bpm": 72, "confidence": 0.65, "display": True},
    }
    base.update(overrides)
    return base


def _make_frame(**overrides) -> dict:
    base = {
        "timestamp": time.time(),
        "floor": 1,
        "people": [_make_person("p1"), _make_person("p2", x=10.0, y=8.0)],
        "occupancy_estimate": 2,
        "occupancy_confidence": 0.92,
        "zone_signal_quality": {
            "kitchen": 0.88,
            "living_room": 0.75,
            "garage": 0.62,
        },
    }
    base.update(overrides)
    return base


# ── Round-trip tests ────────────────────────────────────────────


class TestBreathingData:
    def test_round_trip(self):
        data = {"rate_bpm": 15, "confidence": 0.78}
        model = BreathingData(**data)
        assert model.model_dump() == data

    def test_json_round_trip(self):
        model = BreathingData(rate_bpm=18, confidence=0.91)
        json_str = model.model_dump_json()
        restored = BreathingData.model_validate_json(json_str)
        assert restored == model


class TestHeartrateData:
    def test_round_trip(self):
        data = {"rate_bpm": 72, "confidence": 0.65, "display": True}
        model = HeartrateData(**data)
        assert model.model_dump() == data

    def test_display_false(self):
        model = HeartrateData(rate_bpm=68, confidence=0.1, display=False)
        assert model.display is False
        assert model.model_dump()["display"] is False


class TestPersonPosition:
    def test_round_trip(self):
        data = _make_person()
        model = PersonPosition(**data)
        dumped = model.model_dump()
        assert dumped == data

    def test_json_round_trip(self):
        data = _make_person()
        model = PersonPosition(**data)
        json_str = model.model_dump_json()
        restored = PersonPosition.model_validate_json(json_str)
        assert restored == model

    def test_nested_breathing_heartrate(self):
        model = PersonPosition(**_make_person())
        assert isinstance(model.breathing, BreathingData)
        assert isinstance(model.heartrate, HeartrateData)
        assert model.breathing.rate_bpm == 16
        assert model.heartrate.display is True


class TestTrackingFrame:
    def test_round_trip(self):
        data = _make_frame()
        model = TrackingFrame(**data)
        dumped = model.model_dump()
        assert dumped["floor"] == data["floor"]
        assert len(dumped["people"]) == 2
        assert dumped["occupancy_estimate"] == 2
        assert dumped["zone_signal_quality"] == data["zone_signal_quality"]

    def test_json_round_trip(self):
        data = _make_frame()
        model = TrackingFrame(**data)
        json_str = model.model_dump_json()
        restored = TrackingFrame.model_validate_json(json_str)
        assert restored.floor == model.floor
        assert len(restored.people) == len(model.people)
        assert restored.people[0].id == model.people[0].id

    def test_empty_people(self):
        data = _make_frame(people=[], occupancy_estimate=0)
        model = TrackingFrame(**data)
        assert model.people == []

    def test_simulator_payload_compatible(self):
        """Verify that the exact shape the JS simulator emits can be parsed."""
        payload = {
            "timestamp": 1710532800.123,
            "floor": 1,
            "people": [
                {
                    "id": "p1",
                    "x": 5.23,
                    "y": 3.81,
                    "position_confidence": 0.85,
                    "uncertainty_radius_m": 0.6,
                    "is_stationary": True,
                    "stationary_duration_s": 45.3,
                    "breathing": {"rate_bpm": 16, "confidence": 0.82},
                    "heartrate": {"rate_bpm": 72, "confidence": 0.65, "display": True},
                }
            ],
            "occupancy_estimate": 1,
            "occupancy_confidence": 0.92,
            "zone_signal_quality": {"kitchen": 0.88, "living_room": 0.75},
        }
        model = TrackingFrame(**payload)
        assert model.people[0].breathing.rate_bpm == 16


# ── Validation tests ────────────────────────────────────────────


class TestValidation:
    def test_confidence_above_one_rejected(self):
        with pytest.raises(ValidationError):
            BreathingData(rate_bpm=15, confidence=1.5)

    def test_confidence_below_zero_rejected(self):
        with pytest.raises(ValidationError):
            HeartrateData(rate_bpm=72, confidence=-0.1, display=True)

    def test_x_out_of_bounds_rejected(self):
        with pytest.raises(ValidationError):
            PersonPosition(**_make_person(x=20.0))

    def test_y_out_of_bounds_rejected(self):
        with pytest.raises(ValidationError):
            PersonPosition(**_make_person(y=20.0))

    def test_negative_stationary_duration_rejected(self):
        with pytest.raises(ValidationError):
            PersonPosition(**_make_person(stationary_duration_s=-1.0))

    def test_invalid_floor_rejected(self):
        with pytest.raises(ValidationError):
            TrackingFrame(**_make_frame(floor=0))

    def test_zone_signal_quality_out_of_range(self):
        with pytest.raises(ValidationError):
            TrackingFrame(
                **_make_frame(zone_signal_quality={"kitchen": 1.5})
            )

    def test_boundary_values_accepted(self):
        """Boundary values (0.0 and 1.0) should pass validation."""
        bd = BreathingData(rate_bpm=0, confidence=0.0)
        assert bd.confidence == 0.0
        hd = HeartrateData(rate_bpm=0, confidence=1.0, display=False)
        assert hd.confidence == 1.0


# ── REST model tests ────────────────────────────────────────────


class TestSensorStatus:
    def test_round_trip(self):
        data = {
            "mac": "aa:bb:cc:dd:ee:ff",
            "role": "rx",
            "floor": 1,
            "last_seen_s": 0.5,
            "rssi": -45,
            "packets_per_sec": 98.5,
            "online": True,
        }
        model = SensorStatus(**data)
        assert model.model_dump() == data


class TestCalibrationStatus:
    def test_round_trip(self):
        data = {
            "floor": 2,
            "is_calibrated": True,
            "fingerprint_count": 1200,
            "last_calibrated_at": 1710532800.0,
            "coverage_pct": 78.5,
        }
        model = CalibrationStatus(**data)
        assert model.model_dump() == data

    def test_null_last_calibrated(self):
        model = CalibrationStatus(
            floor=1,
            is_calibrated=False,
            fingerprint_count=0,
            last_calibrated_at=None,
            coverage_pct=0.0,
        )
        assert model.last_calibrated_at is None


class TestSystemStatus:
    def test_round_trip(self):
        data = {
            "online": True,
            "uptime_s": 3600.0,
            "sensors": [
                {
                    "mac": "aa:bb:cc:dd:ee:ff",
                    "role": "rx",
                    "floor": 1,
                    "last_seen_s": 0.5,
                    "rssi": -45,
                    "packets_per_sec": 98.5,
                    "online": True,
                }
            ],
            "calibration": [
                {
                    "floor": 1,
                    "is_calibrated": True,
                    "fingerprint_count": 1200,
                    "last_calibrated_at": 1710532800.0,
                    "coverage_pct": 78.5,
                }
            ],
            "active_connections": 3,
            "tracking_fps": 10.0,
        }
        model = SystemStatus(**data)
        dumped = model.model_dump()
        assert dumped["online"] is True
        assert len(dumped["sensors"]) == 1
        assert dumped["sensors"][0]["mac"] == "aa:bb:cc:dd:ee:ff"

    def test_json_round_trip(self):
        model = SystemStatus(
            online=False,
            uptime_s=0.0,
            sensors=[],
            calibration=[],
            active_connections=0,
            tracking_fps=0.0,
        )
        json_str = model.model_dump_json()
        restored = SystemStatus.model_validate_json(json_str)
        assert restored == model
