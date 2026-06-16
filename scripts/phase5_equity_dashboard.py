#!/usr/bin/env python3
"""Build equity curve dashboard from commission audit results"""
import json, os
import numpy as np

with open('reports/phase5_commissions_audit/result.json') as f:
    d = json.load(f)

eq = d['equity_curve']
dates = [e['date'] for e in eq]
equities = [e['equity'] for e in eq]

# Stats
peak = max(equities)
final = equities[-1]
initial = 100000
dd = [(peak_e - e) / peak_e * 100 for peak_e, e in zip(np.maximum.accumulate(equities), equities)]
max_dd = max(dd)
max_dd_idx = dd.index(max_dd)

# Monthly PnL
months_net = d['monthly_pnl_net']
months_gross = d['monthly_pnl_gross']

html = f"""<!DOCTYPE html>
<html lang="ru">
<head><meta charset="utf-8"><title>Phase 5 — Equity Curve</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
body {{ font-family: system-ui, sans-serif; max-width: 1200px; margin: 0 auto; padding: 20px; background: #0d1117; color: #c9d1d9; }}
h1, h2 {{ color: #58a6ff; }}
.card {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 20px; margin: 16px 0; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; }}
.stat {{ text-align: center; }}
.stat-value {{ font-size: 24px; font-weight: bold; }}
.stat-label {{ font-size: 12px; color: #8b949e; }}
.positive {{ color: #3fb950; }}
.negative {{ color: #f85149; }}
table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
th, td {{ padding: 6px 10px; text-align: right; border-bottom: 1px solid #21262d; }}
th {{ color: #8b949e; font-weight: normal; position: sticky; top: 0; background: #161b22; }}
tr:hover {{ background: #1c2128; }}
</style>
</head>
<body>

<h1>Phase 5 Walk-Forward — Commissions & Equity</h1>
<p>Портфель: 14 тикеров (10 long + 4 short)<br>
Комиссия: 50% мейкер (0%) + 50% тейкер (0.0264% для фондовых)<br>
Slippage: 0.5 tick (0.005%)<br>
Период: OOS 2025-01 — 2026-04</p>

<div class="card">
<div class="grid">
<div class="stat"><div class="stat-value {'positive' if d['return_pct']>0 else 'negative'}">{d['return_pct']:+.1f}%</div><div class="stat-label">Total Return</div></div>
<div class="stat"><div class="stat-value">{d['annual_return']:.0f}%</div><div class="stat-label">Annual Return</div></div>
<div class="stat"><div class="stat-value">{d['max_dd_pct']:.1f}%</div><div class="stat-label">Max Drawdown</div></div>
<div class="stat"><div class="stat-value">{d['calmar']:.1f}</div><div class="stat-label">Calmar Ratio</div></div>
<div class="stat"><div class="stat-value">{d['wr']:.1f}%</div><div class="stat-label">Win Rate</div></div>
<div class="stat"><div class="stat-value">{d['n_trades']:,}</div><div class="stat-label">Trades</div></div>
</div>
</div>

<div class="card">
<h2>Daily Equity Curve</h2>
<canvas id="eqChart" height="80"></canvas>
</div>

<div class="card">
<h2>Drawdown</h2>
<canvas id="ddChart" height="60"></canvas>
</div>

<div class="card">
<h2>Monthly PnL (NET after commissions)</h2>
<canvas id="monthlyChart" height="80"></canvas>
</div>

<div class="card">
<h2>Monthly Detail</h2>
<table>
<tr><th>Month</th><th>Gross PnL</th><th>Net PnL</th><th>Commission</th><th>% of Capital</th></tr>
"""

for m in sorted(months_net.keys()):
    g = months_gross.get(m, 0)
    n = months_net[m]
    c = g - n
    pct = n / 100000 * 100  # % от начального капитала
    cls = 'positive' if n >= 0 else 'negative'
    html += f'<tr><td>{m}</td><td>{g:+,.0f}</td><td class="{cls}">{n:+,.0f}</td><td>{c:+,.0f}</td><td>{pct:+.2f}%</td></tr>\n'

html += f"""
<tr style="font-weight:bold; border-top:2px solid #30363d">
<td>Total</td>
<td>{sum(months_gross.values()):+,.0f}</td>
<td>{sum(months_net.values()):+,.0f}</td>
<td>{d['total_commission']:+,.0f}</td>
<td>{d['return_pct']:.1f}%</td>
</tr>
</table>
</div>

<script>
const eqData = {json.dumps([{'x':dates[i],'y':equities[i]} for i in range(0,len(dates),3)])};
const ddData = {json.dumps([{'x':dates[i],'y':round(dd[i],1)} for i in range(0,len(dates),3)])};

const commonOpts = {{
    responsive: true,
    maintainAspectRatio: false,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{
        x: {{ ticks: {{ color: '#8b949e', maxTicksLimit: 20 }}, grid: {{ color: '#21262d' }} }},
        y: {{ ticks: {{ color: '#8b949e' }}, grid: {{ color: '#21262d' }} }}
    }}
}};

new Chart(document.getElementById('eqChart'), {{
    type: 'line',
    data: {{ datasets: [{{ label:'Equity', data: eqData, borderColor: '#58a6ff', backgroundColor: 'rgba(88,166,255,0.1)', fill: true, tension: 0.1, pointRadius: 0 }}] }},
    options: {{ ...commonOpts, scales: {{ ...commonOpts.scales, y: {{ ...commonOpts.scales.y, title: {{ display: true, text: 'Equity (RUB)', color: '#8b949e' }} }} }} }}
}});

new Chart(document.getElementById('ddChart'), {{
    type: 'line',
    data: {{ datasets: [{{ label:'Drawdown %', data: ddData, borderColor: '#f85149', backgroundColor: 'rgba(248,81,73,0.1)', fill: true, tension: 0.3, pointRadius: 0 }}] }},
    options: {{ ...commonOpts, scales: {{ ...commonOpts.scales, y: {{ ...commonOpts.scales.y, reverse: true, title: {{ display: true, text: 'Drawdown %', color: '#8b949e' }} }} }} }}
}});

// Monthly PnL
const months_sorted = {json.dumps(sorted(months_net.keys()))};
const net_values = {json.dumps([months_net[m] for m in sorted(months_net.keys())])};
const gross_values = {json.dumps([months_gross[m] for m in sorted(months_gross.keys())])};

new Chart(document.getElementById('monthlyChart'), {{
    type: 'bar',
    data: {{
        labels: months_sorted,
        datasets: [
            {{ label: 'Gross', data: gross_values, backgroundColor: 'rgba(88,166,255,0.5)', borderColor: '#58a6ff', borderWidth: 1 }},
            {{ label: 'Net', data: net_values, backgroundColor: net_values.map(v => v>=0 ? 'rgba(63,185,80,0.6)' : 'rgba(248,81,73,0.6)'), borderColor: net_values.map(v => v>=0 ? '#3fb950' : '#f85149'), borderWidth: 1 }}
        ]
    }},
    options: {{
        responsive: true,
        maintainAspectRatio: false,
        plugins: {{ legend: {{ labels: {{ color: '#8b949e' }} }} }},
        scales: {{
            x: {{ ticks: {{ color: '#8b949e' }}, grid: {{ color: '#21262d' }} }},
            y: {{ ticks: {{ color: '#8b949e' }}, grid: {{ color: '#21262d' }}, title: {{ display: true, text: 'PnL (RUB)', color: '#8b949e' }} }}
        }}
    }}
}});
</script>

<p style="color:#8b949e;font-size:12px;margin-top:40px">
Original (no commissions): +1,794% | With commissions: +{d['return_pct']:.1f}%<br>
Commission total: {d['total_commission']:+,.0f}</p>

</body>
</html>
"""

os.makedirs('reports/phase5_commissions_audit', exist_ok=True)
with open('reports/phase5_commissions_audit/dashboard.html','w') as f:
    f.write(html)
print(f"Dashboard: reports/phase5_commissions_audit/dashboard.html ({len(eq)} equity points)")
