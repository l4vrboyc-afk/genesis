"""
Strategy Selector — Detects market regime and selects the right strategy.
This is the brain's brain — it decides WHICH strategy to use based on conditions.

For Day Trader profile, implements a 3×14 matrix approach:
- 3 strategies evaluated per symbol
- 14 currency pairs across multiple sessions
- Session-aware routing prevents signal collisions
"""

import numpy as np
import pandas as pd
from datetime import datetime, time
from typing import Optional
from loguru import logger
from enum import Enum

from bot.config.settings import settings, MarketRegime
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

        # High volatility spike — reduce exposure
        if atr_ratio > settings.atr_volatility_spike:
            regime = MarketRegime.VOLATILE
        # Strong trend
        elif adx > settings.adx_trend_threshold:
            regime = MarketRegime.TRENDING
        # Ranging / sideways
        elif adx < settings.adx_range_threshold:
            regime = MarketRegime.RANGING
        # Low volatility / dead market
        elif atr_ratio < 0.5:
            regime = MarketRegime.DEAD
        else:
            # In-between — default to ranging (safer)
            regime = MarketRegime.RANGING

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
                    logger.info(
                        f"🔄 Hysteresis cleared: {self._current_regime} → {regime} "
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

    # ── Day Trader Multi-Strategy Evaluation ─────────────────────────────
    # For the daytrader profile, evaluate all 3 strategies and return best signal

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

    def get_regime_stats(self) -> dict:
        """Get statistics about regime detection history."""
        return {
            "current": self._current_regime.value if self._current_regime else "unknown",
            "changes": len(self._regime_history),
            "history": self._regime_history[-10:],  # Last 10 changes
        }
