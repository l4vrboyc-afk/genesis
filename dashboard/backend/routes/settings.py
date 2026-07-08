"""Settings route — GET /api/settings, POST /api/settings."""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter
from loguru import logger

from ..models.responses import (
    SettingsResponse,
    SettingsUpdateRequest,
    SettingsUpdateResponse,
)

router = APIRouter()
_app_store: Any = None


@router.get(
    "",
    response_model=SettingsResponse,
    tags=["settings"],
)
async def get_settings():
    """Retrieve current configuration settings."""
    from bot.config import settings as bs

    return {
        "max_risk_per_trade": bs.max_risk_per_trade,
        "max_daily_drawdown": bs.max_daily_drawdown,
        "max_open_positions": bs.max_open_positions,
        "trading_pairs": bs.trading_pairs,
        "paper_trading": bs.paper_trading,
        "bot_name": bs.bot_name,
    }


@router.post(
    "",
    response_model=SettingsUpdateResponse,
    tags=["settings"],
)
async def update_settings(req: SettingsUpdateRequest):
    """Update configurations dynamically."""
    from bot.config import settings as bs
    from fastapi import HTTPException

    try:
        orch = _app_store.state.orchestrator
        dba = orch.db

        if req.max_risk_per_trade is not None:
            bs.max_risk_per_trade = req.max_risk_per_trade
        if req.max_daily_drawdown is not None:
            bs.max_daily_drawdown = req.max_daily_drawdown
        if req.max_open_positions is not None:
            bs.max_open_positions = req.max_open_positions
        if req.trading_pairs is not None:
            bs.trading_pairs = req.trading_pairs
            # Re-enable pairs in MT5 Watch List (async -- routed through the
            # bound AsyncMt5Executor).
            await orch.mt5_conn.enable_all_pairs()
        if req.paper_trading is not None:
            bs.paper_trading = req.paper_trading

        # Persist to database state key-value store for durability across restarts
        await dba.set_state("max_risk_per_trade", str(bs.max_risk_per_trade))
        await dba.set_state("max_daily_drawdown", str(bs.max_daily_drawdown))
        await dba.set_state("max_open_positions", str(bs.max_open_positions))
        await dba.set_state("trading_pairs", ",".join(bs.trading_pairs))
        await dba.set_state("paper_trading", "1" if bs.paper_trading else "0")

        logger.info("⚙️ Configuration settings updated dynamically")
        return {"status": "success", "message": "Settings updated successfully"}
    except Exception as e:
        logger.error(f"Failed to update settings: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def register_routes(app, orchestrator=None, db=None):  # noqa: ANN001
    global _app_store
    _app_store = app
    app.include_router(router, prefix="/api/settings")
