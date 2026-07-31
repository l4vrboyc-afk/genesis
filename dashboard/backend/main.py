"""FastAPI Backend — Exposes API routes to control and monitor the trading bot.
Serves the static React/Vanilla HTML frontend app.
"""
from __future__ import annotations

import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.staticfiles import StaticFiles
from loguru import logger

from .copilot import copilot_for


def create_app(orchestrator) -> FastAPI:
    """Create and configure the FastAPI application."""
    from bot.config import settings as bot_settings

    app = FastAPI(
        title="Genesis Trading Bot Dashboard API",
        version="2.1.0",
        docs_url="/docs",
    )

    # Store references on app.state so route modules can reach them
    app.state.orchestrator = orchestrator
    app.state.copilot = copilot_for(orchestrator)

    # Build CORS origins dynamically so every profile's port is
    # covered.  The browser connects to 127.0.0.1 (the launcher
    # rewrites 0.0.0.0), but may send "localhost" as the Origin
    # header — include all sensible variants. Drop 0.0.0.0 from the
    # list because browsers never send that as an Origin header and
    # the entry is dead weight.
    _port = bot_settings.dashboard_port
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            f"http://{bot_settings.dashboard_host}:{_port}",
            f"http://localhost:{_port}",
            f"http://127.0.0.1:{_port}",
        ],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Ultra-fast health probe (always first, no deps) ──────────
    # This endpoint is hit by the launcher's port-polling loop.  It
    # must return 200 immediately, before any route module is imported.
    @app.get("/api/health")
    async def health():
        return {"status": "ok", "started": True}

    # ── Launcher Info (lightweight, no route-module imports) ─────
    @app.get("/api/launcher-info")
    async def launcher_info():
        import os as _os
        return {
            "launched_by_gui": _os.environ.get("GENESIS_LAUNCHED_BY") == "gui",
            "profile": _os.environ.get("GENESIS_PROFILE"),
            "picker_url": _os.environ.get("GENESIS_PICKER_URL"),
        }

    # ── Route modules (each alias is unique — no collision) ─────────
    # Imported after the bare health endpoint so the port binds fast.
    from .routes.status import register_routes as _reg_status
    from .routes.candles import register_routes as _reg_candles
    from .routes.trades import register_routes as _reg_trades
    from .routes.performance import register_routes as _reg_perf
    from .routes.news import register_routes as _reg_news
    from .routes.risk import register_routes as _reg_risk
    from .routes.control import register_routes as _reg_control
    from .routes.settings import register_routes as _reg_settings
    from .routes.copilot import register_routes as _reg_copilot
    from .routes.metrics import register_routes as _reg_metrics
    from .routes.logs import register_routes as _reg_logs
    from .routes.ws_feed import register_routes as _reg_ws_feed
    from .routes.profile import register_routes as _reg_profile
    from .routes.override import register_routes as _reg_override
    from .routes.evaluator import register_routes as _reg_evaluator
    from .routes.signal import register_routes as _reg_signal

    _reg_status(app)
    _reg_candles(app)
    _reg_trades(app)
    _reg_perf(app)
    _reg_news(app)
    _reg_risk(app)
    _reg_control(app)
    _reg_settings(app)
    _reg_copilot(app)
    _reg_metrics(app)
    _reg_logs(app)
    _reg_ws_feed(app)
    _reg_profile(app)
    _reg_override(app)
    _reg_evaluator(app)
    _reg_signal(app)

    # ── Static File Mounting ────────────────────────────────────────

    frontend_dir = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "frontend")
    )
    if os.path.exists(frontend_dir):
        app.mount(
            "/",
            StaticFiles(directory=frontend_dir, html=True),
            name="frontend",
        )
        logger.info(f"📁 Mounted frontend static directory: {frontend_dir}")
    else:
        logger.warning(
            f"⚠️ Frontend directory not found at {frontend_dir}. Skipping static files."
        )

    return app
