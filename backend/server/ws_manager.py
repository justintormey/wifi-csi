"""WebSocket connection manager for real-time tracking broadcast.

Maintains a set of connected WebSocket clients and broadcasts TrackingFrame
payloads at up to 10 Hz.  Handles connection lifecycle, automatic cleanup
of dead connections, backpressure (frame-dropping for slow clients), and
optional per-client floor filtering.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

# Maximum time (seconds) to wait for a single client send before dropping
# the frame for that client.  At 10 Hz we have 100 ms between frames;
# a 50 ms budget leaves headroom for the gather + next frame.
_SEND_TIMEOUT = 0.05


class _ClientState:
    """Per-client metadata tracked alongside the WebSocket."""

    __slots__ = ("ws", "floor_filter", "dropped_frames")

    def __init__(self, ws: WebSocket, floor_filter: Optional[set[int]] = None):
        self.ws = ws
        self.floor_filter = floor_filter  # None = all floors
        self.dropped_frames: int = 0


class WebSocketManager:
    """Manages WebSocket client connections and broadcasts tracking data.

    Features:
    - Concurrent broadcast to all clients (slow clients don't block fast ones)
    - Backpressure: frames are dropped for clients that can't receive within
      the send timeout (default 50 ms)
    - Per-client floor filter: clients can subscribe to specific floors
    - Connection count metric
    """

    def __init__(self, send_timeout: float = _SEND_TIMEOUT) -> None:
        self._clients: dict[WebSocket, _ClientState] = {}
        self._lock = asyncio.Lock()
        self._send_timeout = send_timeout
        self._total_broadcasts: int = 0
        self._total_dropped: int = 0

    # ── Metrics ────────────────────────────────────────────────────

    @property
    def active_connections(self) -> int:
        """Number of currently connected clients."""
        return len(self._clients)

    @property
    def total_broadcasts(self) -> int:
        """Total number of broadcast calls made."""
        return self._total_broadcasts

    @property
    def total_dropped(self) -> int:
        """Total frames dropped across all clients due to backpressure."""
        return self._total_dropped

    def metrics(self) -> dict[str, Any]:
        """Snapshot of manager metrics for monitoring."""
        return {
            "active_connections": self.active_connections,
            "total_broadcasts": self._total_broadcasts,
            "total_dropped": self._total_dropped,
        }

    # ── Connection lifecycle ───────────────────────────────────────

    async def connect(
        self,
        websocket: WebSocket,
        floor_filter: Optional[set[int]] = None,
    ) -> None:
        """Accept and register a new WebSocket client.

        Args:
            websocket: The WebSocket to accept.
            floor_filter: Optional set of floor numbers to subscribe to.
                          None means receive data for all floors.
        """
        await websocket.accept()
        async with self._lock:
            self._clients[websocket] = _ClientState(websocket, floor_filter)
        logger.info(
            "WebSocket client connected (%s), floors=%s. Total: %d",
            websocket.client,
            floor_filter or "all",
            self.active_connections,
        )

    async def disconnect(self, websocket: WebSocket) -> None:
        """Remove a WebSocket client from the active set."""
        async with self._lock:
            client = self._clients.pop(websocket, None)
        dropped = client.dropped_frames if client else 0
        logger.info(
            "WebSocket client disconnected (%s), dropped_frames=%d. Total: %d",
            websocket.client,
            dropped,
            self.active_connections,
        )

    def set_floor_filter(
        self, websocket: WebSocket, floors: Optional[set[int]]
    ) -> None:
        """Update the floor filter for an existing client.

        Args:
            websocket: The client connection.
            floors: Set of floor numbers, or None for all floors.
        """
        client = self._clients.get(websocket)
        if client is not None:
            client.floor_filter = floors

    # ── Broadcast ──────────────────────────────────────────────────

    async def broadcast(
        self,
        data: dict[str, Any],
        floor: Optional[int] = None,
    ) -> None:
        """Send JSON data to all connected (and subscribed) clients.

        Uses concurrent sends with per-client timeouts for backpressure.
        Clients that fail or timeout have the frame dropped (not buffered).
        Persistently broken clients are removed.

        Args:
            data: JSON-serializable payload.
            floor: If provided, only send to clients subscribed to this floor.
        """
        if not self._clients:
            return

        self._total_broadcasts += 1

        async with self._lock:
            targets = list(self._clients.values())

        # Filter by floor subscription
        if floor is not None:
            targets = [
                c
                for c in targets
                if c.floor_filter is None or floor in c.floor_filter
            ]

        if not targets:
            return

        # Send concurrently with timeout-based backpressure
        results = await asyncio.gather(
            *(self._try_send(c, data) for c in targets),
            return_exceptions=True,
        )

        # Clean up broken clients
        stale: list[WebSocket] = []
        for client, result in zip(targets, results):
            if result is True:
                continue
            elif result == "dropped":
                client.dropped_frames += 1
                self._total_dropped += 1
            else:
                # Connection is broken — schedule removal
                stale.append(client.ws)

        if stale:
            async with self._lock:
                for ws in stale:
                    self._clients.pop(ws, None)
                    logger.debug("Removed broken WebSocket client: %s", ws.client)

    async def _try_send(
        self, client: _ClientState, data: dict[str, Any]
    ) -> bool | str:
        """Attempt to send data to a single client with timeout.

        Returns:
            True on success, "dropped" on timeout, or raises on broken connection.
        """
        try:
            await asyncio.wait_for(
                client.ws.send_json(data),
                timeout=self._send_timeout,
            )
            return True
        except asyncio.TimeoutError:
            return "dropped"
        except (WebSocketDisconnect, RuntimeError, OSError):
            return "broken"
        except Exception:
            return "broken"

    async def broadcast_frame(self, frame: Any) -> None:
        """Broadcast a Pydantic model (e.g. TrackingFrame) as JSON.

        Extracts the floor number from the frame for per-client filtering.
        """
        if hasattr(frame, "model_dump"):
            data = frame.model_dump()
        else:
            data = dict(frame)

        floor = data.get("floor")
        await self.broadcast(data, floor=floor)

    # ── Shutdown ───────────────────────────────────────────────────

    async def close_all(self) -> None:
        """Gracefully close all connections (used during shutdown)."""
        async with self._lock:
            for client in list(self._clients.values()):
                try:
                    await client.ws.close()
                except Exception:
                    pass
            self._clients.clear()
        logger.info("All WebSocket connections closed.")
