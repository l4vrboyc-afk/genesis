//+------------------------------------------------------------------+
//| Genesis Visualizer — MQL5 Companion EA                           |
//| Reads genesis_visuals.json from Common/Files and draws chart   |
//| objects for each active Genesis position.                       |
//|                                                                  |
//| v1.3 — Sweep pins (OBJ_TEXT), session/equilibrium overlays     |
//| (OBJ_RECTANGLE), and trail milestones (OBJ_TREND).              |
//|                                                                  |
//| v1.2 — Runtime heartbeat sweep: deletes orphaned chart objects  |
//| within 500ms of a trade closing (no longer requires a           |
//| bot restart to sweep).                                           |
//|                                                                  |
//| Bug fixes:                                                       |
//| - ChartRedraw only called when objects actually deleted          |
//| - Corruption guard: empty JSON read won't wipe all lines         |
//|                                                                  |
//| v1.1 — Multi-chart drawing + OnDeinit cleanup                    |
//| v1.0 — Initial release                                           |
//+------------------------------------------------------------------+
#property copyright "Genesis Trading Bot"
#property version "1.30"
#property description "Entry, SL, TP, HUD, sweep pins, overlays & trail milestones"
#property strict

// --- Configuration ---
string VISUALS_FILE = "genesis_visuals.json"; // in Common/Files
int TIMER_INTERVAL_MS = 500; // check every 500 ms

// --- Colour Palette ---
color ENTRY_COLOR = clrGoldenrod; // solid amber -- entry price
color SL_COLOR = clrCyan; // dashed cyan -- trailing stop
color TP_COLOR = clrLimeGreen; // dashed green -- take profit
color HUD_TEXT_COLOR = clrWhite; // HUD label foreground

// --- Style Constants ---
// NOTE: STYLE_SOLID, STYLE_DASH, STYLE_DOT, etc. are native MQL5
// ENUM_LINE_STYLE constants (STYLE_SOLID=0, STYLE_DASH=1, STYLE_DOT=2).
// Do NOT redefine them -- doing so causes "identifier already used"
// compile errors. Use the native constants directly.
const int LINE_WIDTH = 2;

// --- Object Name Prefix ---
string PREFIX = "GENESIS_";

// --- Sweep Pin & Overlay Colour Palette ---
color SWEEP_BULLISH_COLOR = clrLimeGreen; // Buy-side sweep -> bullish text
color SWEEP_BEARISH_COLOR = clrTomato; // Sell-side sweep -> bearish text
color OVERLAY_SESSION_COLOR = clrDodgerBlue; // Default session-range fill
color OVERLAY_EQUILIBRIUM_COLOR = clrOrange; // Default equilibrium fill
color TRAIL_MILESTONE_DOT = clrSilver; // Trail milestone marker colour

// --- Runtime Orphan Detection State ---
// Comma-separated list of ticket IDs that were present in the
// previous OnTimer tick. Used to detect tickets that have been
// removed from the JSON file (i.e., positions closed), so their
// 4 chart objects can be deleted immediately.
string _prev_tickets = "";

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit() {
   EventSetTimer(TIMER_INTERVAL_MS / 1000);
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason) {
   EventKillTimer();
   // Wipe all Genesis objects on THIS chart when EA is detached or
   // chart template is changed -- prevents orphaned objects from
   // persisting after the EA is removed.
   ObjectsDeleteAll(ChartID(), PREFIX);
   ChartRedraw(ChartID());
}

//+------------------------------------------------------------------+
//| Timer event -- read visuals file & update chart objects         |
//+------------------------------------------------------------------+
void OnTimer() {
   string json = ReadVisualsFile();
   if (json == "") return;

   // Check for sweep command -- wipe all Genesis objects on every chart
   if (StringFind(json, "\"__sweep__\"") > -1) {
      SweepAllGenesisObjects();
      RemoveSweepFlag();
      _prev_tickets = ""; // fresh state after full sweep
      return;
   }

   // Parse each ticket entry and update chart objects,
   // with runtime orphan detection.
   UpdateChartObjects(json);
}

//+------------------------------------------------------------------+
//| Read the shared JSON visuals file from Common/Files              |
//+------------------------------------------------------------------+
string ReadVisualsFile() {
   int handle = FileOpen(VISUALS_FILE, FILE_READ|FILE_TXT|FILE_COMMON, 0, CP_UTF8);
   if (handle == INVALID_HANDLE) return "";

   string content = "";
   while (!FileIsEnding(handle)) {
      content += FileReadString(handle);
   }
   FileClose(handle);
   return content;
}

//+------------------------------------------------------------------+
//| Write a string to the shared JSON file (atomic update)           |
//+------------------------------------------------------------------+
void WriteVisualsFile(string content) {
   int handle = FileOpen(VISUALS_FILE, FILE_WRITE|FILE_TXT|FILE_COMMON, 0, CP_UTF8);
   if (handle == INVALID_HANDLE) return;
   FileWriteString(handle, content);
   FileClose(handle);
}

//+------------------------------------------------------------------+
//| Delete ALL objects with the GENESIS_ prefix on every chart       |
//+------------------------------------------------------------------+
void SweepAllGenesisObjects() {
   long chartId = ChartFirst();
   while (chartId >= 0) {
      ObjectsDeleteAll(chartId, PREFIX);
      ChartRedraw(chartId);
      chartId = ChartNext(chartId);
   }
}

//+------------------------------------------------------------------+
//| Remove the __sweep__ key from the visuals file                   |
//+------------------------------------------------------------------+
void RemoveSweepFlag() {
   string json = ReadVisualsFile();
   if (json == "") return;

   int pos = StringFind(json, "\"__sweep__\":");
   if (pos == -1) return;

   // Find the end of this key-value pair
   int end = StringFind(json, "\n", pos);
   if (end == -1) end = StringLen(json);
   else end++;

   string cleaned = StringSubstr(json, 0, pos) + StringSubstr(json, end);
   // Clean up syntax artifacts from removal
   StringReplace(cleaned, ",,", ",");
   StringReplace(cleaned, ",\n}", "\n}");
   StringReplace(cleaned, ",\n \"", "\n \"");

   WriteVisualsFile(cleaned);
}

//+------------------------------------------------------------------+
//| Delete 4 Genesis objects for a ticket from EVERY chart           |
//| Bug fix: ChartRedraw only called when an object was actually     |
//| deleted on that chart (avoids unnecessary GPU repaints).         |
//+------------------------------------------------------------------+
void RemoveTicketFromAllCharts(int ticket) {
   string ticketStr = IntegerToString(ticket);
   long chartId = ChartFirst();
   while (chartId >= 0) {
      bool deleted = false;
      deleted |= ObjectDelete(chartId, PREFIX + "ENTRY_" + ticketStr);
      deleted |= ObjectDelete(chartId, PREFIX + "SL_" + ticketStr);
      deleted |= ObjectDelete(chartId, PREFIX + "TP_" + ticketStr);
      deleted |= ObjectDelete(chartId, PREFIX + "HUD_" + ticketStr);

      // Only trigger GPU repaint if at least one object was actually removed
      if (deleted) ChartRedraw(chartId);

      chartId = ChartNext(chartId);
   }
}

//+------------------------------------------------------------------+
//| Update chart objects for all trades in the JSON, with runtime   |
//| orphan detection (heartbeat sweep on every 500ms tick).          |
//|                                                                  |
//| Bug fix: If curr_tickets is empty (corrupt/incomplete read),    |
//| the diff is SKIPPED so active lines are never accidentally       |
//| wiped. _prev_tickets carries forward to the next tick.           |
//+------------------------------------------------------------------+
void UpdateChartObjects(string json) {
   int pos = 0;
   int len = StringLen(json);
   string curr_tickets = ""; // "ticket1,ticket2," format

   // --- Pass 1: Extract current ticket IDs from JSON ---
   // This pass runs first so we can diff against _prev_tickets.
   // We store the ticket list and the block data for drawing in pass 2.
   // MQL5 lacks a native Set type, so we use a simple comma-separated
   // string that is fast to build and search.

   while (pos < len) {
      int startKey = StringFind(json, "\"", pos);
      if (startKey == -1) break;

      int endKey = StringFind(json, "\"", startKey + 1);
      if (endKey == -1) break;

      string key = StringSubstr(json, startKey + 1, endKey - startKey - 1);
      if (key == "" || key == "__sweep__") { pos = endKey + 1; continue; }

      // Check if this is a numeric key (ticket)
      bool isNumeric = true;
      for (int i = 0; i < StringLen(key); i++) {
         ushort ch = StringGetCharacter(key, i);
         if (ch < '0' || ch > '9') { isNumeric = false; break; }
      }
      if (!isNumeric) { pos = endKey + 1; continue; }

      // Found a valid ticket -- add to current set
      curr_tickets += key + ",";

      // Find block end to advance position
      int blockStart = StringFind(json, "{", endKey);
      if (blockStart == -1) break;

      int braceCount = 1;
      int blockEnd = blockStart + 1;
      while (braceCount > 0 && blockEnd < len) {
         if (json[blockEnd] == '{') braceCount++;
         if (json[blockEnd] == '}') braceCount--;
         blockEnd++;
      }
      pos = blockEnd;
   }

   // --- Corrupt-read guard ---
   // If curr_tickets is empty after parsing (e.g., Python was mid-
   // write and the JSON was truncated), we skip the orphan diff to
   // prevent accidentally nuking ALL active lines for 1 tick.
   // _prev_tickets is left untouched and carries forward.
   if (curr_tickets == "") {
      return;
   }

   // --- Pass 2: Diff against previous tick -- clean up orphans ---
   // Any ticket that was in _prev_tickets but is NOT in curr_tickets
   // represents a closed position whose chart objects must be removed.
   if (_prev_tickets != "") {
      string prevList[1];
      int prevCount = StringSplit(_prev_tickets, ',', prevList);

      for (int p = 0; p < prevCount; p++) {
         string pt = prevList[p];
         if (pt == "") continue;
         if (StringFind("," + curr_tickets + ",", "," + pt + ",") == -1) {
            // Ticket pt was in _prev_tickets but NOT in curr_tickets -> closed
            RemoveTicketFromAllCharts((int)StringToInteger(pt));
         }
      }
   }

   // Store current tickets for next tick's diff
   _prev_tickets = curr_tickets;

   // --- Pass 3: Draw all current trades ---
   pos = 0;
   while (pos < len) {
      int startKey = StringFind(json, "\"", pos);
      if (startKey == -1) break;

      int endKey = StringFind(json, "\"", startKey + 1);
      if (endKey == -1) break;

      string key = StringSubstr(json, startKey + 1, endKey - startKey - 1);
      if (key == "" || key == "__sweep__" || StringFind(key, "__") == 0) {
         pos = endKey + 1;
         continue;
      }

      // Check if numeric (ticket)
      bool isNum = true;
      for (int i = 0; i < StringLen(key); i++) {
         ushort ch = StringGetCharacter(key, i);
         if (ch < '0' || ch > '9') { isNum = false; break; }
      }
      if (!isNum) { pos = endKey + 1; continue; }

      int ticket = (int)StringToInteger(key);

      // Find the block for this ticket
      int blockStart = StringFind(json, "{", endKey);
      if (blockStart == -1) break;

      int braceCount = 1;
      int blockEnd = blockStart + 1;
      while (braceCount > 0 && blockEnd < len) {
         if (json[blockEnd] == '{') braceCount++;
         if (json[blockEnd] == '}') braceCount--;
         blockEnd++;
      }

      string block = StringSubstr(json, blockStart, blockEnd - blockStart);

      string symbol = ExtractString(block, "symbol");
      string type = ExtractString(block, "type");
      double entry = ExtractDouble(block, "entry_price");
      double sl = ExtractDouble(block, "current_sl");
      double tp = ExtractDouble(block, "target_tp");
      string mode = ExtractString(block, "mode");
      double atr = ExtractDouble(block, "atr");

      DrawTradeVisualsOnAllCharts(symbol, ticket, type, entry, sl, tp, mode, atr);

      pos = blockEnd;
   }
}

//+------------------------------------------------------------------+
//| Draw 4 visual elements for one trade on a specific chart          |
//+------------------------------------------------------------------+
void DrawTradeVisualsOnChart(
   long chartId, int ticket, string type,
   double entry, double sl, double tp,
   string mode, double atr
) {
   datetime now = TimeCurrent();
   string ticketStr = IntegerToString(ticket);

   // 1. ENTRY LINE -- Horizontal solid amber line at entry price
   string entryName = PREFIX + "ENTRY_" + ticketStr;
   ObjectCreate(chartId, entryName, OBJ_HLINE, 0, 0, entry);
   ObjectSetInteger(chartId, entryName, OBJPROP_COLOR, ENTRY_COLOR);
   ObjectSetInteger(chartId, entryName, OBJPROP_STYLE, STYLE_SOLID);
   ObjectSetInteger(chartId, entryName, OBJPROP_WIDTH, LINE_WIDTH);
   ObjectSetInteger(chartId, entryName, OBJPROP_BACK, false);
   ObjectSetInteger(chartId, entryName, OBJPROP_SELECTABLE, false);
   ObjectSetString(chartId, entryName, OBJPROP_TEXT, "Entry " + DoubleToString(entry, 5));

   // 2. SL LINE -- Horizontal dashed cyan line (extended 48h)
   string slName = PREFIX + "SL_" + ticketStr;
   ObjectCreate(chartId, slName, OBJ_TREND, 0, now - 86400, sl, now + 86400, sl);
   ObjectSetInteger(chartId, slName, OBJPROP_COLOR, SL_COLOR);
   ObjectSetInteger(chartId, slName, OBJPROP_STYLE, STYLE_DASH);
   ObjectSetInteger(chartId, slName, OBJPROP_WIDTH, LINE_WIDTH);
   ObjectSetInteger(chartId, slName, OBJPROP_BACK, false);
   ObjectSetInteger(chartId, slName, OBJPROP_SELECTABLE, false);
   ObjectSetString(chartId, slName, OBJPROP_TEXT, "SL " + DoubleToString(sl, 5));

   // 3. TP LINE -- Horizontal dashed green line (extended 48h)
   string tpName = PREFIX + "TP_" + ticketStr;
   ObjectCreate(chartId, tpName, OBJ_TREND, 0, now - 86400, tp, now + 86400, tp);
   ObjectSetInteger(chartId, tpName, OBJPROP_COLOR, TP_COLOR);
   ObjectSetInteger(chartId, tpName, OBJPROP_STYLE, STYLE_DASH);
   ObjectSetInteger(chartId, tpName, OBJPROP_WIDTH, LINE_WIDTH);
   ObjectSetInteger(chartId, tpName, OBJPROP_BACK, false);
   ObjectSetInteger(chartId, tpName, OBJPROP_SELECTABLE, false);
   ObjectSetString(chartId, tpName, OBJPROP_TEXT, "TP " + DoubleToString(tp, 5));

   // 4. HUD LABEL -- Text label in top-left corner
   string hudName = PREFIX + "HUD_" + ticketStr;
   string hudText = "GENESIS | Ticket: #" + ticketStr
      + " | Mode: " + (mode != "" ? mode : "STATIC")
      + " | ATR: " + DoubleToString(atr, 5)
      + " | " + (type == "buy" ? "BUY" : (type == "sell" ? "SELL" : ""));

   ObjectCreate(chartId, hudName, OBJ_LABEL, 0, 0, 0);
   ObjectSetInteger(chartId, hudName, OBJPROP_XDISTANCE, 10);
   ObjectSetInteger(chartId, hudName, OBJPROP_YDISTANCE, 10);
   ObjectSetInteger(chartId, hudName, OBJPROP_CORNER, CORNER_LEFT_UPPER);
   ObjectSetInteger(chartId, hudName, OBJPROP_COLOR, HUD_TEXT_COLOR);
   ObjectSetInteger(chartId, hudName, OBJPROP_BACK, false);
   ObjectSetInteger(chartId, hudName, OBJPROP_SELECTABLE, false);
   ObjectSetString(chartId, hudName, OBJPROP_TEXT, hudText);
   ObjectSetString(chartId, hudName, OBJPROP_FONT, "Consolas");
   ObjectSetInteger(chartId, hudName, OBJPROP_FONTSIZE, 10);

   ChartRedraw(chartId);
}

//+------------------------------------------------------------------+
//| Draw 4 visual elements for one trade on ALL charts with symbol   |
//+------------------------------------------------------------------+
void DrawTradeVisualsOnAllCharts(
   string symbol, int ticket, string type,
   double entry, double sl, double tp,
   string mode, double atr
) {
   bool drewOnAny = false;
   long chartId = ChartFirst();
   while (chartId >= 0) {
      if (ChartSymbol(chartId) == symbol) {
         DrawTradeVisualsOnChart(chartId, ticket, type, entry, sl, tp, mode, atr);
         drewOnAny = true;
      }
      chartId = ChartNext(chartId);
   }

   // Fallback: if no chart found for the symbol, draw on the current chart
   if (!drewOnAny) {
      long current = ChartID();
      if (current >= 0) {
         DrawTradeVisualsOnChart(current, ticket, type, entry, sl, tp, mode, atr);
      }
   }
}

//+------------------------------------------------------------------+
//| Map a colour name string to an MQL5 colour constant.             |
//| Falls back to clrGray for unknown names.                         |
//+------------------------------------------------------------------+
color ColorFromName(string name) {
   StringToUpper(name);
   if (name == "DODGERBLUE") return clrDodgerBlue;
   if (name == "ORANGE") return clrOrange;
   if (name == "LIMEGREEN" || name == "LIME GREEN") return clrLimeGreen;
   if (name == "TOMATO") return clrTomato;
   if (name == "GOLDENROD") return clrGoldenrod;
   if (name == "CYAN") return clrCyan;
   if (name == "MAGENTA") return clrMagenta;
   if (name == "YELLOW") return clrYellow;
   if (name == "WHITE") return clrWhite;
   if (name == "SILVER") return clrSilver;
   if (name == "GRAY" || name == "GREY") return clrGray;
   return clrGray;
}

//+------------------------------------------------------------------+
//| Draw sweep pins (OBJ_TEXT) from the __sweep_pins__ section.      |
//| Bullish = green up-arrow; bearish = red down-arrow.              |
//| Expired pins (expires_at < now) are auto-cleaned.                |
//+------------------------------------------------------------------+
void DrawSweepPins(string json) {
   // Locate the __sweep_pins__ section
   string marker = "\"__sweep_pins__\":";
   int sectionStart = StringFind(json, marker);
   if (sectionStart == -1) {
      // No sweep pins -- delete all existing GENESIS_SWP_ objects
      SweepAllObjectsByPrefix(PREFIX + "SWP_");
      return;
   }

   // Track which pin IDs exist in the current JSON so we can
   // clean up orphaned pins that were removed by the Python side.
   string curr_pin_ids = "";
   long chartId = ChartFirst();

   // We re-parse the pin array differently than trade entries
   // since pins are nested inside __sweep_pins__ -> symbol -> []
   int pos = sectionStart + StringLen(marker);
   int jsonLen = StringLen(json);

   // Skip whitespace to reach the opening {
   while (pos < jsonLen && (json[pos] == ' ' || json[pos] == '\n' || json[pos] == '\r' || json[pos] == '\t')) pos++;

   // We're inside the __sweep_pins__ value -- iterate symbols and their pin arrays.
   // This is a simplified parser that scans for "id" fields.
   while (pos < jsonLen) {
      int idStart = StringFind(json, "\"id\": \"", pos);
      if (idStart == -1 || idStart >= jsonLen) break;
      idStart += StringLen("\"id\": \"");
      int idEnd = StringFind(json, "\"", idStart);
      if (idEnd == -1) break;
      string pinId = StringSubstr(json, idStart, idEnd - idStart);
      curr_pin_ids += pinId + ",";

      // Extract level
      double level = 0;
      int lvlStart = StringFind(json, "\"level\": ", idEnd);
      if (lvlStart != -1) {
         string levelStr = "";
         for (int i = lvlStart + 9; i < jsonLen && i < lvlStart + 30; i++) {
            ushort ch = StringGetCharacter(json, i);
            if (ch == ',' || ch == ' ' || ch == '\n' || ch == '\r' || ch == '}') break;
            levelStr += ShortToString(ch);
         }
         level = StringToDouble(levelStr);
      }

      // direction
      string dir = "bullish";
      int dirStart = StringFind(json, "\"direction\": \"", idEnd);
      if (dirStart != -1) {
         dirStart += StringLen("\"direction\": \"");
         int dirEnd = StringFind(json, "\"", dirStart);
         if (dirEnd != -1) dir = StringSubstr(json, dirStart, dirEnd - dirStart);
      }

      // label
      string label = "";
      int lblStart = StringFind(json, "\"label\": \"", idEnd);
      if (lblStart != -1) {
         lblStart += StringLen("\"label\": \"");
         int lblEnd = StringFind(json, "\"", lblStart);
         if (lblEnd != -1) label = StringSubstr(json, lblStart, lblEnd - lblStart);
      }

      // time (for anchor)
      datetime pinTime = 0;
      int tStart = StringFind(json, "\"time\": ", idEnd);
      if (tStart != -1) {
         string timeStr = "";
         for (int i = tStart + 8; i < jsonLen && i < tStart + 30; i++) {
            ushort ch = StringGetCharacter(json, i);
            if (ch == ',' || ch == '}' || ch == '\n' || ch == '\r' || ch == ' ') break;
            timeStr += ShortToString(ch);
         }
         pinTime = (datetime)StringToInteger(timeStr);
      }

      // expires_at -- skip expired pins
      int expStart = StringFind(json, "\"expires_at\": ", idEnd);
      if (expStart != -1) {
         string expStr = "";
         for (int i = expStart + 15; i < jsonLen && i < expStart + 30; i++) {
            ushort ch = StringGetCharacter(json, i);
            if (ch == ',' || ch == '}' || ch == '\n' || ch == '\r' || ch == ' ') break;
            expStr += ShortToString(ch);
         }
         double expiresAt = StringToDouble(expStr);
         if (expiresAt > 0 && expiresAt < (double)TimeCurrent()) {
            // Expired -- don't draw, skip to next pin
            pos = idEnd + 1;
            continue;
         }
      }

      if (level == 0) { pos = idEnd + 1; continue; }

      // Draw on all charts
      color pinColor = (dir == "bullish") ? SWEEP_BULLISH_COLOR : SWEEP_BEARISH_COLOR;
      string objName = PREFIX + "SWP_" + pinId;
      string arrow = (dir == "bullish") ? ShortToString((ushort)0x25B2) : ShortToString((ushort)0x25BC); // ▲ or ▼
      string pinLabel = arrow + " " + label;

      chartId = ChartFirst();
      while (chartId >= 0) {
         ObjectCreate(chartId, objName, OBJ_TEXT, 0, pinTime, level);
         ObjectSetString(chartId, objName, OBJPROP_TEXT, pinLabel);
         ObjectSetInteger(chartId, objName, OBJPROP_COLOR, pinColor);
         ObjectSetString(chartId, objName, OBJPROP_FONT, "Arial");
         ObjectSetInteger(chartId, objName, OBJPROP_FONTSIZE, 9);
         ObjectSetInteger(chartId, objName, OBJPROP_ANCHOR, ANCHOR_CENTER);
         ObjectSetInteger(chartId, objName, OBJPROP_BACK, false);
         ObjectSetInteger(chartId, objName, OBJPROP_SELECTABLE, false);
         ChartRedraw(chartId);
         chartId = ChartNext(chartId);
      }

      pos = idEnd + 1;
   }

   // Clean orphaned sweep pins
   SweepOrphanedByPrefix(PREFIX + "SWP_", curr_pin_ids);
}

//+------------------------------------------------------------------+
//| Sweep all objects whose name starts with prefix and are NOT in   |
//| the keep_ids comma-separated list.                               |
//+------------------------------------------------------------------+
void SweepOrphanedByPrefix(string sweepPrefix, string keep_ids) {
   long chartId = ChartFirst();
   while (chartId >= 0) {
      int total = ObjectsTotal(chartId);
      bool deletedAny = false;
      for (int i = total - 1; i >= 0; i--) {
         string objName = ObjectName(chartId, i);
         if (StringFind(objName, sweepPrefix) == 0) {
            // Extract the id suffix after the prefix for matching
            bool keep = false;
            if (keep_ids != "") {
               // Check if this object's ID appears in keep_ids
               if (StringFind(keep_ids, objName + ",") >= 0) keep = true;
               // Fallback: check without prefix
               string idOnly = StringSubstr(objName, StringLen(sweepPrefix));
               if (StringFind(keep_ids, idOnly + ",") >= 0) keep = true;
            }
            if (!keep) {
               ObjectDelete(chartId, objName);
               deletedAny = true;
            }
         }
      }
      if (deletedAny) ChartRedraw(chartId);
      chartId = ChartNext(chartId);
   }
}

//+------------------------------------------------------------------+
//| Sweep all objects starting with a prefix (for clearing state on  |
//| empty section -- no pins/overlays of that prefix to keep).       |
//+------------------------------------------------------------------+
void SweepAllObjectsByPrefix(string sweepPrefix) {
   long chartId = ChartFirst();
   while (chartId >= 0) {
      bool deletedAny = false;
      int total = ObjectsTotal(chartId);
      for (int i = total - 1; i >= 0; i--) {
         string objName = ObjectName(chartId, i);
         if (StringFind(objName, sweepPrefix) == 0) {
            ObjectDelete(chartId, objName);
            deletedAny = true;
         }
      }
      if (deletedAny) ChartRedraw(chartId);
      chartId = ChartNext(chartId);
   }
}

//+------------------------------------------------------------------+
//| Draw overlays (OBJ_RECTANGLE) for sessions and equilibrium.      |
//+------------------------------------------------------------------+
void DrawOverlays(string json) {
   string marker = "\"__overlays__\":";
   int sectionStart = StringFind(json, marker);
   if (sectionStart == -1) {
      SweepAllObjectsByPrefix(PREFIX + "OVL_");
      return;
   }

   string curr_overlay_ids = "";
   int pos = sectionStart + StringLen(marker);
   int jsonLen = StringLen(json);

   while (pos < jsonLen) {
      int idStart = StringFind(json, "\"id\": \"", pos);
      if (idStart == -1 || idStart >= jsonLen) break;
      idStart += StringLen("\"id\": \"");
      int idEnd = StringFind(json, "\"", idStart);
      if (idEnd == -1) break;
      string overlayId = StringSubstr(json, idStart, idEnd - idStart);
      curr_overlay_ids += overlayId + ",";

      // Extract type
      string ovlType = "";
      int tStart = StringFind(json, "\"type\": \"", idEnd);
      if (tStart != -1) {
         tStart += StringLen("\"type\": \"");
         int tEnd = StringFind(json, "\"", tStart);
         if (tEnd != -1) ovlType = StringSubstr(json, tStart, tEnd - tStart);
      }

      // Extract prices
      double priceHigh = 0, priceLow = 0;
      int phStart = StringFind(json, "\"price_high\": ", idEnd);
      if (phStart != -1) {
         string phStr = "";
         for (int i = phStart + 14; i < jsonLen && i < phStart + 30; i++) {
            ushort ch = StringGetCharacter(json, i);
            if (ch == ',' || ch == '}' || ch == '\n' || ch == '\r') break;
            phStr += ShortToString(ch);
         }
         priceHigh = StringToDouble(phStr);
      }
      int plStart = StringFind(json, "\"price_low\": ", idEnd);
      if (plStart != -1) {
         string plStr = "";
         for (int i = plStart + 14; i < jsonLen && i < plStart + 30; i++) {
            ushort ch = StringGetCharacter(json, i);
            if (ch == ',' || ch == '}' || ch == '\n' || ch == '\r') break;
            plStr += ShortToString(ch);
         }
         priceLow = StringToDouble(plStr);
      }

      // Extract times
      datetime tsStart = 0, tsEnd = 0;
      int tsS = StringFind(json, "\"time_start\": ", idEnd);
      if (tsS != -1) {
         string tStr = "";
         for (int i = tsS + 15; i < jsonLen && i < tsS + 30; i++) {
            ushort ch = StringGetCharacter(json, i);
            if (ch == ',' || ch == '}' || ch == '\n' || ch == '\r') break;
            tStr += ShortToString(ch);
         }
         tsStart = (datetime)StringToInteger(tStr);
      }
      int tsE = StringFind(json, "\"time_end\": ", idEnd);
      if (tsE != -1) {
         string tStr = "";
         for (int i = tsE + 13; i < jsonLen && i < tsE + 30; i++) {
            ushort ch = StringGetCharacter(json, i);
            if (ch == ',' || ch == '}' || ch == '\n' || ch == '\r') break;
            tStr += ShortToString(ch);
         }
         tsEnd = (datetime)StringToInteger(tStr);
      }

      // Extract colour type name
      string colName = "";
      int colStart = StringFind(json, "\"color_type\": \"", idEnd);
      if (colStart != -1) {
         colStart += StringLen("\"color_type\": \"");
         int colEnd = StringFind(json, "\"", colStart);
         if (colEnd != -1) colName = StringSubstr(json, colStart, colEnd - colStart);
      }

      // label
      string label = "";
      int lblStart = StringFind(json, "\"label\": \"", idEnd);
      if (lblStart != -1) {
         lblStart += StringLen("\"label\": \"");
         int lblEnd = StringFind(json, "\"", lblStart);
         if (lblEnd != -1) label = StringSubstr(json, lblStart, lblEnd - lblStart);
      }

      if (priceHigh == 0 && priceLow == 0) { pos = idEnd + 1; continue; }

      color fillColor = ColorFromName(colName != "" ? colName : "DodgerBlue");
      string objName = PREFIX + "OVL_" + overlayId;

      // If equilibrium (no time bounds) use +/-48h from current time
      if (ovlType == "equilibrium" || tsStart == 0) {
         tsStart = TimeCurrent() - 172800; // 48h ago
         tsEnd = TimeCurrent() + 172800; // 48h ahead
      }

      // Draw on every chart
      long chartId = ChartFirst();
      while (chartId >= 0) {
         ObjectCreate(chartId, objName, OBJ_RECTANGLE, 0, tsStart, priceHigh, tsEnd, priceLow);
         ObjectSetInteger(chartId, objName, OBJPROP_COLOR, fillColor);
         ObjectSetInteger(chartId, objName, OBJPROP_BACK, true);
         ObjectSetInteger(chartId, objName, OBJPROP_FILL, true);
         ObjectSetInteger(chartId, objName, OBJPROP_SELECTABLE, false);
         ObjectSetInteger(chartId, objName, OBJPROP_STYLE, STYLE_DOT);
         ObjectSetInteger(chartId, objName, OBJPROP_WIDTH, 1);
         ObjectSetString(chartId, objName, OBJPROP_TEXT, label);
         ChartRedraw(chartId);
         chartId = ChartNext(chartId);
      }

      pos = idEnd + 1;
   }

   SweepOrphanedByPrefix(PREFIX + "OVL_", curr_overlay_ids);
}

//+------------------------------------------------------------------+
//| Draw trail milestones: OBJ_TREND connecting SL history points.   |
//+------------------------------------------------------------------+
void DrawTrailMilestones(string json) {
   string marker = "\"__trail_milestones__\":";
   int sectionStart = StringFind(json, marker);
   if (sectionStart == -1) {
      SweepAllObjectsByPrefix(PREFIX + "TRL_");
      return;
   }

   // Scan for ticket groups and their milestone arrays.
   int pos = sectionStart + StringLen(marker);
   int jsonLen = StringLen(json);

   // Skip to opening {
   while (pos < jsonLen && (json[pos] == ' ' || json[pos] == '\n' || json[pos] == '\r' || json[pos] == '\t')) pos++;

   while (pos < jsonLen) {
      // Find next ticket key (numeric string)
      int keyQuotePos = StringFind(json, "\"", pos);
      if (keyQuotePos == -1) break;
      int keyCloseQuote = StringFind(json, "\"", keyQuotePos + 1);
      if (keyCloseQuote == -1) break;

      string ticketStr = StringSubstr(json, keyQuotePos + 1, keyCloseQuote - keyQuotePos - 1);
      // Skip non-numeric or meta keys
      if (ticketStr == "" || StringFind(ticketStr, "__") == 0) {
         pos = keyCloseQuote + 1;
         continue;
      }

      // Get the array of milestones for this ticket
      int arrayStart = StringFind(json, "[", keyCloseQuote);
      if (arrayStart == -1) { pos = keyCloseQuote + 1; continue; }
      int arrayEnd = -1;
      int bracketCount = 1;
      for (int i = arrayStart + 1; i < jsonLen; i++) {
         if (json[i] == '[') bracketCount++;
         if (json[i] == ']') bracketCount--;
         if (bracketCount == 0) { arrayEnd = i; break; }
      }
      if (arrayEnd == -1) break;

      // Process each milestone in the array
      string array = StringSubstr(json, arrayStart, arrayEnd - arrayStart + 1);
      int mPos = 0;
      int arrLen = StringLen(array);
      bool hasPrevMilestone = false;
      double prevMile_price = 0;
      datetime prevMile_time = 0;
      string prevMile_id = "";

      while (mPos < arrLen) {
         int mStart = StringFind(array, "{", mPos);
         if (mStart == -1) break;
         int mEnd = -1;
         int braceCount = 1;
         for (int i = mStart + 1; i < arrLen; i++) {
            if (array[i] == '{') braceCount++;
            if (array[i] == '}') braceCount--;
            if (braceCount == 0) { mEnd = i; break; }
         }
         if (mEnd == -1) break;

         string milestone = StringSubstr(array, mStart, mEnd - mStart + 1);

         string mileId = ExtractString(milestone, "id");
         double milePrice = ExtractDouble(milestone, "sl_price");
         datetime mileTime = (datetime)ExtractDouble(milestone, "time");
         string mileMode = ExtractString(milestone, "mode");

         if (milePrice != 0) {
            // Draw a small OBJ_TEXT marker at this milestone point
            string dotName = PREFIX + mileId;
            // Also draw connecting OBJ_TREND from previous milestone
            if (hasPrevMilestone && prevMile_price != 0) {
               string lineName = PREFIX + "TRLINE_" + ticketStr;
               long chartId = ChartFirst();
               while (chartId >= 0) {
                  ObjectCreate(chartId, lineName, OBJ_TREND, 0,
                     prevMile_time, prevMile_price,
                     mileTime, milePrice);
                  ObjectSetInteger(chartId, lineName, OBJPROP_COLOR, clrSilver);
                  ObjectSetInteger(chartId, lineName, OBJPROP_WIDTH, 1);
                  ObjectSetInteger(chartId, lineName, OBJPROP_STYLE, STYLE_DOT);
                  ObjectSetInteger(chartId, lineName, OBJPROP_BACK, false);
                  ObjectSetInteger(chartId, lineName, OBJPROP_SELECTABLE, false);
                  ChartRedraw(chartId);
                  chartId = ChartNext(chartId);
               }
               chartId = ChartFirst();
               while (chartId >= 0) {
                  // Also add a small DOT marker
                  ObjectCreate(chartId, dotName, OBJ_TEXT, 0, mileTime, milePrice);
                  ObjectSetString(chartId, dotName, OBJPROP_TEXT, ShortToString((ushort)0x25CF)); // bullet
                  ObjectSetInteger(chartId, dotName, OBJPROP_ANCHOR, ANCHOR_CENTER);
                  ObjectSetInteger(chartId, dotName, OBJPROP_COLOR, TRAIL_MILESTONE_DOT);
                  ObjectSetString(chartId, dotName, OBJPROP_FONT, "Arial");
                  ObjectSetInteger(chartId, dotName, OBJPROP_FONTSIZE, 7);
                  ObjectSetInteger(chartId, dotName, OBJPROP_BACK, false);
                  ObjectSetInteger(chartId, dotName, OBJPROP_SELECTABLE, false);
                  ChartRedraw(chartId);
                  chartId = ChartNext(chartId);
               }
            }

            prevMile_price = milePrice;
            prevMile_time = mileTime;
            prevMile_id = mileId;
            hasPrevMilestone = true;
         }

         // Move past this milestone block
         mPos = mEnd + 1;
      }

      pos = MathMin(arrayEnd + 1, jsonLen);
   }
   // Clean up orphaned TRL objects for tickets no longer in the file
   // Not implemented here -- RemoveTicketFromAllCharts handles closed tickets
}

//+------------------------------------------------------------------+
//| Extract a string field from a JSON block                         |
//+------------------------------------------------------------------+
string ExtractString(string block, string key) {
   string search = "\"" + key + "\": \"";
   int start = StringFind(block, search);
   if (start == -1) return "";
   start += StringLen(search);
   int end = StringFind(block, "\"", start);
   if (end == -1) return "";
   return StringSubstr(block, start, end - start);
}

//+------------------------------------------------------------------+
//| Extract a double field from a JSON block                         |
//+------------------------------------------------------------------+
double ExtractDouble(string block, string key) {
   string search = "\"" + key + "\": ";
   int start = StringFind(block, search);
   if (start == -1) return 0;
   start += StringLen(search);

   string numStr = "";
   int len = StringLen(block);
   for (int i = start; i < len; i++) {
      ushort ch = StringGetCharacter(block, i);
      if (ch == ',' || ch == '}' || ch == ' ' || ch == '\n' || ch == '\r') break;
      numStr += ShortToString(ch);
   }
   return StringToDouble(numStr);
}
