const t = { entry_price: null, exit_price: 1.2345 };
const x = `
                        <tr>
                            <td>${(t.entry_price != null) ? t.entry_price.toFixed(5) : '—'} → ${(t.exit_price != null) ? t.exit_price.toFixed(5) : '—'}</td>
                     </tr>
                    `;
console.log(x);
