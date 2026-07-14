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

# Force UTF-8 on stdout/stderr so Unicode icons in the startup banner
# and log lines render correctly on Windows consoles (default cp1252).
# Mirrors scripts/check_setup.py. Idempotent — safe to run twice.
for _stream_name in ("stdout", "stderr"):
    _stream = getattr(sys, _stream_name, None)
    if _stream and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:  # pragma: no cover — best effort
            pass

# Load environment variables — profile-aware merge so a non-default
# profile (set via GENESIS_PROFILE by the GUI launcher) inherits keys
# from the base .env and overrides only what it specializes (port,
# strategy params, db). This honours the "falls back to .env for any
# key not present here" contract documented in .env.<profile> and stops
# the base profile's DASHBOARD_PORT / MT5 creds from leaking in.
#   1. base .env  — override=False (don't clobber real shell vars)
#   2. profile    — override=True  (profile values win over base)
_profile = os.getenv("GENESIS_PROFILE")
load_dotenv(dotenv_path=_project_root / ".env", override=False)
if _profile:
    _pf = _project_root / f".env.{_profile.lower()}"
    if _pf.exists():
        load_dotenv(dotenv_path=_pf, override=True)

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
    # errors BEFORE the orchestrator's async startup churn. Non-zero
    # exit aborts the launch cleanly with a single reminder.
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
