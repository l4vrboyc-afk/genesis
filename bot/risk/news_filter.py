"""
News Filter — Economic calendar integration for pausing during high-impact events.
Uses MT5's built-in calendar or external fallback.
"""

import asyncio
from datetime import datetime, timedelta
from typing import List, Optional
from loguru import logger

from bot.config.settings import settings


class NewsFilter:
    """
    Filters trading during high-impact economic events.

    Logic:
    - Fetches upcoming events from MT5 economic calendar
    - Creates no-trade windows around high-impact events
    - Checks currency relevance (USD news only affects USD pairs)
    """

    def __init__(self):
        self._cached_events = []
        self._last_refresh = None
        self._no_trade_pairs = set()  # Currently blocked pairs

        # Fix #7: Track which source provided the last calendar refresh
        # ("live_mt5" for MT5 native calendar, "fallback" for recurring
        # events schedule). Used by /api/news to indicate calendar reliability.
        self._last_news_source: str = "unknown"

        # Track (d) — Injected by ``TradingOrchestrator.start``. When set,
        # ``refresh_calendar_async`` hops the (potentially-heavy)
        # ``mt5.calendar_value_history`` call onto the executor's single
        # worker thread, so the news read path joins the rest of the bot
        # under the AsyncMt5Executor serialization contract. ``None`` is
        # fine for the unit-test / pre-attach window — it keeps the legacy
        # ``asyncio.to_thread`` path.
        self._executor = None

        # Currency mapping for pairs
        self._pair_currencies = {
            "EURUSD": ["EUR", "USD"],
            "GBPUSD": ["GBP", "USD"],
            "USDJPY": ["USD", "JPY"],
            "AUDUSD": ["AUD", "USD"],
            "USDCAD": ["USD", "CAD"],
            "EURGBP": ["EUR", "GBP"],
            "GBPJPY": ["GBP", "JPY"],
            "NZDUSD": ["NZD", "USD"],
            "USDCHF": ["USD", "CHF"],
            "EURJPY": ["EUR", "JPY"],
        }

    def refresh_calendar(self):
        """
        Refresh the economic calendar cache.
        Tries MT5 native calendar first, falls back to manual schedule.
        """
        try:
            import MetaTrader5 as mt5

            # Fetch events for the next 7 days (168 hours)
            now = datetime.now()
            future = now + timedelta(hours=168)

            # MT5 calendar function (available in MT5 build 2755+)
            events = mt5.calendar_value_history(
                int(now.timestamp()),
                int(future.timestamp()),
            )

            if events is not None and len(events) > 0:
                self._cached_events = self._parse_mt5_events(events)
                self._last_refresh = now
                # Fix #7: Mark source as live MT5 calendar
                self._last_news_source = "live_mt5"
                logger.info(
                    f"📅 Calendar refreshed (live MT5): {len(self._cached_events)} high-impact events"
                )
                return

        except (AttributeError, Exception) as e:
            logger.debug(f"MT5 calendar not available: {e}")

        # Fallback: use a known schedule of recurring events.
        # Track (d) — renamed from ``_get_recurring_events`` to
        # ``_fallback_recurring_events`` with a leading docstring noting
        # this is an APPROXIMATE fallback (CPI retail, RBA, MPI etc.
        # are NOT covered). If you need full event coverage, configure
        # ``news_calendar_refresh_interval`` aggressively and unblock
        # the native MT5 calendar path.
        self._cached_events = self._fallback_recurring_events()
        self._last_refresh = datetime.now()
        # Fix #7: Mark source as fallback schedule
        if self._cached_events:
            self._last_news_source = "fallback"
            logger.info(f"📅 Using recurring event schedule as fallback ({len(self._cached_events)} events)")
        else:
            self._last_news_source = "empty"
            logger.info("📅 Fallback calendar returned no events — news filter inactive")

    def attach_executor(self, executor) -> None:
        """Inject the serialised MT5 executor (see ``RiskManager.attach_executor``)."""
        self._executor = executor

    async def refresh_calendar_async(self) -> None:
        """Async wrapper — runs the synchronous MT5 calendar call on a worker thread.

        `can_trade` (sync) is fine on the hot path because `refresh_calendar` is
        internally throttled to once per `news_calendar_refresh_interval` (default
        3600 s). Async callers should prefer this wrapper for cleanliness.

        When an AsyncMt5Executor is attached (production), the native
        ``mt5.calendar_value_history`` call is queued onto that executor
        so the news refresh joins the rest of the bot under one
        serialization lock. Otherwise we fall back to ``asyncio.to_thread``
        so unit tests + cold-start paths keep working.
        """
        if self._executor is not None:
            await self._executor.submit(self.refresh_calendar)
        else:
            await asyncio.to_thread(self.refresh_calendar)

    def _parse_mt5_events(self, raw_events) -> List[dict]:
        """Parse MT5 calendar events, keeping only high-impact ones."""
        high_impact = []

        for event in raw_events:
            try:
                # Impact levels: 0=None, 1=Low, 2=Medium, 3=High
                if hasattr(event, 'impact_type') and event.impact_type >= 3:
                    high_impact.append({
                        "currency": event.currency_code if hasattr(event, 'currency_code') else "",
                        "name": event.event_name if hasattr(event, 'event_name') else "Unknown",
                        "time": datetime.fromtimestamp(event.time),
                        "impact": "high",
                    })
            except Exception:
                continue

        return high_impact

    def _fallback_recurring_events(self) -> List[dict]:
        """
        Track (d) — APPROXIMATE recurring-events fallback for hosts where
        the MT5 calendar is unreachable. Schedule is weekday- + day-of-
        month-based; it captures the major USD/EUR/GBP rate decisions
        and the headline NFP / CPI prints but does NOT cover retail
        sales, manufacturing PMI, RBA / RBNZ / BOJ decisions, etc.

        This fallback is a SAFETY NET, not a calendar. If you trade
        around non-USD-pair rate decisions, set
        ``news_filter_fail_closed=True`` once your fallback and your
        pair list are aligned — until then the lenient default
        (``False``) means trades proceed on stale data. See ``docs/
        SETUP.md § 9.4`` for the news-related-failure troubleshooting.

        Produces at most a handful of plausible events for the next
        24h based on weekday-of-week and day-of-month heuristics.
        """
        # (name, currency, hour_utc, minute, weekday, day_match_fn)
        # weekday: 0=Mon ... 6=Sun. None means "any day".
        # day_match_fn(candidate_date) -> bool filters by day-of-month pattern.
        now = datetime.now()
        templates = [
            # NFP — first Friday of month (~13:30 UTC)
            ("NFP (Non-Farm Payrolls)", "USD", 13, 30, 4,
             lambda d: 1 <= d.day <= 7),
            # CPI — second week of month (~13:30 UTC)
            ("CPI (Consumer Price Index)", "USD", 13, 30, 2,
             lambda d: 8 <= d.day <= 14),
            # FOMC — Wednesdays in FOMC months (~19:00 UTC)
            ("FOMC Rate Decision", "USD", 19, 0, 2,
             lambda d: d.month in (1, 3, 5, 6, 7, 9, 11, 12)),
            # ECB Rate Decision — Thursdays, ~12:45 UTC
            ("ECB Rate Decision", "EUR", 12, 45, 3,
             lambda d: d.weekday() == 3),
            # BOE Rate Decision — Thursdays, ~12:00 UTC
            ("BOE Rate Decision", "GBP", 12, 0, 3,
             lambda d: True),
        ]

        events = []
        seen_keys = set()
        # Generate events for the next 7 days
        for day_offset in range(7):
            check_date = (now + timedelta(days=day_offset)).date()
            wd = check_date.weekday()
            for name, ccy, hh, mm, expected_wd, day_fn in templates:
                if wd != expected_wd:
                    continue
                if not day_fn(check_date):
                    continue
                event_dt = datetime.combine(check_date, datetime.min.time()).replace(
                    hour=hh, minute=mm
                )
                key = (name, ccy, event_dt)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                if now <= event_dt <= now + timedelta(hours=168):
                    events.append({
                        "name": name,
                        "currency": ccy,
                        "time": event_dt,
                        "impact": "high",
                    })

        return events

    # ── Global Tier-1 News Lockout ─────────────────────────────────

    def has_global_tier1_event_near(self, buffer_minutes: int | None = None) -> bool:
        """Check if a global market-moving event is within the buffer window.

        Tier-1 events (FOMC, NFP, US CPI, ECB/BOE rate decisions) affect
        ALL pairs — not just the event's home currency.  When True, the
        orchestrator should force NEWS_EVENT regime and halt all trading
        until the event window passes.

        Args:
            buffer_minutes: Buffer to use (defaults to
                ``settings.news_buffer_minutes_before``).

        Returns:
            True if a global Tier-1 event is within the buffer window.
        """
        if not settings.news_filter_enabled:
            return False

        if not self._cached_events:
            return False

        buffer = buffer_minutes or settings.news_buffer_minutes_before
        now = datetime.now()

        # Events whose currency indicates global market impact.
        # USD events (FOMC, NFP, CPI) and major central bank decisions
        # (ECB, BOE, BOJ) are classified as Tier-1.
        global_currencies = {"USD", "EUR", "GBP", "JPY"}

        for event in self._cached_events:
            if event.get("currency", "") not in global_currencies:
                continue
            event_time = event["time"]
            # Use the same buffer symmetrically for both sides of the event.
            window_start = event_time - timedelta(minutes=buffer)
            window_end = event_time + timedelta(minutes=buffer)
            if window_start <= now <= window_end:
                return True

        return False

    # ── Trading Filter ──────────────────────────────────────────────

    def can_trade(self, symbol: str) -> dict:
        """
        Check if trading is allowed for this symbol right now.

        Args:
            symbol: Trading pair (e.g., "EURUSD")

        Returns:
            dict with 'allowed' (bool), 'reason' (str), 'event' (dict or None)
        """
        if not settings.news_filter_enabled:
            return {"allowed": True, "reason": "News filter disabled", "event": None}

        # Calendar is now refreshed asynchronously by the orchestrator
        # or dashboard API before calling this method to avoid blocking.

        # Track (d) — fail-closed defence. When settings flag is on AND
        # the cache has nothing AND the last refresh was longer than
        # 2 × the configured refresh interval, we refuse trading rather
        # than allowing stale-data trading. Off by default — operators
        # opt in once they accept the trade-off.
        if settings.news_filter_fail_closed:
            age_s: Optional[float] = None
            if self._last_refresh is not None:
                age_s = (datetime.now() - self._last_refresh).total_seconds()
            if (age_s is None or age_s > 2 * settings.news_calendar_refresh_interval) \
                    and not self._cached_events:
                # Guard against ``None`` before formatting — the very first
                # cycle at bot start has no last_refresh and used to raise
                # TypeError here, silently defeating the fail-closed
                # path the operator enabled.
                age_str = "unknown" if age_s is None else f"{age_s:.0f}"
                logger.warning(
                    f"📰 News-filter fail-closed: cache is empty and no refresh "
                    f"in {age_str}s (> {2 * settings.news_calendar_refresh_interval}s)."
                )
                return {
                    "allowed": False,
                    "reason": (
                        "📰 News filter fail-closed — calendar cache is stale "
                        "(no events and no successful refresh within 2× the "
                        "configured refresh interval)"
                    ),
                    "event": None,
                }

        now = datetime.now()
        currencies = self._pair_currencies.get(symbol, [])

        for event in self._cached_events:
            # Check if this event affects our pair's currencies
            if event["currency"] not in currencies:
                continue

            event_time = event["time"]
            buffer_before = timedelta(minutes=settings.news_buffer_minutes_before)
            buffer_after = timedelta(minutes=settings.news_buffer_minutes_after)

            # Check if we're in the no-trade window
            window_start = event_time - buffer_before
            window_end = event_time + buffer_after

            if window_start <= now <= window_end:
                return {
                    "allowed": False,
                    "reason": (
                        f"📰 {event['name']} ({event['currency']}) "
                        f"at {event_time.strftime('%H:%M')} — "
                        f"no-trade window until {window_end.strftime('%H:%M')}"
                    ),
                    "event": event,
                }

        return {"allowed": True, "reason": "No high-impact news", "event": None}

    def get_upcoming_events(self, hours: int = 24) -> List[dict]:
        """Get list of upcoming high-impact events."""
        # Note: calendar refresh is handled externally via refresh_calendar_async()
        if self._last_refresh is None:
            return []

        now = datetime.now()
        cutoff = now + timedelta(hours=hours)

        return [
            e for e in self._cached_events
            if now <= e["time"] <= cutoff
        ]
