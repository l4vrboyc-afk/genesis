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
    active_profile: str = Field(default="default", description="Active trading profile (e.g. default, scalper, breakout, daytrader)")


    # ── MT5 Connection ──────────────────────────────────────────────
    mt5_login: int = Field(default=0, description="MT5 account number (single locked account — MetaQuotes-Demo)")
    mt5_password: str = Field(default="", description="MT5 account password (leave blank to attach to active open terminal)")
    # Single locked account — always targets MetaQuotes-Demo.
    # Profile .env files inherit these credentials from the base .env.
    mt5_server: str = Field(default="MetaQuotes-Demo", description="MT5 broker server name (locked to MetaQuotes-Demo)")
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

    # ── Rule 1: Gatekeeper Trend Guard (H1 ADX/EMA) ─────────────────
    # Reference: ADX > 25 AND Price > 50 EMA → strong uptrend → block SELLs
    gatekeeper_timeframe: str = Field(
        default="H1",
        description="Timeframe for the gatekeeper trend guard (default H1)",
    )
    gatekeeper_adx_threshold: float = Field(
        default=25.0,
        description="ADX threshold for gatekeeper: block SELLs when ADX exceeds this AND price > 50 EMA",
    )
    gatekeeper_ema_period: int = Field(
        default=50,
        description="EMA period for gatekeeper trend guard (default 50)",
    )

    # ── Rule 2: Minimum SL Floor via H1 ATR ────────────────────────
    # Enforce a minimum stop-loss distance using H1 ATR, so small calculated
    # stops get bumped up to at least 1.0× H1 ATR (gives trades breathing room
    # against normal market noise).
    min_sl_h1_atr_mult: float = Field(
        default=1.0,
        description="ATR multiplier for minimum SL floor; SL distance < 1.0× H1 ATR is bumped up",
    )

    # ── Rule 3: M15 Sniper Confirmation ────────────────────────────
    # Before entry, require the M15 candle pattern to confirm momentum:
    #   SELL: lower high + bearish close.  BUY: higher low + bullish close.
    sniper_confirmation_enabled: bool = Field(
        default=True,
        description="Enable M15 candle-pattern confirmation (lower-high bearish-close for SELL, etc.)",
    )

    # ── Rule 4: Spread-Aware Breakeven Logic ───────────────────────
    # Once price has moved at least N×ATR in our favour, move SL to breakeven
    # with a spread buffer to prevent accidental stop-outs from the Bid/Ask gap.
    breakeven_enabled: bool = Field(
        default=True,
        description="Enable auto-breakeven once price moves N×ATR in profit",
    )
    breakeven_activation_atr: float = Field(
        default=1.0,
        description="ATR multiplier: profit must reach this before breakeven is applied",
    )
    breakeven_spread_buffer_mult: float = Field(
        default=2.0,
        description="Spread buffer multiplier for breakeven SL: SL = entry ∓ (spread × this)",
    )
    breakeven_progressive_buffer_min: float = Field(
        default=0.5,
        description=(
            "Minimum spread buffer multiplier when price is deep in profit. "
            "The buffer scales from breakeven_spread_buffer_mult down to this value "
            "as profit goes from the activation threshold to 3× the activation "
            "threshold. E.g. with activation=1.0×ATR and default values: "
            "1.0×ATR → buffer=2.0×spread, 2.0×ATR → buffer=1.25×spread, "
            "3.0×ATR+ → buffer=0.5×spread"
        ),
    )

    # ── News Filter ─────────────────────────────────────────────────
    news_filter_enabled: bool = Field(default=True, description="Enable economic calendar news filter")
    news_buffer_minutes_before: int = Field(default=30, description="No-trade window before news (minutes)")
    news_buffer_minutes_after: int = Field(default=30, description="No-trade window after news (minutes)")
    news_calendar_refresh_interval: int = Field(default=3600, description="Calendar refresh interval (seconds)")

    # ── Trailing Stop Activation ───────────────────────────────────
    # Fix #15: ATR multiplier that must be reached in profit before trailing
    # starts. 0 = immediate trailing (current behaviour), 1.0 = trail only
    # after price has moved 1× ATR in the profit direction (reduces noise-
    # tightening on M1 timeframes). Configurable via TRAIL_ACTIVATION_ATR
    # in .env.<profile>.
    trail_activation_atr: float = Field(
        default=0.0,
        description="ATR multiplier: profit must reach this before trailing activates (0 = immediate)",
    )

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


# ── Singleton settings ──────────────────────────────────────────────────────
# MT5 credentials are ALWAYS sourced from the base .env via configparser,
# then injected into the pydantic Settings object.  Profile .env files are
# read ONLY for strategy overrides — any MT5_ keys in them are silently
# discarded so a profile swap can never accidentally change accounts.
#
# If base .env is missing, raise a clear error rather than silently
# connecting to a random/demo account.

import re  # noqa: E402

_KV_RE = re.compile(r'^([A-Za-z_][A-Za-z0-9_]*)')


def _parse_env_file(path: str) -> dict:
    """Parse a KEY=VALUE .env file (no section headers). Strips comments/blank lines.
    Preserves original key case (base .env uses UPPERCASE, snake_case fields injected below)."""
    result = {}
    with open(path, encoding="utf-8") as fh:
        for raw_line in fh:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            # skip lines that don't look like KEY=VALUE
            if not _KV_RE.match(key):
                continue
            # strip inline comments (unquoted trailing #)
            val = value.strip()
            if not val.startswith(("'", '"')):
                comment_idx = val.find("#")
                if comment_idx >= 0:
                    val = val[:comment_idx].strip()
            result[key] = val
    return result


def _require_base_env() -> dict:
    """Load MT5 credentials from base .env; raise if .env is missing or incomplete."""
    env_path = ".env"
    if not os.path.exists(env_path):
        raise FileNotFoundError(
            "Genesis requires a .env file for MT5 credentials. "
            "Copy .env.example → .env and fill in MT5_LOGIN / MT5_PASSWORD / MT5_SERVER."
        )
    parsed = _parse_env_file(env_path)
    lower = {k.lower(): v for k, v in parsed.items()}
    missing = [k for k in ("mt5_login", "mt5_password", "mt5_server") if not lower.get(k)]
    if missing:
        raise ValueError(
            f"Base .env is missing required MT5 credentials: {missing}. "
            "Genesis is locked to a single account — credentials must be set in .env."
        )
    return {
        "mt5_login": int(lower["mt5_login"]),
        "mt5_password": lower["mt5_password"],
        "mt5_server": lower["mt5_server"],
        "mt5_path": lower.get("mt5_path", ""),
    }


# ── Build settings ─────────────────────────────────────────────────────────
# All values are assembled into one dict then passed to BotSettings(**…) so
# pydantic validates every field in one shot. No setattr after construction.

# ┌─ Singleton settings ────────────────────────────────────────────────────────────────
# All values are parsed from .env files into ONE dict, then passed to
# BotSettings(**final_dict) so pydantic validates every field in a single
# pass. No setattr after construction. No nested re-reads.
#
# MT5 credentials are ALWAYS locked to base .env (lines 375-388 override
# anything a profile file may have placed in _final).

profile = os.getenv("GENESIS_PROFILE")

# --- Step 1: Load base .env and build the canonical dict ---
_base_parsed = _parse_env_file(".env")

def _base_key(key: str):
    """Resolve a key from base .env, trying UPPERCASE first, then lowercase."""
    return _base_parsed.get(key.upper()) or _base_parsed.get(key.lower()) or ""

_final = {}

# Core MT5 credentials — single source of truth
_mt5_built = {
    "mt5_login":    int(_base_key("mt5_login") or "0"),
    "mt5_password": _base_key("mt5_password"),
    "mt5_server":   _base_key("mt5_server") or "MetaQuotes-Demo",
    "mt5_path":     _base_key("mt5_path"),
}

# Profile meta
_active_profile = (profile or _base_key("active_profile") or "default").lower()

# All other base keys → snake_case lower → _final
_JF = {"trading_pairs"}  # JSON fields

for key, val in _base_parsed.items():
    snake = key.strip().lower()
    if snake in _mt5_built:
        continue  # MT5 handled separately above
    if snake == "active_profile":
        continue  # handled below
    # Coerce JSON-list fields
    if snake in _JF:
        import json as _jn
        try:
            val_coerced = _jn.loads(val)
        except Exception:
            val_coerced = [s.strip().strip('"') for s in val.strip("[]").split(",") if s.strip()]
    else:
        val_coerced = val
    _final[snake] = val_coerced

# Inject MT5 (guarantees correct source of truth)
for k, v in _mt5_built.items():
    _final[k] = v

# Set profile
_final["active_profile"] = _active_profile

# --- Step 2: Profile overrides (strategy-only, MT5 keys stripped) ---
if profile:
    profile_file = f".env.{profile.lower()}"
    if os.path.exists(profile_file):
        parsed_pf = _parse_env_file(profile_file)
        _mt5_ban = {"mt5_login","mt5_password","mt5_server","mt5_path",
                     "MT5_LOGIN","MT5_PASSWORD","MT5_SERVER","MT5_PATH"}
        for key, val in parsed_pf.items():
            if key in _mt5_ban or key.upper() in _mt5_ban:
                continue
            snake = key.strip().lower()
            if snake in _JF:
                import json as _jn
                try:
                    val_coerced = _jn.loads(val)
                except Exception:
                    val_coerced = [s.strip().strip('"') for s in val.strip("[]").split(",") if s.strip()]
            else:
                val_coerced = val
            _final[snake] = val_coerced

# --- Step 3: Construct pydantic settings (single pass, validated) ---
settings = BotSettings(**_final)

# --- Step 4: Post-construction adjustments ---
# Scalper uses M15 gatekeeper (faster short-term pullback filter than H1).
if settings.active_profile == "scalper":
    settings.gatekeeper_timeframe = "M15"
