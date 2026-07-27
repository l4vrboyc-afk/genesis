
        let chart = null;

        // Initialize Chart
        function initChart() {
            const ctx = document.getElementById('pnlChart').getContext('2d');
            chart = new Chart(ctx, {
                type: 'line',
                data: {
                    labels: [],
                    datasets: [{
                        label: 'Cumulative P&L ($)',
                        data: [],
                        borderColor: '#00f2fe',
                        backgroundColor: 'rgba(0, 242, 254, 0.1)',
                        borderWidth: 2.5,
                        fill: true,
                        tension: 0.35,
                        pointRadius: 3,
                        pointHoverRadius: 6
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    scales: {
                        x: {
                            grid: { color: 'rgba(255, 255, 255, 0.05)' },
                            ticks: { color: '#9ca3af' }
                        },
                        y: {
                            grid: { color: 'rgba(255, 255, 255, 0.05)' },
                            ticks: { color: '#9ca3af' }
                        }
                    },
                    plugins: {
                        legend: { display: false }
                    }
                }
            });
        }

        // Fetch dashboard data
        async function fetchDashboardData() {
            try {
                // Status & positions
                const statusRes = await fetch('/api/status');
                const status = await statusRes.json();
                updateStatusUI(status);

                // Performance & Charts
                const perfRes = await fetch('/api/performance');
                const perf = await perfRes.json();
                updatePerformanceUI(perf);

                // Completed Trades
                const tradesRes = await fetch('/api/trades?limit=10');
                const trades = await tradesRes.json();
                updateTradesTable(trades);

                // News
                const newsRes = await fetch('/api/news');
                const news = await newsRes.json();
                updateNewsUI(news.events);

                // Risk stats
                const riskRes = await fetch('/api/risk');
                const risk = await riskRes.json();
                updateRiskUI(risk);

            } catch (err) {
                console.error("Failed to fetch dashboard api: ", err);
            }
        }

        function updateStatusUI(data) {
            // Header badge status
            const badge = document.getElementById('bot-status');
            const label = document.getElementById('status-label');
            const toggleBtn = document.getElementById('toggle-bot-btn');

            if (data.paused) {
                badge.className = "bot-status-badge status-paused";
                label.innerText = "PAUSED";
                toggleBtn.className = "btn btn-green";
                toggleBtn.innerHTML = '<i class="fa-solid fa-play"></i> Resume Trading';
            } else {
                badge.className = "bot-status-badge status-active";
                label.innerText = "ACTIVE";
                toggleBtn.className = "btn btn-outline";
                toggleBtn.innerHTML = '<i class="fa-solid fa-pause"></i> Pause Trading';
            }

            // Stat values
            document.getElementById('balance-val').innerText = `$${data.balance.toFixed(2)}`;
            document.getElementById('equity-val').innerText = `$${data.equity.toFixed(2)}`;
            
            const pnlVal = document.getElementById('pnl-val');
            pnlVal.innerText = `$${data.daily_pnl.toFixed(2)}`;
            if (data.daily_pnl >= 0) {
                pnlVal.className = "stat-value color-win";
                document.getElementById('pnl-icon').style.color = "var(--accent-green)";
            } else {
                pnlVal.className = "stat-value color-loss";
                document.getElementById('pnl-icon').style.color = "var(--accent-red)";
            }

            // Update Active Positions table
            const activeBody = document.getElementById('active-positions-body');
            if (!data.open_trades || data.open_trades.length === 0) {
                activeBody.innerHTML = '<tr><td colspan="8" style="text-align: center; color: var(--text-secondary)">No active positions</td></tr>';
            } else {
                activeBody.innerHTML = data.open_trades.map(p => `
                    <tr>
                        <td><code>${p.ticket}</code></td>
                        <td><strong>${p.symbol}</strong></td>
                        <td><span class="badge ${p.direction === 'buy' ? 'badge-buy' : 'badge-sell'}">${p.direction}</span></td>
                        <td><code>${p.volume.toFixed(2)}</code></td>
                        <td>${p.entry_price.toFixed(5)}</td>
                        <td>${p.current_price ? p.current_price.toFixed(5) : '-'}</td>
                        <td style="font-size: 0.8rem; color: var(--text-secondary)">SL: ${p.sl.toFixed(5)}<br>TP: ${p.tp.toFixed(5)}</td>
                        <td class="${p.profit >= 0 ? 'color-win' : 'color-loss'}" style="font-weight: 600">
                            $${p.profit.toFixed(2)}
                        </td>
                    </tr>
                `).join('');
            }
        }

        function updatePerformanceUI(data) {
            document.getElementById('winrate-val').innerText = `${(data.win_rate * 100).toFixed(1)}%`;
            
            // Update Chart
            if (chart && data.daily_performance_history) {
                const history = data.daily_performance_history;
                chart.data.labels = history.map(h => h.date);
                
                // Calculate cumulative profit
                let cumProfit = 0;
                chart.data.datasets[0].data = history.map(h => {
                    cumProfit += h.pnl;
                    return cumProfit;
                });
                
                chart.update();
            }
        }

        function updateTradesTable(trades) {
            const body = document.getElementById('completed-trades-body');
            if (trades.length === 0) {
                body.innerHTML = '<tr><td colspan="9" style="text-align: center; color: var(--text-secondary)">No completed trades</td></tr>';
            } else {
                body.innerHTML = trades.map(t => {
                    const time = new Date(t.open_time).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
                    return `
                        <tr>
                            <td style="color: var(--text-secondary)">${time}</td>
                            <td><code>${t.ticket}</code></td>
                            <td><strong>${t.symbol}</strong></td>
                            <td><span class="badge ${t.direction === 'buy' ? 'badge-buy' : 'badge-sell'}">${t.direction}</span></td>
                            <td><code>${t.volume.toFixed(2)}</code></td>
                            <td>${(t.entry_price != null) ? t.entry_price.toFixed(5) : '—'} → ${(t.exit_price != null) ? t.exit_price.toFixed(5) : '—'}</td>
                            <td><span style="font-size: 0.8rem">${t.strategy}</span></td>
                            <td><span style="font-size: 0.8rem; text-transform: uppercase;">${t.market_regime || 'Ranging'}</span></td>
                            <td class="${t.profit >= 0 ? 'color-win' : 'color-loss'}" style="font-weight: 600">
                                ${t.profit >= 0 ? '+' : ''}$${t.profit.toFixed(2)}
                            </td>
                        </tr>
                    `;
                }).join('');
            }
        }

        function updateNewsUI(events) {
            const container = document.getElementById('news-list');
            if (!events || events.length === 0) {
                container.innerHTML = '<div style="text-align: center; color: var(--text-secondary); font-size: 0.8rem">No upcoming high impact news</div>';
                return;
            }

            container.innerHTML = events.map(e => {
                const time = new Date(e.time).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
                return `
                    <div class="news-item news-impact-high">
                        <div>
                            <strong style="color: var(--accent-cyan); font-size: 0.85rem">${e.currency}</strong> - ${e.name}
                        </div>
                        <div style="font-weight: 500">${time}</div>
                    </div>
                `;
            }).join('');
        }

        function updateRiskUI(data) {
            document.getElementById('regime-val').innerText = data.cooldown_active ? "Cooldown" : "Trending/Ranging";
            document.getElementById('max-dd-val').innerText = `${data.daily_drawdown_pct.toFixed(2)}% / ${data.daily_drawdown_limit}%`;
            document.getElementById('streak-val').innerText = `${data.consecutive_losses} / ${data.losing_streak_pause}`;
            document.getElementById('cooldown-val').innerText = data.cooldown_active ? "ACTIVE" : "Inactive";
            
            const cooldownSpan = document.getElementById('cooldown-val');
            if (data.cooldown_active) {
                cooldownSpan.style.color = "var(--accent-red)";
            } else {
                cooldownSpan.style.color = "var(--accent-green)";
            }
        }

        // Action Control Commands
        async function toggleBot() {
            const label = document.getElementById('status-label').innerText;
            const action = label === "ACTIVE" ? "pause" : "resume";
            
            try {
                const res = await fetch('/api/control', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ action })
                });
                const data = await res.json();
                if (data.status === "success") {
                    fetchDashboardData();
                }
            } catch (err) {
                console.error("Control toggle bot command failed: ", err);
            }
        }

        async function closeAllPositions() {
            if (!confirm("Are you sure you want to CLOSE ALL open positions immediately?")) {
                return;
            }
            try {
                const res = await fetch('/api/control', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ action: "close_all" })
                });
                const data = await res.json();
                alert(data.message);
                fetchDashboardData();
            } catch (err) {
                console.error("Emergency close command failed: ", err);
            }
        }

        async function releaseRegime() {
            if (!confirm("Release the forced regime override and resume auto-detection?")) {
                return;
            }
            try {
                const res = await fetch('/api/control', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ action: "release_regime" })
                });
                const data = await res.json();
                alert(data.message);
                fetchDashboardData();
            } catch (err) {
                console.error("Release regime command failed: ", err);
            }
        }

        // Settings config loading & saving
        async function loadSettings() {
            try {
                const res = await fetch('/api/settings');
                const data = await res.json();
                
                document.getElementById('set-risk').value = data.max_risk_per_trade * 100;
                document.getElementById('set-drawdown').value = data.max_daily_drawdown * 100;
                document.getElementById('set-max-positions').value = data.max_open_positions;
                document.getElementById('set-pairs').value = data.trading_pairs.join(', ');
            } catch (err) {
                console.error("Failed to load settings: ", err);
            }
        }

        async function saveSettings(e) {
            e.preventDefault();
            const payload = {
                max_risk_per_trade: parseFloat(document.getElementById('set-risk').value) / 100,
                max_daily_drawdown: parseFloat(document.getElementById('set-drawdown').value) / 100,
                max_open_positions: parseInt(document.getElementById('set-max-positions').value),
                trading_pairs: document.getElementById('set-pairs').value.split(',').map(s => s.trim().toUpperCase()).filter(s => s.length > 0)
            };

            try {
                const res = await fetch('/api/settings', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                const data = await res.json();
                if (data.status === "success") {
                    alert("Settings saved successfully!");
                    fetchDashboardData();
                } else {
                    alert("Failed: " + data.message);
                }
            } catch (err) {
                console.error("Failed to save settings: ", err);
            }
        }

        // Set listeners
        document.getElementById('toggle-bot-btn').addEventListener('click', toggleBot);
        document.getElementById('close-all-btn').addEventListener('click', closeAllPositions);
        document.getElementById('release-regime-btn').addEventListener('click', releaseRegime);

        // Run — poll only while tab is visible to avoid hammering the backend
        let pollTimer = null;

        function startPolling() {
            if (pollTimer) return;
            fetchDashboardData();
            pollTimer = setInterval(() => {
                if (!document.hidden) fetchDashboardData();
            }, 3000);
        }

        function stopPolling() {
            if (pollTimer) {
                clearInterval(pollTimer);
                pollTimer = null;
            }
        }

        window.addEventListener('DOMContentLoaded', () => {
            initChart();
            loadSettings();
            startPolling();
        });

        document.addEventListener('visibilitychange', () => {
            if (document.hidden) {
                stopPolling();
            } else {
                startPolling();
            }
        });

        // Ask-Claude copilot (Track c). All UI text is rendered via
        // textContent (never innerHTML) so broker-supplied comments
        // in <untrusted-data> cannot inject markup. Citation chips split
        // the response on /[source:...]/ and render as styled spans.
        (async function setupCopilot() {
            const banner = document.getElementById('copilot-banner');
            const input = document.getElementById('copilot-input');
            const sendBtn = document.getElementById('copilot-send');
            const cancelBtn = document.getElementById('copilot-cancel');
            const clearBtn = document.getElementById('copilot-clear');
            const loading = document.getElementById('copilot-loading');
            const transcript = document.getElementById('copilot-transcript');
            let streamController = null;
            function escapeHtml(s) { return String(s).replace(/[&<>]/g, c => ({'&':'&', '<':'<', '>':'>'}[c])); }
            function renderAnswerWithChips(text) {
                const frag = document.createDocumentFragment();
                const re = /(\[source:[^\]]+\])/g;
                let last = 0, m;
                while ((m = re.exec(text)) !== null) {
                    frag.appendChild(document.createTextNode(escapeHtml(text.slice(last, m.index))));
                    const chip = document.createElement('span');
                    chip.className = 'cite-chip';
                    chip.textContent = m[1];
                    chip.title = m[1];
                    frag.appendChild(chip);
                    last = m.index + m[1].length;
                }
                frag.appendChild(document.createTextNode(escapeHtml(text.slice(last))));
                return frag;
            }
            function appendMessage(role, content, citations) {
                const div = document.createElement('div');
                div.className = 'msg msg-' + role;
                if (content instanceof Node) div.appendChild(content);
                else div.appendChild(document.createTextNode(escapeHtml(content)));
                if (Array.isArray(citations) && citations.length) {
                    const cWrap = document.createElement('div');
                    cWrap.className = 'citations';
                    cWrap.appendChild(document.createTextNode('Sources: '));
                    citations.forEach(function(c) {
                        const chip = document.createElement('span');
                        chip.className = 'cite-chip';
                        const trimmed = String(c).replace(/^\[source:|\]$/g, '');
                        chip.textContent = trimmed;
                        chip.title = c;
                        cWrap.appendChild(chip);
                    });
                    div.appendChild(cWrap);
                }
                transcript.appendChild(div);
                transcript.scrollTop = transcript.scrollHeight;
            }
            function setEnabled(enabled) {
                input.disabled = !enabled;
                sendBtn.disabled = !enabled;
                input.placeholder = enabled
                    ? "e.g. why is the bot paused? What's my drawdown today?"
                    : 'Copilot disabled -- see banner above.';
            }
            try {
                const r = await fetch('/api/copilot/status');
                const status = await r.json();
                if (status.enabled) {
                    banner.textContent = 'Model: ' + status.model + '  ·  API key ending ...' + (status.masked_key_tail || '????');
                    banner.style.color = '#00e676';
                    banner.style.borderColor = 'rgba(0, 230, 118, 0.25)';
                    setEnabled(true);
                } else {
                    banner.textContent = 'Copilot disabled: ' + (status.reason || 'unknown') + '. Set ANTHROPIC_API_KEY in .env (then restart).';
                    banner.style.color = '#ff9100';
                    banner.style.borderColor = 'rgba(255, 145, 0, 0.25)';
                    setEnabled(false);
                }
            } catch (e) {
                banner.textContent = 'Status check failed: ' + e;
                banner.style.color = '#ff9100';
                setEnabled(false);
            }
            sendBtn.addEventListener('click', async function() {
                const q = input.value.trim();
                if (!q) return;
                appendMessage('user', q);
                input.value = '';
                loading.style.display = '';
                cancelBtn.style.display = '';
                clearBtn.style.display = '';
                streamController = new AbortController();
                try {
                    const r = await fetch('/api/copilot/ask', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({question: q, stream: true}),
                        signal: streamController.signal,
                    });
                    if (r.status === 503) {
                        const j = await r.json();
                        appendMessage('bot', 'Copilot disabled: ' + (j.error || 'unknown') + (j.reason ? ' (' + j.reason + ')' : ''));
                    } else if (!r.ok) {
                        appendMessage('bot', 'Error ' + r.status);
                    } else {
                        const reader = r.body.getReader();
                        const decoder = new TextDecoder();
                        let buf = '', answer = '', citations = [];
                        while (true) {
                            const rv = await reader.read();
                            if (rv.done) break;
                            buf += decoder.decode(rv.value, {stream: true});
                            let idx;
                            while ((idx = buf.indexOf('\n\n')) !== -1) {
                                const frame = buf.slice(0, idx).trim();
                                buf = buf.slice(idx + 2);
                                if (!frame.startsWith('data:')) continue;
                                const payload = frame.slice(5).trim();
                                try {
                                    const evt = JSON.parse(payload);
                                    if (evt.type === 'done') { answer = evt.answer || ''; citations = evt.citations || []; }
                                    else if (evt.type === 'error') { appendMessage('bot', String(evt.content || 'unknown error')); answer = ''; break; }
                                } catch (_) {}
                            }
                        }
                        if (answer) appendMessage('bot', renderAnswerWithChips(answer), citations);
                        else appendMessage('bot', '(empty response)');
                    }
                } catch (e) {
                    if (e.name !== 'AbortError') appendMessage('bot', 'Call failed: ' + (e.message || e));
                } finally {
                    loading.style.display = 'none';
                    cancelBtn.style.display = 'none';
                    streamController = null;
                }
            });
            cancelBtn.addEventListener('click', function() { if (streamController) streamController.abort(); });
            clearBtn.addEventListener('click', function() { transcript.innerHTML = ''; clearBtn.style.display = 'none'; });
        })();    