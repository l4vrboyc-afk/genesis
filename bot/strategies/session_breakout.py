"""
Session Breakout Strategy.

Enhanced version for Day Trader profile - uses session_manager for
precise session timing during London (07:00 UTC) and NY (12:00 UTC) opens.
"""

import math
import pandas as pd

from typing import Optional


from bot.config.settings import settings, MarketRegime, TradeDirection
from bot.strategies.base_strategy import BaseStrategy, TradeSignal
from bot.strategies.session_manager import get_session_manager


class SessionBreakoutStrategy(BaseStrategy):
    """
    Session Breakout Strategy for London and New York session opens.

    Session Open Windows (UTC):
    - London: 07:00 - 07:30 (opening window)
    - New York: 12:00 - 12:30 (opening window)

    Enters on momentum as volume spikes at session open.
    """

    def __init__(self):
        super().__init__()
        self._name = "Session Breakout"
        self._description = "Targets London/NY session opening volatility"
        self._suitable_regimes = [MarketRegime.TRENDING, MarketRegime.RANGING]

        # Session manager for precise timing
        self._session_mgr = get_session_manager()

        # Opening window duration (minutes) for breakout detection
        self._opening_window_minutes = 30

    def _is_active_session(self) -> bool:
        """Check if we're in an opening window of an active session."""
        session_info = self._session_mgr.get_current_session()
        return session_info.is_opening_window

    def generate_signal(
        self,
        symbol: str,
        higher_tf_data: pd.DataFrame,
        entry_tf_data: pd.DataFrame,
        current_price: dict,
    ) -> Optional[TradeSignal]:

        if not self._is_active_session():
            return None

        if entry_tf_data is None or entry_tf_data.empty or len(entry_tf_data) < 20:
            return None

        # Look at last 20 candles for a breakout
        recent_high = entry_tf_data['high'].iloc[-20:-1].max()
        recent_low = entry_tf_data['low'].iloc[-20:-1].min()

        bid = current_price.get("bid", 0.0)
        ask = current_price.get("ask", 0.0)
        # Indicator columns include the configured period suffix
        # (``atr_{atr_period}``). Reading "atr" never matches, so the loop
        # used to silently fall back to ATR=0.001 and place orders with
        # essentially zero SL/TP distance.
        atr_col = f"atr_{settings.atr_period}"
        atr = entry_tf_data[atr_col].iloc[-1] if atr_col in entry_tf_data.columns else 0.001
        try:
            if atr is None or (isinstance(atr, float) and math.isnan(atr)):
                atr = 0.001
        except Exception:
            atr = 0.001

        direction = TradeDirection.HOLD
        confidence = 0.0
        entry = 0.0
        sl = 0.0
        tp = 0.0
        reason = ""

        # Breakout to upside
        if ask > recent_high:
            direction = TradeDirection.BUY
            confidence = 0.9
            entry = ask
            sl = recent_low  # stop loss below the range
            tp = entry + ((entry - sl) * settings.min_reward_ratio)
            reason = "Session High Breakout"

        # Breakout to downside
        elif bid < recent_low:
            direction = TradeDirection.SELL
            confidence = 0.9
            entry = bid
            sl = recent_high  # stop loss above the range
            tp = entry - ((sl - entry) * settings.min_reward_ratio)
            reason = "Session Low Breakout"

        if direction == TradeDirection.HOLD:
            return None

        signal = TradeSignal(
            direction=direction,
            symbol=symbol,
            entry_price=entry,
            stop_loss=sl,
            take_profit=tp,
            confidence=confidence,
            strategy_name=self.name,
            timeframe=settings.entry_timeframe,
            reason=reason,
        )

        # NOTE: ``risk_reward_ratio`` is a derived property on TradeSignal
        # (read-only). Mutating it raised AttributeError; validate_signal()
        # already inspects the property from entry/SL/TP, so the
        # assignment found in earlier drafts is removed here.

        if self.validate_signal(signal):
            return signal
        return None
