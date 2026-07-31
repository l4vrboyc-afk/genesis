"""
Genesis — one-shot PyInstaller build + deploy sync.

Rebuilds the launcher exe via ``Genesis.spec`` and mirrors the fresh
output into the repo's three bundle copies (see docs/LAUNCHER.md
"Deploying the rebuilt bundle"):

    1. ``dist/Genesis/``            — canonical PyInstaller build output
    2. project root                 — ``Genesis.exe`` + ``_internal/``
    3. ``Genesis/`` subfolder       — self-contained deployment copy

After syncing, the script verifies every deployed ``_internal`` tree is
recursively identical to the build output, and that the picker HTML
matches ``gui/profile_picker.html``.

Usage
-----
    python scripts/build_deploy.py           # build + sync + verify
    python scripts/build_deploy.py --skip-build   # sync existing dist output
    python scripts/build_deploy.py --force        # proceed even if Genesis.exe is running

Exit codes
----------
    0   build + sync + verify all passed
    1   a step failed (build, copy, or tree mismatch)
    2   misuse (missing spec / dist output)
"""
from __future__ import annotations

import argparse
import filecmp
import shutil
import subprocess
import sys
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
SPEC = ROOT / "Genesis.spec"
PICKER_SRC = ROOT / "gui" / "profile_picker.html"

# The two deployed copies: (destination exe path, destination _internal dir)
DEPLOYS = [
    (ROOT / "Genesis.exe", ROOT / "_internal"),
    (ROOT / "Genesis" / "Genesis.exe", ROOT / "Genesis" / "_internal"),
]


# ── Helpers ───────────────────────────────────────────────────────
def _venv_python() -> Path:
    if sys.platform == "win32":
        return ROOT / ".venv" / "Scripts" / "python.exe"
    return ROOT / ".venv" / "bin" / "python"


def _is_running_genesis() -> bool:
    """Best-effort check: is a Genesis.exe launcher currently running?"""
    if sys.platform != "win32":
        return False
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq Genesis.exe", "/NH"],
            capture_output=True, text=True, timeout=10,
        ).stdout or ""
        return "Genesis.exe" in out
    except Exception:
        return False


def _sync_one(src_exe: Path, src_internal: Path, exe_dst: Path, internal_dst: Path) -> None:
    """Mirror one deployment copy: remove stale dir, then fresh copy."""
    exe_dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_exe, exe_dst)
    if internal_dst.exists():
        shutil.rmtree(internal_dst)  # fresh tree — no stale leftovers
    shutil.copytree(src_internal, internal_dst)


def _trees_identical(a: Path, b: Path) -> list[str]:
    """Recursively compare two directories; return a list of differences.

    Uses strict content comparison (``dcmp.shallow = False``) — the default
    ``dircmp`` only compares size + mtime, which would miss a same-size /
    same-mtime content change.
    """
    diffs: list[str] = []

    def _rel(path: Path) -> str:
        try:
            return str(path.relative_to(a))
        except ValueError:
            return str(path)

    def _walk(left: Path, right: Path) -> None:
        dcmp = filecmp.dircmp(left, right)
        dcmp.shallow = False  # strict: compare file contents, not just stat
        for name in dcmp.left_only:
            diffs.append(f"{_rel(left)}/{name} only in source")
        for name in dcmp.right_only:
            diffs.append(f"{_rel(right)}/{name} only in deployed copy")
        for name in dcmp.diff_files:
            diffs.append(f"{_rel(left)}/{name} content differs")
        for name in dcmp.funny_files:
            diffs.append(f"{_rel(left)}/{name} cannot be compared")
        for sub in dcmp.subdirs.values():
            _walk(Path(sub.left), Path(sub.right))

    _walk(a, b)
    return diffs


def run_build(python: Path) -> int:
    print(bold("Step 1/3 — PyInstaller build (Genesis.spec)…"))
    result = subprocess.run(
        [str(python), "-m", "PyInstaller", "--noconfirm", str(SPEC)],
        cwd=str(ROOT),
    )
    if result.returncode != 0:
        print(red(f"❌ PyInstaller failed with exit code {result.returncode}."))
        return 1
    print(green("✅ PyInstaller build completed."))
    return 0


def sync_deploys() -> int:
    print(bold("Step 2/3 — syncing deployed copies…"))
    src_exe = DIST / "Genesis.exe"
    src_internal = DIST / "_internal"
    if not src_exe.exists() or not src_internal.exists():
        print(red(f"❌ Build output missing at {DIST}. Run without --skip-build first."))
        return 2
    for exe_dst, internal_dst in DEPLOYS:
        try:
            _sync_one(src_exe, src_internal, exe_dst, internal_dst)
        except (PermissionError, OSError) as exc:
            print(red(f"❌ Sync to {exe_dst} failed: {exc}"))
            print(yellow("   If Genesis.exe is running, close it first — a running exe is locked."))
            return 1
        print(green(f"✅ {exe_dst} + {internal_dst}"))
    return 0


def verify() -> int:
    print(bold("Step 3/3 — verifying deployed trees…"))
    ok = True
    src_exe = DIST / "Genesis.exe"
    src_internal = DIST / "_internal"

    # Every deployed Genesis.exe must be byte-identical to the build output.
    for exe_dst, _ in DEPLOYS:
        if not exe_dst.exists():
            ok = False
            print(red(f"❌ Missing exe: {exe_dst}"))
        elif not filecmp.cmp(src_exe, exe_dst, shallow=False):
            ok = False
            print(red(f"❌ exe differs from dist: {exe_dst}"))
        else:
            print(green(f"✅ {exe_dst.name} matches dist (bytes)"))

    # Every deployed _internal tree must match the build output.
    for _, internal_dst in DEPLOYS:
        diffs = _trees_identical(src_internal, internal_dst)
        if diffs:
            ok = False
            print(red(f"❌ Tree mismatch: {internal_dst}"))
            for d in diffs[:10]:
                print(f"   - {d}")
            if len(diffs) > 10:
                print(f"   … and {len(diffs) - 10} more")
        else:
            print(green(f"✅ {internal_dst} matches dist (recursive)"))

    # The bundled picker must match the source restyle.
    for _, internal_dst in DEPLOYS:
        bundled = internal_dst / "gui" / "profile_picker.html"
        if not bundled.exists():
            ok = False
            print(red(f"❌ Missing picker in {internal_dst}"))
        elif not filecmp.cmp(PICKER_SRC, bundled, shallow=False):
            ok = False
            print(red(f"❌ Picker differs from source in {internal_dst}"))
        else:
            print(green(f"✅ picker matches in {internal_dst}"))

    if not ok:
        print(red("❌ Verification failed."))
        return 1
    print(green("✅ All deployed copies are identical to the build output."))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build the launcher exe and sync the deployed bundle copies.",
    )
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help="Skip the PyInstaller build; sync the existing dist/Genesis output.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Proceed even if a Genesis.exe process is running (file may be locked).",
    )
    args = parser.parse_args(argv)

    if not SPEC.exists():
        print(red(f"❌ Genesis.spec not found at {SPEC}."))
        return 2

    if _is_running_genesis() and not args.force:
        print(yellow("⚠️  Genesis.exe appears to be running — the exe is locked."))
        print(yellow("   Close it first, or re-run with --force to try anyway."))
        return 1

    rc = 0 if args.skip_build else run_build(_venv_python())
    if rc != 0:
        return rc
    rc = sync_deploys()
    if rc != 0:
        return rc
    return verify()


if __name__ == "__main__":
    sys.exit(main())
