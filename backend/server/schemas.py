"""Pydantic models for the WiFi CSI tracking WebSocket and REST API.

WebSocket payload (TrackingFrame) is broadcast at 10Hz to connected dashboards.
REST response models cover system status, calibration, and sensor health.

All confidence values are clamped to [0, 1]. Coordinate values are validated
against floor dimension bounds loaded from house.yaml.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


# ── Floor dimension bounds (from house.yaml) ────────────────────
# Largest floor is 15m x 12m.  We allow a small margin for tracking
# noise that places dots slightly outside the strict floor boundary.
MAX_X = 16.0
MAX_Y = 13.0


# ── WebSocket payload models ────────────────────────────────────


class BreathingData(BaseModel):
    """Breathing rate extracted via 0.1-0.5 Hz bandpass + FFT."""

    model_config = ConfigDict(from_attributes=True)

    rate_bpm: int = Field(..., ge=0, le=60, description="Breaths per minute")
    confidence: float = Field(..., ge=0.0, le=1.0)


class HeartrateData(BaseModel):
    """Heart rate extracted via 0.8-2.0 Hz CWT (Morlet wavelet).

    The ``display`` flag is the authoritative gate for UI rendering —
    it is True only when the person is stationary, confidence exceeds
    a threshold, and zone signal quality is sufficient.
    """

    model_config = ConfigDict(from_attributes=True)

    rate_bpm: int = Field(..., ge=0, le=250, description="Beats per minute")
    confidence: float = Field(..., ge=0.0, le=1.0)
    display: bool = Field(
        ..., description="True when conditions are met for reliable HR display"
    )


class PersonPosition(BaseModel):
    """Tracked individual within a single floor."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., description="Person identifier (e.g. 'p1')")
    x: float = Field(..., ge=-1.0, le=MAX_X, description="X position in meters")
    y: float = Field(..., ge=-1.0, le=MAX_Y, description="Y position in meters")
    position_confidence: float = Field(..., ge=0.0, le=1.0)
    uncertainty_radius_m: float = Field(
        ..., ge=0.0, le=10.0, description="Position uncertainty radius in meters"
    )
    is_stationary: bool
    stationary_duration_s: float = Field(
        ..., ge=0.0, description="Seconds the person has been stationary"
    )
    breathing: BreathingData
    heartrate: HeartrateData


class TrackingFrame(BaseModel):
    """Single WebSocket broadcast frame (sent at 10 Hz).

    This is the top-level payload that the dashboard receives over
    ``/ws/tracking``.
    """

    model_config = ConfigDict(from_attributes=True)

    timestamp: float = Field(..., description="Unix epoch seconds (float)")
    floor: int = Field(..., ge=1, le=3, description="Floor number (1-indexed)")
    people: list[PersonPosition] = Field(default_factory=list)
    occupancy_estimate: int = Field(..., ge=0)
    occupancy_confidence: float = Field(..., ge=0.0, le=1.0)
    zone_signal_quality: dict[str, float] = Field(
        default_factory=dict,
        description="Room/zone name → signal quality (0-1)",
    )

    @model_validator(mode="after")
    def _validate_zone_signal_quality(self) -> TrackingFrame:
        for zone, quality in self.zone_signal_quality.items():
            if not 0.0 <= quality <= 1.0:
                raise ValueError(
                    f"zone_signal_quality[{zone!r}] = {quality} is outside [0, 1]"
                )
        return self


# ── REST response models ────────────────────────────────────────


class SensorStatus(BaseModel):
    """Health status for a single ESP32 sensor."""

    model_config = ConfigDict(from_attributes=True)

    mac: str = Field(..., description="Sensor MAC address")
    role: str = Field(..., description="'tx' or 'rx'")
    floor: int = Field(..., ge=1, le=3)
    last_seen_s: float = Field(
        ..., ge=0.0, description="Seconds since last packet received"
    )
    rssi: int = Field(..., description="Latest RSSI (dBm)")
    packets_per_sec: float = Field(..., ge=0.0)
    online: bool


class CalibrationStatus(BaseModel):
    """Current state of the fingerprint calibration database."""

    model_config = ConfigDict(from_attributes=True)

    floor: int = Field(..., ge=1, le=3)
    is_calibrated: bool
    fingerprint_count: int = Field(..., ge=0)
    last_calibrated_at: Optional[float] = Field(
        None, description="Unix epoch of last calibration, or null"
    )
    coverage_pct: float = Field(
        ..., ge=0.0, le=100.0, description="Percentage of floor area with fingerprints"
    )


class SystemStatus(BaseModel):
    """Top-level system status returned by GET /api/status."""

    model_config = ConfigDict(from_attributes=True)

    online: bool
    uptime_s: float = Field(..., ge=0.0)
    sensors: list[SensorStatus] = Field(default_factory=list)
    calibration: list[CalibrationStatus] = Field(default_factory=list)
    active_connections: int = Field(
        ..., ge=0, description="Number of connected WebSocket clients"
    )
    tracking_fps: float = Field(
        ..., ge=0.0, description="Current tracking frame rate (Hz)"
    )
