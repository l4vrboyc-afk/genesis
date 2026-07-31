"""
Trading Orchestrator — The central heartbeat of the Genesis bot.
Coordinates MT5 connectivity, fetches market data, evaluates signals,
manages risk parameters, updates the database, and schedules loop tasks.
"""

import asyncio
import itertools
import queue
import threading
from datetime import datetime, timedelta, timezone
import MetaTrader5 as mt5
from loguru import logger
from typing import Any, List, Optional

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
from bot.core.dynamic_trailing import DynamicTrailingManager
from bot.visual import GenesisVisualEngine

# Lazy import of the WebSocket broadcasting engine (dashboard may not be
# initialised in CLI-only mode).  The ws_manager singleton is a no-op when
# no browser clients are connected, so these calls are safe everywhere.
try:
    from dashboard.backend.ws_manager import ws_manager
    from dashboard.backend.schemas import WSEventPayload
    _WS_AVAILABLE = True
except ImportError:
    _WS_AVAILABLE = False


# ── Constants ──────────────────────────────────────────────────────────

# P&L tolerance (in dollars) for DB-vs-MT5 consistency checks. If the
# profit stored in the database differs from MT5's deal history by more
# than this, the orchestrator logs a WARNING so the operator can catch
# data corruption live — rather than discovering it later in the dashboard.
PNL_TOLERANCE = 0.05

# Cadence (seconds) for the background WS pill-snapshot broadcaster.
# Independent of the 15s trading loop so the dashboard's live gate pills
# and trade-signal pill refresh in real time even while the engine is
# paused or between scans.
WS_PILL_REFRESH_INTERVAL = 15


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
        self.dynamic_trailing = DynamicTrailingManager(self.fetcher, self.order_manager)

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
        self._ws_pill_task = None
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

        # Visual engine — overlay refresh cadence + sweep cleanup counter
        self._last_overlay_refresh: Optional[datetime] = None
        self._overlay_refresh_interval_secs: int = 300  # every 5 minutes
        self._visual_cycle_count: int = 0
        self._sweep_cleanup_every_n_cycles: int = 10


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
        self.dynamic_trailing.attach_executor(self._mt5)

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

        # 4b. Sweep orphaned visual objects from MT5 charts
        try:
            GenesisVisualEngine.cleanup_all_genesis_objects()
        except Exception as e:
            logger.warning(f"[WARN] VisualEngine sweep failed: {e}")

        # 5. Start main loop
        self._running = True
        self._paused = False
        self._loop_task = asyncio.create_task(self._main_loop())

        # 5b. Start the WS pill-snapshot broadcaster (live gate + signal
        # pills refresh in real time without a dropdown change).
        self._ws_pill_task = asyncio.create_task(self._ws_pill_snapshot_loop())
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

        if self._ws_pill_task:
            self._ws_pill_task.cancel()
            try:
                await self._ws_pill_task
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
            "active_profile": settings.active_profile,
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

    async def _ws_pill_snapshot_loop(self):
        """Push live gate + signal pill snapshots to dashboard clients.

        Runs independently of the trading loop so the header gateway pills
        and the trade-signal pill refresh in real time without a dropdown
        change.  Skips all work when no WebSocket client is connected (the
        broadcast manager is a no-op then anyway) and when the dashboard
        package isn't available (CLI-only mode).
        """
        while self._running:
            try:
                if _WS_AVAILABLE and ws_manager.active_connections:
                    await self._broadcast_pill_snapshot()
            except Exception as e:
                logger.debug(f"WS pill snapshot error: {e}")
            await asyncio.sleep(WS_PILL_REFRESH_INTERVAL)

    async def _broadcast_pill_snapshot(self) -> None:
        """Evaluate gates + signal for every trading pair and broadcast.

        Reuses the same stateless dashboard evaluators as ``GET /api/
        evaluator`` and ``GET /api/signal`` so the WebSocket push and the
        HTTP route always agree on pill state.

        Emits one ``GATE_UPDATE`` and one ``SIGNAL_UPDATE`` per pair when
        MT5 data is available (skipped cleanly when it isn't).
        """
        if not _WS_AVAILABLE:
            return
        if not ws_manager.active_connections:
            return

        try:
            from dashboard.backend.routes.signal import (
                _format_price,
                _hold_estimate,
            )
        except Exception as e:
            logger.debug(f"WS pill snapshot import failed: {e}")
            return

        profile = settings.active_profile
        for pair in settings.trading_pairs:
            try:
                # Same data contract as the routes (entry + higher TF frames
                # and gatekeeper indicators), fetched through the executor.
                gk, etf_data, htf_data = await asyncio.gather(
                    self.fetcher.get_gatekeeper_indicators(pair),
                    self.fetcher.get_analyzed_data(
                        pair, settings.entry_timeframe, 120
                    ),
                    self.fetcher.get_analyzed_data(
                        pair, settings.higher_timeframe, 220
                    ),
                )
            except Exception as e:
                logger.debug(f"WS pill data fetch failed for {pair}: {e}")
                continue

            # ── GATE_UPDATE ──────────────────────────────────────────
            # Mirror the evaluator route: broadcast unless every MT5 source
            # is unavailable (then the frontend keeps its last pills).
            if (
                gk
                or (etf_data is not None and not etf_data.empty)
                or (htf_data is not None and not htf_data.empty)
            ):
                try:
                    gates = self.strategy_selector.evaluate_symbol_gates(
                        symbol=pair,
                        gatekeeper_data=gk,
                        entry_tf_data=etf_data,
                        htf_data=htf_data,
                    )
                    gates["profile"] = profile
                    await ws_manager.broadcast(WSEventPayload(
                        event_type="GATE_UPDATE",
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        data=gates,
                    ))
                except Exception as e:
                    logger.debug(f"WS GATE_UPDATE broadcast failed for {pair}: {e}")

            # ── SIGNAL_UPDATE ────────────────────────────────────────
            try:
                current_price = await self.fetcher.get_current_price(pair)
                if (
                    not isinstance(current_price, dict)
                    or "bid" not in current_price
                    or "ask" not in current_price
                ):
                    # Synthesize from the latest close (mirrors the route).
                    src = (
                        etf_data
                        if (etf_data is not None and not etf_data.empty)
                        else htf_data
                    )
                    if src is None or src.empty:
                        continue
                    close = float(src["close"].iloc[-1])
                    current_price = {"bid": close, "ask": close, "last": close}

                signal = self.strategy_selector.evaluate_symbol_signal(
                    symbol=pair,
                    htf_data=htf_data,
                    etf_data=etf_data,
                    current_price=current_price,
                )
                duration = _hold_estimate(profile)

                if signal is None:
                    payload = {
                        "symbol": pair,
                        "profile": profile,
                        "action": "NEUTRAL / WAIT",
                        "type": "WAIT",
                        "sl": "--",
                        "tp": "--",
                        "duration": duration,
                        "entry": None,
                        "confidence": None,
                        "risk_reward_ratio": None,
                        "strategy": None,
                    }
                else:
                    direction = signal.direction.value.upper()
                    payload = {
                        "symbol": pair,
                        "profile": profile,
                        "action": f"{direction} SIGNAL",
                        "type": direction,
                        "sl": _format_price(signal.stop_loss, pair),
                        "tp": _format_price(signal.take_profit, pair),
                        "duration": duration,
                        "entry": _format_price(signal.entry_price, pair),
                        "confidence": signal.confidence,
                        "risk_reward_ratio": signal.risk_reward_ratio,
                        "strategy": signal.strategy_name,
                    }

                await ws_manager.broadcast(WSEventPayload(
                    event_type="SIGNAL_UPDATE",
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    data=payload,
                ))
            except Exception as e:
                logger.debug(f"WS SIGNAL_UPDATE broadcast failed for {pair}: {e}")

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

    @staticmethod
    def _is_m15_data_stale(m15_data: Any, max_age_seconds: int = 15 * 60) -> bool:
        """Return True when M15 snapshot's last bar is older than one period.

        Stale ATR/EMA/swing values inflate ``profit_atr`` and cause premature
        Stage 3 (ACCELERATED) escalation. Re-fetch when the last closed bar
        is older than ``now - 15 minutes``.
        """
        if m15_data is None:
            return True
        try:
            if getattr(m15_data, "empty", True):
                return True
            last_time = m15_data.index[-1]
            if hasattr(last_time, "to_pydatetime"):
                last_time = last_time.to_pydatetime()
            if isinstance(last_time, datetime):
                if last_time.tzinfo is None:
                    last_time = last_time.replace(tzinfo=timezone.utc)
                now = datetime.now(timezone.utc)
                return (now - last_time).total_seconds() > max_age_seconds
        except Exception:
            return True
        return True

    async def _maybe_refresh_overlays(self, symbols: list[str]) -> None:
        """Refresh session-range + equilibrium overlays every ~300 seconds."""
        now = datetime.now(timezone.utc)
        if (
            self._last_overlay_refresh is not None
            and (now - self._last_overlay_refresh).total_seconds()
            < self._overlay_refresh_interval_secs
        ):
            return

        for symbol in symbols:
            try:
                await self._refresh_overlays_for_symbol(symbol)
            except Exception as e:
                logger.debug(f"⚠️ Overlay refresh failed for {symbol}: {e}")

        self._last_overlay_refresh = now

    async def _refresh_overlays_for_symbol(self, symbol: str) -> None:
        """Rebuild session + S/R equilibrium overlays for one symbol."""
        h1 = await self.fetcher.get_analyzed_data(symbol, "H1", 72)
        if h1 is None or h1.empty:
            return

        GenesisVisualEngine.cleanup_overlays(symbol)

        now = datetime.now(timezone.utc)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        # Cover Asia → London → NY window (UTC day start → now + small pad)
        time_start = day_start.timestamp()
        time_end = (now + timedelta(hours=1)).timestamp()

        # Today's H1 high/low as the session range
        try:
            day_start_naive = day_start.replace(tzinfo=None)
            idx = h1.index
            if getattr(idx, "tz", None) is not None:
                mask = idx >= day_start
            else:
                mask = idx >= day_start_naive
            today = h1.loc[mask]
            if today.empty:
                today = h1.tail(24)
            session_high = float(today["high"].max())
            session_low = float(today["low"].min())
            if session_high > session_low:
                GenesisVisualEngine.place_overlay(
                    symbol=symbol,
                    overlay_type="session_range",
                    label="Session Range",
                    price_high=session_high,
                    price_low=session_low,
                    time_start=time_start,
                    time_end=time_end,
                    color_type="DodgerBlue",
                )
        except Exception as e:
            logger.debug(f"Session overlay skipped for {symbol}: {e}")

        # Equilibrium bands from swing S/R
        try:
            sr = self.fetcher.get_support_resistance(h1)
            band = None
            for col in h1.columns:
                if str(col).startswith("atr_"):
                    last = h1[col].iloc[-1]
                    if last is not None and float(last) == float(last) and float(last) > 0:
                        band = float(last) * 0.15
                        break
            if band is None:
                mid = float(h1["close"].iloc[-1])
                band = mid * 0.00015

            for level in (sr.get("resistance") or [])[:2]:
                GenesisVisualEngine.place_overlay(
                    symbol=symbol,
                    overlay_type="equilibrium",
                    label="Resistance",
                    price_high=float(level) + band,
                    price_low=float(level) - band,
                    color_type="Orange",
                )
            for level in (sr.get("support") or [])[:2]:
                GenesisVisualEngine.place_overlay(
                    symbol=symbol,
                    overlay_type="equilibrium",
                    label="Support",
                    price_high=float(level) + band,
                    price_low=float(level) - band,
                    color_type="MediumSeaGreen",
                )
        except Exception as e:
            logger.debug(f"Equilibrium overlay skipped for {symbol}: {e}")

        logger.debug(f"🗏 Overlays refreshed for {symbol}")

    async def _detect_and_draw_sweeps(self, symbols: set) -> None:
        """Detect H1 liquidity sweeps and pin them on charts for open symbols."""
        if not symbols:
            return

        cleanup_now = (
            self._visual_cycle_count % self._sweep_cleanup_every_n_cycles == 0
        )

        for symbol in symbols:
            try:
                if cleanup_now:
                    GenesisVisualEngine.cleanup_sweep_pins(symbol)

                h1 = await self.fetcher.get_analyzed_data(symbol, "H1", 80)
                if h1 is None or h1.empty:
                    continue

                sweeps = self.fetcher.detect_sweeps(h1, atr_multiplier=1.0, lookback=60)
                for sweep in sweeps:
                    direction = (
                        "bullish" if sweep.get("type") == "sellside" else "bearish"
                    )
                    pin_id = GenesisVisualEngine.add_sweep_pin(
                        symbol=symbol,
                        level=float(sweep["level"]),
                        label=str(sweep.get("label") or sweep.get("type") or "Sweep"),
                        direction=direction,
                    )
                    if pin_id:
                        logger.info(
                            f"📍 Sweep pin {symbol}: {sweep.get('label')} "
                            f"@ {sweep['level']:.5f} ({direction})"
                        )
            except Exception as e:
                logger.debug(f"⚠️ Sweep detection failed for {symbol}: {e}")

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

        # Clean up the dynamic trailing manager — remove closed positions
        # from its internal per-position mode tracking.
        active_tickets = {p["ticket"] for p in positions}
        self.dynamic_trailing.cleanup(active_tickets)

        self._visual_cycle_count += 1

        # ── Phase 1.5: Session / equilibrium overlay refresh ───────────
        # Refresh at most every 300s so OBJ_RECTANGLE overlays stay current
        # without rewriting the IPC file every 15s cycle.
        await self._maybe_refresh_overlays(
            symbols=sorted({p["symbol"] for p in positions} | set(settings.trading_pairs))
        )

        # ── Phase 2: Dynamic Multi‑Stage Trailing ──────────────────────
        #   Replaces the old single‑distance breach-only trailing (Phase 1.5
        #   breakeven + Phase 3 simple trailing) with an adaptive mode
        #   switching engine that escalates through:
        #
        #       STATIC → BREAKEVEN → STRUCTURE / ATR_DYNAMIC → ACCELERATED
        #
        #   Each position is evaluated independently. M15 data is re-fetched
        #   when the cached snapshot is older than one M15 period so ATR /
        #   EMA / swing values cannot go stale mid-cycle.
        #
        #   The engine also handles spread‑aware breakeven logic that
        #   prevents accidental stop‑outs from the Bid/Ask gap — critical
        #   for SELL trades where SL triggers on the higher Ask price.

        # Fetch M15 trailing data — cache only while still fresh (< 15m)
        m15_cache: dict[str, Any] = {}
        unique_symbols = {p["symbol"] for p in positions}
        for sym in unique_symbols:
            try:
                m15 = await self.fetcher.get_analyzed_data(sym, "M15", 60)
                if m15 is not None and not m15.empty:
                    m15_cache[sym] = m15
            except Exception as e:
                logger.debug(f"⚠️ M15 data fetch failed for {sym}: {e}")

        for pos in positions:
            # Fetch current tick for this position
            tick = await self._mt5.submit(mt5.symbol_info_tick, pos["symbol"])
            if not tick:
                continue

            # Stale-guard: re-fetch M15 if the cached last bar is older than
            # one M15 period (ATR/EMA/swings would otherwise understate vol
            # and trigger premature stage escalation).
            sym = pos["symbol"]
            m15_data = m15_cache.get(sym)
            if self._is_m15_data_stale(m15_data):
                try:
                    m15_data = await self.fetcher.get_analyzed_data(sym, "M15", 60)
                    if m15_data is not None and not m15_data.empty:
                        m15_cache[sym] = m15_data
                        logger.debug(
                            f"♻️ Refreshed stale M15 data for {sym} before trailing"
                        )
                except Exception as e:
                    logger.debug(f"⚠️ M15 re-fetch failed for {sym}: {e}")

            # Delegate to the dynamic trailing manager
            try:
                await self.dynamic_trailing.evaluate_and_trail(
                    pos, tick, m15_data=m15_data,
                )
            except Exception as e:
                logger.error(
                    f"⚠️ Dynamic trailing failed for T{pos['ticket']} "
                    f"({pos['symbol']}): {e}"
                )

        # ── Phase 2.5: Liquidity sweep pins for open-position symbols ──
        await self._detect_and_draw_sweeps(unique_symbols)

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

            # Rule 1 & 2: Also fetch H1 gatekeeper indicators (ADX, EMA50,
            # ATR, close) — lightweight, no SMC or extra indicator overhead.
            htf_data, etf_data, current_price, gk = await asyncio.gather(
                self.fetcher.get_analyzed_data(pair, settings.higher_timeframe, 300),
                self.fetcher.get_analyzed_data(pair, settings.entry_timeframe, 100),
                self.fetcher.get_current_price(pair),
                self.fetcher.get_gatekeeper_indicators(pair),
            )

            tick_data = {
                "symbol": pair,
                "timestamp": int(current_price["time"].timestamp()),
                "bid": current_price["bid"],
                "ask": current_price["ask"],
                "spread": current_price.get("spread", round(current_price["ask"] - current_price["bid"], 6)),
                "volume": current_price.get("volume", 0),
            }
            await enqueue_tick(tick_data)

            # Broadcast tick to WebSocket dashboard clients (high-frequency
            # path — uses broadcast_json to skip WSEventPayload overhead).
            if _WS_AVAILABLE:
                try:
                    await ws_manager.broadcast_json({
                        "event_type": "TICK",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "data": tick_data,
                    })
                except Exception as e:
                    logger.debug(f"WS broadcast (tick) failed: {e}")

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

            # ── 5-Gate Evaluator ──────────────────────────────────────
            # All 5 gates evaluated in one bundled call. ≥4/5 required.
            #   Gate 1: Gatekeeper  — ADX+EMA50 trend guard (SELL-only)
            #   Gate 2: Sniper      — M15 candle pattern confirmation
            #   Gate 3: Volume      — latest bar ≥ 1.5× 20-bar avg
            #   Gate 4: EMA Stack   — price stacked with EMA50+EMA200
            #   Gate 5: ADX Strength — ADX ≥ 20 (trend conviction)
            gate_eval = self.strategy_selector.evaluate_gates(
                signal, gk, etf_data, htf_data
            )
            # Broadcast gate evaluation to dashboard in real-time
            if _WS_AVAILABLE:
                try:
                    await ws_manager.broadcast(WSEventPayload(
                        event_type="GATE_EVAL",
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        data={
                            "symbol": pair,
                            "direction": signal.direction.value,
                            "passed": gate_eval["passed"],
                            "total": gate_eval["total"],
                            "allowed": gate_eval["allowed"],
                            "gates": gate_eval["gates"],
                        },
                    ))
                except Exception as e:
                    logger.debug(f"WS broadcast (gate_eval) failed: {e}")
            if not gate_eval["allowed"]:
                logger.info(
                    f"🛑 GATES blocked {pair} {signal.direction.value.upper()}: "
                    f"{gate_eval['passed']}/{gate_eval['total']} passed "
                    f"(need ≥4) — "
                    f"{', '.join(g['name'] for g in gate_eval['gates'] if not g['passed'])}"
                )
                continue

            # ── Rule 2: Minimum SL Floor ───────────────────────────────
            # If the raw stop-loss distance is smaller than 1.0× H1 ATR, bump
            # it up so the trade has proper breathing room against standard
            # market noise.  This prevents the tiny-SL problem where normal
            # intra-hour wiggles wipe out the position before the move begins.
            h1_atr = gk.get("atr") if gk else None
            if h1_atr is not None and h1_atr > 0:
                    min_sl_distance = h1_atr * settings.min_sl_h1_atr_mult
                    current_sl_dist = abs(signal.entry_price - signal.stop_loss)
                    if current_sl_dist < min_sl_distance:
                        old_sl = signal.stop_loss
                        if signal.direction == TradeDirection.BUY:
                            signal.stop_loss = signal.entry_price - min_sl_distance
                        else:
                            signal.stop_loss = signal.entry_price + min_sl_distance
                        logger.info(
                            f"📏 SL FLOOR bumped {pair}: {old_sl:.5f} → "
                            f"{signal.stop_loss:.5f} (raw dist {current_sl_dist:.5f} < "
                            f"H1 ATR {h1_atr:.5f} × {settings.min_sl_h1_atr_mult:.1f})"
                        )

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
                profile=settings.active_profile,
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

            # Broadcast trade opened to WebSocket dashboard clients
            if _WS_AVAILABLE:
                try:
                    await ws_manager.broadcast(
                        WSEventPayload(
                            event_type="TRADE_OPEN",
                            timestamp=datetime.now(timezone.utc).isoformat(),
                            data={
                                "ticket": trade_result["ticket"],
                                "symbol": signal.symbol,
                                "direction": signal.direction.value,
                                "volume": lots,
                                "entry_price": trade_result["price"],
                                "sl": signal.stop_loss,
                                "tp": signal.take_profit,
                                "strategy": signal.strategy_name,
                            },
                        )
                    )
                except Exception as e:
                    logger.debug(f"WS broadcast (trade_open) failed: {e}")

            # Draw initial chart visuals for the newly opened position
            # Pass H1 ATR from gatekeeper indicators (already fetched) so the
            # HUD shows a meaningful volatility value instead of 0.0.
            try:
                GenesisVisualEngine.update_trade_visuals(
                    symbol=signal.symbol,
                    ticket=trade_result["ticket"],
                    position_type=signal.direction.value,
                    entry_price=trade_result["price"],
                    current_sl=signal.stop_loss,
                    target_tp=signal.take_profit,
                    current_mode="STATIC",
                    atr_value=h1_atr or 0.0,
                )
            except Exception as e:
                logger.debug(f"VisualEngine: initial draw failed T{trade_result['ticket']}: {e}")


    async def _compute_trade_metrics(
        self,
        trade,
        profit: float,
    ) -> tuple:
        """Compute position_value_usd and return_r for a closed trade.

        Uses MT5 ``symbol_info`` to resolve contract size, tick value and
        tick size so the figures are instrument-agnostic (handles JPY pairs,
        metals, indices, etc.).

        Formulas:
            position_value_usd = volume * contract_size * entry_price
            risk_amount        = (|entry_price - sl| / tick_size) * tick_value * volume
            return_r           = profit / risk_amount  (0 when risk ≈ 0)

        Returns:
            (position_value_usd, return_r) — both ``0.0`` on any MT5
            error so the trade is still persisted with safe fallbacks.
        """
        try:
            symbol_info = await self._mt5.submit(mt5.symbol_info, trade.symbol)
            if symbol_info is None:
                logger.debug(
                    f"⚠️ symbol_info unavailable for {trade.symbol} — "
                    f"metrics defaulted to 0.0"
                )
                return 0.0, 0.0

            contract_size = getattr(symbol_info, "trade_contract_size", 100000)
            tick_value = getattr(symbol_info, "trade_tick_value", 0.0)
            tick_size = getattr(symbol_info, "trade_tick_size", 0.0)

            # Position notional value (in quote currency; approx USD for
            # USD-quote pairs — matches the dashboard's "approximate" intent).
            position_value_usd = round(
                trade.volume * contract_size * trade.entry_price, 2
            )

            # R-multiple: realised PnL ÷ initial risk amount in USD.
            risk_distance = abs(trade.entry_price - trade.sl)
            if (
                tick_size > 0
                and tick_value > 0
                and risk_distance > 0
            ):
                risk_amount = (risk_distance / tick_size) * tick_value * trade.volume
                return_r = round(profit / risk_amount, 2) if risk_amount > 0 else 0.0
            else:
                return_r = 0.0

            return position_value_usd, return_r
        except Exception as e:
            logger.debug(
                f"⚠️ _compute_trade_metrics failed for T{trade.ticket}: {e}"
            )
            return 0.0, 0.0


    def _extract_exit_info(
        self,
        deals,
        default_exit: float = 0.0,
    ) -> tuple:
        """Extract exit price, profit, swap, and comment from MT5 deal history.

        Filters strictly by ``DEAL_ENTRY_OUT`` so that entry deals from other
        positions sharing the same time window cannot overwrite the real exit
        price — the root cause of the cross-market price contamination bug
        (e.g. GBPJPY exit prices appearing on USDCAD / AUDUSD rows).

        Args:
            deals: Iterable of MT5 deal objects returned by
                ``mt5.history_deals_get(position=ticket)``.
            default_exit: Fallback price if no OUT deal is found (typically
                the trade's entry price so the row doesn't show 0.0).

        Returns:
            Tuple of (exit_price, profit, swap, close_comment).
        """
        exit_price = default_exit
        profit = 0.0
        swap = 0.0
        close_comment = "Closed"

        if not deals:
            return exit_price, profit, swap, close_comment

        for deal in deals:
            # Only process the closing (exit) leg — skip entry deals entirely
            # to avoid inheriting prices from unrelated position entries.
            if getattr(deal, "entry", None) == mt5.DEAL_ENTRY_OUT:
                exit_price = deal.price
                profit += deal.profit
                swap += deal.swap
                # Include broker commission so the dashboard profit matches
                # the MT5 account statement down to the cent. In MT5,
                # ``deal.profit`` may NOT include the commission — it's a
                # separate field on the same deal object. Adding it ensures
                # the net P&L is accurate even on ECN accounts where
                # commissions are charged per-lot ($7/lot → $133 on 19 lots).
                commission = getattr(deal, "commission", 0.0) or 0.0
                profit += commission
                if deal.comment:
                    close_comment = deal.comment

        return exit_price, profit, swap, close_comment

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

            # Fetch MT5 history deals for this specific position ticket.
            # The ``position=trade.ticket`` kwarg tells MT5 to pre-filter deals
            # to those belonging to this position — we then apply an additional
            # DEAL_ENTRY_OUT guard inside _extract_exit_info so no unrelated
            # entry deals from concurrent positions can leak in.
            history_from = datetime.now() - timedelta(days=2)
            history_to = datetime.now() + timedelta(days=1)

            deals = await self._mt5.submit(
                mt5.history_deals_get,
                int(history_from.timestamp()),
                int(history_to.timestamp()),
                position=trade.ticket
            )

            # Extract exit price using DEAL_ENTRY_OUT filter to prevent
            # cross-market price contamination (Fix #1).
            exit_price, profit, swap, close_comment = self._extract_exit_info(deals, default_exit=trade.entry_price)

            # ── P&L consistency check ────────────────────────────────
            # Check if the profit MT5 reports differs from what's stored
            # in the database. A mismatch means the DB has stale / corrupted
            # data and should be investigated.
            old_db_profit = round(getattr(trade, "profit", 0.0) or 0.0, 2)
            mt5_profit = round(profit, 2)
            if abs(old_db_profit - mt5_profit) > PNL_TOLERANCE:
                logger.warning(
                    f"⚠️ PNL_MISMATCH Ticket {trade.ticket} ({trade.symbol}): "
                    f"DB profit=${old_db_profit:.2f} ≠ MT5 profit=${mt5_profit:.2f} "
                    f"(diff=${mt5_profit - old_db_profit:+.2f}). "
                    f"DB data is stale — will overwrite with MT5 value."
                )

            # Update Database — compute position value & R-multiple first
            position_value_usd, return_r = await self._compute_trade_metrics(trade, profit)

            updated_trade = await self.db.record_trade_close(
                ticket=trade.ticket,
                exit_price=exit_price,
                profit=profit,
                swap=swap,
                comment=close_comment,
                position_value_usd=position_value_usd,
                return_r=return_r,
            )

            if updated_trade:
                # Update local PerformanceTracker
                trade_dict = updated_trade.to_dict()
                trade_dict["is_win"] = profit > 0
                self.performance_tracker.record_trade(trade_dict)

                # Update RiskManager streak
                self.risk_manager.record_trade_result(profit)

                # Clean up MT5 chart visuals for this closed trade
                try:
                    GenesisVisualEngine.cleanup_trade_objects(
                        symbol=trade.symbol, ticket=trade.ticket,
                    )
                    # Erase the OBJ_TREND trail-milestone history too
                    GenesisVisualEngine.cleanup_trail_milestones(ticket=trade.ticket)
                except Exception as e:
                    logger.debug(f"VisualEngine: cleanup failed T{trade.ticket}: {e}")

                closed_trades.append((trade, exit_price, profit, swap, close_comment, position_value_usd, return_r))

        # Outside the lock — Discord notify (network IO, no MT5 access)
        for trade, exit_price, profit, swap, comment, pos_val, ret_r in closed_trades:
            await notification_manager.notify_trade_close({
                "ticket": trade.ticket,
                "symbol": trade.symbol,
                "direction": trade.direction,
                "volume": trade.volume,
                "open_price": trade.entry_price,
                "close_price": exit_price,
                "profit": profit,
                "comment": comment,
                "position_value_usd": pos_val,
                "return_r": ret_r,
            })

            # Broadcast trade closed to WebSocket dashboard clients
            if _WS_AVAILABLE:
                try:
                    await ws_manager.broadcast(
                        WSEventPayload(
                            event_type="TRADE_CLOSE",
                            timestamp=datetime.now(timezone.utc).isoformat(),
                            data={
                                "ticket": trade.ticket,
                                "symbol": trade.symbol,
                                "direction": trade.direction,
                                "volume": trade.volume,
                                "entry_price": trade.entry_price,
                                "exit_price": exit_price,
                                "profit": profit,
                                "position_value_usd": pos_val,
                                "return_r": ret_r,
                            },
                        )
                    )
                except Exception as e:
                    logger.debug(f"WS broadcast (trade_close) failed: {e}")

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

        missing_closes = []  # tuples of (ticket, exit, profit, swap, comment, pos_val, ret_r)
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

            # Reuse the same DEAL_ENTRY_OUT-filtered helper for consistency
            # with _check_closed_positions — prevents price contamination on
            # startup sync too.
            exit_price, profit, swap, close_comment = self._extract_exit_info(
                deals, default_exit=trade.entry_price
            )

            # ── P&L consistency check (startup sync) ──────────────────
            old_db_profit = round(getattr(trade, "profit", 0.0) or 0.0, 2)
            mt5_profit = round(profit, 2)
            if abs(old_db_profit - mt5_profit) > PNL_TOLERANCE:
                logger.warning(
                    f"⚠️ PNL_MISMATCH (startup) Ticket {trade.ticket} "
                    f"({trade.symbol}): DB profit=${old_db_profit:.2f} ≠ "
                    f"MT5 profit=${mt5_profit:.2f} "
                    f"(diff=${mt5_profit - old_db_profit:+.2f}). "
                    f"DB data is stale — will overwrite with MT5 value."
                )

            if close_comment == "Closed":
                close_comment = "Closed offline"

            # Compute position value & R-multiple for the sync'd close
            pos_val, ret_r = await self._compute_trade_metrics(trade, profit)

            missing_closes.append(
                (trade.ticket, exit_price, profit, swap, close_comment, pos_val, ret_r)
            )

        # ── DB writes outside the executor ───────────────────────────
        for ticket, exit_price, profit, swap, comment, pos_val, ret_r in missing_closes:
            await self.db.record_trade_close(
                ticket, exit_price, profit, swap, comment,
                position_value_usd=pos_val, return_r=ret_r,
            )

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
                    profile=settings.active_profile,
                )

    # ── Closed Trade History (MT5 direct fetch) ───────────────────────

    async def get_closed_trade_history(self, count: int = 20) -> List[dict]:
        """Fetch recent closed trades directly from MT5 deal history.

        Enriches each closed deal with **Position Notional Value**
        (``position_value_usd``) and the realised **R-multiple**
        (``return_r``) — the metrics the dashboard's Recent History table
        now displays alongside raw P&L.

        The deal stream from ``mt5.history_deals_get()`` is grouped by
        ``position_id`` so that each closed position is reconstructed from
        its entry (IN) and exit (OUT) legs.  This gives us the entry price
        and stop-loss needed for the R calculation, which aren't available
        on the closing deal alone.

        Returns:
            List of dicts, most-recent-first, each containing:
            ``ticket``, ``symbol``, ``type``, ``lots``,
            ``position_value_usd``, ``entry_price``, ``close_price``,
            ``pnl``, ``return_r``.
        """
        history: List[dict] = []

        try:
            history_from = datetime.now() - timedelta(days=7)
            history_to = datetime.now() + timedelta(days=1)

            deals = await self._mt5.submit(
                mt5.history_deals_get,
                int(history_from.timestamp()),
                int(history_to.timestamp()),
            )

            if not deals:
                return history

            # Group deals by position_id, pairing IN and OUT legs
            positions: dict = {}
            for deal in deals:
                pos_id = getattr(deal, "position_id", None)
                if pos_id is None:
                    continue
                bucket = positions.setdefault(
                    pos_id, {"in_deal": None, "out_deal": None}
                )
                if deal.entry == getattr(mt5, "DEAL_ENTRY_IN", 1):
                    bucket["in_deal"] = deal
                elif deal.entry == getattr(mt5, "DEAL_ENTRY_OUT", 2):
                    bucket["out_deal"] = deal

            # Keep only fully closed positions (both legs present)
            closed = [
                p for p in positions.values()
                if p["in_deal"] and p["out_deal"]
            ]
            # Take the most recent ``count`` by deal time
            closed.sort(
                key=lambda p: getattr(p["out_deal"], "time", 0),
                reverse=True,
            )
            closed = closed[:count]

            for pos in closed:
                in_deal = pos["in_deal"]
                out_deal = pos["out_deal"]

                symbol = out_deal.symbol
                symbol_info = await self._mt5.submit(mt5.symbol_info, symbol)

                contract_size = (
                    getattr(symbol_info, "trade_contract_size", 100000)
                    if symbol_info else 100000
                )
                tick_value = (
                    getattr(symbol_info, "trade_tick_value", 0.0)
                    if symbol_info else 0.0
                )
                tick_size = (
                    getattr(symbol_info, "trade_tick_size", 0.0)
                    if symbol_info else 0.0
                )

                lots = out_deal.volume
                entry_price = in_deal.price
                close_price = out_deal.price

                # Position notional value (approximate USD)
                position_value_usd = round(lots * contract_size * entry_price, 2)

                # Net P&L including commission
                pnl = round(
                    out_deal.profit
                    + (out_deal.swap or 0.0)
                    + (getattr(out_deal, "commission", 0.0) or 0.0),
                    2,
                )

                # R-multiple: PnL / initial risk amount
                sl = getattr(in_deal, "sl", 0.0) or 0.0
                risk_distance = abs(entry_price - sl) if sl else 0.0
                if tick_size > 0 and tick_value > 0 and risk_distance > 0:
                    risk_amount = (
                        (risk_distance / tick_size) * tick_value * lots
                    )
                    return_r = round(pnl / risk_amount, 2) if risk_amount > 0 else 0.0
                else:
                    return_r = 0.0

                # Type: closing deal type is the opposite of the position
                deal_type = getattr(out_deal, "type", None)
                trade_type = (
                    "buy"
                    if deal_type == getattr(mt5, "DEAL_TYPE_SELL", 1)
                    else "sell"
                )

                history.append({
                    "ticket": out_deal.position_id,
                    "symbol": symbol,
                    "type": trade_type,
                    "lots": lots,
                    "position_value_usd": position_value_usd,
                    "entry_price": entry_price,
                    "close_price": close_price,
                    "pnl": pnl,
                    "return_r": return_r,
                })

        except Exception as e:
            logger.error(f"Failed to fetch closed trade history from MT5: {e}")

        return history

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
