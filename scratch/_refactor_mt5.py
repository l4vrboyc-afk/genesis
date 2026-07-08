"""
MT5 lock-discipline refactor — patch script.
Routes every MT5 call through the AsyncMt5Executor that already exists in
``bot/core/orchestrator.py``. Eliminates lock bypass via
``asyncio.to_thread(lambda: mt5.fn(...))`` and direct sync ``mt5.*`` calls
on the event loop.

Idempotent-ish: re-running with no source changes is a no-op.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORE = ROOT / "bot" / "core"

# Use chr() for any code-path characters that historically got eaten by the
# harness when adjacent to JS template literals. The Python source here is
# pure ASCII but lets us assert ``}`` and ``<`` arrive intact.
LB = chr(123)
RB = chr(125)
LT = chr(60)
GT = chr(62)

def patch(path, label, old, new):
    src = path.read_text(encoding='utf-8')
    if old not in src:
        print(f"  [skip] {path.name} :: {label} (no match)")
        return False
    n = src.count(old)
    if n > 1:
        print(f"  [warn] {path.name} :: {label} matched {n} times; replacing first only")
    path.write_text(src.replace(old, new, 1), encoding='utf-8')
    print(f"  [ok]   {path.name} :: {label}")
    return True

# ─────────────────────────────────────────────────────────────────────────────
# 1) bot/core/mt5_connector.py
# ─────────────────────────────────────────────────────────────────────────────
mc = CORE / "mt5_connector.py"

# 1.1 — Add ``self._executor = None`` to __init__ and an attach_executor() method
init_old = (
    '        ``None`` (default) preserves the legacy sync-only behaviour.\n'
    '        self._status_callback = None\n'
    '\n'
    '    # ' + LB + '─' * 51 + ' Connection '
    + LB + LB + '─' * 5 + '\n'
)

init_new = (
    '        ``None`` (default) preserves the legacy sync-only behaviour.\n'
    '        self._status_callback = None\n'
    '\n'
    '        # Serialised MT5 executor — set by ``TradingOrchestrator.start``\n'
    '        # immediately after the worker thread is alive. Every public\n'
    '        # ``async`` method on this class routes its native ``mt5.*``\n'
    '        # calls through ``self._executor.submit(self._<name>_sync, ...)``\n'
    '        # so a single worker thread + the executor\'s internal lock are\n'
    '        # the only path to the MetaTrader IPC. ``None`` is acceptable\n'
    '        # only on test paths or after ``stop()`` when the executor\n'
    '        # itself has been taken down — at that point the loop is going\n'
    '        # away so direct ``mt5.shutdown()`` is safe.\n'
    '        self._executor = None\n'
    '\n'
    '    def attach_executor(self, executor) -> None:\n'
    '        """Inject the serialised MT5 executor.\n'
    '\n'
    '        Called once from ``TradingOrchestrator.start`` *after*\n'
    '        ``await executor.start()`` so the executor\'s worker thread is\n'
    '        already pumping. Passing ``None`` is a deliberate unhook\n'
    '        (debug / test only).\n'
    '        """\n'
    '        self._executor = executor\n'
    '\n'
    '    # ' + LB + '─' * 51 + ' Connection '
    + LB + LB + '─' * 5 + '\n'
)
patch(mc, '1.1 init: _executor + attach_executor', init_old, init_new)

# 1.2 — replace the connect() block: make it async + rename body to _connect_sync
connect_old = (
    '    def connect(self) -> bool:\n'
    '        """\n'
    '        Initialize and connect to MT5 terminal.\n'
    '        Returns True if connection is successful.\n'
    '        """\n'
)

connect_new = (
    '    async def connect(self) -> bool:\n'
    '        """\n'
    '        Initialize and connect to MT5 terminal.\n'
    '        Async wrapper — runs the sync body on the executor\'s worker\n'
    '        thread so the asyncio event loop stays free during the\n'
    '        multi-second ``mt5.initialize`` + ``account_info`` handshake.\n'
    '        Returns True if connection is successful.\n'
    '        """\n'
    '        return await self._executor.submit(self._connect_sync)\n'
    '\n'
    '    def _connect_sync(self) -> bool:\n'
    '        """Sync body for ``connect`` — runs on the executor\'s worker thread."\n'
    '        """\n'
)
patch(mc, '1.2 connect: async + _connect_sync', connect_old, connect_new)
