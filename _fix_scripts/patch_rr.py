#!/usr/bin/env python3
"""Patch risk_reward_ratio into scalper_momentum and session_breakout."""
import os

def detect_indent(filepath):
    """Detect the leading whitespace of lines inside generate_signal."""
    with open(filepath, "r", newline="") as f:
        lines = f.readlines()
    # Find lines inside generate_signal body (after 'def generate_signal')
    for i, line in enumerate(lines):
        if "signal = TradeSignal" in line:
            indent = line[:len(line) - len(line.lstrip())]
            return indent, lines
    return None, lines

def patch_file(filepath, class_name):
    """Patch a strategy file to add explicit risk_reward_ratio assignment."""
    base_indent, lines = detect_indent(filepath)
    if base_indent is None:
        print(f"[FAIL] {class_name}: could not detect indent")
        return False

    print(f"[INFO] {class_name}: indent repr = {repr(base_indent)}")

    # Build old and new blocks by reading the actual file structure
    new_lines = []
    patched = False
    i = 0
    while i < len(lines):
        line = lines[i]
        if not patched and line.strip() == "if self.validate_signal(signal):":
            # Insert the risk_reward_ratio computation BEFORE validate_signal
            # We need to insert after the TradeSignal closing paren + blank line
            new_lines.append("\n")
            new_lines.append(base_indent + "risk = abs(entry - sl)\n")
            new_lines.append(base_indent + "reward = abs(tp - entry)\n")
            new_lines.append(base_indent + "signal.risk_reward_ratio = round(reward / risk, 2) if risk > 0 else 0.0\n")
            new_lines.append("\n")
            patched = True
        new_lines.append(line)
        i += 1

    if patched:
        with open(filepath, "w", newline="") as f:
            f.writelines(new_lines)
        print(f"[OK] {class_name}: risk_reward_ratio patch applied")
        return True
    else:
        print(f"[FAIL] {class_name}: 'validate_signal' block not found")
        return False


BASE = r"C:\Users\Moses Egbunike\Documents\Claude Code Projects\Genesis"
results = []
results.append(patch_file(
    os.path.join(BASE, r"bot\strategies\scalper_momentum.py"),
    "scalper_momentum"
))
results.append(patch_file(
    os.path.join(BASE, r"bot\strategies\session_breakout.py"),
    "session_breakout"
))

if all(results):
    print("\nAll patches applied successfully.")
else:
    print("\nSome patches failed.")
