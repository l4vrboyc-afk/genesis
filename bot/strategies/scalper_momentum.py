import pandas as pd
from typing import Optional
from bot.config.settings import settings, MarketRegime, TradeDirection
from bot.strategies.base_strategy import BaseStrategy, TradeSignal

class ScalperMomentumStrategy(BaseStrategy):
    """
    Scalper Momentum Strategy.
    Capitalizes on short-term momentum bursts using RSI and volume spikes on M1/M5.
    """

    def __init__(self):
        super().__init__()
        self._name = "Scalper Momentum"
        self._description = "Aggressive intraday scalping based on momentum"
        self._suitable_regimes = [MarketRegime.TRENDING, MarketRegime.VOLATILE]

    def generate_signal(
        self,
        symbol: str,
        higher_tf_data: pd.DataFrame,
        entry_tf_data: pd.DataFrame,
        current_price: dict,
    ) -> Optional[TradeSignal]:
        if entry_tf_data is None or entry_tf_data.empty:
            return None

        latest = entry_tf_data.iloc[-1]

        # Look for aggressive RSI momentum
        rsi = latest.get("rsi", 50)
        atr = latest.get("atr", 0.001)

        direction = TradeDirection.HOLD
        confidence = 0.0

        bid = current_price.get("bid", 0.0)
        ask = current_price.get("ask", 0.0)
        entry = 0.0
        sl = 0.0
        tp = 0.0
        reason = ""

        # Simple momentum logic: if RSI is pumping hard (> 65) but not entirely exhausted
        if 65 < rsi < 85:
            direction = TradeDirection.BUY
            confidence = 0.8
            entry = ask
            sl = entry - (atr * settings.atr_sl_multiplier)
            tp = entry + (atr * settings.atr_tp_multiplier)
            reason = f"Bullish momentum spike (RSI {rsi:.1f})"

        elif 15 < rsi < 35:
            direction = TradeDirection.SELL
            confidence = 0.8
            entry = bid
            sl = entry + (atr * settings.atr_sl_multiplier)
            tp = entry - (atr * settings.atr_tp_multiplier)
            reason = f"Bearish momentum spike (RSI {rsi:.1f})"

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
            reason=reason
        )

        risk = abs(entry - sl)
        reward = abs(tp - entry)
        signal.risk_reward_ratio = round(reward / risk, 2) if risk > 0 else 0.0

        if self.validate_signal(signal):
            return signal
        return None
