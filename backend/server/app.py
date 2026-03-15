"""FastAPI application for WiFi CSI real-time tracking.

Exposes REST endpoints for system status, sensor health, and calibration,
plus a WebSocket endpoint for 10 Hz tracking data broadcast.

Run with:
    uvicorn backend.server.app:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

import yaml
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.server.schemas import (
    CalibrationStatus,
    SensorStatus,
    SystemStatus,
)
from backend.server.ws_manager import WebSocketManager

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def _load_house_config() -> dict[str, Any]:
    """Load house.yaml floor/sensor configuration."""
    config_path = CONFIG_DIR / "house.yaml"
    if config_path.exists():
        with open(config_path) as f:
            return yaml.safe_load(f) or {}
    logger.warning("house.yaml not found at %s", config_path)
    return {}


# ── Application state ───────────────────────────────────────────


class AppState:
    """Mutable application state shared across request handlers."""

    def __init__(self) -> None:
        self.start_time: float = time.time()
        self.ws_manager = WebSocketManager()
        self.house_config: dict[str, Any] = {}
        self.calibrating: dict[int, bool] = {}  # floor -> is_calibrating
        self.tracking_fps: float = 0.0


_state = AppState()


# ── Lifespan ─────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle hook."""
    _state.start_time = time.time()
    _state.house_config = _load_house_config()

    floors = _state.house_config.get("floors", {})
    for floor_id in floors:
        _state.calibrating[int(floor_id)] = False

    logger.info(
        "WiFi CSI server started. Floors configured: %s",
        list(floors.keys()),
    )

    yield  # ── application runs ──

    await _state.ws_manager.close_all()
    logger.info("WiFi CSI server shut down.")


# ── FastAPI app ──────────────────────────────────────────────────

app = FastAPI(
    title="WiFi CSI Tracking",
    description="Real-time people tracking and vital signs via WiFi CSI.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Error handling middleware ────────────────────────────────────


@app.exception_handler(Exception)
async def _global_exception_handler(request, exc):
    logger.exception("Unhandled error: %s", exc)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


# ── REST endpoints ──────────────────────────────────────────────


@app.get("/health")
async def health():
    """System health check."""
    return {"status": "ok", "uptime_s": round(time.time() - _state.start_time, 1)}


@app.get("/api/status", response_model=SystemStatus)
async def system_status():
    """System status: sensors online, calibration state, uptime."""
    floors = _state.house_config.get("floors", {})
    sensors = _build_sensor_list(floors)
    calibration = _build_calibration_list(floors)

    return SystemStatus(
        online=True,
        uptime_s=round(time.time() - _state.start_time, 2),
        sensors=sensors,
        calibration=calibration,
        active_connections=_state.ws_manager.active_connections,
        tracking_fps=_state.tracking_fps,
    )


@app.get("/api/sensors", response_model=list[SensorStatus])
async def list_sensors():
    """List all sensors with status."""
    floors = _state.house_config.get("floors", {})
    return _build_sensor_list(floors)


@app.get("/api/calibration/status", response_model=list[CalibrationStatus])
async def calibration_status():
    """Calibration state per floor."""
    floors = _state.house_config.get("floors", {})
    return _build_calibration_list(floors)


@app.post("/api/calibration/start")
async def calibration_start(floor: int = 1):
    """Trigger calibration mode for a specific floor."""
    floors = _state.house_config.get("floors", {})
    if floor not in [int(f) for f in floors]:
        return JSONResponse(
            status_code=400,
            content={"detail": f"Floor {floor} not configured"},
        )

    if _state.calibrating.get(floor, False):
        return {"status": "already_calibrating", "floor": floor}

    _state.calibrating[floor] = True
    logger.info("Calibration started for floor %d", floor)
    return {"status": "calibrating", "floor": floor}


# ── WebSocket endpoint ──────────────────────────────────────────


@app.websocket("/ws/tracking")
async def ws_tracking(websocket: WebSocket, floors: Optional[str] = None):
    """Real-time tracking data stream (10 Hz broadcast).

    Clients connect here to receive TrackingFrame payloads.
    The actual data push is driven by the backend pipeline
    calling ``_state.ws_manager.broadcast_frame(frame)``.

    Query params:
        floors: Comma-separated floor numbers to subscribe to (e.g. "1,2").
                Omit to receive all floors.
    """
    floor_filter = None
    if floors:
        try:
            floor_filter = {int(f.strip()) for f in floors.split(",")}
        except ValueError:
            pass

    await _state.ws_manager.connect(websocket, floor_filter=floor_filter)
    try:
        while True:
            # Keep connection alive; ignore inbound messages.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await _state.ws_manager.disconnect(websocket)


# ── Helpers ──────────────────────────────────────────────────────


def _build_sensor_list(floors: dict[str, Any]) -> list[SensorStatus]:
    """Build sensor status list from house config.

    In production, this would query live MQTT heartbeats.  For now
    it returns placeholder entries derived from the floor config.
    """
    sensors: list[SensorStatus] = []
    for floor_id, floor_cfg in floors.items():
        fid = int(floor_id)
        # 1 TX + 3 RX per floor
        sensors.append(
            SensorStatus(
                mac=f"aa:bb:cc:dd:{fid:02x}:00",
                role="tx",
                floor=fid,
                last_seen_s=0.0,
                rssi=-30,
                packets_per_sec=100.0,
                online=True,
            )
        )
        for rx_idx in range(1, 4):
            sensors.append(
                SensorStatus(
                    mac=f"aa:bb:cc:dd:{fid:02x}:{rx_idx:02x}",
                    role="rx",
                    floor=fid,
                    last_seen_s=0.0,
                    rssi=-45,
                    packets_per_sec=100.0,
                    online=True,
                )
            )
    return sensors


def _build_calibration_list(floors: dict[str, Any]) -> list[CalibrationStatus]:
    """Build calibration status per floor."""
    result: list[CalibrationStatus] = []
    for floor_id in floors:
        fid = int(floor_id)
        result.append(
            CalibrationStatus(
                floor=fid,
                is_calibrated=False,
                fingerprint_count=0,
                last_calibrated_at=None,
                coverage_pct=0.0,
            )
        )
    return result


# ── Public accessors for pipeline integration ────────────────────


def get_ws_manager() -> WebSocketManager:
    """Get the WebSocket manager for broadcasting from the pipeline."""
    return _state.ws_manager


def get_app_state() -> AppState:
    """Get the application state for pipeline integration."""
    return _state
