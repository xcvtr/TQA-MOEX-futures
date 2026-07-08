#!/usr/bin/env python3
"""Simple dashboard for Stop Hunt paper trader."""
import os, json, http.server, psycopg2, clickhouse_connect as cc
from datetime import datetime, timezone

HOST = '0.0.0.0'
PORT = 8087
PG = dict(host='10.0.0.60', port=5432, dbname='moex', user='postgres')
CH_HOST = '10.0.0.60'
CH_DB = 'moex'

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
.dashboard{display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:8px;margin-bottom:8px}
.card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:12px}
.card h3{font-size:.7rem;color:#8b949e;margin-bottom:3px}
.card .val{font-size:1.3rem;font-weight:700}
.card .sub{font-size:.7rem;color:#8b949e;margin-top:2px}
.col{flex:1;background:#0d1117;border:1px solid #21262d;border-radius:10px;padding:12px}
.col h2{font-size:.85rem;margin-bottom:6px;padding-bottom:6px;border-bottom:1px solid #21262d}
.positive{color:#3fb950};.negative{color:#f85149}
.chart-box{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:14px;margin-bottom:12px}
table{width:100%;border-collapse:collapse;font-size:.8rem}
th{text-align:left;padding:6px 8px;color:#8b949e;border-bottom:1px solid #30363d}
td{padding:6px 8px;border-bottom:1px solid #21262d}
.refresh-info{text-align:right;font-size:.7rem;color:#484f58;margin-top:8px}
</style>
</head>
<body>
<h1>📊 MOEX Futures</h1>
<div style="display:flex;gap:10px;margin-bottom:12px;align-items:stretch">
  <div class="col"><h2 style="color:#58a6ff">🔹 Stop Hunt</h2><div class="dashboard" id="stats-sh"></div><div id="chart-sh" style="height:120px;margin:6px 0"></div><h3 style="font-size:.75rem;color:#8b949e;margin:8px 0 4px">Позиции</h3><div id="positions-sh" style="font-size:.75rem;color:#484f58">—</div><h3 style="font-size:.75rem;color:#8b949e;margin:8px 0 4px">Сделки</h3><div id="trades-sh" style="font-size:.7rem;color:#484f58">—</div></div>
  <div class="col"><h2 style="color:#d29922">🔸 Impulse Return</h2><div class="dashboard" id="stats-ir"></div><div id="chart-ir" style="height:120px;margin:6px 0"></div><h3 style="font-size:.75rem;color:#8b949e;margin:8px 0 4px">Позиции</h3><div id="positions-ir" style="font-size:.75rem;color:#484f58">—</div><h3 style="font-size:.75rem;color:#8b949e;margin:8px 0 4px">Сделки</h3><div id="trades-ir" style="font-size:.7rem;color:#484f58">—</div></div>
  <div class="col"><h2 style="color:#3fb950">🔷 Portfolio SH+IR</h2><div class="dashboard" id="stats-pf"></div><div id="chart-pf" style="height:120px;margin:6px 0"></div><h3 style="font-size:.75rem;color:#8b949e;margin:8px 0 4px">Позиции</h3><div id="positions-pf" style="font-size:.75rem;color:#484f58">—</div><h3 style="font-size:.75rem;color:#8b949e;margin:8px 0 4px">Сделки</h3><div id="trades-pf" style="font-size:.7rem;color:#484f58">—</div></div>
</div>
<div class="refresh-info" id="refresh-info"></div>
<div id="health-bar" style="font-size:.65rem;color:#484f58;margin-top:4px;display:flex;gap:16px"></div>

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
    renderPos('positions-pf', d3.positions);
    
    // Trades per column
    const renderTrades = (id, trades) => {
      if (trades && trades.length > 0) {
        document.getElementById(id).innerHTML = trades.slice(0,5).map(t => {
          const c = t.pnl >= 0 ? 'positive' : 'negative';
          const s = t.pnl >= 0 ? '+' + t.pnl.toFixed(0) : t.pnl.toFixed(0);
          return `<div style="border-bottom:1px solid #21262d;padding:2px 0">${t.ticker} ${t.direction} <span class="${c}">${s}₽</span></div>`;
        }).join('');
      }
    };
    renderTrades('trades-sh', d.trades);
    renderTrades('trades-ir', d2.trades);
    renderTrades('trades-pf', d3.trades);
    
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
    
    document.getElementById('refresh-info').textContent = 'updated: ' + (d.updated_at || '—');
    // Mini equity charts per column
    const renderChart = (elId, curve, color) => {
      if (curve && curve.length > 1) {
        Plotly.react(elId, [{
          x: curve.map(p => p.t), y: curve.map(p => p.e),
          type: 'scatter', mode: 'lines',
          line: {color, width: 1.5},
          fill: 'tozeroy', fillcolor: color + '18',
        }], {
          paper_bgcolor: 'transparent', plot_bgcolor: 'transparent',
          font: {color: '#484f58', size: 8},
          margin: {l:0, r:0, t:0, b:0},
          xaxis: {showgrid:false, visible:false},
          yaxis: {showgrid:false, visible:false},
          hovermode: false,
          showlegend: false,
        });
      }
    };
    renderChart('chart-sh', d.equity_curve, '#58a6ff');
    renderChart('chart-ir', d2.equity_curve, '#d29922');
    renderChart('chart-pf', d3.equity_curve, '#3fb950');
    
    // Health info
    try {
      const hr = await fetch('/api/health');
      const h = await hr.json();
      const hh = h.strategies || {};
      const tick = (s) => s && s.tick ? s.tick.slice(11,19) : '—';
      const age = h.bar_age_min !== null && h.bar_age_min !== undefined ? h.bar_age_min + 'm' : '—';
      const barColor = h.bar_age_min !== null && h.bar_age_min < 10 ? '#3fb950' : h.bar_age_min < 60 ? '#d29922' : '#f85149';
      document.getElementById('health-bar').innerHTML = [
        `🕐 SH: ${tick(hh.stop_hunt)}`,
        `🕐 IR: ${tick(hh.impulse_return)}`,
        `🕐 PF: ${tick(hh.portfolio)}`,
        `<span style="color:${barColor}">📊 data: ${age} ago</span>`,
      ].join(' | ');
    } catch(e) {}
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
        elif self.path == '/api/health':
            self._health()
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
    
    def _health(self):
        try:
            pg = psycopg2.connect(**PG, connect_timeout=5)
            cur = pg.cursor()
            def get_tick(tbl):
                try:
                    cur.execute(f"SELECT updated_at FROM futures.{tbl} ORDER BY updated_at DESC LIMIT 1")
                    r = cur.fetchone()
                    return str(r[0])[:19] if r and r[0] else None
                except: return None
            sh = get_tick('paper_state_stop_hunt')
            ir = get_tick('paper_state_impulse_return')
            pf = get_tick('paper_state_portfolio')
            cur.close(); pg.close()
        except: sh=ir=pf=None
        try:
            ch = cc.get_client(host=CH_HOST, port=8123, database=CH_DB)
            r = ch.query("SELECT max(bt) as max_bt, now() FROM moex.prices_5min WHERE ticker='Si'")
            lb = r.result_rows[0][0]
            nw = r.result_rows[0][1]
            ch.close()
        except: lb=None; nw=None
        age = round((nw - lb).total_seconds()/60,1) if nw and lb else None
        self._json({'strategies':{'stop_hunt':sh,'impulse_return':ir,'portfolio':pf},
                     'last_bar':str(lb)[:19] if lb else None,'now':str(nw)[:19] if nw else None,'bar_age_min':age})
    
    def log_message(self, *a): pass

srv = http.server.ThreadingHTTPServer((HOST, PORT), Handler)
print(f'Dashboard: http://{HOST}:{PORT}')
srv.serve_forever()
