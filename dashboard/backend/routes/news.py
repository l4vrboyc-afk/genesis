"""News route — GET /api/news."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter
from loguru import logger

from ..models.responses import NewsListResponse

router = APIRouter()
_app_store: Any = None


@router.get("", response_model=NewsListResponse, tags=["news"])
async def get_news():
    """Get upcoming economic calendar events."""
    from bot.config.settings import settings
    from fastapi import HTTPException

    try:
        orch = _app_store.state.orchestrator
        filter_obj = orch.news_filter
        if (
            filter_obj._last_refresh is None
            or (datetime.now() - filter_obj._last_refresh).total_seconds()
            > settings.news_calendar_refresh_interval
        ):
            await filter_obj.refresh_calendar_async()

        events = filter_obj.get_upcoming_events(hours=168)
        formatted_events = []
        for e in events:
            e_copy = dict(e)
            if "time" in e_copy and hasattr(e_copy["time"], "isoformat"):
                e_copy["time"] = e_copy["time"].isoformat()
            # Provide both 'name' (canonical) and 'event_name' (backward compat)
            if "name" in e_copy and "event_name" not in e_copy:
                e_copy["event_name"] = e_copy["name"]
            formatted_events.append(e_copy)
        return {"events": formatted_events}
    except Exception as e:
        logger.error(f"Error in /api/news: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def register_routes(app, orchestrator=None, db=None):  # noqa: ANN001
    global _app_store
    _app_store = app
    app.include_router(router, prefix="/api/news")
