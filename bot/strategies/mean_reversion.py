"""
Mean Reversion Strategy — Used in ranging/sideways markets.

Enhanced for Day Trader profile with Bollinger Bands + RSI for Asian session.

Buys at support / lower Bollinger Band, sells at resistance / upper Bollinger Band
when the market is NOT trending. Uses RSI extremes + Bollinger Bands for entries
during quiet hours.

Two independent entry paths (OR logic — whichever triggers first wins):
  Path A — S/R Levels:   Price near S/R level + RSI extreme
  Path B — Bollinger Bands: Price near BB outer band + RSI extreme
"""

from typing import Optional
import pandas as pd
import numpy as np
from loguru import logger


from bot.strategies.base_strategy import BaseStrategy, TradeSignal
from bot.core.data_fetcher import DataFetcher
from bot.config.settings import settings, TradeDirection, MarketRegime



# ── Bollinger Band Configuration ─────────────────────────────────
BB_PERIOD = 20
BB_STD_DEV = 2.0
BB_PROXIMITY_THRESHOLD = 0.002  # 0.2% proximity to band for entry


class MeanReversionStrategy(BaseStrategy):
    """
    Mean Reversion — for ranging markets.

    Enhanced for Day Trader profile:
    - Session-aware (Asian session hours only)
    - Dual entry path: S/R Levels OR Bollinger Bands + RSI
    - ADX < 20 confirmation for ranging market

    Entry Logic (BUY) — Path A (S/R):
    1. H4: ADX < 20 (ranging)
    2. M15: Price near support level
    3. M15: RSI < 35 (near oversold)
    4. Entry at ask, SL below support, TP at next resistance

    Entry Logic (BUY) — Path B (Bollinger):
    1. H4: ADX < 20 (ranging)
    2. M15: Price near or below lower Bollinger Band (20, 2)
    3. M15: RSI < 35 (near oversold)
    4. Entry at ask, SL at BB - 1.5×ATR, TP at middle BB

    Entry Logic (SELL) — mirrored for resistance / upper band.
    """

    def __init__(self):
        super().__init__()
        self._name = "Mean Reversion"
        self._description = "Range-bound — buy support/BB lower, sell resistance/BB upper"
        self._suitable_regimes = [MarketRegime.RANGING]
        self._proximity_threshold = 0.001  # 0.1% proximity to S/R level
        # BB proximity threshold uses the module-level BB_PROXIMITY_THRESHOLD (0.2 %)
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
            self._fetcher = DataFetcher(None)
        return self._fetcher

    # ── Public API ──────────────────────────────────────────────────

    def generate_signal(
        self,
        symbol: str,
        higher_tf_data: pd.DataFrame,
        entry_tf_data: pd.DataFrame,
        current_price: dict,
    ) -> Optional[TradeSignal]:
        """Generate a mean reversion signal.

        Two independent entry paths:
          Path A: Price near S/R level + RSI extreme
          Path B: Price near Bollinger Band outer edge + RSI extreme

        Returns the first valid signal found, or None.
        """

        if higher_tf_data is None or entry_tf_data is None or current_price is None:
            return None

        # ── Step 1: Confirm ranging market ─────────────────────────
        if not self._is_ranging(higher_tf_data):
            return None

        # ── Step 2: Calculate Bollinger Bands from entry TF ─────────
        bb = self._calculate_bollinger_bands(entry_tf_data)

        # ── Step 3: Find S/R levels (delegated to DataFetcher) ──────
        sr_levels = self._sr_fetcher.get_support_resistance(entry_tf_data)

        bid = current_price["bid"]
        ask = current_price["ask"]

        # ── Path A: S/R Level Entries (primary) ────────────────────
        if sr_levels["support"] and sr_levels["resistance"]:
            buy_signal = self._check_buy_at_support(
                symbol, entry_tf_data, ask, sr_levels
            )
            if buy_signal:
                return buy_signal

            sell_signal = self._check_sell_at_resistance(
                symbol, entry_tf_data, bid, sr_levels
            )
            if sell_signal:
                return sell_signal

        # ── Path B: Bollinger Band Entries (secondary) ──────────────
        if bb is not None:
            buy_signal = self._check_buy_at_lower_band(
                symbol, entry_tf_data, ask, bb
            )
            if buy_signal:
                return buy_signal

            sell_signal = self._check_sell_at_upper_band(
                symbol, entry_tf_data, bid, bb
            )
            if sell_signal:
                return sell_signal

        return None

    # ── Bollinger Bands ─────────────────────────────────────────────

    @staticmethod
    def _calculate_bollinger_bands(df: pd.DataFrame) -> Optional[dict]:
        """Calculate Bollinger Bands (20, 2) from the entry TF close prices.

        Uses pandas rolling mean + std (population, ddof=0) to match the
        standard BB(20, 2) definition.  Returns None if insufficient data
        (< BB_PERIOD bars) or the latest values are NaN.

        Returns:
            Dict with keys ``lower``, ``middle``, ``upper``, or None.
        """
        if df is None or df.empty or len(df) < BB_PERIOD:
            return None

        close = df["close"]
        bb_sma = close.rolling(window=BB_PERIOD).mean()
        bb_std = close.rolling(window=BB_PERIOD).std(ddof=0)  # population std

        middle = bb_sma.iloc[-1]
        std_val = bb_std.iloc[-1]

        if pd.isna(middle) or pd.isna(std_val):
            return None

        return {
            "lower": float(middle - BB_STD_DEV * std_val),
            "middle": float(middle),
            "upper": float(middle + BB_STD_DEV * std_val),
            "width": float(2.0 * BB_STD_DEV * std_val / middle) if middle != 0 else 0.0,
        }

    # ── Ranging Check ───────────────────────────────────────────────

    def _is_ranging(self, htf_data: pd.DataFrame) -> bool:
        """Check if the higher timeframe is in a ranging state."""
        if "adx" not in htf_data.columns:
            return False

        latest_adx = htf_data["adx"].iloc[-1]
        if np.isnan(latest_adx):
            return False

        return latest_adx < settings.adx_range_threshold

    # ── Path A: S/R Level Entries ───────────────────────────────────

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

        # RSI must be near oversold
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

        # RSI must be near overbought
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

    # ── Path B: Bollinger Band Entries ───────────────────────────────

    def _check_buy_at_lower_band(
        self,
        symbol: str,
        etf_data: pd.DataFrame,
        price: float,
        bb: dict,
    ) -> Optional[TradeSignal]:
        """Check if price is near or below the lower Bollinger Band with
        RSI oversold — suggests the asset is statistically overextended
        to the downside and due for a mean-reversion bounce."""

        rsi_col = f"rsi_{settings.rsi_period}"
        atr_col = f"atr_{settings.atr_period}"

        if rsi_col not in etf_data.columns or atr_col not in etf_data.columns:
            return None

        current_rsi = etf_data[rsi_col].iloc[-1]
        current_atr = etf_data[atr_col].iloc[-1]

        if np.isnan(current_rsi) or np.isnan(current_atr):
            return None

        # RSI must be near oversold (same buffer as S/R path)
        if current_rsi > settings.rsi_oversold + 5:
            return None

        bb_lower = bb["lower"]
        bb_middle = bb["middle"]

        # Price must be at or below the lower band (within proximity threshold)
        if price <= bb_lower:
            # Price is below the band — extreme overshoot
            distance = 0.0
        else:
            distance = abs(price - bb_lower) / price

        if distance > BB_PROXIMITY_THRESHOLD:
            return None

        # ── Build signal ────────────────────────────────────────────
        sl = bb_lower - (current_atr * settings.atr_sl_multiplier)
        tp = bb_middle  # Mean reversion target: back to the middle band

        signal = TradeSignal(
            direction=TradeDirection.BUY,
            symbol=symbol,
            entry_price=price,
            stop_loss=sl,
            take_profit=tp,
            confidence=0.65,
            strategy_name=self._name,
            timeframe=settings.entry_timeframe,
            reason=(
                f"Buy at lower BB {bb_lower:.5f} (mid={bb_middle:.5f}, "
                f"RSI={current_rsi:.1f})"
            ),
        )

        if self.validate_signal(signal):
            logger.info(f"📊 Signal: {signal}")
            return signal

        return None

    def _check_sell_at_upper_band(
        self,
        symbol: str,
        etf_data: pd.DataFrame,
        price: float,
        bb: dict,
    ) -> Optional[TradeSignal]:
        """Check if price is near or above the upper Bollinger Band with
        RSI overbought — suggests the asset is statistically overextended
        to the upside and due for a mean-reversion pullback."""

        rsi_col = f"rsi_{settings.rsi_period}"
        atr_col = f"atr_{settings.atr_period}"

        if rsi_col not in etf_data.columns or atr_col not in etf_data.columns:
            return None

        current_rsi = etf_data[rsi_col].iloc[-1]
        current_atr = etf_data[atr_col].iloc[-1]

        if np.isnan(current_rsi) or np.isnan(current_atr):
            return None

        # RSI must be near overbought (same buffer as S/R path)
        if current_rsi < settings.rsi_overbought - 5:
            return None

        bb_upper = bb["upper"]
        bb_middle = bb["middle"]

        # Price must be at or above the upper band (within proximity threshold)
        if price >= bb_upper:
            # Price is above the band — extreme overshoot
            distance = 0.0
        else:
            distance = abs(price - bb_upper) / price

        if distance > BB_PROXIMITY_THRESHOLD:
            return None

        # ── Build signal ────────────────────────────────────────────
        sl = bb_upper + (current_atr * settings.atr_sl_multiplier)
        tp = bb_middle  # Mean reversion target: back to the middle band

        signal = TradeSignal(
            direction=TradeDirection.SELL,
            symbol=symbol,
            entry_price=price,
            stop_loss=sl,
            take_profit=tp,
            confidence=0.65,
            strategy_name=self._name,
            timeframe=settings.entry_timeframe,
            reason=(
                f"Sell at upper BB {bb_upper:.5f} (mid={bb_middle:.5f}, "
                f"RSI={current_rsi:.1f})"
            ),
        )

        if self.validate_signal(signal):
            logger.info(f"📊 Signal: {signal}")
            return signal

        return None
