"""Logs route — GET /api/logs.

Fix #14: Returns recent bot log lines so the dashboard can display them
in-browser without SSH access to the host. Supports level filtering and
configurable line count.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Query
from loguru import logger

router = APIRouter()
_app_store: Any = None

# Project root for resolving the log path
PROJECT_ROOT = Path(__file__).resolve().parents[2]


@router.get("", tags=["logs"])
async def get_logs(
    lines: int = Query(default=200, ge=10, le=5000, description="Number of log lines to return"),
    level: Optional[str] = Query(default=None, description="Filter by log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)"),
    module: Optional[str] = Query(default=None, description="Filter by module name substring"),
):
    """Return recent log lines from the bot's log file.

    Args:
        lines: Number of log lines to return (tail of file).
        level: Optional level filter (e.g. \"ERROR\" returns only ERROR and CRITICAL).
        module: Optional module name substring filter.

    Returns:
        Dict with ``lines`` (list of log line strings), ``total`` (count),
        ``file`` (log file path), and ``filtered`` (whether filtering was applied).
    """
    from bot.config.settings import settings
    from fastapi import HTTPException

    try:
        log_path = Path(settings.log_file)
        if not log_path.is_absolute():
            log_path = PROJECT_ROOT / log_path

        if not log_path.exists():
            return {
                "lines": [f"Log file not found: {log_path}"],
                "total": 1,
                "file": str(log_path),
                "filtered": False,
            }

        with log_path.open("r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()

        # Apply filters
        filtered = False
        result_lines = all_lines

        if level:
            level_upper = level.upper()
            result_lines = [
                l for l in result_lines
                if f"| {level_upper}" in l or (level_upper == "ERROR" and "| CRITICAL" in l)
            ]
            filtered = True

        if module:
            result_lines = [l for l in result_lines if module.lower() in l.lower()]
            filtered = True

        # Take tail
        tail = result_lines[-lines:] if result_lines else []

        return {
            "lines": tail,
            "total": len(tail),
            "file": str(log_path),
            "filtered": filtered,
        }
    except Exception as e:
        logger.error(f"Error in /api/logs: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def register_routes(app, orchestrator=None, db=None):  # noqa: ANN001
    global _app_store
    _app_store = app
    app.include_router(router, prefix="/api/logs")
