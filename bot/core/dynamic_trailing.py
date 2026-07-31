"""
Dynamic Trailing Manager — Multi-Stage Position Management Engine.

Replaces the old single‑distance trailing stop with an adaptive mode‑switching
system that reacts to market structure and momentum:

  ╔═══════════════════════════════════╗
  ║   STAGE 0: STATIC ENTRY STOP     ║  ← initial SL from strategy (no change)
  ╚═══════════════╦═══════════════════╝
                  ▼  profit ≥ 1.0×ATR
  ╔═══════════════════════════════════╗
  ║   STAGE 1: SAFE BREAKEVEN        ║  SL → entry ± spread buffer
  ╚═══════════════╦═══════════════════╝
                  ▼
     ┌────────────┴────────────┐
     ▼                         ▼
  [Trending]             [Choppy / Range]
     ▼                         ▼
  ┌──────────────┐     ┌──────────────────┐
  │ STRUCTURE    │     │ ATR DYNAMIC      │
  │ (swing HL)   │     │ (1.5×ATR gap)    │
  └──────┬───────┘     └────────┬─────────┘
         │                      │
         └──────────┬───────────┘
                    ▼  profit ≥ 2.5×ATR
        ┌──────────────────────────┐
        │ ACCELERATED (M15 EMA20)  │  ← tight trail for blow‑off tops
        └──────────────────────────┘

Usage
=====
    manager = DynamicTrailingManager(fetcher, executor)
    manager.attach_executor(executor)

    # In the trading loop, for each open position:
    result = await manager.evaluate_and_trail(position_dict, tick)
    # Returns True if SL was modified, False if no change.
"""

from __future__ import annotations

import pandas as pd
import numpy as np
from enum import Enum
from typing import Any, Optional
from loguru import logger

from bot.visual import GenesisVisualEngine


# ── Constants ──────────────────────────────────────────────────────────
# The activation and gap multipliers used for mode transitions.
BREAKEVEN_ACTIVATION_ATR = 1.0          # 1.0×ATR profit → breakeven
ATR_TRAIL_GAP_MULTIPLIER = 1.5          # Volatility trail: 1.5×ATR behind price
ACCELERATED_ACTIVATION_ATR = 2.5        # 2.5×ATR profit → accelerated (EMA20)
SWING_LOOKBACK_CANDLES = 30             # M15 candles to scan for swing points
SWING_NEIGHBOUR_WINDOW = 2              # Candles either side for swing detection
M15_COUNT = 60                          # M15 candles to fetch (enough for EMA20 + swings)

# Spread buffer for breakeven / trail safety (pips added to spread).
# The existing SPREAD_GUARD_MULTIPLIER in order_manager covers the raw
# Bid/Ask gap; this is an extra 1‑pip cushion so the SL sits cleanly
# outside the noise band.
PIP_BUFFER = 10  # in points (10 points = 1 pip for majors, 1 pip for JPY)


class TrailingMode(str, Enum):
    """Progressive trailing-stop modes, ordered from loosest to tightest."""
    STATIC = "STATIC"            # 0 — Initial strategy SL (no bot modifications)
    BREAKEVEN = "BREAKEVEN"      # 1 — SL moved to entry ± spread buffer
    STRUCTURE = "STRUCTURE"      # 2A — Trail behind M15 swing highs/lows
    ATR_DYNAMIC = "ATR_DYNAMIC"  # 2B — Trail with 1.5×ATR volatility gap
    ACCELERATED = "ACCELERATED"  # 3 — Tight trail behind M15 EMA20


class DynamicTrailingManager:
    """Per‑position adaptive trailing‑stop engine.

    Tracks the current trailing mode for every open position and evaluates
    it each trading cycle, escalating through the stages as profit grows
    and market conditions change.

    Delegates low‑level MT5 writes to ``order_manager._apply_trailing_stop``
    so that TP preservation, spread guard (​`_verify_sl_spread_guard`),
    broker stop‑level clamping, and digits rounding are all handled by the
    existing, tested code path — rather than duplicating it here.
    """

    def __init__(self, fetcher: Any, order_manager: Any = None):
        self.fetcher = fetcher
        self._order_manager = order_manager
        # Serialised MT5 executor — injected by ``TradingOrchestrator.start``
        self._executor = None

        # Per‑position state: ticket → TrailingMode
        self._position_modes: dict[int, TrailingMode] = {}
        # Per‑position state: ticket → bool (has breakeven been applied?)
        self._breakeven_applied: dict[int, bool] = {}
        # Per‑position peak profit in ATR units — guards against stage
        # oscillation when a premature escalation happened on stale M15 data.
        # The peak feeds the accelerated-threshold check so a ticket that
        # briefly read ≥2.5×ATR on a stale snapshot keeps using the
        # accelerated trail instead of flip‑flopping back to STRUCTURE /
        # ATR_DYNAMIC when fresh (lower) data arrives.
        self._max_profit_atr: dict[int, float] = {}

    def attach_executor(self, executor) -> None:
        """Inject the serialised MT5 executor (called during orchestrator start)."""
        self._executor = executor



    # ── Public API ──────────────────────────────────────────────────

    async def evaluate_and_trail(
        self,
        pos: dict,
        tick: Any,
        m15_data: Optional[pd.DataFrame] = None,
    ) -> bool:
        """Run the full evaluation chain for one position and apply the
        best trailing stop.

        Args:
            pos: Position dict (must have keys: ticket, symbol, direction,
                entry_price, sl).
            tick: MT5 tick object with .bid, .ask, .time attributes.
            m15_data: Pre‑fetched M15 DataFrame. If ``None``, the manager
                fetches it internally (slightly slower but self‑contained).

        Returns:
            True if the SL was modified this cycle, False if unchanged.
        """
        symbol = pos["symbol"]
        ticket = pos["ticket"]
        direction = pos["direction"]  # "buy" or "sell"
        entry_price = pos["entry_price"]
        current_sl = pos.get("sl") or 0.0

        side_buy = (direction == "buy")

        # ── Fetch M15 data if not provided ──────────────────────────
        if m15_data is None or m15_data.empty:
            m15_data = await self._fetch_m15_data(symbol)
        if m15_data is None or m15_data.empty:
            return False

        # Compute indicators from the M15 DataFrame
        indicators = self._compute_m15_indicators(m15_data)
        if indicators is None:
            return False

        m15_atr = indicators["atr"]
        m15_ema20 = indicators["ema20"]
        swing_high = indicators["swing_high"]
        swing_low = indicators["swing_low"]

        if m15_atr is None or m15_atr <= 0:
            return False

        # ── Compute current floating profit in price units ──────────
        if side_buy:
            profit_dist = tick.bid - entry_price
        else:
            profit_dist = entry_price - tick.ask

        profit_atr = profit_dist / m15_atr  # normalised to ATR units

        # Track peak profit in ATR units for the oscillation guard below.
        peak_profit_atr = self._max_profit_atr.get(ticket, 0.0)
        if profit_atr > peak_profit_atr:
            self._max_profit_atr[ticket] = profit_atr
            peak_profit_atr = profit_atr

        spread = tick.ask - tick.bid
        spread_buffer = spread + PIP_BUFFER * (getattr(tick, "point", 0.00001) or 0.00001)

        # ── Mode selection state machine ─────────────────────────────
        current_mode = self._position_modes.get(ticket, TrailingMode.STATIC)

        # ── Stage 0 → 1: Check if profit has reached breakeven threshold ─
        if profit_atr < BREAKEVEN_ACTIVATION_ATR:
            # Not enough profit yet — stay in STATIC mode
            if current_mode != TrailingMode.STATIC:
                # Degenerate case: price pulled back below threshold;
                # don't move SL back — one‑way ratchet protects us.
                pass
            return False

        # ── Stage 1: Apply breakeven (once) ─────────────────────────
        if not self._breakeven_applied.get(ticket):
            success = await self._apply_breakeven(
                pos, tick, side_buy, entry_price, spread_buffer,
            )
            if success:
                self._breakeven_applied[ticket] = True
                self._position_modes[ticket] = TrailingMode.BREAKEVEN
                self._update_visuals(pos, TrailingMode.BREAKEVEN, m15_atr)
                logger.info(
                    f"💰 BREAKEVEN applied {direction.upper()} T{ticket} "
                    f"{symbol}: SL→entry ± {spread_buffer:.5f} "
                    f"(profit {profit_atr:.2f}×ATR)"
                )
            return success

        # ── Stage 1 → 2/3: Profit is beyond breakeven — pick active mode ─
        # If profit has reached accelerated threshold, go to Stage 3.
        # Use the *peak* profit (not the live value) for this threshold so a
        # stale‑data spike that briefly pushed profit_atr ≥2.5×ATR cannot
        # oscillate the mode ACCELERATED → STRUCTURE on the next fresh cycle.
        # The SL ratchet already blocks backward SL movement, so staying in
        # accelerated (which only tightens further when price keeps rising)
        # is the stable, safe choice.
        if peak_profit_atr >= ACCELERATED_ACTIVATION_ATR and m15_ema20 is not None:
            return await self._apply_accelerated_trail(
                pos, tick, side_buy, entry_price,
                m15_ema20, m15_atr, spread_buffer, ticket, symbol, profit_atr,
            )

        # Choose between STRUCTURE (Stage 2A) and ATR_DYNAMIC (Stage 2B).
        if swing_high is not None and swing_low is not None:
            # Market has clean swings → use structure trail
            return await self._apply_structure_trail(
                pos, tick, side_buy, entry_price,
                swing_high, swing_low, m15_atr, spread_buffer,
                ticket, symbol, profit_atr,
            )

        # Fallback: ATR volatility trail
        return await self._apply_atr_trail(
            pos, tick, side_buy, entry_price,
            m15_atr, spread_buffer, ticket, symbol, profit_atr,
        )

    # ── Stage 1: Breakeven ──────────────────────────────────────────

    async def _apply_breakeven(
        self,
        pos: dict, tick, side_buy: bool,
        entry_price: float, spread_buffer: float,
    ) -> bool:
        """Move SL to entry ± spread buffer so the trade is risk‑free.

        SELL: SL = entry_price + spread_buffer   (SL triggers on Ask → above entry)
        BUY:  SL = entry_price − spread_buffer   (SL triggers on Bid → below entry)
        """
        if side_buy:
            new_sl = entry_price - spread_buffer
            # Ratchet: only move SL up (better) for BUY
            current_sl = pos.get("sl") or 0.0
            if new_sl <= current_sl:
                return False
        else:
            new_sl = entry_price + spread_buffer
            # Ratchet: only move SL down (better) for SELL
            current_sl = pos.get("sl") or 0.0
            if current_sl > 0 and new_sl >= current_sl:
                return False

        if new_sl <= 0:
            return False

        applied = await self._apply_sl(
            pos["ticket"], pos["symbol"], new_sl,
            existing_tp=pos.get("tp") or 0.0,
        )
        if applied:
            self._record_trail_milestone(
                pos["ticket"], pos["symbol"], new_sl, TrailingMode.BREAKEVEN,
            )
        return applied

    @staticmethod
    def _update_visuals(pos: dict, mode: TrailingMode, atr_value: float) -> None:
        """Fire-and-forget visual update for a trailing stop modification.

        Called after a successful SL modification so the MT5 chart lines
        are updated immediately. This is a synchronous, best-effort call
        — never raises, never blocks.
        """
        try:
            GenesisVisualEngine.update_trade_visuals(
                symbol=pos["symbol"],
                ticket=pos["ticket"],
                position_type=pos["direction"],
                entry_price=pos["entry_price"],
                current_sl=pos.get("sl", 0.0),
                target_tp=pos.get("tp", 0.0),
                current_mode=mode.value if hasattr(mode, 'value') else str(mode),
                atr_value=atr_value,
            )
        except Exception as e:
            logger.debug(f"VisualEngine: update failed for T{pos['ticket']}: {e}")

    @staticmethod
    def _record_trail_milestone(
        ticket: int, symbol: str, sl_price: float, mode: TrailingMode,
    ) -> None:
        """Fire-and-forget trail milestone recording.

        Appends an OBJ_TREND milestone point to the shared visuals file so
        the MQL5 EA can draw the trailing‑stop history for this position.
        Mirrors ``_update_visuals`` — synchronous, best‑effort, never raises.
        """
        try:
            GenesisVisualEngine.add_trail_milestone(
                ticket=ticket,
                symbol=symbol,
                sl_price=sl_price,
                mode=mode.value if hasattr(mode, "value") else str(mode),
            )
        except Exception as e:
            logger.debug(f"VisualEngine: trail milestone failed for T{ticket}: {e}")

    # ── Stage 2A: Structure (Swing) Trail ───────────────────────────

    async def _apply_structure_trail(
        self,
        pos: dict, tick, side_buy: bool, entry_price: float,
        swing_high: float, swing_low: float,
        m15_atr: float, spread_buffer: float,
        ticket: int, symbol: str, profit_atr: float,
    ) -> bool:
        """Trail SL behind the most recent M15 swing high/low.

        SELL: SL = most recent swing high + spread buffer
        BUY:  SL = most recent swing low − spread buffer
        """
        current_sl = pos.get("sl") or 0.0

        if side_buy:
            if swing_low is None or swing_low <= 0:
                return False
            new_sl = swing_low - spread_buffer
            # BUY ratchet: SL must move up (higher)
            if new_sl <= current_sl:
                return False
        else:
            if swing_high is None or swing_high <= 0:
                return False
            new_sl = swing_high + spread_buffer
            # SELL ratchet: SL must move down (lower)
            if current_sl > 0 and new_sl >= current_sl:
                return False

        if new_sl <= 0:
            return False

        applied = await self._apply_sl(
            ticket, symbol, new_sl,
            existing_tp=pos.get("tp") or 0.0,
        )
        if applied:
            self._position_modes[ticket] = TrailingMode.STRUCTURE
            self._update_visuals(pos, TrailingMode.STRUCTURE, m15_atr)
            self._record_trail_milestone(ticket, symbol, new_sl, TrailingMode.STRUCTURE)
            logger.info(
                f"🏗️ STRUCTURE trail {symbol} T{ticket}: SL→{new_sl:.5f} "
                f"(swing HL, profit {profit_atr:.2f}×ATR)"
            )
        return applied

    # ── Stage 2B: ATR Dynamic Trail ─────────────────────────────────

    async def _apply_atr_trail(
        self,
        pos: dict, tick, side_buy: bool, entry_price: float,
        m15_atr: float, spread_buffer: float,
        ticket: int, symbol: str, profit_atr: float,
    ) -> bool:
        """Trail SL with a fixed volatility gap of 1.5×ATR behind current price.

        SELL: SL = current_ask + 1.5×ATR + spread_buffer
        BUY:  SL = current_bid − 1.5×ATR − spread_buffer
        """
        current_sl = pos.get("sl") or 0.0
        atr_gap = ATR_TRAIL_GAP_MULTIPLIER * m15_atr

        if side_buy:
            new_sl = tick.bid - atr_gap - spread_buffer
            # BUY ratchet: SL must move up
            if new_sl <= current_sl:
                return False
        else:
            new_sl = tick.ask + atr_gap + spread_buffer
            # SELL ratchet: SL must move down
            if current_sl > 0 and new_sl >= current_sl:
                return False

        if new_sl <= 0:
            return False

        applied = await self._apply_sl(
            ticket, symbol, new_sl,
            existing_tp=pos.get("tp") or 0.0,
        )
        if applied:
            self._position_modes[ticket] = TrailingMode.ATR_DYNAMIC
            self._update_visuals(pos, TrailingMode.ATR_DYNAMIC, m15_atr)
            self._record_trail_milestone(ticket, symbol, new_sl, TrailingMode.ATR_DYNAMIC)
            logger.info(
                f"🌊 ATR DYNAMIC trail {symbol} T{ticket}: SL→{new_sl:.5f} "
                f"(gap {atr_gap:.5f}, profit {profit_atr:.2f}×ATR)"
            )
        return applied

    # ── Stage 3: Accelerated (EMA20) Trail ──────────────────────────

    async def _apply_accelerated_trail(
        self,
        pos: dict, tick, side_buy: bool, entry_price: float,
        m15_ema20: float, m15_atr: float, spread_buffer: float,
        ticket: int, symbol: str, profit_atr: float,
    ) -> bool:
        """Tight trail snug behind the M15 20‑period EMA.

        Used during parabolic / blow‑off moves to capture the final
        momentum surge without giving back all gains when it reverses.

        SELL: SL = EMA20 + spread_buffer
        BUY:  SL = EMA20 − spread_buffer
        """
        current_sl = pos.get("sl") or 0.0

        if side_buy:
            new_sl = m15_ema20 - spread_buffer
            if new_sl <= current_sl:
                return False
        else:
            new_sl = m15_ema20 + spread_buffer
            if current_sl > 0 and new_sl >= current_sl:
                return False

        if new_sl <= 0:
            return False

        applied = await self._apply_sl(
            ticket, symbol, new_sl,
            existing_tp=pos.get("tp") or 0.0,
        )
        if applied:
            self._position_modes[ticket] = TrailingMode.ACCELERATED
            self._update_visuals(pos, TrailingMode.ACCELERATED, m15_atr)
            self._record_trail_milestone(ticket, symbol, new_sl, TrailingMode.ACCELERATED)
            logger.info(
                f"🚀 ACCELERATED trail {symbol} T{ticket}: SL→{new_sl:.5f} "
                f"(EMA20, profit {profit_atr:.2f}×ATR)"
            )
        return applied

    # ── Low‑Level SL Write ─────────────────────────────────────────
    # Delegates to ``order_manager._apply_trailing_stop()`` so that:
    #   - TP is preserved (critical bug fix — the old code set ``tp: 0.0``)
    #   - The spread guard (``_verify_sl_spread_guard``) is applied
    #   - Broker stop‑level clamping is honoured
    #   - Digits‑correct rounding is used
    #
    # The order manager returns ``True`` on success and logs its own
    # diagnostics, so we can rely on its return value directly.

    async def _apply_sl(
        self, ticket: int, symbol: str, new_sl: float,
        existing_tp: float = 0.0,
    ) -> bool:
        """Delegate SL modification to ``order_manager._apply_trailing_stop``.

        This is the only path that touches MT5 for trailing‑stop writes,
        ensuring every SL modification passes through the same verification
        chain (spread guard, stop‑level clamp, TP preservation).

        Args:
            ticket: MT5 position ticket.
            symbol: Trading pair.
            new_sl: The new stop-loss price.
            existing_tp: The position's current take-profit price. Pass this
                to prevent TP from being cleared on every trailing update
                (MT5's ``TRADE_ACTION_SLTP`` interprets ``tp=0.0`` as
                "remove take profit").
        """
        if self._order_manager is None:
            logger.error("❌ DynamicTrailing: order_manager not attached")
            return False
        return await self._order_manager._apply_trailing_stop(
            ticket, new_sl, symbol, existing_tp=existing_tp,
        )

    # ── Data Helpers ───────────────────────────────────────────────

    async def _fetch_m15_data(self, symbol: str) -> Optional[pd.DataFrame]:
        """Fetch M15 candles and compute the indicators needed for trailing.

        Returns a DataFrame with at least: close, high, low columns.
        The caller's `_compute_m15_indicators` extracts EMA, ATR, swings.
        """
        try:
            df = await self.fetcher.get_analyzed_data(
                symbol, "M15", M15_COUNT,
            )
            return df
        except Exception as e:
            logger.debug(f"⚠️ DynamicTrailing: M15 data fetch failed for {symbol}: {e}")
            return None

    @staticmethod
    def _compute_m15_indicators(df: pd.DataFrame) -> Optional[dict]:
        """Extract ATR, EMA20, and swing high/low from an M15 DataFrame.

        Args:
            df: DataFrame with high, low, close columns, plus any
                pre‑computed ``atr_14`` or ``ema_20`` columns. If those
                columns are missing they are computed here.

        Returns:
            Dict with keys ``atr``, ``ema20``, ``swing_high``, ``swing_low``,
            or None if essential data is missing.
        """
        if df is None or df.empty:
            return None

        # ── ATR ────────────────────────────────────────────────────
        atr = None
        # Try pre‑computed columns first (get_analyzed_data may include them)
        for col in ("atr_14", f"atr_{14}", "atr"):
            if col in df.columns:
                val = df[col].iloc[-1]
                if not pd.isna(val) and val > 0:
                    atr = float(val)
                    break

        if atr is None:
            # Compute fresh ATR(14) from OHLC
            try:
                import ta
                atr_series = ta.volatility.average_true_range(
                    df["high"], df["low"], df["close"], length=14,
                )
                if atr_series is not None:
                    val = atr_series.iloc[-1]
                    if not pd.isna(val) and val > 0:
                        atr = float(val)
            except Exception:
                pass

        # ── EMA(20) ────────────────────────────────────────────────
        ema20 = None
        for col in ("ema_20", "ema20", "EMA_20"):
            if col in df.columns:
                val = df[col].iloc[-1]
                if not pd.isna(val):
                    ema20 = float(val)
                    break

        if ema20 is None:
            try:
                import ta
                ema_series = ta.trend.ema_indicator(df["close"], length=20)
                if ema_series is not None:
                    val = ema_series.iloc[-1]
                    if not pd.isna(val):
                        ema20 = float(val)
            except Exception:
                pass

        # ── Swing Highs / Lows ────────────────────────────────────
        swing_high, swing_low = _detect_swings(
            df["high"].values,
            df["low"].values,
            window=SWING_NEIGHBOUR_WINDOW,
        )

        return {
            "atr": atr,
            "ema20": ema20,
            "swing_high": swing_high,
            "swing_low": swing_low,
        }

    # ── State Cleanup ──────────────────────────────────────────────

    def cleanup(self, active_tickets: set) -> None:
        """Remove closed positions from internal tracking.

        Called from the orchestrator each cycle after the position snapshot.
        """
        stale = set(self._position_modes.keys()) - active_tickets
        for ticket in stale:
            self._position_modes.pop(ticket, None)
            self._breakeven_applied.pop(ticket, None)
            self._max_profit_atr.pop(ticket, None)


# ── Module‑level swing detection ────────────────────────────────────────


def _detect_swings(
    highs: np.ndarray,
    lows: np.ndarray,
    window: int = 2,
) -> tuple[Optional[float], Optional[float]]:
    """Detect the most recent swing high and swing low in a price array.

    A swing high is a candle whose high is the highest among its
    ``window`` neighbours on each side.  A swing low is symmetric.

    Only candles with full neighbours (i.e. not the first/last ``window``
    candles) are considered.

    Args:
        highs: Array of high prices (newest last).
        lows: Array of low prices (newest last).
        window: Number of candles on each side to compare.

    Returns:
        Tuple of (last_swing_high, last_swing_low). Either may be None
        if no swing of that type was found.
    """
    n = len(highs)
    if n < window * 2 + 1:
        return None, None

    last_swing_high: Optional[float] = None
    last_swing_low: Optional[float] = None

    for i in range(window, n - window):
        # Swing high: high[i] is higher than all neighbours
        is_high = all(highs[i] > highs[i - j] for j in range(1, window + 1)) and \
                  all(highs[i] > highs[i + j] for j in range(1, window + 1))
        if is_high:
            last_swing_high = float(highs[i])

        # Swing low: low[i] is lower than all neighbours
        is_low = all(lows[i] < lows[i - j] for j in range(1, window + 1)) and \
                 all(lows[i] < lows[i + j] for j in range(1, window + 1))
        if is_low:
            last_swing_low = float(lows[i])

    return last_swing_high, last_swing_low
