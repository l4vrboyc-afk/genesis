"""
Risk Manager — Position sizing, drawdown protection, and exposure control.
This module ensures the bot never risks more than it should.
"""

import asyncio
from collections import deque
import MetaTrader5 as mt5
from datetime import datetime, timedelta
from typing import Any, Optional
from loguru import logger

from bot.config.settings import settings, TradeDirection
from bot.strategies.base_strategy import TradeSignal

# ── Position-sizing safety guards ──────────────────────────────────────
# Enforce a realistic minimum stop-loss distance.  60 ticks = 6 pips on
# majors (tick_size=0.00001) and 6 pips on JPY crosses (tick_size=0.001).
MIN_SL_TICKS = 60

# Reject the trade outright when the raw lot size would be more than
# CAP_MULTIPLIER × MAX_LOT.  Without this, a tiny SL inflates the
# raw size to absurd levels, the cap silently absorbs 90%+ of the
# intended risk, and the resulting trade has a catastrophically degraded
# risk/reward ratio.
CAP_MULTIPLIER = 10.0


class RiskManager:
    """
    Manages all risk-related decisions:
    - Position sizing based on account balance and risk %
    - Max drawdown protection
    - Max open positions limit
    - Correlation filtering
    - Losing streak detection and cooldown
    """

    def __init__(self, connector):
        self.connector = connector
        self._consecutive_losses = 0
        self._daily_start_balance = 0.0
        self._last_reset_date = None
        self._cooldown_until = None
        self._trade_log = deque(maxlen=200)  # Recent trade results for streak tracking (bounded)

        # Track (d) — Kill-switch state. Once tripped, orchestrator auto-flattens
        # all positions and skips new entries. Re-armed by a new UTC day
        # (daily DD) or by manual release via dashboard / Discord command
        # (equity floor requires a manual reset because the peak reset only
        # happens when a new equity high is recorded).
        self._daily_dd_tripped: bool = False
        self._equity_floor_tripped: bool = False
        self._peak_equity: float = 0.0

        # Track (d) MT5 thread-safety — when the orchestrator wires the
        # AsyncMt5Executor into us, every native ``mt5.*`` call in this
        # module is rerouted through the executor's single worker thread.
        # ``None`` here keeps the legacy direct-call path so unit tests
        # that patch ``bot.risk.risk_manager.mt5.*`` keep working.
        self._executor: Any = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # ── Executor Plumbing (Track d) ─────────────────────────────────

    def attach_executor(self, executor, loop) -> None:
        """Inject the serialised MT5 executor + running loop.

        Called from ``TradingOrchestrator.start`` AFTER
        ``await executor.start()`` so the queue is alive. With this wiring,
        every ``mt5.fn(...)`` site in RiskManager hops onto the executor.
        Without it (test path / pre-attach window) we fall through to the
        direct call so existing tests keep their patch semantics.
        """
        self._executor = executor
        self._loop = loop

    def _mt5_call(self, func, *args, **kwargs):
        """Bridge a native ``mt5.X`` call onto the executor thread.

        Behavior:
            - If an executor + loop are attached AND the caller is off
              the event loop: push onto the executor's queue and block
              the calling worker on the result
              (``asyncio.run_coroutine_threadsafe(...).result()``). The
              executor's internal ``threading.Lock`` then serializes this
              call against all other MT5 work in the bot (connector,
              order_manager, data fetcher).
            - If the caller IS the event loop thread (e.g. a dashboard
              route handler calling ``release_equity_floor_trip``):
              fall through to a direct call — ``future.result()`` would
              block the loop and prevent the scheduled coroutine from
              ever running (same-thread deadlock → 15 s TimeoutError).
            - If executor not attached (tests / cold start): fall through
              to the direct call so unittest.mock patches remain in
              effect and we do not deadlock awaiting a not-yet-attached
              executor.
        """
        if self._executor is None or self._loop is None:
            return func(*args, **kwargs)
        # Guard against same-thread deadlock when the caller is the event
        # loop thread itself (e.g. a dashboard control route).
        try:
            if asyncio.get_running_loop() is self._loop:
                return func(*args, **kwargs)
        except RuntimeError:
            pass  # No running loop — we are on a worker / main thread, safe
        future = asyncio.run_coroutine_threadsafe(
            self._executor.submit(func, *args, **kwargs),
            self._loop,
        )
        return future.result(timeout=15)


    # ── Position Sizing ─────────────────────────────────────────────

    async def calculate_position_size(
        self,
        signal: TradeSignal,
        account_balance: float,
        volatility_ratio: float = 1.0,
    ) -> float:
        """Async wrapper around the sync sizing math."""
        return await asyncio.to_thread(
            self._calculate_position_size_sync,
            signal,
            account_balance,
            volatility_ratio,
        )

    def _calculate_position_size_sync(
        self,
        signal: TradeSignal,
        account_balance: float,
        volatility_ratio: float = 1.0,
    ) -> float:
        """
        Calculate the correct lot size based on risk parameters.

        Formula: Lot Size = (Balance × Risk%) / (SL distance × Pip value)

        Args:
            signal: The trade signal with entry/SL prices
            account_balance: Current account balance
            volatility_ratio: ATR ratio for volatility scaling (higher = smaller size)

        Returns:
            Calculated lot size (clamped to symbol limits)
        """
        if signal.direction == TradeDirection.HOLD:
            return 0.0

        # Calculate risk amount in account currency
        risk_amount = account_balance * settings.max_risk_per_trade

        # SL distance in price
        sl_distance = abs(signal.entry_price - signal.stop_loss)
        if sl_distance == 0:
            logger.warning("⚠️ SL distance is 0 — cannot calculate position size")
            return 0.0

        # Get symbol info for pip value — routed through the executor when
        # attached (production); falls back to direct call when unit tests
        # have patched ``bot.risk.risk_manager.mt5.symbol_info``.
        symbol_info = self._mt5_call(mt5.symbol_info, signal.symbol)
        if symbol_info is None:
            logger.error(f"❌ Cannot get symbol info for {signal.symbol}")
            return 0.0

        # Calculate pip value
        tick_value = symbol_info.trade_tick_value
        tick_size = symbol_info.trade_tick_size

        if tick_size == 0 or tick_value == 0:
            logger.error(f"❌ Invalid tick data for {signal.symbol}")
            return 0.0

        # Guard 1 — reject unrealistically tight stop-losses.
        # A tiny SL inflates the raw lot size to absurd levels, which the
        # downstream cap silently absorbs — degrading the risk/reward ratio
        # to useless levels.  Denominating in ticks makes this universal
        # across majors (tick=0.00001) and JPY pairs (tick=0.001).
        min_sl_distance = tick_size * MIN_SL_TICKS
        if sl_distance < min_sl_distance:
            logger.error(
                f"[TRADE REJECTED] SL distance ({sl_distance:.5f}) "
                f"is below safety minimum ({min_sl_distance:.5f}, "
                f"{MIN_SL_TICKS} ticks). Adjust the strategy's SL logic."
            )
            return 0.0

        # Calculate value per lot for the SL distance
        # Note: tick_value and tick_size work together for position sizing
        # For ALL pairs, we use sl_distance/tick_size * tick_value which handles JPY correctly
        # JPY pairs: tick_value ~0.01, tick_size ~0.01 (per pip)
        # Major pairs: tick_value ~0.0001, tick_size ~0.00001 (per point)
        value_per_lot = (sl_distance / tick_size) * tick_value
        
        # DEBUG: Log the calculated values for verification
        logger.debug(f"[SIZE_CALC] {signal.symbol} sl_dist={sl_distance:.5f} tick_size={tick_size} tick_value={tick_value} value_per_lot={value_per_lot:.6f}")

        if value_per_lot == 0:
            return 0.0

        # Raw lot size
        lot_size = risk_amount / value_per_lot

        # Apply volatility scaling (higher volatility = smaller position)
        if settings.volatility_position_scale and volatility_ratio > 1.0:
            scale_factor = 1.0 / volatility_ratio
            lot_size *= scale_factor
            logger.debug(
                f"[VOL_SCALE] Volatility scaling: ratio={volatility_ratio:.2f}, "
                f"scale={scale_factor:.2f}"
            )

        # Apply FIXED mode override if active
        if getattr(settings, "lot_sizing_mode", "DYNAMIC").upper() == "FIXED":
            fixed_size = getattr(settings, "fixed_lot_size", 0.01)
            lot_size = fixed_size
            logger.debug(f"[SIZE_CALC] FIXED mode active: overriding dynamic size to {lot_size} lots")

        # Apply lot size safety guardrails (prevent catastrophic sizing)
        # Tighter limits: MAX_LOT should be reasonable for small accounts
        # Formula: min(broker_max, 1% of account equity, 5 lots)
        account_max_lot = settings.starting_capital / 100.0  # $1000 -> max 10 lots
        MAX_LOT = min(symbol_info.volume_max, account_max_lot, 50.0)

        # Guard 2 — reject when the raw lot size dwarfs the cap.
        # If the pre-clamp lot is more than CAP_MULTIPLIER × MAX_LOT the
        # trade setup is broken (usually from the tiny SL that Guard 1
        # would have caught; this is defence-in-depth for edge cases
        # Guard 1 misses).
        effective_risk = min(lot_size, MAX_LOT) * value_per_lot
        if lot_size > MAX_LOT * CAP_MULTIPLIER:
            logger.error(
                f"[TRADE REJECTED] Raw position ({lot_size:.1f} lots) "
                f"is {lot_size / MAX_LOT:.1f}× the cap ({MAX_LOT:.1f} lots). "
                f"Effective risk would be ${effective_risk:.2f} "
                f"vs budget ${risk_amount:.2f} — "
                f"{((lot_size - MAX_LOT) / lot_size) * 100:.0f}% of risk absorbed by cap."
            )
            return 0.0

        lot_size = max(0.01, min(lot_size, MAX_LOT))
        
        # Clamp to symbol limits
        lot_size = max(symbol_info.volume_min, min(lot_size, symbol_info.volume_max))
        lot_size = round(lot_size / symbol_info.volume_step) * symbol_info.volume_step
        lot_size = round(lot_size, 2)

        logger.info(
            f"[POSITION] Position size: {lot_size} lots | "
            f"Risk: ${risk_amount:.2f} ({settings.max_risk_per_trade*100}%) | "
            f"SL distance: {sl_distance:.5f}"
        )

        return lot_size

    # ── Trade Validation ────────────────────────────────────────────

    async def can_trade(self, signal: TradeSignal) -> dict:
        """Async wrapper — runs the sync checks on a worker thread."""
        return await asyncio.to_thread(self._can_trade_sync, signal)

    def _can_trade_sync(self, signal: TradeSignal) -> dict:
        """
        Run all pre-trade risk checks.

        Track (d): two new kill switches participate here:
          - daily_dd (auto-resets on new UTC day)
          - equity floor (peak-equity drop; manual release only)
        Both are exposed via ``is_*_tripped()`` so the orchestrator can
        call ``close_all_trades`` on the cycle that breaches them.

        Returns:
            dict with 'allowed' (bool) and 'reason' (str)
        """
        result = {"allowed": True, "reason": "All checks passed"}

        # ── Kill switch bail-out (Track d) ─────────────────────────
        # Fail fast if any switch is already engaged — avoids running
        # the rest of the checks when the verdict is already known.
        if self._daily_dd_tripped:
            return {
                "allowed": False,
                "reason": (
                    f"🚨 Daily drawdown kill switch active "
                    f"(limit {settings.max_daily_drawdown*100:.0f}%)"
                ),
            }
        if self._equity_floor_tripped:
            return {
                "allowed": False,
                "reason": (
                    f"🚨 Equity floor kill switch active (peak ${self._peak_equity:.2f}, "
                    f"drawdown ≥ {settings.equity_floor_kill_switch_pct*100:.0f}%)"
                ),
            }

        # Check cooldown from losing streak
        if self._cooldown_until and datetime.now() < self._cooldown_until:
            remaining = (self._cooldown_until - datetime.now()).total_seconds()
            return {
                "allowed": False,
                "reason": f"Cooling down after losing streak ({remaining:.0f}s remaining)",
            }

        # Equity-floor pre-check (Track d). _check_equity_floor trips
        # self._equity_floor_tripped if the band is breached; the next
        # cycle's early bail above will reject until manual release.
        if not self._check_equity_floor():
            return {
                "allowed": False,
                "reason": (
                    f"🚨 Equity floor breach detected "
                    f"(peak ${self._peak_equity:.2f}, drawdown ≥ "
                    f"{settings.equity_floor_kill_switch_pct*100:.0f}%)"
                ),
            }

        # Track (d) — Magic-number isolation: only our trades count.
        # Foreign / manual positions in the same account must NOT push
        # us past max_open_positions.
        our_positions = self._our_positions()
        if len(our_positions) >= settings.max_open_positions:
            return {
                "allowed": False,
                "reason": (
                    f"Max open positions reached "
                    f"({len(our_positions)}/{settings.max_open_positions})"
                ),
            }

        # Check daily drawdown (also sets self._daily_dd_tripped on breach)
        if not self._check_daily_drawdown():
            return {
                "allowed": False,
                "reason": f"🚨 Daily drawdown kill switch tripped (limit {settings.max_daily_drawdown*100:.0f}%)",
            }

        # Check for duplicate positions (same symbol, same direction) —
        # magic-filtered now.
        if self._has_duplicate_position(signal):
            return {
                "allowed": False,
                "reason": f"Already have a {signal.direction.value} position on {signal.symbol}",
            }

        # Check correlation filter — magic-filtered + opposite-direction
        # tightening (Track d).
        correlated = self._check_correlation(signal)
        if correlated:
            return {
                "allowed": False,
                "reason": f"Correlated position already open: {correlated}",
            }

        # ── Currency Exposure Cap (Track d) ─────────────────────────────
        # Prevent exposure to the same currency across multiple positions.
        # "Currency exposure cap" = max 2 positions containing the same
        # base or quote currency. E.g., EURUSD, EURGBP, EURJPY = 3 EUR positions.
        currency_exposure = self._check_currency_exposure(signal)
        if currency_exposure:
            return {
                "allowed": False,
                "reason": f"Currency exposure cap exceeded: {currency_exposure}",
            }

        # Check minimum R:R
        if signal.risk_reward_ratio < settings.min_reward_ratio:
            return {
                "allowed": False,
                "reason": f"R:R too low ({signal.risk_reward_ratio} < {settings.min_reward_ratio})",
            }

        return result

    # ── Drawdown Protection ─────────────────────────────────────────

    def _check_daily_drawdown(self) -> bool:
        """Check if daily drawdown limit has been reached.

        Track (d): on breach, sets ``self._daily_dd_tripped=True`` so the
        orchestrator's trading loop can call ``close_all_trades``. The
        trip state is cleared automatically on the first cycle of a new
        UTC day (alongside the ``_daily_start_balance`` reset).
        """
        today = datetime.now().date()

        # Reset daily balance tracking + clear tripped state on a new day
        if self._last_reset_date != today:
            account = self._mt5_call(mt5.account_info)
            if account:
                self._daily_start_balance = account.balance
                self._last_reset_date = today
                if self._daily_dd_tripped:
                    logger.info(
                        "🌅 New trading day — daily-DD kill switch released (was tripped)"
                    )
                    self._daily_dd_tripped = False

        if self._daily_start_balance == 0:
            return True

        account = self._mt5_call(mt5.account_info)
        if account is None:
            return False  # Can't verify — block trading

        current_equity = account.equity
        drawdown = (self._daily_start_balance - current_equity) / self._daily_start_balance

        if drawdown >= settings.max_daily_drawdown:
            if not self._daily_dd_tripped:
                logger.critical(
                    f"🚨 DAILY DRAWDOWN LIMIT HIT: {drawdown*100:.1f}% "
                    f"(limit: {settings.max_daily_drawdown*100}%); kill switch TRIPPED"
                )
            self._daily_dd_tripped = True
            return False

        return True

    def _check_equity_floor(self) -> bool:
        """Track peak equity. If unrealised drawdown vs. peak exceeds the
        ``equity_floor_kill_switch_pct`` setting, trip the equity-floor
        kill switch.

        Track (d): there is NO automatic reset — once tripped, the switch
        requires manual release (via dashboard or Discord ``!kill_switch
        release``) because a peak-equity recovery may not coincide with
        a UTC day rollover. Re-baselining the peak on release prevents
        the same drift from re-tripping immediately.
        """
        threshold = settings.equity_floor_kill_switch_pct
        if threshold <= 0:
            return True  # setting disabled

        account = self._mt5_call(mt5.account_info)
        if account is None:
            # Track (d) consistency: previously this method returned True
            # silently on a missing account_info (fail-open) while
            # _check_daily_drawdown returns False on the same condition
            # (fail-closed). We now treat both as "cannot verify → block
            # new entries" so the safety invariants line up. Connectors
            # recover on the next cycle naturally.
            return False

        equity = account.equity
        if equity > self._peak_equity:
            self._peak_equity = equity

        if self._peak_equity <= 0:
            return True  # no peak yet, no floor to compare against

        drawdown = (self._peak_equity - equity) / self._peak_equity
        if drawdown >= threshold:
            if not self._equity_floor_tripped:
                logger.critical(
                    f"🚨 EQUITY FLOOR TRIPPED: peak ${self._peak_equity:.2f} → "
                    f"now ${equity:.2f}; drawdown {drawdown*100:.1f}% ≥ "
                    f"{threshold*100:.1f}%"
                )
            self._equity_floor_tripped = True
            return False
        return True

    # ── Kill-switch Accessors (Track d) ─────────────────────────────

    def is_daily_dd_tripped(self) -> bool:
        """True iff the daily-DD kill switch is currently engaged."""
        return self._daily_dd_tripped

    def is_equity_floor_tripped(self) -> bool:
        """True iff the equity-floor kill switch is currently engaged."""
        return self._equity_floor_tripped

    def tripped_kill_switches(self) -> dict:
        """Snapshot of both kill-switch states. Surfaced via Discord
        ``!kill_switch`` and the dashboard /api/risk endpoint."""
        return {
            "daily_drawdown": self._daily_dd_tripped,
            "equity_floor": self._equity_floor_tripped,
        }

    def release_daily_dd_trip(self) -> None:
        """Manually clear the daily-DD kill switch (idempotent)."""
        if self._daily_dd_tripped:
            logger.warning("🟢 Daily-DD kill switch released (manual)")
            self._daily_dd_tripped = False

    def release_equity_floor_trip(self) -> bool:
        """Manually clear the equity-floor kill switch.

        Returns True if a release happened (state changed), False if the
        switch was already clear. On release the peak is rebaselined to
        current equity so the next trip requires a fresh drawdown band.
        """
        if not self._equity_floor_tripped:
            return False
        logger.warning("🟢 Equity-floor kill switch released (manual)")
        self._equity_floor_tripped = False
        account = self._mt5_call(mt5.account_info)
        if account:
            self._peak_equity = account.equity
        return True

    # ── Losing Streak Detection ─────────────────────────────────────

    def record_trade_result(self, profit: float):
        """Record a trade result for streak tracking."""
        self._trade_log.append({
            "profit": profit,
            "time": datetime.now(),
            "is_win": profit > 0,
        })

        if profit < 0:
            self._consecutive_losses += 1
            logger.warning(
                f"📉 Consecutive losses: {self._consecutive_losses}/{settings.losing_streak_pause}"
            )

            if self._consecutive_losses >= settings.losing_streak_pause:
                self._activate_cooldown()
        else:
            self._consecutive_losses = 0

    def _activate_cooldown(self):
        """Activate the losing streak cooldown period."""
        cooldown_seconds = settings.losing_streak_cooldown
        self._cooldown_until = datetime.now() + timedelta(seconds=cooldown_seconds)
        self._consecutive_losses = 0

        logger.critical(
            f"🛑 LOSING STREAK COOLDOWN ACTIVATED — "
            f"No trading for {cooldown_seconds // 60} minutes"
        )

    # ── Position Helpers (Track d) ─────────────────────────────────

    def _all_positions(self) -> list:
        """All MT5 positions on the account, or empty list — never None."""
        positions = self._mt5_call(mt5.positions_get)
        return list(positions) if positions else []

    def _our_positions(self) -> list:
        """All MT5 positions filtered to ``settings.magic_number``.

        Track (d): magic-number isolation. Foreign / manual positions in
        the same account must NOT count toward our ``max_open_positions``,
        must NOT trigger our correlation filter, and must NOT be flagged
        as our duplicates. Returns a list (possibly empty), never None.
        """
        return [p for p in self._all_positions() if p.magic == settings.magic_number]

    # ── Correlation Filter ──────────────────────────────────────────

    # Correlated pair groups — same direction trades on these would double risk.
    # Track (d): same-group opposite-direction trades also count, because they
    # net each other out (buy EURUSD + sell GBPUSD in same group is a wash).
    CORRELATION_GROUPS = [
        {"EURUSD", "GBPUSD", "AUDUSD", "NZDUSD"},     # USD weakness group
        {"USDCHF", "USDCAD", "USDJPY"},                # USD strength group
        {"EURGBP", "EURJPY", "EURAUD"},                 # EUR group
        {"GBPJPY", "GBPAUD", "GBPCAD"},                 # GBP group
    ]

    def _check_correlation(self, signal: TradeSignal) -> Optional[str]:
        """
        Check if a new trade would overlap with correlated open positions.

        Track (d): now magic-filtered via ``self._our_positions()`` AND
        treats same-group opposite-direction trades as correlated (they
        net the view out).

        Returns:
            Symbol of the correlated open position, or None if safe
        """
        our_positions = self._our_positions()
        if not our_positions:
            return None

        signal_group = None
        for group in self.CORRELATION_GROUPS:
            if signal.symbol in group:
                signal_group = group
                break

        if signal_group is None:
            return None  # Symbol not in any group — no correlation check needed

        for pos in our_positions:
            # Track (d): symbol in the group but not the signal's symbol —
            # the candidate pair. Direction is irrelevant (both same- and
            # opposite-direction are rejected; opposite nets the view).
            if pos.symbol not in signal_group or pos.symbol == signal.symbol:
                continue
            return pos.symbol

        return None

    def _has_duplicate_position(self, signal: TradeSignal) -> bool:
        """Check if we already have a position on this symbol in same direction.

        Track (d): now magic-filtered via ``self._our_positions()`` so a
        foreign / manual trade on the same symbol does not count.
        """
        for pos in self._our_positions():
            if pos.symbol != signal.symbol:
                continue
            pos_direction = "buy" if pos.type == mt5.ORDER_TYPE_BUY else "sell"
            if pos_direction == signal.direction.value:
                return True
        return False

    # ── Currency Exposure Check ─────────────────────────────────────
    # Prevent over-exposure to any single currency (base or quote).

    def _check_currency_exposure(self, signal: TradeSignal) -> Optional[str]:
        """
        Check if adding a new position would exceed the currency exposure cap.

        Day Trader profile limits simultaneous positions to max 2 containing
        the same base OR quote currency. This prevents correlated risk
        multiplication (e.g., EURUSD, EURGBP, EURJPY = 3 EUR positions).

        Track (d): Implemented as a simple count check against open positions
        filtered by magic number.

        Returns:
            Symbol name of the conflicting position if exposure would be exceeded,
            None if safe to proceed.
        """
        our_positions = self._our_positions()
        if not our_positions:
            return None

        # Parse target symbol
        target_symbol = signal.symbol
        if len(target_symbol) < 6:
            return None

        # Extract base and quote currency
        target_base = target_symbol[:3]
        target_quote = target_symbol[-3:]

        # Count current exposure to base and quote currencies
        base_count = 0
        quote_count = 0

        for pos in our_positions:
            pos_symbol = pos.symbol if hasattr(pos, 'symbol') else pos.get('symbol', '')
            if len(pos_symbol) < 6:
                continue

            pos_base = pos_symbol[:3]
            pos_quote = pos_symbol[-3:]

            if pos_base == target_base:
                base_count += 1
            if pos_quote == target_quote:
                quote_count += 1

        # Check if adding this position would exceed the cap of 2
        # (Day Trader profile sets MAX_OPEN_POSITIONS=3 but this is different)
        # We need an additional setting for currency exposure cap
        from bot.config.settings import settings
        currency_cap = getattr(settings, 'currency_exposure_cap', 2)

        # If adding this trade, base currency would be at cap+1?
        if target_base in [our_positions[i].symbol[:3] for i in range(len(our_positions)) if hasattr(our_positions[i], 'symbol')]:
            # Signal's base currency is already represented
            pass

        # Check both directions
        if base_count >= currency_cap and target_base not in [p.symbol[:3] for p in our_positions if hasattr(p, 'symbol') and p.symbol != target_symbol]:
            # We're adding a NEW position with this base currency
            pass

        # Simplified check: count positions sharing base or quote with the signal
        shared_currency_count = 0
        for pos in our_positions:
            pos_symbol = pos.symbol if hasattr(pos, 'symbol') else pos.get('symbol', '')
            if len(pos_symbol) >= 6:
                pos_base = pos_symbol[:3]
                pos_quote = pos_symbol[-3:]
                if target_base in (pos_base, pos_quote) or target_quote in (pos_base, pos_quote):
                    shared_currency_count += 1

        # If this signal adds yet another position sharing currency, check cap
        # But we need to be smarter - only count positions that would share currency
        # after adding this one

        # For now, a simpler approach: check if we have >= cap positions sharing any currency
        # with the proposed signal
        from collections import Counter
        currency_counts = Counter()

        for pos in our_positions:
            pos_symbol = pos.symbol if hasattr(pos, 'symbol') else pos.get('symbol', '')
            if len(pos_symbol) >= 6:
                currency_counts[pos_symbol[:3]] += 1
                currency_counts[pos_symbol[-3:]] += 1

        # Now check what this signal would bring
        currency_counts[target_base] += 1
        currency_counts[target_quote] += 1

        # Check if any currency exceeds the cap
        for currency, count in currency_counts.items():
            if count > currency_cap:
                return f"Currency {currency} would exceed cap ({count}/{currency_cap})"

        return None

    # ── Stats ───────────────────────────────────────────────────────

    async def get_risk_stats(self) -> dict:
        """Async wrapper around the sync stats collector."""
        return await asyncio.to_thread(self._get_risk_stats_sync)

    def _get_risk_stats_sync(self) -> dict:
        """Get current risk management statistics.

        Track (d): enriched with kill-switch states, peak equity, magic-
        filtered open-position count, and equity-floor drawdown. The
        ``kill_switches`` key lets the dashboard / Discord render both
        switches in one block via ``!kill_switch`` or /api/risk.
        """
        account = self._mt5_call(mt5.account_info)
        our_positions = self._our_positions()
        daily_dd = 0.0
        equity_floor_dd = 0.0
        if account and self._daily_start_balance > 0:
            daily_dd = (self._daily_start_balance - account.equity) / self._daily_start_balance
        if account and self._peak_equity > 0:
            equity_floor_dd = (self._peak_equity - account.equity) / self._peak_equity

        return {
            "consecutive_losses": self._consecutive_losses,
            "cooldown_active": self._cooldown_until is not None and datetime.now() < self._cooldown_until,
            "cooldown_until": str(self._cooldown_until) if self._cooldown_until else None,
            "cooldown_remaining_seconds": (
                max(0.0, (self._cooldown_until - datetime.now()).total_seconds())
                if self._cooldown_until is not None
                and datetime.now() < self._cooldown_until
                else 0.0
            ),
            "daily_drawdown_pct": round(daily_dd * 100, 2),
            "daily_drawdown_limit": settings.max_daily_drawdown * 100,
            "equity_floor_pct": round(equity_floor_dd * 100, 2),
            "peak_equity": self._peak_equity,
            "kill_switches": self.tripped_kill_switches(),
            "open_positions": len(our_positions),
            "max_positions": settings.max_open_positions,
            "recent_trades": len(self._trade_log),
        }
