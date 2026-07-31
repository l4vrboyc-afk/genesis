# Genesis GUI Launcher

Double-clickable `Genesis.exe` to launch the bot with a native window — no
terminal required.

## What it does

`Genesis.exe` is a thin wrapper. It:

1. Locates the project root (the directory it lives in — must be the one
   with `main.py` and `.venv/`).
2. Spawns `<root>\.venv\Scripts\python.exe main.py` in a new process group.
   Stdout inherits (no Windows pipe deadlock); stderr goes to `logs/launcher.log`.
3. Polls TCP until the dashboard binds to `127.0.0.1:<DASHBOARD_PORT>`.
4. Opens a native Edge WebView2 window pointed at the dashboard.
5. On window close, sends `CTRL_BREAK_EVENT` to the bot for graceful
   shutdown, waits up to 8 s, escalates to `terminate` and finally `kill`.

The `.exe` is **intentionally tiny** (~14 MB). It does not bundle the bot
itself — when Genesis code changes you don't need to rebuild the launcher.
Only changes to `launch_gui.py` (window size, splash, profile picker) need
a rebuild.

## Prerequisites

| Requirement | Where it comes from |
|-------------|---------------------|
| Windows 10 build 17763+ / Windows 11 | OS |
| WebView2 Runtime | Required. Preinstalled on Win11 24H2+; auto-present on any system with Microsoft Edge. **If missing**, install from [Microsoft WebView2 download](https://developer.microsoft.com/microsoft-edge/webview2/#download-section) (Evergreen Bootstrapper recommended). Genesis.exe will detect the missing runtime and show an error on launch. |
| The project itself | `main.py`, `.venv/`, `.env` all in the same directory as `Genesis.exe` |

## Usage

1. Make sure `Genesis.exe` is in the project root (the folder that holds
   `main.py`). Drop it there if it isn't.
2. Double-click `Genesis.exe`.
3. The Discord + MT5 + orchestrator boot runs as usual, then a black
   dashboard window opens. Wait for the first signal cycle (~15 s).
4. Close the window (X). The bot shuts down gracefully and the tray/console
   is gone.

If the dashboard port is already bound by another running instance (dev
mode, or someone else's copy of `main.py`), `Genesis.exe` opens the
existing dashboard **but does not own the process** — closing the window
leaves the bot alone.

## Files it writes / reads

- Reads: `.env` (for `DASHBOARD_HOST`, `DASHBOARD_PORT`), nothing else.
- Spawns: `<root>\.venv\Scripts\python.exe main.py`.
- Writes (in `<root>\logs\`): `launcher.log` (stderr of `main.py`),
  `bot.log` (the bot's own loguru), `trades.db` (already there).

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| Window never opens; "Could not find venv Python at..." flashes | `Genesis.exe` is in the wrong directory, or `.venv/` is missing | Move `Genesis.exe` to the project root, or rebuild the venv. |
| Window shows "ERR_CONNECTION_REFUSED" / blank page | Bot crashed during boot | Check `logs/bot.log` and `logs/launcher.log` for the traceback. |
| Bot errors with `10030 — Unsupported filling mode` (or orders silently return `None`) | Broker symbol doesn't advertise the filling mode the bot requested | The bot now auto-selects a supported `type_filling` per symbol from `symbol_info.filling_mode` in `bot/core/order_manager.py` (`_choose_filling`), so this should be resolved. If it recurs, the new `❌ Order send returned None … (filling=…); <mt5.last_error()>` log line names the exact reason — send that line. |
| Double-click triggers "Windows protected your PC" SmartScreen | PyInstaller binaries aren't code-signed | Right-click → Properties → "Unblock", or Properties → "Continue" through the SmartScreen dialog. |
| Window closes but bot stays running (orphan) | Older launcher or window was force-killed | Use Task Manager → Details → end `python.exe`. |
| Empty black window for >60 s | Backend didn't bind within deadline | See "ERR_CONNECTION_REFUSED" entry above; remove the `--noconsole` from the build temporarily to see live stdout. |
| "WebView2 runtime not found" error on launch | Edge WebView2 runtime is not installed | Download the Evergreen Bootstrapper from https://developer.microsoft.com/microsoft-edge/webview2/#download-section and run it. Restart Genesis.exe after install. |

## Rebuilding the .exe

From the project root:

```powershell
.venv\Scripts\python.exe -m PyInstaller --noconfirm --onedir --noconsole `
  --name Genesis `
  --icon genesis_icon.ico `
  --collect-all pywebview `
  --hidden-import=webview.platforms.winforms `
  launch_gui.py
```

Or via the saved spec (equivalent):

```powershell
.venv\Scripts\python.exe -m PyInstaller --noconfirm Genesis.spec
```

Resulting `dist\Genesis\Genesis.exe` — a directory build with dependencies
alongside the exe. Copy the **entire `dist\Genesis\` folder** next to
`main.py` to deploy. Do not extract `Genesis.exe` from the folder — it
depends on the sibling DLLs and data files.

`launch_gui.py` is plain Python — any edits you make to it (window
geometry, splash, profile picker, etc.) can be re-bundled with the same
command. The Genesis source it spawns (`main.py`) can change freely
without rebuilding.

## Deploying the rebuilt bundle

After a rebuild, sync the fresh output to the run locations. The repo
keeps three copies of the bundle that must stay identical:

```powershell
# 1. The canonical build output (already fresh from PyInstaller)
#    dist\Genesis\Genesis.exe + dist\Genesis\_internal\

# 2. The run-from-project-root layout (Genesis.exe sits next to main.py)
#    Close Genesis.exe first if it's running (the exe is locked).
Copy-Item dist\Genesis\Genesis.exe Genesis.exe -Force
Remove-Item _internal -Recurse -Force   # remove first so Copy-Item creates a fresh tree
Copy-Item dist\Genesis\_internal _internal -Recurse -Force

# 3. The self-contained deployment folder
Copy-Item dist\Genesis\Genesis.exe Genesis\Genesis.exe -Force
Remove-Item Genesis\_internal -Recurse -Force
Copy-Item dist\Genesis\_internal Genesis\_internal -Recurse -Force
```

> **Note:** use `Copy-Item` / `Remove-Item`, not `robocopy /MIR` —
> `robocopy /MIR` can exit with code 16 ("copied nothing") in some
> shells, silently leaving stale files in place. Always verify the sync
> afterwards. Avoid `diff -rq` in PowerShell (it aliases to
> `Compare-Object`); use `git diff --no-index` instead:
>
> ```powershell
> git diff --no-index --quiet dist\Genesis\_internal _internal
> git diff --no-index --quiet dist\Genesis\_internal Genesis\_internal
> ```
>
> Both must exit 0 (print nothing). The dashboard frontend
> (`dashboard/frontend/`) is **not** bundled — it's served live from the
> project root by the bot, so dashboard-only changes never require a
> rebuild.

## Version check

Run `Genesis.exe --version` to print the launcher version. This is useful
for deployment verification.

When running the windowed (non-console) build, a console window is
allocated automatically so the version is visible.

## Auto .env creation

If `.env` is missing at launch time, the launcher automatically copies
`.env.example` to `.env`. This mirrors `python scripts/check_setup.py --fix`.
You'll still need to fill in your MT5 credentials — the auto-copied file
uses placeholder values.

## Network requirements

None beyond standard outbound for the bot itself (MT5 connection, Discord
webhook, Pandas-TA fetch on first import). The launcher itself opens no
network sockets.

## Sandbox / dev mode

Run `launch_gui.py` directly without building:

```powershell
.venv\Scripts\python.exe launch_gui.py
```

A console window appears (debug output) and a native window with the
dashboard. When you close the window the bot dies.
