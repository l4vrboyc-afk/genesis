"""Profile route — POST /api/v1/profile.

Dynamically switches the active trading profile (DEFAULT, SCALPER, BREAKOUT,
DAYTRADER) at runtime and broadcasts the change to all connected WebSocket
dashboard clients.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter
from loguru import logger

from ..schemas import ProfileChangeRequest, WSEventPayload
from ..ws_manager import ws_manager

router = APIRouter()
_app_store: Any = None


@router.post("", tags=["profile"])
async def change_profile(req: ProfileChangeRequest) -> dict:
    """Dynamically switch the active trading profile.

    Accepts a ``ProfileChangeRequest`` with the target profile and updates
    the orchestrator's strategy selector.  A ``PROFILE_CHANGED`` WebSocket
    event is broadcast to all connected dashboard clients so the UI updates
    immediately.

    Only switches strategy logic — MT5 account and server are never changed.
    All profiles (default, scalper, breakout, daytrader) trade on the same
    MetaQuotes-Demo account.
    """
    from fastapi import HTTPException

    try:
        orch = _app_store.state.orchestrator
        new_profile = req.new_profile

        # Update the strategy selector's profile
        if hasattr(orch.strategy_selector, "set_profile"):
            orch.strategy_selector.set_profile(new_profile.value)
        elif hasattr(orch.strategy_selector, "profile"):
            orch.strategy_selector.profile = new_profile.value

        # Also update settings so the change sticks on the next cycle
        from bot.config import settings as bot_settings
        bot_settings.active_profile = new_profile.value

        logger.info(f"[PROFILE] Profile switched to {new_profile.value} via dashboard")

        # ── Fetch MT5 account info so the UI shows login / server / balance ──
        account_info = None
        try:
            # Use the orchestrator's MT5 executor to get account info
            import MetaTrader5 as mt5
            account_info = await orch._mt5.submit(mt5.account_info)
        except Exception as e:
            logger.warning(f"Profile switch: could not fetch MT5 account info: {e}")

        # Broadcast to all connected WebSocket clients
        await ws_manager.broadcast(
            WSEventPayload(
                event_type="PROFILE_CHANGED",
                timestamp=datetime.now(timezone.utc).isoformat(),
                data={
                    "new_profile": new_profile.value,
                    "account_login": getattr(account_info, "login", 0) if account_info else 0,
                    "server": getattr(account_info, "server", "MetaQuotes-Demo") if account_info else "MetaQuotes-Demo",
                },
            )
        )

        return {
            "status": "success",
            "active_profile": new_profile.value,
            "account_login": getattr(account_info, "login", 0) if account_info else 0,
            "server": getattr(account_info, "server", "MetaQuotes-Demo") if account_info else "MetaQuotes-Demo",
            "balance": getattr(account_info, "balance", 0.0) if account_info else 0.0,
            "equity": getattr(account_info, "equity", 0.0) if account_info else 0.0,
        }
    except Exception as exc:
        logger.error(f"Profile switch failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


def register_routes(app, orchestrator=None, db=None):  # noqa: ANN001
    """Register the profile route under the /api/v1 prefix."""
    global _app_store
    _app_store = app
    app.include_router(router, prefix="/api/v1/profile")
