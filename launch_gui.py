"""Genesis GUI Launcher (launch_gui.py)
Drop Genesis.exe next to main.py and double-click. The launcher:

1. Locates the project root (sibling of the .exe / __file__).
2. Checks for ``--version`` CLI flag and exits early if present.
3. Auto-copies ``.env.example`` -> ``.env`` if the latter is missing.
4. Runs scripts/check_setup.py as a preflight check.
5. Verifies the Edge WebView2 runtime is installed (Windows).
6. Opens the profile picker (gui/profile_picker.html) in a pywebview native window.
7. On profile selection: kills any existing bot, sets GENESIS_PROFILE, spawns
   ``main.py`` via the venv Python, waits for the profile-specific port, then
   navigates the webview to the dashboard.
8. On window close: kills the bot process gracefully.
9. If the bot crashes on its own: navigates back to the picker.

Run standalone:
    .venv\\Scripts\\python.exe launch_gui.py

PyInstaller note: gui/profile_picker.html must be collected into the bundle.
Add to your .spec (or pass via --add-data):
    datas=[("gui/profile_picker.html", "gui")],
"""
from __future__ import annotations

import functools
import http.server
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from bot.config.settings import BotSettings
import webbrowser
import atexit
from loguru import logger

# Global controller reference for signal/atexit handlers
_global_controller: "GUIController | None" = None

def _handle_exit(signum, frame):
    """Signal handler that gracefully shuts down the bot before exiting."""
    if _global_controller is not None:
        _global_controller.shutdown()
    # Restore default handling and re-raise the signal
    signal.signal(signum, signal.SIG_DFL)
    os.kill(os.getpid(), signum)

# Register handlers for common termination signals
for _sig in (signal.SIGINT, signal.SIGTERM):
    signal.signal(_sig, _handle_exit)

# Register atexit shutdown
atexit.register(lambda: _global_controller.shutdown() if _global_controller else None)
from pathlib import Path

try:
    import winreg  # only available on Windows
except ImportError:  # pragma: no cover
    winreg = None  # type: ignore[assignment]

# ===== Locate project root =====================================================
def _project_root() -> Path:
    """Directory that holds main.py + .venv/ + .env.

    - ``python launch_gui.py`` -> ``__file__``'s directory.
    - frozen one-file Genesis.exe -> ``sys.executable``'s directory.
    - frozen one-dir Genesis.exe -> searched upward from the exe directory.
    """
    if getattr(sys, "frozen", False):
        candidate = Path(sys.executable).resolve().parent
        # Try candidate first, then search up to 3 levels of parents.
        # Common layouts:
        #   Genesis.exe  (project root)
        #   dist/Genesis/Genesis.exe  (PyInstaller one-dir)
        #   app/Genesis.exe           (packaged with other files)
        for path in [candidate, candidate.parent, candidate.parent.parent, candidate.parent.parent.parent]:
            if (path / "main.py").exists():
                return path
        # Fallback: return candidate and let downstream code fail with a clear message
        return candidate
    return Path(__file__).resolve().parent


PROJECT_ROOT: Path = _project_root()


# ===== Simple timestamped log helper ==========================================
def _launcher_log(msg: str) -> None:
    """Append a timestamped line to logs/launcher.log for diagnostics."""
    try:
        _lp = PROJECT_ROOT / "logs" / "launcher.log"
        _lp.parent.mkdir(parents=True, exist_ok=True)
        import datetime
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with _lp.open("a", encoding="utf-8", errors="replace") as _f:
            _f.write(f"{ts} | {msg}\n")
    except Exception:
        pass


# ===== Profile -> port / label mapping =========================================
PROFILE_CONFIG: dict[str, dict] = {
    "default": {
        "port": 8000,
        "label": "Swing Trader",
        "category": "Default",
        "description": (
            "H4/M15 Institutional Swing Matrix. Deep regime hysteresis, strict currency exposure caps, and wide ATR bands for catching multi-day trends."
        ),
        "timeframes": "H4 / M15",
        "max_positions": 3,
        "max_risk": "1.0%",
        "rr_ratio": "2.0",
        "news_filter": True,
        "session_aware": True,
        "currency_exposure_cap": 3,
    },
    "scalper": {
        "port": 8001,
        "label": "Fast Scalper",
        "category": "Scalper",
        "description": (
            "M15/M1 High-Frequency Volatility Engine. Micro-regime tracking, volume-surge entry gates, tight equity-floor kill switch, and strict exposure limits."
        ),
        "timeframes": "M15 / M1",
        "max_positions": 5,
        "max_risk": "0.5%",
        "rr_ratio": "1.0",
        "news_filter": True,
        "session_aware": True,
        "currency_exposure_cap": 1,
    },
    "breakout": {
        "port": 8002,
        "label": "Breakout Hunter",
        "category": "Breakout",
        "description": (
            "H1/M15 Kinetic Breakout System. Volatility-spike detection, volume surge gating, and dynamic ATR scaling to capture explosive momentum phases."
        ),
        "timeframes": "H1 / M15",
        "max_positions": 2,
        "max_risk": "1.5%",
        "rr_ratio": "1.5",
        "news_filter": True,
        "session_aware": True,
        "currency_exposure_cap": 2,
    },
    "daytrader": {
        "port": 8003,
        "label": "Day Trader",
        "category": "Advanced",
        "description": (
            "3×14 matrix: Trend Engine + Mean Reversion + Breakout across 14 pairs. "
            "Session-aware, currency exposure capped, daily loss circuit breaker."
        ),
        "timeframes": "H1 / M15",
        "max_positions": 3,
        "max_risk": "1.0%",
        "rr_ratio": "2.0",
        "news_filter": True,
        "session_aware": True,
        "currency_exposure_cap": 2,
    },
}

# ===== Tiny .env reader (avoids importing bot.config) =========================
def _read_env_file() -> dict[str, str]:
    """Read key/value pairs from .env (or return empty dict)."""
    p = PROJECT_ROOT / ".env"
    if not p.exists():
        return {}
    out: dict[str, str] = {}
    for raw in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _read_profile_env(profile: str) -> dict[str, str]:
    """Return env vars from .env.<profile>, merging with .env as fallback.

    Falls back to ``.env`` alone when the profile-specific file is missing.
    """
    profile_env: dict[str, str] = {}
    prof_path = PROJECT_ROOT / f".env.{profile}"
    if prof_path.exists():
        for raw in prof_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            profile_env[k.strip()] = v.strip().strip('"').strip("'")
    # .env provides base; profile file overrides
    base = _read_env_file()
    base.update(profile_env)
    return base


def _dashboard_host_port(env: dict[str, str]) -> tuple[str, int]:
    host = env.get("DASHBOARD_HOST", "127.0.0.1").strip() or "127.0.0.1"
    try:
        port = int(env.get("DASHBOARD_PORT", "8000"))
    except ValueError:
        port = 8000
    return host, port


def _connect_host(bind_host: str) -> str:
    """Return the host to connect to, given a bind host.

    0.0.0.0 is a wildcard bind address; browsers/webviews must connect
    to a concrete address.  127.0.0.1 works for localhost-only binds.
    """
    if bind_host in ("0.0.0.0", "", "*"):
        return "127.0.0.1"
    return bind_host


def _port_available(host: str, port: int) -> bool:
    """Return True if *nothing* is listening on host:port."""
    try:
        with socket.create_connection((host, port), timeout=0.4):
            return False  # someone is listening
    except OSError:
        return True  # connection refused -> port is free


# ===== Port-readiness polling ==================================================
def _port_listening(port: int, host: str = "127.0.0.1", timeout: float = 0.6) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _wait_for_port(port: int, deadline_sec: float = 60.0) -> bool:
    """Block until TCP connect to 127.0.0.1:port succeeds or the deadline elapses."""
    end = time.monotonic() + deadline_sec
    while time.monotonic() < end:
        if _port_listening(port):
            return True
        time.sleep(0.3)
    return False


# ===== Local HTTP server for GUI assets (bridge injection fix) ================
def _find_free_port() -> int:
    """Bind to port 0 to let the OS give us a free port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_html_server(directory: Path) -> tuple[int, http.server.HTTPServer]:
    """Serve *directory* over HTTP on a random localhost port.

    Returns ``(port, server)``.  The server runs on a daemon thread and stops
    automatically when the process exits.

    Why not file://?
    ----------------
    pywebview's Edge/WinForms backend injects ``window.pywebview`` via a
    CoreWebView2 script that runs before page JS.  WebView2 applies the same
    cross-origin restrictions to ``file://`` origins that browsers do, which
    can silently block the injection.  Loading over ``http://127.0.0.1``
    bypasses those restrictions and the bridge always appears.
    """
    port = _find_free_port()
    # SimpleHTTPRequestHandler needs 'directory' kwarg (Python 3.7+)
    handler_cls = functools.partial(
        http.server.SimpleHTTPRequestHandler,
        directory=str(directory),
    )
    # Suppress the noisy request log lines
    handler_cls.log_message = lambda *_: None  # type: ignore[attr-defined]
    server = http.server.HTTPServer(("127.0.0.1", port), handler_cls)
    t = threading.Thread(target=server.serve_forever, daemon=True, name="gui-http")
    t.start()
    return port, server


# ===== Spawn / clean up the bot subprocess =====================================
def _venv_python() -> Path:
    """Resolve the venv interpreter, raise if it does not exist."""
    if sys.platform != "win32":
        cand = PROJECT_ROOT / ".venv" / "bin" / "python"
    else:
        cand = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
    if not cand.exists():
        raise FileNotFoundError(
            f"Could not find venv Python at {cand}. "
            "Re-create the venv or move Genesis.exe back to the project root."
        )
    return cand


def _spawn_bot(stderr_log: Path, profile: str | None = None, picker_url: str | None = None):
    """Spawn ``main.py`` under the venv Python in a new process group.

    Parameters
    ----------
    stderr_log:
        File path for the child's stderr (appended, unbuffered).
    profile:
        Optional profile id. When given, ``GENESIS_PROFILE`` is injected
        into the child environment.
    picker_url:
        Optional HTTP URL of the profile-picker page. When given,
        ``GENESIS_PICKER_URL`` is injected so the dashboard can navigate
        the user back to the picker.
    """
    if sys.platform == "win32":
        # CREATE_NEW_PROCESS_GROUP lets us send CTRL_BREAK_EVENT later.
        # CREATE_NO_WINDOW prevents Windows from allocating a fresh
        # console window for python.exe when the parent (windowed
        # Genesis.exe) has none — otherwise a stray terminal pops up.
        creationflags = (
            subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.CREATE_NO_WINDOW
        )
    else:
        creationflags = 0
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("GENESIS_LAUNCHED_BY", "gui")
    if profile:
        env["GENESIS_PROFILE"] = profile
    if picker_url:
        env["GENESIS_PICKER_URL"] = picker_url
    stderr_log.parent.mkdir(parents=True, exist_ok=True)
    err_fh = stderr_log.open("ab", buffering=0)
    # When running as a windowed exe (console=False), stdout is
    # invalid / None — the child's check_setup.py would crash writing
    # colored output to it.  Redirect both stdout and stderr to the
    # same log file so the bot always has a valid stdout.
    out_fh = stderr_log.open("ab", buffering=0)
    proc = subprocess.Popen(
        [str(_venv_python()), "main.py"],
        cwd=str(PROJECT_ROOT),
        env=env,
        stdout=out_fh,
        stderr=err_fh,
        stdin=subprocess.DEVNULL,
        creationflags=creationflags,
        close_fds=True,
    )
    return proc, err_fh, out_fh


def _graceful_kill(proc: subprocess.Popen, grace_sec: float = 8.0) -> None:
    """Win-friendly shutdown sequence: Ctrl+Break -> terminate -> kill.

    Swallows ALL exceptions — including the spurious C-level
    "<built-in function kill> returned a result with an exception set"
    message that CPython emits during fast/windowed subprocess exit
    when pyinstaller + pywebview + Windows signal-handling race.
    Any error raised here would otherwise bubble back to the JavaScript
    side as a "bridge error" and break the picker UX.
    """
    try:
        if proc.poll() is not None:
            return
        try:
            if sys.platform == "win32":
                proc.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                proc.send_signal(signal.SIGINT)
            try:
                proc.wait(timeout=grace_sec)
                return
            except subprocess.TimeoutExpired:
                pass
        except (OSError, ValueError):
            pass
        try:
            proc.terminate()
            try:
                proc.wait(timeout=3.0)
                return
            except subprocess.TimeoutExpired:
                pass
        except OSError:
            pass
        try:
            proc.kill()
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                pass
        except BaseException:
            # On Windows, proc.kill() can raise a spurious C-level error
            # "<built-in function kill> returned a result with an
            # exception set" when the process has already exited — and
            # the underlying Python C-API exception is fully consumed
            # here.  Swallow *all* of BaseException so this can never
            # surface as a bridge error.
            pass
    except BaseException:
        # Last-ditch: nothing raised from a kill sequence may ever
        # leak out as a bridge error.
        pass


# ===== WebView2 runtime check (Windows) ========================================
# GUIDs for WebView2 Runtime and Microsoft Edge (which embeds WebView2).
# The WebView2 GUID has changed across releases — rather than hard-coding one
# value, we enumerate all EdgeUpdate client keys and match by name.
def _check_webview2_runtime() -> bool:
    """Return True if the Edge WebView2 runtime is installed (Windows only).

    Checks the EdgeUpdate registry keys for any client whose ``name`` value
    contains "WebView2" or "Microsoft Edge" (Edge ships WebView2 embedded).
    Returns True on non-Windows platforms (no-op).
    """
    if sys.platform != "win32" or winreg is None:
        return True  # non-Windows: nothing to check

    # EdgeUpdate can live under WOW6432Node (32-bit-on-64-bit) or native.
    search_paths = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\EdgeUpdate\Clients"),
    ]
    for hive, subkey in search_paths:
        try:
            with winreg.OpenKey(hive, subkey) as clients_key:
                i = 0
                while True:
                    try:
                        guid = winreg.EnumKey(clients_key, i)
                        i += 1
                        try:
                            with winreg.OpenKey(clients_key, guid) as client_key:
                                name, _ = winreg.QueryValueEx(client_key, "name")
                                if isinstance(name, str) and (
                                    "WebView2" in name or name == "Microsoft Edge"
                                ):
                                    return True
                        except OSError:
                            continue  # key unreadable — skip
                    except OSError:
                        break  # no more sub-keys
        except OSError:
            continue  # parent key doesn't exist — try next path
    return False


# ===== Preflight (run ourselves so failures are visible) ======================
def _run_preflight() -> tuple[int, str]:
    """Run scripts/check_setup.py and capture output.

    Returns ``(rc, output)`` where rc == 0 means safe to launch.
    """
    script = PROJECT_ROOT / "scripts" / "check_setup.py"
    try:
        cp = subprocess.run(
            [str(_venv_python()), str(script)],
            cwd=str(PROJECT_ROOT),
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
    except FileNotFoundError as exc:
        return (2, f"Could not run preflight: {exc}")
    out = (cp.stdout or "") + (cp.stderr or "")
    return (cp.returncode, out)


# ===== Auto-copy .env.example -> .env ==========================================
def _ensure_env_file() -> None:
    """Auto-copy .env.example -> .env if .env is absent.

    If the .env file is missing but .env.example exists (either in the
    project root or in the frozen bundle's _MEIPASS), copy it to the
    project root so check_setup.py and the bot can find it.

    This mirrors ``python scripts/check_setup.py --fix`` but runs
    automatically on launcher startup.
    """
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        return  # .env already present — nothing to do

    # Try project-root .env.example first, then frozen-bundle copy
    example_path = PROJECT_ROOT / ".env.example"
    if not example_path.exists() and getattr(sys, "frozen", False):
        meipass_example = Path(sys._MEIPASS) / ".env.example"
        if meipass_example.exists():
            example_path = meipass_example

    if example_path.exists():
        import shutil
        shutil.copy2(str(example_path), str(env_path))
        print(f"[launcher] auto-copied {example_path} -> {env_path}")
        print("[launcher] Edit .env with your MT5 credentials before launching.")
    else:
        print("[launcher] WARNING: .env not found and .env.example is missing.")


# ===== Picker HTML resolution (dev + frozen) ===================================
def _picker_html_path() -> Path:
    """Return profile_picker.html for dev and frozen modes."""
    if getattr(sys, "frozen", False):
        meipass = Path(sys._MEIPASS)
        candidate = meipass / "gui" / "profile_picker.html"
        if candidate.exists():
            return candidate
    return PROJECT_ROOT / "gui" / "profile_picker.html"


# ===== GUI controller (exposed as pywebview *js_api*) ==========================
class GUIController:
    """Methods are injected into ``window.pywebview.api`` by pywebview.

    pywebview 6.x exposes methods using their **original Python names**
    (snake_case). The JavaScript side calls them directly:

    - ``select_profile``  -> ``window.pywebview.api.select_profile``
    - ``get_profiles``     -> ``window.pywebview.api.get_profiles``
    - ``get_profile_details`` -> ``window.pywebview.api.get_profile_details``
    - ``run_preflight_check`` -> ``window.pywebview.api.run_preflight_check``
    """

    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self.err_fh = None
        self.out_fh = None
        self._window = None
        self._picker_http_url: str | None = None
        self._monitor_stop = threading.Event()
        self._monitor_thread: threading.Thread | None = None

    # -- pywebview wiring ----------------------------------------------------
    def bind_window(self, window, picker_http_url: str) -> None:
        """Call once after ``create_window`` so API methods can navigate."""
        self._window = window
        self._picker_http_url = picker_http_url

    # -- pywebview API (called from JavaScript) -------------------------------
    def select_profile(self, name: str) -> dict:
        """Called when the user clicks a profile card in the picker.

        Returns ``{"ok": True, "status": "launching"}`` immediately — the
        actual spawn / port-wait / navigation happens in a background thread
        so the bridge call returns fast and doesn't block the JS promise.

        If the launch fails, the error is sent to JS via ``evaluate_js``
        and logged to ``logs/launcher.log``.
        """
        _launcher_log(f"[bridge] select_profile({name!r}) called — thread={threading.current_thread().name}")

        name = (name or "").strip().lower()
        if name not in PROFILE_CONFIG:
            return {"ok": False, "error": f"Unknown profile: {name}"}

        # Quick synchronous checks before spawning the thread
        port = PROFILE_CONFIG[name]["port"]
        env_vars = _read_profile_env(name)
        bind_host, _ = _dashboard_host_port(env_vars)
        # For connection checks and the webview, 0.0.0.0 → 127.0.0.1
        conn_host = _connect_host(bind_host)
        if not _port_available(conn_host, port):
            _launcher_log(f"[bridge] PORT BUSY: {conn_host}:{port}")
            return {
                "ok": False,
                "error": (
                    f"Port {port} is already in use on {conn_host}. "
                    "Stop the conflicting process or choose another profile."
                ),
            }

        # Fire-and-forget: the heavy work runs in a thread so the bridge
        # returns immediately and the JS overlay keeps spinning.
        threading.Thread(
            target=self._launch_in_thread,
            args=(name, conn_host, bind_host, port),
            daemon=True,
            name="bot-launcher",
        ).start()

        return {"ok": True, "status": "launching"}

    def _launch_in_thread(self, name: str, conn_host: str, bind_host: str, port: int) -> None:
        """Background worker that spawns the bot, waits for the port,
        navigates the webview, and reports errors back to JS.

        *conn_host* is the host the webview/browser should connect to
        (e.g. 127.0.0.1); *bind_host* is what the server binds to.
        """
        _log = lambda msg: _launcher_log(f"[launch] {msg}")
        dash_url = f"http://{conn_host}:{port}"
        _log(f"profile={name} conn_host={conn_host} bind_host={bind_host} port={port} dash_url={dash_url}")

        # Verify we found a valid project root before going further
        if not (PROJECT_ROOT / "main.py").exists():
            _log(f"CRITICAL: main.py not found in {PROJECT_ROOT}")
            self._report_error(
                f"Could not find main.py in {PROJECT_ROOT}. "
                "Make sure Genesis.exe is placed in the project root."
            )
            return

        # Tear down the previous bot (if any)
        self._kill_bot()

        # Re-run preflight on every launch
        _log("running preflight...")
        rc, output = _run_preflight()
        if rc != 0:
            _log(f"preflight FAILED rc={rc}")
            self._report_error(f"Preflight check failed (rc={rc}). Details: {output[-500:]}")
            return
        _log("preflight OK")

        # Spawn with GENESIS_PROFILE in the child env
        stderr_log = PROJECT_ROOT / "logs" / "launcher.log"
        try:
            self.proc, self.err_fh, self.out_fh = _spawn_bot(stderr_log, profile=name, picker_url=self._picker_http_url)
            _log(f"bot spawned pid={self.proc.pid}")
        except (FileNotFoundError, OSError) as exc:
            self.proc = None
            _log(f"spawn FAILED: {exc}")
            self._report_error(str(exc))
            return

        timeout_secs = BotSettings().dashboard_startup_timeout_secs
        _log(f"waiting for port {conn_host}:{port} ({timeout_secs}s deadline)...")
        if not _wait_for_port(port, deadline_sec=timeout_secs):
            _log("PORT WAIT TIMED OUT")
            self._kill_bot()
            self._report_error(f"Backend did not bind to :{port} within {timeout_secs} s")
            return
        _log(f"port {port} is listening")

        # Open the dashboard in the system browser (Edge).
        # This is the most reliable way to show the dashboard — the legacy
        # tkinter launcher did the same via webbrowser.open().  The
        # pywebview in-window navigation (load_url from a background thread)
        # is unreliable on the WinForms/WebView2 backend and can silently
        # fail, leaving the user on the profile picker.
        _log(f"opening {dash_url} in system browser (webbrowser.open)")
        try:
            webbrowser.open(dash_url)
            _log("webbrowser.open() called successfully")
        except Exception as _wb_exc:
            _log(f"webbrowser.open FAILED: {_wb_exc!r}")

        # Also attempt in-window navigation as a secondary channel — if it
        # works, the pywebview window will also show the dashboard.
        _log(f"navigating webview to {dash_url} (window={self._window is not None})")
        if self._window is not None:
            try:
                self._window.load_url(dash_url)
                _log("load_url() called successfully")
            except Exception as _nav_exc:
                _log(f"load_url FAILED: {_nav_exc!r}")
                try:
                    _lp = PROJECT_ROOT / "logs" / "launcher.log"
                    _lp.parent.mkdir(parents=True, exist_ok=True)
                    with _lp.open("a", encoding="utf-8", errors="replace") as _f:
                        import traceback as _tb
                        _f.write(f"\n[launcher] load_url({dash_url!r}) failed:\n")
                        _tb.print_exc(file=_f)
                except Exception:
                    pass
        else:
            _log("no window — skipping in-window navigation")
        self._start_monitor()
        _log("launch complete")

    def _report_error(self, message: str) -> None:
        """Push an error back to the JS frontend via evaluate_js."""
        _launcher_log(f"[launch-error] {message}")
        if self._window is None:
            return
        # Escape for safe JS string embedding
        safe = message.replace("\\", "\\\\").replace("'", "\\'").replace("\n", " ")
        try:
            self._window.evaluate_js(
                f"document.getElementById('overlay').classList.remove('active');"
                f"showError('{safe}');"
            )
        except Exception:
            pass

    def get_profiles(self) -> list[dict]:
        """Return the available profiles with ports and labels."""
        result: list[dict] = []
        for key in PROFILE_CONFIG:
            cfg = PROFILE_CONFIG[key]
            result.append(
                {"id": key, "label": cfg["label"], "port": cfg["port"]}
            )
        return result

    def get_profile_details(self, name: str) -> dict:
        """Return full config details for a profile from its .env file.

        Reads ``.env.{name}`` first, falls back to ``.env`` for keys not
        present. Returns static defaults from PROFILE_CONFIG for display
        fields when the .env key is absent.
        """
        name = (name or "").strip().lower()
        if name not in PROFILE_CONFIG:
            return {"ok": False, "error": f"Unknown profile: {name}"}

        cfg = PROFILE_CONFIG[name]
        env = _read_profile_env(name)

        def _val(env_key: str, fallback: str) -> str:
            return env.get(env_key, fallback)

        details = {
            "ok": True,
            "id": name,
            "label": cfg["label"],
            "category": cfg["category"],
            "description": cfg["description"],
            "timeframes": env.get("HIGHER_TIMEFRAME", "H4") + " / " + env.get("ENTRY_TIMEFRAME", "M15"),
            "max_risk": str(float(env.get("MAX_RISK_PER_TRADE", "0.01")) * 100) + "%",
            "max_positions": int(env.get("MAX_OPEN_POSITIONS", "3")),
            "rr_ratio": env.get("MIN_REWARD_RATIO", "2.0"),
            "news_filter": env.get("NEWS_FILTER_ENABLED", "true").lower() == "true",
            "paper_trading": env.get("PAPER_TRADING", "true").lower() == "true",
            "pair_count": len(
                env.get("TRADING_PAIRS", "[]").strip('"[]\n').replace('"', "").replace("'", "").split(",")
            )
            if env.get("TRADING_PAIRS", "").strip()
            else 0,
        }
        return details

    def run_preflight_check(self, name: str) -> dict:
        """Run preflight for a specific profile and return results.

        Returns ``{"ok": True, "passed": True}`` or
        ``{"ok": True, "passed": False, "errors": [...]}``.
        """
        name = (name or "").strip().lower()
        if name not in PROFILE_CONFIG:
            return {"ok": False, "error": f"Unknown profile: {name}"}

        # Ensure the profile .env file exists
        if name != "default" and not (PROJECT_ROOT / f".env.{name}").exists():
            # fallback gracefully -> use .env
            pass

        env = _read_profile_env(name)
        port = PROFILE_CONFIG[name]["port"]
        host, _ = _dashboard_host_port(env)

        checks: list[dict] = []

        # 1. venv python
        try:
            _venv_python()
            checks.append({"name": "Venv Python", "pass": True})
        except FileNotFoundError as exc:
            checks.append({"name": "Venv Python", "pass": False, "detail": str(exc)})

        # 2. profile .env
        if name == "default":
            env_file = PROJECT_ROOT / ".env"
        else:
            env_file = PROJECT_ROOT / f".env.{name}"
        if env_file.exists():
            checks.append({"name": "Profile .env", "pass": True})
        else:
            checks.append(
                {
                    "name": "Profile .env",
                    "pass": False,
                    "detail": f"{env_file} not found; will fall back to .env",
                }
            )

        # 3. port availability
        if _port_available(host, port):
            checks.append(
                {"name": f"Port {port}", "pass": True, "detail": f"{host}:{port} available"}
            )
        else:
            checks.append(
                {
                    "name": f"Port {port}",
                    "pass": False,
                    "detail": f"{host}:{port} already in use",
                }
            )

        # 4. DASHBOARD_PORT consistency
        dash_port_in_env = int(env.get("DASHBOARD_PORT", str(port)))
        if dash_port_in_env == port:
            checks.append(
                {
                    "name": "Port Config",
                    "pass": True,
                    "detail": f"DASHBOARD_PORT={dash_port_in_env} matches profile port",
                }
            )
        else:
            checks.append(
                {
                    "name": "Port Config",
                    "pass": False,
                    "detail": (
                        f"DASHBOARD_PORT={dash_port_in_env} in .env "
                        f"!== profile port {port}"
                    ),
                }
            )

        passed = all(c["pass"] for c in checks)
        return {"ok": True, "passed": passed, "checks": checks}

    # -- internal helpers ----------------------------------------------------
    def _kill_bot(self) -> None:
        """Kill the running bot subprocess (if any)."""
        if self.proc is not None and self.proc.poll() is None:
            _graceful_kill(self.proc)
        self.proc = None
        for fh_attr in ("err_fh", "out_fh"):
            fh = getattr(self, fh_attr, None)
            if fh is not None:
                try:
                    fh.close()
                except OSError:
                    pass
                setattr(self, fh_attr, None)

    def _start_monitor(self) -> None:
        """Spawn a daemon thread that watches the bot process.

        If the bot exits unexpectedly the picker is re-loaded so the user
        can re-launch without restarting the launcher itself.

        Any previous monitor thread is signalled to stop first to avoid
        stacking redundant watchers.
        """
        # Stop any previous monitor thread before starting a new one.
        self._monitor_stop.set()
        if self._monitor_thread is not None and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=2.0)
        self._monitor_stop.clear()

        def _watch() -> None:
            while not self._monitor_stop.is_set():
                if self.proc is not None and self.proc.poll() is not None:
                    # Navigate back to the picker over HTTP (not file://)
                    # so the pywebview bridge is re-injected correctly.
                    url = self._picker_http_url or _picker_html_path().as_uri()
                    if self._window is not None:
                        try:
                            self._window.load_url(url)
                        except Exception as _nav_exc:
                            try:
                                _lp = PROJECT_ROOT / "logs" / "launcher.log"
                                _lp.parent.mkdir(parents=True, exist_ok=True)
                                with _lp.open("a", encoding="utf-8", errors="replace") as _f:
                                    _f.write(f"\n[monitor] load_url({url!r}) failed: {_nav_exc}\n")
                            except Exception:
                                pass
                    self.proc = None
                    break
                self._monitor_stop.wait(1.0)

        t = threading.Thread(target=_watch, daemon=True)
        t.start()
        self._monitor_thread = t

    def shutdown(self) -> None:
        """Call when the window closes."""
        self._monitor_stop.set()
        self._kill_bot()


# ===== Entry point =============================================================
def main() -> int:
    """Show profile picker, spawn bot on selection, navigate to dashboard.

    Exit codes
    ----------
    0  clean exit (window closed)
    2  venv Python not found (raised from _spawn_bot at preflight time)
    4  preflight check failed
    5  WebView2 runtime not installed
    """
    # --version flag (must run before any side effects) ----------------------
    if "--version" in sys.argv:
        __version__ = "1.0.0"
        ver_str = f"Genesis Launcher {__version__}"
        print(ver_str)
        # When built as console=False (windowed exe), stdout goes nowhere.
        # Allocate a console window so the user can see the version output.
        if getattr(sys, "frozen", False) and sys.platform == "win32":
            import ctypes
            ctypes.windll.kernel32.AllocConsole()
            print(ver_str)
            print("\n(Press Enter to close)")
            input()
        sys.exit(0)

    import webview  # lazy: keeps module-level import graph slim

    # -- Auto-copy .env.example -> .env (before preflight checks .env) -------
    _ensure_env_file()

    # -- Preflight -----------------------------------------------------------
    rc, output = _run_preflight()
    if rc != 0:
        print(f"[launcher] initial preflight failed (rc={rc}) - opening GUI anyway")
        print("=" * 60)
        safe_output = (output or "").rstrip() or "(no output from preflight)"
        enc = getattr(sys.stdout, "encoding", None) or "utf-8"
        print(safe_output.encode(enc, errors="replace").decode(enc, errors="replace"))
        print("=" * 60)
        # We no longer exit here. The GUI's profile picker will re-run
        # the preflight check for the specifically selected profile and
        # display the errors in the HTML UI natively.

    # -- WebView2 runtime check (Windows) ------------------------------------
    if not _check_webview2_runtime():
        print("[launcher] ERROR: Microsoft Edge WebView2 Runtime is not installed.")
        print("[launcher] The launcher requires WebView2 to display the GUI.")
        print("[launcher] Download it from: https://developer.microsoft.com/microsoft-edge/webview2/")
        time.sleep(8.0)
        return 5

    # -- Open profile picker -------------------------------------------------
    picker = _picker_html_path()
    if not picker.exists():
        print(f"[launcher] ERROR: picker HTML not found at {picker}")
        return 4

    # Serve the gui/ directory over a local HTTP server.
    # pywebview's Edge/WinForms backend reliably injects window.pywebview over
    # http:// but can silently fail for file:// and html= string modes.
    gui_dir = picker.parent
    srv_port, _srv = _start_html_server(gui_dir)
    picker_url = f"http://127.0.0.1:{srv_port}/profile_picker.html"

    print(f"[launcher] project_root  = {PROJECT_ROOT}")
    print(f"[launcher] picker        = {picker}")
    print(f"[launcher] picker_url    = {picker_url}")
    print(f"[launcher] picker size   = {picker.stat().st_size} bytes")

    # Pre‑launch cleanup: kill any stray bot process still listening on this profile's port.
    def _kill_existing_bot(port: int):
        """Force‑kill a stray bot process bound to *port* without closing positions."""
        try:
            # Windows netstat to locate PID listening on the port
            result = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, check=True)
            for line in result.stdout.splitlines():
                if f":{port}" in line and "LISTENING" in line:
                    parts = line.split()
                    pid = parts[-1]
                    if int(pid) != os.getpid():
                        logger.info(f"[launcher] Killing stray bot process PID {pid} on port {port}")
                        subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True)
        except Exception as exc:
            logger.warning(f"[launcher] Failed to cleanup stray bot on port {port}: {exc}")

    # Use the profile's configured port for cleanup
    profile_port = PROFILE_CONFIG.get(os.getenv("GENESIS_PROFILE", "default"), {}).get("port", 8000)
    _kill_existing_bot(profile_port)

    _global_controller = GUIController()
    controller = _global_controller

    window = webview.create_window(
        "Genesis Trading Bot",
        url=picker_url,
        width=1280,
        height=820,
        min_size=(960, 680),
        resizable=True,
        confirm_close=False,
        text_select=False,
        js_api=controller,
    )

    controller.bind_window(window, picker_http_url=picker_url)
    webview.start(func=None)

    # -- Window was closed ---------------------------------------------------
    print("[launcher] window closed")
    controller.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
