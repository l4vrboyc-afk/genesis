"""Risk route — GET /api/risk."""
from __future__ import annotations

from fastapi import APIRouter
from loguru import logger

from ..models.responses import RiskStatsResponse

router = APIRouter()
_app_store: Any = None


@router.get("", response_model=RiskStatsResponse, tags=["risk"])
async def get_risk():
    """Get risk parameters and current drawdown limits."""
    from bot.config import settings as bs
    from fastapi import HTTPException

    try:
        stats = await _app_store.state.orchestrator.risk_manager.get_risk_stats()
        # `losing_streak_pause` (the threshold, not the live counter) is a
        # config knob — surface it on the dashboard so the user sees the
        # metric labeled correctly (e.g. "1 / 3" rather than "1 / 3 max-positions").
        stats["losing_streak_pause"] = bs.losing_streak_pause
        return stats
    except Exception as e:
        logger.error(f"Error in /api/risk: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def register_routes(app, orchestrator=None, db=None):  # noqa: ANN001
    global _app_store
    _app_store = app
    app.include_router(router, prefix="/api/risk")
