#!/usr/bin/env python3
"""Edit scalper_momentum.py to add risk_reward_ratio computation."""

path = r"C:\Users\Moses Egbunike\Documents\Claude Code Projects\Genesis\bot\strategies\scalper_momentum.py"
with open(path, "r", newline="") as f:
    content = f.read()

# Locate the exact block
old_start = " signal = TradeSignal(\n"
idx = content.find(old_start)
print(f"Found TradeSignal at index: {idx}")
print(f"Context (100 chars): {repr(content[idx:idx+100])}")

# The block from 'signal = TradeSignal' through 'return None'
# We replace from 'signal = TradeSignal(' to 'return None\n'
old_block = (
    " signal = TradeSignal(\n"
    "        direction=direction,\n"
    "        symbol=symbol,\n"
    "        entry_price=entry,\n"
    "        stop_loss=sl,\n"
    "        take_profit=tp,\n"
    "        confidence=confidence,\n"
    "        strategy_name=self.name,\n"
    "        timeframe=settings.entry_timeframe,\n"
    "        reason=reason\n"
    "    )\n"
    "\n"
    "    if self.validate_signal(signal):\n"
    "        return signal\n"
    "    return None\n"
)

new_block = (
    " signal = TradeSignal(\n"
    "        direction=direction,\n"
    "        symbol=symbol,\n"
    "        entry_price=entry,\n"
    "        stop_loss=sl,\n"
    "        take_profit=tp,\n"
    "        confidence=confidence,\n"
    "        strategy_name=self.name,\n"
    "        timeframe=settings.entry_timeframe,\n"
    "        reason=reason\n"
    "    )\n"
    "\n"
    "    risk = abs(entry - sl)\n"
    "    reward = abs(tp - entry)\n"
    "    signal.risk_reward_ratio = round(reward / risk, 2) if risk > 0 else 0.0\n"
    "\n"
    "    if self.validate_signal(signal):\n"
    "        return signal\n"
    "    return None\n"
)

if old_block in content:
    content = content.replace(old_block, new_block, 1)
    with open(path, "w", newline="") as f:
        f.write(content)
    print("SUCCESS: scalper_momentum.py updated")
else:
    print("FAILED: block not found")
    # Dump the actual bytes
    start = content.find("signal = TradeSignal")
    print(f"Actual bytes: {repr(content[start:start+300])}")
