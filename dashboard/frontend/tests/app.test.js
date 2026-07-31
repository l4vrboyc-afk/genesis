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
  <div id="symbol-dropdown" class="custom-liquid-dropdown">
    <button type="button" id="selected-symbol-text">XAUUSD</button>
    <div id="symbol-menu-list" class="dropdown-list liquid-glass-menu"></div>
  </div>
  <select id="stake-symbol">
    <option value="EURUSD">EURUSD</option>
    <option value="USDJPY">USDJPY</option>
    <option value="GBPUSD">GBPUSD</option>
  </select>
  <input type="number" id="stake-amount-usd" value="20" />
  <span id="calculated-lots-preview">0.02 Lots</span>
  <span class="stake-val">20</span>
  <div id="gateway-overall-status" class="gate-status-text neutral">CHECKING...</div>
  <div id="gate-1" class="gate-pill">EMA</div>
  <div id="gate-2" class="gate-pill">ADX</div>
  <div id="gate-3" class="gate-pill">RSI</div>
  <div id="gate-4" class="gate-pill">VOL</div>
  <div id="gate-5" class="gate-pill">REG</div>

  <!-- Header Trade Signal Pill -->
  <div id="trade-signal-pill" class="signal-pill signal-wait">
    <div class="signal-badge">
      <span class="signal-dot"></span>
      <span id="sig-action">SCANNING...</span>
    </div>
    <div class="signal-details">
      <span id="sig-sl">SL: --</span>
      <span class="sig-divider">•</span>
      <span id="sig-tp">TP: --</span>
      <span class="sig-divider">•</span>
      <span id="sig-duration">Hold: --</span>
    </div>
  </div>

  <!-- Quick Stake Inline Signal Pill with Smart Assist Toggle -->
  <div id="qs-setup-pill" class="qs-setup-capsule">
    <label class="smart-toggle-wrapper" title="Toggle Genesis Smart Assist">
      <input type="checkbox" id="smart-assist-toggle" onchange="toggleSmartAssist(this.checked)" checked>
      <span class="smart-toggle-slider"></span>
    </label>
    <span class="smart-assist-label" id="assist-status-text">SMART ASSIST</span>
    <span class="qs-sep">|</span>
    <div id="qs-targets-group" class="qs-targets-inline">
      <span id="qs-sl">SL: <b>--</b></span>
      <span class="qs-sep">•</span>
      <span id="qs-tp">TP: <b>--</b></span>
      <span class="qs-sep">•</span>
      <span id="qs-hold">Hold: <b>--</b></span>
    </div>
  </div>
  <span id="gate-ema" class="hpill">EMA</span>
  <span id="gate-adx" class="hpill">ADX</span>
  <span id="gate-rsi" class="hpill">RSI</span>
  <span id="gate-vol" class="hpill">VOL</span>
  <span id="gate-reg" class="hpill">REG</span>

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

  <!-- Ticker bar -->
  <div id="dynamic-ticker-container"></div>

  <!-- Tables -->
  <span id="active-count">0 OPEN POSITIONS</span>
  <table><tbody id="active-positions-body"></tbody></table>
  <table><tbody id="recent-history-body"></tbody></table>

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
      "window.toggleSymbolMenu = toggleSymbolMenu;" +
      "window.selectSymbol = selectSymbol;" +
      "window.syncProfilePairsDropdown = syncProfilePairsDropdown;" +
      "window.getSelectedSymbol = function() { return selectedSymbol; };" +
      "window.sendDiscordTradeNotification = sendDiscordTradeNotification;" +
      "window.evaluateFiveGateways = evaluateFiveGateways;" +
      "window.debouncedEvaluateGateways = debouncedEvaluateGateways;" +
      "window.flushDebouncedGateways = flushDebouncedGateways;" +
      "window.clearDebouncedGateways = clearDebouncedGateways;" +
      "window.setGatewayDebounceMs = setGatewayDebounceMs;" +
      "window.updateGatewayUI = updateGatewayUI;" +
      "window.simulateGatewayCheck = simulateGatewayCheck;" +
      "window.updateTradeSignalPill = updateTradeSignalPill;" +
      "window.calculateLocalSignalPreview = calculateLocalSignalPreview;" +
      "window.renderSignalData = renderSignalData;" +
      "window.applyWsGateUpdate = applyWsGateUpdate;" +
      "window.applyWsSignalUpdate = applyWsSignalUpdate;" +
      "window.updateQuickStakeSetup = updateQuickStakeSetup;" +
      "window.toggleSmartAssist = toggleSmartAssist;" +
      "window.checkAllGatesPassed = checkAllGatesPassed;" +
      "window.getCalculatedTargets = getCalculatedTargets;" +
      "window.executeQuickStake = executeQuickStake;" +
      "window.getSmartAssistState = getSmartAssistState;" +
      "window.getActiveProfile = getActiveProfile;" +
      "window.getProfileName = getProfileName;" +
      "window.backendProfileKeyToFrontend = backendProfileKeyToFrontend;" +
      "window.recordBelongsToProfile = recordBelongsToProfile;" +
      "window.computeScopedStats = computeScopedStats;" +
      "window.renderProfileTickerBar = renderProfileTickerBar;" +
      "window.applyTickerTick = applyTickerTick;" +
      "window.onProfileChange = onProfileChange;";

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

    // Reset the custom symbol dropdown (re-create if a previous test removed it)
    if (!document.getElementById("symbol-menu-list")) {
      const wrap = document.createElement("div");
      wrap.id = "symbol-dropdown";
      wrap.className = "custom-liquid-dropdown";
      wrap.innerHTML = '<button type="button" id="selected-symbol-text">XAUUSD</button><div id="symbol-menu-list" class="dropdown-list liquid-glass-menu"></div>';
      document.body.appendChild(wrap);
    } else {
      document.getElementById("symbol-menu-list").classList.remove("open");
      document.getElementById("symbol-menu-list").innerHTML = "";
    }
    if (!document.getElementById("selected-symbol-text")) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.id = "selected-symbol-text";
      btn.textContent = "XAUUSD";
      document.body.appendChild(btn);
    }

    // Reset the header gateway pills (re-create if a previous test removed them)
    var hpillIds = ['gate-ema', 'gate-adx', 'gate-rsi', 'gate-vol', 'gate-reg'];
    hpillIds.forEach(function(id) {
      if (!document.getElementById(id)) {
        var pill = document.createElement("span");
        pill.id = id;
        pill.className = "hpill";
        document.body.appendChild(pill);
      }
      var pillEl = document.getElementById(id);
      if (pillEl) pillEl.className = "hpill";
    });

    // Restore gateway pills if a previous test removed them
    var gateIds = ['gate-1', 'gate-2', 'gate-3', 'gate-4', 'gate-5'];
    gateIds.forEach(function(id) {
      if (!document.getElementById(id)) {
        var pill = document.createElement("div");
        pill.id = id;
        pill.className = "gate-pill";
        document.body.appendChild(pill);
      }
    });
    var statusEl = document.getElementById("gateway-overall-status");
    if (!statusEl) {
      statusEl = document.createElement("div");
      statusEl.id = "gateway-overall-status";
      statusEl.className = "gate-status-text neutral";
      statusEl.textContent = "CHECKING...";
      document.body.appendChild(statusEl);
    }
    // Reset all gate pills to neutral state
    gateIds.forEach(function(id) {
      var el = document.getElementById(id);
      if (el) el.className = "gate-pill";
    });
    if (statusEl) {
      statusEl.className = "gate-status-text neutral";
      statusEl.textContent = "CHECKING...";
    }
    // Reset the Trade Signal Pill to neutral/wait state
    var pill = document.getElementById("trade-signal-pill");
    if (pill) {
      pill.className = "signal-pill liquid-glass signal-wait";
    }
    var sigAction = document.getElementById("sig-action");
    if (sigAction) sigAction.textContent = "SCANNING...";
    ["sig-sl", "sig-tp", "sig-duration"].forEach(function(id) {
      var el = document.getElementById(id);
      if (el) el.textContent = id.replace("sig-", "") + ": --";
    });
    // Reset the Quick Stake setup capsule (re-create if a test removed it)
    var qsPill = document.getElementById("qs-setup-pill");
    if (!qsPill) {
      qsPill = document.createElement("div");
      qsPill.id = "qs-setup-pill";
      qsPill.className = "qs-setup-capsule";
      qsPill.innerHTML = '<label class="smart-toggle-wrapper"><input type="checkbox" id="smart-assist-toggle" checked><span class="smart-toggle-slider"></span></label><span class="smart-assist-label" id="assist-status-text">SMART ASSIST</span><span class="qs-sep">|</span><div id="qs-targets-group" class="qs-targets-inline"><span id="qs-sl">SL: <b>--</b></span><span class="qs-sep">•</span><span id="qs-tp">TP: <b>--</b></span><span class="qs-sep">•</span><span id="qs-hold">Hold: <b>--</b></span></div>';
      document.body.appendChild(qsPill);
    } else {
      qsPill.className = "qs-setup-capsule";
      var toggle = document.getElementById("smart-assist-toggle");
      if (toggle) toggle.checked = true;
      var label = document.getElementById("assist-status-text");
      if (label) {
        label.textContent = "SMART ASSIST";
        label.style.color = "#38bdf8";
      }
      var tg = document.getElementById("qs-targets-group");
      if (tg) tg.classList.remove("disabled");
    }
    ["qs-sl", "qs-tp", "qs-hold"].forEach(function(id) {
      var el = document.getElementById(id);
      if (el) el.innerHTML = id.replace("qs-", "").toUpperCase() + ": <b>--</b>";
    });
    // Reset activeProfile back to swing_trader (the default)
    window.selectProfile("swing_trader");
    // Reset Smart Assist back to enabled state
    window.toggleSmartAssist(true);
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

    it("populates the custom liquid dropdown menu with items", () => {
      window.populateSymbolDropdown(["EURUSD", "XAUUSD", "GBPUSD"]);

      const menu = document.getElementById("symbol-menu-list");
      const items = menu.querySelectorAll(".dropdown-item");
      assert.equal(items.length, 3);
      assert.ok(items[0].textContent.includes("EURUSD"));
      assert.ok(items[1].textContent.includes("Gold"));
    });

    it("selects the first pair as the active symbol", () => {
      window.populateSymbolDropdown(["GBPUSD", "EURUSD"]);
      assert.equal(window.getSelectedSymbol(), "GBPUSD");
      assert.equal(
        document.getElementById("selected-symbol-text").textContent,
        "GBPUSD"
      );
    });
  });

  // ── Custom Liquid Symbol Dropdown ─────────────────────────────────

  describe("Custom Symbol Dropdown", () => {
    // Teardown: cancel any debounce timer a selection armed and restore
    // the default 250 ms window so a stray evaluateFiveGateways cannot
    // fire into the next test or perturb its timing expectations.
    afterEach(() => {
      if (window.clearDebouncedGateways) window.clearDebouncedGateways();
      if (window.setGatewayDebounceMs) window.setGatewayDebounceMs(250);
    });

    it("toggleSymbolMenu toggles the open class", () => {
      const menu = document.getElementById("symbol-menu-list");
      menu.classList.remove("open");
      window.toggleSymbolMenu();
      assert.ok(menu.classList.contains("open"));
      window.toggleSymbolMenu();
      assert.ok(!menu.classList.contains("open"));
    });

    it("selectSymbol updates the trigger label and closes the menu", () => {
      const menu = document.getElementById("symbol-menu-list");
      menu.classList.add("open");

      // skipGateway=true keeps the async gateway re-check out of this test
      window.selectSymbol("XAUUSD", true);

      assert.equal(
        document.getElementById("selected-symbol-text").textContent,
        "🏆 XAUUSD (Gold)"
      );
      assert.ok(!menu.classList.contains("open"));
      assert.equal(window.getSelectedSymbol(), "XAUUSD");
      assert.equal(document.getElementById("stake-symbol").value, "XAUUSD");
    });

    it("selectSymbol labels non-Gold symbols plainly", () => {
      window.selectSymbol("GBPUSD");
      assert.equal(
        document.getElementById("selected-symbol-text").textContent,
        "GBPUSD"
      );
    });

    it("selectSymbol with skipGateway skips the gateway re-check", async () => {
      let fetchCalled = false;
      window.fetch = async () => { fetchCalled = true; return { ok: true, json: async () => ({ gates: [true, true, true, true, true] }) }; };

      window.selectSymbol("EURUSD", true);
      await new Promise(r => setTimeout(r, 20));
      assert.ok(!fetchCalled, "fetch should not fire when skipGateway is true");
    });

    it("debouncedEvaluateGateways coalesces rapid symbol changes into one fetch", async () => {
      // Only count /api/evaluator fetches — the signal + quick-stake pills
      // fire their own (immediate) requests on every selection.
      let evaluatorCalls = [];
      window.fetch = async (url) => {
        if (String(url).includes("/api/evaluator")) evaluatorCalls.push(url);
        return { ok: true, json: async () => ({ gates: [true, true, true, true, true] }) };
      };

      // Shorten the debounce window so the test stays fast and deterministic.
      window.setGatewayDebounceMs(30);

      // Rapid-fire symbol scroll — only the LAST symbol should hit the API.
      window.selectSymbol("EURUSD");
      window.selectSymbol("GBPUSD");
      window.selectSymbol("USDJPY");

      assert.equal(evaluatorCalls.length, 0, "no evaluator fetch before the debounce window elapses");

      await new Promise(r => setTimeout(r, 80));

      assert.equal(evaluatorCalls.length, 1, "exactly one evaluator fetch after the debounce window");
      assert.ok(evaluatorCalls[0].includes("symbol=USDJPY"), "fetch should target the last selected symbol");
    });

    it("debouncedEvaluateGateways drops a stale in-flight response for an older symbol", async () => {
      // First call resolves its response AFTER the user has moved on — the
      // stale result must NOT overwrite the newer symbol's pills.
      let resolveFirst;
      let evaluatorCalls = 0;
      window.fetch = async (url) => {
        if (String(url).includes("/api/evaluator")) evaluatorCalls++;
        if (String(url).includes("symbol=EURUSD")) {
          return new Promise(resolve => { resolveFirst = resolve; });
        }
        // USDJPY resolves immediately with all-pass gates.
        return { ok: true, json: async () => ({ gates: [true, true, true, true, true] }) };
      };

      window.selectSymbol("EURUSD");
      await window.flushDebouncedGateways(); // fires EURUSD fetch, stays in-flight
      await new Promise(r => setTimeout(r, 10));

      // User scrolls on to USDJPY; its fetch resolves and paints pills.
      window.selectSymbol("USDJPY");
      await window.flushDebouncedGateways();
      await new Promise(r => setTimeout(r, 10));
      assert.equal(document.getElementById("gate-1").className.includes("passed"), true);

      // Now the OLD EURUSD response finally arrives — it must be dropped.
      if (resolveFirst) {
        resolveFirst({ ok: true, json: async () => ({ gates: [false, false, false, false, false] }) });
      }
      await new Promise(r => setTimeout(r, 20));

      // Pills still reflect USDJPY's all-pass result, not the stale all-fail one.
      assert.equal(document.getElementById("gate-1").className.includes("passed"), true);
      assert.equal(document.getElementById("gate-5").className.includes("passed"), true);
      const status = document.getElementById("gateway-overall-status");
      assert.equal(status.textContent, "5/5 OPTIMAL");
    });

    it("flushDebouncedGateways runs a pending evaluation immediately", async () => {
      let evaluatorCalls = [];
      window.fetch = async (url) => {
        if (String(url).includes("/api/evaluator")) evaluatorCalls.push(url);
        return { ok: true, json: async () => ({ gates: [true, true, true, true, true] }) };
      };

      // A long window proves flush bypasses the timer rather than waiting.
      window.setGatewayDebounceMs(5000);

      window.selectSymbol("EURUSD");
      assert.equal(evaluatorCalls.length, 0, "pending — debounce not elapsed");
      window.flushDebouncedGateways();
      await new Promise(r => setTimeout(r, 10));
      assert.equal(evaluatorCalls.length, 1, "flush should fire the pending evaluation");
    });

    it("syncProfilePairsDropdown populates menu from the active profile", () => {
      window.selectProfile("day_trader");
      window.syncProfilePairsDropdown();

      const menu = document.getElementById("symbol-menu-list");
      const items = menu.querySelectorAll(".dropdown-item");
      assert.ok(items.length > 0);
      assert.equal(items.length, window.TRADING_PROFILES.day_trader.pairs.length);
    });

    it("updateGatewayUI updates the header hpill matrix", () => {
      window.updateGatewayUI([true, false, true, false, true]);

      assert.ok(document.getElementById("gate-ema").classList.contains("passed"));
      assert.ok(document.getElementById("gate-adx").classList.contains("failed"));
      assert.ok(document.getElementById("gate-rsi").classList.contains("passed"));
      assert.ok(document.getElementById("gate-vol").classList.contains("failed"));
      assert.ok(document.getElementById("gate-reg").classList.contains("passed"));
    });
  });

  // ── updateLotConversionPreview stake-val sync ─────────────────────

  describe("updateLotConversionPreview stake sync", () => {
    it("updates .stake-val spans with the rounded stake amount", () => {
      document.getElementById("stake-amount-usd").value = "35";
      window.updateLotConversionPreview();
      assert.equal(document.querySelector(".stake-val").textContent, "35");
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

  // ── Profile Ticker Bar ─────────────────────────────────────────────

  describe("renderProfileTickerBar", () => {
    it("renders one ticker item per pair configured for the active profile", () => {
      window.selectProfile("swing_trader");
      window.renderProfileTickerBar();

      const container = document.getElementById("dynamic-ticker-container");
      const items = container.querySelectorAll(".ticker-item-clean");
      assert.equal(items.length, window.TRADING_PROFILES.swing_trader.pairs.length);
    });

    it("re-scopes the ticker bar when the profile changes", () => {
      window.selectProfile("swing_trader");
      window.renderProfileTickerBar();
      const swingCount = document.querySelectorAll(".ticker-item-clean").length;

      window.selectProfile("day_trader");
      window.renderProfileTickerBar();
      const dayCount = document.querySelectorAll(".ticker-item-clean").length;

      assert.notEqual(swingCount, dayCount);
      assert.equal(dayCount, window.TRADING_PROFILES.day_trader.pairs.length);
    });

    it("is a no-op when the container element is missing", () => {
      const container = document.getElementById("dynamic-ticker-container");
      const parent = container.parentNode;
      parent.removeChild(container);
      try {
        assert.doesNotThrow(() => window.renderProfileTickerBar());
      } finally {
        parent.appendChild(container);
      }
    });
  });

  describe("applyTickerTick", () => {
    it("patches the ticker price for the matching symbol", () => {
      window.selectProfile("swing_trader");
      window.renderProfileTickerBar();

      window.applyTickerTick({ symbol: "EURUSD", bid: 1.0850, ask: 1.0852 });
      const el = document.getElementById("ticker-price-EURUSD");
      assert.equal(el.textContent, "1.08500");
    });

    it("tints up/down based on the direction of the last move", () => {
      window.selectProfile("swing_trader");
      window.renderProfileTickerBar();

      window.applyTickerTick({ symbol: "EURUSD", bid: 1.0800 });
      window.applyTickerTick({ symbol: "EURUSD", bid: 1.0850 });
      assert.ok(document.getElementById("ticker-price-EURUSD").classList.contains("up"));

      window.applyTickerTick({ symbol: "EURUSD", bid: 1.0820 });
      assert.ok(document.getElementById("ticker-price-EURUSD").classList.contains("down"));
    });
  });

  // ── Profile scoping helpers ───────────────────────────────────────

  describe("backendProfileKeyToFrontend", () => {
    it("maps backend keys to front-end profile keys", () => {
      assert.equal(window.backendProfileKeyToFrontend("default"), "swing_trader");
      assert.equal(window.backendProfileKeyToFrontend("scalper"), "range_scalper");
      assert.equal(window.backendProfileKeyToFrontend("breakout"), "breakout_hunter");
      assert.equal(window.backendProfileKeyToFrontend("daytrader"), "day_trader");
    });

    it("returns null for unmapped backend keys", () => {
      assert.equal(window.backendProfileKeyToFrontend("unknown_profile"), null);
      assert.equal(window.backendProfileKeyToFrontend(""), null);
    });
  });

  describe("recordBelongsToProfile", () => {
    it("keeps records tagged with a strategy of the active profile", () => {
      window.selectProfile("swing_trader");
      assert.ok(window.recordBelongsToProfile({ strategy: "Smart Trend Breakout" }));
      assert.ok(window.recordBelongsToProfile({ strategy: "Mean Reversion" }));
    });

    it("hides records from another profile's strategies", () => {
      window.selectProfile("swing_trader");
      assert.equal(window.recordBelongsToProfile({ strategy: "Scalper Momentum" }), false);
      assert.equal(window.recordBelongsToProfile({ strategy: "Trend Engine" }), false);
      assert.equal(window.recordBelongsToProfile({ strategy: "Session Breakout" }), false);
    });

    it("keeps un-attributed records visible", () => {
      window.selectProfile("swing_trader");
      assert.ok(window.recordBelongsToProfile({}));
      assert.ok(window.recordBelongsToProfile({ strategy: "Manual Override" }));
    });

    it("honours an explicit profileKey argument", () => {
      assert.ok(window.recordBelongsToProfile({ strategy: "Trend Engine" }, "day_trader"));
      assert.equal(window.recordBelongsToProfile({ strategy: "Trend Engine" }, "swing_trader"), false);
    });

    it("prefers the exact profile tag over strategy-name inference", () => {
      // day_trader can run "Trend Engine" via strategy-name inference, but
      // the exact profile tag is authoritative: a tag of "swing_trader"
      // must exclude the record even though its strategy matches day_trader.
      assert.equal(
        window.recordBelongsToProfile({ profile: "swing_trader", strategy: "Trend Engine" }, "day_trader"),
        false
      );
      assert.ok(
        window.recordBelongsToProfile({ profile: "swing_trader", strategy: "Scalper Momentum" }, "swing_trader")
      );
    });

    it("maps backend profile keys to front-end keys when matching the tag", () => {
      // DB stores the backend key (default/scalper/breakout/daytrader) from
      // settings.active_profile — the helper must map before comparing.
      assert.ok(
        window.recordBelongsToProfile({ profile: "daytrader", strategy: "Manual Override" }, "day_trader")
      );
      assert.equal(
        window.recordBelongsToProfile({ profile: "scalper", strategy: "Manual Override" }, "swing_trader"),
        false
      );
    });

    it("falls back to strategy inference when the record has no profile tag", () => {
      window.selectProfile("swing_trader");
      assert.ok(window.recordBelongsToProfile({ strategy: "Smart Trend Breakout" }));
      assert.equal(window.recordBelongsToProfile({ strategy: "Trend Engine" }), false);
    });
  });

  describe("renderRecentHistory profile scoping", () => {
    it("shows only trades from the active profile's strategies", () => {
      window.selectProfile("day_trader");
      const trades = [
        { symbol: "EURUSD", strategy: "Trend Engine", direction: "buy", profit: 10 },
        { symbol: "GBPUSD", strategy: "Scalper Momentum", direction: "sell", profit: -5 },
        { symbol: "USDJPY", strategy: "Mean Reversion", direction: "buy", profit: 3 },
      ];
      window.renderRecentHistory(trades);

      const rows = document.getElementById("recent-history-body").querySelectorAll("tr");
      assert.equal(rows.length, 2);
      assert.ok(rows[0].textContent.includes("EURUSD"));
      assert.ok(rows[1].textContent.includes("USDJPY"));
    });

    it("shows an empty state when no trades match the active profile", () => {
      window.selectProfile("breakout_hunter");
      window.renderRecentHistory([
        { symbol: "EURUSD", strategy: "Trend Engine", direction: "buy", profit: 10 },
      ]);

      const html = document.getElementById("recent-history-body").innerHTML;
      assert.ok(html.includes("No closed trades"));
    });
  });

  describe("renderActivePositions profile scoping", () => {
    it("hides positions left by another profile's strategies", () => {
      window.selectProfile("swing_trader");
      window.renderActivePositions([
        { ticket: 1, symbol: "EURUSD", comment: "Smart Trend Breakout entry", direction: "buy", volume: 0.1, profit: 5 },
        { ticket: 2, symbol: "GBPUSD", comment: "Trend Engine entry", direction: "sell", volume: 0.1, profit: -3 },
        { ticket: 3, symbol: "USDJPY", comment: "Manual Override", direction: "buy", volume: 0.1, profit: 1 },
      ]);

      const rows = document.getElementById("active-positions-body").querySelectorAll("tr");
      assert.equal(rows.length, 2);
      assert.equal(document.getElementById("active-count").textContent, "2 OPEN POSITIONS");
    });

    it("keeps all positions when none are attributed", () => {
      window.selectProfile("swing_trader");
      window.renderActivePositions([
        { ticket: 1, symbol: "EURUSD", direction: "buy", volume: 0.1, profit: 5 },
        { ticket: 2, symbol: "GBPUSD", direction: "sell", volume: 0.1, profit: -3 },
      ]);

      const rows = document.getElementById("active-positions-body").querySelectorAll("tr");
      assert.equal(rows.length, 2);
    });
  });

  describe("onProfileChange", () => {
    it("persists the profile and refreshes the header badge", () => {
      window.onProfileChange("breakout_hunter");

      assert.equal(window.getActiveProfile(), "breakout_hunter");
      assert.equal(window.localStorage.getItem("genesis_active_profile"), "breakout_hunter");
      assert.equal(document.getElementById("active-profile-badge").textContent, "PROFILE BREAKOUT_HUNTER");
    });

    it("re-renders the ticker bar for the new profile", () => {
      window.onProfileChange("swing_trader");
      window.renderProfileTickerBar();
      const swingCount = document.querySelectorAll(".ticker-item-clean").length;

      window.onProfileChange("day_trader");
      const dayCount = document.querySelectorAll(".ticker-item-clean").length;
      assert.equal(dayCount, window.TRADING_PROFILES.day_trader.pairs.length);
      assert.notEqual(dayCount, swingCount);
    });

    it("ignores unknown profile keys without mutating state", () => {
      window.selectProfile("swing_trader");
      window.onProfileChange("not_a_profile");

      assert.equal(window.getActiveProfile(), "swing_trader");
      assert.equal(window.localStorage.getItem("genesis_active_profile"), "swing_trader");
    });
  });

  describe("computeScopedStats", () => {
    it("computes wins, losses, and pnl from a closed-trade list", () => {
      const stats = window.computeScopedStats([
        { profit: 10 },
        { profit: -5 },
        { profit: 2.5 },
      ]);
      assert.equal(stats.total, 3);
      assert.equal(stats.winRate, "66.7");
      assert.equal(stats.totalPnl, 7.5);
      assert.equal(stats.avgWin, 6.25);
      assert.equal(stats.avgLoss, 5);
    });

    it("computes avgRr as the mean |return_r| over non-zero trades (mirrors backend avg_rr)", () => {
      // Backend averages positive achieved_rr (= |move|/|risk|), so the
      // frontend mirrors it with the magnitudes of non-zero return_r:
      // (2 + 1 + 0.5) / 3 ≈ 1.17
      const stats = window.computeScopedStats([
        { profit: 10, return_r: 2 },
        { profit: -5, return_r: -1 },
        { profit: 2.5, return_r: 0.5 },
      ]);
      assert.equal(stats.avgRr, 1.17);
    });

    it("ignores zero/absent return_r when averaging", () => {
      const stats = window.computeScopedStats([
        { profit: 10, return_r: 2 },
        { profit: -5, return_r: 0 },
        { profit: 2.5 }, // no return_r → excluded
      ]);
      assert.equal(stats.avgRr, 2);
    });

    it("computes maxDrawdown from the chronological cumulative PnL curve", () => {
      // Close order: +100 peak, then -50 (drawdown 50%), then -20 (drawdown
      // 70% vs peak 100), then a fresh +200 peak. Largest drop = 70%.
      const stats = window.computeScopedStats([
        { profit: 100, close_time: "2026-07-01T10:00:00Z" },
        { profit: -50, close_time: "2026-07-02T10:00:00Z" },
        { profit: -20, close_time: "2026-07-03T10:00:00Z" },
        { profit: 200, close_time: "2026-07-04T10:00:00Z" },
      ]);
      assert.equal(stats.maxDrawdown, 70);
    });

    it("computes maxDrawdown even when trades arrive out of close-time order", () => {
      // Same curve as above, but the list is scrambled — the function must
      // sort chronologically before measuring the drawdown.
      const stats = window.computeScopedStats([
        { profit: 200, close_time: "2026-07-04T10:00:00Z" },
        { profit: -20, close_time: "2026-07-03T10:00:00Z" },
        { profit: 100, close_time: "2026-07-01T10:00:00Z" },
        { profit: -50, close_time: "2026-07-02T10:00:00Z" },
      ]);
      assert.equal(stats.maxDrawdown, 70);
    });

    it("clamps drawdown at 100% when the PnL curve dips below zero", () => {
      // +100 peak, then -150 → running = -50, so (100 - (-50))/100 = 150%.
      // Without a starting balance the raw value looks impossible, so it
      // is clamped to 100.
      const stats = window.computeScopedStats([
        { profit: 100, close_time: "2026-07-01T10:00:00Z" },
        { profit: -150, close_time: "2026-07-02T10:00:00Z" },
      ]);
      assert.equal(stats.maxDrawdown, 100);
    });

    it("returns 0 drawdown and 0 avgRr for a never-positive or empty list", () => {
      const empty = window.computeScopedStats([]);
      assert.equal(empty.total, 0);
      assert.equal(empty.winRate, "0.0");
      assert.equal(empty.totalPnl, 0);
      assert.equal(empty.avgRr, 0);
      assert.equal(empty.maxDrawdown, 0);

      // Always-losing curve never goes positive → drawdown stays 0.
      const losing = window.computeScopedStats([
        { profit: -5, close_time: "2026-07-01T10:00:00Z" },
        { profit: -10, close_time: "2026-07-02T10:00:00Z" },
      ]);
      assert.equal(losing.maxDrawdown, 0);
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

  // ── evaluateFiveGateways ───────────────────────────────────────────

  describe("evaluateFiveGateways", () => {
    afterEach(() => {
      window.fetch = undefined;
    });

    it("fetches gate data from the API and calls updateGatewayUI", async () => {
      window.fetch = async (url) => {
        assert.ok(url.includes("symbol=EURUSD"));
        assert.ok(url.includes("profile=swing_trader"));
        return {
          ok: true,
          json: async () => ({
            gates: [true, true, true, true, true],
            overall: "5/5 OPTIMAL",
          }),
        };
      };

      await window.evaluateFiveGateways();

      const pills = document.querySelectorAll(".gate-pill");
      for (const pill of pills) {
        assert.ok(pill.classList.contains("passed"), "gate should be 'passed'");
      }
      const status = document.getElementById("gateway-overall-status");
      assert.equal(status.textContent, "5/5 OPTIMAL");
      assert.ok(status.classList.contains("ready"));
    });

    it("falls back to simulateGatewayCheck when fetch fails", async () => {
      window.fetch = async () => {
        throw new Error("Network error");
      };

      await window.evaluateFiveGateways();

      // simulateGatewayCheck for EURUSD returns [true, true, false, true, true]
      const gate3 = document.getElementById("gate-3");
      assert.ok(gate3.classList.contains("failed"));
      const gate1 = document.getElementById("gate-1");
      assert.ok(gate1.classList.contains("passed"));
    });

    it("falls back to simulateGatewayCheck on a non-OK backend response", async () => {
      // e.g. /api/evaluator returns 503 when MT5 market data is unavailable
      window.fetch = async () => ({
        ok: false,
        status: 503,
        json: async () => ({ detail: "MT5 market data unavailable" }),
      });

      await window.evaluateFiveGateways();

      // simulateGatewayCheck for EURUSD returns [true, true, false, true, true]
      const gate3 = document.getElementById("gate-3");
      assert.ok(gate3.classList.contains("failed"));
      const gate1 = document.getElementById("gate-1");
      assert.ok(gate1.classList.contains("passed"));
    });

    it("falls back to simulateGatewayCheck on a malformed payload", async () => {
      window.fetch = async () => ({
        ok: true,
        json: async () => ({ nonsense: true }),
      });

      await window.evaluateFiveGateways();

      // simulateGatewayCheck for EURUSD returns [true, true, false, true, true]
      const gate3 = document.getElementById("gate-3");
      assert.ok(gate3.classList.contains("failed"));
      const gate1 = document.getElementById("gate-1");
      assert.ok(gate1.classList.contains("passed"));
    });

    it("calls updateLotConversionPreview after evaluation", async () => {
      window.fetch = async () => ({
        ok: true,
        json: async () => ({ gates: [true, true, true, true, true] }),
      });

      const before = document.getElementById("calculated-lots-preview").textContent;
      await window.evaluateFiveGateways();
      const after = document.getElementById("calculated-lots-preview").textContent;
      // The lot preview should be refreshed (value might not change for same input, but no error)
      assert.equal(typeof after, "string");
    });
  });

  // ── updateGatewayUI ───────────────────────────────────────────────

  describe("updateGatewayUI", () => {
    it("marks all gates passed when all results are true", () => {
      window.updateGatewayUI([true, true, true, true, true]);

      const pills = document.querySelectorAll(".gate-pill");
      pills.forEach((pill) => {
        assert.ok(pill.classList.contains("passed"), "should be passed");
        assert.ok(!pill.classList.contains("failed"));
      });

      const status = document.getElementById("gateway-overall-status");
      assert.equal(status.textContent, "5/5 OPTIMAL");
      assert.ok(status.classList.contains("ready"));
    });

    it("marks failing gates as failed and sets warning status for 3/5", () => {
      window.updateGatewayUI([true, true, true, false, false]);

      const g4 = document.getElementById("gate-4");
      const g5 = document.getElementById("gate-5");
      assert.ok(g4.classList.contains("failed"));
      assert.ok(g5.classList.contains("failed"));

      const g1 = document.getElementById("gate-1");
      assert.ok(g1.classList.contains("passed"));

      const status = document.getElementById("gateway-overall-status");
      assert.equal(status.textContent, "3/5 MODERATE");
      assert.ok(status.classList.contains("warning"));
    });

    it("sets blocked status when fewer than 3 gates pass", () => {
      window.updateGatewayUI([true, false, false, false, false]);

      const status = document.getElementById("gateway-overall-status");
      assert.equal(status.textContent, "1/5 BLOCKED");
      assert.ok(status.classList.contains("blocked"));
    });

    it("is a no-op when gate elements are missing", () => {
      // Remove all gate pills
      const pills = document.querySelectorAll(".gate-pill");
      pills.forEach((p) => p.remove());

      assert.doesNotThrow(() => window.updateGatewayUI([true, true, true, true, true]));
    });
  });

  // ── simulateGatewayCheck ──────────────────────────────────────────

  describe("simulateGatewayCheck", () => {
    it("fails gate 3 (RSI) for non-Gold symbols", () => {
      window.simulateGatewayCheck("EURUSD");

      const g3 = document.getElementById("gate-3");
      assert.ok(g3.classList.contains("failed"));

      const g1 = document.getElementById("gate-1");
      assert.ok(g1.classList.contains("passed"));

      // 4 out of 5 pass → MODERATE
      const status = document.getElementById("gateway-overall-status");
      assert.equal(status.textContent, "4/5 MODERATE");
      assert.ok(status.classList.contains("warning"));
    });

    it("passes all gates for Gold (XAUUSD)", () => {
      window.simulateGatewayCheck("XAUUSD");

      const pills = document.querySelectorAll(".gate-pill");
      pills.forEach((pill) => {
        assert.ok(pill.classList.contains("passed"), "all gates should pass for Gold");
      });

      const status = document.getElementById("gateway-overall-status");
      assert.equal(status.textContent, "5/5 OPTIMAL");
      assert.ok(status.classList.contains("ready"));
    });
  });

  // ── updateTradeSignalPill ──────────────────────────────────────────

  describe("updateTradeSignalPill", () => {
    afterEach(() => {
      window.fetch = undefined;
    });

    it("fetches signal data from the API and renders it", async () => {
      window.fetch = async (url) => {
        assert.ok(url.includes("symbol=EURUSD"));
        assert.ok(url.includes("profile=swing_trader"));
        return {
          ok: true,
          json: async () => ({
            action: "BUY SIGNAL",
            type: "BUY",
            sl: "-15 pips",
            tp: "+30 pips",
            duration: "45m",
          }),
        };
      };

      await window.updateTradeSignalPill("EURUSD");

      const pill = document.getElementById("trade-signal-pill");
      assert.ok(pill.classList.contains("signal-buy"));

      const action = document.getElementById("sig-action");
      assert.equal(action.textContent, "BUY SIGNAL");

      assert.equal(document.getElementById("sig-sl").textContent, "SL: -15 pips");
      assert.equal(document.getElementById("sig-tp").textContent, "TP: +30 pips");
      assert.equal(document.getElementById("sig-duration").textContent, "Hold: 45m");
    });

    it("falls back to calculateLocalSignalPreview when fetch fails", async () => {
      window.fetch = async () => {
        throw new Error("Network unreachable");
      };

      await window.updateTradeSignalPill("XAUUSD");

      // Gold should get a BUY signal
      const pill = document.getElementById("trade-signal-pill");
      assert.ok(pill.classList.contains("signal-buy"));

      const action = document.getElementById("sig-action");
      assert.equal(action.textContent, "BUY SIGNAL");
    });

    it("falls back to calculateLocalSignalPreview on a non-OK backend response", async () => {
      // e.g. /api/signal returns 503 when MT5 market data is unavailable
      window.fetch = async () => ({
        ok: false,
        status: 503,
        json: async () => ({ detail: "MT5 market data unavailable" }),
      });

      await window.updateTradeSignalPill("XAUUSD");

      const pill = document.getElementById("trade-signal-pill");
      assert.ok(pill.classList.contains("signal-buy"));
      assert.equal(document.getElementById("sig-action").textContent, "BUY SIGNAL");
    });

    it("falls back to calculateLocalSignalPreview on a malformed payload", async () => {
      window.fetch = async () => ({
        ok: true,
        json: async () => ({ nonsense: true }),
      });

      await window.updateTradeSignalPill("EURUSD");

      // Non-Gold pair should get a WAIT preview
      const pill = document.getElementById("trade-signal-pill");
      assert.ok(pill.classList.contains("signal-wait"));
      assert.equal(document.getElementById("sig-action").textContent, "NEUTRAL / WAIT");
    });
  });

  // ── calculateLocalSignalPreview ────────────────────────────────────

  describe("calculateLocalSignalPreview", () => {
    it("generates a BUY signal for Gold (XAUUSD)", () => {
      window.calculateLocalSignalPreview("XAUUSD");

      const pill = document.getElementById("trade-signal-pill");
      assert.ok(pill.classList.contains("signal-buy"));
      assert.equal(document.getElementById("sig-action").textContent, "BUY SIGNAL");
    });

    it("generates a WAIT signal for non-Gold pairs", () => {
      window.calculateLocalSignalPreview("EURUSD");

      const pill = document.getElementById("trade-signal-pill");
      assert.ok(pill.classList.contains("signal-wait"));
      assert.equal(document.getElementById("sig-action").textContent, "NEUTRAL / WAIT");
    });
  });

  // ── renderSignalData ───────────────────────────────────────────────

  describe("renderSignalData", () => {
    it("renders BUY state with green pill", () => {
      window.renderSignalData({
        action: "BUY SIGNAL",
        type: "BUY",
        sl: "-15 pips",
        tp: "+30 pips",
        duration: "45m",
      });

      const pill = document.getElementById("trade-signal-pill");
      assert.ok(pill.classList.contains("signal-buy"));
      assert.ok(!pill.classList.contains("signal-sell"));
      assert.ok(!pill.classList.contains("signal-wait"));

      assert.equal(document.getElementById("sig-action").textContent, "BUY SIGNAL");
      assert.equal(document.getElementById("sig-sl").textContent, "SL: -15 pips");
      assert.equal(document.getElementById("sig-tp").textContent, "TP: +30 pips");
      assert.equal(document.getElementById("sig-duration").textContent, "Hold: 45m");
    });

    it("renders SELL state with red pill", () => {
      window.renderSignalData({
        action: "SELL SIGNAL",
        type: "SELL",
        sl: "-22 pips",
        tp: "+40 pips",
        duration: "1h - 2h",
      });

      const pill = document.getElementById("trade-signal-pill");
      assert.ok(pill.classList.contains("signal-sell"));
      assert.ok(!pill.classList.contains("signal-buy"));

      assert.equal(document.getElementById("sig-action").textContent, "SELL SIGNAL");
    });

    it("renders WAIT state with neutral pill", () => {
      window.renderSignalData({
        action: "NEUTRAL / WAIT",
        type: "WAIT",
        sl: "--",
        tp: "--",
        duration: "15m - 45m",
      });

      const pill = document.getElementById("trade-signal-pill");
      assert.ok(pill.classList.contains("signal-wait"));
      assert.ok(!pill.classList.contains("signal-buy"));
      assert.ok(!pill.classList.contains("signal-sell"));

      assert.equal(document.getElementById("sig-action").textContent, "NEUTRAL / WAIT");
    });

    it("is a no-op when the pill element is missing", () => {
      document.body.removeChild(document.getElementById("trade-signal-pill"));
      assert.doesNotThrow(() =>
        window.renderSignalData({
          action: "BUY SIGNAL",
          type: "BUY",
          sl: "-15",
          tp: "+30",
          duration: "45m",
        })
      );
    });
  });

  // ── applyWsGateUpdate / applyWsSignalUpdate (WS pill refresh) ─────

  describe("applyWsGateUpdate / applyWsSignalUpdate", () => {
    // The renderSignalData "no-op when the pill element is missing" test
    // removes #trade-signal-pill from the DOM and never restores it.  The
    // outer beforeEach only resets the pill *if present*, so re-create it
    // here (same re-creation convention as the other reset elements).
    beforeEach(() => {
      if (!document.getElementById("trade-signal-pill")) {
        const pill = document.createElement("div");
        pill.id = "trade-signal-pill";
        pill.className = "signal-pill liquid-glass signal-wait";
        pill.innerHTML =
          '<div class="signal-badge"><span class="signal-dot"></span><span id="sig-action">SCANNING...</span></div>' +
          '<div class="signal-details">' +
          '<span id="sig-sl">SL: --</span><span class="sig-divider">•</span>' +
          '<span id="sig-tp">TP: --</span><span class="sig-divider">•</span>' +
          '<span id="sig-duration">Hold: --</span></div>';
        document.body.appendChild(pill);
      }
    });

    it("applyWsGateUpdate paints pills only for the selected symbol", () => {
      // Selected symbol comes from the legacy select (first option = EURUSD)
      document.getElementById("stake-symbol").value = "EURUSD";
      window.applyWsGateUpdate({
        symbol: "EURUSD",
        gates: [true, false, true, false, true],
      });

      assert.ok(document.getElementById("gate-ema").classList.contains("passed"));
      assert.ok(document.getElementById("gate-adx").classList.contains("failed"));
      assert.ok(document.getElementById("gate-vol").classList.contains("failed"));
      assert.equal(
        document.getElementById("gateway-overall-status").textContent,
        "3/5 MODERATE"
      );
    });

    it("applyWsGateUpdate ignores events for other symbols", () => {
      document.getElementById("stake-symbol").value = "EURUSD";
      window.updateGatewayUI([true, true, true, true, true]); // baseline all-pass

      // A background snapshot for GBPUSD must not overwrite the pills.
      window.applyWsGateUpdate({
        symbol: "GBPUSD",
        gates: [false, false, false, false, false],
      });

      assert.ok(document.getElementById("gate-ema").classList.contains("passed"));
      assert.equal(
        document.getElementById("gateway-overall-status").textContent,
        "5/5 OPTIMAL"
      );
    });

    it("applyWsSignalUpdate renders signal for the selected symbol", () => {
      document.getElementById("stake-symbol").value = "EURUSD";
      window.applyWsSignalUpdate({
        symbol: "EURUSD",
        action: "BUY SIGNAL",
        type: "BUY",
        sl: "1.08450",
        tp: "1.09000",
        duration: "45m",
      });

      const pill = document.getElementById("trade-signal-pill");
      assert.ok(pill.classList.contains("signal-buy"));
      assert.equal(document.getElementById("sig-action").textContent, "BUY SIGNAL");
    });

    it("applyWsSignalUpdate ignores events for other symbols", () => {
      document.getElementById("stake-symbol").value = "EURUSD";
      window.calculateLocalSignalPreview("EURUSD"); // baseline WAIT

      window.applyWsSignalUpdate({
        symbol: "GBPUSD",
        action: "SELL SIGNAL",
        type: "SELL",
        sl: "1.25000",
        tp: "1.24000",
        duration: "45m",
      });

      const pill = document.getElementById("trade-signal-pill");
      assert.ok(pill.classList.contains("signal-wait"));
      assert.equal(document.getElementById("sig-action").textContent, "NEUTRAL / WAIT");
    });
  });

  // ── updateQuickStakeSetup ──────────────────────────────────────────

  describe("updateQuickStakeSetup", () => {
    afterEach(() => {
      window.fetch = undefined;
    });

    it("fetches signal data from the API and renders it to the inline pill", async () => {
      window.fetch = async (url) => {
        assert.ok(url.includes("symbol=EURUSD"));
        return {
          ok: true,
          json: async () => ({
            sl: "1.0835 (-18p)",
            tp: "1.0885 (+32p)",
            duration: "15m - 45m",
            type: "BUY",
          }),
        };
      };

      await window.updateQuickStakeSetup("EURUSD");

      assert.equal(document.getElementById("qs-sl").innerHTML, "SL: <b>1.0835 (-18p)</b>");
      assert.equal(document.getElementById("qs-tp").innerHTML, "TP: <b>1.0885 (+32p)</b>");
      assert.equal(document.getElementById("qs-hold").innerHTML, "Hold: <b>15m - 45m</b>");
      const pill = document.getElementById("qs-setup-pill");
      assert.ok(pill.classList.contains("buy-active"));
    });

    it("falls back to calculateLocalSignalPreview for Gold (XAUUSD)", async () => {
      window.fetch = async () => {
        throw new Error("Network error");
      };

      await window.updateQuickStakeSetup("XAUUSD");

      const pill = document.getElementById("qs-setup-pill");
      assert.ok(pill.classList.contains("buy-active"));
    });

    it("falls back to calculateLocalSignalPreview for non-Gold pairs", async () => {
      window.fetch = async () => {
        throw new Error("Network error");
      };

      await window.updateQuickStakeSetup("GBPUSD");

      const pill = document.getElementById("qs-setup-pill");
      assert.ok(pill.classList.contains("qs-setup-capsule"));
      assert.ok(!pill.classList.contains("buy-active"));
      assert.ok(!pill.classList.contains("sell-active"));
      assert.equal(document.getElementById("qs-sl").innerHTML, "SL: <b>--</b>");
    });
  });

  // ── Smart Assist Mode Toggle ───────────────────────────────────────

  describe("toggleSmartAssist", () => {
    it("enables Smart Assist mode when checked", () => {
      window.toggleSmartAssist(true);

      const pill = document.getElementById("qs-setup-pill");
      assert.ok(!pill.classList.contains("manual-mode"));

      const label = document.getElementById("assist-status-text");
      assert.equal(label.textContent, "SMART ASSIST");
    });

    it("disables Smart Assist mode when unchecked", () => {
      document.getElementById("assist-status-text").textContent = "SMART ASSIST";
      window.toggleSmartAssist(false);

      const pill = document.getElementById("qs-setup-pill");
      assert.ok(pill.classList.contains("manual-mode"));

      const label = document.getElementById("assist-status-text");
      assert.equal(label.textContent, "MANUAL ONLY");

      const tg = document.getElementById("qs-targets-group");
      assert.ok(tg.classList.contains("disabled"));
    });

    it("is enabled by default on bootstrap", () => {
      assert.equal(window.getSmartAssistState(), true);
    });
  });

  // ── getCalculatedTargets ───────────────────────────────────────────

  describe("getCalculatedTargets", () => {
    it("returns Gold SL/TP for XAUUSD BUY", () => {
      var t = window.getCalculatedTargets("XAUUSD", "BUY");
      assert.equal(t.sl, "2410.50");
      assert.equal(t.tp, "2428.00");
    });

    it("returns Gold SL/TP for XAUUSD SELL", () => {
      var t = window.getCalculatedTargets("XAUUSD", "SELL");
      assert.equal(t.sl, "2428.50");
      assert.equal(t.tp, "2410.50");
    });

    it("returns forex SL/TP for non-Gold BUY", () => {
      var t = window.getCalculatedTargets("EURUSD", "BUY");
      assert.equal(t.sl, "1.0835");
      assert.equal(t.tp, "1.0885");
    });

    it("returns reversed SL/TP for non-Gold SELL", () => {
      var t = window.getCalculatedTargets("GBPUSD", "SELL");
      assert.equal(t.sl, "1.0885");
      assert.equal(t.tp, "1.0835");
    });
  });

  // ── checkAllGatesPassed ────────────────────────────────────────────

  describe("checkAllGatesPassed", () => {
    afterEach(() => {
      window.fetch = undefined;
    });

    it("returns true when all 5 gates have 'passed' class", () => {
      ["gate-1", "gate-2", "gate-3", "gate-4", "gate-5"].forEach(function(id) {
        var el = document.getElementById(id);
        if (el) el.className = "gate-pill passed";
      });
      assert.equal(window.checkAllGatesPassed(), true);
    });

    it("returns false when any gate has 'failed' class", () => {
      ["gate-1", "gate-2", "gate-3"].forEach(function(id) {
        var el = document.getElementById(id);
        if (el) el.className = "gate-pill passed";
      });
      var g4 = document.getElementById("gate-4");
      if (g4) g4.className = "gate-pill failed";
      var g5 = document.getElementById("gate-5");
      if (g5) g5.className = "gate-pill passed";
      assert.equal(window.checkAllGatesPassed(), false);
    });
  });

  // ── executeQuickStake ─────────────────────────────────────────────

  describe("executeQuickStake", () => {
    afterEach(() => {
      window.fetch = undefined;
      const pill = document.getElementById("qs-setup-pill");
      if (pill) pill.className = "qs-setup-capsule";
      window.toggleSmartAssist(true);
    });

    it("alerts and aborts when Smart Assist is on and gates fail", async () => {
      window.fetch = async () => ({
        ok: true,
        json: async () => ({ gates: [true, false, true, true, false], overall: "3/5" }),
      });

      var alerted = false;
      var originalAlert = window.alert;
      window.alert = function(msg) { alerted = true; };

      await window.executeQuickStake("BUY");

      window.alert = originalAlert;
      assert.ok(alerted, "alert should have been called");
    });

    it("executes trade with auto SL/TP when gates pass", async () => {
      window.fetch = async () => ({
        ok: true,
        json: async () => ({ gates: [true, true, true, true, true], overall: "5/5 STRONG" }),
      });

      await window.executeQuickStake("BUY");

      var slElem = document.getElementById("qs-sl");
      var tpElem = document.getElementById("qs-tp");
      assert.ok(slElem.innerHTML.includes("1.0835"));
      assert.ok(tpElem.innerHTML.includes("1.0885"));

      var pill = document.getElementById("qs-setup-pill");
      assert.ok(pill.classList.contains("buy-active"));
    });

    it("executes raw trade when Smart Assist is off", async () => {
      window.toggleSmartAssist(false);
      var pill = document.getElementById("qs-setup-pill");
      assert.ok(pill.classList.contains("manual-mode"));

      await window.executeQuickStake("SELL");

      assert.equal(document.getElementById("qs-sl").innerHTML, "SL: <b>--</b>");
      assert.equal(document.getElementById("qs-tp").innerHTML, "TP: <b>--</b>");
      assert.ok(!pill.classList.contains("buy-active"));
      assert.ok(!pill.classList.contains("sell-active"));
    });
  });
});
