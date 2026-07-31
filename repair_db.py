#!/usr/bin/env python3
"""
P&L Database Repair Script — Genesis Trading Bot
=================================================

Cross-references every closed trade in the bot's SQLite database against
MetaTrader 5's actual deal history and overwrites corrupted profit values
with MT5's ground-truth numbers.

Why this is needed
------------------
If the bot ran previous code that calculated P&L manually (e.g. with
inverted formulas or incorrect contract-size multipliers), those wrong
values were persisted in the database. The dashboard faithfully displays
whatever is stored — garbage in, garbage out.

This script reads the real profit from MT5's deal history (which is the
only authoritative source) and fixes the database to match.

Usage
-----
    1. ⚠️  **STOP the bot engine** first (CTRL+C in the terminal, or click
        "Pause" in the dashboard). A running bot holds an open SQLite
        connection and will cause locking conflicts.
    2. Make sure MetaTrader 5 is running and logged into the correct account.
    3. Run::

        python repair_db.py

    4. Review the mismatches shown.
    5. Type "yes" to apply fixes or "no" for a dry-run (just reporting).

The script uses the same database path as the bot's default configuration
(``database/trades.db`` relative to the project root). If your bot uses a
custom path, pass it via ``--db-path`` or ``--help`` for all options.
"""

import argparse
import sqlite3

from collections import defaultdict
from pathlib import Path
from datetime import datetime, timedelta

# Lazy import — MT5 may not be installed outside production environments.
# Import happens inside the function so --help still works without MT5.
# MetaTrader5 as mt5  # imported inside repair_database_pnl()


# ── Configuration ──────────────────────────────────────────────────────

# Default database path (resolved relative to this script's location)
DEFAULT_DB_PATH = Path(__file__).resolve().parent / "database" / "trades.db"

# Profit mismatch tolerance in dollars — values differing by less than this
# are considered already correct and left untouched.
TOLERANCE = 0.05

# Default history window: how many days back to search MT5 deal history.
# 7 days covers most situations; older trades need a wider window.
DEFAULT_HISTORY_DAYS = 7


# ── Argparse ───────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(
        description="Repair corrupted P&L values in the Genesis bot database "
                    "by cross-referencing against MT5 deal history.",
    )
    parser.add_argument(
        "--db-path",
        type=str,
        default=str(DEFAULT_DB_PATH),
        help=f"Path to the SQLite database file (default: {DEFAULT_DB_PATH})",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=DEFAULT_HISTORY_DAYS,
        help=f"How many days back to search MT5 deal history "
             f"(default: {DEFAULT_HISTORY_DAYS})",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Auto-confirm fixes without prompting",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show every profit value, not just mismatches",
    )
    return parser.parse_args()


# ── Core Logic ────────────────────────────────────────────────────────

def repair_database_pnl(
    db_path: str,
    auto_yes: bool = False,
    verbose: bool = False,
    history_days: int = DEFAULT_HISTORY_DAYS,
):
    """
    Main repair routine.

    1. Initialises MT5 (blocking, synchronous — this is a standalone script).
    2. Opens the bot's SQLite database.
    3. For every trade record, fetches the real deal history from MT5.
    4. Compares DB profit vs MT5 profit and updates mismatches.
    """
    import MetaTrader5 as mt5

    # ── Safety: warn the user to stop the bot first ───────────────────
    print()
    print("⚠️  IMPORTANT: STOP THE BOT ENGINE BEFORE RUNNING THIS SCRIPT")
    print("   " + "─" * 65)
    print("   The bot keeps an open database connection at all times.")
    print("   Running this script while the bot is active can cause:")
    print("   • SQLite locking errors")
    print("   • Silent race conditions between the repair and live writes")
    print("   • Data corruption")
    print()
    print("   Steps:")
    print("   1. Stop the bot in the dashboard or press CTRL+C in its terminal")
    print("   2. Wait 5 seconds")
    print("   3. Run this script")
    print()
    if not auto_yes:
        confirm = input("Continue? [y/N] ").strip().lower()
        if confirm not in ("y", "yes"):
            print("❌ Aborted by user.")
            return
    else:
        print("   (--yes flag set, proceeding automatically)")
    print()

    db_file = Path(db_path)
    if not db_file.is_file():
        print(f"❌ Database file not found: {db_file}")
        print(f"   Expected path: {db_file.resolve()}")
        print(f"   Make sure the bot has run at least once to create it.")
        return

    # ── Step 1: Initialise MT5 ────────────────────────────────────────
    print("🔌 Connecting to MetaTrader 5...")
    if not mt5.initialize():
        last_err = mt5.last_error()
        if isinstance(last_err, tuple):
            code, desc = last_err[0], last_err[1]
            print(f"❌ Failed to initialise MT5: error {code} — {desc}")
        else:
            print(f"❌ Failed to initialise MT5: {last_err}")
        print("   Make sure MT5 is running and you're logged in.")
        return
    print("✅ MT5 connected.")

    try:
        # ── Step 2: Connect to the bot's SQLite database ──────────────
        print(f"📂 Opening database: {db_file.resolve()}")
        conn = sqlite3.connect(str(db_file))
        cursor = conn.cursor()

        # ── Step 3: Fetch all trades ──────────────────────────────────
        cursor.execute(
            "SELECT id, ticket, symbol, direction, profit, swap, status "
            "FROM trade_logs ORDER BY id"
        )
        all_trades = cursor.fetchall()
        print(f"🔍 Found {len(all_trades)} trade record(s) in database.\n")

        if not all_trades:
            print("Nothing to repair — database is empty.")
            return

        # Separate open vs closed for reporting
        open_trades = [t for t in all_trades if t[6] == "open"]
        closed_trades = [t for t in all_trades if t[6] == "closed"]

        if open_trades:
            print(f"   ⏳ {len(open_trades)} open trade(s) — skipping "
                  f"(open positions don't have deal history yet).")
        if not closed_trades:
            print("   No closed trades to repair. Exiting.")
            return

        print(f"   🔎 Inspecting {len(closed_trades)} closed trade(s)...\n")

        # ── Step 4: Check each trade against MT5 deal history ─────────
        mismatches = []
        fixed_count = 0
        skip_count = 0
        error_count = 0

        # ── Pre-fetch: get ALL deals in the history window once ────
        # IMPORTANT: mt5.history_deals_get(date_from, date_to, position=X)
        # silently IGNORES the position filter on some MT5 builds.
        # To work around this, we fetch ALL deals in the date range once
        # and then filter by position ticket in Python.
        print(f"   📡 Fetching all deals from the last {history_days} day(s)...")
        history_from = datetime.now() - timedelta(days=history_days)
        history_to = datetime.now() + timedelta(days=1)

        try:
            all_deals_raw = mt5.history_deals_get(
                int(history_from.timestamp()),
                int(history_to.timestamp()),
            )
        except Exception as e:
            print(f"   ❌ Failed to fetch deal history: {e}")
            return

        if all_deals_raw is None or len(all_deals_raw) == 0:
            print("   ⚠️  No deals found in history window. Try --days to widen.")
            return

        # Index deals by position ticket for fast lookup
        from collections import defaultdict
        deals_by_position: dict[int, list] = defaultdict(list)
        for d in all_deals_raw:
            pos_id = getattr(d, "position", None) or getattr(d, "position_id", None)
            if pos_id:
                deals_by_position[pos_id].append(d)

        print(f"   📊 Found {len(all_deals_raw)} total deal(s) across "
              f"{len(deals_by_position)} position(s).\n")

        for row in closed_trades:
            trade_id, ticket, symbol, direction, db_profit, db_swap, status = row

            deals = deals_by_position.get(ticket)

            # Guard: skip if no deals found for this ticket
            if not deals:
                if verbose:
                    print(f"   ⏭️  Ticket {ticket} ({symbol}): no deal history "
                          f"found in the {history_days}-day window.")
                skip_count += 1
                continue

            # Calculate real P&L from MT5 deal history (matching the
            # orchestrator's _extract_exit_info logic exactly).
            real_profit = 0.0
            real_swap = 0.0
            has_out_deal = False

            for deal in deals:
                # Only DEAL_ENTRY_OUT deals represent the closing leg.
                # Use getattr for consistency with the orchestrator's
                # _extract_exit_info() — belts and suspenders.
                if getattr(deal, "entry", None) == mt5.DEAL_ENTRY_OUT:
                    has_out_deal = True
                    real_profit += deal.profit
                    real_swap += deal.swap
                    # Include commission (ECN accounts charge per-lot)
                    commission = getattr(deal, "commission", 0.0) or 0.0
                    real_profit += commission

            if not has_out_deal:
                # No exit deal found for this ticket — possible if the
                # deal is still open, or the history window is too narrow.
                if verbose:
                    print(f"   ⏭️  Ticket {ticket} ({symbol}): no DEAL_ENTRY_OUT "
                          f"found (position may still be open in MT5).")
                skip_count += 1
                continue

            # Round for comparison (database stores rounded values)
            real_profit = round(real_profit, 2)
            real_swap = round(real_swap, 2)

            # Check if profit needs fixing
            db_profit_float = round(db_profit, 2) if db_profit is not None else 0.0
            db_swap_float = round(db_swap, 2) if db_swap is not None else 0.0

            profit_diff = abs(db_profit_float - real_profit)
            swap_diff = abs(db_swap_float - real_swap)

            if profit_diff > TOLERANCE or swap_diff > TOLERANCE:
                mismatches.append((ticket, symbol, direction,
                                   db_profit_float, real_profit,
                                   db_swap_float, real_swap))
            elif verbose:
                print(f"   ✅ Ticket {ticket} ({symbol}): "
                      f"DB=${db_profit_float:.2f} | MT5=${real_profit:.2f} "
                      f"(match within ${TOLERANCE:.2f})")

        # ── Step 5: Report mismatches ─────────────────────────────────
        if not mismatches:
            print("\n✨ All closed trades match MT5 deal history! Nothing to fix.")
            return

        print(f"\n{'='*70}")
        print(f"⚠️  FOUND {len(mismatches)} MISMATCHE(S)")
        print(f"{'='*70}")
        print(f"{'Ticket':>8} {'Symbol':>8} {'Dir':>5} {'DB Profit':>12} "
              f"{'MT5 Profit':>12} {'Diff':>10} {'DB Swap':>10} {'MT5 Swap':>10}")
        print(f"{'-'*8:>8} {'-'*8:>8} {'-'*5:>5} {'-'*12:>12} "
              f"{'-'*12:>12} {'-'*10:>10} {'-'*10:>10} {'-'*10:>10}")

        for t in mismatches:
            ticket, symbol, direction, db_p, real_p, db_s, real_s = t
            diff = real_p - db_p
            diff_str = f"{diff:+9.2f}"
            print(f"{ticket:>8} {symbol:>8} {direction.upper():>5} "
                  f"${db_p:>9.2f}  ${real_p:>9.2f}  {diff_str}  "
                  f"${db_s:>7.2f}  ${real_s:>7.2f}")

        print()

        # ── Step 6: Apply fixes ──────────────────────────────────────
        if auto_yes:
            apply_fixes = True
        else:
            resp = input(f"Apply fixes for all {len(mismatches)} mismatche(s)? "
                         f"[y/N] ").strip().lower()
            apply_fixes = resp in ("y", "yes")

        if not apply_fixes:
            print("❌ Fixes NOT applied (dry-run mode).")
            print("   Re-run with --yes or type 'y' to commit fixes.")
            return

        print(f"\n🛠️  Applying fixes...")
        for t in mismatches:
            ticket, symbol, direction, db_p, real_p, db_s, real_s = t
            try:
                cursor.execute(
                    "UPDATE trade_logs SET profit = ?, swap = ? WHERE ticket = ?",
                    (real_p, real_s, ticket),
                )
                fixed_count += 1
                print(f"   ✅ Ticket {ticket} ({symbol}): "
                      f"profit ${db_p:.2f} → ${real_p:.2f}, "
                      f"swap ${db_s:.2f} → ${real_s:.2f}")
            except Exception as e:
                print(f"   ❌ Failed to update ticket {ticket}: {e}")
                error_count += 1

        # ── Step 7: Commit & report ──────────────────────────────────
        conn.commit()
        print(f"\n{'='*70}")
        print(f"✅ REPAIR COMPLETE")
        print(f"{'='*70}")
        print(f"   Total records inspected:  {len(closed_trades)}")
        print(f"   Mismatches found:          {len(mismatches)}")
        print(f"   Fixed:                     {fixed_count}")
        print(f"   Skipped (no history):      {skip_count}")
        print(f"   Errors:                    {error_count}")
        print(f"\n🔄 Refresh your web dashboard to see updated numbers.")

    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if 'conn' in locals():
            conn.close()
        mt5.shutdown()
        print("🔌 MT5 disconnected.")


# ── Entry Point ───────────────────────────────────────────────────────

if __name__ == "__main__":
    args = parse_args()
    repair_database_pnl(
        db_path=args.db_path,
        auto_yes=args.yes,
        verbose=args.verbose,
        history_days=args.days,
    )
