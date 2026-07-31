"""Evaluator route — GET /api/evaluator (live 5-gateway pill matrix).

Evaluates the dashboard's five gateway pills (EMA / ADX / RSI / VOL / REG)
for a symbol from live MT5 data, so the header gateway matrix updates on
every symbol change in the custom liquid-glass dropdown.
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Query
from loguru import logger

router = APIRouter()
_app_store: Any = None  # set by register_routes


@router.get("", tags=["evaluator"])
async def get_evaluator(
    symbol: str = Query(..., description="Trading pair, e.g. EURUSD"),
    profile: str = Query(default="", description="Active profile key (informational)"),
):
    """Evaluate the five gateway pills for a symbol from real MT5 data.

    Returns ``{gates: [5 bools], passed, total, overall, details, symbol}``
    in the dashboard pill order ``[EMA, ADX, RSI, VOL, REG]``.  Raises 503
    when MT5 data is entirely unavailable so the frontend falls back to its
    simulation mode instead of showing all-green pills.
    """
    from fastapi import HTTPException
    from bot.config.settings import settings as bot_settings

    try:
        orch = _app_store.state.orchestrator
        if not orch or not orch.fetcher or not orch.strategy_selector:
            raise HTTPException(status_code=503, detail="Orchestrator not ready")

        sym = symbol.upper()

        # Fetch the three data sources in parallel through the serialised
        # MT5 executor so the broker IPC stays on a single worker thread.
        gk, etf_data, htf_data = await asyncio.gather(
            orch.fetcher.get_gatekeeper_indicators(sym),
            orch.fetcher.get_analyzed_data(sym, bot_settings.entry_timeframe, 120),
            orch.fetcher.get_analyzed_data(sym, bot_settings.higher_timeframe, 220),
        )

        # If MT5 is entirely unavailable, signal the frontend so it falls
        # back to simulation rather than painting stale all-pass pills.
        if (
            not gk
            and (etf_data is None or etf_data.empty)
            and (htf_data is None or htf_data.empty)
        ):
            raise HTTPException(status_code=503, detail="MT5 market data unavailable")

        result = orch.strategy_selector.evaluate_symbol_gates(
            symbol=sym,
            gatekeeper_data=gk,
            entry_tf_data=etf_data,
            htf_data=htf_data,
        )
        result["profile"] = profile or bot_settings.active_profile
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in /api/evaluator: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def register_routes(app, orchestrator=None, db=None):  # noqa: ANN001
    global _app_store
    _app_store = app
    app.include_router(router, prefix="/api/evaluator")
