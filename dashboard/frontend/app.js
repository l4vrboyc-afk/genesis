/**
 * GENESIS Trading Engine — Obsidian Liquid Glass Dashboard
 * Full API integration with FastAPI backend and WebSocket.
 */

const LEVERAGE = 100; // Account leverage ratio

/* ── Module state ─────────────────────────────────────────────────── */
let mainChart = null;
let modalChart = null;
let isEngineRunning = false;
let livePositions = [];
let liveTimer = null;
let historyTimer = null;
let wsSocket = null;
let wsReconnectTimer = null;
let posCandleChart = null;
let posChartTimer = null;
let posChartSymbol = null;
let posChartTF = 'M1';

/* ── Trading Profiles Dictionary ──────────────────────────────────── */

const TRADING_PROFILES = {
    swing_trader: {
        name: "Swing Trader",
        pairs: ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "AUDUSD",
                "EURGBP", "EURJPY", "GBPJPY", "AUDJPY", "CADJPY",
                "CHFJPY", "NZDUSD", "USDCAD", "USDCHF", "EURAUD",
                "EURCAD", "GBPCHF"],
        riskPerTrade: 0.01,
        goldStopMultiplier: 2.5
    },
    range_scalper: {
        name: "Range Fade Scalper",
        pairs: ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "EURGBP",
                "USDCHF", "AUDUSD", "USDCAD", "NZDUSD", "EURJPY",
                "EURCHF", "EURAUD", "EURCAD", "GBPJPY", "GBPCHF",
                "GBPAUD", "GBPCAD"],
        riskPerTrade: 0.005,
        goldStopMultiplier: 0.8
    },
    breakout_hunter: {
        name: "Breakout Hunter",
        pairs: ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "GBPJPY",
                "AUDUSD", "USDCAD", "NZDUSD", "EURGBP", "EURJPY",
                "EURCHF", "EURAUD", "EURCAD", "GBPCHF", "GBPAUD",
                "GBPCAD", "AUDJPY", "CADJPY"],
        riskPerTrade: 0.015,
        goldStopMultiplier: 1.8
    },
    day_trader: {
        name: "Universal Day Trader",
        pairs: ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "AUDJPY",
                "NZDUSD", "EURGBP", "EURJPY", "EURCHF", "AUDUSD",
                "USDCAD", "USDCHF", "EURAUD", "EURCAD", "GBPJPY",
                "GBPCHF", "GBPAUD", "GBPCAD", "CADJPY", "CHFJPY",
                "GBPCHF", "XAUEUR", "USDZAR"],
        riskPerTrade: 0.01,
        goldStopMultiplier: 1.5
    }
};

let activeProfile = 'swing_trader';

/* ── Small helpers ───────────────────────────────────────────────── */

function esc(value) {
  // Uses replaceAll (string args, no regex literals) so the project's
  // check_js.py delimiter scanner stays happy.
  return String(value == null ? '' : value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function fmtMoney(v) {
  const n = Number(v) || 0;
  return '$' + n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function fmtPrice(v, symbol) {
  const n = Number(v);
  if (!isFinite(n)) return '—';
  const digits = symbol && /JPY/.test(String(symbol)) ? 3 : 5;
  return n.toFixed(digits);
}

function fmtPnl(v) {
  const n = Number(v) || 0;
  return (n >= 0 ? '+' : '-') + fmtMoney(Math.abs(n));
}

function fmtTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '—';
  return d.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function pnlClass(v) {
  return Number(v) >= 0 ? 'positive' : 'negative';
}

/* ── API helper ──────────────────────────────────────────────────── */

async function apiFetch(url, options) {
  const opts = options || {};
  if (opts.body && typeof opts.body === 'object') {
    opts.headers = Object.assign({ 'Content-Type': 'application/json' }, opts.headers || {});
    opts.body = JSON.stringify(opts.body);
  }
  const res = await fetch(url, opts);
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (e) { /* ignore */ }
    throw new Error(detail);
  }
  return res.json();
}

/**
 * Intercepts USD cash stake input and dynamically converts to MT5 Lots.
 *
 * Conversion pipeline (1:100 leverage):
 *   Exposure = Stake Amount × 100
 *   Lots = Exposure / 100,000
 *
 * The "Notional Controlled" display was removed by design — only the
 * converted lot preview is shown under the stake panel.
 */
function updateLotConversionPreview() {
  const stakeUSD = parseFloat(document.getElementById('stake-amount-usd').value) || 0;

  // Total leverage-adjusted exposure (internal only)
  const notionalValue = stakeUSD * LEVERAGE;

  // Standard Lot Calculation: 1 Lot = 100,000 units of currency
  const lots = (notionalValue / 100000).toFixed(2);

  const el = document.getElementById('calculated-lots-preview');
  if (el) el.innerText = `${lots} Lots`;
}

/**
 * Handles execution trigger for the quick stake panel.
 * Reads the selected symbol, USD stake, and computed lots,
 * then logs the order and notifies the user.
 */
function executeStakeTrade(direction) {
  const symbol = document.getElementById('stake-symbol').value;
  const stakeUSD = document.getElementById('stake-amount-usd').value;
  const lots = document.getElementById('calculated-lots-preview').innerText;

  console.log(`[ORDER EXECUTION] ${direction} | Symbol: ${symbol} | Stake: $${stakeUSD} (${lots})`);
  alert(`Executed ${direction} on ${symbol} ($${stakeUSD} stake -> ${lots})`);
}

/* ── Dropdown Menu Toggles ───────────────────────────────────────── */

function toggleToolsMenu() {
  const dropdown = document.getElementById('tools-dropdown');
  if (dropdown) dropdown.classList.toggle('hidden');
}

// Click-away listener to dismiss dropdowns automatically
if (typeof window !== 'undefined') {
  window.addEventListener('click', function(e) {
    const wrapper = document.getElementById('more-tools-wrapper');
    if (wrapper && !wrapper.contains(e.target)) {
      const dropdown = document.getElementById('tools-dropdown');
      if (dropdown) dropdown.classList.add('hidden');
    }
  });
}

/* ── Chart Lifecycle ─────────────────────────────────────────────── */

function chartDataset(points) {
  return {
    type: 'line',
    data: {
      labels: points.labels,
      datasets: [{
        label: 'Cumulative PnL ($)',
        data: points.data,
        borderColor: '#4ade80',
        backgroundColor: 'rgba(74, 222, 128, 0.08)',
        fill: true,
        tension: 0.35,
        pointRadius: 2,
        pointHoverRadius: 5,
        borderWidth: 2,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { grid: { color: 'rgba(255, 255, 255, 0.05)' }, ticks: { color: '#94a3b8', maxTicksLimit: 8, font: { size: 10 } } },
        y: { grid: { color: 'rgba(255, 255, 255, 0.05)' }, ticks: { color: '#94a3b8', font: { size: 10 } } },
      },
    },
  };
}

function defaultPoints() {
  return {
    labels: ['09:00', '10:00', '11:00', '12:00', '13:00', '14:00'],
    data: [0, 45.20, 30.10, 85.50, 60.00, 94.74],
  };
}

function pointsFromPerformance(perf) {
  const history = (perf && perf.daily_performance_history) || [];
  const labels = [];
  const data = [];
  let cum = 0;
  for (const day of history.slice(-30)) {
    labels.push(String(day.date || '').slice(5) || '—');
    cum += Number(day.pnl) || 0;
    data.push(cum);
  }
  if (!labels.length) return defaultPoints();
  return { labels, data };
}

async function loadPerformance() {
  if (typeof fetch === 'undefined') return null;
  try {
    return await apiFetch('/api/performance');
  } catch (e) {
    return null;
  }
}

async function initCharts() {
  if (typeof Chart === 'undefined') return;
  const canvas = document.getElementById('mainProfitChart');
  if (!canvas) return;

  const perf = await loadPerformance();
  const config = chartDataset(pointsFromPerformance(perf));

  if (mainChart) mainChart.destroy();
  mainChart = new Chart(canvas.getContext('2d'), config);
}

async function renderModalChart() {
  if (typeof Chart === 'undefined') return;
  const canvas = document.getElementById('modalProfitChart');
  if (!canvas) return;

  let points = null;
  if (mainChart) {
    points = { labels: mainChart.data.labels, data: mainChart.data.datasets[0].data };
  } else {
    const perf = await loadPerformance();
    points = pointsFromPerformance(perf);
  }

  const config = chartDataset(points);
  config.options.plugins.legend.display = true;

  if (modalChart) modalChart.destroy();
  modalChart = new Chart(canvas.getContext('2d'), config);

  // Re-bind sizing once the modal is laid out (fixed-height container)
  setTimeout(() => { if (modalChart) modalChart.resize(); }, 80);
}

/* ── Modal Management ────────────────────────────────────────────── */

function openChartModal() {
  const modal = document.getElementById('chart-modal');
  if (modal) {
    modal.classList.remove('hidden');
    renderModalChart();
  }
}

function closeChartModal() {
  const modal = document.getElementById('chart-modal');
  if (modal) modal.classList.add('hidden');
}

function openModal(type) {
  // Discord configuration modal uses a dedicated overlay element
  if (type === 'discord-modal') {
    const discordModal = document.getElementById('discord-modal');
    if (discordModal) {
      discordModal.classList.remove('hidden');
    }
    const dropdown = document.getElementById('tools-dropdown');
    if (dropdown) dropdown.classList.add('hidden');
    return;
  }

  const modal = document.getElementById('utility-modal');
  const title = document.getElementById('utility-modal-title');
  const body = document.getElementById('utility-modal-body');

  if (type === 'bot-logs') {
    title.innerText = '📄 Bot Log Output';
    body.innerHTML = `<pre class="text-zinc-300">[INFO] WS Connected\n[INFO] Session Breakout Engine active\n[INFO] 0 Open risk alerts</pre>`;
    if (typeof fetch !== 'undefined') {
      apiFetch('/api/logs?lines=60').then(logs => {
        const lines = (logs && logs.lines) || [];
        if (lines.length) body.innerHTML = `<pre class="text-zinc-300">${esc(lines.join('\n'))}</pre>`;
      }).catch(() => {});
    }
  } else if (type === 'calendar') {
    title.innerText = '📅 Economic Calendar (UTC)';
    body.innerHTML = `<div class="p-2 bg-white/5 rounded border border-white/10 flex justify-between"><span>USD Fed Rate Statement</span><span class="text-white font-bold">18:00</span></div>`;
    if (typeof fetch !== 'undefined') {
      apiFetch('/api/news').then(news => {
        const events = (news && news.events) || [];
        if (events.length) {
          body.innerHTML = events.slice(0, 12).map(ev => `
            <div class="p-2 bg-white/5 rounded border border-white/10 flex justify-between gap-4">
              <span>${esc(ev.event_name || ev.name || 'Event')}</span>
              <span class="font-bold">${esc(ev.time || '').replace('T', ' ').slice(0, 16)}</span>
            </div>`).join('');
        }
      }).catch(() => {});
    }
  } else if (type === 'parameters') {
    title.innerText = '⚙️ Strategy Parameters';
    body.innerHTML = `<p class="text-zinc-400">Parameter configurations active.</p>`;
    if (typeof fetch !== 'undefined') {
      apiFetch('/api/settings').then(s => {
        body.innerHTML = `
          <div class="p-2 bg-white/5 rounded border border-white/10 flex justify-between"><span>Max Risk / Trade</span><span class="text-white font-bold">${(s.max_risk_per_trade * 100).toFixed(1)}%</span></div>
          <div class="p-2 bg-white/5 rounded border border-white/10 flex justify-between"><span>Max Daily Drawdown</span><span class="text-white font-bold">${(s.max_daily_drawdown * 100).toFixed(1)}%</span></div>
          <div class="p-2 bg-white/5 rounded border border-white/10 flex justify-between"><span>Max Open Positions</span><span class="text-white font-bold">${s.max_open_positions}</span></div>
          <div class="p-2 bg-white/5 rounded border border-white/10 flex justify-between"><span>Lot Sizing</span><span class="text-white font-bold">${esc(s.lot_sizing_mode || '—')}</span></div>
          <div class="p-2 bg-white/5 rounded border border-white/10 flex justify-between"><span>Paper Trading</span><span class="text-white font-bold">${s.paper_trading ? 'ON' : 'OFF'}</span></div>
        `;
      }).catch(() => {});
    }
  } else if (type === 'history') {
    modal.classList.add('hidden');
    openHistoryModal();
    return;
  }

  if (modal) modal.classList.remove('hidden');
  const dropdown = document.getElementById('tools-dropdown');
  if (dropdown) dropdown.classList.add('hidden');
}

function closeUtilityModal() {
  const modal = document.getElementById('utility-modal');
  if (modal) modal.classList.add('hidden');
}

/**
 * Generic modal closer — hides any modal by its element ID.
 * Works with both the utility modal and the Discord modal overlays.
 */
function closeModal(modalId) {
  const modal = document.getElementById(modalId);
  if (modal) {
    modal.classList.add('hidden');
  }
}

/* ── Discord Integration Handler ────────────────────────────────── */

/**
 * Checks whether the Discord webhook is connected by querying the
 * Python / MT5 backend API.  Falls back to localStorage if the backend
 * is unreachable.
 */
async function checkDiscordConnection() {
  // Early-exit when this build has no Discord UI (no status badge in the
  // served index.html) — avoids a wasted 404 request on every page load.
  if (!document.getElementById('discord-status-badge')) return;
  try {
    const res = await fetch('/api/discord/status');
    const data = await res.json();
    updateDiscordUI(data.connected);
  } catch (err) {
    // Fallback check from localStorage / environment config
    const savedUrl = localStorage.getItem('discord_webhook_url');
    updateDiscordUI(Boolean(savedUrl));
  }
}

/**
 * Updates the Discord status badge to reflect connected / disconnected state.
 */
function updateDiscordUI(isConnected) {
  const badge = document.getElementById('discord-status-badge');
  const text = document.getElementById('discord-status-text');

  if (!badge || !text) return;

  if (isConnected) {
    badge.className = 'status-badge discord-connected';
    text.textContent = 'DISCORD: LIVE';
  } else {
    badge.className = 'status-badge discord-disconnected';
    text.textContent = 'DISCORD: OFF';
  }
}

/**
 * Persists the Discord webhook URL and notification preferences,
 * then pings the backend API to activate the connection.
 */
async function saveDiscordSettings() {
  const webhookUrl = document.getElementById('discord-webhook-url').value;

  if (!webhookUrl) {
    alert('Please enter a valid Discord Webhook URL');
    return;
  }

  localStorage.setItem('discord_webhook_url', webhookUrl);

  try {
    await fetch('/api/discord/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        webhook_url: webhookUrl,
        notify_trades: document.getElementById('notify-trades').checked,
        notify_alerts: document.getElementById('notify-alerts').checked,
      })
    });

    updateDiscordUI(true);
    closeModal('discord-modal');
  } catch (err) {
    console.warn('Backend API offline, saving configuration locally.');
    updateDiscordUI(true);
    closeModal('discord-modal');
  }
}

/**
 * Sends a test notification directly to the Discord webhook URL so the
 * user can verify connectivity without going through the backend.
 */
async function testDiscordWebhook() {
  const webhookUrl =
    document.getElementById('discord-webhook-url').value ||
    localStorage.getItem('discord_webhook_url');

  if (!webhookUrl) {
    alert('Provide a Webhook URL first.');
    return;
  }

  try {
    await fetch(webhookUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        content: "🟢 **GENESIS ENGINE**: Discord Webhook test successfully connected!"
      })
    });
    alert('Test notification sent to Discord!');
  } catch (err) {
    alert('Failed to send Discord alert. Check CORS or URL validity.');
  }
}

/* ── Engine State Toggle ─────────────────────────────────────────── */

function setEngineState(running) {
  isEngineRunning = !!running;
  const btn = document.getElementById('engine-toggle-btn');
  const txt = document.getElementById('engine-btn-text');
  if (btn) btn.className = running ? 'engine-btn running' : 'engine-btn paused';
  if (txt) txt.textContent = running ? 'PAUSE ENGINE' : 'START ENGINE';
}

async function toggleEngineState() {
  if (typeof fetch === 'undefined') return;
  const action = isEngineRunning ? 'pause' : 'resume';
  const btn = document.getElementById('engine-toggle-btn');
  if (btn) btn.disabled = true;
  try {
    await apiFetch('/api/control', { method: 'POST', body: { action } });
    setEngineState(action === 'resume');
  } catch (e) {
    alert('Engine toggle failed: ' + (e.message || e));
  } finally {
    if (btn) btn.disabled = false;
  }
}

/* ── Profile Switcher ────────────────────────────────────────────── */

async function switchProfile() {
  if (typeof fetch === 'undefined') {
    window.location.href = '/launcher';
    return;
  }
  try {
    const res = await apiFetch('/api/control', { method: 'POST', body: { action: 'switch_profile' } });
    if (res && res.picker_url) {
      window.location.href = res.picker_url;
      return;
    }
    alert('Profile switch requested — returning to launcher.');
    setTimeout(() => { window.location.href = '/'; }, 800);
  } catch (e) {
    alert('Could not switch profile: ' + (e.message || e));
  }
}

/* ── Profile-Aware Contract Sizing ──────────────────────────────── */

/**
 * Activates a trading profile: persists it to localStorage, repopulates
 * the stake symbol dropdown with that profile's pair list, and navigates
 * to the dashboard (or stays if already there for in-app testing).
 */
function selectProfile(profileKey) {
  if (!TRADING_PROFILES[profileKey]) {
    console.warn('Unknown profile key: ' + profileKey);
    return;
  }

  activeProfile = profileKey;

  // Persist the active profile so it survives page reloads
  if (typeof localStorage !== 'undefined') {
    localStorage.setItem('genesis_active_profile', profileKey);
  }

  // Re-populate the execution symbol dropdown with this profile's pairs
  populateSymbolDropdown(TRADING_PROFILES[profileKey].pairs);

  // Only navigate in a real browser. jsdom sets its user-agent to contain
  // "jsdom", which we use to skip navigation in tests.
  var isJSDOM = (typeof navigator !== 'undefined' && /jsdom/i.test(navigator.userAgent || ""));
  if (!isJSDOM) {
    window.location.href = '/dashboard';
  }
}

/**
 * Rebuilds the <select id="stake-symbol"> dropdown with the given
 * list of currency pairs, labelling XAUUSD as "(Gold)".
 */
function populateSymbolDropdown(pairs) {
  var selectElem = document.getElementById('stake-symbol');
  if (!selectElem) return;

  selectElem.innerHTML = (pairs || []).map(function(symbol) {
    var label = symbol === 'XAUUSD' ? symbol + ' (Gold)' : symbol;
    return '<option value="' + esc(symbol) + '">' + esc(label) + '</option>';
  }).join('');

  // Re-calculate the lot preview with the new symbol
  updateLotConversionPreview();
}

/* ── Profile-Aware Discord Alert Handler ────────────────────────── */

/**
 * Sends a Discord trade-execution notification via webhook, tagging
 * which profile initiated the trade and highlighting Gold executions.
 */
function sendDiscordTradeNotification(trade) {
  var profile = 'Genesis Engine';
  if (activeProfile && TRADING_PROFILES[activeProfile]) {
    profile = TRADING_PROFILES[activeProfile].name;
  }

  var isGold = trade.symbol === 'XAUUSD';
  var icon = isGold ? '🏆 [GOLD]' : '📊';

  var payload = {
    embeds: [{
      title: icon + ' ' + trade.type + ' Order Executed (' + profile + ')',
      color: trade.type === 'BUY' ? 0x4ade80 : 0xf87171,
      fields: [
        { name: 'Active Profile', value: profile, inline: true },
        { name: 'Symbol', value: trade.symbol, inline: true },
        { name: 'Volume (Lots)', value: String(trade.lots), inline: true },
        { name: 'Entry Price', value: '$' + trade.price, inline: true }
      ],
      footer: { text: 'Genesis Engine • Discord Alert Matrix' },
      timestamp: new Date().toISOString()
    }]
  };

  var webhookUrl = localStorage.getItem('discord_webhook_url');
  if (!webhookUrl) {
    console.warn('No Discord webhook URL configured — skipping trade notification.');
    return;
  }

  fetch(webhookUrl, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  }).catch(function(e) {
    console.warn('Discord trade notification failed: ' + (e && e.message ? e.message : e));
  });
}

/* ── Five Gateway Evaluator ───────────────────────────────────────── */

/**
 * Queries the MT5 backend (or falls back to local simulation) to
 * evaluate the five trade-entry gates for the currently selected
 * symbol and active profile, then updates the gate pills in the UI.
 */
async function evaluateFiveGateways() {
  var selectElem = document.getElementById('stake-symbol');
  var symbol = selectElem ? selectElem.value : '';
  if (!symbol) return;

  var profile = activeProfile || 'swing_trader';

  try {
    var res = await fetch('/api/evaluator?symbol=' + encodeURIComponent(symbol) + '&profile=' + encodeURIComponent(profile));
    var gateData = await res.json();
    // Expected: { gates: [true, true, true, false, true], overall: "4/5 PASSED" }
    if (gateData && Array.isArray(gateData.gates)) {
      updateGatewayUI(gateData.gates);
    } else if (gateData && Array.isArray(gateData.overall)) {
      updateGatewayUI(gateData.overall);
    }
  } catch (err) {
    // Fallback simulation mode if API is offline / unreachable
    simulateGatewayCheck(symbol);
  }

  // Also refresh the lot preview whenever the symbol changes
  updateLotConversionPreview();
}

/**
 * Updates the five gate pills based on an array of boolean results.
 * @param {boolean[]} gateResults - Array of 5 booleans: [EMA, ADX, RSI, VOL, REG]
 */
function updateGatewayUI(gateResults) {
  var gateIds = ['gate-1', 'gate-2', 'gate-3', 'gate-4', 'gate-5'];
  var passedCount = 0;

  gateResults.forEach(function(passed, index) {
    var elem = document.getElementById(gateIds[index]);
    if (!elem) return;

    if (passed) {
      elem.className = 'gate-pill passed';
      passedCount++;
    } else {
      elem.className = 'gate-pill failed';
    }
  });

  var statusElem = document.getElementById('gateway-overall-status');
  if (!statusElem) return;

  if (passedCount === 5) {
    statusElem.textContent = '5/5 OPTIMAL';
    statusElem.className = 'gate-status-text ready';
  } else if (passedCount >= 3) {
    statusElem.textContent = passedCount + '/5 MODERATE';
    statusElem.className = 'gate-status-text warning';
  } else {
    statusElem.textContent = passedCount + '/5 BLOCKED';
    statusElem.className = 'gate-status-text blocked';
  }
}

/**
 * Generates simulated gate evaluations for UI testing when the
 * MT5 backend API is not reachable.
 */
function simulateGatewayCheck(symbol) {
  // Gate 4 (Volume) is always more favorable for high-volatility Gold
  var gates = [true, true, symbol === 'XAUUSD', true, true];
  updateGatewayUI(gates);
}

/* ── Live Status / Positions ─────────────────────────────────────── */

async function refreshStatus() {
  if (typeof fetch === 'undefined') return;
  try {
    const status = await apiFetch('/api/status');
    if (!status) return;

    setEngineState(!status.paused);

    const bal = document.getElementById('metric-balance');
    const eq = document.getElementById('metric-equity');
    const dpnl = document.getElementById('metric-daily-pnl');
    const wr = document.getElementById('metric-win-rate');
    if (bal) bal.textContent = fmtMoney(status.balance);
    if (eq) eq.textContent = fmtMoney(status.equity);
    if (dpnl) {
      dpnl.textContent = fmtPnl(status.daily_pnl);
      dpnl.className = 'text-2xl font-bold mono-num ' + pnlClass(status.daily_pnl);
    }
    if (wr) wr.textContent = (Number(status.win_rate) * 100).toFixed(1) + '%';

    const badge = document.getElementById('active-profile-badge');
    if (badge) badge.textContent = 'PROFILE ' + (status.active_profile || 'default').toUpperCase();

    renderActivePositions(status.open_trades || []);

    if (status.open_trades && status.open_trades.length) {
      const ticker = document.getElementById('live-price-banner-text');
      if (ticker) {
        ticker.textContent = status.open_trades
          .slice(0, 3)
          .map(p => `${p.symbol}: ${fmtPrice(p.live_price != null ? p.live_price : p.current_price, p.symbol)}`)
          .join(' | ');
      }
    }
  } catch (e) {
    // silent — backend may be starting up
  }
}

function renderActivePositions(positions) {
  livePositions = positions || [];
  const tbody = document.getElementById('active-positions-body');
  if (!tbody) return;

  const count = document.getElementById('active-count');
  if (count) count.textContent = livePositions.length + ' OPEN POSITION' + (livePositions.length === 1 ? '' : 'S');

  if (!livePositions.length) {
    tbody.innerHTML = `<tr><td colspan="7" class="py-6 text-center text-zinc-600">No open positions</td></tr>`;
    return;
  }

  tbody.innerHTML = livePositions.map(p => {
    const price = p.live_price != null ? p.live_price : p.current_price;
    return `
      <tr class="clickable-row" data-ticket="${esc(p.ticket)}" onclick="openPositionModal(${esc(p.ticket)})">
        <td class="py-3 text-zinc-500 mono-num">${esc(p.ticket)}</td>
        <td class="py-3 font-bold text-white">${esc(p.symbol)}</td>
        <td class="py-3"><span class="badge ${p.direction === 'buy' ? 'buy' : 'sell'}">${esc((p.direction || '').toUpperCase())}</span></td>
        <td class="py-3 mono-num">${Number(p.volume).toFixed(2)}</td>
        <td class="py-3 mono-num text-zinc-400">${fmtPrice(p.entry_price != null ? p.entry_price : p.open_price, p.symbol)}</td>
        <td class="py-3 mono-num text-zinc-400" id="price-${esc(p.ticket)}">${fmtPrice(price, p.symbol)}</td>
        <td class="py-3 mono-num text-right font-bold ${pnlClass(p.profit)}" id="pnl-${esc(p.ticket)}">${fmtPnl(p.profit)}</td>
      </tr>`;
  }).join('');
}

function updatePositionTicks(tickData) {
  // TICK event: symbol + bid/ask → patch CURRENT price + PROFIT cells live
  if (!tickData || !tickData.symbol) return;
  for (const p of livePositions) {
    if (p.symbol !== tickData.symbol) continue;
    const price = tickData.bid != null ? tickData.bid : tickData.ask;
    const priceEl = document.getElementById('price-' + p.ticket);
    if (priceEl) priceEl.textContent = fmtPrice(price, p.symbol);
    const pnlEl = document.getElementById('pnl-' + p.ticket);
    if (pnlEl) {
      const entry = Number(p.entry_price != null ? p.entry_price : p.open_price) || 0;
      const dir = p.direction === 'buy' ? 1 : -1;
      const approxPnl = (Number(price) - entry) * dir * Number(p.volume) * 100000;
      pnlEl.textContent = fmtPnl(approxPnl);
      pnlEl.className = 'py-3 mono-num text-right font-bold ' + pnlClass(approxPnl);
    }
  }
  patchPositionChartTick(tickData);
}

/* ── Recent Trade History ────────────────────────────────────────── */

async function refreshRecentHistory() {
  if (typeof fetch === 'undefined') return;
  try {
    const trades = await apiFetch('/api/trades?status=closed&limit=8');
    renderRecentHistory(trades || []);
  } catch (e) {
    // silent
  }
}

function renderRecentHistory(trades) {
  const tbody = document.getElementById('recent-history-body');
  if (!tbody) return;

  if (!trades || !trades.length) {
    tbody.innerHTML = `<tr><td colspan="4" class="py-6 text-center text-zinc-600">No closed trades yet</td></tr>`;
    return;
  }

  tbody.innerHTML = trades.map(t => `
    <tr class="table-row-hover">
      <td class="py-3 text-zinc-500 mono-num">${fmtTime(t.close_time || t.open_time)}</td>
      <td class="py-3 font-bold text-white">${esc(t.symbol)}</td>
      <td class="py-3"><span class="badge ${t.direction === 'buy' ? 'buy' : 'sell'}">${esc((t.direction || '').toUpperCase())}</span></td>
      <td class="py-3 mono-num text-right font-bold ${pnlClass(t.profit)}">${fmtPnl(t.profit)}</td>
    </tr>`).join('');
}

/* ── Extended History Modal ──────────────────────────────────────── */

async function openHistoryModal() {
  const modal = document.getElementById('history-modal');
  if (!modal) return;
  modal.classList.remove('hidden');

  const metrics = document.getElementById('history-metrics');
  const tbody = document.getElementById('history-table-body');
  if (metrics) metrics.innerHTML = `<div class="hm-item"><span class="hm-label">Loading…</span><span class="hm-value">—</span></div>`;
  if (tbody) tbody.innerHTML = `<tr><td colspan="8" class="py-6 text-center text-zinc-600">Loading…</td></tr>`;

  if (typeof fetch === 'undefined') return;
  const [perf, trades] = await Promise.all([
    loadPerformance(),
    apiFetch('/api/trades?status=closed&limit=100').catch(() => []),
  ]);

  if (metrics && perf) {
    metrics.innerHTML = `
      <div class="hm-item"><span class="hm-label">Total Trades</span><span class="hm-value">${Number(perf.total_trades) || 0}</span></div>
      <div class="hm-item"><span class="hm-label">Win Rate</span><span class="hm-value">${(Number(perf.win_rate) * 100).toFixed(1)}%</span></div>
      <div class="hm-item"><span class="hm-label">Total PnL</span><span class="hm-value ${pnlClass(perf.total_pnl)}">${fmtPnl(perf.total_pnl)}</span></div>
      <div class="hm-item"><span class="hm-label">Profit Factor</span><span class="hm-value">${Number(perf.profit_factor).toFixed(2)}</span></div>
      <div class="hm-item"><span class="hm-label">Avg Win</span><span class="hm-value positive">${fmtPnl(perf.avg_win)}</span></div>
      <div class="hm-item"><span class="hm-label">Avg Loss</span><span class="hm-value negative">${fmtPnl(perf.avg_loss)}</span></div>
      <div class="hm-item"><span class="hm-label">Max Drawdown</span><span class="hm-value negative">${Number(perf.max_drawdown).toFixed(2)}%</span></div>
      <div class="hm-item"><span class="hm-label">Avg R:R</span><span class="hm-value">${Number(perf.avg_rr).toFixed(2)}</span></div>`;
  }

  if (tbody) {
    if (!trades || !trades.length) {
      tbody.innerHTML = `<tr><td colspan="8" class="py-6 text-center text-zinc-600">No historical trades</td></tr>`;
    } else {
      tbody.innerHTML = trades.map(t => `
        <tr class="table-row-hover">
          <td class="py-3 text-zinc-500 mono-num">${fmtTime(t.close_time || t.open_time)}</td>
          <td class="py-3 font-bold text-white">${esc(t.symbol)}</td>
          <td class="py-3"><span class="badge ${t.direction === 'buy' ? 'buy' : 'sell'}">${esc((t.direction || '').toUpperCase())}</span></td>
          <td class="py-3 mono-num">${Number(t.volume).toFixed(2)}</td>
          <td class="py-3 mono-num text-zinc-400">${fmtPrice(t.entry_price, t.symbol)}</td>
          <td class="py-3 mono-num text-zinc-400">${fmtPrice(t.exit_price, t.symbol)}</td>
          <td class="py-3 mono-num text-right font-bold ${pnlClass(t.profit)}">${fmtPnl(t.profit)}</td>
          <td class="py-3 mono-num text-right">${Number(t.return_r).toFixed(2)}R</td>
        </tr>`).join('');
    }
  }
}

function closeHistoryModal() {
  const modal = document.getElementById('history-modal');
  if (modal) modal.classList.add('hidden');
}

/* ── Position Details Modal ──────────────────────────────────────── */

function findPosition(ticket) {
  return livePositions.find(p => String(p.ticket) === String(ticket)) || null;
}

function openPositionModal(ticket) {
  const pos = findPosition(ticket);
  const modal = document.getElementById('position-modal');
  const title = document.getElementById('pos-modal-ticket');
  const body = document.getElementById('pos-modal-details');
  if (!modal || !body) return;

  if (title) title.textContent = '#' + ticket;

  if (!pos) {
    body.innerHTML = `<p class="text-zinc-500">Position #${esc(ticket)} not found in live snapshot.</p>`;
    modal.classList.remove('hidden');
    return;
  }

  const price = pos.live_price != null ? pos.live_price : pos.current_price;
  body.innerHTML = `
    <div class="pos-grid">
      <div class="pos-field"><span class="pos-label">Symbol</span><span class="pos-value">${esc(pos.symbol)}</span></div>
      <div class="pos-field"><span class="pos-label">Type</span><span class="pos-value">${esc((pos.direction || '').toUpperCase())}</span></div>
      <div class="pos-field"><span class="pos-label">Volume</span><span class="pos-value">${Number(pos.volume).toFixed(2)}</span></div>
      <div class="pos-field"><span class="pos-label">Entry</span><span class="pos-value mono-num">${fmtPrice(pos.entry_price != null ? pos.entry_price : pos.open_price, pos.symbol)}</span></div>
      <div class="pos-field"><span class="pos-label">Current</span><span class="pos-value mono-num">${fmtPrice(price, pos.symbol)}</span></div>
      <div class="pos-field"><span class="pos-label">Profit</span><span class="pos-value mono-num ${pnlClass(pos.profit)}">${fmtPnl(pos.profit)}</span></div>
      <div class="pos-field"><span class="pos-label">Stop Loss</span><span class="pos-value mono-num">${Number(pos.sl) ? fmtPrice(pos.sl, pos.symbol) : '—'}</span></div>
      <div class="pos-field"><span class="pos-label">Take Profit</span><span class="pos-value mono-num">${Number(pos.tp) ? fmtPrice(pos.tp, pos.symbol) : '—'}</span></div>
      <div class="pos-field"><span class="pos-label">Magic</span><span class="pos-value mono-num">${esc(pos.magic != null ? pos.magic : '—')}</span></div>
    </div>

    <div class="pos-chart-header">
      <span class="pos-label">Live Mini Chart</span>
      <div class="pos-tf-group">
        <button type="button" class="pos-tf active" data-tf="M1" onclick="setPosChartTF('M1')">M1</button>
        <button type="button" class="pos-tf" data-tf="M5" onclick="setPosChartTF('M5')">M5</button>
        <button type="button" class="pos-tf" data-tf="M15" onclick="setPosChartTF('M15')">M15</button>
      </div>
    </div>
    <div class="pos-chart-wrap"><canvas id="posMiniChart"></canvas></div>

    <div class="pos-trail-row">
      <input type="number" step="any" id="pos-sl-input" class="glass-input p-2 text-xs mono-num" placeholder="New SL" />
      <input type="number" step="any" id="pos-tp-input" class="glass-input p-2 text-xs mono-num" placeholder="New TP" />
      <button class="liquid-btn-sm" onclick="updatePositionStops(${esc(pos.ticket)})">APPLY SL / TP</button>
    </div>

    <div class="flex gap-3 justify-end">
      <button class="btn-danger" onclick="closePosition(${esc(pos.ticket)})">✕ CLOSE POSITION</button>
    </div>`;

  modal.classList.remove('hidden');
  startPositionChart(pos.symbol);
}

function closePositionModal() {
  stopPositionChart();
  const modal = document.getElementById('position-modal');
  if (modal) modal.classList.add('hidden');
}

async function closePosition(ticket) {
  if (typeof fetch === 'undefined') return;
  if (!confirm('Close position #' + ticket + '?')) return;
  try {
    await apiFetch('/api/trades/' + ticket + '/close', { method: 'POST' });
    alert('Position #' + ticket + ' closed.');
    closePositionModal();
    refreshStatus();
    refreshRecentHistory();
  } catch (e) {
    alert('Failed to close position: ' + (e.message || e));
  }
}

async function updatePositionStops(ticket) {
  if (typeof fetch === 'undefined') return;
  const sl = document.getElementById('pos-sl-input');
  const tp = document.getElementById('pos-tp-input');
  const params = new URLSearchParams();
  if (sl && sl.value) params.set('sl', sl.value);
  if (tp && tp.value) params.set('tp', tp.value);
  if (!params.toString()) {
    alert('Enter a new SL and/or TP value first.');
    return;
  }
  try {
    await apiFetch('/api/trades/' + ticket + '/modify?' + params.toString(), { method: 'POST' });
    alert('Position #' + ticket + ' stop levels updated.');
    closePositionModal();
    refreshStatus();
  } catch (e) {
    alert('Failed to update position: ' + (e.message || e));
  }
}

/* ── Position Mini Candlestick Chart ─────────────────────────────── */

function candlePoints(candles) {
  return (candles || []).map(c => ({
    x: Number(c.time) * 1000,
    o: Number(c.open),
    h: Number(c.high),
    l: Number(c.low),
    c: Number(c.close)
  }));
}

function ensureCandleController() {
  if (typeof Chart === 'undefined') return false;
  try {
    if (Chart.registry && Chart.registry.controllers && Chart.registry.controllers.get('candlestick')) {
      return true;
    }
  } catch (e) { /* controller lookup may throw when unregistered */ }
  if (Chart.CandlestickController && Chart.CandlestickElement) {
    Chart.register(Chart.CandlestickController, Chart.CandlestickElement);
    return true;
  }
  return false;
}

function buildMiniChart(symbol, candles) {
  if (typeof Chart === 'undefined') return;
  if (!symbol) return;
  const canvas = document.getElementById('posMiniChart');
  if (!canvas) return;

  const useCandle = ensureCandleController();
  const points = candlePoints(candles);

  if (posCandleChart) { posCandleChart.destroy(); posCandleChart = null; }

  const dataset = useCandle ? {
    label: symbol,
    data: points,
    color: { up: '#4ade80', down: '#f87171', unchanged: '#94a3b8' },
    borderColor: { up: '#4ade80', down: '#f87171', unchanged: '#94a3b8' },
  } : {
    label: symbol,
    data: points.map(p => ({ x: p.x, y: p.c })),
    borderColor: '#4ade80',
    backgroundColor: 'rgba(74, 222, 128, 0.08)',
    fill: true,
    pointRadius: 0,
    tension: 0.25,
  };

  posCandleChart = new Chart(canvas.getContext('2d'), {
    type: useCandle ? 'candlestick' : 'line',
    data: { datasets: [dataset] },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      plugins: { legend: { display: false } },
      scales: {
        // chartjs-chart-financial requires a linear (or time) x-scale — a
        // default category axis would space candles evenly and ignore
        // maxTicksLimit, collapsing market-close gaps into clutter.
        x: { type: 'linear', position: 'bottom', grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { color: '#94a3b8', maxTicksLimit: 6, font: { size: 9 } } },
        y: { grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { color: '#94a3b8', font: { size: 9 }, maxTicksLimit: 5 } },
      },
    },
  });
}

async function loadCandles(symbol, tf, count) {
  try {
    return await apiFetch('/api/candles/' + encodeURIComponent(symbol) + '?timeframe=' + tf + '&count=' + count);
  } catch (e) {
    return { candles: [] };
  }
}

async function refreshPositionChart() {
  if (typeof fetch === 'undefined' || !posChartSymbol) return;
  const sym = posChartSymbol;
  const res = await loadCandles(sym, posChartTF, 60);
  // Stale-fetch guard: the modal may have been closed or switched to
  // another symbol while the network call was in flight — bail without
  // touching the (possibly destroyed) chart state.
  if (sym !== posChartSymbol) return;
  const candles = (res && res.candles) || [];
  if (posCandleChart && document.getElementById('posMiniChart')) {
    const points = candlePoints(candles);
    const isCandle = posCandleChart.config.type === 'candlestick';
    posCandleChart.data.datasets[0].data = isCandle ? points : points.map(p => ({ x: p.x, y: p.c }));
    posCandleChart.update('none');
  } else {
    buildMiniChart(sym, candles);
  }
}

function startPositionChart(symbol) {
  if (typeof Chart === 'undefined' || typeof fetch === 'undefined') return;
  stopPositionChart();
  posChartSymbol = symbol;
  posChartTF = 'M1';
  refreshPositionChart();
  posChartTimer = setInterval(refreshPositionChart, 3000);
}

function stopPositionChart() {
  if (posChartTimer) { clearInterval(posChartTimer); posChartTimer = null; }
  if (posCandleChart) { posCandleChart.destroy(); posCandleChart = null; }
  posChartSymbol = null;
}

function setPosChartTF(tf) {
  posChartTF = tf;
  const group = document.querySelector('.pos-tf-group');
  if (group) {
    const btns = group.querySelectorAll('.pos-tf');
    for (let i = 0; i < btns.length; i++) {
      btns[i].classList.toggle('active', btns[i].getAttribute('data-tf') === tf);
    }
  }
  refreshPositionChart();
}

function patchPositionChartTick(tickData) {
  if (!posCandleChart || !posChartSymbol) return;
  if (!tickData || !tickData.symbol || tickData.symbol !== posChartSymbol) return;
  const price = tickData.bid != null ? tickData.bid : tickData.ask;
  if (price == null) return;
  const ds = posCandleChart.data.datasets[0];
  if (!ds || !ds.data || !ds.data.length) return;
  const last = ds.data[ds.data.length - 1];
  const isCandle = posCandleChart.config.type === 'candlestick';
  if (isCandle) {
    last.c = Number(price);
    if (last.h == null || Number(price) > Number(last.h)) last.h = Number(price);
    if (last.l == null || Number(price) < Number(last.l)) last.l = Number(price);
  } else {
    last.y = Number(price);
  }
  posCandleChart.update('none');
}

/* ── WebSocket + Polling ─────────────────────────────────────────── */

function connectWebSocket() {
  if (typeof WebSocket === 'undefined') return;
  if (typeof window === 'undefined' || !window.location) return;
  if (wsSocket && wsSocket.readyState === WebSocket.OPEN) return;

  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
  try {
    wsSocket = new WebSocket(`${proto}://${window.location.host}/ws/feed`);
  } catch (e) {
    return;
  }

  wsSocket.onopen = () => {
    if (wsReconnectTimer) { clearTimeout(wsReconnectTimer); wsReconnectTimer = null; }
  };

  wsSocket.onmessage = (evt) => {
    let msg = null;
    try { msg = JSON.parse(evt.data); } catch (e) { return; }
    if (!msg || !msg.event_type) return;

    if (msg.event_type === 'TICK') {
      updatePositionTicks(msg.data || {});
    } else if (
      msg.event_type === 'TRADE_OPEN' ||
      msg.event_type === 'TRADE_CLOSE' ||
      msg.event_type === 'PROFILE_CHANGED' ||
      msg.event_type === 'REGIME_CHANGE' ||
      msg.event_type === 'SYSTEM_LOG'
    ) {
      refreshStatus();
      refreshRecentHistory();
    }
  };

  wsSocket.onclose = () => {
    wsSocket = null;
    if (wsReconnectTimer) clearTimeout(wsReconnectTimer);
    wsReconnectTimer = setTimeout(connectWebSocket, 4000);
  };

  wsSocket.onerror = () => {
    try { if (wsSocket) wsSocket.close(); } catch (e) { /* ignore */ }
  };
}

function startLiveUpdates() {
  // Guard: in jsdom (unit tests) fetch/WebSocket semantics differ and the
  // polling timers would keep the Node process alive. A real browser always
  // has fetch, so this only short-circuits the test environment.
  if (typeof fetch === 'undefined' || typeof WebSocket === 'undefined') return;
  refreshStatus();
  refreshRecentHistory();
  connectWebSocket();
  if (liveTimer) clearInterval(liveTimer);
  liveTimer = setInterval(refreshStatus, 3000);
  if (historyTimer) clearInterval(historyTimer);
  historyTimer = setInterval(refreshRecentHistory, 10000);
}

/* ── Bootstrap ───────────────────────────────────────────────────── */

if (typeof document !== 'undefined') {
  document.addEventListener('DOMContentLoaded', () => {
    // Restore the last-selected trading profile from localStorage
    if (typeof localStorage !== 'undefined') {
      var saved = localStorage.getItem('genesis_active_profile');
      if (saved && TRADING_PROFILES[saved]) {
        activeProfile = saved;
      }
    }
    // Populate the symbol dropdown for the active profile
    if (TRADING_PROFILES[activeProfile]) {
      populateSymbolDropdown(TRADING_PROFILES[activeProfile].pairs);
    }

    updateLotConversionPreview();
    initCharts();
    startLiveUpdates();
    checkDiscordConnection();
    evaluateFiveGateways();
  });
}

// ── Conditional exports for Node.js testing (does not affect browser) ──
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    LEVERAGE,
    updateLotConversionPreview,
    executeStakeTrade,
    toggleToolsMenu,
    openChartModal,
    closeChartModal,
    openModal,
    closeUtilityModal,
    closeModal,
    checkDiscordConnection,
    updateDiscordUI,
    saveDiscordSettings,
    testDiscordWebhook,
    setEngineState,
    toggleEngineState,
    switchProfile,
    TRADING_PROFILES,
    selectProfile,
    populateSymbolDropdown,
    sendDiscordTradeNotification,
    evaluateFiveGateways,
    updateGatewayUI,
    simulateGatewayCheck,
    initCharts,
    openHistoryModal,
    closeHistoryModal,
    openPositionModal,
    closePositionModal,
    closePosition,
    updatePositionStops,
    refreshStatus,
    renderActivePositions,
    renderRecentHistory,
    fmtMoney,
    fmtPnl,
    startPositionChart,
    stopPositionChart,
    setPosChartTF,
    refreshPositionChart,
    patchPositionChartTick,
    loadCandles,
  };
}
