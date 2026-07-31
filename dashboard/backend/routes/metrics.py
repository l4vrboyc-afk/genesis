"""Metrics route — GET /api/metrics (Prometheus / OpenMetrics format).

Fix #12: Exposes bot health metrics in Prometheus text format so operators
can integrate Genesis with Grafana, Datadog, or any observability stack.
Opt-in behind PROMETHEUS_ENABLED env var.
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse
from loguru import logger

router = APIRouter()
_app_store: Any = None


def _sanitize(v: Any) -> str:
    """Convert a value to a Prometheus-safe string."""
    if v is None:
        return "0"
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, float):
        if v != v:  # NaN check
            return "0"
        return f"{v:.6f}"
    return str(v)


def _metric_line(name: str, value: Any, labels: dict | None = None) -> str:
    """Format a single Prometheus metric line with optional labels.

    Label values are escaped per the Prometheus text exposition format
    (backslash, double-quote, newline) so a profile/login containing
    those characters cannot corrupt the metrics output.
    """
    val = _sanitize(value)
    if labels:
        def _esc(s: Any) -> str:
            return str(s).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        label_str = "{" + ",".join(f'{k}="{_esc(v)}"' for k, v in labels.items()) + "}"
        return f"{name}{label_str} {val}\n"
    return f"{name} {val}\n"


@router.get("", response_class=PlainTextResponse, tags=["metrics"])
async def get_metrics():
    """Return bot metrics in Prometheus / OpenMetrics text format."""
    enabled = os.environ.get("PROMETHEUS_ENABLED", "false").lower() == "true"
    if not enabled:
        return PlainTextResponse(
            content="# Prometheus metrics disabled. Set PROMETHEUS_ENABLED=true in .env\n",
            status_code=200,
            media_type="text/plain; version=0.0.4",
        )

    from fastapi import HTTPException

    try:
        orch = _app_store.state.orchestrator
        status = await orch.get_status()
        risk_stats = await orch.risk_manager.get_risk_stats()
        perf = orch.performance_tracker.get_summary()

        lines = [
            "# HELP genesis_bot_info Static bot information",
            "# TYPE genesis_bot_info gauge",
            _metric_line('genesis_bot_info', 1, {
                "name": str(orch.mt5_conn._account_info.login if orch.mt5_conn._account_info else 0),
                "profile": orch.strategy_selector.profile,
                "paper_trading": "true" if status.get("paper_trading") else "false",
            }),
            "",
            "# HELP genesis_bot_paused Whether the bot is paused (1=paused)",
            "# TYPE genesis_bot_paused gauge",
            _metric_line("genesis_bot_paused", 1 if status.get("paused") else 0),
            "",
            "# HELP genesis_mt5_connected Whether MT5 is connected (1=connected)",
            "# TYPE genesis_mt5_connected gauge",
            _metric_line("genesis_mt5_connected", 1 if status.get("mt5_connected") else 0),
            "",
            "# HELP genesis_account_balance Account balance in account currency",
            "# TYPE genesis_account_balance gauge",
            _metric_line("genesis_account_balance", status.get("balance", 0)),
            "",
            "# HELP genesis_account_equity Account equity in account currency",
            "# TYPE genesis_account_equity gauge",
            _metric_line("genesis_account_equity", status.get("equity", 0)),
            "",
            "# HELP genesis_daily_pnl Daily profit/loss in account currency",
            "# TYPE genesis_daily_pnl gauge",
            _metric_line("genesis_daily_pnl", status.get("daily_pnl", 0)),
            "",
            "# HELP genesis_win_rate Rolling win rate (0.0-1.0)",
            "# TYPE genesis_win_rate gauge",
            _metric_line("genesis_win_rate", status.get("win_rate", 0)),
            "",
            "# HELP genesis_open_positions Number of currently open positions",
            "# TYPE genesis_open_positions gauge",
            _metric_line("genesis_open_positions", status.get("open_positions", 0)),
            "",
            "# HELP genesis_market_regime Current market regime",
            "# TYPE genesis_market_regime gauge",
            _metric_line('genesis_market_regime', 1, {"regime": str(status.get("regime", "unknown"))}),
            "",
            "# HELP genesis_kill_switch_active Whether any kill switch is engaged (1=engaged)",
            "# TYPE genesis_kill_switch_active gauge",
            _metric_line("genesis_kill_switch_daily_dd", 1 if risk_stats.get("kill_switches", {}).get("daily_drawdown") else 0),
            _metric_line("genesis_kill_switch_equity_floor", 1 if risk_stats.get("kill_switches", {}).get("equity_floor") else 0),
            "",
            "# HELP genesis_peak_equity Running peak equity",
            "# TYPE genesis_peak_equity gauge",
            _metric_line("genesis_peak_equity", risk_stats.get("peak_equity", 0)),
            "",
            "# HELP genesis_daily_drawdown_pct Current daily drawdown percentage",
            "# TYPE genesis_daily_drawdown_pct gauge",
            _metric_line("genesis_daily_drawdown_pct", risk_stats.get("daily_drawdown_pct", 0)),
            "",
            "# HELP genesis_consecutive_losses Current losing streak count",
            "# TYPE genesis_consecutive_losses gauge",
            _metric_line("genesis_consecutive_losses", risk_stats.get("consecutive_losses", 0)),
            "",
            "# HELP genesis_cooldown_active Whether losing-streak cooldown is active (1=active)",
            "# TYPE genesis_cooldown_active gauge",
            _metric_line("genesis_cooldown_active", 1 if risk_stats.get("cooldown_active") else 0),
            "",
            "# HELP genesis_total_trades Total trades in performance window",
            "# TYPE genesis_total_trades gauge",
            _metric_line("genesis_total_trades", perf.get("total_trades", 0)),
            "",
            "# HELP genesis_profit_factor Rolling profit factor",
            "# TYPE genesis_profit_factor gauge",
            _metric_line("genesis_profit_factor", perf.get("profit_factor", 0)),
            "",
            "# EOF",
        ]
        return PlainTextResponse(
            content="".join(lines),
            media_type="text/plain; version=0.0.4",
        )
    except Exception as e:
        logger.error(f"Error in /api/metrics: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def register_routes(app, orchestrator=None, db=None):  # noqa: ANN001
    global _app_store
    _app_store = app
    app.include_router(router, prefix="/api/metrics")
