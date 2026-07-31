"""Signal route — GET /api/signal (live trade-signal pill).

Generates a real trade signal for the dashboard's header trade-signal pill
from live MT5 data, so it updates on every dropdown symbol change instead of
falling back to the local preview simulation.
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Query
from loguru import logger

router = APIRouter()
_app_store: Any = None  # set by register_routes


def _format_price(price, symbol: str) -> str:
    """Format a price for the signal pill (5dp FX, 3dp JPY, 2dp metals)."""
    if price is None:
        return "--"
    try:
        value = float(price)
    except (TypeError, ValueError):
        return "--"
    upper = symbol.upper()
    if upper in ("XAUUSD", "XAGUSD", "XAUEUR") or upper.startswith("XAU"):
        return f"{value:.2f}"
    if "JPY" in upper:
        return f"{value:.3f}"
    return f"{value:.5f}"


def _hold_estimate(profile: str) -> str:
    """Tailor hold-duration estimate per profile (mirrors the frontend's
    ``calculateLocalSignalPreview`` so online and offline paths agree)."""
    p = profile or ""
    if "swing" in p:
        return "1d - 3d"
    if "scalper" in p:
        return "15m - 45m"
    if "breakout" in p:
        return "2h - 6h"
    return "45m"


@router.get("", tags=["signal"])
async def get_signal(
    symbol: str = Query(..., description="Trading pair, e.g. EURUSD"),
    profile: str = Query(default="", description="Active profile key (drives hold estimate)"),
):
    """Generate a live trade signal for a symbol from real MT5 data.

    Returns ``{action, type, sl, tp, duration, ...}`` — the shape the
    frontend ``renderSignalData`` expects.  ``type`` is ``BUY`` / ``SELL`` /
    ``WAIT``; ``sl``/``tp`` are formatted price strings (``--`` when no
    signal).  Raises 503 when MT5 data is entirely unavailable so the
    frontend falls back to its local preview.
    """
    from fastapi import HTTPException
    from bot.config.settings import settings as bot_settings

    try:
        orch = _app_store.state.orchestrator
        if not orch or not orch.fetcher or not orch.strategy_selector:
            raise HTTPException(status_code=503, detail="Orchestrator not ready")

        sym = symbol.upper()

        # Fetch the data the strategies need, in parallel through the
        # serialised MT5 executor so the broker IPC stays single-threaded.
        current_price, htf_data, etf_data = await asyncio.gather(
            orch.fetcher.get_current_price(sym),
            orch.fetcher.get_analyzed_data(sym, bot_settings.higher_timeframe, 220),
            orch.fetcher.get_analyzed_data(sym, bot_settings.entry_timeframe, 120),
        )

        # If MT5 is entirely unavailable, signal the frontend so it falls
        # back to its local preview rather than rendering stale pills.
        if (
            current_price is None
            and (htf_data is None or htf_data.empty)
            and (etf_data is None or etf_data.empty)
        ):
            raise HTTPException(status_code=503, detail="MT5 market data unavailable")

        # Some strategies index current_price["bid"] / ["ask"] directly.  If
        # the live tick is missing (partial MT5 hiccup) but candle frames
        # came back, synthesize a price dict from the latest close so the
        # strategy never raises a TypeError.
        if not isinstance(current_price, dict) or "bid" not in current_price or "ask" not in current_price:
            src = etf_data if (etf_data is not None and not etf_data.empty) else htf_data
            if src is not None and not src.empty:
                close = float(src["close"].iloc[-1])
                current_price = {"bid": close, "ask": close, "last": close}
            else:
                raise HTTPException(status_code=503, detail="MT5 market data unavailable")

        signal = orch.strategy_selector.evaluate_symbol_signal(
            symbol=sym,
            htf_data=htf_data,
            etf_data=etf_data,
            current_price=current_price,
        )

        resolved_profile = profile or bot_settings.active_profile
        duration = _hold_estimate(resolved_profile)

        if signal is None:
            return {
                "symbol": sym,
                "profile": resolved_profile,
                "action": "NEUTRAL / WAIT",
                "type": "WAIT",
                "sl": "--",
                "tp": "--",
                "duration": duration,
                "entry": None,
                "confidence": None,
                "risk_reward_ratio": None,
                "strategy": None,
            }

        direction = signal.direction.value.upper()  # 'BUY' / 'SELL'
        return {
            "symbol": sym,
            "profile": resolved_profile,
            "action": f"{direction} SIGNAL",
            "type": direction,
            "sl": _format_price(signal.stop_loss, sym),
            "tp": _format_price(signal.take_profit, sym),
            "duration": duration,
            "entry": _format_price(signal.entry_price, sym),
            "confidence": signal.confidence,
            "risk_reward_ratio": signal.risk_reward_ratio,
            "strategy": signal.strategy_name,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in /api/signal: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def register_routes(app, orchestrator=None, db=None):  # noqa: ANN001
    global _app_store
    _app_store = app
    app.include_router(router, prefix="/api/signal")
