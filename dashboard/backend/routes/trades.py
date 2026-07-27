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


def register_routes(app, orchestrator=None, db=None):  # noqa: ANN001
    global _app_store
    _app_store = app
    app.include_router(router, prefix="/api/trades")
