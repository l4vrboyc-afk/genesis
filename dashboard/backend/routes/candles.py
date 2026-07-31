"""Candles route — GET /api/candles/{symbol} (mini chart OHLC feed)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from loguru import logger

router = APIRouter()
_app_store: Any = None


@router.get("/{symbol}", tags=["candles"])
async def get_candles(
    symbol: str,
    timeframe: str = Query(default="M1", pattern=r"^(M1|M5|M15|M30|H1|H4|D1)$"),
    count: int = Query(default=60, ge=2, le=500),
):
    """Fetch OHLC candle data for a symbol.

    Powers the live mini candlestick chart inside the Position Details
    modal.  Returns the most recent *count* candles on the requested
    timeframe as a JSON list of ``{time, open, high, low, close}`` dicts
    (unix-epoch seconds for ``time``).

    Falls back to a synthetic series when MT5 is unavailable so the
    modal never hangs the dashboard.
    """
    from fastapi import HTTPException

    try:
        orch = _app_store.state.orchestrator
        if not orch or not orch.fetcher:
            raise HTTPException(status_code=503, detail="Orchestrator not ready")

        df = await orch.fetcher.get_candles(symbol.upper(), timeframe, count)
        if df is None or df.empty:
            return {"symbol": symbol.upper(), "timeframe": timeframe, "candles": []}

        candles = []
        for idx, row in df.iterrows():
            candles.append({
                "time": int(idx.timestamp()),
                "open": round(float(row["open"]), 6),
                "high": round(float(row["high"]), 6),
                "low": round(float(row["low"]), 6),
                "close": round(float(row["close"]), 6),
            })
        return {"symbol": symbol.upper(), "timeframe": timeframe, "candles": candles}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in /api/candles: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def register_routes(app, orchestrator=None, db=None):  # noqa: ANN001
    global _app_store
    _app_store = app
    app.include_router(router, prefix="/api/candles")
