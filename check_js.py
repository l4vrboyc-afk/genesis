"""Check JavaScript brace/paren/bracket balance in app.js.

Scans dashboard/frontend/app.js for unbalanced delimiters, ignoring
strings, line comments, and block comments.  Exits 0 on success, 1 if
any imbalance is found.

Usage:
    python check_js.py          # human-readable output
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# ── Resolve frontend/app.js relative to this script's location ──────
PROJECT_ROOT = Path(__file__).resolve().parent
filepath = PROJECT_ROOT / "dashboard" / "frontend" / "app.js"

if not filepath.exists():
    # Fallback to the legacy absolute path (for backwards compat)
    filepath = Path(r"C:\Users\Moses Egbunike\Documents/Claude Code Projects/Genesis\dashboard/frontend/app.js")

with open(filepath, encoding="utf-8") as f:
    js = f.read()

# ── Track brace/paren/bracket balance, ignoring strings and comments ──
depth_brace = 0
depth_paren = 0
depth_bracket = 0
in_string = None  # ' ', ", `, or None
in_comment = False
in_line_comment = False
line_num = 1
errors = []

i = 0
while i < len(js):
    ch = js[i]

    if in_string:
        if ch == "\\":
            i += 1  # Skip next char (escape sequence)
        elif ch == in_string:
            in_string = None
        if ch == "\n":
            line_num += 1
        i += 1
        continue

    if in_line_comment:
        if ch == "\n":
            in_line_comment = False
            line_num += 1
        i += 1
        continue

    if in_comment:
        if ch == "*" and i + 1 < len(js) and js[i + 1] == "/":
            in_comment = False
            i += 2
            continue
        if ch == "\n":
            line_num += 1
        i += 1
        continue

    # Line comment
    if ch == "/" and i + 1 < len(js) and js[i + 1] == "/":
        in_line_comment = True
        i += 2
        continue

    # Block comment
    if ch == "/" and i + 1 < len(js) and js[i + 1] == "*":
        in_comment = True
        i += 2
        continue

    # String start (single, double, template)
    if ch in ('"', "'", "`"):
        in_string = ch
        i += 1
        continue

    # Delimiters
    if ch == "{":
        depth_brace += 1
    elif ch == "}":
        depth_brace -= 1
        if depth_brace < 0:
            errors.append(f"Line {line_num}: Extra closing brace }}")
            depth_brace = 0
    elif ch == "(":
        depth_paren += 1
    elif ch == ")":
        depth_paren -= 1
        if depth_paren < 0:
            errors.append(f"Line {line_num}: Extra closing paren )")
            depth_paren = 0
    elif ch == "[":
        depth_bracket += 1
    elif ch == "]":
        depth_bracket -= 1
        if depth_bracket < 0:
            errors.append(f"Line {line_num}: Extra closing bracket ]")
            depth_bracket = 0

    if ch == "\n":
        line_num += 1
    i += 1

# ── Report ─────────────────────────────────────────────────────────
print(f"File: {filepath}")
print(f"End of script: brace={depth_brace}, paren={depth_paren}, bracket={depth_bracket}")
print(f"String still open: {in_string}")
print(f"Errors: {errors if errors else 'None'}")

if depth_brace != 0 or depth_paren != 0 or depth_bracket != 0 or in_string or errors:
    sys.exit(1)
