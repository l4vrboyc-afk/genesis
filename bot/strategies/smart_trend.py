"""
Smart Trend Breakout (STB) Strategy — Primary trading strategy.

Combines:
1. Multi-timeframe trend analysis (200 EMA + ADX on H4)
2. Smart Money entry zones (Order Blocks + Fair Value Gaps on M15)
3. RSI momentum confirmation
4. ATR-based dynamic stop loss and take profit
"""

from typing import Optional
import pandas as pd
import numpy as np
from loguru import logger

from bot.strategies.base_strategy import BaseStrategy, TradeSignal
from bot.config.settings import settings, TradeDirection, MarketRegime


class SmartTrendStrategy(BaseStrategy):
    """
    Smart Trend Breakout — merges institutional concepts with technical indicators.

    Entry Logic (BUY):
    1. H4: Price > 200 EMA AND ADX > 25 (confirmed uptrend)
    2. M15: Price pulls back to bullish Order Block OR Fair Value Gap
    3. M15: RSI crosses above 30 (oversold bounce confirmation)
    4. Entry at market, SL = 1.5x ATR below, TP = 3x ATR above

    Entry Logic (SELL):
    1. H4: Price < 200 EMA AND ADX > 25 (confirmed downtrend)
    2. M15: Price rallies to bearish Order Block OR Fair Value Gap
    3. M15: RSI crosses below 70 (overbought rejection confirmation)
    4. Entry at market, SL = 1.5x ATR above, TP = 3x ATR below

    Confidence scoring (sum, clamped to [0, 1.0]):
        ADX strength      : +0.25 ( > 35 ) or +0.15 ( > 25 )
        EMA slope, ATR-norm: +0.20 ( |slope|/ATR > 0.005 )
                            or +0.10 ( |slope|/ATR > 0.002 )
        OB vs FVG zone    : +0.25 ( OB ) or +0.15 ( FVG )
        Volume confirm    : +0.10 ( latest vol > 1.2x 20-bar mean )
        ATR calm          : +0.10 ( atr_ratio < settings.atr_volatility_spike )

    The EMA-slope contribution uses the linear-regression slope of the H4 200
    EMA over a 20-bar window (provided by `DataFetcher._rolling_slope`),
    normalized by the ATR so the score is comparable across low- and
    high-volatility pairs.
    """

    def __init__(self):
        super().__init__()
        self._name = "Smart Trend Breakout"
        self._description = "Multi-TF trend following with SMC entries"
        self._suitable_regimes = [MarketRegime.TRENDING]

    def generate_signal(
        self,
        symbol: str,
        higher_tf_data: pd.DataFrame,
        entry_tf_data: pd.DataFrame,
        current_price: dict,
    ) -> Optional[TradeSignal]:
        """Generate a trade signal based on the STB strategy."""

        if higher_tf_data is None or entry_tf_data is None or current_price is None:
            return None

        # ── Step 1: Higher TF Trend Filter (H4) ────────────────────
        trend_bias = self._check_trend(higher_tf_data)
        if trend_bias == TradeDirection.HOLD:
            return None

        # ── Step 2: Entry TF Smart Money Zone (M15) ────────────────
        smc_zone = self._check_smc_zone(entry_tf_data, trend_bias, current_price)
        if not smc_zone["valid"]:
            return None

        # ── Step 3: RSI Momentum Confirmation (M15) ────────────────
        rsi_confirmed = self._check_rsi_confirmation(entry_tf_data, trend_bias)
        if not rsi_confirmed:
            return None

        # ── Step 4: Calculate SL & TP ──────────────────────────────
        atr_col = f"atr_{settings.atr_period}"
        current_atr = entry_tf_data[atr_col].iloc[-1]

        if np.isnan(current_atr) or current_atr <= 0:
            return None

        if trend_bias == TradeDirection.BUY:
            entry_price = current_price["ask"]
            stop_loss = entry_price - (current_atr * settings.atr_sl_multiplier)
            take_profit = entry_price + (current_atr * settings.atr_tp_multiplier)
        else:
            entry_price = current_price["bid"]
            stop_loss = entry_price + (current_atr * settings.atr_sl_multiplier)
            take_profit = entry_price - (current_atr * settings.atr_tp_multiplier)

        # ── Build Signal ───────────────────────────────────────────
        confidence = self._calculate_confidence(
            higher_tf_data, entry_tf_data, smc_zone
        )

        signal = TradeSignal(
            direction=trend_bias,
            symbol=symbol,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            confidence=confidence,
            strategy_name=self._name,
            timeframe=settings.entry_timeframe,
            reason=smc_zone["reason"],
        )

        # Validate before returning
        if self.validate_signal(signal):
            logger.info(f"📊 Signal: {signal}")
            return signal

        return None

    # ── Internal Checks ─────────────────────────────────────────────

    def _check_trend(self, htf_data: pd.DataFrame) -> TradeDirection:
        """
        Check higher timeframe for trend direction.
        Requires price vs 200 EMA alignment, EMA slope agreement, and ADX > threshold.

        The slope-agreement check prevents acting on brief price crossovers when
        the underlying EMA is still rolling the opposite way — i.e. it forces
        a "real" trend instead of an EMA-flicker.
        """
        if len(htf_data) < settings.ema_period:
            return TradeDirection.HOLD

        latest = htf_data.iloc[-1]
        ema_col = f"ema_{settings.ema_period}"

        # Check if indicators exist
        if ema_col not in htf_data.columns or "adx" not in htf_data.columns:
            return TradeDirection.HOLD

        ema_value = latest[ema_col]
        ema_slope = latest.get("ema_slope", np.nan)
        adx_value = latest["adx"]
        close = latest["close"]

        # Must have valid data
        if np.isnan(ema_value) or np.isnan(adx_value):
            return TradeDirection.HOLD

        # ADX must show trending market
        if adx_value < settings.adx_trend_threshold:
            return TradeDirection.HOLD

        # EMA slope should agree with the price-vs-EMA direction.
        # If slope is missing/NaN we fall back to price vs EMA alone (legacy
        # behaviour for callers that hand in pre-calculated indicator frames).
        slope_positive = not np.isnan(ema_slope) and ema_slope > 0
        slope_negative = not np.isnan(ema_slope) and ema_slope < 0

        if close > ema_value and (slope_positive or np.isnan(ema_slope)):
            return TradeDirection.BUY
        elif close < ema_value and (slope_negative or np.isnan(ema_slope)):
            return TradeDirection.SELL

        return TradeDirection.HOLD

    def _check_smc_zone(
        self,
        etf_data: pd.DataFrame,
        bias: TradeDirection,
        current_price: dict,
    ) -> dict:
        """
        Check if current price is in a Smart Money zone (Order Block or FVG).
        Returns dict with 'valid' bool and 'reason' string.
        """
        result = {"valid": False, "reason": "", "zone_type": ""}

        price = current_price["bid"] if bias == TradeDirection.SELL else current_price["ask"]

        # Look at recent candles for active zones
        recent = etf_data.tail(50)

        if bias == TradeDirection.BUY:
            # Check for bullish Order Blocks
            ob_candles = recent[recent["ob_bullish"] == True]
            for idx, row in ob_candles.iterrows():
                if not np.isnan(row["ob_bull_low"]) and not np.isnan(row["ob_bull_high"]):
                    # Price is touching or inside the OB zone
                    if row["ob_bull_low"] <= price <= row["ob_bull_high"] * 1.001:
                        result["valid"] = True
                        result["reason"] = f"Bullish OB at {row['ob_bull_low']:.5f}-{row['ob_bull_high']:.5f}"
                        result["zone_type"] = "order_block"
                        return result

            # Check for bullish FVGs
            fvg_candles = recent[recent["fvg_bullish"] == True]
            for idx, row in fvg_candles.iterrows():
                if not np.isnan(row["fvg_bull_bottom"]) and not np.isnan(row["fvg_bull_top"]):
                    if row["fvg_bull_bottom"] <= price <= row["fvg_bull_top"]:
                        result["valid"] = True
                        result["reason"] = f"Bullish FVG at {row['fvg_bull_bottom']:.5f}-{row['fvg_bull_top']:.5f}"
                        result["zone_type"] = "fvg"
                        return result

        elif bias == TradeDirection.SELL:
            # Check for bearish Order Blocks
            ob_candles = recent[recent["ob_bearish"] == True]
            for idx, row in ob_candles.iterrows():
                if not np.isnan(row["ob_bear_low"]) and not np.isnan(row["ob_bear_high"]):
                    if row["ob_bear_low"] * 0.999 <= price <= row["ob_bear_high"]:
                        result["valid"] = True
                        result["reason"] = f"Bearish OB at {row['ob_bear_low']:.5f}-{row['ob_bear_high']:.5f}"
                        result["zone_type"] = "order_block"
                        return result

            # Check for bearish FVGs
            fvg_candles = recent[recent["fvg_bearish"] == True]
            for idx, row in fvg_candles.iterrows():
                if not np.isnan(row["fvg_bear_bottom"]) and not np.isnan(row["fvg_bear_top"]):
                    if row["fvg_bear_bottom"] <= price <= row["fvg_bear_top"]:
                        result["valid"] = True
                        result["reason"] = f"Bearish FVG at {row['fvg_bear_bottom']:.5f}-{row['fvg_bear_top']:.5f}"
                        result["zone_type"] = "fvg"
                        return result

        return result

    def _check_rsi_confirmation(
        self, etf_data: pd.DataFrame, bias: TradeDirection
    ) -> bool:
        """
        Check RSI for momentum confirmation.
        BUY: RSI recently crossed above oversold (30)
        SELL: RSI recently crossed below overbought (70)
        """
        rsi_col = f"rsi_{settings.rsi_period}"
        if rsi_col not in etf_data.columns:
            return False

        rsi_values = etf_data[rsi_col].tail(5).values

        # Remove NaN values
        rsi_values = rsi_values[~np.isnan(rsi_values)]
        if len(rsi_values) < 3:
            return False

        current_rsi = rsi_values[-1]

        if bias == TradeDirection.BUY:
            # RSI was recently oversold and is now bouncing
            was_oversold = any(r <= settings.rsi_oversold for r in rsi_values[:-1])
            now_above = current_rsi > settings.rsi_oversold
            # Also accept RSI in 30-50 range (still has room to run)
            in_buy_zone = settings.rsi_oversold <= current_rsi <= 55
            return (was_oversold and now_above) or in_buy_zone

        elif bias == TradeDirection.SELL:
            # RSI was recently overbought and is now dropping
            was_overbought = any(r >= settings.rsi_overbought for r in rsi_values[:-1])
            now_below = current_rsi < settings.rsi_overbought
            # Also accept RSI in 50-70 range (still has room to fall)
            in_sell_zone = 45 <= current_rsi <= settings.rsi_overbought
            return (was_overbought and now_below) or in_sell_zone

        return False

    def _calculate_confidence(
        self,
        htf_data: pd.DataFrame,
        etf_data: pd.DataFrame,
        smc_zone: dict,
    ) -> float:
        """
        Calculate signal confidence score (0.0 - 1.0).
        Higher = more confluence factors aligned.
        """
        score = 0.0

        latest_htf = htf_data.iloc[-1]
        latest_etf = etf_data.iloc[-1]

        # ADX strength (stronger trend = higher confidence)
        if not np.isnan(latest_htf["adx"]):
            if latest_htf["adx"] > 35:
                score += 0.25
            elif latest_htf["adx"] > 25:
                score += 0.15

        # EMA slope (steeper = more confidence) — proper linear-regression slope
        # per 20-bar window. Normalized by ATR so the score is regime-invariant:
        # a 0.001 slope means different things in low- vs high-vol pairs. The
        # 0.005 / 0.002 thresholds correspond to ~0.5% / 0.2% of an ATR per bar.
        if "ema_slope" in htf_data.columns and not np.isnan(latest_htf["ema_slope"]):
            atr_col_h = f"atr_{settings.atr_period}"
            atr_val = latest_htf.get(atr_col_h, np.nan) if atr_col_h in htf_data.columns else np.nan
            if not np.isnan(atr_val) and atr_val > 0:
                normalized_slope = abs(latest_htf["ema_slope"]) / atr_val
                if normalized_slope > 0.005:
                    score += 0.20
                elif normalized_slope > 0.002:
                    score += 0.10
            else:
                # ATR not available — fall back to a small magnitude check so
                # we still credit a non-trivial slope instead of returning zero.
                if abs(latest_htf["ema_slope"]) > 0.0001:
                    score += 0.05

        # OB vs FVG (Order Blocks generally more reliable)
        if smc_zone["zone_type"] == "order_block":
            score += 0.25
        elif smc_zone["zone_type"] == "fvg":
            score += 0.15

        # Volume confirmation
        if "volume" in etf_data.columns:
            vol_avg = etf_data["volume"].tail(20).mean()
            if latest_etf["volume"] > vol_avg * 1.2:
                score += 0.10

        # ATR not spiking (calmer = more predictable)
        if "atr_ratio" in etf_data.columns and not np.isnan(latest_etf["atr_ratio"]):
            if latest_etf["atr_ratio"] < settings.atr_volatility_spike:
                score += 0.10

        return min(score, 1.0)
