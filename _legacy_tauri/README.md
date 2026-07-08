# Genesis Shell — Tauri 2.x Desktop Wrapper

Native desktop shell for the Genesis Trading Bot. Wraps the existing
FastAPI dashboard at `http://127.0.0.1:8000` in a Tauri WebView window
plus a secondary always-on-top panel reserved for the Ask-Claude copilot
(track c).

This directory is **standalone**: nothing in `bot/`, `dashboard/`,
`scripts/`, or `main.py` is modified by the shell. The shell assumes
that `python main.py` is already running on the loopback dashboard port.

---

## Toolchain

| Tool | Required | Install |
|------|----------|---------|
| Rust toolchain (`rustc` + `cargo`) | 1.77+, stable | <https://rustup.rs> (`rustup-init.exe` on Windows) |
| Node.js | 18 LTS or newer | <https://nodejs.org/> |
| WebView2 runtime | preinstalled on Windows 11 24H2 | n/a |

Verify with:

```powershell
rustc --version
cargo --version
node --version
npm --version
```

If any tool is missing, install it before continuing — see `docs/INSTALL_TAURI_TOOLCHAIN.md`
or follow the official Tauri prerequisites guide:
<https://tauri.app/start/prerequisites/>.

---

## One-time setup

```powershell
cd "C:\Users\Moses Egbunike\Documents\Claude Code Projects\Genesis\desktop"

# Generate placeholder icon set (~1 s, Pillow is already in the project venv)
..\..\..\.venv\Scripts\python.exe scripts\generate_icons.py
# If you cloned to a different depth:
# & ".\.venv\Scripts\python.exe" (from project root) desktop\scripts\generate_icons.py

# Install the Tauri CLI into node_modules
npm install
```

## Running the shell (development)

The shell is a thin wrapper. The actual bot + dashboard must already
be running:

```powershell
# Terminal A — bot + dashboard
cd "C:\Users\Moses Egbunike\Documents\Claude Code Projects\Genesis"
.\.venv\Scripts\python.exe main.py

# Terminal B — native shell
cd "C:\Users\Moses Egbunike\Documents\Claude Code Projects\Genesis\desktop"
npm run dev
```

The first `npm run dev` invocation will fetch crate dependencies
(`tauri`, `wry`, `windows-rs`, `webview2-com`, the plugin crates)
and compile them — expect **5 to 15 minutes** of one-time warm-cache
build time on a clean machine. Subsequent dev runs are sub-second.

While compiling you should see:

```
Compiling tauri v2.x.x
Compiling wry v...
Compiling windows-sys v...
...
Compiling desktop v0.1.0
    Finished `dev` profile [unoptimized + debuginfo] target(s) in 8m 23s
```

When the built binary launches, you'll get:

- A **system tray icon** (gray square at idle). Left-click or
  right-click → menu: `Open Dashboard`, `Open Ask-Claude Panel`, `Quit`.
  Left-click on the tray icon brings the dashboard window to the
  foreground.
- The **dashboard window** points at `http://127.0.0.1:8000`. If the
  dashboard isn't running you'll see a browser "site not reachable"
  error inside the shell — that's expected.
- The **Ask-Claude window** (`Ctrl+`-style secondary panel, 480×720,
  always-on-top) is a placeholder HTML until track (c) lands.

## Single-instance behaviour

If you run `npm run dev` again while the first window is open, the
existing instance is detected (via `tauri-plugin-single-instance`) and
the dashboard window is un-minimized + focused instead of spawning a
second shell.

## Tray icon states

The placeholder tray icon ships in two variants:

- `icons/tray-idle.png` (gray) — installed in `tauri.conf.json` as
  the window default and currently the only state wired. Running state
  would swap to `icons/tray-running.png` (green) once a future release
  hooks a status event from the orchestrator.

## What's intentionally out of scope (MVP)

- **Installer / `cargo tauri build`.** No `msi` / `nsis` artifacts are
  produced in this MVP. `bundle.active` is left on for future
  packaging but `npm run build` is documented here as a no-op in the
  current scope.
- **Auto-start.** The shell does not launch `python main.py` itself;
  you start the bot in a separate terminal. Long-term the plan is to
  fold the bot spawn into a `tauri-plugin-shell` command.
- **Native notification plumbing.** Track (d) already pushes trade
  events to Discord and the dashboard REST API.

## File layout

```
desktop/
├── .gitignore
├── package.json
├── README.md
├── scripts/
│   └── generate_icons.py     # one-shot Pillow script for placeholder PNG/ICO
└── src-tauri/
    ├── Cargo.toml
    ├── build.rs
    ├── tauri.conf.json
    ├── capabilities/
    │   └── default.json
    ├── icons/                # populated by generate_icons.py
    ├── static/
    │   └── ask_claude.html
    └── src/
        ├── main.rs
        └── lib.rs
```

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `cargo: command not found` | Install Rust from <https://rustup.rs>; restart the shell so PATH picks up `~/.cargo/bin` |
| `error: linker 'link.exe' not found` | Install "Build Tools for Visual Studio" with the "Desktop development with C++" workload |
| WebView2 missing error | Windows 11 24H2 ships it; older Windows 10 needs the Evergreen runtime from Microsoft |
| Dashboard window blank | Run `python main.py` first — the shell has no fallback dashboard provider |
| `npm install` warns about peer deps | Tauri CLI 2.x is the only required dep; warnings about optional peers are harmless |
| First compile stalls for >20 min | This is expected on cold cache. Re-running `npm run dev` is fast (incremental) |

For deeper issues see <https://tauri.app/start/troubleshooting/>.
