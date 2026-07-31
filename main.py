"""
Genesis Trading Bot — Main Entry Point.
Bootstrap connections, load environment variables, and run
FastAPI Dashboard, Discord Bot, and Orchestrator Loops concurrently under asyncio.
"""

import asyncio
import subprocess
import sys
import os
from pathlib import Path

# ── UTF-8 bootstrap — must happen before ANY import that touches stdout ────────
# PYTHONUTF8=1 is Python 3.7+ global UTF-8 mode: covers file I/O, subprocesses,
# and streams that reconfigure() alone can miss (e.g. PyInstaller builds).
os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
for _s in ("stdout", "stderr"):
    _st = getattr(sys, _s, None)
    if _st and hasattr(_st, "reconfigure"):
        try:
            _st.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

# Auto-re-execute in virtual environment if available and not already inside it
_project_root = Path(__file__).resolve().parent
_venv_dir = _project_root / ".venv"
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


import uvicorn
from dotenv import load_dotenv
from loguru import logger

# ── MT5 env-var keys that the credential cascade protects ─────────────
# Defined here rather than imported from ``bot.config.env_utils`` because
# ``from bot.config.…`` triggers ``bot/config/__init__.py`` which imports
# ``from bot.config.settings import settings``, creating the settings
# singleton before the profile environment file has been loaded — and
# before ``os.environ`` has the correct values.
_MT5_KEYS = ("MT5_LOGIN", "MT5_PASSWORD", "MT5_SERVER", "MT5_PATH")
_PLACEHOLDER_VALUES = frozenset({
    "your_login", "your_password", "your_server",
    "changeme", "placeholder", "<password>", "<server>",
})
def _is_placeholder(value: str) -> bool:
    return not value or value == "0" or value.lower() in _PLACEHOLDER_VALUES

def _preserve_mt5_credentials(profile_env_path, load_dotenv_func):
    """Snapshot MT5 env vars, load profile env, restore placeholders."""
    snapshot = {k: os.environ[k] for k in _MT5_KEYS if os.environ.get(k)}
    load_dotenv_func(profile_env_path, override=True)
    for key, base_value in snapshot.items():
        current = os.environ.get(key, "").strip()
        if _is_placeholder(current):
            os.environ[key] = base_value

# (UTF-8 bootstrap already applied above at module load, before any import)

# Load environment variables — profile-aware merge so a non-default
# profile (set via GENESIS_PROFILE by the GUI launcher) inherits keys
# from the base .env and overrides only what it specializes (port,
# strategy params, db).
#
# The order is CRITICAL:
#   1. Load base .env first  → puts real MT5_LOGIN/PASSWORD into os.environ
#   2. Load profile .env with MT5 cascade → puts DASHBOARD_PORT into
#      os.environ, but restores any MT5 credential the profile wiped
#      with a placeholder.
#   3. THEN import settings → pydantic-settings reads the profile .env
#      file, then checks os.environ: DASHBOARD_PORT=8003 matches the
#      file, MT5_LOGIN=<real> overrides the file's placeholder "0".
#
# We inline the MT5 cascade here instead of importing from
# ``bot.config.env_utils`` because ANY ``from bot.config.…`` triggers
# ``bot/config/__init__.py → from bot.config.settings import settings``,
# creating the singleton before step 2 has populated os.environ with
# the profile-specific values.
_profile = os.getenv("GENESIS_PROFILE")
load_dotenv(dotenv_path=_project_root / ".env", override=False)
if _profile:
    _pf = _project_root / f".env.{_profile.lower()}"
    if _pf.exists():
        # ── MT5 credential cascade (inlined) ───────────────────────────
        # Profile .env files only specialise strategy params.  If they
        # contain MT5_LOGIN=0 or empty MT5_PASSWORD, ``override=True``
        # would wipe the real credentials from the base .env.  The
        # cascade snapshots before loading and restores wiped values.
        _preserve_mt5_credentials(_pf, load_dotenv)

# Import settings AFTER loading profile env so log_file is correct
from bot.config.settings import settings

# Configure logger using profile's log_file
logger.remove()
logger.add(
    sys.stdout,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{module}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
    level=settings.log_level,
)
logger.add(
    settings.log_file,
    rotation="10 MB",
    retention="14 days",
    compression="zip",
    encoding="utf-8",  # Explicit UTF-8 — prevents CP1252 garbling on Windows
    level="DEBUG",
)


async def main():
    """Bootstrap and orchestrate the Genesis services."""

    # Avoid circular imports by importing inside main
    from bot.core.orchestrator import TradingOrchestrator
    from bot.notifications.discord_bot import DiscordBot
    from dashboard.backend.main import create_app

    # 1. Instantiate the orchestrator
    orchestrator = TradingOrchestrator()

    # 2. Setup FastAPI Application
    app = create_app(orchestrator)
    uvicorn_config = uvicorn.Config(
        app,
        host=settings.dashboard_host,
        port=settings.dashboard_port,
        log_level="warning",
        loop="asyncio",
    )
    uvicorn_server = uvicorn.Server(uvicorn_config)
    app.state.uvicorn_server = uvicorn_server

    # 3. Setup Discord Bot
    discord_bot = None
    if settings.discord_token and settings.discord_channel_id > 0:
        logger.info("Configuring Discord Bot integration...")
        discord_bot = DiscordBot(orchestrator)
    else:
        logger.warning("Discord Bot token or Channel ID missing. Discord notifications will be disabled.")
    # Expose the Discord bot on app.state so the control route's
    # switch_profile action can close it during a graceful shutdown.
    # gather() awaits the discord task too, so without closing it the
    # process would never exit when the user switches profiles.
    app.state.discord_bot = discord_bot

    # 4. All services run concurrently — do NOT await orchestrator.start()
    # sequentially before the dashboard, or the port stays silent until
    # MT5 connects (30+ seconds), causing the launcher's port-wait to expire.
    orchestrator_task = asyncio.create_task(orchestrator.start())
    # Expose the start() task so switch_profile can cancel it if a user
    # clicks the header button before startup finished (e.g. stuck mid
    # MT5 connect) — otherwise gather() would block and the bot would
    # never exit to return the launcher to the picker.
    app.state.orchestrator_task = orchestrator_task
    uvicorn_task = asyncio.create_task(uvicorn_server.serve())
    if discord_bot is not None:
        discord_task = asyncio.create_task(discord_bot.start(settings.discord_token))
    else:
        discord_task = None

    logger.info(f"Starting Web Dashboard on http://{settings.dashboard_host}:{settings.dashboard_port}")

    # 5. Execute services and wait
    try:
        tasks = [orchestrator_task, uvicorn_task]
        if discord_task is not None:
            tasks.append(discord_task)
        await asyncio.gather(*tasks)
    except KeyboardInterrupt:
        logger.info("Termination signal received...")
    except asyncio.CancelledError:
        logger.info("Task cancellation received...")
    finally:
        # Graceful Shutdown
        logger.info("Commencing graceful shutdown of all services...")

        # Stop trading bot loops
        await orchestrator.stop()

        # Stop discord connection
        if discord_bot and not discord_bot.is_closed():
            logger.info("Disconnecting Discord Bot...")
            await discord_bot.close()

        # Stop FastAPI server
        logger.info("Stopping Web Dashboard API...")
        uvicorn_server.should_exit = True

        logger.success("Genesis System successfully halted.")


# Project paths (resolved once at module load — needed below for preflight)
PROJECT_ROOT = Path(__file__).resolve().parent
PREFLIGHT_SCRIPT = PROJECT_ROOT / "scripts" / "check_setup.py"

if __name__ == "__main__":
    # Ensure logs folder exists
    os.makedirs("logs", exist_ok=True)

    # Preflight setup check — runs scripts/check_setup.py as a
    # subprocess so its colored / Markdown output stays isolated from
    # the bot's logger formatting and so users always see actionable
    # errors BEFORE the orchestrator's async startup churn.
    #
    # When launched from the GUI (GENESIS_LAUNCHED_BY=gui), the
    # launcher already ran preflight — skip the duplicate to shave
    # ~2 seconds off the 5-second target.
    _launched_by_gui = os.environ.get("GENESIS_LAUNCHED_BY") == "gui"
    if not _launched_by_gui:
        rc = subprocess.call(
            [sys.executable, str(PREFLIGHT_SCRIPT), *sys.argv[1:]],
            cwd=str(PROJECT_ROOT),
        )
        if rc != 0:
            sys.exit(
                "Genesis preflight failed — see output above. "
                "Re-run `python scripts/check_setup.py` once issues are fixed."
            )

    # Single highlighted startup block. The dashboard URL appears here,
    # *before* any other output, so the user never scrapes the log for
    # it. The profile, paper-trading flag, and Discord enable bit
    # surface enough context to confirm the bot is the right one.
    dashboard_url = f"http://127.0.0.1:{settings.dashboard_port}"
    lan_url = (
        f"http://<lan-ip>:{settings.dashboard_port} (DASHBOARD_HOST={settings.dashboard_host})"
        if settings.dashboard_host in ("0.0.0.0", "")
        else None
    )
    discord_enabled = bool(settings.discord_token and settings.discord_channel_id > 0)
    banner = (
        "\n"
        + "=" * 60
        + "\n"
        + f" Genesis Trading Bot is starting\n"
        + f" Dashboard: {dashboard_url}\n"
        + (f" {lan_url}\n" if lan_url else "")
        + f" Profile: {settings.active_profile}\n"
        + f" Paper mode: {'yes (demo)' if settings.paper_trading else 'NO — LIVE MONEY'}\n"
        + f" Discord: {'enabled' if discord_enabled else 'disabled'}\n"
        + "=" * 60
    )
    logger.info(banner)

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutdown requested.")
        sys.exit(0)
