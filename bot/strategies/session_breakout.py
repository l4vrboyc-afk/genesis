import pandas as pd
from datetime import datetime, timezone
from typing import Optional
from bot.config.settings import settings, MarketRegime, TradeDirection
from bot.strategies.base_strategy import BaseStrategy, TradeSignal

class SessionBreakoutStrategy(BaseStrategy):
    """
    Session Breakout Strategy.
    Trades the opening volatility of the London or New York sessions.
    """

    def __init__(self):
        super().__init__()
        self._name = "Session Breakout"
        self._description = "Targets London/NY session opening volatility"
        self._suitable_regimes = [MarketRegime.TRENDING, MarketRegime.RANGING]

    def _is_active_session(self) -> bool:
        # Simplified: London opens ~8 UTC, NY opens ~13 UTC
        current_hour = datetime.now(timezone.utc).hour
        is_london_open = 8 <= current_hour <= 10
        is_ny_open = 13 <= current_hour <= 15
        return is_london_open or is_ny_open

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
        atr = entry_tf_data.iloc[-1].get("atr", 0.001)

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

        risk = abs(entry - sl)
        reward = abs(tp - entry)
        signal.risk_reward_ratio = round(reward / risk, 2) if risk > 0 else 0.0

        if self.validate_signal(signal):
            return signal
        return None
