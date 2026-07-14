"""
Mean Reversion Strategy — Used in ranging/sideways markets.

Buys at support, sells at resistance when the market is NOT trending.
Uses RSI extremes + support/resistance levels for entries.
"""

from typing import Optional
import pandas as pd
import numpy as np
from loguru import logger

from bot.strategies.base_strategy import BaseStrategy, TradeSignal
from bot.core.data_fetcher import DataFetcher
from bot.config.settings import settings, TradeDirection, MarketRegime


class MeanReversionStrategy(BaseStrategy):
    """
    Mean Reversion — for ranging markets.

    Entry Logic (BUY):
    1. H4: ADX < 20 (market is NOT trending — ranging)
    2. M15: Price near support level
    3. M15: RSI < 30 (oversold)
    4. Entry at market, SL below support, TP at resistance

    Entry Logic (SELL):
    1. H4: ADX < 20 (ranging)
    2. M15: Price near resistance level
    3. M15: RSI > 70 (overbought)
    4. Entry at market, SL above resistance, TP at support
    """

    def __init__(self):
        super().__init__()
        self._name = "Mean Reversion"
        self._description = "Range-bound trading — buy support, sell resistance"
        self._suitable_regimes = [MarketRegime.RANGING]
        self._proximity_threshold = 0.001  # 0.1% proximity to S/R level
        # Stateless S/R reuse — ``get_support_resistance`` is a pure
        # DataFrame transform and never reaches for MT5, so connector=None
        # is safe. The instance is lazily resolved so we don't keep a
        # permanent half-wired reference and we avoid a brittle ``None``
        # pre-allocation that future contributors will trip over.
        self._fetcher = None

    @property
    def _sr_fetcher(self):
        if self._fetcher is None:
            # Local import keeps the strategy import-cost flat for the
            # other strategies that don't need the fetcher at all.
            from bot.core.data_fetcher import DataFetcher
            self._fetcher = DataFetcher(None)
        return self._fetcher

    def generate_signal(
        self,
        symbol: str,
        higher_tf_data: pd.DataFrame,
        entry_tf_data: pd.DataFrame,
        current_price: dict,
    ) -> Optional[TradeSignal]:
        """Generate a mean reversion signal."""

        if higher_tf_data is None or entry_tf_data is None or current_price is None:
            return None

        # ── Step 1: Confirm ranging market ─────────────────────────
        if not self._is_ranging(higher_tf_data):
            return None

        # ── Step 2: Find S/R levels (delegated to DataFetcher) ──────
        sr_levels = self._sr_fetcher.get_support_resistance(entry_tf_data)

        if not sr_levels["support"] or not sr_levels["resistance"]:
            return None

        bid = current_price["bid"]
        ask = current_price["ask"]

        # ── Step 3: Check for buy setup (near support) ─────────────
        buy_signal = self._check_buy_at_support(
            symbol, entry_tf_data, ask, sr_levels
        )
        if buy_signal:
            return buy_signal

        # ── Step 4: Check for sell setup (near resistance) ─────────
        sell_signal = self._check_sell_at_resistance(
            symbol, entry_tf_data, bid, sr_levels
        )
        if sell_signal:
            return sell_signal

        return None

    # ── Internal Checks ─────────────────────────────────────────────

    def _is_ranging(self, htf_data: pd.DataFrame) -> bool:
        """Check if the higher timeframe is in a ranging state."""
        if "adx" not in htf_data.columns:
            return False

        latest_adx = htf_data["adx"].iloc[-1]
        if np.isnan(latest_adx):
            return False

        return latest_adx < settings.adx_range_threshold

    def _check_buy_at_support(
        self,
        symbol: str,
        etf_data: pd.DataFrame,
        price: float,
        sr_levels: dict,
    ) -> Optional[TradeSignal]:
        """Check if price is near support with RSI oversold."""

        rsi_col = f"rsi_{settings.rsi_period}"
        atr_col = f"atr_{settings.atr_period}"

        if rsi_col not in etf_data.columns or atr_col not in etf_data.columns:
            return None

        current_rsi = etf_data[rsi_col].iloc[-1]
        current_atr = etf_data[atr_col].iloc[-1]

        if np.isnan(current_rsi) or np.isnan(current_atr):
            return None

        # RSI must be oversold
        if current_rsi > settings.rsi_oversold + 5:  # Give 5 points buffer
            return None

        # Check proximity to support
        for support in sr_levels["support"]:
            distance = abs(price - support) / price
            if distance < self._proximity_threshold:
                # Found support — calculate SL/TP
                sl = support - (current_atr * settings.atr_sl_multiplier)

                # TP at nearest resistance
                tp = None
                for resistance in sr_levels["resistance"]:
                    if resistance > price:
                        tp = resistance
                        break

                if tp is None:
                    tp = price + (current_atr * settings.atr_tp_multiplier)

                signal = TradeSignal(
                    direction=TradeDirection.BUY,
                    symbol=symbol,
                    entry_price=price,
                    stop_loss=sl,
                    take_profit=tp,
                    confidence=0.6,
                    strategy_name=self._name,
                    timeframe=settings.entry_timeframe,
                    reason=f"Buy at support {support:.5f}, RSI={current_rsi:.1f}",
                )

                if self.validate_signal(signal):
                    logger.info(f"📊 Signal: {signal}")
                    return signal

        return None

    def _check_sell_at_resistance(
        self,
        symbol: str,
        etf_data: pd.DataFrame,
        price: float,
        sr_levels: dict,
    ) -> Optional[TradeSignal]:
        """Check if price is near resistance with RSI overbought."""

        rsi_col = f"rsi_{settings.rsi_period}"
        atr_col = f"atr_{settings.atr_period}"

        if rsi_col not in etf_data.columns or atr_col not in etf_data.columns:
            return None

        current_rsi = etf_data[rsi_col].iloc[-1]
        current_atr = etf_data[atr_col].iloc[-1]

        if np.isnan(current_rsi) or np.isnan(current_atr):
            return None

        # RSI must be overbought
        if current_rsi < settings.rsi_overbought - 5:
            return None

        # Check proximity to resistance
        for resistance in sr_levels["resistance"]:
            distance = abs(price - resistance) / price
            if distance < self._proximity_threshold:
                # Found resistance — calculate SL/TP
                sl = resistance + (current_atr * settings.atr_sl_multiplier)

                # TP at nearest support
                tp = None
                for support in sr_levels["support"]:
                    if support < price:
                        tp = support
                        break

                if tp is None:
                    tp = price - (current_atr * settings.atr_tp_multiplier)

                signal = TradeSignal(
                    direction=TradeDirection.SELL,
                    symbol=symbol,
                    entry_price=price,
                    stop_loss=sl,
                    take_profit=tp,
                    confidence=0.6,
                    strategy_name=self._name,
                    timeframe=settings.entry_timeframe,
                    reason=f"Sell at resistance {resistance:.5f}, RSI={current_rsi:.1f}",
                )

                if self.validate_signal(signal):
                    logger.info(f"📊 Signal: {signal}")
                    return signal

        return None
