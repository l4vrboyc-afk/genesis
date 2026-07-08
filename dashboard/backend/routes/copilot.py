"""Copilot route — GET /api/copilot/status, POST /api/copilot/ask."""
from __future__ import annotations

import asyncio
import json
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse
from loguru import logger

from ..models.responses import CopilotAskRequest, CopilotStatusResponse

router = APIRouter()
_app_store: Any = None


@router.get(
    "/status",
    response_model=CopilotStatusResponse,
    tags=["copilot"],
)
async def copilot_status():
    """Return copilot availability and configuration."""
    from fastapi import HTTPException

    try:
        return _app_store.state.copilot.status
    except Exception as e:
        logger.error(f"copilot_status failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/ask", tags=["copilot"])
async def copilot_ask(req: CopilotAskRequest):
    """Ask the copilot a question.

    ``stream=true`` returns Server-Sent-Events (``text/event-stream``).
    When ``stream=false`` (default) a JSON object is returned.
    """
    from fastapi import HTTPException

    try:
        cp = _app_store.state.copilot
        if not cp.is_available():
            return JSONResponse(
                status_code=503,
                content={
                    "enabled": False,
                    "error": "copilot disabled — set ANTHROPIC_API_KEY",
                    "reason": cp.status.get("reason", ""),
                },
            )
        if not req.question or not req.question.strip():
            raise HTTPException(status_code=400, detail="empty question")
        if len(req.question) > cp.INPUT_LIMIT:
            raise HTTPException(
                status_code=400,
                detail=f"question too long (max {cp.INPUT_LIMIT} chars)",
            )

        if req.stream:
            async def _event_stream():
                try:
                    async for chunk in cp.stream_ask(
                        req.question, scope=req.scope
                    ):
                        line = "data: " + json.dumps(chunk) + "\n\n"
                        yield line.encode("utf-8")
                    yield b'data: {"done": true}\n\n'
                except asyncio.CancelledError:
                    logger.debug("Copilot stream cancelled by client")
                except Exception as e:
                    logger.error(f"Copilot stream error: {e}")
                    err = (
                        b"data: "
                        + json.dumps(
                            {"type": "error", "content": str(e)}
                        ).encode("utf-8")
                        + b"\n\n"
                    )
                    yield err

            return StreamingResponse(
                _event_stream(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                },
            )

        result = await cp.ask(req.question, scope=req.scope)
        if not result.get("enabled", True) and "error" in result:
            # Defensive: if is_available changed between the gate above
            # and the actual call (e.g. SDK init failed mid-flight), return
            # 503 instead of leaking a bare 200.
            return JSONResponse(status_code=503, content=result)
        return result

    except Exception as e:
        logger.error(f"Copilot ask failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def register_routes(app, orchestrator=None, db=None):  # noqa: ANN001
    global _app_store
    _app_store = app
    app.include_router(router, prefix="/api/copilot")
