"""
Genesis — first-run setup check.

Validates the runtime environment BEFORE the bot starts. Runs in ~1 s,
exits non-zero on any failure so CI / pre-launch scripts can rely on it.

Checks performed
----------------
  1. Python version           (must be >= 3.10)
  2. Platform                 (warns on macOS / Linux; MT5 needs Wine / VM)
  3. .env file                (must exist; auto-copied from .env.example
                               when run with --fix)
  4. MT5 install              (MT5_PATH exists when on Windows; SDK import
                               OK on Windows)
  5. MT5 credentials          (login / password / server not placeholders)
  6. Dashboard port           (DASHBOARD_PORT free on 127.0.0.1)
  7. Discord token            (only validated when present; empty is OK)

Usage
-----
    python scripts/check_setup.py          # human-readable; prints colored
                                           #   ok / fail lines; exit 0/1
    python scripts/check_setup.py --fix    # also auto-copy .env.example -> .env
                                           #   when .env is missing
    python scripts/check_setup.py --md     # markdown output for CI / docs

Exit codes
----------
  0   all checks passed
  1   one or more checks failed
  2   misuse (unknown flag, missing .env.example)
"""
from __future__ import annotations

import argparse
import os
import shutil
import socket
import sys
from pathlib import Path

# ── Resolve project root regardless of CWD ────────────────────────
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ── Force UTF-8 on stdout/stderr so non-ASCII icons (✅/❌) render
#    correctly on Windows consoles (default cp1252). Idempotent. ────
for stream_name in ("stdout", "stderr"):
    stream = getattr(sys, stream_name, None)
    if stream and hasattr(stream, "reconfigure"):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:  # pragma: no cover — best effort
            pass

# ── Best-effort .env load so MT5_* / DASHBOARD_* / DISCORD_* get
#    picked up from .env even when the user runs the script without
#    exporting them. python-dotenv is a hard project dependency, so
#    the import is safe. ──────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    # Profile-aware merge: inherit base .env, then overlay the profile
    # file so a non-default profile inherits keys (esp. MT5 creds) from
    # .env and overrides only what it specializes (port, db, strategy
    # params). Mirrors main.py / settings.py and the documented "falls
    # back to .env for any key not present here" contract in .env.<profile>.
    _profile = os.environ.get("GENESIS_PROFILE")
    load_dotenv(dotenv_path=ROOT / ".env", override=False)
    if _profile:
        _pf = ROOT / f".env.{_profile.lower()}"
        if _pf.exists():
            load_dotenv(dotenv_path=_pf, override=True)
except ImportError:  # pragma: no cover — dotenv is in requirements.txt
    pass


# ── Tiny color helpers — degrade gracefully when stdout is not a TTY ─
def _ansi(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if sys.stdout.isatty() else text


def green(s: str) -> str: return _ansi("32", s)
def red(s: str) -> str: return _ansi("31", s)
def yellow(s: str) -> str: return _ansi("33", s)
def bold(s: str) -> str: return _ansi("1", s)


# ── Default MT5 path mirrors bot/config/settings.py ──────────────
WIN_DEFAULT_MT5 = r"C:\Program Files\MetaTrader 5\terminal64.exe"


def _print(label: str, ok: bool, detail: str = "", *, md: bool = False) -> None:
    if md:
        icon = "PASS" if ok else "FAIL"
        print(f"- **{icon}** {label}: {detail or 'ok'}")
        return
    icon = "✅" if ok else "❌"
    line = f"{icon} {bold(label)}"
    if detail:
        line += f"  — {detail}"
    print(line)


# ── Individual checks — each returns (ok: bool, issues: list[str]) ──
def check_python() -> tuple[bool, list[str]]:
    issues: list[str] = []
    v = sys.version_info
    if (v.major, v.minor) < (3, 10):
        issues.append(f"Python 3.10+ required, found {v.major}.{v.minor}.{v.micro}.")
    return (not issues, issues)


def check_platform() -> tuple[bool, list[str]]:
    """Warn on macOS / Linux; OK is treated as 'advisory' since the
    dashboard-only / smoke-test path can still run. MT5 itself requires
    Windows natively (Wine / Parallels / a VPS bridge it)."""
    issues: list[str] = []
    if sys.platform != "win32":
        issues.append(
            "MetaTrader 5 runs natively only on Windows. On macOS / Linux you "
            "need Crossover, Parallels, or a Windows VPS for live trading. "
            "The dashboard can still start; this is informational."
        )
        return (False, issues)  # mark failed -> reminder surfaces to user
    return (True, [])


def check_dotenv(fix: bool, md: bool) -> tuple[bool, list[str]]:
    issues: list[str] = []
    example = ROOT / ".env.example"
    env_path = ROOT / ".env"

    if not example.exists():
        msg = ".env.example is missing from project root — re-clone or restore it."
        if md:
            return (False, [msg])
        return (False, [f"{msg} (this file ships with the repo)"])

    if not env_path.exists():
        if fix:
            shutil.copy(example, env_path)
            msg = "auto-copied .env.example to .env (--fix)"
        else:
            msg = (
                ".env is missing. Copy .env.example to .env, then fill in "
                "MT5_LOGIN, MT5_PASSWORD, MT5_SERVER. Re-run with --fix to "
                "auto-copy the example."
            )
            issues.append(msg)
            return (False, issues)

    # Lightly sanity-check that .env is non-empty (catches accidentally
    # touching into a zero-byte .env).
    if env_path.stat().st_size == 0:
        msg = ".env exists but is empty. Copy .env.example over it again."
        issues.append(msg)
        return (False, issues)

    return (True, [])


def check_mt5_install() -> tuple[bool, list[str]]:
    issues: list[str] = []
    mt5_path = os.environ.get("MT5_PATH", "") or (
        WIN_DEFAULT_MT5 if sys.platform == "win32" else ""
    )
    if sys.platform == "win32":
        if not mt5_path:
            issues.append("MT5_PATH is empty. Set it in .env to your terminal64.exe.")
            return (False, issues)
        if not Path(mt5_path).is_file():
            issues.append(
                f"MT5_PATH does not point to an existing file: {mt5_path!r}. "
                "Install MetaTrader 5 from your broker or fix MT5_PATH in .env."
            )
            return (False, issues)
        # SDK import — only meaningful on Windows
        try:
            import MetaTrader5  # noqa: F401
        except ImportError as e:
            issues.append(
                f"MetaTrader5 Python SDK import failed: {e}. "
                "Run: pip install MetaTrader5"
            )
            return (False, issues)
    elif mt5_path:
        # On non-windows we tolerate MT5_PATH pointing at a Wine /
        # Parallels / VPS path but cannot verify it exists from inside
        # Linux / Mac without extra plumbing. Just note it.
        issues.append(
            f"MT5_PATH is set to {mt5_path!r} on a non-Windows host — verify "
            "this points to a valid Wine / Parallels / VPS bridge."
        )
        return (False, issues)
    return (True, [])


def check_credentials() -> tuple[bool, list[str]]:
    issues: list[str] = []
    if sys.platform != "win32":
        # On Mac / Linux we don't validate credentials — the user is
        # running the dashboard-only path or has a VPS downstream.
        return (True, [])

    login = os.environ.get("MT5_LOGIN", "").strip()
    password = os.environ.get("MT5_PASSWORD", "")
    server = os.environ.get("MT5_SERVER", "").strip()

    # Login: ints only, must be > 0
    if not login:
        issues.append("MT5_LOGIN is unset. Fill in your MT5 account login.")
    else:
        try:
            n = int(login)
            if n <= 0:
                issues.append(
                    f"MT5_LOGIN must be a positive integer; got {login!r}."
                )
        except ValueError:
            issues.append(f"MT5_LOGIN is not numeric: {login!r}.")

    # Password: non-empty, not a placeholder
    placeholder_markers = ("your_password", "your_", "placeholder", "<password>", "changeme")
    if not password or any(m in password.lower() for m in placeholder_markers):
        issues.append(
            "MT5_PASSWORD is unset or a placeholder. Fill in your MT5 account "
            "password (the demo account you intend to use)."
        )

    # Server: non-empty, not placeholder
    if not server or any(m in server.lower() for m in placeholder_markers):
        issues.append(
            "MT5_SERVER is unset or a placeholder. Use your broker's exact "
            "server name (e.g. 'Exness-MT5Trial7', 'ICMarkets-Live06')."
        )
    return (not issues, issues)


def check_dashboard_port() -> tuple[bool, list[str]]:
    issues: list[str] = []
    raw = os.environ.get("DASHBOARD_PORT", "8000")
    try:
        port = int(raw)
    except ValueError:
        issues.append(f"DASHBOARD_PORT is not an integer: {raw!r}.")
        return (False, issues)

    if not (1 <= port <= 65535):
        issues.append(f"DASHBOARD_PORT out of range (1-65535): {port}.")
        return (False, issues)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.5)
    try:
        in_use = sock.connect_ex(("127.0.0.1", port)) == 0
        if in_use:
            issues.append(
                f"DASHBOARD_PORT {port} already in use on 127.0.0.1. Either "
                "stop the conflicting process or change DASHBOARD_PORT in .env."
            )
    except Exception as e:  # pragma: no cover — defensive
        issues.append(f"Could not probe port {port}: {e}")
        return (False, issues)
    finally:
        sock.close()
    return (not issues, issues)


def check_discord() -> tuple[bool, list[str]]:
    issues: list[str] = []
    token = os.environ.get("DISCORD_TOKEN", "").strip()
    if not token or any(m in token.lower() for m in ("your_", "placeholder", "<token>", "changeme")):
        # Empty / placeholder is treated as intentionally disabled.
        return (True, [])
    # A bot token looks like "<id>.<timestamp_hex>.<hmac>" — three
    # dot-separated base64url-ish segments.
    parts = token.split(".")
    if len(parts) != 3 or len(token) < 50:
        issues.append(
            "DISCORD_TOKEN does not look like a Discord bot token "
            "(expected 3 dot-separated segments, ~70+ chars)."
        )
    chan = os.environ.get("DISCORD_CHANNEL_ID", "0").strip()
    try:
        if int(chan) <= 0:
            issues.append(
                "DISCORD_CHANNEL_ID is unset or zero — Discord alerts will "
                "not reach a channel."
            )
    except ValueError:
        issues.append(f"DISCORD_CHANNEL_ID is not numeric: {chan!r}.")

    return (not issues, issues)


# ── Orchestration ──────────────────────────────────────────────────
CHECKS = [
    ("Python version",     check_python),
    ("Platform",           check_platform),
    (".env file",          check_dotenv),
    ("MT5 install",        check_mt5_install),
    ("MT5 credentials",    check_credentials),
    ("Dashboard port",     check_dashboard_port),
    ("Discord token",      check_discord),
]


def run_all(fix: bool, md: bool) -> int:
    if md:
        print("## Genesis — first-run check\n")
    else:
        print(bold("Genesis — first-run check\n"))

    failures = 0
    for label, fn in CHECKS:
        # Pass `fix` and `md` only where applicable
        if fn is check_dotenv:
            ok, issues = fn(fix=fix, md=md)
        else:
            ok, issues = fn()
        detail = "; ".join(issues) if issues else ("ok" if not md else "")
        _print(label, ok, detail, md=md)
        if not ok:
            failures += 1
    print()
    if failures:
        msg = (
            f"❌  {failures} check(s) failed. Resolve them above and "
            "re-run `python scripts/check_setup.py`."
        )
        print(red(msg) if not md else f"**Result:** {failures} failure(s).")
        return 1
    msg = "✅  All checks passed. Run `python main.py` to start Genesis."
    print(green(msg) if not md else "**Result:** all checks passed.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="First-run check for Genesis Trading Bot.",
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Auto-copy .env.example to .env when missing.",
    )
    parser.add_argument(
        "--md", "--markdown",
        action="store_true",
        help="Emit a Markdown report instead of human-readable lines.",
    )
    args = parser.parse_args(argv)
    return run_all(fix=args.fix, md=args.md)


if __name__ == "__main__":
    sys.exit(main())
