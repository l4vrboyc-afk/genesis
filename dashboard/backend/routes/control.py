"""Control route — POST /api/control."""
from __future__ import annotations

import asyncio
import os
from typing import Any

from fastapi import APIRouter
from loguru import logger

from ..models.responses import ControlRequest, ControlResponse

router = APIRouter()
_app_store: Any = None


async def _request_shutdown(app: Any, delay: float = 0.4) -> None:
    """Trigger the bot's normal graceful-exit path so the launcher's
    monitor detects the process exit and reloads the webview on the
    profile picker.

    main.py runs all services under ``asyncio.gather`` — uvicorn, the
    orchestrator's start() task, and (optionally) Discord. For the
    process to exit so the launcher can return to the picker, every
    gathered task must complete:

      * uvicorn      — set ``should_exit`` so serve() returns.
      * discord      — close the client so start() returns (when enabled).
      * orchestrator — cancel the start() task if the user clicked before
        startup finished (e.g. stuck in MT5 connect); otherwise gather()
        would block forever.

    The canonical orchestrator teardown (loop cancel + MT5 disconnect +
    executor stop) runs in main.py's ``finally`` block once gather()
    returns — this helper only unblocks gather(). If a blocking MT5
    call then keeps the process alive past the watchdog deadline,
    ``_shutdown_watchdog`` force-exits so the user is never stuck.
    A short delay lets the HTTP response flush to the client.
    Any error is swallowed so a fire-and-forget task can never surface
    as a bridge error.
    """
    try:
        await asyncio.sleep(delay)
        uv = getattr(app.state, "uvicorn_server", None)
        if uv is not None:
            uv.should_exit = True
        try:
            bot = getattr(app.state, "discord_bot", None)
            if bot is not None and hasattr(bot, "is_closed") and not bot.is_closed():
                await asyncio.wait_for(bot.close(), timeout=3.0)
        except asyncio.TimeoutError:
            logger.warning("discord.close() timed out during switch_profile — continuing")
        except Exception as exc:
            logger.error(f"discord.close() during switch_profile failed: {exc}")
        t = getattr(app.state, "orchestrator_task", None)
        if t is not None and not t.done():
            t.cancel()
    except Exception as exc:  # never raise out of a fire-and-forget task
        logger.error(f"switch_profile shutdown task failed: {exc}")


async def _shutdown_watchdog(timeout: float = 8.0) -> None:
    """Force-exit the process if graceful shutdown hasn't completed.

    On a clean exit the event loop (and this sleep) is cancelled as the
    process winds down, so the watchdog is a no-op. It only fires when a
    blocking MT5 C call — typically a mid-flight ``connect()`` that
    ``orchestrator.stop()`` then waits on inside main.py's finally —
    keeps the loop alive past the deadline, so the user is never stuck
    waiting to return to the profile picker.
    """
    try:
        await asyncio.sleep(timeout)
    except asyncio.CancelledError:
        return
    logger.warning("switch_profile: graceful shutdown timed out — forcing process exit")
    os._exit(0)


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
            picker = os.environ.get("GENESIS_PICKER_URL")
            if not picker:
                raise HTTPException(
                    status_code=503,
                    detail="Not launched from GUI — no picker available",
                )
            await notification_manager.notify_alert(
                "🔄 Switching profile via Web Dashboard — bot stopping", "system"
            )
            # Stop the bot so the launcher's monitor detects the process
            # exit and reloads the webview on the profile picker. The
            # response (with picker_url) is returned first; the shutdown
            # runs after a short delay so it flushes to the client. The
            # watchdog force-exits only if a blocking MT5 call keeps the
            # process alive past its deadline.
            asyncio.create_task(_request_shutdown(_app_store))
            asyncio.create_task(_shutdown_watchdog())
            return {
                "status": "success",
                "message": "Bot stopping — returning to profile selector",
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
