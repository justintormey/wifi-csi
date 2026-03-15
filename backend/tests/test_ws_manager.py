"""Tests for the WebSocket connection manager."""

import asyncio

import pytest

from backend.server.ws_manager import WebSocketManager


# ── Mock WebSocket ──────────────────────────────────────────────


class MockWebSocket:
    """Minimal WebSocket mock for unit testing the manager."""

    def __init__(self, *, fail_send: bool = False, slow_send: float = 0.0):
        self.accepted = False
        self.closed = False
        self.sent: list = []
        self.fail_send = fail_send
        self.slow_send = slow_send
        self.client = ("127.0.0.1", 9999)

    async def accept(self):
        self.accepted = True

    async def close(self):
        self.closed = True

    async def send_json(self, data):
        if self.fail_send:
            raise RuntimeError("connection lost")
        if self.slow_send > 0:
            await asyncio.sleep(self.slow_send)
        self.sent.append(data)


# ── Tests ───────────────────────────────────────────────────────


@pytest.fixture
def manager():
    return WebSocketManager(send_timeout=0.5)


@pytest.fixture
def fast_manager():
    """Manager with tight timeout for backpressure tests."""
    return WebSocketManager(send_timeout=0.02)


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

    @pytest.mark.asyncio
    async def test_connect_with_floor_filter(self, manager):
        ws = MockWebSocket()
        await manager.connect(ws, floor_filter={1, 2})
        assert manager.active_connections == 1


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

    @pytest.mark.asyncio
    async def test_broadcast_increments_counter(self, manager):
        ws = MockWebSocket()
        await manager.connect(ws)
        assert manager.total_broadcasts == 0
        await manager.broadcast({"a": 1})
        await manager.broadcast({"b": 2})
        assert manager.total_broadcasts == 2


class TestBackpressure:
    @pytest.mark.asyncio
    async def test_slow_client_gets_frame_dropped(self, fast_manager):
        """A client that takes longer than send_timeout gets its frame dropped."""
        fast = MockWebSocket()
        slow = MockWebSocket(slow_send=0.1)  # 100ms > 20ms timeout
        await fast_manager.connect(fast)
        await fast_manager.connect(slow)

        await fast_manager.broadcast({"data": "x"})

        # Fast client received it
        assert {"data": "x"} in fast.sent
        # Slow client did NOT receive it (frame dropped due to timeout)
        assert len(slow.sent) == 0
        # Dropped counter incremented
        assert fast_manager.total_dropped == 1
        # Slow client is still connected (not removed, just dropped)
        assert fast_manager.active_connections == 2

    @pytest.mark.asyncio
    async def test_broken_client_removed_not_just_dropped(self, fast_manager):
        """A client with a broken connection is removed entirely."""
        good = MockWebSocket()
        broken = MockWebSocket(fail_send=True)
        await fast_manager.connect(good)
        await fast_manager.connect(broken)

        await fast_manager.broadcast({"data": "y"})

        assert fast_manager.active_connections == 1
        assert {"data": "y"} in good.sent


class TestFloorFilter:
    @pytest.mark.asyncio
    async def test_floor_filter_sends_only_to_subscribed(self, manager):
        ws_all = MockWebSocket()
        ws_floor1 = MockWebSocket()
        ws_floor2 = MockWebSocket()

        await manager.connect(ws_all)  # all floors
        await manager.connect(ws_floor1, floor_filter={1})
        await manager.connect(ws_floor2, floor_filter={2})

        await manager.broadcast({"floor": 1, "data": "f1"}, floor=1)

        assert {"floor": 1, "data": "f1"} in ws_all.sent
        assert {"floor": 1, "data": "f1"} in ws_floor1.sent
        assert len(ws_floor2.sent) == 0  # Not subscribed to floor 1

    @pytest.mark.asyncio
    async def test_no_floor_sends_to_all(self, manager):
        ws_floor1 = MockWebSocket()
        ws_floor2 = MockWebSocket()

        await manager.connect(ws_floor1, floor_filter={1})
        await manager.connect(ws_floor2, floor_filter={2})

        await manager.broadcast({"msg": "global"})  # No floor specified

        assert {"msg": "global"} in ws_floor1.sent
        assert {"msg": "global"} in ws_floor2.sent

    @pytest.mark.asyncio
    async def test_set_floor_filter(self, manager):
        ws = MockWebSocket()
        await manager.connect(ws, floor_filter={1})

        # Initially subscribed to floor 1
        await manager.broadcast({"data": "a"}, floor=2)
        assert len(ws.sent) == 0

        # Update filter to include floor 2
        manager.set_floor_filter(ws, {1, 2})
        await manager.broadcast({"data": "b"}, floor=2)
        assert {"data": "b"} in ws.sent

    @pytest.mark.asyncio
    async def test_broadcast_frame_uses_floor_from_payload(self, manager):
        """broadcast_frame extracts floor from model for filtering."""

        class FakeFrame:
            def model_dump(self):
                return {"floor": 2, "people": []}

        ws_floor1 = MockWebSocket()
        ws_floor2 = MockWebSocket()
        await manager.connect(ws_floor1, floor_filter={1})
        await manager.connect(ws_floor2, floor_filter={2})

        await manager.broadcast_frame(FakeFrame())

        assert len(ws_floor1.sent) == 0
        assert ws_floor2.sent == [{"floor": 2, "people": []}]


class TestMetrics:
    @pytest.mark.asyncio
    async def test_metrics_snapshot(self, manager):
        ws = MockWebSocket()
        await manager.connect(ws)
        await manager.broadcast({"x": 1})

        m = manager.metrics()
        assert m["active_connections"] == 1
        assert m["total_broadcasts"] == 1
        assert m["total_dropped"] == 0


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
