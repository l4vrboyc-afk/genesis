"""
Benchmark Startup Time — Measures profile startup performance.
Runs each profile (default, scalper, breakout, daytrader) sequentially,
recording port-bind latency, HTTP health-check latency, and MT5
connection latency.

Usage:
    python scripts/benchmark_profiles.py
"""

import os
import sys
import time
import socket
import subprocess
import urllib.request
import json
from pathlib import Path

PROFILES = [
    ("default",   ".env",         8000),
    ("scalper",   ".env.scalper", 8001),
    ("breakout",  ".env.breakout",8002),
    ("daytrader", ".env.daytrader",8003),
]

TIMEOUT_SECS = 45       # max wait per profile
COOLDOWN_SECS = 10.0    # wait between profiles for OS port + MT5 terminal release
POLL_INTERVAL = 0.05    # port/health polling interval (seconds)


def port_listening(host: str, port: int) -> bool:
    s = socket.socket()
    s.settimeout(0.5)
    try:
        rc = s.connect_ex((host, port))
        return rc == 0
    finally:
        s.close()


def http_ok(url: str, timeout: int = 3) -> tuple[bool, float, dict | None]:
    start = time.monotonic()
    try:
        r = urllib.request.urlopen(url, timeout=timeout)
        data = json.loads(r.read())
        return True, time.monotonic() - start, data
    except Exception:
        return False, time.monotonic() - start, None


def kill_stale_on_port(port: int) -> None:
    """Kill any process listening on *port* (Windows)."""
    try:
        result = subprocess.run(
            ["netstat", "-ano"], capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.splitlines():
            if f":{port}" in line and "LISTENING" in line:
                pid = line.split()[-1]
                if pid.isdigit() and int(pid) != os.getpid():
                    subprocess.run(
                        ["taskkill", "/PID", pid, "/F"],
                        capture_output=True, timeout=5,
                    )
                    time.sleep(1.0)
    except Exception:
        pass


def benchmark_profile(
    name: str, env_file: str, port: int
) -> dict:
    """Spawn main.py for *profile*, wait for port + health, kill, return timings."""
    root = Path.cwd()
    profile_env = os.environ.copy()
    profile_env["GENESIS_PROFILE"] = name
    profile_env["GENESIS_LAUNCHED_BY"] = "gui"

    # Ensure no stale listener
    kill_stale_on_port(port)

    print(f"\n  Launching {name} (port {port})...", end="", flush=True)
    t0 = time.monotonic()

    proc = subprocess.Popen(
        [sys.executable, "main.py"],
        cwd=str(root),
        env=profile_env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # ── Phase 1: Wait for port to bind ──────────────────────────────────
    port_ok = False
    t_port = None
    deadline = time.monotonic() + TIMEOUT_SECS
    while time.monotonic() < deadline:
        if port_listening("127.0.0.1", port):
            t_port = time.monotonic()
            port_ok = True
            break
        time.sleep(POLL_INTERVAL)

    if not port_ok:
        proc.kill()
        proc.wait(timeout=10)
        return {"profile": name, "error": "port never bound", "port_bind_s": None, "health_s": None, "mt5_s": None}

    elapsed_port = t_port - t0
    print(f" port={elapsed_port:.2f}s", end="", flush=True)

    # ── Phase 2: Wait for /api/health (HTTP server up) ────────────────
    health_ok = False
    t_health = None
    extra = 0.0
    while time.monotonic() < deadline:
        ok, dt, _ = http_ok(f"http://127.0.0.1:{port}/api/health", timeout=2)
        if ok:
            t_health = time.monotonic()
            health_ok = True
            extra = dt
            break
        time.sleep(POLL_INTERVAL)

    if not health_ok:
        proc.kill()
        proc.wait(timeout=10)
        return {"profile": name, "error": "health never responded", "port_bind_s": f"{elapsed_port:.2f}", "health_s": None, "mt5_s": None}

    elapsed_health = t_health - t0
    print(f" health={elapsed_health:.2f}s", end="", flush=True)

    # ── Phase 3: Wait for /api/status → mt5_connected=true ─────────────
    mt5_ok = False
    t_mt5 = None
    while time.monotonic() < deadline:
        ok, dt, data = http_ok(f"http://127.0.0.1:{port}/api/status", timeout=3)
        if ok and data and data.get("mt5_connected"):
            t_mt5 = time.monotonic()
            mt5_ok = True
            break
        time.sleep(0.25)  # slower poll for MT5

    if mt5_ok:
        elapsed_mt5 = t_mt5 - t0
        print(f" mt5={elapsed_mt5:.2f}s ✅", end="", flush=True)
    else:
        elapsed_mt5 = None
        print(f" mt5=TIMEOUT ❌", end="", flush=True)

    # ── Cleanup ──────────────────────────────────────────────────────────
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)

    # Give OS a moment to release the port
    time.sleep(COOLDOWN_SECS)

    return {
        "profile": name,
        "port": port,
        "port_bind_s": f"{elapsed_port:.2f}",
        "health_s": f"{elapsed_health:.2f}",
        "mt5_s": f"{elapsed_mt5:.2f}" if mt5_ok else "TIMEOUT",
        "mt5_ok": mt5_ok,
        "error": None if mt5_ok else "mt5 not connected within timeout",
    }


def main():
    print("=" * 62)
    print("  Genesis Profile Startup Benchmark")
    print("=" * 62)

    results = []
    for name, env_file, port in PROFILES:
        r = benchmark_profile(name, env_file, port)
        results.append(r)

    # ── Summary Table ──────────────────────────────────────────────────
    print("\n" + "=" * 62)
    print("  SUMMARY")
    print("=" * 62)
    print(f"  {'Profile':<14} {'Port':<6} {'Port Bind':<12} {'Health':<12} {'MT5 Ready':<12}")
    print(f"  {'─'*14} {'─'*6} {'─'*12} {'─'*12} {'─'*12}")
    for r in results:
        if r.get("error"):
            print(f"  {r['profile']:<14} {r.get('port','—'):<6} {'FAILED':<12} {'—':<12} {'—':<12}")
            print(f"  {'':>14} {'':>6} Error: {r['error']}")
        else:
            print(f"  {r['profile']:<14} {r['port']:<6} {r['port_bind_s']:<12} {r['health_s']:<12} {r['mt5_s']:<12}")

    connected = sum(1 for r in results if r.get("mt5_ok"))
    total = len(results)
    print(f"\n  MT5 connected: {connected}/{total}")
    print("=" * 62)


if __name__ == "__main__":
    main()
