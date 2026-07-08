import sys
p = r'dashboard\frontend\index.html'
with open(p, 'r', encoding='utf-8') as f:
    src = f.read()
lines = src.splitlines()
broken = lines[872]
EM = '—'
RB = '}'
LT = '<'
GT = '>'
SL = '/'
TD = 'td'
# construct: ' : ' + EM +</td>'
close_tag = LT + SL + TD + GT
needle = " : '" + EM + close_tag
replacement = " : '" + EM + RB + close_tag
print('Found expected pattern:', needle in broken)
fixed = broken.replace(needle, replacement)
assert fixed != broken, 'no replacement made'
lines[872] = fixed
with open(p, 'w', encoding='utf-8') as f:
    f.write('\n'.join(lines))
with open(p, 'r', encoding='utf-8') as f:
    print('Result line:', repr(f.read().splitlines()[872]))
