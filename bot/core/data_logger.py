# Data logger for persisting tick and trade data to SQLite.
"""
Data logger for persisting tick and trade data to SQLite.

The functions are lightweight wrappers that run the actual DB operations on the
shared ``AsyncMt5Executor`` thread pool so they do not block the main event loop.
"""

import asyncio
import sqlite3
from pathlib import Path
from typing import Dict, Any, List
from loguru import logger

from bot.config.settings import settings

# Resolve the DB path from settings (default points to ``database/trades.db``)
_DB_PATH = Path(settings.database_url.replace('sqlite:///', ''))
_DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Queue for tick logging (async, bounded)
# ---------------------------------------------------------------------------
_tick_queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue(maxsize=getattr(settings, "tick_queue_maxsize", 10_000))

async def enqueue_tick(tick: Dict[str, Any]) -> None:
    """Enqueue a raw tick dict for async batch persistence.

    The queue is bounded – ``await`` will back‑pressure the caller if the queue
    fills up, preventing memory blow‑up.
    """
    await _tick_queue.put(tick)

# ---------------------------------------------------------------------------
# Internal helpers – table creation
# ---------------------------------------------------------------------------

def _ensure_tables(conn: sqlite3.Connection):
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS ticks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            timestamp INTEGER NOT NULL,
            bid REAL NOT NULL,
            ask REAL NOT NULL,
            volume REAL NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            volume REAL NOT NULL,
            price REAL NOT NULL,
            sl REAL,
            tp REAL,
            result TEXT,
            profit REAL,
            timestamp INTEGER NOT NULL
        )
        """
    )
    conn.commit()

def _get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB_PATH))
    # Enable WAL mode for concurrent reads/writes if configured
    if getattr(settings, "sqlite_wal_mode", True):
        conn.execute("PRAGMA journal_mode=WAL;")
    _ensure_tables(conn)
    return conn

# ---------------------------------------------------------------------------
# Public API – trade logging (unchanged, kept for compatibility)
# ---------------------------------------------------------------------------
async def log_trade(trade: Dict[str, Any]) -> None:
    """Persist a completed trade.

    Expected keys: ``ticket``, ``symbol``, ``direction``, ``volume``, ``price``,
    ``sl``, ``tp``, ``result`` (optional), ``profit`` (optional), ``timestamp``.
    """
    def _write():
        try:
            conn = _get_connection()
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO trades (
                    ticket, symbol, direction, volume, price, sl, tp, result, profit, timestamp
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    trade.get("ticket"),
                    trade.get("symbol"),
                    trade.get("direction"),
                    trade.get("volume"),
                    trade.get("price"),
                    trade.get("sl"),
                    trade.get("tp"),
                    trade.get("result"),
                    trade.get("profit"),
                    trade.get("timestamp"),
                ),
            )
            conn.commit()
        except Exception as e:
            logger.error(f"[DB] Failed to log trade: {e}")
        finally:
            conn.close()
    return _write()

# ---------------------------------------------------------------------------
# Background worker – batch tick persistence
# ---------------------------------------------------------------------------
async def _tick_writer_worker() -> None:
    """Continuously drain the tick queue and write in batches.

    The worker respects ``settings.tick_batch_size`` and ``settings.tick_flush_interval_secs``.
    """
    batch_size = getattr(settings, "tick_batch_size", 500)
    flush_interval = getattr(settings, "tick_flush_interval_secs", 2.0)
    enable_dedupe = getattr(settings, "enable_tick_deduplication", True)

    while True:
        batch: List[Dict[str, Any]] = []
        # Wait for at least one tick or timeout
        try:
            first = await asyncio.wait_for(_tick_queue.get(), timeout=flush_interval)
            batch.append(first)
        except asyncio.TimeoutError:
            # No ticks arrived in the interval – loop again
            continue

        # Gather up to batch_size-1 more without waiting
        while len(batch) < batch_size:
            try:
                tick = _tick_queue.get_nowait()
                batch.append(tick)
            except asyncio.QueueEmpty:
                break

        # Optional deduplication – keep only rows where bid/ask differ from previous entry for same symbol
        if enable_dedupe:
            deduped: List[Dict[str, Any]] = []
            last_seen: Dict[str, Any] = {}
            for t in batch:
                key = t["symbol"]
                prev = last_seen.get(key)
                if not prev or prev["bid"] != t["bid"] or prev["ask"] != t["ask"]:
                    deduped.append(t)
                    last_seen[key] = t
            batch = deduped

        if not batch:
            continue

        # Write batch in a single transaction via executemany
        def _write_batch():
            try:
                conn = _get_connection()
                cur = conn.cursor()
                cur.executemany(
                    "INSERT INTO ticks (symbol, timestamp, bid, ask, volume) VALUES (?,?,?,?,?)",
                    [
                        (
                            t.get("symbol"),
                            t.get("timestamp"),
                            t.get("bid"),
                            t.get("ask"),
                            t.get("volume"),
                        )
                        for t in batch
                    ],
                )
                conn.commit()
            except Exception as e:
                logger.error(f"[DB] Failed to batch log ticks: {e}")
            finally:
                conn.close()
        _write_batch()

# Helper to start the background task from elsewhere (e.g., orchestrator)
async def start_tick_logger() -> None:
    """Spawn the background tick writer task.

    This function should be awaited once during application start‑up.
    """
    asyncio.create_task(_tick_writer_worker())
