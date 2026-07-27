"""
Bot Configuration & Settings
Central configuration for the entire Genesis trading bot system.
"""

import os
import sys
from pydantic_settings import BaseSettings
from pydantic import Field
from typing import List
from enum import Enum


class MarketRegime(str, Enum):
    """Market condition classification."""
    TRENDING = "trending"
    RANGING = "ranging"
    VOLATILE = "volatile"
    DEAD = "dead"
    NEWS_EVENT = "news_event"


class TradeDirection(str, Enum):
    """Trade direction."""
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


class BotSettings(BaseSettings):
    """Main bot configuration — loaded from .env file."""
    
    # ── Profile ─────────────────────────────────────────────────────
    active_profile: str = Field(default="default", description="Active trading profile (e.g. default, scalper, breakout)")


    # ── MT5 Connection ──────────────────────────────────────────────
    mt5_login: int = Field(default=0, description="MT5 account number")
    mt5_password: str = Field(default="", description="MT5 account password")
    mt5_server: str = Field(default="Exness-MT5Trial7", description="MT5 broker server name")
    mt5_path: str = Field(
        default_factory=lambda: (
            r"C:\Program Files\MetaTrader 5\terminal64.exe"
            if sys.platform == "win32"
            else ""
        ),
        description=(
            "Path to MT5 terminal executable. Windows default: "
            "C:\\Program Files\\MetaTrader 5\\terminal64.exe. "
            "On macOS / Linux, set this explicitly to a Wine / Parallels "
            "/ Windows-VPS path. scripts/check_setup.py validates the file exists."
        ),
    )

    # ── Trading Pairs ───────────────────────────────────────────────
    trading_pairs: List[str] = Field(
        default=[
            "EURUSD", "GBPUSD", "USDJPY", "AUDUSD",
            "USDCAD", "EURGBP", "GBPJPY"
        ],
        description="Currency pairs to trade"
    )

    # ── Risk Management ─────────────────────────────────────────────
    max_risk_per_trade: float = Field(default=0.01, description="Max risk per trade (1% = 0.01)")
    max_daily_drawdown: float = Field(default=0.05, description="Max daily drawdown (5% = 0.05)")
    max_open_positions: int = Field(default=3, description="Maximum simultaneous open positions")
    currency_exposure_cap: int = Field(default=2, description="Maximum positions sharing the same currency (base or quote)")
    min_reward_ratio: float = Field(default=2.0, description="Minimum Risk:Reward ratio")
    losing_streak_pause: int = Field(default=3, description="Pause after N consecutive losses")
    losing_streak_cooldown: int = Field(default=3600, description="Cooldown in seconds after losing streak")

    # ── Strategy Parameters ─────────────────────────────────────────
    ema_period: int = Field(default=200, description="EMA period for trend filter")
    adx_period: int = Field(default=14, description="ADX period for trend strength")
    adx_trend_threshold: float = Field(default=25.0, description="ADX value indicating trending market")
    adx_range_threshold: float = Field(default=20.0, description="ADX value indicating ranging market")

    rsi_period: int = Field(default=14, description="RSI calculation period")
    rsi_oversold: float = Field(default=30.0, description="RSI oversold level")
    rsi_overbought: float = Field(default=70.0, description="RSI overbought level")

    atr_period: int = Field(default=14, description="ATR period")
    atr_sl_multiplier: float = Field(default=1.5, description="ATR multiplier for stop loss distance")
    atr_tp_multiplier: float = Field(default=3.0, description="ATR multiplier for take profit distance")
    atr_volatility_spike: float = Field(default=2.0, description="ATR spike detection multiplier")

    # ── Tick Logger Configuration ────────────────────────────────────────
    tick_queue_maxsize: int = Field(default=10_000, description="Maximum size of the async tick queue before back‑pressure applies")
    tick_batch_size: int = Field(default=500, description="Number of ticks to write per SQLite batch insert")
    tick_flush_interval_secs: float = Field(default=2.0, description="Maximum seconds to wait before flushing the tick queue, even if batch size not reached")
    enable_tick_deduplication: bool = Field(default=True, description="If true, drop consecutive ticks with identical bid/ask for the same symbol")
    sqlite_wal_mode: bool = Field(default=True, description="Enable SQLite WAL mode for concurrent reads/writes")

    # Volume-surge gate for ScalperMomentum (scalper-profile unblock).
    # Latest-bar tick volume must be >= this × the 20-bar mean before a
    # momentum signal fires. M1 forex tick volume rarely sustains 1.5×,
    # so a profile can lower it (``VOLUME_SURGE_RATIO`` in .env.<profile>).
    # Default 1.5 mirrors the prior hardcoded constant — no behaviour change.
    volume_surge_ratio: float = Field(
        default=1.5,
        description="ScalperMomentum: latest-bar volume / 20-bar mean required before a signal fires (1.5 = prior hardcoded default)",
    )

    higher_timeframe: str = Field(default="H4", description="Higher timeframe for trend analysis")
    entry_timeframe: str = Field(default="M15", description="Lower timeframe for trade entries")

    # ── News Filter ─────────────────────────────────────────────────
    news_filter_enabled: bool = Field(default=True, description="Enable economic calendar news filter")
    news_buffer_minutes_before: int = Field(default=30, description="No-trade window before news (minutes)")
    news_buffer_minutes_after: int = Field(default=30, description="No-trade window after news (minutes)")
    news_calendar_refresh_interval: int = Field(default=3600, description="Calendar refresh interval (seconds)")

    # ── Track (d) — Real-Money Safety Hysteresis ──────────────────────
    # Sticky regime window: once classified, the regime is held for this many
    # seconds before flipping. Defaults to 5 minutes — kills the 22–23 jitter
    # that otherwise flicks the selector between TRENDING / RANGING every
    # cycle and forces a handful of unwanted trades around the S/R crossover.
    hysteresis_window_seconds: int = Field(
        default=300,
        description="Seconds a newly-classified regime must persist before the selector flips to it",
    )
    # Equity-floor kill switch. Unrealised drawdown vs the running peak beyond
    # this fraction trips the kill switch (set _equity_floor_tripped=True) AND
    # causes the orchestrator to flatten all open positions. Default 40 %
    # protects against account-tail blow-up on a series of bad fills.
    equity_floor_kill_switch_pct: float = Field(
        default=0.40,
        description="Peak-equity drawdown fraction at which the equity-floor kill switch trips",
    )
    # News-filter fail-closed defence. Default is OFF (permissive — the MT5
    # calendar can be flaky and we prefer to trade with stale data rather
    # than miss valid sessions). Set to true once you have validated the
    # fallback schedule in your jurisdiction.
    news_filter_fail_closed: bool = Field(
        default=False,
        description="Block trading when the news calendar cache is stale (no successful refresh in 2x refresh interval)",
    )

    # ── Self-Optimization ───────────────────────────────────────────
    performance_window: int = Field(default=20, description="Rolling trade window for performance metrics")
    min_win_rate: float = Field(default=0.40, description="Min win rate before auto-pause (40%)")
    min_avg_rr: float = Field(default=1.0, description="Min average R:R before tightening filters")
    min_confidence: float = Field(default=0.40, description="Minimum signal confidence (0.0-1.0) required for entry")

    # ── Position Sizing ─────────────────────────────────────────────
    starting_capital: float = Field(default=1000.0, description="Starting account capital")
    lot_sizing_mode: str = Field(default="DYNAMIC", description="Mode for position sizing (DYNAMIC or FIXED)")
    fixed_lot_size: float = Field(default=0.01, description="Lot size when mode is FIXED")
    volatility_position_scale: bool = Field(default=True, description="Scale position size with volatility")

    # ── Notifications ───────────────────────────────────────────────
    discord_token: str = Field(default="", description="Discord bot token")
    discord_channel_id: int = Field(default=0, description="Discord channel ID for alerts")
    discord_command_prefix: str = Field(default="!", description="Discord command prefix")

    # ── Dashboard ───────────────────────────────────────────────────
    dashboard_host: str = Field(default="0.0.0.0", description="Dashboard API host")
    dashboard_port: int = Field(default=8000, description="Dashboard API port")
    dashboard_startup_timeout_secs: int = Field(default=120, env="GENESIS_DASHBOARD_STARTUP_TIMEOUT_SECS", description="Timeout in seconds for dashboard backend to bind")

    # ── Database ────────────────────────────────────────────────────
    database_url: str = Field(default="sqlite:///database/trades.db", description="Database URL")
    
    # ── Logging ─────────────────────────────────────────────────────
    log_level: str = Field(default="INFO", description="Logging level")
    log_file: str = Field(default="logs/bot.log", description="Log file path")
    
    # ── General ─────────────────────────────────────────────────────
    paper_trading: bool = Field(default=True, description="Paper trading mode (demo account)")
    bot_name: str = Field(default="Genesis Trading Bot", description="Bot display name")

    # Magic number used as MT5 order identifier (single source of truth for all
    # order tickets across order placement and close)
    magic_number: int = Field(
        default=202406,
        description="MT5 magic number used by all Genesis orders",
    )

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
        "extra": "ignore",
    }


# ── Timeframe mapping (string → MT5 constant) ──────────────────────
TIMEFRAME_MAP = {
    "M1": 1, "M5": 5, "M15": 15, "M30": 30,
    "H1": 16385, "H4": 16388,
    "D1": 16408, "W1": 32769, "MN1": 49153,
}


# Singleton settings instance
env_file = ".env"
profile = os.getenv("GENESIS_PROFILE")
if profile:
    env_file = f".env.{profile.lower()}"

# Fallback to .env if the profile-specific file doesn't exist
if profile and not os.path.exists(env_file):
    env_file = ".env"

settings = BotSettings(_env_file=env_file)
# Overwrite active_profile with the env var if it exists so the app knows its identity
if profile:
    settings.active_profile = profile.lower()
