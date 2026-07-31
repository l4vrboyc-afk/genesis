# P&L Database Repair Script — `repair_db.py`

Cross-references every closed trade in the bot's SQLite database against
MetaTrader 5's actual deal history and overwrites corrupted profit values
with MT5's ground-truth numbers.

---

## Why This Exists

### The Problem

The bot's dashboard displays P&L from the `trade_logs` table in the SQLite
database. If a bug in the bot's code (or a previous version's code) stored
incorrectly calculated profit values — such as an **inverted BUY formula**
where `(entry - exit) × lots × contract_size` was stored instead of
`(exit - entry) × lots × contract_size` — the database would contain
**garbage numbers** that the dashboard faithfully renders.

### The Solution

**MT5 is the only authoritative source of truth for P&L.** Every trade's
real profit, swap, and commission is recorded in MT5's deal history when
the position closes. This script:

1. Reads all closed trades from the bot's SQLite database
2. For each trade, fetches the corresponding **DEAL_ENTRY_OUT** deals from
   MT5 history (the closing leg of the position)
3. Accumulates `deal.profit + deal.swap + deal.commission` — matching the
   orchestrator's `_extract_exit_info()` logic exactly
4. Compares the MT5 values against the stored DB values
5. Fixes any mismatches exceeding \$0.05 tolerance

### Runtime Detection

Since deploying this fix, the orchestrator also has **live PNL_MISMATCH
warnings**. When `_check_closed_positions()` detects a closed trade, it
compares the fresh MT5 profit against the old DB value and logs:

```
⚠️ PNL_MISMATCH Ticket 9999 (EURUSD): DB profit=$-260.10 ≠ MT5 profit=$253.10
   (diff=$+513.20). DB data is stale — will overwrite with MT5 value.
```

This means new P&L corruption is caught immediately on the next trading
cycle, not discovered weeks later in the dashboard.

---

## Usage

### Prerequisites

- **MetaTrader 5** must be running and logged into the correct account
- **The bot must be stopped** — a running bot holds an open SQLite
  connection that can cause locking conflicts

### Basic Usage

```bash
# Dry-run mode: shows mismatches without modifying anything
python repair_db.py

# Apply fixes automatically
python repair_db.py --yes

# Verbose: show every trade, not just mismatches
python repair_db.py --verbose --yes
```

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `--db-path` | `database/trades.db` | Path to the SQLite database file |
| `--days` | `7` | How many days back to search MT5 deal history |
| `--yes` | `false` | Auto-confirm fixes without prompting |
| `--verbose` | `false` | Show every profit value, not just mismatches |

### Dealing With Old Trades

By default, the script searches MT5 history for the last **7 days**. If
you have trades older than that, widen the window:

```bash
# Search last 60 days of MT5 history
python repair_db.py --days 60 --yes

# For very old trades, try 90 or 365 days
python repair_db.py --days 365 --yes
```

---

## How It Works (Internal)

### Dealing With an MT5 API Limitation

The MT5 Python API has a subtle bug: calling
`mt5.history_deals_get(from_date, to_date, position=TICKET)` **silently
ignores the `position` filter** on some MT5 builds when combined with date
range parameters. This caused the first version of this script to
accumulate ALL deals in the date range for EVERY ticket — assigning the
same aggregate profit to every trade (all 13 trades showed exactly
`-$1,158.36`).

The workaround implemented in this script:

1. **Fetch all deals once** in the date range:
   ```python
   all_deals_raw = mt5.history_deals_get(from_date, to_date)
   ```
2. **Index by position ticket** using `defaultdict(list)`:
   ```python
   deals_by_position[pos_id].append(deal)
   ```
3. **Filter in Python** by ticket:
   ```python
   deals = deals_by_position.get(ticket)
   ```

This ensures each ticket gets only its own deals — verified by the
successful run where 13 trades each received **unique, correct** P&L
values (e.g., `+$1,790.21`, `-$115.12`, `+$512.72`, etc.).

### The DEAL_ENTRY_OUT Filter

MT5 deal objects have an `entry` attribute indicating whether the deal
opened (`DEAL_ENTRY_IN`) or closed (`DEAL_ENTRY_OUT`) a position. The
script (and the orchestrator) only accumulate profit from
`DEAL_ENTRY_OUT` deals — entry deals are skipped entirely.

### Commission Handling

ECN/RAW accounts charge a per-lot commission that MT5 stores as a
separate `commission` attribute on the deal object (not included in
`deal.profit`). The script adds it:

```python
real_profit += deal.profit
real_profit += getattr(deal, "commission", 0.0) or 0.0
```

This matches the exact logic in `orchestrator._extract_exit_info()`.

---

## Safety Features

| Feature | How it protects you |
|---------|-------------------|
| **Dry-run default** | Shows mismatches without modifying anything — you type `y` to commit |
| **Stop-bot warning** | Prompts you to stop the bot engine before accessing the database |
| **\$0.05 tolerance** | Only fixes values that differ meaningfully — avoids float-noise churn |
| **Guarded `finally` block** | `conn.close()` is guarded with `if 'conn' in locals()` to prevent `NameError` if connection setup fails |
| **No date-position mixing** | Uses Python-side filtering to avoid MT5 API `position` filter bug |

---

## After Running

1. **Refresh the dashboard** — corrected P&L values will appear in the
   Recent History table
2. **Check the bot logs** — on the next trading cycle, the orchestrator
   will log `PNL_MISMATCH` warnings for any remaining stale trades
3. **Re-run with a wider window** if some trades were skipped (older than
   `--days`)

### Verifying the Fix

```python
python -c "
import sqlite3
conn = sqlite3.connect('database/trades.db')
c = conn.cursor()
c.execute('SELECT ticket, symbol, direction, ROUND(profit,2) FROM trade_logs WHERE status=\"closed\" ORDER BY id')
for r in c.fetchall():
    print(r)
conn.close()
"
```

All trades should show **unique, realistic** profit values — no more
duplicated numbers across different symbols.
