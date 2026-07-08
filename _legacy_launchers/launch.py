"""
Genesis — cross-platform CLI launcher.

Track (a) introduces launch.py to replace the GUI-bound launcher.py
for headless / multi-platform use. Track (b) will replace launcher.py
with a Tauri shell for desktop GUI users; until then, `python launch.py
run` works on Windows / macOS / Linux from the project root.

Subcommands
-----------
    run           Run the bot (default). Forwards any extra args to
                  main.py and sets PYTHONIOENCODING=utf-8 so the Unicode
                  banner renders on Windows consoles.
    check         Run scripts/check_setup.py (preflight diagnostic).
                  Forwards --fix / --md / -- etc.
    tray          Launch the Windows-only pystray tray (launcher.py).
                  On non-Windows hosts, prints guidance and exits 0.
    dashboard     Print the dashboard URL once the bot has bound, then
                  optionally --open in the OS-default browser.
                  Recognised flags: --wait-for-port, --timeout, --open.

Examples
--------
    python launch.py                       # → run
    python launch.py run                   # → run (explicit)
    python launch.py check                 # → preflight only
    python launch.py check --fix           # → preflight + auto-copy .env
    python launch.py dashboard             # → print dashboard URL
    python launch.py dashboard --wait-for-port --open

Note on argparse subcommands + passthrough
------------------------------------------
We deliberately do NOT use argparse.REMAINDER inside subparsers.
argparse subparsers inspect unknown flags before passing the
remainder through, so `python launch.py check --fix` errors with
"unrecognized arguments: --fix". Instead we use parse_known_args
at the root and forward everything into the subcommand handler.
Each subcommand line-for-line accepts its own passthrough / flags.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent

# Auto-re-execute in virtual environment if available and not already inside it
_venv_dir = PROJECT_ROOT / ".venv"
_in_project_venv = sys.executable.lower().startswith(str(_venv_dir).lower() + os.sep)
if not _in_project_venv and _venv_dir.exists():
    _venv_exe = (
        _venv_dir / "Scripts" / "python.exe"
        if sys.platform == "win32"
        else _venv_dir / "bin" / "python"
    )
    if _venv_exe.exists():
        try:
            sys.exit(subprocess.call([str(_venv_exe), __file__, *sys.argv[1:]]))
        except KeyboardInterrupt:
            sys.exit(0)



PREFLIGHT_SCRIPT = PROJECT_ROOT / "scripts" / "check_setup.py"

MAIN_SCRIPT = PROJECT_ROOT / "main.py"
TRAY_SCRIPT = PROJECT_ROOT / "launcher.py"


# ── Output helpers ────────────────────────────────────────────────
def _ok(msg: str) -> None:
    print(f"✅  {msg}")


def _warn(msg: str) -> None:
    print(f"⚠️   {msg}")


def _err(msg: str) -> None:
    print(f"❌  {msg}")


def _info(msg: str) -> None:
    print(f"•   {msg}")


# ── Subcommand handlers ────────────────────────────────────────────
def cmd_run(rest: list[str]) -> int:
    """Run the bot. Equivalent to `python main.py [...]`."""
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    cmd = [sys.executable, str(MAIN_SCRIPT), *rest]
    _info(f"Launching Genesis bot: {' '.join(cmd)}")
    _info(f"cwd: {PROJECT_ROOT}")
    try:
        return subprocess.call(cmd, env=env, cwd=str(PROJECT_ROOT))
    except KeyboardInterrupt:
        return 0


def cmd_check(rest: list[str]) -> int:
    """Run scripts/check_setup.py; forwards --fix / --md / -- etc."""
    cmd = [sys.executable, str(PREFLIGHT_SCRIPT), *rest]
    return subprocess.call(cmd, cwd=str(PROJECT_ROOT))


def cmd_tray(rest: list[str]) -> int:
    """Launch the Windows-only tray. Non-Windows → guidance + exit 0."""
    if sys.platform != "win32":
        _warn("launcher.py is Windows-only (pystray).")
        _info("On this host, use 'python launch.py run' instead.")
        _info("Track (b) replaces launcher.py with a cross-platform Tauri shell.")
        return 0
    cmd = [sys.executable, str(TRAY_SCRIPT), *rest]
    return subprocess.call(cmd, cwd=str(PROJECT_ROOT))


def cmd_dashboard(rest: list[str]) -> int:
    """Print the dashboard URL once DASHBOARD_PORT is bound; optionally open."""
    sub = argparse.ArgumentParser(prog="launch dashboard", add_help=True)
    sub.add_argument("--wait-for-port", action="store_true",
                     help="Block until DASHBOARD_PORT is bound.")
    sub.add_argument("--timeout", type=int, default=30,
                     help="Seconds before giving up (with --wait-for-port).")
    sub.add_argument("--open", action="store_true", dest="open_browser",
                     help="Open the URL in the OS-default browser once reachable.")
    sn, extra = sub.parse_known_args(rest)
    if extra:
        _warn(f"Unrecognized dashboard args (ignored): {extra}")

    host_loopback = "127.0.0.1"
    raw_port = os.environ.get("DASHBOARD_PORT", "8000")
    try:
        port = int(raw_port)
    except ValueError:
        _err(f"DASHBOARD_PORT is not numeric: {raw_port!r}")
        return 2
    url = f"http://{host_loopback}:{port}"

    if sn.wait_for_port:
        deadline = time.time() + sn.timeout
        while time.time() < deadline:
            if _port_open(host_loopback, port):
                break
            time.sleep(0.5)
        else:
            _err(f"Timed out waiting for {url} after {sn.timeout}s.")
            _info("Is the bot running? Try `python launch.py run` in another terminal.")
            return 1

    if _port_open(host_loopback, port):
        _ok(f"Dashboard is live: {url}")
    else:
        _warn(f"Port {port} is not bound yet.")
        _info("Start the bot with `python launch.py run`, then rerun with --wait-for-port.")

    if sn.open_browser:
        try:
            webbrowser.open(url)
            _info(f"Opened {url} in your default browser.")
        except Exception as e:  # pragma: no cover — environment-dependent
            _warn(f"Couldn't open browser automatically: {e}")
    return 0


def _port_open(host: str, port: int) -> bool:
    """Probe TCP connect; returns True iff a listener is bound on host:port."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


# ── Top-level parser ───────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="launch",
        description="Genesis cross-platform CLI launcher.",
    )
    sub = p.add_subparsers(dest="cmd", metavar="CMD")
    sub.add_parser("run", help="Run the bot (default).")
    sub.add_parser("check", help="Run preflight diagnostic.")
    sub.add_parser("tray", help="Launch Windows tray (no-op elsewhere).")
    sub.add_parser("dashboard", help="Print / open the dashboard URL.")
    return p


def _verify_deps() -> bool:
    """Soft-check that critical runtime deps are importable.

    When the user runs launch.py on a fresh box without `pip install -e .`,
    this prints a one-line fix instead of letting main.py crash mid-startup
    with an ImportError. Each module listed here is one the bot's runtime
    path reaches within the first second of start; missing any of them
    guarantees the same crash downstream.
    """
    missing: list[str] = []
    candidates = [
        "loguru", "pydantic", "pydantic_settings", "sqlalchemy",
        "fastapi", "uvicorn", "discord", "aiohttp",
    ]
    if sys.platform == "win32":
        candidates.append("MetaTrader5")
    for mod in candidates:
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    if missing:
        _err(f"Missing Python dependencies: {', '.join(missing)}")
        _info("Install them with one of:")
        _info("    pip install -e .")
        _info("    pip install -r requirements.txt")
        _info("    pip install -r requirements-mac-linux.txt   (non-Windows hosts)")
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    raw = argv if argv is not None else sys.argv[1:]
    # Default to `run` when no subcommand was given, or when the first
    # token looks like a flag (the user meant run, not a sub-command).
    # We deliberately let help/version through so `launch.py --help`
    # prints the full subcommand list.
    if not raw or (
        raw[0] not in {"run", "check", "tray", "dashboard"}
        and raw[0] not in {"-h", "--help", "-V", "--version"}
    ):
        raw = ["run", *raw]
    ns, unknown = parser.parse_known_args(raw)
    rest = unknown

    # Friendly deps-not-installed check. Skip when we're already routing
    # to `check` (the preflight has its own message) or to `dashboard`
    # (which doesn't need project deps).
    if ns.cmd == "run" and not _verify_deps():
        return 1

    handlers = {
        "run": cmd_run,
        "check": cmd_check,
        "tray": cmd_tray,
        "dashboard": cmd_dashboard,
    }
    return handlers[ns.cmd](rest)


if __name__ == "__main__":
    sys.exit(main())
