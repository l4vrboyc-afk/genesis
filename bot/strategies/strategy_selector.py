"""
Strategy Selector — Detects market regime and selects the right strategy.
This is the brain's brain — it decides WHICH strategy to use based on conditions.

For Day Trader profile, implements a 3×14 matrix approach:
- 3 strategies evaluated per symbol
- 14 currency pairs across multiple sessions
- Session-aware routing prevents signal collisions
"""

import asyncio
import numpy as np
import pandas as pd
from datetime import datetime, time
from typing import Optional
from loguru import logger
from enum import Enum

# Late import to avoid circular dependency; wrapped in try/except for safety.
try:
    from bot.notifications.notification_manager import notification_manager
    _NOTIFICATION_AVAILABLE = True
except ImportError:
    _NOTIFICATION_AVAILABLE = False

# Lazy import of the WebSocket broadcasting engine (dashboard may not be
# initialised in CLI-only mode). The ws_manager is a no-op when no browser
# clients are connected, so these calls are safe everywhere.
try:
    from dashboard.backend.ws_manager import ws_manager
    from dashboard.backend.schemas import WSEventPayload
    _WS_AVAILABLE = True
except ImportError:
    _WS_AVAILABLE = False

from bot.config.settings import settings, MarketRegime, TradeDirection
from bot.strategies.base_strategy import BaseStrategy, TradeSignal
from bot.strategies.smart_trend import SmartTrendStrategy
from bot.strategies.mean_reversion import MeanReversionStrategy
from bot.strategies.scalper_momentum import ScalperMomentumStrategy
from bot.strategies.session_breakout import SessionBreakoutStrategy
from bot.strategies.trend_engine import TrendEngineStrategy


class SessionType(Enum):
    """Market session classification for daytrader profile."""
    ASIAN = "asian"
    LONDON = "london"
    NEW_YORK = "new_york"
    OVERLAP = "overlap"
    OUTSIDE = "outside"


class StrategySelector:
    """
    Detects market regime and selects the appropriate strategy.

    Regime Detection:
    - TRENDING: ADX > 25, clear EMA direction
    - RANGING: ADX < 20, price between S/R
    - VOLATILE: ATR spike > 2x average
    - DEAD: Very low ATR, no movement
    - NEWS_EVENT: Paused by news filter

    Hysteresis (Track d):
        The current regime is "sticky" — once classified, the selector
        refuses to flip out of it for at least ``hysteresis_window_seconds``.
        This kills the 22–23 ADX jitter that otherwise flicks the engine
        between TRENDING and RANGING every cycle and produces a handful of
        unwanted re-entries on each crossover.

    Profile gating (Track d):
        Scalper / breakout strategies only import if the active profile is
        ``scalper`` / ``breakout``. A WARNING is logged on first regime
        evaluation if profile is ``default`` but the selector ends up
        wired to a profile-gated strategy — a soft reminder rather than a
        blocker because ``__init__.py`` guards the imports anyway.
    """

    def __init__(self):
        self.profile = settings.active_profile

        if self.profile == "scalper":
            self.strategies = {
                MarketRegime.TRENDING: ScalperMomentumStrategy(),
                MarketRegime.RANGING: ScalperMomentumStrategy(),
                MarketRegime.VOLATILE: ScalperMomentumStrategy(), # Scalper handles volatility
            }
        elif self.profile == "breakout":
            self.strategies = {
                MarketRegime.TRENDING: SessionBreakoutStrategy(),
                MarketRegime.RANGING: SessionBreakoutStrategy(),
            }
        elif self.profile == "daytrader":
            # Day Trader Profile - uses Trend Engine for trend following
            # Note: Full day trader uses session-aware logic in TrendEngineStrategy
            # Mean Reversion and Breakout strategies will be integrated in Phase 2
            self.strategies = {
                MarketRegime.TRENDING: TrendEngineStrategy(),
                MarketRegime.RANGING: MeanReversionStrategy(),  # Placeholder for mean reversion
            }
        else:
            # Default Profile
            self.strategies = {
                MarketRegime.TRENDING: SmartTrendStrategy(),
                MarketRegime.RANGING: MeanReversionStrategy(),
            }

        self._current_regime = None
        self._regime_history = []

        # Hysteresis state — Track d. Set to (None, None) so the very first
        # classification is admitted unconditionally; subsequent flips have
        # to survive ``hysteresis_window_seconds`` of consistent readings.
        self._candidate_regime = None
        self._candidate_since = None
        self._hysteresis_profile_warned = False

    @property
    def current_regime(self) -> Optional[MarketRegime]:
        return self._current_regime

    @property
    def active_strategy(self) -> Optional[BaseStrategy]:
        if self._current_regime in self.strategies:
            return self.strategies[self._current_regime]
        return None

    @staticmethod
    def _classify_regime(adx: float, atr_ratio: float) -> MarketRegime:
        """Stateless ADX / ATR-ratio regime classification.

        Shared by ``detect_regime`` (stateful wrapper with hysteresis) and
        ``evaluate_symbol_gates`` (read-only dashboard evaluator) so the
        classification thresholds can never drift apart.
        """
        # High volatility spike — reduce exposure
        if atr_ratio > settings.atr_volatility_spike:
            return MarketRegime.VOLATILE
        # Strong trend
        if adx > settings.adx_trend_threshold:
            return MarketRegime.TRENDING
        # Ranging / sideways
        if adx < settings.adx_range_threshold:
            return MarketRegime.RANGING
        # Low volatility / dead market
        if atr_ratio < 0.5:
            return MarketRegime.DEAD
        # In-between — default to ranging (safer)
        return MarketRegime.RANGING

    def detect_regime(self, htf_data: pd.DataFrame) -> MarketRegime:
        """
        Detect current market regime from higher timeframe data.

        Args:
            htf_data: Analyzed H4 DataFrame with indicators

        Returns:
            MarketRegime classification
        """
        if htf_data is None or htf_data.empty:
            return MarketRegime.DEAD

        latest = htf_data.iloc[-1]

        # Get indicator values
        adx = latest.get("adx", 0)
        atr_ratio = latest.get("atr_ratio", 1.0)

        if np.isnan(adx):
            adx = 0
        if np.isnan(atr_ratio):
            atr_ratio = 1.0

        # ── Classification Logic ───────────────────────────────────
        # Stateless shared classifier — the dashboard evaluator uses the
        # exact same thresholds (see ``_classify_regime``).
        regime = self._classify_regime(adx, atr_ratio)

        # ── Hysteresis (Track d) ───────────────────────────────────
        # The current regime is sticky inside ``hysteresis_window_seconds``.
        # A different reading resets the candidate timer; the flip only
        # lands once the new classification has held for the full window.
        now = datetime.now()
        first_evaluation = self._current_regime is None

        if first_evaluation:
            self._current_regime = regime
            self._candidate_regime = None
            self._candidate_since = None
        elif regime != self._current_regime:
            # Track how long the candidate has been observed
            if regime != self._candidate_regime:
                self._candidate_regime = regime
                self._candidate_since = now
            else:
                elapsed = (now - self._candidate_since).total_seconds()
                if elapsed >= settings.hysteresis_window_seconds:
                    old_regime = self._current_regime
                    logger.info(
                        f"🔄 Hysteresis cleared: {old_regime} → {regime} "
                        f"(held candidate for {elapsed:.0f}s, "
                        f"window {settings.hysteresis_window_seconds}s; "
                        f"ADX={adx:.1f}, ATR ratio={atr_ratio:.2f})"
                    )
                    self._current_regime = regime
                    self._candidate_regime = None
                    self._candidate_since = None
                    self._regime_history.append({
                        "from": "_hysteresis",
                        "to": regime,
                        "adx": adx,
                        "atr_ratio": atr_ratio,
                        "elapsed_s": elapsed,
                    })
                    # Fire Discord regime change alert (fire-and-forget task).
                    # Keep a strong reference on the instance so the task
                    # isn't garbage-collected before the alert is sent.
                    if _NOTIFICATION_AVAILABLE:
                        try:
                            _tasks = getattr(self, "_bg_tasks", None)
                            if _tasks is None:
                                _tasks = self._bg_tasks = set()
                            _task = asyncio.create_task(notification_manager.notify_regime_change(
                                old_regime=str(old_regime.value) if old_regime else "unknown",
                                new_regime=str(regime.value),
                                adx=adx,
                                atr_ratio=atr_ratio,
                            ))
                            _tasks.add(_task)
                            _task.add_done_callback(_tasks.discard)
                        except Exception:
                            pass

                    # Broadcast regime change to WebSocket dashboard clients
                    if _WS_AVAILABLE:
                        try:
                            _ws_tasks = getattr(self, "_ws_tasks", None)
                            if _ws_tasks is None:
                                _ws_tasks = self._ws_tasks = set()
                            _ws_task = asyncio.create_task(ws_manager.broadcast(
                                WSEventPayload(
                                    event_type="REGIME_CHANGE",
                                    timestamp=datetime.now().isoformat(),
                                    data={
                                        "old_regime": str(old_regime.value) if old_regime else "unknown",
                                        "new_regime": str(regime.value),
                                        "adx": float(adx) if not isinstance(adx, float) else adx,
                                        "atr_ratio": float(atr_ratio) if not isinstance(atr_ratio, float) else atr_ratio,
                                    },
                                )
                            ))
                            _ws_tasks.add(_ws_task)
                            _ws_task.add_done_callback(_ws_tasks.discard)
                        except Exception:
                            pass
                else:
                    logger.debug(
                        f"⏳ Regime candidate {regime} pending "
                        f"({elapsed:.0f}s / {settings.hysteresis_window_seconds}s)"
                    )
        else:
            # Reading matches the live regime — clear any pending candidate.
            if self._candidate_regime is not None:
                logger.debug(
                    f"✅ Regime candidate {self._candidate_regime} cleared "
                    f"(reading reverted to {self._current_regime})"
                )
            self._candidate_regime = None
            self._candidate_since = None

        # ── Profile gate warning (Track d) ─────────────────────────
        if not self._hysteresis_profile_warned and self._current_regime in self.strategies:
            chosen = self.strategies[self._current_regime]
            profile_gated = (
                chosen.__class__.__name__ == "ScalperMomentumStrategy"
                or chosen.__class__.__name__ == "SessionBreakoutStrategy"
            )
            default_profile_with_gated = (
                settings.active_profile == "default" and profile_gated
            )
            if default_profile_with_gated:
                logger.warning(
                    f"⚠️ active_profile is 'default' but selector picked "
                    f"{chosen.__class__.__name__}; track (d) note: "
                    f"scalper_momentum / session_breakout are profile-gated, "
                    f"see strategies/__init__.py"
                )
            self._hysteresis_profile_warned = True

        return self._current_regime

    def get_signal(
        self,
        symbol: str,
        higher_tf_data: pd.DataFrame,
        entry_tf_data: pd.DataFrame,
        current_price: dict,
    ) -> Optional[TradeSignal]:
        """
        Detect regime → select strategy → generate signal.

        This is the main entry point for the strategy engine.

        Args:
            symbol: Trading pair
            higher_tf_data: Analyzed H4 data
            entry_tf_data: Analyzed M15 data
            current_price: Current bid/ask

        Returns:
            TradeSignal if a valid setup is found
        """
        # Detect current regime
        regime = self.detect_regime(higher_tf_data)

        # Skip non-tradeable regimes — profile-aware
        # Scalper profile trades VOLATILE (mapped to ScalperMomentumStrategy)
        # Breakout profile doesn't have VOLATILE mapped
        # Default profile doesn't have VOLATILE mapped
        skip_regimes = {MarketRegime.DEAD, MarketRegime.NEWS_EVENT}
        # VOLATILE is NOT skipped for any profile: scalper has it mapped
        # to ScalperMomentumStrategy (it's the best regime for momentum
        # scalping). Default / breakout profiles don't have VOLATILE in
        # their strategies dict, so it falls through to "no strategy"
        # naturally — no explicit skip needed.

        if regime in skip_regimes:
            logger.info(
                f"⏸️ {symbol} — Regime {regime.value} skipped (profile={self.profile})"
            )
            return None

        # Get the strategy for this regime
        strategy = self.strategies.get(regime)
        if strategy is None:
            logger.warning(f"⚠️ No strategy configured for regime: {regime}")
            return None

        # Generate signal
        signal = strategy.generate_signal(
            symbol, higher_tf_data, entry_tf_data, current_price
        )

        # Strategy helpers now thread `symbol` through end-to-end, so there's
        # nothing to overwrite here. `validate_signal()` does not gate on
        # `symbol`, so this remains safe for downstream consumers that key
        # off symbol.

        return signal

    # ── Rule 1: Gatekeeper Trend Guard ────────────────────────────────────
    # Blocks SELL signals when the H1 timeframe shows a strong uptrend:
    #   ADX > 25  AND  Price > 50 EMA

    def check_gatekeeper(
        self,
        symbol: str,
        gatekeeper_data: dict,
        signal_direction: TradeDirection,
    ) -> bool:
        """Rule 1: Gatekeeper Trend Guard 🛑

        Blocks SELL trades during strong H1 uptrends.
        Returns True if the signal is allowed through, False if blocked.

        The Gatekeeper only applies to SELL signals — BUY signals always pass.
        If gatekeeper data is unavailable (None / empty dict / NaN values),
        the signal is allowed through (fail-open).

        Args:
            symbol: Trading pair symbol (for logging).
            gatekeeper_data: Dict with keys ``adx``, ``ema_50``, ``close``
                (from ``DataFetcher.get_gatekeeper_indicators()``).
            signal_direction: The proposed trade direction.

        Returns:
            True if the trade is allowed, False if gatekeeper blocks it.
        """
        # Only gatekeep SELL signals
        if signal_direction != TradeDirection.SELL:
            return True

        # Can't determine trend — allow through (fail-open)
        if gatekeeper_data is None:
            return True

        adx = gatekeeper_data.get("adx")
        ema_50 = gatekeeper_data.get("ema_50")
        close = gatekeeper_data.get("close")

        if adx is None or ema_50 is None or close is None:
            return True

        # The core gatekeeper logic:
        # If ADX > 25 AND price is above the 50 EMA, sellers must wait.
        if adx > settings.gatekeeper_adx_threshold and close > ema_50:
            logger.info(
                f"🚫 GATEKEEPER blocked SELL on {symbol}: "
                f"ADX={adx:.1f} (>{settings.gatekeeper_adx_threshold}) "
                f"AND Price={close:.5f} > EMA50={ema_50:.5f} — strong uptrend in play"
            )
            return False

        return True

    # ── Rule 3: M15 Sniper Confirmation ────────────────────────────────
    # Requires the latest M15 candle to confirm the intended direction:
    #   SELL: lower high + bearish close
    #   BUY:  higher low + bullish close

    def check_sniper_confirmation(
        self,
        entry_tf_data: pd.DataFrame,
        direction: TradeDirection,
    ) -> bool:
        """Rule 3: M15 Sniper Confirmation 🎯

        Waits for the M15 chart to physically prove momentum has turned
        before allowing entry.  For SELLs: latest high < previous high
        (lower high) AND latest close < latest open (bearish close).
        For BUYs: latest low > previous low (higher low) AND latest
        close > latest open (bullish close).

        Args:
            entry_tf_data: M15 (or entry timeframe) DataFrame with OHLC.
            direction: The proposed trade direction.

        Returns:
            True if the pattern is confirmed (or sniper is disabled), False
            if the pattern does not support entry.
        """
        if not settings.sniper_confirmation_enabled:
            return True

        if entry_tf_data is None or len(entry_tf_data) < 3:
            logger.debug("⏳ Sniper — insufficient candle data (< 3 bars)")
            return False

        candle_prev = entry_tf_data.iloc[-2]  # One bar back
        candle_latest = entry_tf_data.iloc[-1]  # Current / latest bar

        if direction == TradeDirection.SELL:
            # Lower high: latest high < previous high
            lower_high = candle_latest["high"] < candle_prev["high"]
            # Bearish close: close price < open price
            bearish_close = candle_latest["close"] < candle_latest["open"]

            if lower_high and bearish_close:
                logger.debug(
                    f"🎯 Sniper SELL confirmed: lower high "
                    f"({candle_latest['high']:.5f} < {candle_prev['high']:.5f}) "
                    f"+ bearish close ({candle_latest['close']:.5f} < {candle_latest['open']:.5f})"
                )
                return True

            logger.debug(
                f"⏳ Sniper SELL waiting — lower_high={lower_high}, "
                f"bearish_close={bearish_close}"
            )
            return False

        elif direction == TradeDirection.BUY:
            # Higher low: latest low > previous low
            higher_low = candle_latest["low"] > candle_prev["low"]
            # Bullish close: close price > open price
            bullish_close = candle_latest["close"] > candle_latest["open"]

            if higher_low and bullish_close:
                logger.debug(
                    f"🎯 Sniper BUY confirmed: higher low "
                    f"({candle_latest['low']:.5f} > {candle_prev['low']:.5f}) "
                    f"+ bullish close ({candle_latest['close']:.5f} > {candle_latest['open']:.5f})"
                )
                return True

            logger.debug(
                f"⏳ Sniper BUY waiting — higher_low={higher_low}, "
                f"bullish_close={bullish_close}"
            )
            return False

        return True

# ── 5-Gate Evaluator ──────────────────────────────────────────────────
# All 5 gates are evaluated in a single structured call. Minimum 4/5 must pass.
# Returns dict with gate names + pass/fail status for logging and WS broadcast.
#
# Gates implemented:
#   1. Gatekeeper   — ADX/EMA trend guard (SELL-only; fail-open)
#   2. Sniper       — M15 candle pattern confirmation
#   3. Volume       — latest bar volume >= threshold x 20-bar avg
#   4. EMA Stack    — price stacked with EMA50 + EMA200 (HTF)
#   5. ADX Strength — ADX >= 20 (trend has conviction; fail-open on NaN)
#
# NOTE: Gate 5 (ADX Strength) is fully implemented. An earlier RSI
# Momentum gate was replaced by ADX Strength after entry-TF column
# naming alignment issues with 'rsi_14' vs 'RSI_14'.

    def evaluate_gates(
        self,
        signal: TradeSignal,
        gatekeeper_data: Optional[dict],
        entry_tf_data: Optional[pd.DataFrame],
        htf_data: Optional[pd.DataFrame],
    ) -> dict:
        """Evaluate all 5 gates, return pass/fail summary.
        Args:
            signal: Proposed trade signal
            gatekeeper_data: Dict from DataFetcher.get_gatekeeper_indicators()
                with keys: adx, ema_50, atr, close
            entry_tf_data: Entry TF DataFrame (M15/M1) from get_analyzed_data()
            htf_data: Higher TF DataFrame (H4/H1) from get_analyzed_data()
        Returns:
            Dict with 'passed', 'total', 'gates' list, and 'allowed' bool
        """
        gates = []
        direction = signal.direction.value  # 'buy' or 'sell'

        # ── Gate 1: Gatekeeper (ADX + EMA50 trend guard) ───────────────
        gk_pass = self.check_gatekeeper(signal.symbol, gatekeeper_data, signal.direction)
        gates.append({"name": "gatekeeper", "passed": gk_pass})

        # ── Gate 2: Sniper (M15 candle pattern) ────────────────────────
        sniper_pass = self.check_sniper_confirmation(entry_tf_data, signal.direction)
        gates.append({"name": "sniper", "passed": sniper_pass})

        # ── Gate 3: Volume (latest bar >= threshold x 20-bar avg) ──────
        vol_pass = True  # fail-open
        if entry_tf_data is not None and not entry_tf_data.empty:
            vol_col = 'tick_volume' if 'tick_volume' in entry_tf_data.columns else 'volume'
            if vol_col in entry_tf_data.columns:
                latest_vol = entry_tf_data[vol_col].iloc[-1]
                # Compute 20-bar average inline
                vol_avg = entry_tf_data[vol_col].tail(20).mean()
                if vol_avg > 0 and not pd.isna(latest_vol):
                    threshold = getattr(settings, 'volume_surge_ratio', 1.5)
                    vol_pass = latest_vol >= (threshold * vol_avg)
        gates.append({"name": "volume", "passed": vol_pass})

        # ── Gate 4: EMA Alignment (price stacked with EMA50 + EMA200) ──
        ema_pass = True  # fail-open
        if htf_data is not None and not htf_data.empty:
            close = htf_data['close'].iloc[-1]
            # Find EMA columns (data_fetcher names them ema_{period})
            ema_cols = [c for c in htf_data.columns if c.startswith('ema_')]
            if len(ema_cols) >= 2:
                ema50_col = next((c for c in ema_cols if '50' in c), ema_cols[0])
                ema200_col = next((c for c in ema_cols if '200' in c), ema_cols[-1])
                ema50 = htf_data[ema50_col].iloc[-1]
                ema200 = htf_data[ema200_col].iloc[-1]
                if not any(pd.isna(v) for v in [close, ema50, ema200]):
                    stacked_bull = close > ema50 > ema200
                    stacked_bear = close < ema50 < ema200
                    ema_pass = stacked_bull or stacked_bear
        gates.append({"name": "ema_alignment", "passed": ema_pass})

        # ── Gate 5: ADX Strength (ADX >= 20 = trend has conviction) ────
        adx_pass = True  # fail-open on NaN/missing
        adx_val = None
        if gatekeeper_data is not None:
            adx_val = gatekeeper_data.get('adx')
        if adx_val is None and htf_data is not None and not htf_data.empty and 'adx' in htf_data.columns:
            adx_val = htf_data['adx'].iloc[-1]
        if adx_val is not None and not pd.isna(adx_val):
            adx_pass = adx_val >= 20.0
        gates.append({"name": "adx_strength", "passed": adx_pass})

        # ── Gate Result ─────────────────────────────────────────────────
        passed = sum(1 for g in gates if g["passed"])
        allowed = passed >= 4
        return {"passed": passed, "total": 5, "gates": gates, "allowed": allowed}

    def evaluate_symbol_gates(
        self,
        symbol: str,
        gatekeeper_data: Optional[dict],
        entry_tf_data: Optional[pd.DataFrame],
        htf_data: Optional[pd.DataFrame],
    ) -> dict:
        """Evaluate the five dashboard gateway pills for a *symbol* from
        live MT5 data — no trade signal / direction required.

        This is the backend behind the dashboard's header gateway matrix
        (EMA / ADX / RSI / VOL / REG).  It is deliberately **stateless**:
        unlike ``detect_regime`` it never mutates hysteresis state, fires
        notifications, or broadcasts WebSocket events, so it is safe to
        call from a read-only HTTP route on every dropdown symbol change.

        Pill order matches the dashboard header IDs:
            [gate-ema, gate-adx, gate-rsi, gate-vol, gate-reg]

        Gates (each fail-open → ``True`` when its data is unavailable,
        matching the conventions of ``evaluate_gates``):
            1. EMA  — price stacked with EMA50 + EMA200 (bull or bear stack)
            2. ADX  — ADX >= 20 (trend has conviction)
            3. RSI  — entry-TF RSI inside [rsi_oversold, rsi_overbought]
            4. VOL  — latest bar volume >= volume_surge_ratio × 20-bar avg
            5. REG  — stateless regime classification != DEAD

        Args:
            symbol: Trading pair.
            gatekeeper_data: Dict from ``DataFetcher.get_gatekeeper_indicators``
                with keys ``adx``, ``ema_50``, ``atr``, ``close``.
            entry_tf_data: Analyzed entry-timeframe DataFrame (RSI, volume).
            htf_data: Analyzed higher-timeframe DataFrame (ema_200, adx, atr_ratio).

        Returns:
            Dict with ``gates`` (list of 5 booleans in pill order),
            ``passed``, ``total``, ``overall`` and ``details``.
        """
        gates = []

        # ── Gate 1: EMA Trend Alignment (stacked with EMA50 + EMA200) ──
        ema_pass = True  # fail-open
        close = gatekeeper_data.get("close") if gatekeeper_data else None
        ema_50 = gatekeeper_data.get("ema_50") if gatekeeper_data else None
        ema_200 = None
        if htf_data is not None and not htf_data.empty and "ema_200" in htf_data.columns:
            ema_200 = htf_data["ema_200"].iloc[-1]
        if (
            close is not None and ema_50 is not None
            and not pd.isna(close) and not pd.isna(ema_50)
        ):
            if ema_200 is not None and not pd.isna(ema_200):
                stacked_bull = close > ema_50 > ema_200
                stacked_bear = close < ema_50 < ema_200
                ema_pass = stacked_bull or stacked_bear
            else:
                # No EMA200 available — require a meaningful ATR-relative
                # separation from EMA50 instead of a tautology that always
                # passes (close > ema_50 or close < ema_50).
                atr = gatekeeper_data.get("atr") if gatekeeper_data else None
                if atr is not None and not pd.isna(atr) and float(atr) > 0:
                    ema_pass = abs(float(close) - float(ema_50)) > 0.1 * float(atr)
                else:
                    ema_pass = True  # fail-open
        gates.append({"name": "ema", "passed": bool(ema_pass)})

        # ── Gate 2: ADX Volatility Gate (ADX >= 20 = conviction) ────────
        adx_pass = True  # fail-open
        adx_val = gatekeeper_data.get("adx") if gatekeeper_data else None
        if (
            (adx_val is None or pd.isna(adx_val))
            and htf_data is not None and not htf_data.empty
            and "adx" in htf_data.columns
        ):
            adx_val = htf_data["adx"].iloc[-1]
        if adx_val is not None and not pd.isna(adx_val):
            adx_pass = float(adx_val) >= 20.0
        gates.append({"name": "adx", "passed": bool(adx_pass)})

        # ── Gate 3: RSI Momentum (not overbought / oversold) ────────────
        rsi_pass = True  # fail-open
        if entry_tf_data is not None and not entry_tf_data.empty:
            rsi_col = f"rsi_{settings.rsi_period}"
            if rsi_col in entry_tf_data.columns:
                rsi_val = entry_tf_data[rsi_col].iloc[-1]
                if not pd.isna(rsi_val):
                    rsi_pass = settings.rsi_oversold <= float(rsi_val) <= settings.rsi_overbought
        gates.append({"name": "rsi", "passed": bool(rsi_pass)})

        # ── Gate 4: Volume Surge (latest bar >= ratio × 20-bar avg) ─────
        vol_pass = True  # fail-open
        if entry_tf_data is not None and not entry_tf_data.empty:
            vol_col = "tick_volume" if "tick_volume" in entry_tf_data.columns else "volume"
            if vol_col in entry_tf_data.columns:
                latest_vol = entry_tf_data[vol_col].iloc[-1]
                vol_avg = entry_tf_data[vol_col].tail(20).mean()
                if vol_avg > 0 and not pd.isna(latest_vol):
                    vol_pass = latest_vol >= (settings.volume_surge_ratio * vol_avg)
        gates.append({"name": "volume", "passed": bool(vol_pass)})

        # ── Gate 5: Market Regime Gate (stateless — not DEAD) ───────────
        reg_pass = True  # fail-open
        if htf_data is not None and not htf_data.empty:
            latest = htf_data.iloc[-1]
            adx = latest.get("adx", 0)
            atr_ratio = latest.get("atr_ratio", 1.0)
            if pd.isna(adx):
                adx = 0
            if pd.isna(atr_ratio):
                atr_ratio = 1.0
            regime = self._classify_regime(adx, atr_ratio)
            reg_pass = regime != MarketRegime.DEAD
        gates.append({"name": "regime", "passed": bool(reg_pass)})

        passed = sum(1 for g in gates if g["passed"])
        return {
            "gates": [g["passed"] for g in gates],
            "passed": passed,
            "total": 5,
            "overall": (
                f"{passed}/5 OPTIMAL" if passed >= 5
                else f"{passed}/5 MODERATE" if passed >= 3
                else f"{passed}/5 BLOCKED"
            ),
            "details": gates,
            "symbol": symbol,
        }

    def evaluate_symbol_signal(
        self,
        symbol: str,
        htf_data: Optional[pd.DataFrame],
        etf_data: Optional[pd.DataFrame],
        current_price: Optional[dict],
    ) -> Optional[TradeSignal]:
        """Generate a live trade signal for a *symbol* — stateless.

        Backend behind the dashboard's header trade-signal pill
        (``GET /api/signal``).  Mirrors ``get_signal`` (regime → profile
        strategy → ``generate_signal``) but uses ``_classify_regime``
        directly instead of ``detect_regime``, so it NEVER mutates
        hysteresis state, fires notifications, or broadcasts WebSocket
        events — safe to call from a read-only HTTP route on every
        dropdown symbol change.

        Args:
            symbol: Trading pair.
            htf_data: Analyzed higher-timeframe DataFrame (adx, atr_ratio).
            etf_data: Analyzed entry-timeframe DataFrame.
            current_price: Current bid/ask dict from ``get_current_price``.

        Returns:
            ``TradeSignal`` if the profile's strategy produced one,
            ``None`` on DEAD / NEWS_EVENT regimes or when no strategy is
            configured for the classified regime.
        """
        if htf_data is None or htf_data.empty:
            return None

        latest = htf_data.iloc[-1]
        adx = latest.get("adx", 0)
        atr_ratio = latest.get("atr_ratio", 1.0)
        if pd.isna(adx):
            adx = 0
        if pd.isna(atr_ratio):
            atr_ratio = 1.0
        regime = self._classify_regime(adx, atr_ratio)

        # Same non-tradeable regimes as get_signal (VOLATILE falls through
        # to "no strategy" naturally for profiles that don't map it).
        if regime in (MarketRegime.DEAD, MarketRegime.NEWS_EVENT):
            return None

        strategy = self.strategies.get(regime)
        if strategy is None:
            return None

        return strategy.generate_signal(
            symbol, htf_data, etf_data, current_price
        )


    def _get_session_from_time(self) -> SessionType:
        """Determine current market session based on UTC time."""
        now = datetime.utcnow().time()

        # Asian: 00:00 - 07:00 UTC
        if time(0, 0) <= now < time(7, 0):
            return SessionType.ASIAN

        # London: 07:00 - 12:00 UTC
        if time(7, 0) <= now < time(12, 0):
            return SessionType.LONDON

        # Overlap: 12:00 - 16:00 UTC (London-NY overlap)
        if time(12, 0) <= now < time(16, 0):
            return SessionType.OVERLAP

        # New York: 16:00 - 20:00 UTC
        if time(16, 0) <= now < time(20, 0):
            return SessionType.NEW_YORK

        # Outside all sessions: 20:00 - 00:00 UTC
        return SessionType.OUTSIDE

    def _is_opening_window(self, session: SessionType) -> bool:
        """Check if we're in the opening window of a session (0-30 min)."""
        now = datetime.utcnow().time()

        if session == SessionType.LONDON:
            # London opens at 07:00 UTC
            return time(7, 0) <= now < time(7, 30)
        elif session == SessionType.NEW_YORK:
            # NY opens at 12:00 UTC
            return time(12, 0) <= now < time(12, 30)

        return False

    def evaluate_daytrader_signals(
        self,
        symbol: str,
        htf_data: pd.DataFrame,
        etf_data: pd.DataFrame,
        current_price: dict,
    ) -> Optional[TradeSignal]:
        """
        Evaluate all 3 strategies for daytrader profile.

        The 3×14 Matrix:
        - Strategy B: Mean Reversion (Asian Session / Range Gate)
        - Strategy C: Session Breakout (London/NY Opens)
        - Strategy A: Trend Engine (London & NY Main Sessions)

        Returns the best signal found, or None if no valid signal.
        """
        if self.profile != "daytrader":
            return None

        # Get session state
        session = self._get_session_from_time()

        # Initialize strategies
        trend_strategy = TrendEngineStrategy()
        breakout_strategy = SessionBreakoutStrategy()
        mean_reversion_strategy = MeanReversionStrategy()

        signals = []

        # ── Strategy B: Mean Reversion (Asian Session Only) ──────────
        if session == SessionType.ASIAN:
            signal = mean_reversion_strategy.generate_signal(
                symbol, htf_data, etf_data, current_price
            )
            if signal:
                logger.info(f"[DAYTRADER] MeanReversion signal: {signal}")
                signals.append(signal)

        # ── Strategy C: Session Breakout (Opening Windows) ───────────
        if self._is_opening_window(session):
            signal = breakout_strategy.generate_signal(
                symbol, htf_data, etf_data, current_price
            )
            if signal:
                logger.info(f"[DAYTRADER] Breakout signal: {signal}")
                signals.append(signal)

        # ── Strategy A: Trend Engine (London/NY Sessions) ─────────────
        if session in (SessionType.LONDON, SessionType.NEW_YORK, SessionType.OVERLAP):
            signal = trend_strategy.generate_signal(
                symbol, htf_data, etf_data, current_price
            )
            if signal:
                logger.info(f"[DAYTRADER] TrendEngine signal: {signal}")
                signals.append(signal)

        # Return best signal (highest confidence, then highest R:R)
        if signals:
            # Sort by confidence first, then by risk_reward_ratio
            best_signal = max(signals, key=lambda s: (s.confidence, s.risk_reward_ratio))
            return best_signal

        return None

    def force_regime(self, regime: MarketRegime):
        """Manually override the regime (e.g., for news events)."""
        old = self._current_regime
        self._current_regime = regime
        logger.warning(f"⚡ Regime forced: {old} → {regime}")

        # Broadcast forced regime change to WebSocket dashboard clients
        if _WS_AVAILABLE:
            try:
                _ws_tasks = getattr(self, "_ws_tasks", None)
                if _ws_tasks is None:
                    _ws_tasks = self._ws_tasks = set()
                _ws_task = asyncio.create_task(ws_manager.broadcast(
                    WSEventPayload(
                        event_type="REGIME_CHANGE",
                        timestamp=datetime.now().isoformat(),
                        data={
                            "old_regime": str(old.value) if old else "unknown",
                            "new_regime": str(regime.value),
                            "forced": True,
                            "reason": "manual override",
                        },
                    )
                ))
                _ws_tasks.add(_ws_task)
                _ws_task.add_done_callback(_ws_tasks.discard)
            except Exception:
                pass

    def release_forced_regime(self) -> None:
        """
        Clear any user-forced regime so detect_regime() resumes auto-classification.

        Called by the orchestrator when a manual override (e.g., a news-event
        pause) should expire; after this returns, the next call to
        `detect_regime()` will reclassify from the live HTF data.
        """
        if self._current_regime is None:
            logger.debug("No forced regime to release")
            return
        old = self._current_regime
        self._current_regime = None
        self._regime_history.append({
            "from": old,
            "to": None,
            "adx": None,
            "atr_ratio": None,
            "released": True,
        })
        logger.warning(f"🔓 Forced regime released: {old.value} → auto")

        # Broadcast regime release to WebSocket dashboard clients
        if _WS_AVAILABLE:
            try:
                _ws_tasks = getattr(self, "_ws_tasks", None)
                if _ws_tasks is None:
                    _ws_tasks = self._ws_tasks = set()
                _ws_task = asyncio.create_task(ws_manager.broadcast(
                    WSEventPayload(
                        event_type="REGIME_CHANGE",
                        timestamp=datetime.now().isoformat(),
                        data={
                            "old_regime": str(old.value) if old else "unknown",
                            "new_regime": "auto",
                            "forced": False,
                            "reason": "forced regime released",
                        },
                    )
                ))
                _ws_tasks.add(_ws_task)
                _ws_task.add_done_callback(_ws_tasks.discard)
            except Exception:
                pass

    def get_regime_stats(self) -> dict:
        """Get statistics about regime detection history."""
        return {
            "current": self._current_regime.value if self._current_regime else "unknown",
            "changes": len(self._regime_history),
            "history": self._regime_history[-10:],  # Last 10 changes
        }
