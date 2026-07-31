"""
Strategy Engine — Multi-Strategy Evaluator with Attribution.

Evaluates strategies as OR-conditions (first valid strategy triggers entry)
for the active profile and market regime.  Returns a dict with:
  - signal (SignalType): BUY or SELL
  - strategy (str): Unique attribution name (e.g. "DEFAULT_FVG_RETEST")
  - explanation (str): Human-readable reason for the dashboard / MT5 HUD

The engine is profile-aware and regime-separated — only strategies mapped
to the current (profile, regime) pair are evaluated.

Usage:
    from bot.strategies.strategy_engine import StrategyEngine

    result = StrategyEngine.evaluate(
        profile=Profile.DEFAULT,
        regime=MarketRegime.TRENDING,
        df_h4=h4_df,
        df_m15=m15_df,
        gatekeeper_bias="BULLISH",
        current_price=price_dict,
    )
    # → {"signal": "buy", "strategy": "DEFAULT_FVG_RETEST", "explanation": "..."}
"""

from __future__ import annotations

from typing import Any, Optional

import numpy as np
import pandas as pd

from bot.config.enums import MarketRegime, Profile, SignalType


class StrategyEngine:
    """Evaluates entry strategies for the active profile and market regime.

    Each profile defines which regimes it can trade and which strategies
    to evaluate within each regime.  Strategies are evaluated in priority
    order; the first one that fires wins (OR-condition).
    """

    # ── Public API ─────────────────────────────────────────────────

    @staticmethod
    def evaluate(
        profile: Profile,
        regime: MarketRegime,
        df_h4: pd.DataFrame,
        df_m15: pd.DataFrame,
        gatekeeper_bias: str | None = None,
        current_price: dict | None = None,
    ) -> dict | None:
        """Evaluate strategies for the active profile & regime.

        Args:
            profile: Active trading profile.
            regime: Current market regime (from ``detect_regime()``).
            df_h4: H4 OHLCV + indicator DataFrame.
            df_m15: M15 OHLCV + indicator DataFrame.
            gatekeeper_bias: ``"BULLISH"``, ``"BEARISH"``, or None.
            current_price: Dict with ``bid`` and ``ask`` keys.

        Returns:
            Dict with ``signal``, ``strategy``, ``explanation`` keys,
            or None if no strategy fires.
        """
        # ── Hard lockouts ──────────────────────────────────────────
        if regime in (MarketRegime.DEAD, MarketRegime.NEWS_EVENT):
            return None

        # ── Profile Routing ────────────────────────────────────────
        if profile == Profile.DEFAULT:
            return StrategyEngine._evaluate_default(regime, df_h4, df_m15, gatekeeper_bias, current_price)

        if profile == Profile.SCALPER:
            return StrategyEngine._evaluate_scalper(regime, df_m15, current_price)

        if profile == Profile.BREAKOUT:
            return StrategyEngine._evaluate_breakout(regime, df_m15, current_price)

        if profile == Profile.DAYTRADER:
            return StrategyEngine._evaluate_daytrader(regime, df_h4, df_m15, gatekeeper_bias, current_price)

        return None

    # ── Profile: Default ──────────────────────────────────────────

    @staticmethod
    def _evaluate_default(
        regime: MarketRegime,
        df_h4: pd.DataFrame,
        df_m15: pd.DataFrame,
        gatekeeper_bias: str | None,
        current_price: dict | None,
    ) -> dict | None:
        """Default profile: trend-following via FVG retests + EMA pullbacks."""

        if regime == MarketRegime.TRENDING and gatekeeper_bias:
            # Strategy 1: FVG Retest — price returns to a fair value gap
            # with momentum confirmation.
            result = StrategyEngine._check_fvg_retest(df_m15, gatekeeper_bias, current_price)
            if result:
                return result

            # Strategy 2: 21-EMA Pullback — price pulls back to the
            # M15 21 EMA in the direction of the H4 bias.
            result = StrategyEngine._check_ema_pullback(df_m15, gatekeeper_bias, current_price)
            if result:
                return result

        if regime == MarketRegime.RANGING:
            # Strategy 3: Bollinger Band Fade — price touches outer
            # band with RSI divergence.
            result = StrategyEngine._check_bollinger_fade(df_m15, current_price)
            if result:
                return result

        if regime == MarketRegime.VOLATILE:
            # Volatility: pass through to scalper logic (small, quick entries)
            return StrategyEngine._evaluate_scalper(regime, df_m15, current_price)

        return None

    # ── Profile: Scalper ──────────────────────────────────────────

    @staticmethod
    def _evaluate_scalper(
        regime: MarketRegime,
        df_m15: pd.DataFrame,
        current_price: dict | None,
    ) -> dict | None:
        """Scalper profile: volume-surge + RSI hooks. Runs in TRENDING,
        RANGING, and VOLATILE regimes."""
        if df_m15 is None or df_m15.empty or len(df_m15) < 20:
            return None

        # Volume surge gate — requires tick volume > 1.5x the 20-bar mean
        volume = df_m15["volume"].iloc[-1] if "volume" in df_m15.columns else None
        vol_avg = df_m15["volume_avg_20"].iloc[-1] if "volume_avg_20" in df_m15.columns else None
        rsi_col = "rsi_14" if "rsi_14" in df_m15.columns else None

        if volume is None or vol_avg is None or vol_avg <= 0 or rsi_col is None:
            return None

        vol_ratio = volume / vol_avg
        if vol_ratio < 1.5:
            return None

        rsi = df_m15[rsi_col].iloc[-1]
        prev_rsi = df_m15[rsi_col].iloc[-2]

        # BUY: RSI oversold recovery with volume surge
        if prev_rsi <= 35 and rsi > 35:
            return {
                "signal": SignalType.BUY,
                "strategy": "SCALPER_VOL_SURGE",
                "explanation": (
                    f"Volume surge {vol_ratio:.1f}×avg with RSI oversold "
                    f"recovery ({prev_rsi:.0f}→{rsi:.0f})"
                ),
            }

        # SELL: RSI overbought rejection with volume surge
        if prev_rsi >= 65 and rsi < 65:
            return {
                "signal": SignalType.SELL,
                "strategy": "SCALPER_VOL_SURGE",
                "explanation": (
                    f"Volume surge {vol_ratio:.1f}×avg with RSI overbought "
                    f"rejection ({prev_rsi:.0f}→{rsi:.0f})"
                ),
            }

        return None

    # ── Profile: Breakout ─────────────────────────────────────────

    @staticmethod
    def _evaluate_breakout(
        regime: MarketRegime,
        df_m15: pd.DataFrame,
        current_price: dict | None,
    ) -> dict | None:
        """Breakout profile: session open range breakouts."""
        if df_m15 is None or df_m15.empty or len(df_m15) < 20:
            return None

        # Check if we're in a session open window (London 07:00-07:30 or NY 12:00-12:30 UTC)
        now = pd.Timestamp.utcnow()
        hour, minute = now.hour, now.minute
        in_session_window = (hour == 7 and minute < 30) or (hour == 12 and minute < 30)
        if not in_session_window:
            return None

        # 20-bar consolidation range
        recent_high = df_m15["high"].iloc[-20:-1].max()
        recent_low = df_m15["low"].iloc[-20:-1].min()
        bid = current_price.get("bid", 0) if current_price else df_m15["close"].iloc[-1]
        ask = current_price.get("ask", 0) if current_price else df_m15["close"].iloc[-1]

        # Upside breakout
        if ask > recent_high:
            return {
                "signal": SignalType.BUY,
                "strategy": "BREAKOUT_SESSION_OPEN",
                "explanation": f"Price broke above 20-bar range ({recent_high:.5f}) during session open",
            }

        # Downside breakout
        if bid < recent_low:
            return {
                "signal": SignalType.SELL,
                "strategy": "BREAKOUT_SESSION_OPEN",
                "explanation": f"Price broke below 20-bar range ({recent_low:.5f}) during session open",
            }

        return None

    # ── Profile: Day Trader ───────────────────────────────────────

    @staticmethod
    def _evaluate_daytrader(
        regime: MarketRegime,
        df_h4: pd.DataFrame,
        df_m15: pd.DataFrame,
        gatekeeper_bias: str | None,
        current_price: dict | None,
    ) -> dict | None:
        """Day trader profile: session-routed strategy matrix."""
        now = pd.Timestamp.utcnow()
        hour = now.hour

        # Asian session (00:00-07:00 UTC) → mean reversion
        if 0 <= hour < 7:
            if regime in (MarketRegime.RANGING, MarketRegime.TRENDING):
                return StrategyEngine._check_bollinger_fade(df_m15, current_price)

        # London/NY main (07:00-20:00 UTC) → trend following
        if 7 <= hour < 20:
            if regime == MarketRegime.TRENDING and gatekeeper_bias:
                result = StrategyEngine._check_fvg_retest(df_m15, gatekeeper_bias, current_price)
                if result:
                    return result
                result = StrategyEngine._check_ema_pullback(df_m15, gatekeeper_bias, current_price)
                if result:
                    return result

        # Session opens → breakout
        if (hour == 7 and now.minute < 30) or (hour == 12 and now.minute < 30):
            return StrategyEngine._evaluate_breakout(regime, df_m15, current_price)

        return None

    # ── Condition Helpers ─────────────────────────────────────────

    @staticmethod
    def _check_fvg_retest(
        df_m15: pd.DataFrame,
        bias: str,
        current_price: dict | None,
    ) -> dict | None:
        """Check if price returned to a Fair Value Gap (FVG) zone.

        A bullish FVG exists when ``high[i-2] < low[i]`` — the middle
        candle's range is gapped over.  Returns a signal when the latest
        M15 close is inside an active FVG aligned with the gatekeeper bias.
        """
        if df_m15 is None or len(df_m15) < 3:
            return None

        # Look for FVG in the last 10 candles
        for i in range(-1, -11, -1):
            idx = df_m15.index[i] if i >= -len(df_m15) else None
            if idx is None:
                continue

            # Detect FVG where two candles don't overlap
            high_2 = df_m15["high"].iloc[i - 2] if abs(i) + 2 <= len(df_m15) else None
            low = df_m15["low"].iloc[i] if abs(i) <= len(df_m15) else None
            if high_2 is None or low is None:
                continue

            # Bullish FVG gap-up: high[i-2] < low[i]
            if bias == "BULLISH" and high_2 < low:
                fvg_top = low
                fvg_bottom = high_2

                # Check if latest close is inside the FVG zone
                latest_close = df_m15["close"].iloc[-1]
                if fvg_bottom <= latest_close <= fvg_top:
                    return {
                        "signal": SignalType.BUY,
                        "strategy": "DEFAULT_FVG_RETEST",
                        "explanation": (
                            f"Price retested bullish FVG ({fvg_bottom:.5f}-{fvg_top:.5f}) "
                            f"with {bias} H4 bias"
                        ),
                    }

            # Bearish FVG gap-down: low[i-2] > high[i]
            if bias == "BEARISH":
                low_2 = df_m15["low"].iloc[i - 2] if abs(i) + 2 <= len(df_m15) else None
                high = df_m15["high"].iloc[i] if abs(i) <= len(df_m15) else None
                if low_2 is not None and high is not None and low_2 > high:
                    fvg_top = low_2
                    fvg_bottom = high
                    latest_close = df_m15["close"].iloc[-1]
                    if fvg_bottom <= latest_close <= fvg_top:
                        return {
                            "signal": SignalType.SELL,
                            "strategy": "DEFAULT_FVG_RETEST",
                            "explanation": (
                                f"Price retested bearish FVG ({fvg_bottom:.5f}-{fvg_top:.5f}) "
                                f"with {bias} H4 bias"
                            ),
                        }

        return None

    @staticmethod
    def _check_ema_pullback(
        df_m15: pd.DataFrame,
        bias: str,
        current_price: dict | None,
    ) -> dict | None:
        """Check if price pulled back to the M15 21 EMA in the direction
        of the H4 bias and is showing reversal signs.
        """
        # Compute M15 21 EMA
        if df_m15 is None or len(df_m15) < 22:
            return None

        closes = df_m15["close"].values
        ema21 = _ema(closes, 21)[-1]
        if np.isnan(ema21):
            return None

        latest_close = closes[-1]
        prev_close = closes[-2]

        # BULLISH: price above 21 EMA after pullback touch
        if bias == "BULLISH" and latest_close > ema21 and prev_close < ema21 * 1.001:
            return {
                "signal": SignalType.BUY,
                "strategy": "DEFAULT_EMA_PULLBACK",
                "explanation": f"Price bounced off M15 21 EMA ({ema21:.5f}) with {bias} H4 bias",
            }

        # BEARISH: price below 21 EMA after pullback touch
        if bias == "BEARISH" and latest_close < ema21 and prev_close > ema21 * 0.999:
            return {
                "signal": SignalType.SELL,
                "strategy": "DEFAULT_EMA_PULLBACK",
                "explanation": f"Price rejected at M15 21 EMA ({ema21:.5f}) with {bias} H4 bias",
            }

        return None

    @staticmethod
    def _check_bollinger_fade(
        df_m15: pd.DataFrame,
        current_price: dict | None,
    ) -> dict | None:
        """Fade extreme Bollinger Band touches.

        When price closes outside a Bollinger Band with RSI divergence,
        fade the move (sell at upper band, buy at lower band).
        """
        if df_m15 is None or len(df_m15) < 20:
            return None

        closes = df_m15["close"].values[-20:].astype(float)
        mean = np.nanmean(closes)
        std = np.nanstd(closes)
        upper = mean + 2 * std
        lower = mean - 2 * std

        rsi_col = "rsi_14" if "rsi_14" in df_m15.columns else None
        rsi = df_m15[rsi_col].iloc[-1] if rsi_col else 50

        latest_close = float(closes[-1])

        # Oversold bounce: close below lower band + RSI < 30
        if latest_close < lower and rsi < 30:
            return {
                "signal": SignalType.BUY,
                "strategy": "DEFAULT_BOLLINGER_FADE",
                "explanation": (
                    f"Price closed below Bollinger lower band ({lower:.5f}) "
                    f"with RSI {rsi:.0f} — oversold bounce setup"
                ),
            }

        # Overbought rejection: close above upper band + RSI > 70
        if latest_close > upper and rsi > 70:
            return {
                "signal": SignalType.SELL,
                "strategy": "DEFAULT_BOLLINGER_FADE",
                "explanation": (
                    f"Price closed above Bollinger upper band ({upper:.5f}) "
                    f"with RSI {rsi:.0f} — overbought rejection setup"
                ),
            }

        return None


# ── Module-level utilities ─────────────────────────────────────────

def _ema(values: np.ndarray, period: int) -> np.ndarray:
    """Simple EMA computation — avoids importing pandas_ta for one calc."""
    out = np.full_like(values, np.nan, dtype=float)
    if len(values) < period:
        return out
    alpha = 2.0 / (period + 1)
    out[period - 1] = np.nanmean(values[:period])
    for i in range(period, len(values)):
        out[i] = alpha * values[i] + (1 - alpha) * out[i - 1]
    return out
