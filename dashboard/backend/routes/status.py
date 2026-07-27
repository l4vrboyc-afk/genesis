"""Status route — GET /api/status."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from loguru import logger

from ..models.responses import StatusResponse

router = APIRouter()
_app_store: Any = None  # set by register_routes


@router.get("", response_model=StatusResponse, tags=["status"])
async def get_status():
    """Get current bot status, balance, equity, and open positions."""
    from fastapi import HTTPException

    try:
        orch = _app_store.state.orchestrator
        status_data = await orch.get_status()
        status_data["open_trades"] = await orch.get_live_open_trades()
        return status_data
    except Exception as e:
        logger.error(f"Error in /api/status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def register_routes(app, orchestrator=None, db=None):  # noqa: ANN001
    global _app_store
    _app_store = app
    app.include_router(router, prefix="/api/status")
