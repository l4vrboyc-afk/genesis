# Genesis Visual Engine — MQL5 EA Installation Guide

The visual engine draws entry, stop-loss, take-profit lines and HUD labels
directly on your MetaTrader 5 charts. It works via a **companion MQL5 EA**
that must be installed and running in MT5.

## How It Works

```
Python Bot  ──JSON──▶  Shared File  ──READ──▶  MQL5 EA  ──DRAWS──▶  MT5 Chart
```

The Python bot writes visual instructions to a shared JSON file. The MQL5
EA reads this file every 500ms and creates/updates/deletes chart objects
(Horizontal Lines, Trend Lines, Labels) accordingly.

## Installation

### Step 1: Find Your MT5 Data Folder

1. Open MetaTrader 5
2. Go to **File → Open Data Folder** (or press `Ctrl+Shift+D`)
3. This opens your MT5 data directory (e.g. `C:\Users\<You>\AppData\Roaming\MetaQuotes\Terminal\<InstanceID>\`)

### Step 2: Install the MQL5 EA

1. Download `genesis_visualizer.mq5` from the project's `bot/visual/` folder
2. Copy it to: `<MT5_Data>\MQL5\Experts\`
3. In MT5, open the **Navigator** panel (`Ctrl+N`)
4. Right-click **Expert Advisors** → **Refresh**
5. Find **Genesis Visualizer** in the list
6. Drag-and-drop it onto **any chart** (EURUSD recommended)
7. In the dialog:
   - Enable **Allow Expert Advisors** (must be checked in Tools → Options → Expert Advisors too)
   - Enable **Allow DLL imports** (not required but recommended for future updates)
   - Click **OK**

### Step 3: Verify It's Running

On the top-right corner of the chart, you should see a smiley face 😊
(if not, hover the Expert Advisor label to see the error).

The EA will automatically find and update charts for every symbol
your Genesis bot trades.

### Important: Auto-Start (Optional)

If you want the EA to auto-attach when MT5 starts:

1. In MT5, go to **Tools → Options → Expert Advisors**
2. Enable **Allow Automated Trading**
3. Right-click the attached EA on the chart → **Expert Properties → Common**
4. Check **Allow modifications of signal's settings** and uncheck **Do not delete pending orders on deactivation**

### Visual Elements

For each open position, the EA draws:

| Element | Colour | Style | Description |
|---------|--------|-------|-------------|
| Entry Line | 🟡 Goldenrod | Solid | Horizontal line at entry price |
| Stop Loss | 🔷 Cyan | Dashed | Moves with dynamic trailing |
| Take Profit | 🟢 Lime | Dashed | Static profit target |
| HUD | White text | Top-left | `GENESIS | Ticket: #12345 | Mode: STRUCTURE | ATR: 0.00142` |

### Troubleshooting

**Q: Nothing appears on the chart**
- Make sure the Genesis bot is running and has open positions
- Make sure the EA is attached and showing a smiley face
- Check that the EA is on a chart for a symbol the bot is trading
- Check `%AppData%\Roaming\MetaQuotes\Terminal\Common\Files\genesis_visuals.json`
  exists (the Python bot writes to this file)

**Q: "Cannot find chart for symbol" in logs**
- The EA checks every chart in MT5 for the traded symbol
- Open at least one chart per symbol the bot trades, or attach the EA to one chart
  and it will try that as fallback

**Q: EA shows errors on attach**
- Make sure `Allow Expert Advisors` is checked in MT5
- Make sure `FileOpen` operations are allowed (they are by default)

### Updating

When the MQL5 file is updated (new features, bug fixes), simply:

1. Replace the `.mq5` file in `<MT5_Data>\MQL5\Experts\`
2. In MT5: **Navigator → Expert Advisors → Right-click → Refresh**
3. Detach and re-attach the EA to your charts
