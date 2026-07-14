"""
Scalper Momentum Strategy (v2).

Catches intraday momentum bursts on M1 (or any intraday TF set via
``settings.entry_timeframe``). The original v1 logic was a single
hard threshold (``rsi > 65`` for buys, ``rsi < 35`` for sells) which
confined entries to the small slice of bars where momentum was already
exhausted — that produced ~0 trades/hour on quiet sessions.

v2 keeps the same RSI-anchored signal concept but moves the trigger
to where momentum actually *starts*, and pairs it with the volume
surge gate that v1 promised but never read:

  BUY when EITHER:
    (a) crossover: previous bar's RSI ≤ 55 and current RSI > 55
    (b) zone-with-slope:  58 ≤ RSI ≤ 65  AND  RSI is higher than the
        bar 3 steps back  AND  previous bar's RSI > 56 (so we are
        already inside bullish territory, not a dead-cat bounce
        out of a deep low)

  SELL when EITHER (mirrored):
    (a) crossover: previous bar's RSI ≥ 45 and current RSI < 45
    (b) zone-with-slope:  35 ≤ RSI ≤ 42  AND  RSI lower than 3 bars ago
        AND  previous bar's RSI < 44

  CONFIDENCE GATE — both directions require:
    • tick volume on the latest bar >= 1.5× the 20-bar mean
      (MT5 reports tick-volume; ``volume_avg_20`` is precomputed by
      ``DataFetcher.calculate_indicators`` so this is a single
      arithmetic comparison)
    • Confidence score: 0.65 for a crossover (reversal), 0.55 for
      a zone-with-slope (continuation). ``MIN_CONFIDENCE`` defaults
      to 0.45 in ``.env.scalper`` so both pass validation; the
      asymmetry pushes the validator's preference toward crossovers
      because they have a stronger statistical edge.

  ENTRY / EXIT — unchanged from v1:
    • SL = entry ∓ (settings.atr_sl_multiplier × ATR)
    • TP = entry ± (settings.atr_tp_multiplier × ATR)
    Adapted to whatever timeframe / multiplier the scalper profile
    exposes (.env.scalper ships M1 / 0.8× / 1.2× by default).

This is a v2, not v3: the data flow, validation, and risk gating are
unchanged. The orchestrator's existing ``_execute_trading_cycle`` loop
already iterates ``settings.trading_pairs`` and routes pairs through
``asyncio.gather`` for parallel reads, so adding more pairs in .env
scales linearly with no code change.
"""
import numpy as np
import pandas as pd
from typing import Optional
from loguru import logger

from bot.config.settings import settings, MarketRegime, TradeDirection
from bot.strategies.base_strategy import BaseStrategy, TradeSignal


# v2 constants — kept module-level so the strategy class methods stay
# readable and so future parameter sweeps only have to edit one place.
_BUY_CROSSOVER_THRESHOLD = 55.0      # previous RSI must be at-or-below this
_BUY_ZONE_LOW = 58.0                 # zone-with-slope must sit in [58, 65]
_BUY_ZONE_HIGH = 65.0
# _BUY_ZONE_PREV_MIN = 56.0         # REMOVED: prev RSI > 56 no longer required (relaxed)

_SELL_CROSSOVER_THRESHOLD = 45.0
_SELL_ZONE_LOW = 35.0
_SELL_ZONE_HIGH = 42.0
# _SELL_ZONE_PREV_MAX = 44.0         # REMOVED: prev RSI < 44 no longer required (relaxed)

_VOLUME_SURGE_RATIO = 1.5           # require latest bar volume >= 1.5× the 20-bar mean
_SLOPE_LOOKBACK = 3                  # compare latest RSI to RSI 3 bars earlier

# Two confidence tiers — crossover (reversal) edges out zone (continuation).
_CONFIDENCE_CROSSOVER = 0.65
_CONFIDENCE_ZONE = 0.55


class ScalperMomentumStrategy(BaseStrategy):
    """Momentum-burst scalp entries anchored to RSI + tick-volume.

    Designed for intraday timeframes (``M1`` / ``M3`` / ``M5``).
    regime matcher says this strategy is suitable for trending and
    volatile markets; for ranging / dead markets the strategy
    selector falls through to a placeholder which the regeneration
    logic intentionally keeps empty so the bot does not trade a
    sideways noise floor with the same setup.
    """

    def __init__(self):
        super().__init__()
        self._name = "Scalper Momentum"
        self._description = (
            "RSI crossover / zone-with-slope entries confirmed by "
            "tick-volume surge; for intraday trending & volatile sessions."
        )
        self._suitable_regimes = [MarketRegime.TRENDING, MarketRegime.VOLATILE]

        # ── Session counters for summary logging ──────────────────────
        self._eval_count = 0
        self._signal_counts = {
            "CROSSOVER_BUY": 0,
            "CROSSOVER_SELL": 0,
            "ZONE_WITH_SLOPE_BUY": 0,
            "ZONE_WITH_SLOPE_SELL": 0,
            "HOLD": 0,
            "VOLUME_REJECT": 0,
            "NAN_REJECT": 0,
        }
        self._SUMMARY_INTERVAL = 100  # log summary every N evaluations

    def generate_signal(
        self,
        symbol: str,
        higher_tf_data: pd.DataFrame,
        entry_tf_data: pd.DataFrame,
        current_price: dict,
    ) -> Optional[TradeSignal]:
        # ── Data sufficiency guards ────────────────────────────────
        #  - need 4+ bars for prev / 3-ago references and for rolling
        #    baseline to be meaningful.
        #  - ``_SLOPE_LOOKBACK + 1`` lets the strategy read ``iloc[-4]``
        #    safely even on a fresh-history limit.
        if entry_tf_data is None or entry_tf_data.empty:
            return None
        if len(entry_tf_data) < _SLOPE_LOOKBACK + 1:
            return None

        latest = entry_tf_data.iloc[-1]
        prev = entry_tf_data.iloc[-2]
        slope_bar = entry_tf_data.iloc[-(_SLOPE_LOOKBACK + 1)]

        # ── Pull indicator values ──────────────────────────────────
        # Per-period column names (``rsi_14``, ``atr_14``) avoid the
        # v1 bug of silently producing RSI=50 / ATR=0.001 when the
        # period couldn't be found.
        rsi_col = f"rsi_{settings.rsi_period}"
        atr_col = f"atr_{settings.atr_period}"

        rsi = latest.get(rsi_col, np.nan)
        prev_rsi = prev.get(rsi_col, np.nan)
        rsi_3ago = slope_bar.get(rsi_col, np.nan)
        atr = latest.get(atr_col, np.nan)
        volume = latest.get("volume", np.nan)
        volume_avg = latest.get("volume_avg_20", np.nan)

        # Any NaN short-circuits — when indicators aren't warm yet
        # (post-startup, post-reconnect, or insufficient history) the
        # strategy refuses to fire rather than fall back to defaults.
        for v in (rsi, prev_rsi, rsi_3ago, atr, volume, volume_avg):
            if pd.isna(v):
                self._signal_counts["NAN_REJECT"] += 1
                logger.info(f"📉 {symbol} — NaN indicator (RSI={rsi}, prev={prev_rsi}, 3ago={rsi_3ago}, ATR={atr}, vol={volume}, vol_avg={volume_avg})")
                return None

        # Volume-surge gate — the bar we're reading must be at least
        # ``_VOLUME_SURGE_RATIO`` of the 20-bar tick-volume mean.
        # volume_avg <= 0 covers the edge cases where MT5 reports 0
        # for an inactive session (we don't want to divide by zero).
        if volume_avg <= 0:
            self._signal_counts["VOLUME_REJECT"] += 1
            logger.info(f"📉 {symbol} — volume_avg=0 (inactive session)")
            return None
        volume_ratio = volume / volume_avg
        if volume_ratio < _VOLUME_SURGE_RATIO:
            self._signal_counts["VOLUME_REJECT"] += 1
            logger.info(f"📉 {symbol} — volume surge FAIL: {volume_ratio:.1f}×avg (< {_VOLUME_SURGE_RATIO}×)")
            return None

        # ── Direction detection ────────────────────────────────────
        # Slope is the 3-bar change in RSI, sign matters for the
        # zone-with-slope branch (continuation needs direction
        # already under way), but crossovers *can* trigger against
        # the slope because they represent reversals.
        bullish_slope = rsi > rsi_3ago
        bearish_slope = rsi < rsi_3ago

        crossover_up = (prev_rsi <= _BUY_CROSSOVER_THRESHOLD) and (rsi > _BUY_CROSSOVER_THRESHOLD)
        # Relaxed zone: RSI in zone + 3-bar trend (no prev_rsi gate)
        zone_up = (
            (_BUY_ZONE_LOW <= rsi <= _BUY_ZONE_HIGH)
            and bullish_slope
        )

        crossover_down = (prev_rsi >= _SELL_CROSSOVER_THRESHOLD) and (rsi < _SELL_CROSSOVER_THRESHOLD)
        zone_down = (
            (_SELL_ZONE_LOW <= rsi <= _SELL_ZONE_HIGH)
            and bearish_slope
        )

        # ── Build the TradeSignal (or HOLD) ────────────────────────
        bid = current_price.get("bid", 0.0)
        ask = current_price.get("ask", 0.0)
        direction = TradeDirection.HOLD
        confidence = 0.0
        entry = 0.0
        sl = 0.0
        tp = 0.0
        reason = ""

        # ── Signal type tracking for debug logging ──
        signal_type = "HOLD"
        signal_details = ""

        if crossover_up:
            direction = TradeDirection.BUY
            confidence = _CONFIDENCE_CROSSOVER
            entry = ask
            sl = entry - (atr * settings.atr_sl_multiplier)
            tp = entry + (atr * settings.atr_tp_multiplier)
            signal_type = "CROSSOVER"
            signal_details = (
                f"RSI cross>{_BUY_CROSSOVER_THRESHOLD:.0f} "
                f"(curr={rsi:.1f}, prev={prev_rsi:.1f}, vol={volume_ratio:.1f}×avg, "
                f"3bar_ago={rsi_3ago:.1f}, slope={'up' if bullish_slope else 'down'})"
            )
            reason = f"Bullish RSI cross>{_BUY_CROSSOVER_THRESHOLD:.0f} (RSI {rsi:.1f} from {prev_rsi:.1f}, vol {volume_ratio:.1f}×avg)"
        elif zone_up:
            direction = TradeDirection.BUY
            confidence = _CONFIDENCE_ZONE
            entry = ask
            sl = entry - (atr * settings.atr_sl_multiplier)
            tp = entry + (atr * settings.atr_tp_multiplier)
            signal_type = "ZONE_WITH_SLOPE"
            signal_details = (
                f"RSI zone {_BUY_ZONE_LOW:.0f}-{_BUY_ZONE_HIGH:.0f} "
                f"(curr={rsi:.1f}, 3bar_ago={rsi_3ago:.1f}, slope={'up' if bullish_slope else 'down'}, "
                f"vol={volume_ratio:.1f}×avg)"
            )
            reason = f"Bullish zone {_BUY_ZONE_LOW:.0f}-{_BUY_ZONE_HIGH:.0f} (RSI {rsi:.1f}, +3bar slope, vol {volume_ratio:.1f}×avg)"
        elif crossover_down:
            direction = TradeDirection.SELL
            confidence = _CONFIDENCE_CROSSOVER
            entry = bid
            sl = entry + (atr * settings.atr_sl_multiplier)
            tp = entry - (atr * settings.atr_tp_multiplier)
            signal_type = "CROSSOVER"
            signal_details = (
                f"RSI cross<{_SELL_CROSSOVER_THRESHOLD:.0f} "
                f"(curr={rsi:.1f}, prev={prev_rsi:.1f}, vol={volume_ratio:.1f}×avg, "
                f"3bar_ago={rsi_3ago:.1f}, slope={'up' if bullish_slope else 'down'})"
            )
            reason = f"Bearish RSI cross<{_SELL_CROSSOVER_THRESHOLD:.0f} (RSI {rsi:.1f} from {prev_rsi:.1f}, vol {volume_ratio:.1f}×avg)"
        elif zone_down:
            direction = TradeDirection.SELL
            confidence = _CONFIDENCE_ZONE
            entry = bid
            sl = entry + (atr * settings.atr_sl_multiplier)
            tp = entry - (atr * settings.atr_tp_multiplier)
            signal_type = "ZONE_WITH_SLOPE"
            signal_details = (
                f"RSI zone {_SELL_ZONE_LOW:.0f}-{_SELL_ZONE_HIGH:.0f} "
                f"(curr={rsi:.1f}, 3bar_ago={rsi_3ago:.1f}, slope={'up' if bullish_slope else 'down'}, "
                f"vol={volume_ratio:.1f}×avg)"
            )
            reason = f"Bearish zone {_SELL_ZONE_LOW:.0f}-{_SELL_ZONE_HIGH:.0f} (RSI {rsi:.1f}, -3bar slope, vol {volume_ratio:.1f}×avg)"

        if direction == TradeDirection.HOLD:
            # Debug: log why no signal fired (throttle to avoid spam)
            logger.debug(
                f"🔍 SCALPER HOLD | {symbol} | RSI={rsi:.1f} (prev={prev_rsi:.1f}, "
                f"3bar_ago={rsi_3ago:.1f}) | vol_ratio={volume_ratio:.1f}×avg "
                f"| bull_slope={bullish_slope} bear_slope={bearish_slope} "
                f"| cross_up={crossover_up} zone_up={zone_up} "
                f"| cross_down={crossover_down} zone_down={zone_down}"
            )
            self._signal_counts["HOLD"] += 1
            self._maybe_log_summary()
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

        # BaseStrategy.validate_signal enforces:
        #   - SL/TP on the correct side of entry
        #   - min_reward_ratio (settings.min_reward_ratio, 1.0
        #     by default in .env.scalper, so 1.5 R:R passes)
        #   - min_confidence (settings.min_confidence, 0.45 in
        #     .env.scalper, so both tiers pass)
        if self.validate_signal(signal):
            # Track signal type for summary
            key = f"{signal_type}_{direction.value.upper()}"
            self._signal_counts[key] = self._signal_counts.get(key, 0) + 1

            logger.debug(
                f"🎯 SCALPER SIGNAL | {symbol} | {signal_type} | "
                f"{direction.value.upper()} | conf={confidence:.2f} | "
                f"entry={entry:.5f} SL={sl:.5f} TP={tp:.5f} | {signal_details}"
            )
            self._maybe_log_summary()
            return signal
        return None

    def _maybe_log_summary(self) -> None:
        """Log a summary of signal distribution every N evaluations."""
        self._eval_count += 1
        if self._eval_count % self._SUMMARY_INTERVAL != 0:
            return

        c = self._signal_counts
        total_signals = (
            c.get("CROSSOVER_BUY", 0)
            + c.get("CROSSOVER_SELL", 0)
            + c.get("ZONE_WITH_SLOPE_BUY", 0)
            + c.get("ZONE_WITH_SLOPE_SELL", 0)
        )
        logger.info(
            f"📊 SCALPER SUMMARY (last {self._SUMMARY_INTERVAL} evals) | "
            f"Total evals: {self._eval_count} | Signals: {total_signals} | "
            f"CROSSOVER: {c.get('CROSSOVER_BUY', 0)}B/{c.get('CROSSOVER_SELL', 0)}S | "
            f"ZONE+SLOPE: {c.get('ZONE_WITH_SLOPE_BUY', 0)}B/{c.get('ZONE_WITH_SLOPE_SELL', 0)}S | "
            f"HOLD: {c.get('HOLD', 0)} | "
            f"VOL_REJECT: {c.get('VOLUME_REJECT', 0)} | NAN_REJECT: {c.get('NAN_REJECT', 0)}"
        )
