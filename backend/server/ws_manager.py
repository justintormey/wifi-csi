"""WebSocket connection manager for real-time tracking broadcast.

Maintains a set of connected WebSocket clients and broadcasts TrackingFrame
payloads at up to 10 Hz.  Handles connection lifecycle, automatic cleanup
of dead connections, and JSON serialization via Pydantic.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)


class WebSocketManager:
    """Manages WebSocket client connections and broadcasts tracking data."""

    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    @property
    def active_connections(self) -> int:
        """Number of currently connected clients."""
        return len(self._connections)

    async def connect(self, websocket: WebSocket) -> None:
        """Accept and register a new WebSocket client."""
        await websocket.accept()
        async with self._lock:
            self._connections.add(websocket)
        logger.info(
            "WebSocket client connected (%s). Total: %d",
            websocket.client,
            self.active_connections,
        )

    async def disconnect(self, websocket: WebSocket) -> None:
        """Remove a WebSocket client from the active set."""
        async with self._lock:
            self._connections.discard(websocket)
        logger.info(
            "WebSocket client disconnected (%s). Total: %d",
            websocket.client,
            self.active_connections,
        )

    async def broadcast(self, data: dict[str, Any]) -> None:
        """Send JSON data to all connected clients.

        Silently removes clients that fail to receive (broken pipe, etc.).
        """
        if not self._connections:
            return

        async with self._lock:
            stale: list[WebSocket] = []
            for ws in self._connections:
                try:
                    await ws.send_json(data)
                except (WebSocketDisconnect, RuntimeError, Exception):
                    stale.append(ws)

            for ws in stale:
                self._connections.discard(ws)
                logger.debug("Removed stale WebSocket client: %s", ws.client)

    async def broadcast_frame(self, frame: Any) -> None:
        """Broadcast a Pydantic model (e.g. TrackingFrame) as JSON.

        Calls ``model_dump()`` if available, otherwise falls back to dict cast.
        """
        if hasattr(frame, "model_dump"):
            data = frame.model_dump()
        else:
            data = dict(frame)
        await self.broadcast(data)

    async def close_all(self) -> None:
        """Gracefully close all connections (used during shutdown)."""
        async with self._lock:
            for ws in list(self._connections):
                try:
                    await ws.close()
                except Exception:
                    pass
            self._connections.clear()
        logger.info("All WebSocket connections closed.")
