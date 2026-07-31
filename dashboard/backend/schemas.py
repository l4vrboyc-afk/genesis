"""Web API Data Contracts — Pydantic schemas for dashboard request/response payloads and WebSocket event broadcasts.

All enums used here are re-exported from ``bot.config.enums`` so the dashboard
backend never needs to import from the core trading modules directly.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from bot.config.enums import Profile, MarketRegime, TrailingState, SignalType


# ── REQUEST SCHEMAS ──────────────────────────────────────────────────────


class ProfileChangeRequest(BaseModel):
    """Switch the active trading profile at runtime."""

    new_profile: Profile = Field(..., description="Target profile to switch execution logic")


class ManualOverrideRequest(BaseModel):
    """Emergency manual override to force lockdowns or release locks."""

    symbol: Optional[str] = None
    action: str = Field(
        ...,
        description="Action to take: 'FORCE_LOCKDOWN', 'RELEASE_LOCK', 'CLOSE_ALL', or 'FORCE_REGIME'",
    )
    reason: str = Field(..., description="User-provided justification for audit logs")
    regime: Optional[str] = Field(
        default=None,
        description="Regime name when action is 'FORCE_REGIME'",
    )


# ── DASHBOARD STATE SCHEMAS ──────────────────────────────────────────────


class SystemStatusResponse(BaseModel):
    """Real-time system health snapshot for dashboard initialisation."""

    active_profile: Profile = Field(description="Currently active profile")
    global_regime: MarketRegime = Field(description="Current detected or forced market regime")
    is_circuit_breaker_active: bool = Field(description="Whether any kill switch is engaged")
    daily_start_equity: float = Field(description="Equity at the start of the trading day")
    current_equity: float = Field(description="Current live equity from MT5")
    daily_pnl_pct: float = Field(description="Daily P&L as a percentage of start equity")
    active_positions_count: int = Field(description="Number of currently open positions")


class PositionResponse(BaseModel):
    """Single open position displayed in the dashboard positions table."""

    ticket: int
    symbol: str
    type: SignalType = Field(description="Trade direction: 'buy' or 'sell'")
    volume: float
    entry_price: float
    current_sl: float = Field(description="Current stop-loss price")
    current_tp: float = Field(description="Current take-profit price")
    current_price: float = Field(description="Current live market price")
    unrealized_pnl: float = Field(description="Unrealised profit/loss in account currency")
    pnl_r: float = Field(description="P&L expressed as an R-multiple of the initial risk")
    trailing_state: TrailingState = Field(description="Current trailing-stop mode")
    strategy: str = Field(description="Name of the strategy that opened this trade")


# ── WEBSOCKET EVENT PAYLOAD ──────────────────────────────────────────────


class WSEventPayload(BaseModel):
    """Payload envelope for every WebSocket broadcast event.

    The ``event_type`` field lets clients dispatch on event kind without
    parsing the data body. Known event types:

    - ``TICK`` — real-time price tick
    - ``REGIME_CHANGE`` — market regime classification updated
    - ``TRADE_OPEN`` — new position opened
    - ``TRADE_CLOSE`` — position closed
    - ``PROFILE_CHANGED`` — active trading profile switched
    - ``OVERRIDE_TRIGGERED`` — emergency override action taken
    - ``GATE_EVAL`` — 5-gate evaluator results (pass/fail per gate, score)
    - ``GATE_UPDATE`` — live header gateway pill snapshot pushed by the
      orchestrator's background pill-snapshot broadcaster (same shape as
      ``GET /api/evaluator``)
    - ``SIGNAL_UPDATE`` — live header trade-signal pill snapshot (same
      shape as ``GET /api/signal``)
    - ``SYSTEM_LOG`` — a log line from the bot
    - ``HANDSHAKE`` — initial connection confirmation
    """

    event_type: str = Field(description="e.g. 'TICK', 'REGIME_CHANGE', 'TRADE_OPEN', 'LOG'")
    timestamp: str = Field(description="ISO-8601 UTC timestamp of the event")
    data: Dict[str, Any] = Field(description="Event-specific payload")
