"""Pydantic response models for all Genesis Trading Bot API routes."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


# ── Status ────────────────────────────────────────────────────────────

class StatusResponse(BaseModel):
    """Current bot status, balance, equity, and open positions."""
    model_config = ConfigDict(from_attributes=True)

    bot_name: str = Field(description="Display name of the bot")
    paused: bool = Field(description="Whether the bot is paused")
    mt5_connected: bool = Field(description="Whether MT5 is connected")
    account_login: int = Field(description="MT5 account login number")
    balance: float = Field(description="Account balance")
    equity: float = Field(description="Account equity")
    daily_pnl: float = Field(description="Daily profit/loss")
    win_rate: float = Field(description="Win rate (0.0 – 1.0)")
    open_positions: int = Field(description="Number of open positions")
    regime: str = Field(description="Current market regime")
    active_profile: str = Field(description="Active trading profile name")
    paper_trading: bool = Field(description="Whether paper trading is active")
    open_trades: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Currently open trades as plain dicts",
    )


# ── Trades ────────────────────────────────────────────────────────────

class TradeResponse(BaseModel):
    """Individual trade record."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    ticket: int
    symbol: str
    direction: str = Field(description='"buy" or "sell"')
    volume: float
    entry_price: float
    exit_price: Optional[float] = None
    sl: float
    tp: float
    profit: float
    swap: float
    entry_comment: Optional[str] = None
    close_comment: Optional[str] = None
    comment: Optional[str] = None
    open_time: Optional[str] = None
    close_time: Optional[str] = None
    strategy: Optional[str] = None
    market_regime: Optional[str] = None
    status: str = Field(description='"open" or "closed"')
    position_value_usd: float = Field(
        default=0.0,
        description="Notional position value in USD (volume × contract size × entry price)",
    )
    return_r: float = Field(
        default=0.0,
        description="Realised R-multiple (PnL / initial risk amount)",
    )

    @model_validator(mode="before")
    @classmethod
    def _coalesce_null_metrics(cls, data: Any) -> Any:
        """Coerce NULL position_value_usd / return_r to 0.0 so that
        legacy database rows (written before these columns existed)
        don't trigger Pydantic validation failures."""
        if isinstance(data, dict):
            if data.get("position_value_usd") is None:
                data["position_value_usd"] = 0.0
            if data.get("return_r") is None:
                data["return_r"] = 0.0
        return data


class TradesListResponse(BaseModel):
    """Paginated trade list."""
    model_config = ConfigDict(from_attributes=True)

    trades: List[TradeResponse]
    total: int = Field(description="Total number of trades matching the query")


# ── Performance ───────────────────────────────────────────────────────

class DailyPerformanceResponse(BaseModel):
    """Single-day performance snapshot."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    date: str = Field(description="ISO date string YYYY-MM-DD")
    balance: float
    equity: float
    pnl: float
    drawdown: float
    win_rate: float
    trade_count: int


class PerformanceSummaryResponse(BaseModel):
    """Rolling performance summary."""
    model_config = ConfigDict(from_attributes=True)

    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    total_pnl: float
    avg_rr: float
    avg_win: float
    avg_loss: float
    profit_factor: float
    max_drawdown: float
    daily_performance_history: List[DailyPerformanceResponse] = Field(
        default_factory=list,
        description="Last N days of performance",
    )


class PerformanceResponse(PerformanceSummaryResponse):
    """Alias kept for backwards-compat with /api/performance route."""
    pass


# ── News ──────────────────────────────────────────────────────────────

class NewsEventResponse(BaseModel):
    """Upcoming economic calendar event."""
    model_config = ConfigDict(from_attributes=True)

    time: datetime = Field(description="Event timestamp (UTC)")
    currency: str
    impact: str = Field(description="e.g. high, medium, low")
    event_name: str
    previous: Optional[str] = None
    forecast: Optional[str] = None
    actual: Optional[str] = None


class NewsListResponse(BaseModel):
    """List of upcoming economic events."""
    model_config = ConfigDict(from_attributes=True)

    events: List[NewsEventResponse]
    news_source: str = Field(
        default="unknown",
        description='Source of calendar data: "live_mt5", "fallback", "empty", or "unknown"',
    )


# ── Risk ──────────────────────────────────────────────────────────────

class KillSwitchesResponse(BaseModel):
    """Current kill-switch states."""
    model_config = ConfigDict(from_attributes=True)

    daily_drawdown: bool
    equity_floor: bool


class RiskStatsResponse(BaseModel):
    """Risk parameters and current drawdown limits."""
    model_config = ConfigDict(from_attributes=True)

    consecutive_losses: int
    cooldown_active: bool
    cooldown_until: Optional[str] = None
    daily_drawdown_pct: float = Field(description="Current daily drawdown percentage")
    daily_drawdown_limit: float = Field(description="Daily drawdown limit percentage")
    equity_floor_pct: float = Field(description="Equity floor percentage")
    peak_equity: float
    kill_switches: KillSwitchesResponse
    open_positions: int
    max_positions: int
    recent_trades: int
    losing_streak_pause: int = Field(
        description="Loss streak threshold that triggers a pause",
    )


# ── Control ───────────────────────────────────────────────────────────

class ControlResponse(BaseModel):
    """Generic control-action response."""
    model_config = ConfigDict(from_attributes=True)

    status: str = Field(description='"success" or "error"')
    message: str
    engagement_cleared: Optional[bool] = Field(
        default=None,
        description="True when a kill-switch engagement latch was cleared",
    )
    picker_url: Optional[str] = Field(
        default=None,
        description=(
            "Returned only for the switch_profile action: the GUI profile "
            "picker URL to navigate to. The backend stops the bot so the "
            "launcher's monitor then reloads the webview on the picker."
        ),
    )


class ControlRequest(BaseModel):
    """Incoming control action payload."""
    action: str = Field(description='e.g. "pause", "resume", "close_all", "force_regime"')
    regime: Optional[str] = Field(
        default=None,
        description='Required when action is "force_regime"',
    )


# ── Settings ──────────────────────────────────────────────────────────

class SettingsResponse(BaseModel):
    """Current configuration settings snapshot."""
    model_config = ConfigDict(from_attributes=True)

    max_risk_per_trade: float
    max_daily_drawdown: float
    max_open_positions: int
    lot_sizing_mode: str
    fixed_lot_size: float
    trading_pairs: List[str]
    paper_trading: bool
    bot_name: str


class SettingsUpdateRequest(BaseModel):
    """Partial settings update — all fields optional."""
    model_config = ConfigDict(from_attributes=True)

    max_risk_per_trade: Optional[float] = None
    max_daily_drawdown: Optional[float] = None
    max_open_positions: Optional[int] = None
    lot_sizing_mode: Optional[str] = None
    fixed_lot_size: Optional[float] = None
    trading_pairs: Optional[List[str]] = None
    paper_trading: Optional[bool] = None


class SettingsUpdateResponse(BaseModel):
    """Response after a settings update."""
    model_config = ConfigDict(from_attributes=True)

    status: str
    message: str


# ── Copilot ───────────────────────────────────────────────────────────

class CopilotStatusResponse(BaseModel):
    """Copilot availability and config."""
    model_config = ConfigDict(from_attributes=True)

    enabled: bool
    model: str
    reason: Optional[str] = None
    masked_key_tail: Optional[str] = None


class CopilotAskRequest(BaseModel):
    """Incoming copilot question."""
    question: str = Field(description="The user's question (max INPUT_LIMIT chars)")
    scope: Optional[str] = Field(
        default=None,
        description="Optional scope hint for context assembly",
    )
    stream: bool = Field(
        default=False,
        description="Return SSE stream when true",
    )


class CopilotAskResponse(BaseModel):
    """Non-streaming copilot answer."""
    model_config = ConfigDict(from_attributes=True)

    answer: Optional[str] = None
    citations: List[str] = Field(default_factory=list)
    enabled: bool
    error: Optional[str] = None
