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
                            <td>${(t.entry_price != null) ? t.entry_price.toFixed(5) : '—'} → ${(t.exit_price != null) ? t.exit_price.toFixed(5) : '—}</td>
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