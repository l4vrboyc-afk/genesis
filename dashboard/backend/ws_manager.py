"""Real-Time WebSocket Connection & Broadcasting Engine.

Manages active browser WebSocket connections and supports clean fan-out
broadcasting so multiple UI tabs or devices receive synchronised real-time
updates without blocking the core trading thread.

Usage::

    from dashboard.backend.ws_manager import ws_manager

    # From anywhere in the app (orchestrator, routes, etc.):
    await ws_manager.broadcast(WSEventPayload(
        event_type="TRADE_OPEN",
        timestamp=datetime.utcnow().isoformat(),
        data={"ticket": 1234, "symbol": "EURUSD"},
    ))
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from fastapi import WebSocket

from .schemas import WSEventPayload

logger = logging.getLogger("GenesisWSManager")


class ConnectionManager:
    """Manages active browser WebSocket connections with fan-out broadcasting.

    Automatically cleans up disconnected clients on the next broadcast
    cycle so no stale sockets accumulate in the pool.
    """

    def __init__(self) -> None:
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        """Accept an incoming browser client and add it to the tracking pool.

        Args:
            websocket: The FastAPI WebSocket connection to register.
        """
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(
            "🌐 UI Client Connected. "
            f"Total Clients: {len(self.active_connections)}"
        )

    def disconnect(self, websocket: WebSocket) -> None:
        """Remove a client on disconnect (idempotent)."""
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.info(
                "🔌 UI Client Disconnected. "
                f"Remaining: {len(self.active_connections)}"
            )

    async def broadcast(self, event: WSEventPayload) -> None:
        """Push a real-time JSON payload to all connected UI clients concurrently.

        Dead sockets are detected during send and removed automatically —
        a single stuck client cannot block the rest of the broadcast.

        Args:
            event: The typed event payload to send to every connected client.
        """
        if not self.active_connections:
            return

        payload: Dict[str, Any] = event.model_dump()
        disconnected_clients: List[WebSocket] = []

        for connection in self.active_connections:
            try:
                await connection.send_json(payload)
            except Exception as exc:
                logger.error(
                    f"Error streaming to client: {exc}"
                )
                disconnected_clients.append(connection)

        # Clean up dead sockets after iteration (avoid modifying list in place)
        for dead_client in disconnected_clients:
            self.disconnect(dead_client)

    async def broadcast_json(self, payload: Dict[str, Any]) -> None:
        """Convenience method to broadcast a raw dict without a WSEventPayload wrapper.

        Useful for high-frequency events (e.g. ticks) where the schema
        overhead is undesirable.
        """
        if not self.active_connections:
            return

        disconnected_clients: List[WebSocket] = []
        for connection in self.active_connections:
            try:
                await connection.send_json(payload)
            except Exception as exc:
                logger.error(f"Error streaming to client: {exc}")
                disconnected_clients.append(connection)

        for dead_client in disconnected_clients:
            self.disconnect(dead_client)

    @property
    def client_count(self) -> int:
        """Number of currently connected WebSocket clients."""
        return len(self.active_connections)


# Global singleton — imported by route modules and the orchestrator.
ws_manager = ConnectionManager()
