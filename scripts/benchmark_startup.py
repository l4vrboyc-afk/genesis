"""
Genesis Startup Time Benchmark
──────────────────────────────
Measures time-to-dashboard for each trading profile by spawning
``main.py`` as a subprocess and timing how long until the HTTP
dashboard port is reachable.

Usage::

    # All profiles (default)
    .venv\Scripts\python.exe scripts/benchmark_startup.py

    # Single profile
    .venv\Scripts\python.exe scripts/benchmark_startup.py --profile scalper

    # Custom timeout, skip health check
    .venv\Scripts\python.exe scripts/benchmark_startup.py --timeout 15 --no-health

    # Programmatic use
    from scripts.benchmark_startup import main
    results = main(profiles=["scalper", "breakout"], check_health=False)

Output example::

    profile        port    port-ready (s)   health-200 (s)   total (s)  spawn (ms)
    ───────        ────    ───────────────   ─────────────   ────────   ─────────
    default        8000            3.14            0.22       3.38       15.0
"""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ── Profile config ────────────────────────────────────────────────────────────
PROFILES = {
    "default":   {"port": 8000,  "env_file": ".env"},
    "scalper":   {"port": 8001,  "env_file": ".env.scalper"},
    "breakout":  {"port": 8002,  "env_file": ".env.breakout"},
    "daytrader": {"port": 8003,  "env_file": ".env.daytrader"},
}

TIMEOUT_SECS = 30      # max wait per profile
COOLDOWN_SECS = 2.0    # wait between profiles for OS port release
POLL_INTERVAL = 0.05   # port/health polling interval (seconds)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _port_listening(port: int, host: str = "127.0.0.1", timeout: float = 0.2) -> bool:
    """Return True if TCP connect succeeds."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _health_ok(port: int, host: str = "127.0.0.1", timeout: float = 1.0) -> bool:
    """Return True if ``/api/health`` returns HTTP 200."""
    import urllib.request
    import urllib.error
    try:
        resp = urllib.request.urlopen(
            f"http://{host}:{port}/api/health",
            timeout=timeout,
        )
        return resp.status == 200
    except (urllib.error.URLError, OSError):
        return False


def _wait_until(
    check_fn,
    port: int,
    deadline: float,
    poll_ms: float = 0.05,
) -> float:
    """Poll *check_fn* until it returns True or *deadline* elapses.

    Returns the elapsed seconds (monotonic) on success, or raises
    TimeoutError on failure.
    """
    start = time.monotonic()
    while time.monotonic() < deadline:
        if check_fn(port):
            return time.monotonic() - start
        time.sleep(poll_ms)
    raise TimeoutError(
        f"check did not succeed within {deadline - start:.1f} s"
    )


# ── Benchmark runner ──────────────────────────────────────────────────────────

def _kill_stale_on_port(port: int) -> None:
    """Kill any process listening on *port* before we start.

    Windows-only: uses ``netstat`` + ``taskkill``.  No-op on other
    platforms where the concept of stale port processes doesn't apply
    (benchmark runs isolated in CI containers).
    """
    if sys.platform != "win32":
        return  # no-op on macOS/Linux
    try:
        result = subprocess.run(
            ["netstat", "-ano"], capture_output=True, text=True, check=True
        )
        for line in result.stdout.splitlines():
            if f":{port}" in line and "LISTENING" in line:
                parts = line.split()
                pid = parts[-1]
                if pid.isdigit() and int(pid) != os.getpid():
                    subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True)
                    time.sleep(0.5)
    except Exception:
        pass


def _benchmark_profile(
    profile: str,
    cfg: dict,
    check_health: bool = True,
    timeout: float = TIMEOUT_SECS,
) -> dict:
    """Spawn ``main.py`` for *profile*, wait for port + health, kill, return times."""

    port = cfg["port"]

    # ── Pre-check: port must be free ────────────────────────────────────────────
    if _port_listening(port):
        _kill_stale_on_port(port)
        if _port_listening(port):
            raise RuntimeError(f"Port {port} still in use — cannot benchmark {profile}")

    env = os.environ.copy()
    env["GENESIS_PROFILE"] = profile
    env["GENESIS_LAUNCHED_BY"] = "gui"
    env["PYTHONIOENCODING"] = "utf-8"

    # Suppress logging noise — redirect stdout/stderr to devnull
    startup = time.monotonic()
    proc = subprocess.Popen(
        [sys.executable, "main.py"],
        cwd=str(PROJECT_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        creationflags=(
            subprocess.CREATE_NEW_PROCESS_GROUP
            if sys.platform == "win32"
            else 0
        ),
        close_fds=True,
    )
    spawn_ms = (time.monotonic() - startup) * 1000

    deadline = time.monotonic() + timeout

    # Phase 1: Port ready (TCP connect)
    port_elapsed = None
    try:
        port_elapsed = _wait_until(_port_listening, port, deadline, poll_ms=POLL_INTERVAL)
    except TimeoutError:
        pass

    # Phase 2: Health endpoint (HTTP 200) — skipped when ``check_health=False``
    health_elapsed = None
    if check_health and port_elapsed is not None:
        try:
            health_elapsed = _wait_until(_health_ok, port, deadline, poll_ms=POLL_INTERVAL)
        except TimeoutError:
            pass

    total = time.monotonic() - startup

    # ── Kill ────────────────────────────────────────────────────────────────────
    _kill(proc)

    return {
        "profile": profile,
        "port": port,
        "spawn_ms": round(spawn_ms, 1),
        "port_ready_secs": round(port_elapsed, 2) if port_elapsed is not None else None,
        "health_200_secs": round(health_elapsed, 2) if health_elapsed is not None else None,
        "total_secs": round(total, 2),
        "pid": proc.pid,
    }


def _kill(proc: subprocess.Popen) -> None:
    """Graceful then forceful shutdown."""
    if proc.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            proc.send_signal(subprocess.signal.CTRL_BREAK_EVENT)
        else:
            proc.send_signal(subprocess.signal.SIGINT)
        proc.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        pass
    if proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            pass
    if proc.poll() is None:
        try:
            proc.kill()
            proc.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            pass


# ── Report ────────────────────────────────────────────────────────────────────

def _print_results(results: list[dict]) -> None:
    header = (
        f"{'profile':<14} {'port':<6} {'port-ready (s)':<16} "
        f"{'health-200 (s)':<16} {'total (s)':<10} {'spawn (ms)':<10}"
    )
    sep = "─" * len(header)
    print(f"\n{' Genesis Startup Benchmark ':=^{len(header)}s}")
    print(header)
    print(sep)
    for r in results:
        port_str = f":{r['port']}"
        port_ready = f"{r['port_ready_secs']:.2f}" if r['port_ready_secs'] is not None else "TIMEOUT"
        health = f"{r['health_200_secs']:.2f}" if r['health_200_secs'] is not None else "TIMEOUT"
        print(
            f"{r['profile']:<14} {port_str:<6} {port_ready:<16} "
            f"{health:<16} {r['total_secs']:<10.2f} {r['spawn_ms']:<10.1f}"
        )
    print(sep)

    # Summary stats
    successful = [r for r in results if r["port_ready_secs"] is not None]
    if successful:
        avg_port = sum(r["port_ready_secs"] for r in successful) / len(successful)
        avg_health = sum(
            r["health_200_secs"] for r in successful if r["health_200_secs"] is not None
        )
        health_count = sum(1 for r in successful if r["health_200_secs"] is not None)
        avg_health /= health_count if health_count else 1
        print(
            f"\n{' Average (successful):':<22} port-ready={avg_port:.2f}s  "
            f"health-200={avg_health:.2f}s  "
            f"profiles={len(successful)}/{len(results)}"
        )

    # Check 5-second target
    under_5 = [r for r in successful if r["port_ready_secs"] is not None and r["port_ready_secs"] < 5.0]
    pf = ", ".join(r["profile"] for r in under_5)
    if pf:
        print(f"\n✅ Profiles under 5 s (port-ready): {pf}")
    else:
        print(f"\n❌ No profiles reached port within 5 s")

    # Target: 5s from process start to health endpoint
    under_5_health = [r for r in successful if r["health_200_secs"] is not None and r["health_200_secs"] < 5.0]
    if under_5_health:
        pf_h = ", ".join(r["profile"] for r in under_5_health)
        print(f"✅ Profiles under 5 s (health-200):   {pf_h}")
    else:
        print(f"❌ No profiles reached health endpoint within 5 s")


# ── CLI argument parsing ─────────────────────────────────────────────────────

_ARGS_HELP = """Benchmark Genesis startup time for one or more trading profiles.

Results are printed to stdout and returned as a list of dicts for
programmatic consumption.

Examples:
    python scripts/benchmark_startup.py
    python scripts/benchmark_startup.py --profile scalper
    python scripts/benchmark_startup.py --profile default --profile breakout
    python scripts/benchmark_startup.py --timeout 15 --no-health
""".strip()


def _parse_args(argv: list[str] | None = None) -> dict:
    """Parse CLI flags, return a kwargs dict for ``main()``."""
    import argparse
    p = argparse.ArgumentParser(
        prog="benchmark_startup",
        description=_ARGS_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--profile", "-p",
        action="append",
        choices=list(PROFILES.keys()),
        help="Profile(s) to benchmark (repeatable; default: all)",
    )
    p.add_argument(
        "--timeout", "-t",
        type=float,
        default=TIMEOUT_SECS,
        help=f"Per-profile timeout in seconds (default: {TIMEOUT_SECS})",
    )
    p.add_argument(
        "--cooldown", "-c",
        type=float,
        default=COOLDOWN_SECS,
        help=f"Cooldown between profiles in seconds (default: {COOLDOWN_SECS})",
    )
    p.add_argument(
        "--no-health",
        action="store_false",
        dest="check_health",
        help="Skip the HTTP /api/health check phase",
    )
    ns = p.parse_args(argv)
    return {
        "profiles": ns.profile or list(PROFILES.keys()),
        "timeout": ns.timeout,
        "cooldown": ns.cooldown,
        "check_health": ns.check_health,
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def main(
    profiles: list[str] | None = None,
    timeout: float | None = None,
    cooldown: float | None = None,
    check_health: bool = True,
    _print: bool = True,
) -> list[dict]:
    """Benchmark one or more trading profiles and return timing results.

    Parameters
    ----------
    profiles : list of str, optional
        Profile names to benchmark.  Defaults to all defined profiles.
    timeout : float, optional
        Per-profile timeout in seconds.  Default 30.
    cooldown : float, optional
        Seconds to wait between profiles for OS port release.  Default 2.
    check_health : bool
        Whether to probe the ``/api/health`` HTTP endpoint after the port
        is detected.  Default True.
    _print : bool
        Internal flag — set to False when calling from tests to suppress
        printed output.

    Returns
    -------
    list of dict
        Each dict contains ``profile``, ``port``, ``port_ready_secs``,
        ``health_200_secs``, ``total_secs``, ``spawn_ms``, ``pid``.
    """
    if profiles is None:
        profiles = list(PROFILES.keys())
    if timeout is None:
        timeout = TIMEOUT_SECS
    if cooldown is None:
        cooldown = COOLDOWN_SECS

    # Verify we are in the project root
    if not (PROJECT_ROOT / "main.py").exists():
        raise RuntimeError(f"main.py not found in {PROJECT_ROOT}")

    # Warn about missing profile .env files
    for name in profiles:
        cfg = PROFILES[name]
        if name != "default":
            p = PROJECT_ROOT / cfg["env_file"]
            if not p.exists():
                print(f"  ⚠  {name}: {p.name} not found (will fall back to .env)")

    if _print:
        print(f"\n  Benchmark timeout: {timeout}s per profile")
        print(f"  Python: {sys.executable}")
        print(f"  CWD:    {PROJECT_ROOT}")

    results = []
    for name in profiles:
        cfg = PROFILES[name]
        if _print:
            print(f"\n  ── Benchmarking {name} (port {cfg['port']}) ──")
        try:
            result = _benchmark_profile(
                name, cfg, check_health=check_health, timeout=timeout,
            )
            results.append(result)
            if _print:
                status = (
                    f"port={result['port_ready_secs']}s"
                    if result["port_ready_secs"] is not None
                    else "port TIMEOUT"
                )
                print(f"  ✓ {name}: {status}")
        except Exception as exc:
            if _print:
                print(f"  ✗ {name}: {exc}")
            results.append({
                "profile": name,
                "port": cfg["port"],
                "spawn_ms": 0,
                "port_ready_secs": None,
                "health_200_secs": None,
                "total_secs": 0,
                "pid": None,
            })

        # Cooldown: let OS release the port before the next profile
        if _print:
            print(f"     cooldown {cooldown}s...")
        time.sleep(cooldown)

    if _print:
        _print_results(results)

    return results


if __name__ == "__main__":
    kwargs = _parse_args()
    results = main(**kwargs)
    failed = any(r.get("port_ready_secs") is None for r in results)
    sys.exit(1 if failed else 0)
