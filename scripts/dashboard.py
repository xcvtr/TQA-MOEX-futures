#!/usr/bin/env python3
"""Simple dashboard for Stop Hunt paper trader."""
import os, json, http.server, psycopg2
from datetime import datetime, timezone

HOST = '0.0.0.0'
PORT = 8087
PG = dict(host='10.0.0.60', port=5432, dbname='moex', user='postgres')

def query(q, params=None):
    pg = psycopg2.connect(**PG)
    cur = pg.cursor()
    cur.execute(q, params or ())
    cols = [d[0] for d in cur.description] if cur.description else []
    rows = cur.fetchall()
    cur.close()
    pg.close()
    return cols, rows

HTML = '''<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>MOEX Futures Dashboard</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Segoe UI',sans-serif;background:#0d1117;color:#e6edf3;padding:16px}
h1{font-size:1.3rem;margin-bottom:12px;color:#58a6ff}
.dashboard{display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:10px;margin-bottom:16px}
.card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:14px}
.card h3{font-size:.75rem;color:#8b949e;margin-bottom:4px}
.card .val{font-size:1.6rem;font-weight:700}
.card .sub{font-size:.75rem;color:#8b949e;margin-top:2px}
.positive{color:#3fb950};.negative{color:#f85149}
.chart-box{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:14px;margin-bottom:12px}
#equity-chart{width:100%;height:350px}
table{width:100%;border-collapse:collapse;font-size:.8rem}
th{text-align:left;padding:6px 8px;color:#8b949e;border-bottom:1px solid #30363d}
td{padding:6px 8px;border-bottom:1px solid #21262d}
.refresh-info{text-align:right;font-size:.7rem;color:#484f58;margin-top:8px}
</style>
</head>
<body>
<h1>📊 MOEX Futures</h1>
<div style="display:flex;gap:10px;margin-bottom:16px">
  <div style="flex:1"><h2 style="font-size:.9rem;color:#58a6ff">🔹 Stop Hunt</h2><div class="dashboard" id="stats-sh"></div></div>
  <div style="flex:1"><h2 style="font-size:.9rem;color:#d29922">🔸 Impulse Return</h2><div class="dashboard" id="stats-ir"></div></div>
  <div style="flex:1"><h2 style="font-size:.9rem;color:#3fb950">🔷 Portfolio SH+IR</h2><div class="dashboard" id="stats-pf"></div></div>
</div>
<div class="chart-box"><div id="equity-chart"></div></div>
<div style="display:flex;gap:20px">
  <div style="flex:1"><div class="chart-box"><h3 style="margin-bottom:8px;color:#8b949e">Stop Hunt позиции</h3><div id="positions-sh">—</div></div></div>
  <div style="flex:1"><div class="chart-box"><h3 style="margin-bottom:8px;color:#8b949e">Impulse Return позиции</h3><div id="positions-ir">—</div></div></div>
</div>
<div class="chart-box"><h3 style="margin-bottom:8px;color:#8b949e">Сделки</h3><table id="trades"><tr><th>Время</th><th>Тикер</th><th>Стратегия</th><th>Направление</th><th>PnL</th></tr></table></div>
<div class="refresh-info" id="refresh-info"></div>

<script>
async function load() {
  try {
    const [r1, r2, r3] = await Promise.all([
      fetch('/api/state'),
      fetch('/api/state?strategy=impulse_return'),
      fetch('/api/state?strategy=portfolio')
    ]);
    const d = await r1.json();
    const d2 = await r2.json();
    const d3 = await r3.json();
    
    // Stop Hunt cards
    const eq = d.equity;
    const init = d.capital;
    const ret = ((eq/init)-1)*100;
    const eqCls = ret >= 0 ? 'positive' : 'negative';
    const posCount = d.positions ? d.positions.length : 0;
    
    document.getElementById('stats-sh').innerHTML = [
      `<div class="card"><h3>Equity</h3><div class="val ${eqCls}">${eq.toLocaleString()} ₽</div><div class="sub">start: ${init.toLocaleString()} ₽</div></div>`,
      `<div class="card"><h3>Return</h3><div class="val ${eqCls}">${ret >= 0 ? '+' : ''}${ret.toFixed(2)}%</div><div class="sub">peak: ${d.peak.toLocaleString()} ₽</div></div>`,
      `<div class="card"><h3>MDD</h3><div class="val">${d.mdd_pct.toFixed(2)}%</div><div class="sub">drawdown from peak</div></div>`,
      `<div class="card"><h3>Positions</h3><div class="val">${posCount}</div><div class="sub">open / ${d.n_trades || 0} total</div></div>`,
    ].join('');
    
    // Impulse Return cards
    const eq2 = d2.equity || init;
    const ret2 = ((eq2/init)-1)*100;
    const eq2Cls = ret2 >= 0 ? 'positive' : 'negative';
    const pos2Count = d2.positions ? d2.positions.length : 0;
    
    document.getElementById('stats-ir').innerHTML = [
      `<div class="card"><h3>Equity</h3><div class="val ${eq2Cls}">${eq2.toLocaleString()} ₽</div><div class="sub">start: ${init.toLocaleString()} ₽</div></div>`,
      `<div class="card"><h3>Return</h3><div class="val ${eq2Cls}">${ret2 >= 0 ? '+' : ''}${ret2.toFixed(2)}%</div><div class="sub">peak: ${d2.peak.toLocaleString()} ₽</div></div>`,
      `<div class="card"><h3>MDD</h3><div class="val">${d2.mdd_pct.toFixed(2)}%</div><div class="sub">drawdown from peak</div></div>`,
      `<div class="card"><h3>Positions</h3><div class="val">${pos2Count}</div><div class="sub">open / ${d2.n_trades || 0} total</div></div>`,
    ].join('');
    
    // Positions
    const renderPos = (id, data) => {
      if (data && data.length > 0) {
        document.getElementById(id).innerHTML = data.map(p => 
          `<div style="display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid #21262d">
            <span><b>${p.ticker}</b> ${p.direction}</span>
            <span>entry: ${p.entry_price}</span>
            <span>bars: ${p.bars_held}</span>
          </div>`
        ).join('');
      } else {
        document.getElementById(id).innerHTML = '<span style="color:#484f58">— нет открытых позиций</span>';
      }
    };
    renderPos('positions-sh', d.positions);
    renderPos('positions-ir', d2.positions);
    
    // Portfolio cards
    const eq3 = d3.equity || init;
    const ret3 = ((eq3/init)-1)*100;
    const eq3Cls = ret3 >= 0 ? 'positive' : 'negative';
    const pos3Count = d3.positions ? d3.positions.length : 0;
    
    document.getElementById('stats-pf').innerHTML = [
      `<div class="card"><h3>Equity</h3><div class="val ${eq3Cls}">${eq3.toLocaleString()} ₽</div><div class="sub">start: ${init.toLocaleString()} ₽</div></div>`,
      `<div class="card"><h3>Return</h3><div class="val ${eq3Cls}">${ret3 >= 0 ? '+' : ''}${ret3.toFixed(2)}%</div><div class="sub">peak: ${d3.peak.toLocaleString()} ₽</div></div>`,
      `<div class="card"><h3>MDD</h3><div class="val">${d3.mdd_pct.toFixed(2)}%</div><div class="sub">drawdown from peak</div></div>`,
      `<div class="card"><h3>Positions</h3><div class="val">${pos3Count}</div><div class="sub">open / ${d3.n_trades || 0} total</div></div>`,
    ].join('');
    
    // Trades table
    const allTrades = [...(d.trades || []), ...(d2.trades || [])];
    allTrades.sort((a,b) => (b.time||'').localeCompare(a.time||''));
    if (allTrades.length > 0) {
      document.getElementById('trades').innerHTML = '<tr><th>Время</th><th>Тикер</th><th>Стратегия</th><th>Направление</th><th>PnL</th></tr>' +
        allTrades.slice(0,50).map(t => {
          const pnlCls = t.pnl >= 0 ? 'positive' : 'negative';
          const pnlStr = t.pnl >= 0 ? '+' + t.pnl.toFixed(0) : t.pnl.toFixed(0);
          return `<tr><td>${t.time || ''}</td><td>${t.ticker}</td><td>${t.strategy || 'sh'}</td><td>${t.direction}</td><td class="${pnlCls}">${pnlStr} ₽</td></tr>`;
        }).join('');
    }
    document.getElementById('refresh-info').textContent = 'updated: ' + (d.updated_at || '—');
    // Equity chart
    if (d.equity_curve && d.equity_curve.length > 1) {
      Plotly.react('equity-chart', [{
        x: d.equity_curve.map(p => p.t),
        y: d.equity_curve.map(p => p.e),
        type: 'scatter', mode: 'lines',
        line: {color: '#58a6ff', width: 2},
        fill: 'tozeroy', fillcolor: 'rgba(88,166,255,0.08)',
        name: 'Equity'
      }, {
        x: d.equity_curve.map(p => p.t),
        y: d.equity_curve.map(p => p.m),
        type: 'scatter', mode: 'lines',
        line: {color: '#d29922', width: 2, dash: 'dot'},
        name: 'MTM'
      }], {
        paper_bgcolor: '#161b22', plot_bgcolor: '#0d1117',
        font: {color: '#8b949e', size: 10},
        margin: {l:50, r:15, t:15, b:30},
        xaxis: {type: 'date', gridcolor: '#21262d'},
        yaxis: {gridcolor: '#21262d', tickprefix: ''},
        hovermode: 'x unified',
        legend: {orientation: 'h', y: 1.1},
      });
    }
    
    document.getElementById('refresh-info').textContent = 'updated: ' + (d.updated_at || '—');
  } catch(e) {
    document.getElementById('stats').innerHTML = `<div class="card"><h3>Error</h3><div class="val negative">${e.message}</div></div>`;
  }
}

load();
setInterval(load, 15000);
</script>
</body>
</html>'''

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/':
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(HTML.encode('utf-8'))
        elif self.path.startswith('/api/state'):
            self._state()
        else:
            self.send_response(404)
            self.end_headers()
    
    def _state(self):
        try:
            from urllib.parse import urlparse, parse_qs
            qs = parse_qs(urlparse(self.path).query)
            strategy = qs.get('strategy', [None])[0]
            tbl = 'futures.paper_state' + ('' if not strategy else '_' + strategy)
            cols, rows = query(f"SELECT capital, equity, peak, positions_json, updated_at FROM {tbl}")
            if not rows:
                self._json({'error': 'no data'})
                return
            d = dict(zip(cols, rows[0]))
            
            # Parse positions
            positions = json.loads(d.get('positions_json', '[]') or '[]')
            equity = float(d['equity'])
            peak = float(d['peak'])
            capital = float(d['capital'])
            mdd_pct = (peak - equity) / peak * 100 if peak > 0 else 0
            
            # Trades from equity_curve (all time)
            result = {
                'capital': capital,
                'equity': equity,
                'peak': peak,
                'mdd_pct': round(mdd_pct, 2),
                'positions': positions,
                'n_trades': len(positions),
                'updated_at': str(d.get('updated_at', '')),
                'equity_curve': [],
                'trades': [],
            }
            
            # Equity curve (last 1000 points from backtest if available)
            try:
                eq_cols, eq_rows = query(
                    "SELECT ts_msk, balance, mtm FROM backtest.equity_curve WHERE run_id LIKE 'sh_200k_%' ORDER BY bar_idx DESC LIMIT 1000"
                )
                for r in reversed(eq_rows):
                    rd = dict(zip(eq_cols, r))
                    result['equity_curve'].append({
                        't': str(rd['ts_msk']),
                        'e': float(rd['balance']),
                        'm': float(rd['mtm']),
                    })
            except:
                pass
            
            # Recent trades
            try:
                t_cols, t_rows = query(
                    "SELECT entry_time, ticker, direction, entry_price, pnl, exit_reason FROM backtest.trades WHERE run_id LIKE 'sh_200k_%' ORDER BY entry_bar DESC LIMIT 50"
                )
                for r in reversed(t_rows):
                    rd = dict(zip(t_cols, r))
                    result['trades'].append({
                        'time': str(rd.get('entry_time', ''))[:19] if rd.get('entry_time') else '',
                        'ticker': rd['ticker'],
                        'direction': rd['direction'],
                        'price': float(rd['entry_price']) if rd['entry_price'] else 0,
                        'pnl': float(rd['pnl']) if rd['pnl'] else 0,
                        'reason': rd['exit_reason'],
                    })
            except:
                pass
            
            self._json(result)
        except Exception as e:
            self._json({'error': str(e)})
    
    def _json(self, data):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data, default=str).encode())
    
    def log_message(self, *a): pass

srv = http.server.ThreadingHTTPServer((HOST, PORT), Handler)
print(f'Dashboard: http://{HOST}:{PORT}')
srv.serve_forever()
