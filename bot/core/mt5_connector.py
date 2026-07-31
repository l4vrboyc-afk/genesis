"""
MT5 Connector — Handles connection to MetaTrader 5 terminal.
Features: auto-reconnection, connection health monitoring, graceful shutdown.
"""

import asyncio
import sys
from datetime import datetime
from pathlib import Path

import MetaTrader5 as mt5
from loguru import logger
from bot.config.settings import settings


# Default install path mirrors bot/config/settings.py. Used for the
# "you probably need this" suggestion when MT5_PATH is empty.
_WIN_DEFAULT_MT5 = r"C:\Program Files\MetaTrader 5\terminal64.exe"

# Single locked-account server — profile switching must never change this.
_LOCKED_SERVER = "MetaQuotes-Demo"

# Known MT5 last_error_code codes → human-readable remediation hints.
# Codes observed in the wild (and from the MetaTrader5 docs).
_MT5_ERROR_HINTS: dict[int, str] = {
    -1:   "terminal64.exe is missing or MT5_PATH is wrong. Install MetaTrader 5 from your broker, or correct MT5_PATH in .env.",
    -2:   "wrong MT5_LOGIN or MT5_PASSWORD. Confirm credentials against your broker.",
    -3:   "broker server unreachable. Verify MT5_SERVER matches your broker's exact server string (case-sensitive).",
    -4:   "MetaTrader 5 terminal is not running. Launch terminal64.exe before starting Genesis.",
    -5:   "'Allow algorithmic trading' is disabled in MT5. Open Tools → Options → Expert Advisors and tick both checkboxes.",
    -6:   "MetaTrader5 Python SDK is outdated. Run: pip install -U MetaTrader5",
    -10005: "MT5 terminal lost connection to the broker. Wait for it to recover, or restart the terminal.",
}


def _explain_mt5_error(error) -> str:
    """Format a `mt5.last_error()` payload with the closest known fix.

    On most platforms `last_error()` returns a `(code, description)`
    tuple, but on a few builds it's just an integer or None. Handle
    all three without crashing.
    """
    if error is None:
        return "no error reported"
    try:
        # Pad to length 2 so unpacking always works regardless of shape.
        code, description = (tuple(error) + ("",))[:2][:2]  # type: ignore[arg-type]
    except Exception:
        code, description = (None, str(error))
    parts: list[str] = []
    if code is not None:
        parts.append(f"code={code!r}")
    if description:
        parts.append(f"description={description!r}")
    hint = _MT5_ERROR_HINTS.get(code) if isinstance(code, int) else None  # type: ignore[arg-type]
    if hint:
        parts.append(f"hint: {hint}")
    return "; ".join(parts) or str(error)


class MT5Connector:
    """Manages the connection lifecycle with MetaTrader 5."""

    def __init__(self):
        self._connected = False
        self._last_heartbeat = None
        self._reconnect_attempts = 0
        self._max_reconnect_attempts = 10
        self._reconnect_delay = 5  # seconds
        self._account_info = None

        # Track (d) — Optional hook for connection-state transitions.
        # Caller passes a ``callable(connected: bool, reason: str)``.
        # Wired by the orchestrator to forward transitions to Discord.
        # ``None`` (default) preserves the legacy sync-only behaviour.
        self._status_callback = None

        # Serialised MT5 executor -- set by ``TradingOrchestrator.start``
        # immediately after the worker thread is alive. Every public
        # ``async`` method on this class routes its native ``mt5.*`` calls
        # through ``self._executor.submit(self._<name>_sync, ...)`` so the
        # single worker thread + the executor's internal ``threading.Lock``
        # are the only path to the MT5 IPC. ``None`` is acceptable only on
        # test paths or after ``stop()`` when the executor is gone (then
        # the loop is dying anyway so direct ``mt5.shutdown()`` is safe).
        self._executor = None

    def attach_executor(self, executor) -> None:
        """Inject the serialised MT5 executor.

        Called once from ``TradingOrchestrator.start`` AFTER
        ``await executor.start()`` so the executor's worker thread is
        already pumping. Passing ``None`` is a deliberate unhook
        (debug / test only).
        """
        self._executor = executor

    # ── Connection ──────────────────────────────────────────────────

    async def connect(self) -> bool:
        """
        Initialize and connect to MT5 terminal.
        Async wrapper — runs the sync body on the executor's worker
        thread so the asyncio event loop stays free during the
        multi-second ``mt5.initialize`` + ``account_info`` handshake.
        Returns True if connection is successful.
        """
        return await self._executor.submit(self._connect_sync)

    def _connect_sync(self) -> bool:
        """Sync body for ``connect`` — runs on the executor's worker thread.
        Has no lock of its own; the executor's worker thread + internal
        ``threading.Lock`` give the right serialization for free.
        """
        logger.info("🔌 Connecting to MetaTrader 5...")

        # ── Validate the terminal path BEFORE mt5.initialize. The
        # native call would otherwise drop a cryptic `(-1, 'No IPC')
        # tuple into mt5.last_error(), which doesn't tell a non-author
        # what to fix. ─────────────────────────────────────────────
        if not settings.mt5_path:
            logger.error(
                f"❌ MT5_PATH is empty. On {sys.platform}, install MetaTrader 5 "
                f"from your broker and set MT5_PATH in .env "
                f"(Windows default: {_WIN_DEFAULT_MT5!r})."
            )
            return False
        if not Path(settings.mt5_path).is_file():
            logger.error(
                f"❌ MT5_PATH does not point to an existing file: "
                f"{settings.mt5_path!r}. Install MetaTrader 5 from your broker, "
                f"or correct MT5_PATH in .env. Run "
                f"`python scripts/check_setup.py` for a preflight report."
            )
            return False

        # ── Surface placeholder credentials. Without this, paper-mode
        # users with unfilled MT5_LOGIN/PASSWORD get silent order
        # rejections on every cycle. ────────────────────────────────
        pwd_lower = (settings.mt5_password or "").lower()
        srv_lower = (settings.mt5_server or "").lower()
        creds_look_placeholder = (
            settings.mt5_login == 0
            or not settings.mt5_password
            or any(marker in pwd_lower for marker in ("your_", "placeholder", "<password>", "changeme"))
            or any(marker in srv_lower for marker in ("your_", "placeholder", "<server>", "changeme"))
        )
        if creds_look_placeholder:
            msg = (
                "MT5 credentials look like placeholders (MT5_LOGIN=0 or "
                "MT5_PASSWORD / MT5_SERVER unset). Edit .env (or .env.<profile> "
                "when GENESIS_PROFILE is set) and fill MT5_LOGIN, MT5_PASSWORD, "
                "MT5_SERVER with real broker values. Paper-trading still needs "
                "a real demo login."
            )
            if settings.paper_trading:
                logger.warning(f"⚠️ {msg}")
            else:
                logger.error(f"❌ {msg}")

        # Initialize MT5
        if not mt5.initialize(
            path=settings.mt5_path,
            login=settings.mt5_login,
            password=settings.mt5_password,
            server=settings.mt5_server,
        ):
            error = mt5.last_error()
            logger.error(f"❌ MT5 initialization failed: {_explain_mt5_error(error)}")
            return False

        # Verify login
        account = mt5.account_info()
        if account is None:
            logger.error("❌ Failed to get account info after initialization")
            mt5.shutdown()
            return False

        # ── Server guard: single locked account — always MetaQuotes-Demo ──
        if account.server != _LOCKED_SERVER:
            logger.error(
                f"❌ WRONG SERVER: MT5 connected to '{account.server}' but Genesis "
                f"is locked to '{_LOCKED_SERVER}'. Update MT5_SERVER in your .env "
                f"file to '{_LOCKED_SERVER}' and restart. Profile switching does "
                f"NOT change accounts — all profiles trade on {_LOCKED_SERVER}."
            )
            mt5.shutdown()
            return False

        self._connected = True
        self._last_heartbeat = datetime.now()
        self._reconnect_attempts = 0
        self._account_info = account
        self._fire_status(True, "connect")

        logger.success(f"✅ Connected to MT5 — Account: {account.login}")
        logger.info(f"   Server: {account.server}")
        logger.info(f"   Balance: ${account.balance:.2f}")
        logger.info(f"   Leverage: 1:{account.leverage}")
        logger.info(f"   Mode: {'Demo' if account.trade_mode == 0 else 'Live'}")

        return True

    async def disconnect(self):
        """Gracefully shut down MT5 connection. Async — runs ``mt5.shutdown`` on the executor's worker thread."""
        if self._executor is not None:
            await self._executor.submit(self._disconnect_sync)
        else:
            # Executor gone (shutdown path) — call sync directly.
            self._disconnect_sync()

    def _disconnect_sync(self):
        """Sync body for ``disconnect`` — runs on the executor's worker thread."""
        if self._connected:
            mt5.shutdown()
            self._connected = False
            self._fire_status(False, "disconnect")
            logger.info("🔌 Disconnected from MetaTrader 5")

    async def reconnect(self) -> bool:
        """
        Attempt to reconnect to MT5 with exponential backoff.

        Async — uses `await asyncio.sleep` so the bot's asyncio loop stays
        responsive during the (possibly multi-minute) backoff. The previous
        sync implementation blocked the event loop with `time.sleep`, which
        froze the dashboard server, Discord listener, and any pending MT5
        reads while we waited.

        Returns True if reconnection is successful.
        """
        self._reconnect_attempts += 1

        if self._reconnect_attempts > self._max_reconnect_attempts:
            logger.critical(
                f"💀 Max reconnection attempts ({self._max_reconnect_attempts}) reached. Giving up."
            )
            self._connected = False
            self._fire_status(False, "max_retries_exhausted")
            return False

        delay = self._reconnect_delay * (2 ** (self._reconnect_attempts - 1))
        delay = min(delay, 300)  # Cap at 5 minutes

        logger.warning(
            f"🔄 Reconnection attempt {self._reconnect_attempts}/{self._max_reconnect_attempts} "
            f"in {delay}s..."
        )

        # Shutdown any existing (possibly stale) connection first so we don't
        # hold a dead socket during the backoff. Routed through the executor
        # so it serialises against in-flight reads (reconnect()'s docstring
        # used to claim this; we made it true).
        try:
            await self._executor.submit(mt5.shutdown)
        except Exception:
            pass

        # Async sleep — does NOT block the event loop.
        await asyncio.sleep(delay)

        return await self.connect()

    def set_status_callback(self, callback) -> None:
        """Register a callable fired on connection-state transitions.

        Track (d): the orchestrator hooks its transition notifications
        into this so ``connect`` / ``disconnect`` / give-up paths can
        propagate state changes via a single subscription. Pass ``None``
        to remove the callback.

        Signature: ``callback(connected: bool, reason: str)``.
        Errors raised by the callback are swallowed (logged at debug)
        — never propagate to the connector.
        """
        self._status_callback = callback if callback is None or callable(callback) else None

    def _fire_status(self, connected: bool, reason: str = "") -> None:
        """Internal — invoke the registered status callback if any."""
        cb = self._status_callback
        if cb is None:
            return
        try:
            cb(connected, reason)
        except Exception as e:  # pragma: no cover — best-effort
            logger.debug(f"MT5 status callback raised (ignored): {e}")

    async def is_connected(self) -> bool:
        """Check if MT5 terminal is still connected and responsive. Async — runs the ping on the executor's worker thread."""
        if not self._connected:
            return False
        return await self._executor.submit(self._is_connected_ping_sync)

    def _is_connected_ping_sync(self) -> bool:
        """Sync body for ``is_connected`` — runs on the executor's worker thread.

        Returns True iff connected; sets ``self._connected = False`` on detection
        of a severed link.
        """
        try:
            # Ping MT5 by requesting terminal info
            info = mt5.terminal_info()
            if info is None:
                logger.warning("⚠️ MT5 terminal not responding")
                self._connected = False
                return False

            if not info.connected:
                logger.warning("⚠️ MT5 terminal lost broker connection")
                self._connected = False
                return False

            self._last_heartbeat = datetime.now()
            return True

        except Exception as e:
            logger.error(f"❌ MT5 health check failed: {e}")
            self._connected = False
            return False

    async def ensure_connected(self) -> bool:
        """Async wrapper — delegates to ``is_connected``.

        Sync callers (tests / shutdown helpers) can still use
        ``_ensure_connected_cached()`` which returns the cached boolean
        without touching MT5.
        """
        return await self.is_connected()

    def _ensure_connected_cached(self) -> bool:
        """Cached-only check — does no MT5 I/O. Safe for sync / shutdown paths."""
        return self._connected

    # ── Account Info ────────────────────────────────────────────────

    async def get_account_info(self) -> dict:
        """Get current account information as a dictionary. Async — runs ``mt5.account_info`` on the executor's worker thread."""
        if not await self.ensure_connected():
            return {}

        account = await self._executor.submit(self._get_account_info_sync)
        if account is None:
            return {}

        # Refresh the cache so the sync ``balance`` / ``equity`` / ``free_margin``
        # @property methods see a non-stale value.
        self._account_info = account

        return {
            "login": account.login,
            "server": account.server,
            "balance": account.balance,
            "equity": account.equity,
            "margin": account.margin,
            "free_margin": account.margin_free,
            "margin_level": account.margin_level if account.margin_level else 0,
            "profit": account.profit,
            "leverage": account.leverage,
            "currency": account.currency,
            "trade_mode": "Demo" if account.trade_mode == 0 else "Live",
        }

    def _get_account_info_sync(self):
        """Sync body for ``get_account_info`` — runs on the executor's worker thread."""
        return mt5.account_info()

    @property
    def balance(self) -> float:
        """Most-recently cached account balance.

        Sync property — reads the cached value populated by the async
        ``get_account_info()`` (called from the orchestrator's ``/api/status``
        endpoint and on each cycle). No fresh MT5 I/O here; sync callers
        that need refreshed values should call ``asyncio.run_coroutine_threadsafe``
        against ``get_account_info()`` from their own loop.
        """
        return self._account_info.balance if self._account_info else 0.0

    @property
    def equity(self) -> float:
        """Most-recently cached equity. See ``balance`` for refresh caveat."""
        return self._account_info.equity if self._account_info else 0.0

    @property
    def free_margin(self) -> float:
        """Most-recently cached free margin. See ``balance`` for refresh caveat."""
        return self._account_info.margin_free if self._account_info else 0.0

    # ── Symbol Info ─────────────────────────────────────────────────

    async def get_symbol_info(self, symbol: str) -> dict:
        """Get information about a trading symbol. Async — runs ``mt5.symbol_info`` on the executor's worker thread."""
        if not await self.ensure_connected():
            return {}

        info = await self._executor.submit(self._get_symbol_info_sync, symbol)
        if info is None:
            logger.warning(f"⚠️ Symbol {symbol} not found")
            return {}

        return {
            "symbol": info.name,
            "bid": info.bid,
            "ask": info.ask,
            "spread": info.spread,
            "digits": info.digits,
            "point": info.point,
            "volume_min": info.volume_min,
            "volume_max": info.volume_max,
            "volume_step": info.volume_step,
            "trade_contract_size": info.trade_contract_size,
            "trade_tick_value": info.trade_tick_value,
            "trade_tick_size": info.trade_tick_size,
        }

    def _get_symbol_info_sync(self, symbol: str):
        """Sync body for ``get_symbol_info`` — runs on the executor's worker thread."""
        return mt5.symbol_info(symbol)

    async def enable_symbol(self, symbol: str) -> bool:
        """Enable a symbol for trading in Market Watch. Async — runs ``mt5.symbol_select`` on the executor's worker thread."""
        if not await self.ensure_connected():
            return False

        ok = await self._executor.submit(self._enable_symbol_sync, symbol)
        if not ok:
            logger.error(f"❌ Failed to enable symbol {symbol}")
            return False

        logger.debug(f"✅ Symbol {symbol} enabled in Market Watch")
        return True

    def _enable_symbol_sync(self, symbol: str) -> bool:
        """Sync body for ``enable_symbol`` — runs on the executor's worker thread."""
        return mt5.symbol_select(symbol, True)

    async def enable_all_pairs(self) -> bool:
        """Enable all configured trading pairs in Market Watch. Async — loops via the executor's worker thread."""
        success = True
        for pair in settings.trading_pairs:
            if not await self.enable_symbol(pair):
                success = False
        return success

    # ── Context Manager ─────────────────────────────────────────────

    def __enter__(self):
        # Sync context-manager entry — calls the sync body directly. No
        # callers in the codebase; if you find a need for ``with`` over
        # ``async with``, prefer using ``await asyncio.to_thread(self._connect_sync)``
        # so the bound executor still serialises the call.
        return self._connect_sync() or self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._disconnect_sync()
        return False
