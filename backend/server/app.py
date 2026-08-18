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

from backend.calibration.builder import build_and_save, compute_coverage
from backend.calibration.collector import CalibrationSession
from backend.calibration.zone_recal import (
    ZoneBounds,
    recalibrate_zone,
)
from backend.server.schemas import (
    CalibrationBuildResult,
    CalibrationSessionStatus,
    CalibrationStartRequest,
    CalibrationStatus,
    CalibrationSubmitRequest,
    GridPoint as GridPointSchema,
    SensorStatus,
    SystemStatus,
    ZoneRecalRequest,
    ZoneRecalResult as ZoneRecalResultSchema,
)
from backend.server.ws_manager import WebSocketManager
from backend.tracker.fingerprint_db import FingerprintDB

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


DEFAULT_DB_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "fingerprints"


class AppState:
    """Mutable application state shared across request handlers."""

    def __init__(self) -> None:
        self.start_time: float = time.time()
        self.ws_manager = WebSocketManager()
        self.house_config: dict[str, Any] = {}
        self.calibrating: dict[int, bool] = {}  # floor -> is_calibrating
        self.tracking_fps: float = 0.0
        # Calibration sessions: floor_id -> active session
        self.calibration_sessions: dict[int, CalibrationSession] = {}
        # Fingerprint database (shared with pipeline)
        self.fingerprint_db: Optional[FingerprintDB] = None
        self.db_dir: Path = DEFAULT_DB_DIR


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

    # Load existing fingerprint databases
    _state.fingerprint_db = FingerprintDB(_state.db_dir)
    try:
        _state.fingerprint_db.load()
        logger.info(
            "Loaded fingerprint DBs for floors: %s",
            _state.fingerprint_db.floor_numbers,
        )
    except FileNotFoundError:
        logger.info("No existing fingerprint databases found")

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
    version="0.8.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
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
    """Calibration state per floor (from fingerprint DB on disk)."""
    floors = _state.house_config.get("floors", {})
    return _build_calibration_list(floors)


@app.post("/api/calibration/start", response_model=CalibrationSessionStatus)
async def calibration_start(body: CalibrationStartRequest):
    """Start a guided calibration walk for a specific floor."""
    floors = _state.house_config.get("floors", {})
    floor = body.floor
    if floor not in [int(f) for f in floors]:
        return JSONResponse(
            status_code=400,
            content={"detail": f"Floor {floor} not configured"},
        )

    if floor in _state.calibration_sessions and _state.calibration_sessions[floor].is_active:
        return JSONResponse(
            status_code=409,
            content={"detail": f"Calibration already in progress for floor {floor}"},
        )

    # Get floor dimensions from house config
    floor_cfg = floors.get(floor, floors.get(str(floor), {}))
    dims = floor_cfg.get("dimensions", {})
    width = dims.get("width_m", 18.0)
    depth = dims.get("depth_m", 10.5)

    session = CalibrationSession(
        floor_id=floor,
        width_m=width,
        depth_m=depth,
        grid_resolution_m=body.grid_resolution_m,
        frames_per_point=body.frames_per_point,
    )
    first_point = session.start()
    _state.calibration_sessions[floor] = session
    _state.calibrating[floor] = True

    logger.info(
        "Calibration started for floor %d: %d grid points at %.1fm resolution",
        floor, session.total_points, body.grid_resolution_m,
    )

    return CalibrationSessionStatus(
        floor=floor,
        active=True,
        grid_resolution_m=body.grid_resolution_m,
        total_points=session.total_points,
        collected_points=0,
        current_point=GridPointSchema(
            x=first_point.x, y=first_point.y, collected=False, frame_count=0,
        ),
        progress_pct=0.0,
        frames_per_point=body.frames_per_point,
        started_at=session.started_at,
    )


@app.get("/api/calibration/session/{floor}", response_model=CalibrationSessionStatus)
async def calibration_session_status(floor: int):
    """Get the current calibration session status for a floor."""
    session = _state.calibration_sessions.get(floor)
    if session is None:
        return JSONResponse(
            status_code=404,
            content={"detail": f"No calibration session for floor {floor}"},
        )

    current = session.current_point
    current_schema = None
    if current is not None:
        current_schema = GridPointSchema(
            x=current.x,
            y=current.y,
            collected=current.collected,
            frame_count=current.frame_count,
        )

    return CalibrationSessionStatus(
        floor=floor,
        active=session.is_active,
        grid_resolution_m=session.grid_resolution_m,
        total_points=session.total_points,
        collected_points=session.collected_points,
        current_point=current_schema,
        progress_pct=session.progress_pct,
        frames_per_point=session.frames_per_point,
        started_at=session.started_at,
        completed_at=session.completed_at,
    )


@app.post("/api/calibration/session/{floor}/start-point")
async def calibration_start_point(floor: int, point_index: Optional[int] = None):
    """Begin collecting CSI data at the current or specified grid point."""
    session = _state.calibration_sessions.get(floor)
    if session is None or not session.is_active:
        return JSONResponse(
            status_code=404,
            content={"detail": f"No active calibration session for floor {floor}"},
        )

    try:
        point = session.start_point(point_index)
    except (RuntimeError, IndexError) as e:
        return JSONResponse(status_code=400, content={"detail": str(e)})

    return {
        "status": "collecting",
        "point": {"x": point.x, "y": point.y, "index": point.index},
        "frames_needed": session.frames_per_point,
    }


@app.post("/api/calibration/session/{floor}/skip-point")
async def calibration_skip_point(floor: int):
    """Skip the current grid point and advance to the next."""
    session = _state.calibration_sessions.get(floor)
    if session is None or not session.is_active:
        return JSONResponse(
            status_code=404,
            content={"detail": f"No active calibration session for floor {floor}"},
        )

    try:
        next_point = session.skip_point()
    except RuntimeError as e:
        return JSONResponse(status_code=400, content={"detail": str(e)})

    if next_point is None:
        return {"status": "no_more_points", "collected": session.collected_points}

    return {
        "status": "skipped",
        "next_point": {"x": next_point.x, "y": next_point.y, "index": next_point.index},
    }


@app.post("/api/calibration/session/{floor}/pause")
async def calibration_pause(floor: int):
    """Pause calibration for a specific floor. Retains all progress."""
    session = _state.calibration_sessions.get(floor)
    if session is None:
        return JSONResponse(
            status_code=404,
            content={"detail": f"No calibration session for floor {floor}"},
        )

    try:
        session.pause()
    except RuntimeError as e:
        return JSONResponse(status_code=400, content={"detail": str(e)})

    return {"status": "paused", "floor": floor, "collected": session.collected_points}


@app.post("/api/calibration/session/{floor}/resume")
async def calibration_resume(floor: int):
    """Resume a paused calibration session."""
    session = _state.calibration_sessions.get(floor)
    if session is None:
        return JSONResponse(
            status_code=404,
            content={"detail": f"No calibration session for floor {floor}"},
        )

    try:
        session.resume()
    except RuntimeError as e:
        return JSONResponse(status_code=400, content={"detail": str(e)})

    return {"status": "resumed", "floor": floor, "collected": session.collected_points}


@app.post("/api/calibration/session/{floor}/cancel")
async def calibration_cancel(floor: int):
    """Cancel calibration and discard all collected data."""
    session = _state.calibration_sessions.get(floor)
    if session is None:
        return JSONResponse(
            status_code=404,
            content={"detail": f"No calibration session for floor {floor}"},
        )

    try:
        session.cancel()
    except RuntimeError as e:
        return JSONResponse(status_code=400, content={"detail": str(e)})

    _state.calibrating[floor] = False
    del _state.calibration_sessions[floor]
    return {"status": "cancelled", "floor": floor}


@app.get("/api/calibration/session/{floor}/progress")
async def calibration_progress(floor: int):
    """Get detailed calibration progress with ETA estimate."""
    session = _state.calibration_sessions.get(floor)
    if session is None:
        return JSONResponse(
            status_code=404,
            content={"detail": f"No calibration session for floor {floor}"},
        )

    progress = session.get_progress()
    return {
        "state": progress.state,
        "floor": progress.floor,
        "total_points": progress.total_points,
        "completed_points": progress.completed_points,
        "skipped_points": progress.skipped_points,
        "progress_pct": progress.progress_pct,
        "current_point": progress.current_point,
        "elapsed_s": progress.elapsed_s,
        "estimated_remaining_s": progress.estimated_remaining_s,
    }


@app.get("/api/calibration/session/{floor}/grid")
async def calibration_grid_overlay(floor: int):
    """Get grid state for dashboard floor plan overlay rendering."""
    session = _state.calibration_sessions.get(floor)
    if session is None:
        return JSONResponse(
            status_code=404,
            content={"detail": f"No calibration session for floor {floor}"},
        )

    return {"floor": floor, "grid": session.get_grid_overlay()}


@app.post("/api/calibration/session/{floor}/finish", response_model=CalibrationBuildResult)
async def calibration_finish(floor: int):
    """Finalize calibration, build fingerprint DB, and save to disk."""
    session = _state.calibration_sessions.get(floor)
    if session is None:
        return JSONResponse(
            status_code=404,
            content={"detail": f"No calibration session for floor {floor}"},
        )

    if session.collected_points == 0:
        return JSONResponse(
            status_code=400,
            content={"detail": "No data collected. Cannot build fingerprint DB."},
        )

    collected_data = session.finish()
    _state.calibrating[floor] = False

    try:
        db = build_and_save(
            floor_id=floor,
            collected_points=collected_data,
            db_dir=_state.db_dir,
            grid_resolution_m=session.grid_resolution_m,
        )
    except ValueError as e:
        return JSONResponse(status_code=400, content={"detail": str(e)})

    # Update the shared fingerprint DB reference
    if _state.fingerprint_db is not None:
        _state.fingerprint_db.floors[floor] = db.floors[floor]
    else:
        _state.fingerprint_db = db

    floor_db = db.floors[floor]
    floor_cfg = _state.house_config.get("floors", {}).get(
        floor, _state.house_config.get("floors", {}).get(str(floor), {})
    )
    dims = floor_cfg.get("dimensions", {})
    coverage = compute_coverage(
        floor_db, dims.get("width_m", 18.0), dims.get("depth_m", 10.5),
    )

    feature_dim = floor_db.features.shape[1] if floor_db.size > 0 else 0

    return CalibrationBuildResult(
        floor=floor,
        fingerprints_created=floor_db.size,
        coverage_pct=coverage,
        feature_dimension=feature_dim,
        db_path=str(_state.db_dir / f"floor_{floor}.npz"),
    )


@app.post("/api/calibration/zone-recal", response_model=ZoneRecalResultSchema)
async def zone_recalibration(body: ZoneRecalRequest):
    """Recalibrate a specific zone within a floor.

    Requires an existing fingerprint DB for the floor and collected
    calibration data for the zone (via a completed calibration session
    or direct data submission).
    """
    if _state.fingerprint_db is None or body.floor not in _state.fingerprint_db.floors:
        return JSONResponse(
            status_code=400,
            content={"detail": f"No fingerprint DB for floor {body.floor}. Run full calibration first."},
        )

    session = _state.calibration_sessions.get(body.floor)
    if session is None or session.collected_points == 0:
        return JSONResponse(
            status_code=400,
            content={"detail": "No collected data. Start a calibration session and collect zone data first."},
        )

    collected_data = session.get_collected_data()

    zone = ZoneBounds(
        x_min=body.x_min,
        x_max=body.x_max,
        y_min=body.y_min,
        y_max=body.y_max,
    )

    floor_db = _state.fingerprint_db.floors[body.floor]
    result = recalibrate_zone(floor_db, zone, collected_data)

    # Save updated DB
    _state.fingerprint_db.save(floor=body.floor)

    return ZoneRecalResultSchema(
        floor=body.floor,
        points_replaced=result.points_removed,
        points_added=result.points_added,
        total_fingerprints=result.total_fingerprints,
        zone_bounds={
            "x_min": zone.x_min,
            "x_max": zone.x_max,
            "y_min": zone.y_min,
            "y_max": zone.y_max,
        },
    )


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
            await websocket.close(code=1008, reason="Invalid floor filter")
            return

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
    """Build calibration status per floor from fingerprint DB."""
    result: list[CalibrationStatus] = []
    for floor_id in floors:
        fid = int(floor_id)
        fp_count = 0
        last_cal = None
        coverage = 0.0

        if _state.fingerprint_db and fid in _state.fingerprint_db.floors:
            floor_db = _state.fingerprint_db.floors[fid]
            meta = floor_db.get_metadata()
            fp_count = meta.num_fingerprints
            last_cal = meta.calibration_timestamp if fp_count > 0 else None

            floor_cfg = floors.get(fid, floors.get(str(fid), {}))
            dims = floor_cfg.get("dimensions", {})
            if fp_count > 0:
                coverage = compute_coverage(
                    floor_db, dims.get("width_m", 18.0), dims.get("depth_m", 10.5),
                )

        result.append(
            CalibrationStatus(
                floor=fid,
                is_calibrated=fp_count > 0,
                fingerprint_count=fp_count,
                last_calibrated_at=last_cal,
                coverage_pct=coverage,
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
