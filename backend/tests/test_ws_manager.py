"""Tests for the WebSocket connection manager."""

import asyncio

import pytest

from backend.server.ws_manager import WebSocketManager


# ── Mock WebSocket ──────────────────────────────────────────────


class MockWebSocket:
    """Minimal WebSocket mock for unit testing the manager."""

    def __init__(self, *, fail_send: bool = False):
        self.accepted = False
        self.closed = False
        self.sent: list = []
        self.fail_send = fail_send
        self.client = ("127.0.0.1", 9999)

    async def accept(self):
        self.accepted = True

    async def close(self):
        self.closed = True

    async def send_json(self, data):
        if self.fail_send:
            raise RuntimeError("connection lost")
        self.sent.append(data)


# ── Tests ───────────────────────────────────────────────────────


@pytest.fixture
def manager():
    return WebSocketManager()


class TestConnect:
    @pytest.mark.asyncio
    async def test_accept_and_register(self, manager):
        ws = MockWebSocket()
        await manager.connect(ws)
        assert ws.accepted
        assert manager.active_connections == 1

    @pytest.mark.asyncio
    async def test_multiple_connections(self, manager):
        ws1, ws2 = MockWebSocket(), MockWebSocket()
        await manager.connect(ws1)
        await manager.connect(ws2)
        assert manager.active_connections == 2


class TestDisconnect:
    @pytest.mark.asyncio
    async def test_remove_connection(self, manager):
        ws = MockWebSocket()
        await manager.connect(ws)
        await manager.disconnect(ws)
        assert manager.active_connections == 0

    @pytest.mark.asyncio
    async def test_disconnect_unknown_is_safe(self, manager):
        ws = MockWebSocket()
        await manager.disconnect(ws)  # Should not raise
        assert manager.active_connections == 0


class TestBroadcast:
    @pytest.mark.asyncio
    async def test_broadcast_to_all(self, manager):
        ws1, ws2 = MockWebSocket(), MockWebSocket()
        await manager.connect(ws1)
        await manager.connect(ws2)

        await manager.broadcast({"msg": "hello"})
        assert {"msg": "hello"} in ws1.sent
        assert {"msg": "hello"} in ws2.sent

    @pytest.mark.asyncio
    async def test_broadcast_no_clients(self, manager):
        # Should not raise
        await manager.broadcast({"msg": "nobody home"})

    @pytest.mark.asyncio
    async def test_stale_client_removed(self, manager):
        good = MockWebSocket()
        bad = MockWebSocket(fail_send=True)
        await manager.connect(good)
        await manager.connect(bad)

        await manager.broadcast({"msg": "test"})

        assert manager.active_connections == 1
        assert {"msg": "test"} in good.sent

    @pytest.mark.asyncio
    async def test_broadcast_frame_pydantic(self, manager):
        """Test broadcast_frame with a Pydantic-like object."""

        class FakeModel:
            def model_dump(self):
                return {"floor": 1, "people": []}

        ws = MockWebSocket()
        await manager.connect(ws)
        await manager.broadcast_frame(FakeModel())
        assert ws.sent == [{"floor": 1, "people": []}]


class TestCloseAll:
    @pytest.mark.asyncio
    async def test_closes_everything(self, manager):
        ws1, ws2 = MockWebSocket(), MockWebSocket()
        await manager.connect(ws1)
        await manager.connect(ws2)

        await manager.close_all()

        assert ws1.closed
        assert ws2.closed
        assert manager.active_connections == 0
