"""
Genesis Trading Bot — Strategies Package.

Track (d): ScalperMomentum and SessionBreakout are conditionally
imported based on ``settings.active_profile``. The default profile
keeps only SmartTrend + MeanReversion to avoid surprising operators
who didn't opt into the higher-frequency / higher-TF strategies.
The selection happens at module-load time so the rest of the bot
never sees an import error when ``profile=default``.
"""
from bot.config.settings import settings
from bot.strategies.base_strategy import BaseStrategy, TradeSignal
from bot.strategies.smart_trend import SmartTrendStrategy
from bot.strategies.mean_reversion import MeanReversionStrategy
from bot.strategies.strategy_selector import StrategySelector

# ── Profile-gated imports (Track d) ─────────────────────────────────
_profile = (settings.active_profile or "default").lower()

if _profile == "scalper":
    from bot.strategies.scalper_momentum import ScalperMomentumStrategy
elif _profile == "breakout":
    from bot.strategies.session_breakout import SessionBreakoutStrategy

__all__ = [
    "BaseStrategy", "TradeSignal",
    "SmartTrendStrategy", "MeanReversionStrategy",
    "StrategySelector",
]

# Populate __all__ based on the active profile so ``from
# bot.strategies import ScalperMomentumStrategy`` Just Works for
# the matching profile. Default profile omits the profile-gated
# names — importing them on default is a soft error rather than a
# hard one (the symbol is not in __all__, the module attribute
# is still reachable via direct import for backward compat).
if _profile == "scalper" and "ScalperMomentumStrategy" in dir():
    __all__.append("ScalperMomentumStrategy")
elif _profile == "breakout" and "SessionBreakoutStrategy" in dir():
    __all__.append("SessionBreakoutStrategy")
