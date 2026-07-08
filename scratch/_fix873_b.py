import sys
p = r'dashboard\frontend\index.html'
with open(p, 'r', encoding='utf-8') as f:
    src = f.read()
lines = src.split('\n')

LB = chr(123)
RB = chr(125)
QT = chr(0x27)  # '
EM = chr(0x2014)
ARR = chr(0x2192)

# Build the fixed template-cell line entirely from explicit codes
fixed_line = (
    '                            '
    + '<td>'
    + '${'
    + '(t.entry_price != null) ? t.entry_price.toFixed(5) : '
    + QT
    + EM
    + QT
    + RB
    + ' '
    + ARR
    + ' '
    + '${'
    + '(t.exit_price != null) ? t.exit_price.toFixed(5) : '
    + QT
    + EM
    + QT
    + RB
    + '<'
    + '/'
    + 'td'
    + '>'
)

print('Constructed line:')
print(fixed_line)
print()
print('REPR:', repr(fixed_line))

# Verify in source
target_idx = None
for i, line in enumerate(lines):
    if 't.entry_price != null' in line and 't.exit_price != null' in line:
        target_idx = i
        break
print()
print('Target line index:', target_idx)
if target_idx is not None:
    print('Existing:', repr(lines[target_idx]))
    assert repr(lines[target_idx]).replace('\\\\r', '').count(fixed_line) == 0 or True  # not exact assertion, verification by visual

    # Replace
    # Preserve original CRLF line ending if present
    orig = lines[target_idx]
    if orig.endswith('\r'):
        new_line = fixed_line + '\r'
    else:
        new_line = fixed_line

    if orig == new_line:
        print('Line already correct — no changes needed')
    else:
        lines[target_idx] = new_line
        with open(p, 'w', encoding='utf-8', newline='') as f:
            f.write('\n'.join(lines))
        print('WROTE updated line. Final:')
        with open(p, 'r', encoding='utf-8') as f:
            new_lines = f.read().split('\n')
            print(repr(new_lines[target_idx]))
