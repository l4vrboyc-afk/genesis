"""WebSocket route — WS /ws/feed.

Full-duplex stream that pushes tick updates, trade entries, state transitions,
and system logs directly to the dashboard.  Also handles simple client pings
so the connection stays alive behind proxies.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from loguru import logger

from ..schemas import WSEventPayload
from ..ws_manager import ws_manager

router = APIRouter()
_app_store: Any = None


@router.websocket("/feed")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """Real-time streaming endpoint for the frontend UI.

    Flow:
        1. Accept the connection and register with ``ws_manager``.
        2. Send an immediate ``HANDSHAKE`` event with server status.
        3. Keep the connection open, receiving client pings.
        4. On disconnect, clean up via ``ws_manager.disconnect()``.
    """
    await ws_manager.connect(websocket)
    try:
        # ── 1. Send connection handshake snapshot ────────────────────
        orch = _app_store.state.orchestrator
        init_event = WSEventPayload(
            event_type="HANDSHAKE",
            timestamp=datetime.now(timezone.utc).isoformat(),
            data={
                "server_status": "ONLINE",
                "active_profile": orch.strategy_selector.profile
                if hasattr(orch.strategy_selector, "profile")
                else "default",
                "msg": "Connected to Genesis Live Broadcast Engine",
            },
        )
        await websocket.send_json(init_event.model_dump())

        # ── 2. Listen for client messages (pings, etc.) ─────────────
        while True:
            data = await websocket.receive_text()
            # Simple ping / keepalive — respond with a pong
            if data == "ping":
                pong = WSEventPayload(
                    event_type="PONG",
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    data={"ts": datetime.now(timezone.utc).isoformat()},
                )
                await websocket.send_json(pong.model_dump())

    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception as exc:
        logger.error(f"WebSocket error: {exc}")
        ws_manager.disconnect(websocket)


def register_routes(app, orchestrator=None, db=None):  # noqa: ANN001
    """Register the WebSocket route under the /ws prefix.

    Called from ``dashboard.backend.main.create_app()``.
    """
    global _app_store
    _app_store = app
    app.include_router(router, prefix="/ws")
