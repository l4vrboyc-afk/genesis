"""
Genesis — one-shot bundle smoke test.

Verifies a freshly rebuilt launcher bundle end-to-end without needing to
click through the GUI:

    Step 1 — ``--version``   every deployed Genesis.exe reports the expected
                             launcher version string.
    Step 2 — hashes          every deployed Genesis.exe is byte-identical
                             (sha256) and every bundled picker copy matches
                             ``gui/profile_picker.html``.
    Step 3 — render          the bundled picker is served over a local HTTP
                             server and validated: HTTP 200, expected content
                             markers (title, 4 profile cards, inline favicon).
                             If Chrome is available, the page is additionally
                             rendered headless (``--dump-dom``) so a JS failure
                             or missing asset surfaces here instead of on a
                             user's desktop.

Usage
-----
    python scripts/smoke_test_bundle.py                # full smoke test
    python scripts/smoke_test_bundle.py --no-render    # skip step 3
    python scripts/smoke_test_bundle.py --version 2.1.0  # expected launcher version

Exit codes
----------
    0   all checks passed
    1   a check failed
    2   misuse (missing dist build output / unknown argument)
"""
from __future__ import annotations

import argparse
import functools
import hashlib
import http.server
import os
import shutil
import socket
import subprocess
import sys
import threading
from pathlib import Path

# ── Resolve project root regardless of CWD ────────────────────────
ROOT = Path(__file__).resolve().parent.parent

# ── Force UTF-8 so non-ASCII icons (✅/❌) render on Windows ──────
for stream_name in ("stdout", "stderr"):
    stream = getattr(sys, stream_name, None)
    if stream and hasattr(stream, "reconfigure"):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:  # pragma: no cover — best effort
            pass


# ── Tiny color helpers (degrade when stdout is not a TTY) ─────────
def _ansi(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if sys.stdout.isatty() else text


def green(s: str) -> str: return _ansi("32", s)
def red(s: str) -> str: return _ansi("31", s)
def yellow(s: str) -> str: return _ansi("33", s)
def bold(s: str) -> str: return _ansi("1", s)


DIST = ROOT / "dist" / "Genesis"
PICKER_SRC = ROOT / "gui" / "profile_picker.html"

# The canonical build output + the two deployed copies.
EXES = [
    DIST / "Genesis.exe",
    ROOT / "Genesis.exe",
    ROOT / "Genesis" / "Genesis.exe",
]

# Source picker + the three bundled copies.
PICKERS = [
    PICKER_SRC,
    DIST / "_internal" / "gui" / "profile_picker.html",
    ROOT / "_internal" / "gui" / "profile_picker.html",
    ROOT / "Genesis" / "_internal" / "gui" / "profile_picker.html",
]

# Content markers that must be present in the served picker HTML.
PICKER_MARKERS = [
    "<title>Genesis — Select Profile</title>",
    'data-profile="default"',
    'data-profile="scalper"',
    'data-profile="breakout"',
    'data-profile="daytrader"',
    'rel="icon"',
]


# ── Helpers ───────────────────────────────────────────────────────
def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _find_chrome() -> Path | None:
    """Locate a Chrome/Chromium binary (Windows + *nix common paths)."""
    candidates = [
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path("/usr/bin/google-chrome"),
        Path("/usr/bin/chromium"),
        Path("/usr/bin/chromium-browser"),
    ]
    for c in candidates:
        if c.is_file():
            return c
    which = shutil.which("chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
    return Path(which) if which else None


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _serve(directory: Path, port: int) -> tuple[http.server.HTTPServer, threading.Thread]:
    handler_cls = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(directory)
    )
    handler_cls.log_message = lambda *_: None  # type: ignore[attr-defined]
    server = http.server.HTTPServer(("127.0.0.1", port), handler_cls)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server, t


# ── Step 1: launcher --version ────────────────────────────────────
def check_version(expected: str) -> int:
    print(bold("Step 1/3 — launcher --version on every exe copy…"))
    ok = True
    for exe in EXES:
        if not exe.is_file():
            ok = False
            print(red(f"❌ Missing exe: {exe}"))
            continue
        try:
            # Windowed build: --version allocates a console and waits for
            # Enter — pipe a newline so the process exits cleanly.
            cp = subprocess.run(
                [str(exe), "--version"],
                input="\n",
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=60,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            out = (cp.stdout or "") + (cp.stderr or "")
            # Parse the exact version token ("Genesis Launcher 2.1.0" → "2.1.0")
            # and compare with == so a prefix like "12.1.0" can't pass an
            # expected "2.1.0" via endswith.
            version_line = next(
                (ln.strip() for ln in out.splitlines() if "Genesis Launcher" in ln),
                None,
            )
            got = version_line.rsplit(None, 1)[-1] if version_line else None
            if got == expected:
                print(green(f"✅ {exe} → {version_line.strip()}"))
            else:
                ok = False
                print(red(f"❌ {exe} version mismatch. Expected '{expected}', got '{got}'"))
                if not version_line:
                    print(f"   raw output: {out[:200]!r}")
        except (OSError, subprocess.TimeoutExpired) as exc:
            ok = False
            print(red(f"❌ {exe} could not run: {exc}"))
    return 0 if ok else 1


# ── Step 2: byte-identity hashes ──────────────────────────────────
def check_hashes() -> int:
    print(bold("Step 2/3 — byte-identity (sha256) of exes + bundled picker…"))
    ok = True

    # Require that EVERY expected copy was actually hashed — otherwise a
    # missing copy would shrink the set to 1 and falsely pass "byte-identical".
    exe_hashes = [_sha256(p) for p in EXES if p.is_file()]
    if len(exe_hashes) == len(EXES) and len(set(exe_hashes)) == 1:
        print(green(f"✅ all {len(EXES)} exe copies byte-identical ({exe_hashes[0][:12]}…)"))
    else:
        ok = False
        print(red("❌ exe copies differ or are missing!"))
        for p in EXES:
            print(f"   {p}: {'missing' if not p.is_file() else _sha256(p)[:12]}")

    picker_hashes = [_sha256(p) for p in PICKERS if p.is_file()]
    if len(picker_hashes) == len(PICKERS) and len(set(picker_hashes)) == 1:
        print(green(f"✅ all {len(PICKERS)} picker copies byte-identical ({picker_hashes[0][:12]}…)"))
    else:
        ok = False
        print(red("❌ picker copies differ or are missing!"))
        for p in PICKERS:
            print(f"   {p}: {'missing' if not p.is_file() else _sha256(p)[:12]}")

    return 0 if ok else 1


# ── Step 3: render check ──────────────────────────────────────────
def check_render() -> int:
    print(bold("Step 3/3 — bundled picker render…"))
    gui_dir = DIST / "_internal" / "gui"
    picker = gui_dir / "profile_picker.html"
    if not picker.is_file():
        print(red(f"❌ Bundled picker missing: {picker}"))
        return 1

    port = _free_port()
    server, thread = _serve(gui_dir, port)
    url = f"http://127.0.0.1:{port}/profile_picker.html"
    ok = True
    try:
        # 3a. HTTP fetch + content markers
        try:
            import urllib.request
            with urllib.request.urlopen(url, timeout=15) as resp:
                status = resp.status
                body = resp.read().decode("utf-8", errors="replace")
        except Exception as exc:
            ok = False
            print(red(f"❌ Could not fetch {url}: {exc}"))
            return 1
        print(green(f"✅ HTTP {status} from bundled picker"))
        for marker in PICKER_MARKERS:
            if marker in body:
                print(green(f"✅ marker present: {marker[:40]}{'…' if len(marker) > 40 else ''}"))
            else:
                ok = False
                print(red(f"❌ missing marker: {marker}"))

        # 3b. Headless Chrome render (best-effort — Chrome may be absent)
        chrome = _find_chrome()
        if chrome is None:
            print(yellow("⚠️  Chrome not found — skipping headless DOM render (HTTP markers still passed)."))
        else:
            dom = _render_with_chrome(chrome, url)
            if dom is None:
                ok = False
                print(red("❌ headless Chrome render failed"))
            elif all(m in dom for m in ["Genesis — Select Profile", 'data-profile="daytrader"', 'rel="icon"']):
                print(green(f"✅ headless Chrome rendered the picker ({len(dom)} chars DOM)"))
            else:
                ok = False
                print(red("❌ headless Chrome rendered but key markers missing from DOM"))
    finally:
        server.shutdown()
        thread.join(timeout=5)
    return 0 if ok else 1


def _render_with_chrome(chrome: Path, url: str) -> str | None:
    """Render *url* headless and return the DOM, or None on failure."""
    # --headless=new needs Chrome ≥112; fall back to legacy --headless for
    # older installs so the render check still runs.
    base = [
        "--disable-gpu",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--dump-dom",
        url,
    ]
    for headless_flag in ("--headless=new", "--headless"):
        try:
            cp = subprocess.run(
                [str(chrome), headless_flag, *base],
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=90,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            dom = (cp.stdout or "") + (cp.stderr or "")
            if cp.returncode == 0 and len(dom) > 500:
                return dom
            print(yellow(f"⚠️  headless Chrome ({headless_flag}) exit={cp.returncode}, dom_len={len(dom)}"))
        except (OSError, subprocess.TimeoutExpired) as exc:
            print(yellow(f"⚠️  headless Chrome ({headless_flag}) failed: {exc}"))
    return None


# ── Entry point ───────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Smoke-test the rebuilt Genesis bundle (version, hashes, render).",
    )
    parser.add_argument(
        "--version",
        default="2.1.0",
        help="Expected launcher version string (default: 2.1.0).",
    )
    parser.add_argument(
        "--no-render",
        action="store_true",
        help="Skip the render check (steps 1-2 only).",
    )
    args = parser.parse_args(argv)

    if not (DIST / "Genesis.exe").is_file():
        print(red(f"❌ Build output missing at {DIST}. Run scripts/build_deploy.py first."))
        return 2

    results = [check_version(args.version), check_hashes()]
    if not args.no_render:
        results.append(check_render())
    else:
        print(yellow("⏭️  --no-render: skipping render check."))

    if all(r == 0 for r in results):
        print(green("✅ All smoke tests passed."))
        return 0
    print(red("❌ Smoke test failed."))
    return 1


if __name__ == "__main__":
    sys.exit(main())
