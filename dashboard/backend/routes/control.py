"""Control route — POST /api/control."""
from __future__ import annotations

from fastapi import APIRouter
from loguru import logger

from ..models.responses import ControlRequest, ControlResponse

router = APIRouter()
_app_store: Any = None


@router.post(
    "",
    response_model=ControlResponse,
    tags=["control"],
)
async def bot_control(req: ControlRequest):
    """Control bot execution (Pause/Resume/Close All/Override Regime)."""
    from bot.config.settings import MarketRegime
    from bot.notifications.notification_manager import notification_manager
    from fastapi import HTTPException

    orch = _app_store.state.orchestrator
    action = req.action.lower()

    try:
        if action == "pause":
            orch.pause()
            await notification_manager.notify_alert(
                "⏸️ Trading paused via Web Dashboard", "system"
            )
            return {"status": "success", "message": "Bot paused"}

        elif action == "resume":
            orch.resume()
            await notification_manager.notify_alert(
                "▶️ Trading resumed via Web Dashboard", "system"
            )
            return {"status": "success", "message": "Bot resumed"}

        elif action == "close_all":
            closed = await orch.close_all_trades()
            await notification_manager.notify_alert(
                f"🧹 Closed {len(closed)} open position(s) via Web Dashboard",
                "warning",
            )
            return {
                "status": "success",
                "message": f"Closed {len(closed)} position(s)",
            }

        elif action == "force_regime":
            if not req.regime:
                raise HTTPException(
                    status_code=400,
                    detail="Regime name required for override",
                )
            try:
                regime_enum = MarketRegime(req.regime.lower())
                orch.strategy_selector.force_regime(regime_enum)
                return {
                    "status": "success",
                    "message": f"Forced regime to {regime_enum.value}",
                }
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid market regime: {req.regime}",
                )

        elif action == "release_regime":
            if not hasattr(orch, "release_forced_regime"):
                raise HTTPException(
                    status_code=503,
                    detail="release_forced_regime unavailable on this build",
                )
            orch.release_forced_regime()
            await notification_manager.notify_alert(
                "🔓 Forced regime released via Web Dashboard", "system"
            )
            return {
                "status": "success",
                "message": "Regime auto-detection resumed",
            }

        elif action == "switch_profile":
            import os as _os
            picker = _os.environ.get("GENESIS_PICKER_URL")
            if not picker:
                raise HTTPException(
                    status_code=503,
                    detail="Not launched from GUI — no picker available",
                )
            await notification_manager.notify_alert(
                "🔄 Switching profile via Web Dashboard", "system"
            )
            return {
                "status": "success",
                "message": "Switch profile — navigate to picker",
                "picker_url": picker,
            }

        elif action == "release_kill_switches":
            rm = orch.risk_manager
            rm.release_daily_dd_trip()
            rm.release_equity_floor_trip()
            cleared = (
                orch.release_kill_switch_engagement()
                if hasattr(orch, "release_kill_switch_engagement")
                else False
            )
            if cleared:
                await notification_manager.notify_alert(
                    "🟢 Kill switches + engagement latch released via Web Dashboard",
                    "system",
                )
            orch.resume()
            await _app_store.state.orchestrator.db.set_state(
                "daily_dd_tripped_date", ""
            )
            return {
                "status": "success",
                "message": "Kill switches released and bot resumed",
                "engagement_cleared": cleared,
            }

        else:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown control action: {action}",
            )

    except Exception as e:
        logger.error(f"Control action {req.action} failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def register_routes(app, orchestrator=None, db=None):  # noqa: ANN001
    global _app_store
    _app_store = app
    app.include_router(router, prefix="/api/control")
