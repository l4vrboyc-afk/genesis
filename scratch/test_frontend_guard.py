"""Unit-exercise scripts/build_deploy.verify_frontend().

Monkeypatches FRONTEND_SRC / CHECK_JS to temp dirs so we can verify the
success path AND each failure path (missing entry, missing asset, broken
JS, missing frontend dir) without touching the real source tree.
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

spec = importlib.util.spec_from_file_location(
    "build_deploy", str(ROOT / "scripts" / "build_deploy.py")
)
bd = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(bd)


def _make_frontend(files: dict[str, str], check_js: str | None) -> Path:
    tmp = Path(tempfile.mkdtemp())
    for name, content in files.items():
        p = tmp / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    if check_js is not None:
        (tmp / "check_js.py").write_text(check_js, encoding="utf-8")
    return tmp


results: list[str] = []


def check(name: str, actual: int, expected: int) -> None:
    status = "PASS" if actual == expected else "FAIL"
    results.append(f"{status} {name}: rc={actual} (expected {expected})")


# 1. Success: complete real frontend (the actual repo source).
rc = bd.verify_frontend()
check("real frontend success", rc, 0)

# 2. Missing frontend dir entirely.
orig_src, orig_check = bd.FRONTEND_SRC, bd.CHECK_JS
missing_dir = Path(tempfile.mkdtemp()) / "does_not_exist"
bd.FRONTEND_SRC = missing_dir
rc = bd.verify_frontend()
check("missing dir", rc, 1)
bd.FRONTEND_SRC, bd.CHECK_JS = orig_src, orig_check

# 3. Missing entry file (styles.css).
# CHECK_JS is pointed at a non-existent path so the subprocess step is
# skipped without setting it to None (which would crash the None-guarded
# CHECK_JS.is_file() check if that guard were ever removed).
bd.FRONTEND_SRC = _make_frontend(
    {"index.html": "<html></html>", "app.js": "const a = 1;"}, None
)
bd.CHECK_JS = bd.FRONTEND_SRC / "nope"
rc = bd.verify_frontend()
check("missing entry", rc, 1)
bd.FRONTEND_SRC, bd.CHECK_JS = orig_src, orig_check

# 4. Missing asset referenced by index.html (vendor/chart.umd.min.js).
bd.FRONTEND_SRC = _make_frontend(
    {
        "index.html": '<script src="vendor/chart.umd.min.js"></script>',
        "styles.css": "body {}",
        "app.js": "const a = 1;",
    },
    None,
)
bd.CHECK_JS = bd.FRONTEND_SRC / "nope"
rc = bd.verify_frontend()
check("missing asset", rc, 1)
bd.FRONTEND_SRC, bd.CHECK_JS = orig_src, orig_check

# 5. Broken JS (check_js.py subprocess exits 1).
bd.FRONTEND_SRC = _make_frontend(
    {
        "index.html": "<html></html>",
        "styles.css": "body {}",
        "app.js": "const a = 1;",
    },
    'print("UNBALANCED")\nimport sys\nsys.exit(1)\n',
)
# Point CHECK_JS at the temp script we just wrote to simulate a failing
# delimiter validator.
bd.CHECK_JS = bd.FRONTEND_SRC / "check_js.py"
rc = bd.verify_frontend()
check("broken js", rc, 1)
bd.FRONTEND_SRC, bd.CHECK_JS = orig_src, orig_check

# 6. Asset-ref parser skips external/anchor/query refs correctly.
html = (
    '<script src="https://cdn.tailwindcss.com"></script>\n'
    '<script src="vendor/chart.umd.min.js?v=2"></script>\n'
    '<link href="styles.css" rel="stylesheet">\n'
    '<a href="#section">x</a>\n'
    '<img src="data:image/png;base64,xxx">\n'
)
refs = bd._local_asset_refs(html)
expected = ["vendor/chart.umd.min.js", "styles.css"]
status = "PASS" if refs == expected else "FAIL"
results.append(f"{status} asset-ref parser: {refs} (expected {expected})")

print("\n".join(results))
failures = [r for r in results if r.startswith("FAIL")]
sys.exit(1 if failures else 0)
