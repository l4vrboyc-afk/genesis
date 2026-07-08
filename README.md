# Genesis Trading Bot

> An async MT5 forex trading system with FastAPI dashboard, Discord control, and regime-switching strategies.

**[Quickstart →](https://github.com) [Long-form setup guide →](docs/SETUP.md)** · default mode is paper-trading — no real money touched until you flip `PAPER_TRADING=false`.

## ⚡ TL;DR

```bash
git clone <your-fork-url> genesis
cd genesis
python -m venv .venv
source .venv/bin/activate            # macOS / Linux
# .venv\Scripts\activate             # Windows
pip install -e .                     # installs MetaTrader5 on Windows automatically
copy .env.example .env               # Windows
# cp .env.example .env               # macOS / Linux

# Fill MT5_LOGIN, MT5_PASSWORD, MT5_SERVER in .env with broker-issued values
python scripts/check_setup.py        # ← diagnostic; must print "All checks passed"
python main.py                       # ← starts bot + dashboard + Discord
```

Dashboard: **http://127.0.0.1:8000**.

> **Don't want to use the terminal?** Double-click `Genesis.exe` — it spawns the bot, opens the dashboard in a native window, and shuts everything down when you close it. See [`docs/LAUNCHER.md`](docs/LAUNCHER.md).

## ✨ Features

- **Regime-aware engine** — `StrategySelector` switches between Smart Trend, Mean Reversion, Scalper Momentum, and Session Breakout based on ADX + ATR volatility. Skips `VOLATILE`, `DEAD`, `NEWS_EVENT` regimes.
- **Risk manager** — per-trade risk cap (1% default), ATR-volatility sizing, daily drawdown circuit breaker, losing-streak cooldown, correlation filter.
- **News filter** — pauses trading around high-impact events via the MT5 native calendar (with a recurring-event fallback for older builds).
- **Discord bot** — alerts on every trade open / close, plus commands: `!status`, `!pause`, `!resume`, `!release_regime`, `!stats`, `!risk`, `!pairs`, `!trades`.
- **FastAPI dashboard** — live balance, equity, win-rate, drawdown, P&L chart, open positions, news calendar, live settings editor.
- **Async heart** — single `asyncio.Lock` serializes every MetaTrader5 call so the sync broker API can never race with itself across the dashboard, Discord, and the trading loop.

## 🚀 Quickstart

### 0. Prerequisites

- **Python 3.10+** (3.11+ recommended).
- **MetaTrader 5** installed and logged into *some* account (paper or live). MT5 only runs natively on Windows; on macOS / Linux it must run through Wine, Parallels, Crossover, or a Windows VPS — see [`docs/SETUP.md` § Operating systems](docs/SETUP.md#2-operating-systems).
- A broker account that supports MT5. **Most brokers offer free demo accounts** — search `<broker name> + demo MT5` and sign up; takes minutes.

### 1. Clone, venv, install

```bash
git clone <your-fork-url> genesis
cd genesis
python -m venv .venv

# Activate the venv
source .venv/bin/activate            # macOS / Linux
# .venv\Scripts\activate             # Windows

# Cross-platform install
pip install -e .
```

`pip install -e .` reads `pyproject.toml`, which uses environment markers to pull `MetaTrader5` only on Windows. On macOS / Linux, the bot's dashboard-only mode still works (it's just trading that can't run without a Windows-hosted MT5).

### 2. Configure

```bash
copy .env.example .env               # Windows
# cp .env.example .env               # macOS / Linux
```

Open `.env` in any editor and fill in:

| Field | Where to find it |
|---|---|
| `MT5_LOGIN` | Number 6-9 digits; in your broker's MT5 welcome email |
| `MT5_PASSWORD` | The **master** (read-write) password from the broker email — not the investor password |
| `MT5_SERVER` | Exact server string (case-sensitive) e.g. `Exness-MT5Trial7`, `ICMarkets-Live06` |
| `MT5_PATH` | Default `C:\Program Files\MetaTrader 5\terminal64.exe` is correct for most Windows installs |

All other keys have sane defaults — see `.env.example` for the full schema and per-key explanations.

### 3. Preflight

```bash
python scripts/check_setup.py
```

You want to see:

```
Genesis — first-run check

✅ Python version  — ok
✅ Platform  — ok
✅ .env file  — ok
✅ MT5 install  — ok
✅ MT5 credentials  — ok
✅ Dashboard port  — ok
✅ Discord token  — ok

✅  All checks passed. Run `python main.py` to start Genesis.
```

If anything fails, the script prints a colored `❌` line with a one-sentence fix. Re-run after each change. `python scripts/check_setup.py --fix` auto-copies `.env.example` → `.env` when missing.

### 4. Run

```bash
python main.py
```

`main.py` *re-runs* the preflight automatically. On success, the startup banner appears before any other output (so you don't scroll for the URL):

```
════════════════════════════════════════════════════════════
  🚀 Genesis Trading Bot is starting
     Dashboard:  http://127.0.0.1:8000
     Profile:    default
     Paper mode: yes (demo)
     Discord:    enabled
════════════════════════════════════════════════════════════
```

Open the dashboard URL in your browser. The Discord bot posts an initial status there automatically. Stop with **Ctrl+C**.

### 5. Profiles

Switch strategy profile without editing `.env`:

```bash
# macOS / Linux
GENESIS_PROFILE=scalper  python main.py
GENESIS_PROFILE=breakout python main.py

# Windows PowerShell
$env:GENESIS_PROFILE="scalper"; python main.py

# Windows cmd
set GENESIS_PROFILE=scalper && python main.py
```

Profiles switch the active `.env.<profile>` and re-target the strategy selector. See `.env.scalper` and `.env.breakout` for what each variant changes.

## 🗂 Architecture

```
Genesis/
├── main.py                   # Service bootstrap (asyncio). Calls preflight first.
├── bot/
│   ├── config/               # Settings singleton (Pydantic v2)
│   ├── core/                 # MT5Connector, DataFetcher, OrderManager, TradingOrchestrator
│   ├── strategies/           # SmartTrend, MeanReversion, ScalperMomentum, SessionBreakout, StrategySelector
│   ├── risk/                 # RiskManager, PerformanceTracker, NewsFilter
│   └── notifications/        # Async queue → Discord embeds
├── dashboard/
│   ├── backend/main.py       # FastAPI REST API
│   └── frontend/index.html   # Vanilla JS + Chart.js (no build step)
├── database/                 # SQLAlchemy 2.0 async + aiosqlite
├── scripts/check_setup.py    # First-run diagnostic (auto-runs from main.py)
├── docs/SETUP.md             # Long-form install / troubleshooting guide
├── backtest/                 # Backtester
├── tests/                    # unittest
└── pyproject.toml            # Package metadata + cross-platform install
```

The orchestrator runs on a 15-second cycle: sync open positions with MT5, fetch candles + indicators, score signals, run risk checks, place orders, trail stops, notify.

## 🎮 Discord commands

| Command | What it does |
|---|---|
| `!status` | Connection state, balance, equity, daily P&L, open positions |
| `!pause` / `!resume` | Halt / resume new entries (existing positions still managed) |
| `!close_all` / `!flatten` | Emergency flatten — **magic-filtered**, only Genesis positions close; auto-pauses the bot |
| `!kill_switch` | Show tripped risk states (daily drawdown, equity floor, engagement latch). `!kill_switch release` clears all trip flags + resumes |
| `!release_regime` | Clear a forced regime override and resume auto-classification |
| `!stats` | Win rate, profit factor, average R:R, total P&L |
| `!risk` | Open positions, daily drawdown, consecutive losses, cooldown state, all kill switches |
| `!pairs` | Active trading pairs |
| `!trades` | Last 5 closed trades |

### Kill switches

Two real-money safety switches auto-flatten all Genesis positions when tripped:

- **Daily drawdown** — when equity falls `MAX_DAILY_DRAWDOWN` (5% by default) below the day's starting balance *within a single UTC day*. Auto-resets on the next day; the bot stops but stays paused until you `!resume`.
- **Equity-floor** — when equity drops `EQUITY_FLOOR_KILL_SWITCH_PCT` (40% by default) below the running peak equity. **No auto-reset**: a peak-equity recovery may not coincide with a day rollover. Use `!kill_switch release` or the **Release kill switches** button on the dashboard's risk panel.

Both switches are **magic-number isolated** — `!close_all` / `!kill_switch` only target positions whose `magic` field matches `MAGIC_NUMBER`. Foreign / manual trades in the same account are explicitly left untouched. Notify (Discord) + Dashboard (POST `/api/control close_all`) + `!close_all` all flow through the same emergency-flatten path.

## 📡 REST API

```
GET  /api/status          # Status + open trades
GET  /api/trades          # History (?status=open|closed)
GET  /api/performance     # Rolling metrics + 30-day daily history
GET  /api/news            # Upcoming high-impact events (24h)
GET  /api/risk            # Risk state + parameters
GET  /api/settings        # Current configuration
POST /api/settings        # Mutate config (persisted to DB)
POST /api/control         # pause | resume | close_all | force_regime | release_regime | release_kill_switches
```

Full schema at `/docs` once the bot is running.

## 📐 Strategies

| Strategy | Profile | Timeframes | Best in | Key filter |
|---|---|---|---|---|
| **Smart Trend Breakout** | default | H4 → M15 | Trending | price > EMA200 + ADX > 25; OB/FVG entries; RSI confirmation; SL = 1.5×ATR, TP = 3×ATR |
| **Mean Reversion** | default | H4 → M15 | Ranging (ADX < 20) | Buy swing support / sell resistance with RSI oversold / overbought |
| **Scalper Momentum** | scalper | M15 → M1 | Quick bursts | Tighter stops (0.8×ATR), more positions (≤ 5), wider daily DD (3%) |
| **Session Breakout** | breakout | H1 → M15 | Session opens | Confirms breakouts over a higher-TF reference |

`StrategySelector` flips between strategies based on the regime. Strategies are also profile-gated: `.env.scalper` and `.env.breakout` activate the matching names; default profile uses Smart Trend + Mean Reversion only (the others are conditionally imported — they're not in `__all__` of `bot.strategies` for the default profile, so an accidentally-wired selector logs a warning instead of erroring).

Regime classification is **sticky**: a regime that has held for less than `HYSTERESIS_WINDOW_SECONDS` (5 min by default) is *not* flipped on a brief ADX dip — this kills the 22-23 chatter around S/R crossovers.

## ⚙️ Configuration

The full schema is in `.env.example` — every key has a `# <explanation>` line. The most common knobs:

- **Account** — `MT5_LOGIN`, `MT5_PASSWORD`, `MT5_SERVER`, `MT5_PATH`, `PAPER_TRADING`, `STARTING_CAPITAL`
- **Risk** — `MAX_RISK_PER_TRADE`, `MAX_DAILY_DRAWDOWN`, `MAX_OPEN_POSITIONS`, `MIN_REWARD_RATIO`
- **Strategy** — `EMA_PERIOD`, `ADX_*`, `RSI_*`, `ATR_*`, `HIGHER_TIMEFRAME`, `ENTRY_TIMEFRAME`
- **Discord** — `DISCORD_TOKEN`, `DISCORD_CHANNEL_ID`, `DISCORD_COMMAND_PREFIX`
- **Dashboard** — `DASHBOARD_HOST`, `DASHBOARD_PORT`
- **Database** — `DATABASE_URL`

Settings can also be updated at runtime via `POST /api/settings` (persisted to SQLite `bot_state`).

## 🤖 Ask Claude

An optional in-dashboard copilot that answers natural-language questions about bot state, recent trades (DB + log tail), and settings, grounded in your local data with citations back to the underlying source. **Active only when `ANTHROPIC_API_KEY` is set.**

**Setup:** add `ANTHROPIC_API_KEY=sk-...` to your `.env` (or set it as an OS env var), then restart the bot. The dashboard's `🤖 Ask Claude` card lights up green and surfaces the model's last-4 key chars.

**Example questions:**

- *"Why is the bot paused?"*
- *"What's my daily drawdown today, and what's the most recent losing trade?"*
- *"Was trade T1234 a good entry — what's the regime and R:R?"*
- *"Show me which kill switch just tripped and what triggered it."*

**Failure mode:** when `ANTHROPIC_API_KEY` is not set, the card shows a yellow `Copilot disabled` banner; the `/api/copilot/ask` endpoint returns HTTP 503 with a structured JSON body. The dashboard stays usable.

**Citations + safety:** every fact in the assistant's answer carries a bracketed citation like `[source:trades:T1234]` rendered as clickable chips on the card. Broker-supplied free-text (trade comments, log lines) is wrapped in `<untrusted-data>` blocks inside the system prompt — the model is instructed to treat them strictly as data, never as instructions.

## 🔧 Troubleshooting

One-line recipes for the common install failures. Detailed walk-throughs in [`docs/SETUP.md`](docs/SETUP.md).

| Symptom | Fix |
|---|---|
| `❌ MT5_PATH does not point to an existing file` | Install MT5 from broker; fix `MT5_PATH` in `.env` |
| `❌ MT5 initialization failed: code=-2` | Wrong login/password — confirm against broker email |
| `❌ MT5 initialization failed: code=-3` | Wrong server — exact broker string (case-sensitive) |
| `❌ MT5 initialization failed: code=-5` | MT5 → Tools → Options → Expert Advisors → enable Algorithmic Trading + DLL imports |
| `❌ DASHBOARD_PORT 8000 already in use` | Stop the conflicting process (see `docs/SETUP.md` § 6) or change `DASHBOARD_PORT` |
| `⚠️ MT5 credentials look like placeholders` | Fill `MT5_LOGIN`, `MT5_PASSWORD`, `MT5_SERVER` in `.env` (demo creds are still required) |
| Dashboard URL line never appears | Bot is stuck at preflight — run `python scripts/check_setup.py` directly to see the failing check |
| Discord channel never receives alerts | Confirm `DISCORD_TOKEN` and `DISCORD_CHANNEL_ID`; see `docs/SETUP.md` § 5 for bot creation |
| `ImportError: No module named 'MetaTrader5'` on Mac/Linux | Run `pip install -e '.[mac-linux]'` (no-op install on Windows) |
| Strategies logged but never trade | Regime is `VOLATILE` / `NEWS_EVENT` / `DEAD` — see Dashboard → Regime indicator; wait or force via `POST /api/control force_regime trending` |

## ⚠️ Risk Warning

This software trades **real money** through MetaTrader 5. Default mode is `paper_trading=true` — always test on a demo account first. Past performance of any strategy is no guarantee of future returns. The authors disclaim all liability for trading losses. You, the operator, are solely responsible for compliance with your broker's terms and local financial regulation. **Switch `PAPER_TRADING=false` only after you've validated paper results, sized-down live (`MAX_RISK_PER_TRADE=0.005` for the first 24 hours), and read [`docs/SETUP.md` § 11](docs/SETUP.md#11-going-live-when-youre-ready).**

## 📜 License

MIT.
