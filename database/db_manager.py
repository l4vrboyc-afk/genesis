"""
Database Manager — Handles asynchronous CRUD operations for the bot.
Integrates with SQLite using SQLAlchemy 2.0 and aiosqlite.
"""

import os
from datetime import datetime
from typing import List, Optional
from loguru import logger

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select, update, desc

from bot.config.settings import settings
from database.models import Base, TradeLog, DailyPerformance, BotState


class DatabaseManager:
    """Manages database connection lifecycle and async queries."""

    def __init__(self):
        # Adapt database URL for async SQLite if needed
        db_url = settings.database_url
        if db_url.startswith("sqlite:///"):
            # Ensure the database directory exists
            db_path = db_url.replace("sqlite:///", "")
            db_dir = os.path.dirname(db_path)
            if db_dir and not os.path.exists(db_dir):
                os.makedirs(db_dir, exist_ok=True)
                logger.info(f"📁 Created database directory: {db_dir}")

            db_url = db_url.replace("sqlite:///", "sqlite+aiosqlite:///")

        self.engine = create_async_engine(db_url, echo=False)
        self.async_session = sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )
        # Snapshot-only-once-per-day state for daily performance rows
        self._last_snapshot_date: Optional[str] = None
        logger.info(f"🗄️ Database engine initialized: {db_url}")

    async def init_db(self):
        """Create all database tables and apply lightweight column-add migrations."""
        try:
            from sqlalchemy import text
            async with self.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
                # Lightweight schema migration for pre-existing DBs — each ALTER
                # is wrapped in try/except since SQLite raises on duplicate cols.
                migration_stmts = [
                    "ALTER TABLE trade_logs ADD COLUMN entry_comment VARCHAR(200)",
                    "ALTER TABLE trade_logs ADD COLUMN close_comment VARCHAR(200)",
                    "ALTER TABLE trade_logs ADD COLUMN position_value_usd FLOAT DEFAULT 0.0",
                    "ALTER TABLE trade_logs ADD COLUMN return_r FLOAT DEFAULT 0.0",
                ]
                for stmt in migration_stmts:
                    try:
                        await conn.execute(text(stmt))
                        logger.info(f"🔧 Migration applied: {stmt}")
                    except Exception:
                        pass  # Column already exists — expected on fresh DBs

            logger.success("✅ Database tables initialized successfully")
        except Exception as e:
            logger.critical(f"❌ Failed to initialize database: {e}")
            raise e

    # ── Trade Logging ────────────────────────────────────────────────

    async def record_trade_complete(
        self,
        ticket: int,
        symbol: str,
        direction: str,
        volume: float,
        entry_price: float,
        exit_price: float,
        profit: float,
        sl: float,
        tp: float,
        strategy: str = "",
        regime: str = "",
        entry_comment: str = "",
        close_comment: str = "",
        swap: float = 0.0,
        position_value_usd: float = 0.0,
        return_r: float = 0.0,
    ) -> Optional[TradeLog]:
        """Atomically record a completed trade (open + close in one transaction).

        Fix #3: Creates both the open and close record in a single DB
        transaction, eliminating the orphaned-open-position race that exists
        with the two-step ``record_trade_open`` + ``record_trade_close``
        pattern. If the bot crashes after record_trade_open but before
        record_trade_close, there's no orphan.

        Returns the TradeLog with status='closed'.
        """
        async with self.async_session() as session:
            try:
                now = datetime.utcnow()
                trade = TradeLog(
                    ticket=ticket,
                    symbol=symbol,
                    direction=direction.lower(),
                    volume=volume,
                    entry_price=entry_price,
                    exit_price=exit_price,
                    profit=profit,
                    swap=swap,
                    sl=sl,
                    tp=tp,
                    strategy=strategy,
                    market_regime=regime,
                    entry_comment=entry_comment,
                    close_comment=close_comment or "Closed",
                    comment=close_comment or entry_comment or "Closed",
                    open_time=now,
                    close_time=now,
                    status="closed",
                    position_value_usd=position_value_usd,
                    return_r=return_r,
                )
                session.add(trade)
                await session.commit()
                await session.refresh(trade)
                logger.debug(f"💾 Trade saved atomically: ticket={ticket} (open+closed)")
                return trade
            except Exception as e:
                logger.error(f"❌ Failed to record trade atomically: {e}")
                await session.rollback()
                return None

    async def record_trade_open(
        self,
        ticket: int,
        symbol: str,
        direction: str,
        volume: float,
        entry_price: float,
        sl: float,
        tp: float,
        strategy: str,
        regime: str,
        comment: str = "",
    ) -> Optional[TradeLog]:
        """Record a newly opened position in the database."""
        async with self.async_session() as session:
            try:
                trade = TradeLog(
                    ticket=ticket,
                    symbol=symbol,
                    direction=direction.lower(),
                    volume=volume,
                    entry_price=entry_price,
                    sl=sl,
                    tp=tp,
                    strategy=strategy,
                    market_regime=regime,
                    entry_comment=comment,
                    comment=comment,  # Legacy column — keep in sync
                    open_time=datetime.utcnow(),
                    status="open",
                )
                session.add(trade)
                await session.commit()
                await session.refresh(trade)
                logger.debug(f"💾 Trade saved to DB: ticket={ticket} (open)")
                return trade
            except Exception as e:
                logger.error(f"❌ Failed to record trade open in DB: {e}")
                await session.rollback()
                return None

    async def record_trade_close(
        self,
        ticket: int,
        exit_price: float,
        profit: float,
        swap: float = 0.0,
        comment: str = "",
        position_value_usd: Optional[float] = None,
        return_r: Optional[float] = None,
    ) -> Optional[TradeLog]:
        """Update a trade record when it is closed. Stores `comment` in the
        dedicated close_comment column — entry_comment is preserved as-is."""
        async with self.async_session() as session:
            try:
                # Find the open trade
                stmt = select(TradeLog).where(TradeLog.ticket == ticket)
                result = await session.execute(stmt)
                trade = result.scalar_one_or_none()

                if not trade:
                    logger.warning(f"⚠️ Trade with ticket {ticket} not found in DB to close")
                    return None

                trade.exit_price = exit_price
                trade.profit = profit
                trade.swap = swap
                trade.close_time = datetime.utcnow()
                trade.status = "closed"
                if position_value_usd is not None:
                    trade.position_value_usd = position_value_usd
                if return_r is not None:
                    trade.return_r = return_r
                if comment:
                    trade.close_comment = comment
                    # Legacy mirror for older UI surfaces; to_dict prefers close_comment anyway.
                    trade.comment = comment

                await session.commit()
                await session.refresh(trade)
                logger.debug(f"💾 Trade updated in DB: ticket={ticket} (closed) | Profit={profit}")
                return trade
            except Exception as e:
                logger.error(f"❌ Failed to record trade close in DB: {e}")
                await session.rollback()
                return None

    async def get_open_trades(self) -> List[TradeLog]:
        """Fetch all currently open trades from database."""
        async with self.async_session() as session:
            stmt = select(TradeLog).where(TradeLog.status == "open")
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def get_trades(
        self, limit: int = 100, offset: int = 0, status: Optional[str] = None
    ) -> List[TradeLog]:
        """Fetch historical trades with optional status filtering."""
        async with self.async_session() as session:
            stmt = select(TradeLog)
            if status:
                stmt = stmt.where(TradeLog.status == status)
            stmt = stmt.order_by(desc(TradeLog.open_time)).limit(limit).offset(offset)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    # ── Daily Performance Logging ────────────────────────────────────

    async def record_daily_performance(
        self,
        balance: float,
        equity: float,
        pnl: float,
        drawdown: float,
        win_rate: float,
        trade_count: int,
    ) -> Optional[DailyPerformance]:
        """
        Save or update today's performance stats.

        Rule: only the FIRST write of a UTC day is persisted — subsequent polls
        within the same day are no-ops. This ensures the row reflects the
        snapshot-of-record for the day, not the most recent mid-day drift.

        `_last_snapshot_date` is in-process only, so a bot restart within the
        same UTC day still triggers an update on the first cycle (the flag
        resets to None at startup).
        """
        today_str = datetime.now().strftime("%Y-%m-%d")
        if self._last_snapshot_date == today_str:
            # Already snapshotted this UTC day — skip
            return None
        self._last_snapshot_date = today_str

        async with self.async_session() as session:
            try:
                stmt = select(DailyPerformance).where(DailyPerformance.date == today_str)
                result = await session.execute(stmt)
                perf = result.scalar_one_or_none()

                if perf:
                    perf.balance = balance
                    perf.equity = equity
                    perf.pnl = pnl
                    perf.drawdown = drawdown
                    perf.win_rate = win_rate
                    perf.trade_count = trade_count
                else:
                    perf = DailyPerformance(
                        date=today_str,
                        balance=balance,
                        equity=equity,
                        pnl=pnl,
                        drawdown=drawdown,
                        win_rate=win_rate,
                        trade_count=trade_count,
                    )
                    session.add(perf)

                await session.commit()
                await session.refresh(perf)
                logger.info(f"📊 Daily performance snapshot recorded for {today_str}")
                return perf
            except Exception as e:
                logger.error(f"❌ Failed to save daily performance: {e}")
                await session.rollback()
                return None

    async def get_daily_performance_history(self, limit: int = 30) -> List[DailyPerformance]:
        """Get history of daily performance records, always sorted chronologically (ASC).

        The ORDER BY date ASC is applied at the SQL level. A Python-level sort
        is added as a belt-and-suspenders guard: if rows were ever inserted
        out-of-order (e.g. after a clock correction) the chart's cumulative
        P&L would plot a false dip before recovering — Fix #3.
        """
        async with self.async_session() as session:
            stmt = (
                select(DailyPerformance)
                .order_by(DailyPerformance.date)  # ASC — chronological for chart
                .limit(limit)
            )
            result = await session.execute(stmt)
            rows = list(result.scalars().all())
            # Secondary Python sort ensures chronological order even if DB index
            # returns rows in insertion order (e.g. after back-dated corrections).
            rows.sort(key=lambda r: r.date)
            return rows

    # ── Bot State Persistence ────────────────────────────────────────

    async def set_state(self, key: str, value: str):
        """Set a persistent state variable."""
        async with self.async_session() as session:
            try:
                stmt = select(BotState).where(BotState.key == key)
                result = await session.execute(stmt)
                state = result.scalar_one_or_none()

                if state:
                    state.value = value
                else:
                    state = BotState(key=key, value=value)
                    session.add(state)

                await session.commit()
                logger.debug(f"💾 Saved bot state: {key}={value}")
            except Exception as e:
                logger.error(f"❌ Failed to set state {key}: {e}")
                await session.rollback()

    async def get_state(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Get a persistent state variable."""
        async with self.async_session() as session:
            stmt = select(BotState).where(BotState.key == key)
            result = await session.execute(stmt)
            state = result.scalar_one_or_none()
            return state.value if state else default
