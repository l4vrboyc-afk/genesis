"""
Performance Tracker — Monitors bot performance and triggers auto-adjustments.
Tracks win rate, profit factor, average R:R, and triggers self-optimization.
"""

from datetime import datetime
from typing import List, Optional
from collections import deque
from loguru import logger

from bot.config.settings import settings


class PerformanceTracker:
    """
    Tracks rolling performance metrics and triggers optimization actions.

    Monitors:
    - Win rate (rolling window)
    - Profit factor (gross profit / gross loss)
    - Average R:R achieved
    - Max drawdown
    - Sharpe-like ratio
    """

    def __init__(self):
        self._trades = deque(maxlen=500)  # Store last 500 trades
        self._daily_pnl = {}  # date → cumulative P&L
        self._peak_balance = 0.0
        self._max_drawdown = 0.0
        self._alerts = []

    def record_trade(self, trade: dict):
        """
        Record a completed trade.

        Args:
            trade: dict with keys: symbol, direction, profit, entry_price,
                   exit_price, sl, tp, strategy, regime, duration_seconds
        """
        trade["timestamp"] = datetime.now()
        trade["is_win"] = trade.get("profit", 0) > 0

        # Calculate achieved R:R
        entry = trade.get("entry_price", 0)
        exit_p = trade.get("exit_price", 0)
        sl = trade.get("sl", 0)
        if entry and sl and abs(entry - sl) > 0:
            risk = abs(entry - sl)
            reward = abs(exit_p - entry)
            trade["achieved_rr"] = round(reward / risk, 2)
        else:
            trade["achieved_rr"] = 0

        self._trades.append(trade)

        # Update daily P&L
        today = datetime.now().strftime("%Y-%m-%d")
        self._daily_pnl[today] = self._daily_pnl.get(today, 0) + trade.get("profit", 0)

        logger.info(
            f"📝 Trade recorded: {trade.get('symbol')} "
            f"{'🟢 WIN' if trade['is_win'] else '🔴 LOSS'} "
            f"${trade.get('profit', 0):.2f} | R:R {trade['achieved_rr']}"
        )

        # Check for optimization triggers
        self._check_triggers()

    # ── Hydration from persistent storage ──────────────────────────

    def hydrate_from_records(self, records: List[dict]) -> None:
        """Bulk-load historical closed trades on startup.

        The in-memory ``_trades`` deque and ``_daily_pnl`` map are populated
        from ``records`` so win-rate, profit-factor, and total PnL reflect
        the bot's full history on the very first cycle — instead of resetting
        to zero after every restart and slowly climbing as fresh closes
        trickle in.

        Records are replayed in chronological order (oldest first) so the
        deque's maxlen=500 window ends up holding the most-recent trades.

        ``_check_triggers`` is called ONCE after hydration so the alerts list reflects full performance history (not just fresh closes). Trigger logging is rate-limited inside ``_check_triggers`` itself (it checks ``len(trades) >= window // 2``), so historical replay produces at most one alert per trigger type per restart.
        """
        if not records:
            return

        # Ordered tuples so the deque's tail is the most recent trade (the
        # shape the rolling-window metrics assume).
        def _ts(rec: dict) -> datetime:
            raw = rec.get("close_time") or rec.get("open_time")
            if isinstance(raw, str):
                try:
                    return datetime.fromisoformat(raw)
                except ValueError:
                    return datetime.min
            return raw or datetime.min

        for trade in sorted(records, key=_ts):
            # Mirror the per-trade enrichment record_trade() does, but skip
            # the alerts/triggers path that would spam the log on startup.
            trade["is_win"] = (trade.get("profit") or 0.0) > 0
            trade["timestamp"] = _ts(trade) or datetime.now()

            entry = trade.get("entry_price") or 0.0
            exit_p = trade.get("exit_price") or 0.0
            sl = trade.get("sl") or 0.0
            if entry and sl and abs(entry - sl) > 0:
                trade["achieved_rr"] = round(abs(exit_p - entry) / abs(entry - sl), 2)
            else:
                trade["achieved_rr"] = 0.0

            self._trades.append(trade)

            ts = trade["timestamp"]
            day = ts.strftime("%Y-%m-%d") if isinstance(ts, datetime) else str(ts)[:10]
            self._daily_pnl[day] = self._daily_pnl.get(day, 0.0) + (trade.get("profit") or 0.0)

        logger.info(
            f"💧 PerformanceTracker hydrated: {len(records)} closed trade(s) "
            f"across {len(self._daily_pnl)} day(s)"
        )

        # Run triggers once after hydration so alerts reflect full history
        self._check_triggers()
        logger.info("🔔 Trigger check complete after hydration")

    def hydrate_daily_performance(self, daily_records: List[dict]) -> None:
        """Seed peak-balance tracker from prior DailyPerformance snapshots.

        Without this, ``update_drawdown`` starts ``_peak_balance == 0`` after
        every restart, so the very next equity read gives a drawdown of
        ``(equity - 0) / equity = 100%`` and pollutes the max-drawdown metric
        with garbage until the running balance has been seen before.
        """
        if not daily_records:
            return

        peak = max((float(d.get("equity") or 0.0) for d in daily_records), default=0.0)
        if peak > self._peak_balance:
            self._peak_balance = peak
        logger.info(
            f"💧 Hydrated peak balance = ${peak:.2f} from {len(daily_records)} snapshot(s)"
        )

    # ── Metrics ─────────────────────────────────────────────────────

    def get_win_rate(self, window: Optional[int] = None) -> float:
        """Calculate win rate over the specified window of trades."""
        trades = self._get_window(window)
        if not trades:
            return 0.0

        wins = sum(1 for t in trades if t["is_win"])
        return round(wins / len(trades), 4)

    def get_profit_factor(self, window: Optional[int] = None) -> float:
        """Calculate profit factor (gross profits / gross losses)."""
        trades = self._get_window(window)
        if not trades:
            return 0.0

        gross_profit = sum(t["profit"] for t in trades if t["profit"] > 0)
        gross_loss = abs(sum(t["profit"] for t in trades if t["profit"] < 0))

        if gross_loss == 0:
            return float("inf") if gross_profit > 0 else 0.0
        return round(gross_profit / gross_loss, 2)

    def get_average_rr(self, window: Optional[int] = None) -> float:
        """Calculate average achieved Risk:Reward ratio."""
        trades = self._get_window(window)
        if not trades:
            return 0.0

        rrs = [t["achieved_rr"] for t in trades if t["achieved_rr"] > 0]
        if not rrs:
            return 0.0
        return round(sum(rrs) / len(rrs), 2)

    def get_total_pnl(self, window: Optional[int] = None) -> float:
        """Get total P&L over the window."""
        trades = self._get_window(window)
        return round(sum(t.get("profit", 0) for t in trades), 2)

    def get_max_drawdown(self) -> float:
        """Get the maximum drawdown percentage recorded."""
        return round(self._max_drawdown, 2)

    def get_streak(self) -> dict:
        """Get current winning/losing streak."""
        if not self._trades:
            return {"type": "none", "count": 0}

        streak_type = "win" if self._trades[-1]["is_win"] else "loss"
        count = 0

        for trade in reversed(list(self._trades)):
            if (trade["is_win"] and streak_type == "win") or \
               (not trade["is_win"] and streak_type == "loss"):
                count += 1
            else:
                break

        return {"type": streak_type, "count": count}

    def get_strategy_breakdown(self) -> dict:
        """Get performance breakdown by strategy."""
        breakdown = {}

        for trade in self._trades:
            strat = trade.get("strategy", "unknown")
            if strat not in breakdown:
                breakdown[strat] = {"trades": 0, "wins": 0, "pnl": 0.0}

            breakdown[strat]["trades"] += 1
            if trade["is_win"]:
                breakdown[strat]["wins"] += 1
            breakdown[strat]["pnl"] += trade.get("profit", 0)

        # Calculate win rates
        for strat in breakdown:
            if breakdown[strat]["trades"] > 0:
                breakdown[strat]["win_rate"] = round(
                    breakdown[strat]["wins"] / breakdown[strat]["trades"], 4
                )
            breakdown[strat]["pnl"] = round(breakdown[strat]["pnl"], 2)

        return breakdown

    # ── Full Summary ────────────────────────────────────────────────

    def get_summary(self) -> dict:
        """Get complete performance summary."""
        window = settings.performance_window
        return {
            "total_trades": len(self._trades),
            "win_rate": self.get_win_rate(window),
            "profit_factor": self.get_profit_factor(window),
            "average_rr": self.get_average_rr(window),
            "total_pnl": self.get_total_pnl(),
            "rolling_pnl": self.get_total_pnl(window),
            "max_drawdown": self.get_max_drawdown(),
            "streak": self.get_streak(),
            "strategy_breakdown": self.get_strategy_breakdown(),
            "daily_pnl": dict(list(self._daily_pnl.items())[-7:]),  # Last 7 days
            "alerts": self._alerts[-5:],  # Last 5 alerts
        }

    # ── Self-Optimization Triggers ──────────────────────────────────

    def _check_triggers(self):
        """Check if any optimization triggers should fire."""
        window = settings.performance_window
        trades = self._get_window(window)

        if len(trades) < window // 2:
            return  # Not enough data yet

        win_rate = self.get_win_rate(window)
        avg_rr = self.get_average_rr(window)

        # Trigger: Win rate too low
        if win_rate < settings.min_win_rate:
            alert = (
                f"⚠️ Win rate dropped to {win_rate*100:.1f}% "
                f"(threshold: {settings.min_win_rate*100}%)"
            )
            logger.warning(alert)
            self._alerts.append({"message": alert, "time": str(datetime.now()), "type": "win_rate"})

        # Trigger: Average R:R too low
        if avg_rr < settings.min_avg_rr:
            alert = (
                f"⚠️ Average R:R dropped to {avg_rr} "
                f"(threshold: {settings.min_avg_rr})"
            )
            logger.warning(alert)
            self._alerts.append({"message": alert, "time": str(datetime.now()), "type": "rr_ratio"})

        # Trigger: Profit factor below 1 (losing money)
        pf = self.get_profit_factor(window)
        if pf < 1.0 and len(trades) >= window:
            alert = f"🚨 Profit factor below 1.0 ({pf}) — bot is losing money!"
            logger.critical(alert)
            self._alerts.append({"message": alert, "time": str(datetime.now()), "type": "profit_factor"})

    def should_pause(self) -> bool:
        """Check if bot should auto-pause based on performance."""
        window = settings.performance_window
        trades = self._get_window(window)

        if len(trades) < window // 2:
            return False

        win_rate = self.get_win_rate(window)
        pf = self.get_profit_factor(window)

        # Pause if win rate is dangerously low AND profit factor is below 1
        return win_rate < settings.min_win_rate and pf < 1.0

    # ── Helpers ─────────────────────────────────────────────────────

    def _get_window(self, window: Optional[int] = None) -> List[dict]:
        """Get trades within the specified window."""
        if window is None:
            return list(self._trades)
        return list(self._trades)[-window:]

    def update_drawdown(self, current_balance: float):
        """Update max drawdown tracking."""
        if current_balance > self._peak_balance:
            self._peak_balance = current_balance

        if self._peak_balance > 0:
            dd = (self._peak_balance - current_balance) / self._peak_balance
            if dd > self._max_drawdown:
                self._max_drawdown = dd
