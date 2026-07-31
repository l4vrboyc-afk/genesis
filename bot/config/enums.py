"""Genesis System Enums."""
from __future__ import annotations

from enum import Enum
from bot.config.settings import MarketRegime, TradeDirection


class Profile(str, Enum):
    """Active user trading profile."""
    DEFAULT = "default"   # SmartTrend + FVG / Bollinger
    SCALPER = "scalper"   # Fast M5 range-fading scalping (M1->M5 entry)
    BREAKOUT = "breakout" # Session opening range breakout (M15 / H4)
    DAYTRADER = "daytrader"  # 3x14 Session Matrix (M15 / H1)


class TrailingState(str, Enum):
    """4 states of an active trade's risk lifecycle."""
    INITIAL_RISK = "STATIC"        # Full initial SL distance
    BREAKEVEN = "BREAKEVEN"        # SL moved to entry + spread
    STRUCTURE_TRAIL = "STRUCTURE"  # SL stepping behind M15 swing highs/lows
    VOLATILITY_LOCK = "ATR_DYNAMIC"  # Tight 1.0x ATR trail on parabolic moves


class SignalType(str, Enum):
    """Trade signal direction."""
    BUY = "buy"
    SELL = "sell"


# User-facing labels
PROFILE_LABELS = {
    Profile.DEFAULT: "Default (H4->M15 Multi-TF)",
    Profile.SCALPER: "Scalper (M1/M5 Range-Fade)",
    Profile.BREAKOUT: "Breakout Hunter (M15/H4)",
    Profile.DAYTRADER: "Day Trader (M15/H1 Session Matrix)",
}

# Regime emoji
REGIME_ICONS = {
    "trending": "📈",
    "ranging": "↔️",
    "volatile": "💥",
    "dead": "🪦",
    "news_event": "🛡️",
}
