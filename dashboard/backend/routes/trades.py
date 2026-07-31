"""Trades route — GET /api/trades."""
from __future__ import annotations

from typing import Any, List, Optional

from fastapi import APIRouter, Query
from loguru import logger

from ..models.responses import TradeResponse

router = APIRouter()
_app_store: Any = None


@router.get(
    "",
    response_model=List[TradeResponse],
    tags=["trades"],
)
async def get_trades(
    limit: int = 50,
    offset: int = 0,
    status: Optional[str] = Query(default="closed"),
):
    """Get trade history (closed by default).

    ``status`` defaults to ``"closed"`` so the dashboard's "completed
    trades" table doesn't mix in open positions — those have ``null``
    ``exit_price`` and would crash the front-end renderer
    (``exit_price.toFixed(5)``). Pass ``status="open"`` (or ``None``)
    to retrieve open / all trades explicitly.
    """
    from fastapi import HTTPException

    try:
        trades = await _app_store.state.orchestrator.db.get_trades(
            limit=limit, offset=offset, status=status
        )
        return [t.to_dict() for t in trades]
    except Exception as e:
        logger.error(f"Error in /api/trades: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/{ticket}/close", tags=["trades"])
async def close_trade(ticket: int):
    """Manually close an open position by ticket number."""
    from fastapi import HTTPException
    
    try:
        orch = _app_store.state.orchestrator
        if not orch or not orch.order_manager:
            raise HTTPException(status_code=503, detail="Order manager not ready")
            
        success = await orch.order_manager.close_position(ticket, comment="Closed via Dashboard")
        if not success:
            raise HTTPException(status_code=400, detail=f"Failed to close position {ticket}")
            
        return {"ok": True, "message": f"Successfully closed position {ticket}"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error closing trade {ticket}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{ticket}/modify", tags=["trades"])
async def modify_trade(
    ticket: int,
    sl: Optional[float] = None,
    tp: Optional[float] = None,
):
    """Adjust the stop-loss / take-profit of an open position.

    Used by the dashboard's Position Details modal for live trailing-stop
    adjustments. Either ``sl`` or ``tp`` may be supplied (or both); values
    are passed through to ``order_manager.modify_position`` which applies
    the broker spread-guard before sending the SLTP request.
    """
    from fastapi import HTTPException

    try:
        orch = _app_store.state.orchestrator
        if not orch or not orch.order_manager:
            raise HTTPException(status_code=503, detail="Order manager not ready")

        # Pass None through unchanged: modify_position preserves the current
        # level whenever the arg is None (sl if sl is not None else pos.sl).
        # Passing 0.0 here would WIPE the stop-loss/take-profit.
        success = await orch.order_manager.modify_position(
            ticket,
            sl=sl,
            tp=tp,
        )
        if not success:
            raise HTTPException(status_code=400, detail=f"Failed to modify position {ticket}")

        return {"ok": True, "message": f"Modified position {ticket}"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error modifying trade {ticket}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/manual-open", tags=["trades"])
async def manual_open_trade(symbol: str, direction: str, volume: float, sl: float, tp: float):
    """Manually open a trade (Fix #13)."""
    from bot.config import settings as bs
    from bot.config.settings import TradeDirection
    from fastapi import HTTPException

    try:
        orch = _app_store.state.orchestrator
        if not orch or not orch.order_manager:
            raise HTTPException(status_code=503, detail="Order manager not ready")

        dir_enum = TradeDirection.BUY if direction.lower() == "buy" else TradeDirection.SELL
        result = await orch.order_manager.place_market_order(
            symbol=symbol.upper(),
            direction=dir_enum,
            volume=volume,
            sl=sl,
            tp=tp,
            comment="Manual Override",
            magic=bs.magic_number,
        )
        if not result:
            raise HTTPException(status_code=400, detail=f"Failed to open {symbol} {direction}")

        await orch.db.record_trade_open(
            ticket=result["ticket"],
            symbol=symbol.upper(),
            direction=direction.lower(),
            volume=volume,
            entry_price=result["price"],
            sl=sl,
            tp=tp,
            strategy="Manual Override",
            regime=orch.strategy_selector.current_regime.value if orch.strategy_selector.current_regime else "unknown",
            comment="Manual Override",
            profile=bs.active_profile,
        )

        return {"ok": True, "ticket": result["ticket"], "message": f"Opened {symbol} {direction}"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error opening manual trade: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def register_routes(app, orchestrator=None, db=None):  # noqa: ANN001
    global _app_store
    _app_store = app
    app.include_router(router, prefix="/api/trades")
