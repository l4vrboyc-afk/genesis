"""
Trend Engine Strategy — Trend continuation for high-volume sessions.

Part of the Day Trader "Market Matrix" profile. Designed to run during
London/NY sessions when ADX > 25 and volume is healthy.

Logic:
- Waits for price to pull back to moving average (e.g., 21 EMA) in a confirmed trend
- Buys dips in uptrends (price > EMA21, pull back to EMA21, enter BUY)
- Sells rallies in downtrends (price < EMA21, rally up to EMA21, enter SELL)

This strategy is session-aware and only runs during active trading sessions
(London 07:00-16:00 UTC, NY 12:00-20:00 UTC, or their overlap).
"""

import pandas as pd
from typing import Optional
from loguru import logger

from bot.config.settings import settings, MarketRegime, TradeDirection
from bot.strategies.base_strategy import BaseStrategy, TradeSignal
from bot.strategies.session_manager import get_session_manager, MarketSession


class TrendEngineStrategy(BaseStrategy):
    """
    Trend continuation strategy for high-volume London/NY sessions.

    Uses pullback to EMA after confirming trend direction via ADX.
    Designed for the Day Trader profile's "Market Matrix".

    Entry Logic:
    - Trend confirmed: ADX > 25
    - Direction: Price > EMA21 = uptrend, Price < EMA21 = downtrend
    - Entry: Pullback to EMA21 with momentum confirmation

    Session Requirements:
    - Only runs during London (07:00-16:00 UTC) or NY (12:00-20:00 UTC) sessions
    - Session must have been active for at least 5 minutes
    """

    def __init__(self):
        super().__init__()
        self._name = "Trend Engine"
        self._description = (
            "Trend continuation via EMA pullback in high-volume sessions. "
            "Active London/NY sessions when ADX > 25."
        )
        self._suitable_regimes = [MarketRegime.TRENDING]

        # EMA period for trend detection and pullback entry
        self._ema_period = 21

        # Settings from profile
        self._adx_trend_threshold = settings.adx_trend_threshold

        # Session manager for time-based filtering
        self._session_mgr = get_session_manager()

    def _is_session_active(self) -> bool:
        """Check if we're in an active London or NY session."""
        session_info = self._session_mgr.get_current_session()

        # Accept London, NY, or overlap sessions
        return session_info.session in (MarketSession.LONDON,
                                        MarketSession.NEW_YORK,
                                        MarketSession.OVERLAP)

    def _get_current_price(self, df: pd.DataFrame, atr_col: str) -> tuple:
        """Extract current price and ATR from the entry timeframe data."""
        if df is None or df.empty:
            return None, None

        latest = df.iloc[-1]

        # Get current price (use midpoint of bid/ask if available)
        bid = latest.get("bid", 0.0)
        ask = latest.get("ask", 0.0)
        if bid and ask:
            current_price = (bid + ask) / 2
        else:
            current_price = latest.get("close", 0.0)

        atr = latest.get(atr_col, 0.0)
        if pd.isna(atr) or atr is None:
            atr = 0.0

        return current_price, atr

    def generate_signal(
        self,
        symbol: str,
        higher_tf_data: pd.DataFrame,
        entry_tf_data: pd.DataFrame,
        current_price: dict,
    ) -> Optional[TradeSignal]:
        """
        Generate a trend continuation signal.

        Args:
            symbol: Trading pair (e.g., "EURUSD")
            higher_tf_data: H4 data with indicators
            entry_tf_data: M15 data with indicators
            current_price: Current bid/ask dict

        Returns:
            TradeSignal if valid setup found, None otherwise
        """
        # ── Session Check ────────────────────────────────────────
        if not self._is_session_active():
            logger.debug(f"Trend Engine: Outside active session for {symbol}")
            return None

        # ── Data Sufficiency ──────────────────────────────────────
        if higher_tf_data is None or higher_tf_data.empty:
            return None
        if entry_tf_data is None or entry_tf_data.empty:
            return None

        # Need enough data for EMA calculation
        ema_required = self._ema_period + 10
        if len(entry_tf_data) < ema_required:
            return None

        # ── Trend Detection ───────────────────────────────────────
        latest = entry_tf_data.iloc[-1]

        # Get ADX - trend must be confirmed
        adx_col = "adx"
        adx = latest.get(adx_col, 0.0)

        if adx < self._adx_trend_threshold:
            logger.debug(f"Trend Engine: ADX {adx:.1f} below threshold {self._adx_trend_threshold}")
            return None

        # ── Determine Trend Direction ─────────────────────────────
        ema_col = f"ema_{self._ema_period}"

        if ema_col not in entry_tf_data.columns:
            logger.warning(f"Trend Engine: EMA column {ema_col} not found")
            return None

        # Calculate EMA if not present
        if "close" in entry_tf_data.columns:
            prices = entry_tf_data["close"]
        else:
            # Fallback - won't work but prevents crash
            return None

        # Get EMA value
        ema_series = prices.rolling(window=self._ema_period).mean()
        if ema_series.isna().all():
            return None

        ema = ema_series.iloc[-1]

        current_price_value = latest.get("close", latest.get("bid", 0.0))

        # Determine trend direction
        is_uptrend = current_price_value > ema

        # ── Pullback Check ───────────────────────────────────────
        # Look for pullback toward EMA in the direction of trend
        atr_col = "atr_14"
        atr = latest.get(atr_col, 0.0)
        if pd.isna(atr) or atr <= 0:
            atr = 0.001  # Fallback

        # Get bid/ask if available
        bid = current_price.get("bid", current_price_value)
        ask = current_price.get("ask", current_price_value)

        if not bid or not ask:
            bid = ask = current_price_value

        # Signal generation based on trend direction
        if is_uptrend:
            # Uptrend: Look for pullback to EMA as entry
            # Price should have pulled back toward or below EMA
            signal = self._generate_buy_signal(
                symbol, latest, ema, atr, bid, ask, adx
            )
        else:
            # Downtrend: Look for rally up to EMA as entry
            signal = self._generate_sell_signal(
                symbol, latest, ema, atr, bid, ask, adx
            )

        return signal

    def _generate_buy_signal(
        self,
        symbol: str,
        latest: pd.Series,
        ema: float,
        atr: float,
        bid: float,
        ask: float,
        adx: float,
    ) -> Optional[TradeSignal]:
        """Generate buy signal on pullback to EMA in uptrend."""
        entry_price = ask

        # Pullback to EMA - we want to enter near EMA
        # If price is well above EMA, it's not a pullback
        price_distance = entry_price - ema

        # Check if we're in pullback territory (within 1x ATR of EMA)
        pullback_threshold = atr * 1.0

        if price_distance > pullback_threshold:
            logger.debug(f"Trend Engine {symbol}: Not in pullback zone (dist={price_distance:.5f})")
            return None

        # Calculate SL (below EMA minus some buffer)
        atr_sl_mult = settings.atr_sl_multiplier
        sl_distance = atr * atr_sl_mult
        sl = ema - sl_distance

        # Calculate TP
        target_move = (entry_price - sl) * settings.min_reward_ratio
        tp = entry_price + target_move

        confidence = min(0.5 + (adx - 25) * 0.02, 0.95)  # Higher ADX = higher confidence

        signal = TradeSignal(
            direction=TradeDirection.BUY,
            symbol=symbol,
            entry_price=entry_price,
            stop_loss=sl,
            take_profit=tp,
            confidence=round(confidence, 2),
            strategy_name=self._name,
            timeframe=settings.entry_timeframe,
            reason=f"Pullback to EMA {ema:.5f} in uptrend (ADX={adx:.1f})"
        )

        logger.info(f"[{self._name}] {symbol} BUY signal: Entry={entry_price:.5f}, "
                   f"SL={sl:.5f}, TP={tp:.5f}, R:R={signal.risk_reward_ratio:.2f}")

        return signal

    def _generate_sell_signal(
        self,
        symbol: str,
        latest: pd.Series,
        ema: float,
        atr: float,
        bid: float,
        ask: float,
        adx: float,
    ) -> Optional[TradeSignal]:
        """Generate sell signal on rally to EMA in downtrend."""
        entry_price = bid

        # Check if we're in rally territory (within 1x ATR of EMA)
        price_distance = ema - entry_price

        pullback_threshold = atr * 1.0

        if price_distance > pullback_threshold:
            logger.debug(f"Trend Engine {symbol}: Not in rally zone (dist={price_distance:.5f})")
            return None

        # Calculate SL (above EMA plus buffer)
        atr_sl_mult = settings.atr_sl_multiplier
        sl_distance = atr * atr_sl_mult
        sl = ema + sl_distance

        # Calculate TP
        target_move = (sl - entry_price) * settings.min_reward_ratio
        tp = entry_price - target_move

        confidence = min(0.5 + (adx - 25) * 0.02, 0.95)

        signal = TradeSignal(
            direction=TradeDirection.SELL,
            symbol=symbol,
            entry_price=entry_price,
            stop_loss=sl,
            take_profit=tp,
            confidence=round(confidence, 2),
            strategy_name=self._name,
            timeframe=settings.entry_timeframe,
            reason=f"Rally to EMA {ema:.5f} in downtrend (ADX={adx:.1f})"
        )

        logger.info(f"[{self._name}] {symbol} SELL signal: Entry={entry_price:.5f}, "
                    f"SL={sl:.5f}, TP={tp:.5f}, R:R={signal.risk_reward_ratio:.2f}")

        return signal