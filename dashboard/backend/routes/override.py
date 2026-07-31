"""Override route — POST /api/v1/override.

Emergency manual control: force lockouts, release locks, force regimes, or
close all positions.  All actions are broadcast to WebSocket clients and
logged to Discord (when enabled).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter
from loguru import logger

from ..schemas import ManualOverrideRequest, WSEventPayload
from ..ws_manager import ws_manager

router = APIRouter()
_app_store: Any = None


@router.post("", tags=["override"])
async def manual_override(req: ManualOverrideRequest) -> dict:
    """Execute an emergency override action.

    Supported actions:

    - ``FORCE_LOCKDOWN`` — Engage a ``NEWS_EVENT`` regime, blocking all new
      entries (equivalent to emergency pause).
    - ``RELEASE_LOCK`` — Clear any forced-regime override, resuming normal
      auto-detection.
    - ``FORCE_REGIME`` — Force a specific market regime (requires ``regime``
      field).  Valid values: trending, ranging, volatile, dead, news_event.
    - ``CLOSE_ALL`` — Flatten all open positions immediately.
    """
    from bot.config.settings import MarketRegime
    from bot.notifications.notification_manager import notification_manager
    from fastapi import HTTPException

    try:
        orch = _app_store.state.orchestrator
        action = req.action.upper()

        if action == "FORCE_LOCKDOWN":
            # Force a NEWS_EVENT regime to halt all entries
            orch.strategy_selector.force_regime(MarketRegime.NEWS_EVENT)
            orch.pause()
            await notification_manager.notify_alert(
                f"🔒 FORCE LOCKDOWN engaged via dashboard: {req.reason}",
                "critical",
            )
            result_msg = "Lockdown engaged — bot paused, regime forced to NEWS_EVENT"

        elif action == "RELEASE_LOCK":
            # Release forced regime + resume
            orch.release_forced_regime()
            orch.resume()
            await notification_manager.notify_alert(
                f"🔓 Lock released via dashboard: {req.reason}",
                "system",
            )
            result_msg = "Lock released — auto-detection resumed"

        elif action == "FORCE_REGIME":
            if not req.regime:
                raise HTTPException(
                    status_code=400,
                    detail="regime field required when action is FORCE_REGIME",
                )
            try:
                regime_enum = MarketRegime(req.regime.lower())
                orch.strategy_selector.force_regime(regime_enum)
                await notification_manager.notify_alert(
                    f"📊 Regime forced to {regime_enum.value} via dashboard: {req.reason}",
                    "warning",
                )
                result_msg = f"Regime forced to {regime_enum.value}"
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid regime: {req.regime}. Valid: trending, ranging, volatile, dead, news_event",
                )

        elif action == "CLOSE_ALL":
            closed = await orch.close_all_trades()
            await notification_manager.notify_alert(
                f"🧹 Emergency CLOSE ALL via dashboard ({len(closed)} positions): {req.reason}",
                "warning",
            )
            result_msg = f"Closed {len(closed)} position(s)"

        else:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown action: {action}. Valid: FORCE_LOCKDOWN, RELEASE_LOCK, FORCE_REGIME, CLOSE_ALL",
            )

        # Broadcast to all WebSocket clients
        await ws_manager.broadcast(
            WSEventPayload(
                event_type="OVERRIDE_TRIGGERED",
                timestamp=datetime.now(timezone.utc).isoformat(),
                data={
                    "action": action,
                    "reason": req.reason,
                    "result": result_msg,
                },
            )
        )

        return {
            "status": "SUCCESS",
            "action": action,
            "message": result_msg,
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Override action {req.action} failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


def register_routes(app, orchestrator=None, db=None):  # noqa: ANN001
    """Register the override route under the /api/v1 prefix."""
    global _app_store
    _app_store = app
    app.include_router(router, prefix="/api/v1/override")
