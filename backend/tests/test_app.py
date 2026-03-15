"""Tests for the FastAPI application (REST endpoints + WebSocket)."""

import asyncio
import time

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.server.app import app, _state


# ── Fixtures ────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_state():
    """Reset application state before each test."""
    _state.start_time = time.time()
    _state.house_config = {
        "floors": {
            1: {
                "name": "Ground Floor",
                "tx_channel": 1,
                "dimensions": {"width_m": 15.0, "depth_m": 12.0, "height_m": 2.7},
                "rooms": [{"name": "Kitchen"}, {"name": "Living Room"}],
            },
            2: {
                "name": "Second Floor",
                "tx_channel": 6,
                "dimensions": {"width_m": 15.0, "depth_m": 12.0, "height_m": 2.7},
                "rooms": [{"name": "Bedroom"}],
            },
            3: {
                "name": "Third Floor",
                "tx_channel": 11,
                "dimensions": {"width_m": 12.0, "depth_m": 10.0, "height_m": 2.4},
                "rooms": [{"name": "Office"}],
            },
        }
    }
    _state.calibrating = {1: False, 2: False, 3: False}
    _state.tracking_fps = 0.0
    yield


@pytest.fixture
def client():
    """Synchronous test client for REST endpoints."""
    return TestClient(app)


# ── GET /health ─────────────────────────────────────────────────


class TestHealth:
    def test_returns_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert "uptime_s" in body

    def test_uptime_increases(self, client):
        _state.start_time = time.time() - 42.0
        resp = client.get("/health")
        assert resp.json()["uptime_s"] >= 42.0


# ── GET /api/status ─────────────────────────────────────────────


class TestSystemStatus:
    def test_returns_200(self, client):
        resp = client.get("/api/status")
        assert resp.status_code == 200

    def test_online_flag(self, client):
        body = client.get("/api/status").json()
        assert body["online"] is True

    def test_contains_sensors(self, client):
        body = client.get("/api/status").json()
        # 3 floors × (1 TX + 3 RX) = 12 sensors
        assert len(body["sensors"]) == 12

    def test_contains_calibration(self, client):
        body = client.get("/api/status").json()
        assert len(body["calibration"]) == 3
        floors = {c["floor"] for c in body["calibration"]}
        assert floors == {1, 2, 3}

    def test_active_connections_zero(self, client):
        body = client.get("/api/status").json()
        assert body["active_connections"] == 0


# ── GET /api/sensors ────────────────────────────────────────────


class TestSensors:
    def test_returns_list(self, client):
        resp = client.get("/api/sensors")
        assert resp.status_code == 200
        sensors = resp.json()
        assert isinstance(sensors, list)
        assert len(sensors) == 12

    def test_sensor_roles(self, client):
        sensors = client.get("/api/sensors").json()
        tx_count = sum(1 for s in sensors if s["role"] == "tx")
        rx_count = sum(1 for s in sensors if s["role"] == "rx")
        assert tx_count == 3  # 1 per floor
        assert rx_count == 9  # 3 per floor

    def test_sensor_fields(self, client):
        sensor = client.get("/api/sensors").json()[0]
        assert "mac" in sensor
        assert "floor" in sensor
        assert "online" in sensor
        assert "rssi" in sensor
        assert "packets_per_sec" in sensor


# ── GET /api/calibration/status ─────────────────────────────────


class TestCalibrationStatus:
    def test_returns_list(self, client):
        resp = client.get("/api/calibration/status")
        assert resp.status_code == 200
        cals = resp.json()
        assert len(cals) == 3

    def test_not_calibrated_by_default(self, client):
        cals = client.get("/api/calibration/status").json()
        for cal in cals:
            assert cal["is_calibrated"] is False
            assert cal["fingerprint_count"] == 0
            assert cal["coverage_pct"] == 0.0


# ── POST /api/calibration/start ─────────────────────────────────


class TestCalibrationStart:
    def test_start_calibration(self, client):
        resp = client.post("/api/calibration/start?floor=1")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "calibrating"
        assert body["floor"] == 1

    def test_already_calibrating(self, client):
        client.post("/api/calibration/start?floor=2")
        resp = client.post("/api/calibration/start?floor=2")
        assert resp.status_code == 200
        assert resp.json()["status"] == "already_calibrating"

    def test_invalid_floor(self, client):
        resp = client.post("/api/calibration/start?floor=99")
        assert resp.status_code == 400
        assert "not configured" in resp.json()["detail"]


# ── WebSocket /ws/tracking ──────────────────────────────────────


class TestWebSocket:
    def test_connect_and_disconnect(self, client):
        with client.websocket_connect("/ws/tracking") as ws:
            assert _state.ws_manager.active_connections == 1
        # After context exit, disconnect fires
        assert _state.ws_manager.active_connections == 0

    def test_multiple_connections(self, client):
        with client.websocket_connect("/ws/tracking"):
            assert _state.ws_manager.active_connections == 1
            with client.websocket_connect("/ws/tracking"):
                assert _state.ws_manager.active_connections == 2
            assert _state.ws_manager.active_connections == 1

    def test_broadcast_reaches_client(self, client):
        """Verify broadcast sends data to connected client."""
        import asyncio

        with client.websocket_connect("/ws/tracking") as ws:
            frame_data = {
                "timestamp": time.time(),
                "floor": 1,
                "people": [],
                "occupancy_estimate": 0,
                "occupancy_confidence": 0.0,
                "zone_signal_quality": {},
            }
            # Run broadcast in the event loop
            loop = asyncio.new_event_loop()
            loop.run_until_complete(_state.ws_manager.broadcast(frame_data))
            loop.close()

            data = ws.receive_json()
            assert data["floor"] == 1
            assert data["people"] == []


# ── CORS ────────────────────────────────────────────────────────


class TestCORS:
    def test_cors_headers_present(self, client):
        resp = client.options(
            "/health",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert "access-control-allow-origin" in resp.headers


# ── Error handling ──────────────────────────────────────────────


class TestErrorHandling:
    def test_404_for_unknown_route(self, client):
        resp = client.get("/api/nonexistent")
        assert resp.status_code == 404
