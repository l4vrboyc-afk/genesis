# Genesis Trading Bot — Long-form Setup Guide

This is the reference companion to [`README.md`](../README.md). Reach for it when the quickstart hits a wart, or before you switch on live trading.

## 1. Why this doc exists

The README gets you from `git clone` to "running on paper" in under 10 minutes when everything lines up. This doc covers the cases where it doesn't — wrong MT5 install, weird platform, broker-specific server naming, port conflicts, kill-switch wiring, and the things you need to know *before* flipping `PAPER_TRADING=false`.

If you're reading this top-to-bottom before install, focus on **§ 2 (operating systems)**, **§ 3 (broker demo account)**, and **§ 5 (Discord bot setup)**. The rest is reference material for problems.

## 2. Operating systems

### 2.1 Windows (recommended)

- Native. MetaTrader 5 ships for Windows; the Python `MetaTrader5` package is a wheel that installs cleanly.
- Steps: install Python 3.10+ from python.org, install MT5 from your broker's download page, log in once with each demo account you intend to use.
- No special configuration needed.

### 2.2 macOS (works through emulation)

MT5 has no native Mac build. The path-of-least-resistance options:

- **Parallels** (paid) — full Windows VM. Best latency, easiest setup.
- **CrossOver** (paid) — Wine-based; runs MT5 directly without a full Windows image. Quirks on Apple Silicon.
- **WineHQ Bottles** (free) — same engine, more manual configuration.

Set `MT5_PATH` in `.env` to point inside the Wine / Parallels file system, e.g.:

```ini
MT5_PATH=/Users/<you>/.wine/drive_c/Program Files/MetaTrader 5/terminal64.exe
```

The preflight script detects the empty-mac-platform case and prints a clear advisory message — the dashboard still starts, but trading requires the bridge.

### 2.3 Linux (VPS or containerized Wine)

Same story as macOS. The popular path for serious forex traders is:

- Rent a **Windows VPS** close to your broker's server — `LD4` for London brokers, `NY4` for US, `TY3` for Tokyo. Latency matters at news spikes.
- Connect via RDP (Remote Desktop). Everything runs natively; `MT5_PATH=C:\Program Files\MetaTrader 5\terminal64.exe` works as written.
- Cost: typically £20–£50/month. Many brokers (Pepperstone, IC Markets, Vantage) give active clients a free VPS.

Local Linux + Wine is possible but adds ~10–15% CPU overhead and is fiddly when MetaTrader updates. Local Linux is **not** recommended unless you specifically want the learning curve.

## 3. MT5 demo account creation

Every broker does this slightly differently, but the shape is:

1. **Sign up** — name, email, country, sometimes KYC tier (basic / advanced).
2. **KYC** — ID upload (passport / driver's licence), proof of address (utility bill or bank statement within 3 months). Some brokers auto-approve; some take a day.
3. **Fund** — for demo, this is one click: "Open demo account" or top up with a small amount. Real accounts need a bank transfer / card / wire.
4. **Save credentials from the broker email**:
   - **Login** — 6-9 digit number.
   - **Password** — the *investor* and *master* passwords differ. The bot needs the **master** (sometimes called "read-write") password.
   - **Server** — strings like `Exness-MT5Trial7`, `ICMarkets-Live06`, `Pepperstone-Live03`. **Case-sensitive**.
5. **In MT5**: File → Login to Trade Account → enter those credentials. Confirm the connection status icon at the bottom-right goes green.

Broker-specific quirks:

- **Exness** — the trial demo `Exness-MT5Trial7` server can occasionally be unreachable during regional maintenance. The preflight reports this clearly with `code=-3` and a server-string hint.
- **IC Markets** — server strings change with platform updates. Always double-check on the broker's "Servers" page before pasting.
- **Pepperstone** — `Razor` / `Standard` account types use different server strings. Match your account type.

## 4. `terminal64.exe` path discovery

When `MT5_PATH` is wrong, the bot fails at startup with a clear remediation line. Common non-default paths:

- `C:\Program Files\MetaTrader 5\terminal64.exe` — most installs default here.
- `C:\Program Files (x86)\MetaTrader 5\terminal64.exe` — 32-bit terminal on 64-bit Windows. Use the 64-bit one unless you've specifically chosen otherwise.
- `<broker-branded> terminal64.exe` — e.g. `C:\Program Files\Exness MetaTrader 5\terminal64.exe` when the broker bundled their own installer.
- VPS bucket — `C:\Users\Administrator\AppData\Roaming\MetaQuotes\Terminal\<hash>\terminal64.exe`. Indicates MT5 was installed as the admin user.

To find it on Windows: open File Explorer, navigate to `C:\`, search `terminal64.exe`, right-click → Properties → copy the path.

### 4.5 WebView2 Runtime (GUI launcher)

The GUI launcher (`Genesis.exe`) uses Microsoft's Edge WebView2 control to render the profile picker and dashboard. This runtime is preinstalled on Windows 11 (24H2+) and on any system that runs Microsoft Edge.

**If the launcher shows "WebView2 runtime not found":**

1. Download the **Evergreen Bootstrapper** from [Microsoft WebView2](https://developer.microsoft.com/microsoft-edge/webview2/#download-section).
2. Run the installer (no admin rights required for per-user install).
3. Restart Genesis.exe.

The launcher checks the Windows registry for the runtime before opening a window, so you'll get a clear error instead of a silent blank window.

**VPS users:** Many bare Windows Server VPS images ship without Edge. Install WebView2 before deploying Genesis.exe.

**Offline / air-gapped machines:** Use the **Fixed Version** runtime instead of the Evergreen Bootstrapper. Extract it next to `Genesis.exe` in the deployment directory.

## 5. Discord bot setup (optional but recommended)

1. Open Discord in a browser, visit <https://discord.com/developers/applications>.
2. **New Application** → name it `Genesis`. Open the application's page.
3. **Bot** section (left sidebar) → **Add Bot**. Click **Reset Token** if it's hidden, then **Copy**. Store it; this becomes `DISCORD_TOKEN`.
4. **OAuth2 → URL Generator**: scopes = `bot`; permissions = *Send Messages*, *Read Message History*, *Embed Links*, *Attach Files*. Open the URL and invite the bot to your server.
5. In Discord, with Developer Mode on (Settings → Advanced → Developer Mode): right-click the channel where you want alerts → **Copy Channel ID**. This is `DISCORD_CHANNEL_ID`.
6. Restart Genesis. The bot joins your channel. Try `!status` to confirm.

**Treat your bot token like a password.** Anyone with it can post as your bot. It's stored in `.env`, which is `.gitignored` by default — keep it that way.

## 6. Dashboard port conflicts

Genesis defaults to `127.0.0.1:8000`. If something else owns it, find the conflict and either kill the process or change Genesis's port:

```powershell
# PowerShell — find the PID listening on a port
Get-NetTCPConnection -LocalPort 8000 -State Listen

# Stop it
Stop-Process -Id <pid> -Force
```

```bash
# macOS / Linux
sudo lsof -iTCP:8000 -sTCP:LISTEN
kill <pid>
```

Each profile uses a different default port (`default=8000`, `scalper=8001`, `breakout=8002`), so running a scalper and a breakout side-by-side works without conflict. To change a profile's port, edit `DASHBOARD_PORT` in the profile's `.env.<profile>` file.

If you want LAN access (other devices reaching the dashboard on your machine), set `DASHBOARD_HOST=0.0.0.0`. The startup banner then prints both the LAN URL and the loopback URL.

## 7. Reading the log file

`logs/bot.log` is the truth — every order, every error, every state transition. Find it:

```powershell
Get-Content logs\bot.log -Tail 50 -Wait    # live tail, last 50 lines
```

```bash
tail -F logs/bot.log                       # same on macOS / Linux
```

Loguru rotates at 10 MB and keeps 14 days of `INFO+` and compressed `DEBUG` history. To protect your disk: clear old logs occasionally, or lower `LOG_LEVEL` from `INFO` to `WARNING` for production runs.

## 8. Preflight output, decoded

```
✅ Python version  — ok                          # 3.10+
✅ Platform  — ok                                 # Windows OR advisory message on Mac/Linux
✅ .env file  — ok                                # .env exists, .env.example exists, .env not zero-byte
✅ MT5 install  — ok                              # MT5_PATH is a real file (Win); SDK imports
✅ MT5 credentials  — ok                          # MT5_LOGIN>0, MT5_PASSWORD & MT5_SERVER not placeholders
✅ Dashboard port  — ok                           # DASHBOARD_PORT free on 127.0.0.1
✅ Discord token  — ok                            # Empty OR a 3-segment Discord bot token
```

A red `❌` line is followed by a one-sentence fix. Run `--fix` to auto-repair the most common case (missing `.env` ↦ copy from `.env.example`).

## 9. Common error recipes

### 9.1 `MT5 credentials look like placeholders` warning

You cloned the repo and didn't fill in the `.env`. Open it and find:

```ini
MT5_LOGIN=0
MT5_PASSWORD=
MT5_SERVER=Exness-MT5Trial7
```

Replace with the broker-issued values from your welcome email. Even paper-trading needs real demo credentials — there is no "open as guest" MT5 mode.

### 9.2 `MT5 initialization failed: code=-5 ... "Allow algorithmic trading" is disabled`

Open MT5 → **Tools → Options → Expert Advisors**:

- [x] Allow algorithmic trading
- [x] Allow DLL imports (only if your broker uses DDE or EA add-ons)
- Trusted EAs list: leave empty unless your broker documents otherwise

Click **OK**. Restart Genesis.

### 9.3 Dashboard line never appears in stdout

`main.py` re-runs `scripts/check_setup.py` before any other startup. If the preflight fails, you'll see green/red lines only — no banner. Re-run the preflight directly to see the failing check:

```bash
python scripts/check_setup.py
```

### 9.4 Bot connects but never trades

Most common cause: regime classification is `VOLATILE` or `NEWS_EVENT`. Check the dashboard's "Regime" indicator; if it's not TRENDING or RANGING, the bot is intentionally skipping entries. Wait for the regime to stabilize, or override via:

```bash
curl -X POST http://127.0.0.1:8000/api/control -H "Content-Type: application/json" -d '{"action":"force_regime","payload":"trending"}'
```

To resume auto-classification after an override: `release_regime` (same route).

### 9.5 Daily-drawdown circuit breaker tripped

After `-MAX_DAILY_DRAWDOWN` equity drop in a single day, no new entries. Open positions are auto-flattened when track (d) lands the kill-switch wiring. Today (pre-track-d) positions stay open until `MAX_OPEN_POSITIONS` is breached or manual flat via the dashboard.

The tripped state persists across restarts via the `bot_state` SQLite table — restart Genesis the next day to resume normally.

## 10. Kill switches (current and upcoming)

| Action | Today | After track (d) |
|---|---|---|
| Pause new entries | Dashboard button / `POST /api/control pause` | + `!pause` Discord |
| Flatten everything | Dashboard / `POST /api/control close_all` | + `!close_all` Discord |
| Stop the bot | Ctrl+C in terminal | + Tauri shell icon (track b) |
| Restart after daily-DD trip | Manual next day | + auto-release next day |

These are dashboard-only today; Track (d) wires them into Discord commands and orchestrator-side auto-flatten. Track (b) wires them into a native taskbar tray.

## 11. Going live (when you're ready)

Three things, in order:

1. **Switch `PAPER_TRADING=false` in `.env`.** The startup banner now reads `Paper mode: NO — LIVE MONEY` — a final sanity check.
2. **Verify your broker's exact server name and account number.** Demo and live servers are **different strings** — past the preflight, those are your keys to real money.
3. **Reduce `MAX_RISK_PER_TRADE` to `0.005` (0.5%) for the first 24 hours** of live. Bump after that only if the paper backtest matches.

**There is no software guard rail for "you turned on live money."** If you set `PAPER_TRADING=false` and fill in real credentials, the bot will trade real money at the next opportunity. The kill switches you'll wire in Track (d) are how you stop it.

## 12. Getting help when stuck

Run these in order — they catch 90% of "this isn't working" cases:

1. Read the log file: `tail -F logs/bot.log`.
2. Run the preflight: `python scripts/check_setup.py`.
3. See **Troubleshooting** in `README.md` for one-line recipes.
4. Read this doc — most issues are covered above or in the linked sections.
5. File an issue at the project tracker with:
   - Genesis version (commit hash)
   - `python --version`
   - `pip show MetaTrader5 | grep Version` (on Windows)
   - The relevant slice of `logs/bot.log`

Don't paste broker credentials, account numbers, or bot tokens into public channels.

## 13. What lives where

Quick map of the project so you don't grep blindly:

- `main.py` — bootstrap. Don't add business logic here.
- `bot/core/` — MT5 IO, order placement, the trading loop. Heart of the system.
- `bot/strategies/` — pure functions: indicators in, signals out. Stateless; one per regime.
- `bot/risk/` — gates every signal. Files are kept side-effect-free where possible.
- `bot/notifications/` — outbound Discord. Inbound commands route through here too.
- `dashboard/` — FastAPI frontend + REST API. Zero state lives here; everything goes through the orchestrator.
- `database/` — async SQLAlchemy. Read by `dashboard/backend/main.py`, written by `bot/core/orchestrator.py`.
- `scripts/check_setup.py` — install-time diagnostic. Standalone; can be removed without affecting runtime.
- `docs/SETUP.md` — this document.
- `backtest/` — historical simulator. Run it before every config change to a tuned strategy.
- `tests/` — unittest suite. `python -m unittest tests.test_suite -v`.

## 14. License

MIT, same as the README.
