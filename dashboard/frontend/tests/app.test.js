/**
 * Genesis Dashboard App Test Suite
 * ================================
 * Tests the app.js logic (lot conversion, stake execution, dropdown toggle,
 * modal management, engine state toggle, position modal, and click-away
 * listener) using jsdom.
 *
 * The app.js source is loaded directly — no HTML extraction needed since
 * the JS lives in an external file (app.js), not inline in index.html.
 *
 * Run:  npm test
 * Watch: npm run test:watch
 */

const { describe, it, before, beforeEach, afterEach } = require("node:test");
const assert = require("node:assert/strict");
const { JSDOM } = require("jsdom");
const fs = require("fs");
const path = require("path");

const APP_JS_PATH = path.resolve(__dirname, "..", "app.js");

// Minimal DOM fixture matching the elements referenced by app.js.
// Note: the "Notional Controlled" preview was intentionally removed.
const HTML = `<!DOCTYPE html>
<html><body>
  <!-- 3-Dot Utility Dropdown -->
  <div id="more-tools-wrapper">
    <button id="tools-btn">⋮</button>
    <div id="tools-dropdown" class="hidden">Dropdown Content</div>
  </div>

  <!-- Stake Panel -->
  <select id="stake-symbol">
    <option value="EURUSD">EURUSD</option>
    <option value="USDJPY">USDJPY</option>
    <option value="GBPUSD">GBPUSD</option>
  </select>
  <input type="number" id="stake-amount-usd" value="20" />
  <span id="calculated-lots-preview">0.02 Lots</span>

  <!-- Header controls -->
  <button id="engine-toggle-btn" class="engine-btn paused"><span id="engine-btn-text">START ENGINE</span></button>
  <span id="active-profile-badge">PROFILE —</span>

  <!-- Discord Status Badge -->
  <span id="discord-status-badge" class="status-badge discord-disconnected">
    <span class="discord-icon">💬</span>
    <span id="discord-status-text">DISCORD: OFF</span>
  </span>
  <div id="discord-modal" class="hidden">
    <input type="password" id="discord-webhook-url" />
    <input type="checkbox" id="notify-trades" checked />
    <input type="checkbox" id="notify-alerts" checked />
  </div>

  <!-- Metrics -->
  <span id="metric-balance"></span>
  <span id="metric-equity"></span>
  <span id="metric-daily-pnl"></span>
  <span id="metric-win-rate"></span>

  <!-- Charts -->
  <canvas id="mainProfitChart"></canvas>
  <canvas id="modalProfitChart"></canvas>

  <!-- Tables -->
  <span id="active-count">0 OPEN POSITIONS</span>
  <tbody id="active-positions-body"></tbody>
  <tbody id="recent-history-body"></tbody>

  <!-- Modals -->
  <div id="chart-modal" class="hidden">Chart Modal</div>
  <div id="history-modal" class="hidden">
    <div id="history-metrics"></div>
    <tbody id="history-table-body"></tbody>
  </div>
  <div id="position-modal" class="hidden">
    <span id="pos-modal-ticket">#</span>
    <div id="pos-modal-details"></div>
  </div>
  <div id="utility-modal" class="hidden">
    <h2 id="utility-modal-title">Tool Output</h2>
    <div id="utility-modal-body"></div>
  </div>
</body></html>`;

describe("Genesis Dashboard App", () => {
  /** @type {JSDOM} */
  let dom;
  /** @type {Document} */
  let document;
  /** @type {Window} */
  let window;
  /** @type {string[]} */
  let logCalls;
  /** @type {string[]} */
  let alertCalls;

  // ── Lifecycle ──────────────────────────────────────────────────────

  before(() => {
    const appCode = fs.readFileSync(APP_JS_PATH, "utf8");

    // Strip the conditional module.exports section so eval doesn't fail
    // in the jsdom (Node.js) context — the browser code runs as-is, then
    // we alias the const/function declarations onto `window` for testing.
    const cleanCode = appCode.replace(
      /\n\/\/ ── Conditional exports[\s\S]*$/,
      ""
    );

    dom = new JSDOM(HTML, {
      url: "http://localhost:8000",
      runScripts: "outside-only",
    });
    document = dom.window.document;
    window = dom.window;

    // Spy on console.log and alert
    logCalls = [];
    alertCalls = [];
    window.console.log = (...args) => logCalls.push(args.join(" "));
    window.alert = (...args) => alertCalls.push(args.join(" "));

    // localStorage shim for jsdom
    let store = {};
    window.localStorage = {
      getItem: (k) => (k in store ? store[k] : null),
      setItem: (k, v) => { store[k] = String(v); },
      removeItem: (k) => { delete store[k]; },
      clear: () => { store = {}; },
    };

    const bootstrap =
      cleanCode +
      "; window.LEVERAGE = LEVERAGE;" +
      "window.updateLotConversionPreview = updateLotConversionPreview;" +
      "window.executeStakeTrade = executeStakeTrade;" +
      "window.toggleToolsMenu = toggleToolsMenu;" +
      "window.openChartModal = openChartModal;" +
      "window.closeChartModal = closeChartModal;" +
      "window.openModal = openModal;" +
      "window.closeUtilityModal = closeUtilityModal;" +
      "window.closeModal = closeModal;" +
      "window.checkDiscordConnection = checkDiscordConnection;" +
      "window.updateDiscordUI = updateDiscordUI;" +
      "window.saveDiscordSettings = saveDiscordSettings;" +
      "window.testDiscordWebhook = testDiscordWebhook;" +
      "window.setEngineState = setEngineState;" +
      "window.toggleEngineState = toggleEngineState;" +
      "window.switchProfile = switchProfile;" +
      "window.initCharts = initCharts;" +
      "window.openHistoryModal = openHistoryModal;" +
      "window.closeHistoryModal = closeHistoryModal;" +
      "window.renderActivePositions = renderActivePositions;" +
      "window.openPositionModal = openPositionModal;" +
      "window.closePositionModal = closePositionModal;" +
      "window.closePosition = closePosition;" +
      "window.updatePositionStops = updatePositionStops;" +
      "window.refreshStatus = refreshStatus;" +
      "window.renderRecentHistory = renderRecentHistory;" +
      "window.fmtMoney = fmtMoney;" +
      "window.fmtPnl = fmtPnl;" +
      "window.startPositionChart = startPositionChart;" +
      "window.stopPositionChart = stopPositionChart;" +
      "window.setPosChartTF = setPosChartTF;" +
      "window.refreshPositionChart = refreshPositionChart;" +
      "window.patchPositionChartTick = patchPositionChartTick;" +
      "window.loadCandles = loadCandles;" +
      "window.TRADING_PROFILES = TRADING_PROFILES;" +
      "window.selectProfile = selectProfile;" +
      "window.populateSymbolDropdown = populateSymbolDropdown;" +
      "window.sendDiscordTradeNotification = sendDiscordTradeNotification;" +
      "window.getActiveProfile = function() { return activeProfile; };";

    window.eval(bootstrap);

    // Sanity checks
    assert.equal(typeof window.updateLotConversionPreview, "function");
    assert.equal(typeof window.executeStakeTrade, "function");
    assert.equal(typeof window.toggleToolsMenu, "function");
    assert.equal(typeof window.openModal, "function");
    assert.equal(typeof window.setEngineState, "function");
    assert.equal(typeof window.openHistoryModal, "function");
    assert.equal(typeof window.openPositionModal, "function");
    assert.equal(window.LEVERAGE, 100);
  });

  beforeEach(() => {
    logCalls.length = 0;
    alertCalls.length = 0;
    // Reset stake amount to default and refresh lot preview
    document.getElementById("stake-amount-usd").value = "20";
    window.updateLotConversionPreview();
    // Reset all modals to hidden
    ["chart-modal", "history-modal", "position-modal", "utility-modal", "discord-modal"].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.classList.add("hidden");
    });
    // Reset dropdown to hidden
    document.getElementById("tools-dropdown").classList.add("hidden");
    // Default engine to paused
    window.setEngineState(false);
    // Clear localStorage
    window.localStorage.clear();
    // Reset Discord badge to disconnected state
    const badge = document.getElementById("discord-status-badge");
    if (badge) badge.className = "status-badge discord-disconnected";
    const text = document.getElementById("discord-status-text");
    if (text) text.textContent = "DISCORD: OFF";
    // Clear webhook input
    const wh = document.getElementById("discord-webhook-url");
    if (wh) wh.value = "";
    // Reset stake-symbol to default options (re-create if a previous test removed it)
    let sel = document.getElementById("stake-symbol");
    if (!sel) {
      sel = document.createElement("select");
      sel.id = "stake-symbol";
      document.body.appendChild(sel);
    }
    sel.innerHTML = '<option value="EURUSD">EURUSD</option><option value="USDJPY">USDJPY</option><option value="GBPUSD">GBPUSD</option>';
    // Reset activeProfile back to swing_trader (the default)
    window.selectProfile("swing_trader");
    // Remove any fetch stub left by engine-toggle tests so later tests
    // (history modal) observe the jsdom default (no fetch).
    window.fetch = undefined;
  });

  // ── LEVERAGE Constant ──────────────────────────────────────────────

  it("LEVERAGE is set to 100", () => {
    assert.equal(window.LEVERAGE, 100);
  });

  // ── updateLotConversionPreview ─────────────────────────────────────

  describe("updateLotConversionPreview", () => {
    it("converts $20 stake to 0.02 Lots", () => {
      document.getElementById("stake-amount-usd").value = "20";
      window.updateLotConversionPreview();
      assert.equal(
        document.getElementById("calculated-lots-preview").innerText,
        "0.02 Lots"
      );
    });

    it("converts $10 stake to 0.01 Lots", () => {
      document.getElementById("stake-amount-usd").value = "10";
      window.updateLotConversionPreview();
      assert.equal(
        document.getElementById("calculated-lots-preview").innerText,
        "0.01 Lots"
      );
    });

    it("converts $100 stake to 0.10 Lots", () => {
      document.getElementById("stake-amount-usd").value = "100";
      window.updateLotConversionPreview();
      assert.equal(
        document.getElementById("calculated-lots-preview").innerText,
        "0.10 Lots"
      );
    });

    it("handles empty input as $0 (0.00 Lots)", () => {
      document.getElementById("stake-amount-usd").value = "";
      window.updateLotConversionPreview();
      assert.equal(
        document.getElementById("calculated-lots-preview").innerText,
        "0.00 Lots"
      );
    });

    it("handles non-numeric input as $0", () => {
      document.getElementById("stake-amount-usd").value = "abc";
      window.updateLotConversionPreview();
      assert.equal(
        document.getElementById("calculated-lots-preview").innerText,
        "0.00 Lots"
      );
    });

    it("converts large stakes (e.g. $2500 → 2.50 Lots)", () => {
      document.getElementById("stake-amount-usd").value = "2500";
      window.updateLotConversionPreview();
      assert.equal(
        document.getElementById("calculated-lots-preview").innerText,
        "2.50 Lots"
      );
    });
  });

  // ── executeStakeTrade ──────────────────────────────────────────────

  describe("executeStakeTrade", () => {
    it("logs BUY order with correct symbol, stake, and lots", () => {
      window.executeStakeTrade("BUY");
      assert.ok(
        logCalls.some((c) =>
          c.includes("[ORDER EXECUTION] BUY | Symbol: EURUSD | Stake: $20 (0.02 Lots)")
        ),
        `Expected BUY log call, got: ${JSON.stringify(logCalls)}`
      );
    });

    it("shows alert for BUY with correct parameters", () => {
      window.executeStakeTrade("BUY");
      assert.ok(
        alertCalls.some((c) => c.includes("BUY") && c.includes("EURUSD") && c.includes("$20")),
        `Expected BUY alert, got: ${JSON.stringify(alertCalls)}`
      );
    });

    it("logs SELL order with correct symbol, stake, and lots", () => {
      window.executeStakeTrade("SELL");
      assert.ok(
        logCalls.some((c) =>
          c.includes("[ORDER EXECUTION] SELL | Symbol: EURUSD | Stake: $20 (0.02 Lots)")
        ),
        `Expected SELL log call, got: ${JSON.stringify(logCalls)}`
      );
    });

    it("shows alert for SELL with correct parameters", () => {
      window.executeStakeTrade("SELL");
      assert.ok(
        alertCalls.some((c) => c.includes("SELL") && c.includes("EURUSD") && c.includes("$20")),
        `Expected SELL alert, got: ${JSON.stringify(alertCalls)}`
      );
    });
  });

  // ── toggleToolsMenu ────────────────────────────────────────────────

  describe("toggleToolsMenu", () => {
    it("removes hidden class when dropdown is hidden", () => {
      const dropdown = document.getElementById("tools-dropdown");
      dropdown.classList.add("hidden");
      assert.ok(dropdown.classList.contains("hidden"));
      window.toggleToolsMenu();
      assert.ok(!dropdown.classList.contains("hidden"));
    });

    it("toggles hidden class on repeated calls", () => {
      const dropdown = document.getElementById("tools-dropdown");
      dropdown.classList.add("hidden");

      window.toggleToolsMenu(); // show
      assert.ok(!dropdown.classList.contains("hidden"));

      window.toggleToolsMenu(); // hide
      assert.ok(dropdown.classList.contains("hidden"));

      window.toggleToolsMenu(); // show again
      assert.ok(!dropdown.classList.contains("hidden"));
    });
  });

  // ── Click-away listener ────────────────────────────────────────────

  describe("click-away listener", () => {
    it("hides dropdown when clicking outside the wrapper", () => {
      const dropdown = document.getElementById("tools-dropdown");
      dropdown.classList.remove("hidden");
      assert.ok(!dropdown.classList.contains("hidden"));

      const outside = document.createElement("div");
      document.body.appendChild(outside);
      outside.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));

      assert.ok(dropdown.classList.contains("hidden"));
      document.body.removeChild(outside);
    });

    it("keeps dropdown visible when clicking inside the wrapper", () => {
      const dropdown = document.getElementById("tools-dropdown");
      dropdown.classList.remove("hidden");
      assert.ok(!dropdown.classList.contains("hidden"));

      const wrapper = document.getElementById("more-tools-wrapper");
      wrapper.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));

      assert.ok(!dropdown.classList.contains("hidden"));
    });
  });

  // ── Modal Management ───────────────────────────────────────────────

  describe("openChartModal / closeChartModal", () => {
    it("openChartModal removes hidden class", () => {
      const modal = document.getElementById("chart-modal");
      modal.classList.add("hidden");
      window.openChartModal();
      assert.ok(!modal.classList.contains("hidden"));
    });

    it("closeChartModal adds hidden class", () => {
      window.openChartModal();
      const modal = document.getElementById("chart-modal");
      assert.ok(!modal.classList.contains("hidden"));
      window.closeChartModal();
      assert.ok(modal.classList.contains("hidden"));
    });
  });

  describe("openModal", () => {
    it("shows utility modal with bot-logs content", () => {
      window.openModal("bot-logs");
      const modal = document.getElementById("utility-modal");
      assert.ok(!modal.classList.contains("hidden"));
      assert.equal(
        document.getElementById("utility-modal-title").innerText,
        "📄 Bot Log Output"
      );
      assert.ok(
        document.getElementById("utility-modal-body").innerHTML.includes(
          "WS Connected"
        )
      );
    });

    it("shows utility modal with calendar content", () => {
      window.openModal("calendar");
      assert.ok(
        !document.getElementById("utility-modal").classList.contains("hidden")
      );
      assert.equal(
        document.getElementById("utility-modal-title").innerText,
        "📅 Economic Calendar (UTC)"
      );
      assert.ok(
        document.getElementById("utility-modal-body").innerHTML.includes(
          "Fed Rate Statement"
        )
      );
    });

    it("shows utility modal with parameters content", () => {
      window.openModal("parameters");
      assert.ok(
        !document.getElementById("utility-modal").classList.contains("hidden")
      );
      assert.equal(
        document.getElementById("utility-modal-title").innerText,
        "⚙️ Strategy Parameters"
      );
      assert.ok(
        document.getElementById("utility-modal-body").textContent.includes(
          "Parameter configurations active"
        )
      );
    });

    it("hides the tools dropdown when opening a modal", () => {
      const dropdown = document.getElementById("tools-dropdown");
      dropdown.classList.remove("hidden");
      window.openModal("bot-logs");
      assert.ok(dropdown.classList.contains("hidden"));
    });
  });

  describe("closeUtilityModal", () => {
    it("hides the utility modal", () => {
      window.openModal("bot-logs");
      assert.ok(
        !document.getElementById("utility-modal").classList.contains("hidden")
      );
      window.closeUtilityModal();
      assert.ok(
        document.getElementById("utility-modal").classList.contains("hidden")
      );
    });
  });

  // ── Engine State Toggle ────────────────────────────────────────────

  describe("setEngineState / toggleEngineState", () => {
    it("setEngineState(false) applies paused styling and START label", () => {
      window.setEngineState(false);
      const btn = document.getElementById("engine-toggle-btn");
      assert.ok(btn.classList.contains("paused"));
      assert.ok(!btn.classList.contains("running"));
      assert.equal(
        document.getElementById("engine-btn-text").textContent,
        "START ENGINE"
      );
    });

    it("setEngineState(true) applies running styling and PAUSE label", () => {
      window.setEngineState(true);
      const btn = document.getElementById("engine-toggle-btn");
      assert.ok(btn.classList.contains("running"));
      assert.ok(!btn.classList.contains("paused"));
      assert.equal(
        document.getElementById("engine-btn-text").textContent,
        "PAUSE ENGINE"
      );
    });

    it("toggleEngineState sends resume when paused and flips to running", async () => {
      window.setEngineState(false);
      let captured = null;
      window.fetch = async (url, opts) => {
        captured = { url, opts };
        return { ok: true, json: async () => ({ status: "success", message: "Bot resumed" }) };
      };
      await window.toggleEngineState();
      assert.ok(captured, "fetch should have been called");
      assert.equal(captured.url, "/api/control");
      assert.equal(JSON.parse(captured.opts.body).action, "resume");
      assert.ok(document.getElementById("engine-toggle-btn").classList.contains("running"));
    });

    it("toggleEngineState sends pause when running", async () => {
      window.setEngineState(true);
      let captured = null;
      window.fetch = async (url, opts) => {
        captured = { url, opts };
        return { ok: true, json: async () => ({ status: "success", message: "Bot paused" }) };
      };
      await window.toggleEngineState();
      assert.ok(captured, "fetch should have been called");
      assert.equal(JSON.parse(captured.opts.body).action, "pause");
      assert.ok(document.getElementById("engine-toggle-btn").classList.contains("paused"));
    });
  });

  // ── History Modal ──────────────────────────────────────────────────

  describe("openHistoryModal / closeHistoryModal", () => {
    it("opens the history modal", () => {
      window.openHistoryModal();
      assert.ok(
        !document.getElementById("history-modal").classList.contains("hidden")
      );
    });

    it("closes the history modal", () => {
      window.openHistoryModal();
      window.closeHistoryModal();
      assert.ok(
        document.getElementById("history-modal").classList.contains("hidden")
      );
    });
  });

  // ── Position Details Modal ─────────────────────────────────────────

  describe("Mini candlestick chart in position modal", () => {
    it("renders the live mini chart canvas + timeframe buttons", () => {
      window.renderActivePositions([
        { ticket: 123, symbol: "EURUSD", direction: "buy", volume: 0.05, entry_price: 1.08000, live_price: 1.09000, profit: 50.0, sl: 1.07000, tp: 1.10000, magic: 202406 },
      ]);
      window.openPositionModal(123);
      const body = document.getElementById("pos-modal-details").innerHTML;
      assert.ok(body.includes("posMiniChart"), "canvas should exist in modal body");
      assert.ok(body.includes("pos-tf-group"), "timeframe selector should exist");
      assert.ok(body.includes("M1") && body.includes("M15"));
    });

    it("setPosChartTF toggles the active timeframe button", () => {
      window.renderActivePositions([
        { ticket: 123, symbol: "EURUSD", direction: "buy", volume: 0.05, entry_price: 1.08000, live_price: 1.09000, profit: 50.0 },
      ]);
      window.openPositionModal(123);
      window.setPosChartTF("M5");
      const btns = document.querySelectorAll(".pos-tf");
      assert.equal(btns.length, 3);
      for (const b of btns) {
        assert.equal(b.classList.contains("active"), b.getAttribute("data-tf") === "M5");
      }
    });

    it("closePositionModal is safe without a chart instance", () => {
      window.openPositionModal(999); // not found — no chart started
      assert.doesNotThrow(() => window.closePositionModal());
      assert.ok(document.getElementById("position-modal").classList.contains("hidden"));
    });

    it("loadCandles returns candles from the API and [] on failure", async () => {
      window.fetch = async () => ({
        ok: true,
        json: async () => ({
          symbol: "EURUSD",
          timeframe: "M1",
          candles: [
            { time: 1700000000, open: 1.08, high: 1.085, low: 1.079, close: 1.084 },
          ],
        }),
      });
      const res = await window.loadCandles("EURUSD", "M1", 60);
      assert.equal(res.candles.length, 1);
      assert.equal(res.candles[0].close, 1.084);

      window.fetch = async () => { throw new Error("offline"); };
      const fail = await window.loadCandles("EURUSD", "M1", 60);
      // Compare length, not deepEqual: arrays created inside the jsdom realm
      // have a different Array prototype than the Node test realm.
      assert.equal(fail.candles.length, 0);
    });
  });

  describe("openPositionModal / closePositionModal", () => {
    it("renders position details from live snapshot", () => {
      window.renderActivePositions([
        { ticket: 123, symbol: "EURUSD", direction: "buy", volume: 0.05, entry_price: 1.08000, live_price: 1.09000, profit: 50.0, sl: 1.07000, tp: 1.10000, magic: 202406 },
      ]);
      window.openPositionModal(123);
      assert.ok(
        !document.getElementById("position-modal").classList.contains("hidden")
      );
      assert.equal(document.getElementById("pos-modal-ticket").textContent, "#123");
      assert.ok(
        document.getElementById("pos-modal-details").innerHTML.includes("EURUSD")
      );
    });

    it("shows a friendly message when the ticket is not in the snapshot", () => {
      window.renderActivePositions([]);
      window.openPositionModal(999);
      assert.ok(
        !document.getElementById("position-modal").classList.contains("hidden")
      );
      assert.ok(
        document.getElementById("pos-modal-details").innerHTML.includes("not found")
      );
    });

    it("closes the position modal", () => {
      window.openPositionModal(123);
      window.closePositionModal();
      assert.ok(
        document.getElementById("position-modal").classList.contains("hidden")
      );
    });
  });

  // ── Formatting helpers ─────────────────────────────────────────────

  describe("fmtMoney / fmtPnl", () => {
    it("formats money with two decimals", () => {
      assert.equal(window.fmtMoney(96.5), "$96.50");
      assert.equal(window.fmtMoney(0), "$0.00");
    });

    it("formats pnl with sign", () => {
      assert.equal(window.fmtPnl(94.74), "+$94.74");
      assert.equal(window.fmtPnl(-13.45), "-$13.45");
    });
  });

  // ── Discord Integration ─────────────────────────────────────────────

  describe("closeModal", () => {
    it("hides a modal by ID", () => {
      const modal = document.getElementById("discord-modal");
      modal.classList.remove("hidden");
      assert.ok(!modal.classList.contains("hidden"));
      window.closeModal("discord-modal");
      assert.ok(modal.classList.contains("hidden"));
    });

    it("hides any modal by ID (utility-modal)", () => {
      window.openModal("bot-logs");
      assert.ok(!document.getElementById("utility-modal").classList.contains("hidden"));
      window.closeModal("utility-modal");
      assert.ok(document.getElementById("utility-modal").classList.contains("hidden"));
    });
  });

  describe("openModal('discord-modal')", () => {
    it("shows the discord modal and hides the tools dropdown", () => {
      const dropdown = document.getElementById("tools-dropdown");
      dropdown.classList.remove("hidden");
      window.openModal("discord-modal");
      assert.ok(!document.getElementById("discord-modal").classList.contains("hidden"));
      assert.ok(dropdown.classList.contains("hidden"));
    });
  });

  describe("updateDiscordUI", () => {
    it("sets connected state when true", () => {
      window.updateDiscordUI(true);
      const badge = document.getElementById("discord-status-badge");
      assert.ok(badge.classList.contains("discord-connected"));
      assert.ok(!badge.classList.contains("discord-disconnected"));
      assert.equal(document.getElementById("discord-status-text").textContent, "DISCORD: LIVE");
    });

    it("sets disconnected state when false", () => {
      window.updateDiscordUI(true); // first connect
      window.updateDiscordUI(false);
      const badge = document.getElementById("discord-status-badge");
      assert.ok(badge.classList.contains("discord-disconnected"));
      assert.ok(!badge.classList.contains("discord-connected"));
      assert.equal(document.getElementById("discord-status-text").textContent, "DISCORD: OFF");
    });

    it("is a no-op when badge/text elements are missing", () => {
      const badge = document.getElementById("discord-status-badge");
      const parent = badge.parentNode;
      const saved = badge.cloneNode(true);
      parent.removeChild(badge);
      try {
        assert.doesNotThrow(() => window.updateDiscordUI(true));
      } finally {
        // Restore so subsequent tests still see the badge
        parent.appendChild(saved);
      }
    });
  });

  describe("saveDiscordSettings", () => {
    it("alerts when webhook URL is empty", () => {
      const wh = document.getElementById("discord-webhook-url");
      wh.value = "";
      window.saveDiscordSettings();
      assert.ok(alertCalls.some((c) => c.includes("valid Discord Webhook URL")));
    });

    it("saves to localStorage and updates UI on success", async () => {
      const wh = document.getElementById("discord-webhook-url");
      wh.value = "https://discord.com/api/webhooks/123/test";
      let captured = null;
      window.fetch = async (url, opts) => {
        captured = { url, opts };
        return { ok: true, json: async () => ({ status: "success" }) };
      };

      await window.saveDiscordSettings();

      assert.equal(window.localStorage.getItem("discord_webhook_url"), "https://discord.com/api/webhooks/123/test");
      assert.ok(captured, "fetch should have been called");
      assert.equal(captured.url, "/api/discord/config");
      assert.equal(JSON.parse(captured.opts.body).webhook_url, "https://discord.com/api/webhooks/123/test");
      assert.ok(document.getElementById("discord-status-badge").classList.contains("discord-connected"));
      assert.ok(document.getElementById("discord-modal").classList.contains("hidden"));
    });

    it("falls back to local save when backend is offline", async () => {
      const wh = document.getElementById("discord-webhook-url");
      wh.value = "https://discord.com/api/webhooks/456/test";
      window.fetch = async () => { throw new Error("Network error"); };

      await window.saveDiscordSettings();

      assert.equal(window.localStorage.getItem("discord_webhook_url"), "https://discord.com/api/webhooks/456/test");
      assert.ok(document.getElementById("discord-status-badge").classList.contains("discord-connected"));
    });
  });

  describe("testDiscordWebhook", () => {
    it("alerts when no webhook URL is provided", () => {
      window.testDiscordWebhook();
      assert.ok(alertCalls.some((c) => c.includes("Webhook URL")));
    });

    it("sends a test POST to the webhook URL", async () => {
      const wh = document.getElementById("discord-webhook-url");
      wh.value = "https://discord.com/api/webhooks/789/test";
      let captured = null;
      window.fetch = async (url, opts) => {
        captured = { url, opts };
        return { ok: true, json: async () => ({}) };
      };

      await window.testDiscordWebhook();

      assert.ok(captured, "fetch should have been called");
      assert.equal(captured.url, "https://discord.com/api/webhooks/789/test");
      assert.equal(captured.opts.method, "POST");
      assert.ok(captured.opts.body.includes("GENESIS ENGINE"));
      assert.ok(alertCalls.some((c) => c.includes("Test notification sent")));
    });

    it("alerts on fetch failure", async () => {
      const wh = document.getElementById("discord-webhook-url");
      wh.value = "https://discord.com/api/webhooks/bad/test";
      window.fetch = async () => { throw new Error("CORS error"); };

      await window.testDiscordWebhook();

      assert.ok(alertCalls.some((c) => c.includes("Failed to send Discord alert")));
    });

    it("uses localStorage webhook URL when input is empty", async () => {
      const wh = document.getElementById("discord-webhook-url");
      wh.value = "";
      window.localStorage.setItem("discord_webhook_url", "https://discord.com/api/webhooks/local/test");
      let captured = null;
      window.fetch = async (url, opts) => {
        captured = { url, opts };
        return { ok: true, json: async () => ({}) };
      };

      await window.testDiscordWebhook();

      assert.ok(captured);
      assert.equal(captured.url, "https://discord.com/api/webhooks/local/test");
    });
  });

  describe("checkDiscordConnection", () => {
    it("updates to connected when backend reports connected", async () => {
      window.fetch = async () => ({
        ok: true,
        json: async () => ({ connected: true }),
      });

      await window.checkDiscordConnection();

      assert.ok(document.getElementById("discord-status-badge").classList.contains("discord-connected"));
      assert.equal(document.getElementById("discord-status-text").textContent, "DISCORD: LIVE");
    });

    it("updates to disconnected when backend reports disconnected", async () => {
      window.fetch = async () => ({
        ok: true,
        json: async () => ({ connected: false }),
      });

      await window.checkDiscordConnection();

      assert.ok(document.getElementById("discord-status-badge").classList.contains("discord-disconnected"));
      assert.equal(document.getElementById("discord-status-text").textContent, "DISCORD: OFF");
    });

    it("falls back to localStorage when backend is unreachable", async () => {
      window.fetch = async () => { throw new Error("Backend down"); };
      window.localStorage.setItem("discord_webhook_url", "https://discord.com/api/webhooks/x/y");

      await window.checkDiscordConnection();

      assert.ok(document.getElementById("discord-status-badge").classList.contains("discord-connected"));
      assert.equal(document.getElementById("discord-status-text").textContent, "DISCORD: LIVE");
    });

    it("shows disconnected when backend is unreachable and no saved URL", async () => {
      window.fetch = async () => { throw new Error("Backend down"); };
      window.localStorage.removeItem("discord_webhook_url");

      await window.checkDiscordConnection();

      assert.ok(document.getElementById("discord-status-badge").classList.contains("discord-disconnected"));
      assert.equal(document.getElementById("discord-status-text").textContent, "DISCORD: OFF");
    });
  });

  // ── Trading Profiles ───────────────────────────────────────────────

  describe("TRADING_PROFILES", () => {
    it("defines all four profiles with correct metadata", () => {
      assert.ok(window.TRADING_PROFILES.swing_trader);
      assert.ok(window.TRADING_PROFILES.range_scalper);
      assert.ok(window.TRADING_PROFILES.breakout_hunter);
      assert.ok(window.TRADING_PROFILES.day_trader);

      assert.equal(window.TRADING_PROFILES.swing_trader.name, "Swing Trader");
      assert.equal(window.TRADING_PROFILES.range_scalper.name, "Range Fade Scalper");
      assert.equal(window.TRADING_PROFILES.breakout_hunter.name, "Breakout Hunter");
      assert.equal(window.TRADING_PROFILES.day_trader.name, "Universal Day Trader");
    });

    it("includes XAUUSD (Gold) in every profile's pair list", () => {
      for (const key of Object.keys(window.TRADING_PROFILES)) {
        const pairs = window.TRADING_PROFILES[key].pairs;
        assert.ok(pairs.includes("XAUUSD"), "Profile " + key + " should include XAUUSD");
      }
    });

    it("has correct pair counts", () => {
      assert.equal(window.TRADING_PROFILES.swing_trader.pairs.length, 17);
      assert.equal(window.TRADING_PROFILES.range_scalper.pairs.length, 17);
      assert.equal(window.TRADING_PROFILES.breakout_hunter.pairs.length, 18);
      assert.equal(window.TRADING_PROFILES.day_trader.pairs.length, 23);
    });

    it("has profile-specific riskPerTrade and goldStopMultiplier", () => {
      assert.equal(window.TRADING_PROFILES.swing_trader.riskPerTrade, 0.01);
      assert.equal(window.TRADING_PROFILES.swing_trader.goldStopMultiplier, 2.5);
      assert.equal(window.TRADING_PROFILES.range_scalper.riskPerTrade, 0.005);
      assert.equal(window.TRADING_PROFILES.range_scalper.goldStopMultiplier, 0.8);
      assert.equal(window.TRADING_PROFILES.breakout_hunter.riskPerTrade, 0.015);
      assert.equal(window.TRADING_PROFILES.breakout_hunter.goldStopMultiplier, 1.8);
      assert.equal(window.TRADING_PROFILES.day_trader.riskPerTrade, 0.01);
      assert.equal(window.TRADING_PROFILES.day_trader.goldStopMultiplier, 1.5);
    });
  });

  // ── populateSymbolDropdown ─────────────────────────────────────────

  describe("populateSymbolDropdown", () => {
    it("repopulates stake-symbol options with the given pairs", () => {
      const pairs = ["EURUSD", "XAUUSD", "GBPUSD"];
      window.populateSymbolDropdown(pairs);

      const select = document.getElementById("stake-symbol");
      const options = select.querySelectorAll("option");
      assert.equal(options.length, 3);
      assert.equal(options[0].value, "EURUSD");
      assert.equal(options[1].value, "XAUUSD");
      assert.equal(options[2].value, "GBPUSD");
    });

    it("labels XAUUSD as (Gold)", () => {
      window.populateSymbolDropdown(["XAUUSD"]);

      const select = document.getElementById("stake-symbol");
      const opt = select.querySelector("option");
      assert.equal(opt.value, "XAUUSD");
      assert.ok(opt.textContent.includes("Gold"));
    });

    it("is a no-op when select element is missing", () => {
      document.body.removeChild(document.getElementById("stake-symbol"));
      assert.doesNotThrow(() => window.populateSymbolDropdown(["EURUSD"]));
    });
  });

  // ── selectProfile ──────────────────────────────────────────────────

  describe("selectProfile", () => {
    it("sets activeProfile, saves to localStorage, and populates dropdown", () => {
      window.selectProfile("swing_trader");

      assert.equal(window.getActiveProfile(), "swing_trader");
      assert.equal(window.localStorage.getItem("genesis_active_profile"), "swing_trader");

      const options = document.getElementById("stake-symbol").querySelectorAll("option");
      assert.ok(options.length > 0);
      const values = Array.from(options).map(o => o.value);
      assert.ok(values.includes("EURUSD"));
      assert.ok(values.includes("XAUUSD"));
    });

    it("switches to a different profile", () => {
      window.selectProfile("breakout_hunter");

      assert.equal(window.getActiveProfile(), "breakout_hunter");
      assert.equal(window.localStorage.getItem("genesis_active_profile"), "breakout_hunter");

      const options = document.getElementById("stake-symbol").querySelectorAll("option");
      assert.ok(options.length > 0);
    });

    it("ignores unknown profile keys", () => {
      window.selectProfile("nonexistent_profile");

      // activeProfile should remain "swing_trader" (set by beforeEach)
      assert.equal(window.getActiveProfile(), "swing_trader");
      // localStorage should still have "swing_trader", not "nonexistent_profile"
      assert.equal(window.localStorage.getItem("genesis_active_profile"), "swing_trader");
    });

    it("persists active profile across profile switches", () => {
      window.selectProfile("scalper");
      window.selectProfile("day_trader");

      assert.equal(window.getActiveProfile(), "day_trader");
      assert.equal(window.localStorage.getItem("genesis_active_profile"), "day_trader");
    });
  });

  // ── sendDiscordTradeNotification ───────────────────────────────────

  describe("sendDiscordTradeNotification", () => {
    afterEach(() => {
      window.fetch = undefined;
    });

    it("sends a POST fetch with profile name and embed to the webhook URL", async () => {
      window.localStorage.setItem("discord_webhook_url", "https://discord.com/api/webhooks/test/abc");
      window.selectProfile("swing_trader");

      let captured = null;
      window.fetch = async (url, opts) => {
        captured = { url: url, opts: opts };
        return { ok: true };
      };

      await window.sendDiscordTradeNotification({
        type: "BUY",
        symbol: "EURUSD",
        lots: 0.02,
        price: 1.0850,
      });

      assert.ok(captured);
      assert.equal(captured.url, "https://discord.com/api/webhooks/test/abc");
      assert.equal(captured.opts.method, "POST");
      assert.equal(captured.opts.headers["Content-Type"], "application/json");

      const body = JSON.parse(captured.opts.body);
      assert.ok(body.embeds && body.embeds.length === 1);
      assert.equal(body.embeds[0].title, "📊 BUY Order Executed (Swing Trader)");
      assert.equal(body.embeds[0].color, 0x4ade80);

      const fields = body.embeds[0].fields;
      const profField = fields.find(f => f.name === "Active Profile");
      assert.equal(profField.value, "Swing Trader");
    });

    it("tags Gold (XAUUSD) executions with the gold icon", () => {
      window.localStorage.setItem("discord_webhook_url", "https://discord.com/api/webhooks/test/abc");
      window.selectProfile("range_scalper");

      let captured = null;
      window.fetch = async (url, opts) => {
        captured = { url: url, opts: opts };
        return { ok: true };
      };

      window.sendDiscordTradeNotification({
        type: "SELL",
        symbol: "XAUUSD",
        lots: 0.5,
        price: 2350.50,
      });

      const body = JSON.parse(captured.opts.body);
      assert.equal(body.embeds[0].title, "🏆 [GOLD] SELL Order Executed (Range Fade Scalper)");
      assert.equal(body.embeds[0].color, 0xf87171);
    });

    it("warns and returns when no webhook URL is configured", () => {
      window.localStorage.removeItem("discord_webhook_url");
      window.selectProfile("breakout_hunter");

      let fetchCalled = false;
      window.fetch = async () => { fetchCalled = true; };

      window.sendDiscordTradeNotification({
        type: "BUY",
        symbol: "GBPJPY",
        lots: 0.1,
        price: 185.30,
      });

      assert.ok(!fetchCalled, "fetch should not be called without a webhook URL");
    });

    it("uses the default profile name when activeProfile is not in TRADING_PROFILES", () => {
      window.localStorage.setItem("discord_webhook_url", "https://discord.com/api/webhooks/test/abc");

      // Temporarily make swing_trader unknown to trigger the fallback
      const savedSwing = window.TRADING_PROFILES.swing_trader;
      delete window.TRADING_PROFILES.swing_trader;
      window.selectProfile("swing_trader"); // will not change activeProfile since key is deleted

      let captured = null;
      window.fetch = async (url, opts) => {
        captured = { url: url, opts: opts };
        return { ok: true };
      };

      window.sendDiscordTradeNotification({
        type: "BUY",
        symbol: "AUDUSD",
        lots: 0.05,
        price: 0.6650,
      });

      const body = JSON.parse(captured.opts.body);
      assert.equal(body.embeds[0].title, "📊 BUY Order Executed (Genesis Engine)");

      // Restore
      window.TRADING_PROFILES.swing_trader = savedSwing;
      window.selectProfile("swing_trader");
    });
  });
});
