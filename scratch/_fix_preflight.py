"""Replace the broken preflight-failure block in launch_gui.py with a clean
version constructed at runtime. Avoids the harness's</ scrubbing."""
from pathlib import Path

LT = "<"
SL = "/"
GT = ">"

CODE_TO_REPLACE_START = '            html = ('
CODE_TO_REPLACE_END = '            )'
# We'll find the block programmatically by collapse logic.

src_path = Path(r"C:\Users\Moses Egbunike\Documents\Claude Code Projects\Genesis\launch_gui.py")
text = src_path.read_text(encoding="utf-8")

i = text.find('html = (')
assert i != -1, "couldn't find html = ("
depth = 0
j = i
while j < len(text):
    if text[j:j+5] == 'html ':
        pass
    if text[j] == '(' and (j == i or text[j-1] != '"'):
        depth += 1
    elif text[j] == ')':
        depth -= 1
        if depth == 0:
            break
    j += 1
assert depth == 0, f"unbalanced parens; ended at j={j}"
segment_end = j + 1  # include the close paren

# Confirm the segment actually contains the broken pattern
seg = text[i:segment_end]
print("REPLACING segment %d..%d (%d chars):" % (i, segment_end, len(seg)))
print("---first 80---")
print(seg[:80])
print("---last 200---")
print(seg[-200:])

# Build the new clean code using string concatenation.
# This code contains zero `</...>` substrings in the source.
LT, SL, GT = "<", "/", ">"
C_ENDIV = LT + SL + "div" + GT
C_ENPRE = LT + SL + "pre" + GT
C_ENBODY = LT + SL + "body" + GT
C_ENHTML = LT + SL + "html" + GT
C_ENHEAD = LT + SL + "head" + GT
C_ENSTYLE = LT + SL + "style" + GT

# We construct the assignment line by line to avoid the harness.
new_lines = [
    "            html = ",

    "                " + repr(
        '<!doctype html><html><head><meta charset="utf-8">'
        '<title>Genesis - preflight failed</title>'
        "<style>"
        "html,body{margin:0;height:100%;background:#1a0d12;color:#ffd9d9;"
        "font:13px/1.45 Consolas,'Courier New',monospace;}"
        ".w{padding:24px 32px;}"
        "h1{margin:0 0 12px;font:600 16px system-ui;}"
        "pre{white-space:pre-wrap;color:#ffe6e6;}"
       </style</head><body>"
        "<div class='w'>"
        "<h1>Genesis preflight check failed</h1>"
        "<pre>" + output +</pre>"
       </div</body</html>"
    ) + " .replace(\"&\", \"&\") .replace(\"<\", \"<\")",
]
new_segment = "\n".join(new_lines)

print("\nNEW SEGMENT preview:")
print(new_segment[:300])

# Splice.
new_text = text[:i] + new_segment + text[segment_end:]
src_path.write_text(new_text, encoding="utf-8")
print("\nwrote", len(new_text), "chars")
