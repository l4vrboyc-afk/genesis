"""
Trading Orchestrator — The central heartbeat of the Genesis bot.
Coordinates MT5 connectivity, fetches market data, evaluates signals,
manages risk parameters, updates the database, and schedules loop tasks.
"""

import asyncio
import itertools
import queue
import threading
from datetime import datetime, timedelta
import MetaTrader5 as mt5
from loguru import logger
from typing import List

from bot.config.settings import settings, TradeDirection
from bot.core.mt5_connector import MT5Connector
from bot.core.data_fetcher import DataFetcher
from bot.core.order_manager import OrderManager
from bot.risk.risk_manager import RiskManager
from bot.risk.news_filter import NewsFilter
from bot.risk.performance_tracker import PerformanceTracker
from bot.strategies.strategy_selector import StrategySelector
from bot.notifications.notification_manager import notification_manager
from database.db_manager import DatabaseManager
from bot.core.data_logger import start_tick_logger, enqueue_tick


# ── Module-level helpers (used by AsyncMt5Executor callers) ─────────────


def _raw_positions_to_dicts(positions_raw):
    """Convert raw MT5 positions to the dict format used throughout the bot."""
    if not positions_raw:
        return []
    return [
        {
            "ticket": pos.ticket,
            "symbol": pos.symbol,
            "direction": "buy" if pos.type == mt5.ORDER_TYPE_BUY else "sell",
            "volume": pos.volume,
            "open_price": pos.price_open,
            "current_price": pos.price_current,
            "entry_price": pos.price_open,
            "live_price": pos.price_current,
            "sl": pos.sl,
            "tp": pos.tp,
            "profit": pos.profit,
            "swap": pos.swap,
            "magic": pos.magic,
            "comment": pos.comment,
            "time": datetime.fromtimestamp(pos.time),
        }
        for pos in positions_raw
    ]


class ThreadMT5Lock:
    """Serializes blocking MT5 calls across asyncio tasks.

    Uses a ``threading.Lock`` acquired INSIDE the ``to_thread`` worker so
    that the ``asyncio.Lock`` (which is released during ``await``) does
    not create a race window between worker threads.
    """

    def __init__(self):
        self._lock = threading.Lock()

    async def run(self, func, *args, **kwargs):
        """Offload *func* to a worker thread and run it under the serialization lock."""
        return await asyncio.to_thread(self._run_sync, func, *args, **kwargs)

    def _run_sync(self, func, *args, **kwargs):
        with self._lock:
            return func(*args, **kwargs)


class AsyncMt5Executor:
    """
    Non-blocking MT5 dispatcher backed by a single-threaded executor.
    """

    def __init__(self):
        self._in_queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._running = False
        self._counter = itertools.count()
        self._pending = {}
        self._loop = None

    async def start(self) -> None:
        """Start the background worker thread."""
        if self._running:
            return
        self._loop = asyncio.get_running_loop()
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="Mt5Executor")
        self._thread.start()
        logger.debug("Mt5Executor thread started")

    def _run(self) -> None:
        mt5_lock = threading.Lock()
        while self._running:
            try:
                item = self._in_queue.get(timeout=0.05)
            except queue.Empty:
                continue

            if item is None:
                self._in_queue.task_done()
                break

            call_id, func, args, kwargs = item
            try:
                with mt5_lock:
                    result = func(*args, **kwargs)
                if self._loop and not self._loop.is_closed():
                    self._loop.call_soon_threadsafe(self._resolve_future, call_id, result, None)
            except Exception as e:
                if self._loop and not self._loop.is_closed():
                    self._loop.call_soon_threadsafe(self._resolve_future, call_id, None, e)
            finally:
                try:
                    self._in_queue.task_done()
                except ValueError:
                    pass

    def _resolve_future(self, call_id, result, error):
        if call_id in self._pending:
            future = self._pending.pop(call_id)
            if not future.done():
                if error:
                    future.set_exception(error)
                else:
                    future.set_result(result)

    async def submit(self, func, *args, **kwargs):
        call_id = next(self._counter)
        if not self._loop:
            self._loop = asyncio.get_running_loop()
        future = self._loop.create_future()
        self._pending[call_id] = future
        self._in_queue.put((call_id, func, args, kwargs))
        return await future

    async def stop(self) -> None:
        """Graceful shutdown: stop the worker thread and drain queues."""
        if not self._running:
            return
        self._running = False
        self._in_queue.put(None)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                logger.warning("[WARN] Mt5Executor thread did not stop within 5 s")
        logger.debug("Mt5Executor stopped")


class TradingOrchestrator:
    """Coordinates and executes the automated trading system."""

    def __init__(self):
        self.db = DatabaseManager()
        self.mt5_conn = MT5Connector()
        self.fetcher = DataFetcher(self.mt5_conn)
        self.order_manager = OrderManager(self.mt5_conn)
        self.risk_manager = RiskManager(self.mt5_conn)
        self.news_filter = NewsFilter()
        self.performance_tracker = PerformanceTracker()
        self.strategy_selector = StrategySelector()

        # Single-threaded executor serialises all MT5 calls behind a simple
        # async queue — no asyncio.Lock contention, no event-loop starvation.
        # All MT5 calls from this point forward should use
        # ``await self._mt5.submit(mt5.fn, ...); never ``asyncio.to_thread``.
        self._mt5 = AsyncMt5Executor()

        # Only used for close_all_trades to prevent the emergency path from
        # racing a live _execute_trading_cycle mid-position-close.
        self._emergency_lock = asyncio.Lock()

        self._paused = True
        self._running = False
        self._loop_task = None
        self._last_run_time = None

        # Track (d) — Connection state tracking + kill-switch engagement.
        # ``_was_connected`` enables edge-detection of mid-cycle disconnects
        # (so Discord gets a single notification per transition, not every
        # cycle's reconnect attempt). ``_kill_switch_fired`` is set when
        # the orchestrator engages an emergency flatten on DD / equity-floor
        # breach; it stays set until the underlying flag is released
        # (manual dashboard/Discord ``!kill_switch release``), so the
        # next cycle does NOT re-flatten an already-flat portfolio.
        self._was_connected: bool = False
        self._kill_switch_fired: bool = False

    async def start(self):
        """Initialize connections and start the background trading loop."""
        if self._running:
            return

        logger.info("🚀 Starting Genesis Trading Orchestrator...")

        # 0. Start the MT5 executor thread before any MT5 calls
        await self._mt5.start()

        # 0b. Inject the executor into every submodule that may touch MT5 so
        # their ``async`` public methods route through it. Without this,
        # connector / fetcher / order_manager calls would land on the
        # default executor pool and break lock discipline.
        self.mt5_conn.attach_executor(self._mt5)
        self.fetcher.attach_executor(self._mt5)
        self.order_manager.attach_executor(self._mt5)

        # 1. Initialize DB
        await self.db.init_db()
        await start_tick_logger()

        # 1b. Hydrate in-memory state from persistent storage so win-rate /
        # drawdown / daily-pnl reflect full history on the very first cycle
        # instead of starting at zero after every restart.
        try:
            closed_trades = await self.db.get_trades(status="closed", limit=500)
            self.performance_tracker.hydrate_from_records([t.to_dict() for t in closed_trades])
            daily_history = await self.db.get_daily_performance_history(limit=90)
            self.performance_tracker.hydrate_daily_performance([d.to_dict() for d in daily_history])
        except Exception as e:
            # Hydration is best-effort — a corrupted historical row must NOT
            # prevent the bot from starting.
            logger.warning(f"[WARN] PerformanceTracker hydration skipped: {e}")

        await self._load_persisted_state()

        # 2. Connect to MetaTrader 5 (async — runs on the executor's worker thread)
        if not await self.mt5_conn.connect():
            logger.error("[ERR] Failed to connect to MetaTrader 5 during startup")
            # We don't raise error, let it run and attempt reconnection in background

        # 3. Synchronize open positions from MT5 database
        await self._sync_open_positions()

        # 4. Enable symbols (async — scheduled through the executor)
        await self.mt5_conn.enable_all_pairs()

        # 5. Start main loop
        self._running = True
        self._paused = False
        self._loop_task = asyncio.create_task(self._main_loop())
        logger.success("[GREEN] Genesis Trading Orchestrator is running")

    async def stop(self):
        """Stop trading loop and disconnect from MT5.

        Tracks the disconnect call so even if ``_mt5.stop()`` raises,
        ``mt5.shutdown()`` still runs. ``_mt5.stop()`` is called LAST
        so the connector's ``disconnect`` can still use the executor
        on its way out (and the no-executor fallback handles the case
        where the executor had already died).
        """
        logger.info("🛑 Stopping Genesis Trading Orchestrator...")
        self._running = False

        if self._loop_task:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass

        try:
            await self.mt5_conn.disconnect()
        except Exception as e:
            logger.error(f"[ERR] MT5 disconnect failed (will still stop executor): {e}")

        try:
            await self._mt5.stop()
        except Exception as e:
            logger.warning(f"[WARN] MT5 executor stop failed: {e}")

        logger.success("🏁 Trading Orchestrator stopped gracefully")

    def pause(self):
        """Temporarily pause execution of new trades."""
        self._paused = True
        logger.warning("⏸️ Trading loop PAUSED")

    def resume(self):
        """Resume execution of new trades."""
        self._paused = False
        logger.success("▶️ Trading loop RESUMED")

    def release_forced_regime(self) -> None:
        """Reset a user-forced regime so the bot resumes auto-classification."""
        self.strategy_selector.release_forced_regime()
        logger.info("🔄 Forced regime released — auto-detection resumed")

    @property
    def is_paused(self) -> bool:
        return self._paused

    async def get_status(self) -> dict:
        """Get status summary for Discord or Frontend.

        All three MT5 reads are dispatched through the AsyncMt5Executor so
        they run concurrently on the worker thread. The executor internally
        serialises them, but since they are independent reads they complete
        in roughly one broker-round-trip latency (not three).  The event loop
        stays free the entire time — the HTTP dashboard is never starved.
        """
        account_info, mt5_connected, open_positions = await asyncio.gather(
            self._mt5.submit(mt5.account_info),
            self.mt5_conn.is_connected(),
            self._mt5.submit(lambda: len([p for p in (mt5.positions_get() or []) if p.magic == settings.magic_number])),
        )

        pnl_summary = self.performance_tracker.get_summary()
        regime = self.strategy_selector.current_regime

        return {
            "bot_name": settings.bot_name,
            "paused": self._paused,
            "mt5_connected": mt5_connected,
            "account_login": getattr(account_info, "login", 0) if account_info else 0,
            "balance": getattr(account_info, "balance", settings.starting_capital) if account_info else settings.starting_capital,
            "equity": getattr(account_info, "equity", settings.starting_capital) if account_info else settings.starting_capital,
            "daily_pnl": pnl_summary.get("rolling_pnl", 0.0),
            "win_rate": pnl_summary.get("win_rate", 0.0),
            "open_positions": open_positions,
            "regime": regime.value if regime else "unknown",
            "paper_trading": settings.paper_trading,
        }

    async def get_live_open_trades(self) -> list[dict]:
        """Fetch live open positions from MT5 with real-time prices.

        Returns the same dict shape as ``_raw_positions_to_dicts`` so the
        dashboard modal can read ``live_price`` and ``profit`` directly.
        Falls back to the database when MT5 is unreachable.
        """
        try:
            positions_raw = await self._mt5.submit(
                mt5.positions_get, magic=settings.magic_number
            )
            if positions_raw:
                return _raw_positions_to_dicts(positions_raw)
            # MT5 returned nothing — might be zero open positions
            return []
        except Exception as e:
            logger.warning(f"⚠ Could not fetch live positions from MT5: {e}")
            # Fallback: serve from DB (no live_price, but dashboard won't crash)
            try:
                db_trades = await self.db.get_open_trades()
                return [t.to_dict() for t in db_trades]
            except Exception:
                return []

    # ── Orchestrator Core Loops ─────────────────────────────────────

    async def _main_loop(self):
        """Asynchronous execution loop running every N seconds."""
        loop_interval = 15  # seconds
        logger.info(f"[LOOP] Main loop started. Interval: {loop_interval}s")

        while self._running:
            start_time = datetime.now()
            self._last_run_time = start_time

            try:
                # 1. Health check & reconnect — Track (d) wires transition
                #    notifications + post-reconnect reconciliation here.
                ok = await self._handle_connection_state()
                if not ok:
                    # Cycle 4 onwards: paused via _handle_connection_state
                    # (give-up case) — sleep and try again next cycle.
                    self._paused = True
                    elapsed = (datetime.now() - start_time).total_seconds()
                    await asyncio.sleep(max(0.1, loop_interval - elapsed))
                    continue

                # 2. Check for closed trades (SL/TP hits) in MT5
                await self._check_closed_positions()

                if not self._paused:
                    # 3. Scan for new entries & update trailing stops
                    await self._execute_trading_cycle()

                # 4. Log daily stats
                await self._record_daily_stats()

            except BaseException as e:
                # Capture full traceback to stderr immediately
                import sys
                import traceback
                print(f"\n{'='*60}", file=sys.stderr)
                print(f"[ORCHESTRATOR CRASH] {type(e).__name__}: {e}", file=sys.stderr)
                traceback.print_exc(file=sys.stderr)
                print(f"{'='*60}\n", file=sys.stderr)

                # Also try logger
                try:
                    logger.error(f"[ERR] Error in main loop cycle: {e}")
                except Exception:
                    pass

            # Sleep remaining time
            elapsed = (datetime.now() - start_time).total_seconds()
            sleep_time = max(0.1, loop_interval - elapsed)
            await asyncio.sleep(sleep_time)

    async def _handle_connection_state(self) -> bool:
        """Track connection state across cycles (Track d).

        Behaviour:
            - On the first cycle after a connected → disconnected edge,
              fire ONE warning notification; don't notify every cycle.
            - On reconnect success, run ``_reconcile_after_reconnect``
              so DB stays in sync with MT5 after we were offline.
            - On connector give-up (max retries exhausted), fire ONE
              critical notification; pause subsequent cycles until the
              operator notices.

        Returns True if the bot is currently connected (i.e. safe to
        write trades this cycle), False otherwise.
        """
        now_connected = await self.mt5_conn.is_connected()

        # Edge transition: connected → disconnected
        if not now_connected and self._was_connected:
            logger.warning("📡 MT5 connection lost — entering reconnect loop")
            try:
                await notification_manager.notify_alert(
                    "[WARN] MT5 connection lost — orchestrator attempting reconnect",
                    "warning",
                )
            except Exception as e:  # pragma: no cover — best-effort
                logger.debug(f"notify_alert failed (transition warn): {e}")

        self._was_connected = now_connected

        if now_connected:
            return True

        # Not connected — attempt reconnect. The connector itself has
        # exponential backoff and a max-retry ceiling (returns False on
        # give-up).
        logger.warning("[WARN] MT5 disconnected. Reconnecting...")
        ok = await self.mt5_conn.reconnect()

        if ok:
            logger.success("[GREEN] MT5 reconnected — running post-reconnect reconciliation")
            try:
                await notification_manager.notify_alert(
                    "[GREEN] MT5 reconnected — running post-reconnect reconciliation",
                    "system",
                )
            except Exception as e:  # pragma: no cover — best-effort
                logger.debug(f"notify_alert failed (reconnect ok): {e}")
            await self._reconcile_after_reconnect()
            self._was_connected = True
            return True

        # Give-up path: notify once, then pause. The next cycle's
        # _handle_connection_state() call will retry — so even after
        # give-up the bot returns to trading once the operator brings
        # MT5 back, without explicit user intervention.
        logger.critical(
            "💀 MT5 reconnection FAILED (max retries exhausted) — "
            "halting writes until the terminal is reachable again"
        )
        try:
            await notification_manager.notify_alert(
                "💀 MT5 reconnection FAILED (max retries) — bot halted until terminal is reachable",
                "critical",
            )
        except Exception as e:  # pragma: no cover — best-effort
            logger.debug(f"notify_alert failed (give-up): {e}")
        return False

    async def _reconcile_after_reconnect(self) -> None:
        """Track (d): post-reconnect reconciliation.

        Best-effort re-snapshot of MT5 open positions vs. the database.
        Best executed via the same path as startup ``_sync_open_positions``
        — that method already covers: «MT5-closed, DB-closed» updates and
        «MT5-open, DB-missing» re-logging. Errors here are warned, never
        raised — a partial reconciliation is better than a crashed loop.
        """
        try:
            active_raw = await self._mt5.submit(
                mt5.positions_get, magic=settings.magic_number
            )
            active_count = len(active_raw) if active_raw else 0
            await self._sync_open_positions()
            logger.info("[OK] Post-reconnect reconciliation completed")
            try:
                await notification_manager.notify_alert(
                    f"🔄 Post-reconnect reconciliation completed "
                    f"— {active_count} Genesis position(s) open",
                    "system",
                )
            except Exception as e:  # pragma: no cover
                logger.debug(f"notify_alert failed (reconcile): {e}")
        except Exception as e:
            logger.warning(f"[WARN] Post-reconnect reconciliation failed: {e}")

    async def _fire_kill_switch(self, reason: str) -> None:
        """Engage an emergency flatten (Track d).

        Called from ``_execute_trading_cycle`` when daily-DD or
        equity-floor trips mid-cycle. We deliberately do NOT auto-resume
        when the underlying flag is later released — operators should
        re-evaluate and ``!resume`` manually after a close-call. This
        also means the bot does not re-engage the kill switch every
        cycle: ``_kill_switch_fired`` sticky until the operator
        releases the switch AND clears the engagement flag.
        """
        if self._kill_switch_fired:
            return  # already in kill-switch window; do not re-flatten

        self._kill_switch_fired = True
        self._paused = True
        logger.critical(f"[ALERT] Kill switch engaged: {reason}")

        try:
            await notification_manager.notify_alert(
                f"[ALERT] Kill switch engaged: {reason}",
                "critical",
            )
        except Exception as e:  # pragma: no cover
            logger.debug(f"notify_alert failed (kill engage): {e}")

        try:
            closed = await self.close_all_trades()
            logger.critical(
                f"🛑 Kill switch closed {len(closed)} Genesis position(s)"
            )
            try:
                await notification_manager.notify_alert(
                    f"🛑 Kill switch closed {len(closed)} Genesis position(s)",
                    "critical",
                )
            except Exception as e:  # pragma: no cover
                logger.debug(f"notify_alert failed (kill closed notify): {e}")
        except Exception as e:
            logger.error(f"[ERR] Kill-switch emergency close failed: {e}")
            try:
                await notification_manager.notify_alert(
                    f"[ERR] Kill switch flat FAILED: {e} — manual intervention required",
                    "critical",
                )
            except Exception:
                pass

    def release_kill_switch_engagement(self) -> bool:
        """Clear the orchestrator's kill-switch latching flag.

        Called by ``release_forced_regime``-style paths (Dashboard /
        Discord ``!kill_switch release``) AFTER the underlying risk flag
        has been cleared via ``risk_manager.release_*_trip()``. Paused
        state is left untouched — operators manually ``!resume`` after
        re-checking conditions.
        """
        if not self._kill_switch_fired:
            return False
        logger.warning("[GREEN] Kill-switch engagement latch cleared (manual release)")
        self._kill_switch_fired = False
        return True

    async def _execute_trading_cycle(self):
        """Executes a single scanning and position management cycle."""
        
        if self.performance_tracker.should_pause():
            logger.critical("🛑 Auto-pause: performance thresholds breached")
            self.pause()
            return

        # ── Phase 1: Snapshot open positions via the serialised executor
        #   — no asyncio.Lock needed; the executor serialises all MT5 calls.
        positions_raw = await self._mt5.submit(mt5.positions_get, magic=settings.magic_number)
        positions = _raw_positions_to_dicts(positions_raw)

        # ── Phase 2: Compute trailing-stop levels (no lock, pure Python)
        #   Each iteration off-loads mt5 reads to the worker thread pool;
        #   the asyncio event loop stays free for dashboard / Discord.
        trail_jobs = []  # list of (ticket, symbol, distance)
        for pos in positions:
            atr_data = await self.fetcher.get_analyzed_data(
                pos["symbol"], settings.entry_timeframe, 50
            )
            if atr_data is None or atr_data.empty:
                continue
            atr_col = f"atr_{settings.atr_period}"
            latest_atr = atr_data[atr_col].iloc[-1]
            distance = latest_atr * settings.atr_sl_multiplier
            trail_jobs.append((pos["ticket"], pos["symbol"], distance))

        # ── Phase 3: Apply trailing stop SLs via the serialised executor.
        #   The executor holds a threading.Lock on its own worker thread,
        #   so there is no asyncio.Lock contention at all.  The ~1 ms
        #   mt5.order_send is serialised naturally; the event loop is never
        #   starved waiting for it.
        for ticket, symbol, distance in trail_jobs:
            position = await self._mt5.submit(mt5.positions_get, ticket=ticket)
            if not position:
                continue
            pos = position[0]
            tick = await self._mt5.submit(mt5.symbol_info_tick, pos.symbol)
            if not tick:
                continue

            new_sl = None
            if pos.type == mt5.ORDER_TYPE_BUY:
                potential = tick.bid - distance
                if potential > pos.sl and potential > pos.price_open:
                    new_sl = potential
            elif pos.type == mt5.ORDER_TYPE_SELL:
                potential = tick.ask + distance
                if pos.sl == 0 or (potential < pos.sl and potential < pos.price_open):
                    new_sl = potential

            if new_sl is None:
                continue

            # This call serialises with all other MT5 work inside the executor.
            await self.order_manager._apply_trailing_stop(ticket, new_sl, symbol)

        # Scan for new signals (outside the lock — fetching data is read-only,
        # ordering is its own critical section below)
        if (
            self.news_filter._last_refresh is None
            or (datetime.now() - self.news_filter._last_refresh).seconds > settings.news_calendar_refresh_interval
        ):
            await self.news_filter.refresh_calendar_async()

        for pair in settings.trading_pairs:
            # Check news filter
            news_check = self.news_filter.can_trade(pair)
            if not news_check["allowed"]:
                logger.debug(f"📰 News Filter blocks {pair}: {news_check['reason']}")
                continue

            # ── Fetch market data through the executor ──
            # Each fetcher method is async and self-routes through the bound
            # ``AsyncMt5Executor``, so no ``asyncio.to_thread`` is needed.
            # ``asyncio.gather`` still wavefronts the three reads onto the
            # executor's queue, and they execute in arrival order on the
            # serialised worker thread.
            htf_data, etf_data, current_price = await asyncio.gather(
                self.fetcher.get_analyzed_data(pair, settings.higher_timeframe, 300),
                self.fetcher.get_analyzed_data(pair, settings.entry_timeframe, 100),
                self.fetcher.get_current_price(pair),
            )

            await enqueue_tick({
                "symbol": pair,
                "timestamp": int(current_price["time"].timestamp()),
                "bid": current_price["bid"],
                "ask": current_price["ask"],
                "volume": current_price.get("volume", 0),
            })

            if htf_data is None or etf_data is None or current_price is None:
                logger.warning(f"[WARN] {pair}: No data returned (HTF={htf_data is not None}, ETF={etf_data is not None}, price={current_price is not None})")
                continue

            # Check strategy signal
            regime = self.strategy_selector.current_regime
            signal = self.strategy_selector.get_signal(
                pair, htf_data, etf_data, current_price
            )

            if not signal or signal.direction == TradeDirection.HOLD:
                # Log WHY no signal fired — helps diagnose why bot isn't trading
                latest = htf_data.iloc[-1]
                adx = latest.get("adx", 0)
                atr_ratio = etf_data["atr_ratio"].iloc[-1]

                # Also log entry TF RSI for scalper (M1)
                entry_rsi = etf_data[f"rsi_{settings.rsi_period}"].iloc[-1]
                entry_volume = etf_data["volume"].iloc[-1] if "volume" in etf_data.columns else 0
                entry_vol_avg = etf_data["volume_avg_20"].iloc[-1] if "volume_avg_20" in etf_data.columns else 0
                vol_ratio = entry_volume / entry_vol_avg if entry_vol_avg > 0 else 0

                logger.info(
                    f"[SIGNAL] {pair} — regime={regime.value if regime else 'unknown'} | "
                    f"HTF_ADX={adx:.1f} HTF_ATRratio={atr_ratio:.2f} | "
                    f"ETF_RSI={entry_rsi:.1f} ETF_vol={vol_ratio:.1f}×avg | "
                    f"No signal this cycle"
                )
                continue

            # ── Trade execution critical section ─────────────────────────
            # Only `_emergency_lock` prevents `close_all_trades` from racing
            # mid-trade. All MT5 calls go through AsyncMt5Executor which
            # serialises them on its own worker thread — no other lock needed.
            async with self._emergency_lock:
                # Track (d): pull kill-switch state up front so we can
                # distinguish "tripped" from other disallowed reasons.
                # Risk checks are otherwise unchanged from before.
                risk_check = await self.risk_manager.can_trade(signal)
                if not risk_check["allowed"]:
                    # Kill-switch trigger — engages only on the cycle where
                    # the trip is FIRST observed, not every subsequent
                    # cycle while paused. Operators manually ``!release`` /
                    # ``!resume`` after triage.
                    if (
                        not self._kill_switch_fired
                        and (
                            self.risk_manager.is_daily_dd_tripped()
                            or self.risk_manager.is_equity_floor_tripped()
                        )
                    ):
                        await self._fire_kill_switch(risk_check["reason"])
                    logger.debug(
                        f"🛡️ Risk blocks signal on {pair}: {risk_check['reason']}"
                    )
                    continue

                atr_ratio = etf_data["atr_ratio"].iloc[-1]
                # Use executor for fast, non-blocking account info
                account_info = await self._mt5.submit(mt5.account_info)
                balance = account_info.balance if account_info else settings.starting_capital

                lots = await self.risk_manager.calculate_position_size(signal, balance, atr_ratio)
                if lots <= 0:
                    continue

                # Place trade — goes through executor, serialised with everything
                trade_result = await self.order_manager.place_market_order(
                    symbol=signal.symbol,
                    direction=signal.direction,
                    volume=lots,
                    sl=signal.stop_loss,
                    tp=signal.take_profit,
                    comment=f"{signal.strategy_name} entry",
                    magic=settings.magic_number,
                )

            if not trade_result:
                continue

            # Outside the lock: persist + notify (DB / Discord latency shouldn't
            # block the next pair's evaluation)
            await self.db.record_trade_open(
                ticket=trade_result["ticket"],
                symbol=signal.symbol,
                direction=signal.direction.value,
                volume=lots,
                entry_price=trade_result["price"],
                sl=signal.stop_loss,
                tp=signal.take_profit,
                strategy=signal.strategy_name,
                regime=self.strategy_selector.current_regime.value,
                comment=trade_result["comment"],
            )

            await notification_manager.notify_trade_open({
                "ticket": trade_result["ticket"],
                "symbol": signal.symbol,
                "direction": signal.direction.value,
                "volume": lots,
                "price": trade_result["price"],
                "sl": signal.stop_loss,
                "tp": signal.take_profit,
                "strategy": signal.strategy_name,
                "reason": signal.reason,
            })


    async def _check_closed_positions(self):
        """Compares open positions in MT5 against open positions in DB to log closes.

        The executor serialises all MT5 reads and writes, so the MT5 snapshot
        is always taken in a consistent state relative to any concurrent
        trading or close operations.

        The DB read happens before the MT5 read — if a trade closes between
        them the executor ensures the close is queued and visible to the MT5
        snapshot (since close goes through the same serialised executor path).
        """
        db_open_trades = await self.db.get_open_trades()
        if not db_open_trades:
            return

        closed_trades = []  # accumulate for Discord notify below

        # Snapshot MT5 state via the serialised executor so it can't race
        # a concurrent position close that goes through the executor too.
        active_raw = await self._mt5.submit(mt5.positions_get, magic=settings.magic_number)
        active_positions = _raw_positions_to_dicts(active_raw)
        active_tickets = {p["ticket"] for p in active_positions}

        for trade in db_open_trades:
            if trade.ticket in active_tickets:
                continue

            logger.info(f"🔔 Detected closed position: Ticket {trade.ticket}")

            # Fetch MT5 history deals for this ticket
            history_from = datetime.now() - timedelta(days=2)
            history_to = datetime.now() + timedelta(days=1)

            deals = await self._mt5.submit(
                mt5.history_deals_get,
                int(history_from.timestamp()),
                int(history_to.timestamp()),
                position=trade.ticket
            )

            exit_price = trade.entry_price
            profit = 0.0
            swap = 0.0
            close_comment = "Closed"

            if deals:
                for deal in deals:
                    if deal.entry == mt5.DEAL_ENTRY_OUT:
                        exit_price = deal.price
                        profit += deal.profit
                        swap += deal.swap
                        close_comment = deal.comment or "Closed in MT5"

            # Update Database
            updated_trade = await self.db.record_trade_close(
                ticket=trade.ticket,
                exit_price=exit_price,
                profit=profit,
                swap=swap,
                comment=close_comment
            )

            if updated_trade:
                # Update local PerformanceTracker
                trade_dict = updated_trade.to_dict()
                trade_dict["is_win"] = profit > 0
                self.performance_tracker.record_trade(trade_dict)

                # Update RiskManager streak
                self.risk_manager.record_trade_result(profit)

                closed_trades.append((trade, exit_price, profit, swap, close_comment))

        # Outside the lock — Discord notify (network IO, no MT5 access)
        for trade, exit_price, profit, swap, comment in closed_trades:
            await notification_manager.notify_trade_close({
                "ticket": trade.ticket,
                "symbol": trade.symbol,
                "direction": trade.direction,
                "volume": trade.volume,
                "open_price": trade.entry_price,
                "close_price": exit_price,
                "profit": profit,
                "comment": comment,
            })

    async def close_all_trades(self) -> List[dict]:
        """Emergency method to close all OUR trades (magic-filtered).

        Track (d) — magic-number isolation: only positions with
        ``settings.magic_number`` are flattened. Foreign / manual trades
        in the same MT5 account are explicitly left untouched. All
        closes go through ``order_manager.close_position`` (one ticket
        at a time) so each close is verified independently — a single
        rejected close does not abort the rest of the flatten.

        All TT5 order_send calls reach the serialised executor through
        ``close_position``, so partial closes cannot complete before we
        finish iterating. ``_emergency_lock`` prevents
        ``_execute_trading_cycle`` from placing a new order while we
        finish closing everything.
        """
        closed_results: List[dict] = []
        async with self._emergency_lock:
            # Magic-filtered list — never close foreign / manual trades.
            positions = await self._mt5.submit(
                mt5.positions_get, magic=settings.magic_number
            )
            if positions:
                for pos in positions:
                    try:
                        result = await self.order_manager.close_position(
                            pos.ticket, comment="Genesis Kill-Switch Close"
                        )
                        if result:
                            closed_results.append(result)
                    except Exception as e:
                        # One bad close shouldn't kill the rest of the flatten
                        logger.error(
                            f"[ERR] Failed to close position {pos.ticket}: {e}"
                        )

        await self._check_closed_positions()
        return closed_results

    # ── Startup & State Sync Helpers ───────────────────────────────

    async def _sync_open_positions(self):
        """Syncs database open trades with actual MT5 state on startup.

        At startup the executor is still new so there are no other concurrent
        MT5 operations — the executor serialises these reads correctly.
        DB writes (which do not touch MT5) happen outside this path.
        """
        logger.info("🔄 Synchronizing positions on startup...")

        db_open_trades = await self.db.get_open_trades()
        db_open_tickets = {t.ticket for t in db_open_trades}

        # ── All MT5 reads via the executor ───────────────────────────
        active_raw = await self._mt5.submit(mt5.positions_get, magic=settings.magic_number)
        active_positions = _raw_positions_to_dicts(active_raw)
        active_tickets = {p["ticket"] for p in active_positions}

        missing_closes = []  # tuples of (ticket, exit, profit, swap, comment)
        for trade in db_open_trades:
            if trade.ticket in active_tickets:
                continue
            logger.info(
                f"[WARN] Sync: Ticket {trade.ticket} was closed while bot "
                f"was offline. Logging closure."
            )
            history_from = datetime.now() - timedelta(days=7)
            history_to_ts = int(datetime.now().timestamp()) + 3600
            deals = await self._mt5.submit(
                mt5.history_deals_get,
                int(history_from.timestamp()),
                history_to_ts,
                position=trade.ticket,
            )

            exit_price = trade.entry_price
            profit = 0.0
            swap = 0.0
            close_comment = "Closed offline"

            if deals:
                for deal in deals:
                    if deal.entry == mt5.DEAL_ENTRY_OUT:
                        exit_price = deal.price
                        profit += deal.profit
                        swap += deal.swap
                        close_comment = deal.comment or "Closed offline"

            missing_closes.append(
                (trade.ticket, exit_price, profit, swap, close_comment)
            )

        # ── DB writes outside the executor ───────────────────────────
        for ticket, exit_price, profit, swap, comment in missing_closes:
            await self.db.record_trade_close(ticket, exit_price, profit, swap, comment)

        # Re-log any active MT5 positions the bot doesn't know about
        for pos in active_positions:
            if pos["ticket"] not in db_open_tickets:
                logger.info(
                    f"[WARN] Sync: Active ticket {pos['ticket']} is missing from "
                    f"database. Re-logging."
                )
                await self.db.record_trade_open(
                    ticket=pos["ticket"],
                    symbol=pos["symbol"],
                    direction=pos["direction"],
                    volume=pos["volume"],
                    entry_price=pos["open_price"],
                    sl=pos["sl"],
                    tp=pos["tp"],
                    strategy="Sync Recovered",
                    regime="ranging",
                    comment=pos["comment"] or "Recovered position",
                )

        logger.success("[OK] Position synchronization completed")

    async def _record_daily_stats(self):
        """Save today's performance stats to DB and update max-drawdown tracker.

        The MT5 account-info read goes through the AsyncMt5Executor which
        serialises it with all other trading operations — no explicit
        all MT5 reads go through the AsyncMt5Executor, which serialises them
        with all other trading operations — no explicit lock needed.
        """
        account = await self._mt5.submit(mt5.account_info)
        if not account:
            return

        balance = getattr(account, "balance", settings.starting_capital)
        equity = getattr(account, "equity", settings.starting_capital)

        # Track drawdown against the running peak (was previously dead code)
        self.performance_tracker.update_drawdown(equity)

        summary = self.performance_tracker.get_summary()
        snapshot = {
            "balance": balance,
            "equity": equity,
            "pnl": summary.get("rolling_pnl", 0.0),
            "drawdown": self.performance_tracker.get_max_drawdown(),
            "win_rate": summary.get("win_rate", 0.0),
            "trade_count": summary.get("total_trades", 0),
        }

        # DB writes don't touch MT5 — safe outside the executor path.
        await self.db.record_daily_performance(**snapshot)

    async def _load_persisted_state(self):
        """Load configuration settings saved in the DB."""
        logger.info("⚙️ Loading persisted states from database...")
        
        # Load settings overrides
        risk = await self.db.get_state("max_risk_per_trade")
        if risk:
            settings.max_risk_per_trade = float(risk)
            
        dd = await self.db.get_state("max_daily_drawdown")
        if dd:
            settings.max_daily_drawdown = float(dd)
            
        max_pos = await self.db.get_state("max_open_positions")
        if max_pos:
            settings.max_open_positions = int(max_pos)
            
        pairs = await self.db.get_state("trading_pairs")
        if pairs:
            settings.trading_pairs = [p.strip() for p in pairs.split(",") if p.strip()]
            
        paper = await self.db.get_state("paper_trading")
        if paper:
            settings.paper_trading = paper == "1"
            
        logger.info("⚙️ Persisted configuration settings applied successfully")
