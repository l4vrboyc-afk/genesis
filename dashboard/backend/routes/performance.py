"""Performance route — GET /api/performance."""
from __future__ import annotations

from fastapi import APIRouter
from loguru import logger

from ..models.responses import PerformanceResponse

router = APIRouter()
_app_store: Any = None


@router.get("", response_model=PerformanceResponse, tags=["performance"])
async def get_performance():
    """Get rolling performance stats, daily P&L data, and metrics."""
    from fastapi import HTTPException

    try:
        orch = _app_store.state.orchestrator
        summary = orch.performance_tracker.get_summary()
        history = await orch.db.get_daily_performance_history(limit=30)
        summary["daily_performance_history"] = [h.to_dict() for h in history]
        return summary
    except Exception as e:
        logger.error(f"Error in /api/performance: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def register_routes(app, orchestrator=None, db=None):  # noqa: ANN001
    global _app_store
    _app_store = app
    app.include_router(router, prefix="/api/performance")
