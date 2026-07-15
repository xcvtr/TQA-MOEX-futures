#!/usr/bin/env python3
"""Dashboard — data-driven: стратегии из массива, не хардкод."""
import os, json, http.server, psycopg2, clickhouse_connect as cc
from datetime import datetime, timezone

HOST = '0.0.0.0'
PORT = 8087
PG = dict(host='10.0.0.60', port=5432, dbname='moex', user='postgres')
CH_HOST = '10.0.0.60'
CH_DB = 'moex'

# ═══════════════════════════════════════════
# ЕДИНСТВЕННОЕ МЕСТО — список стратегий
# Новая стратегия = добавить сюда, всё остальное само
# ═══════════════════════════════════════════
STRATEGIES = [
    {'key': 'stop_hunt',      'name': 'Stop Hunt',      'icon': '🔹', 'color': '#58a6ff'},
    {'key': 'impulse_return', 'name': 'Impulse Return', 'icon': '🔸', 'color': '#d29922'},
    {'key': 'dragon',         'name': 'Dragon',         'icon': '🐉', 'color': '#bf7fff'},
    {'key': 'portfolio',      'name': 'Portfolio All',  'icon': '🔷', 'color': '#3fb950'},
]
PORTFOLIO_KEY = 'portfolio'

def query(q, params=None):
    pg = psycopg2.connect(**PG)
    cur = pg.cursor()
    cur.execute(q, params or ())
    cols = [d[0] for d in cur.description] if cur.description else []
    rows = cur.fetchall()
    cur.close(); pg.close()
    return cols, rows

# Генерируем HTML + JS из STRATEGIES
cols_html = ''.join(
    f'  <div class="col" id="col-{s["key"]}">'
    f'<h2 style="color:{s["color"]}">{s["icon"]} {s["name"]}</h2>'
    f'<div class="dashboard" id="stats-{s["key"]}"></div>'
    f'<div id="chart-{s["key"]}" style="height:{"120" if s["key"]==PORTFOLIO_KEY else "70"}px;margin:6px 0"></div>'
    f'<h3 style="font-size:.75rem;color:#8b949e;margin:8px 0 4px">Позиции</h3>'
    f'<div id="positions-{s["key"]}" style="font-size:.75rem;color:#484f58">—</div>'
    f'<h3 style="font-size:.75rem;color:#8b949e;margin:8px 0 4px">Сделки</h3>'
    f'<div id="trades-{s["key"]}" style="font-size:.7rem;color:#484f58">—</div></div>\n'
    for s in STRATEGIES
)

strategies_js = json.dumps(STRATEGIES)

HTML = f'''<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>MOEX Futures Dashboard</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:'Segoe UI',sans-serif;background:#0d1117;color:#e6edf3;padding:16px}}
h1{{font-size:1.3rem;margin-bottom:12px;color:#58a6ff}}
.dashboard{{display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:8px;margin-bottom:8px}}
.card{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:12px}}
.card h3{{font-size:.7rem;color:#8b949e;margin-bottom:3px}}
.card .val{{font-size:1.3rem;font-weight:700}}
.card .sub{{font-size:.7rem;color:#8b949e;margin-top:2px}}
.col{{flex:1;background:#0d1117;border:1px solid #21262d;border-radius:10px;padding:12px}}
.col h2{{font-size:.85rem;margin-bottom:6px;padding-bottom:6px;border-bottom:1px solid #21262d}}
.positive{{color:#3fb950}};.negative{{color:#f85149}}
.chart-box{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:14px;margin-bottom:12px}}
.refresh-info{{text-align:right;font-size:.7rem;color:#484f58;margin-top:8px}}
</style>
</head>
<body>
<h1>📊 MOEX Futures</h1>
<div style="display:flex;gap:10px;margin-bottom:12px;align-items:stretch">
{cols_html}
</div>
<div class="refresh-info" id="refresh-info"></div>
<div id="health-bar" style="font-size:.65rem;color:#484f58;margin-top:4px;display:flex;gap:16px"></div>

<script>
const STRATEGIES = {strategies_js};
const PORTFOLIO_KEY = '{PORTFOLIO_KEY}';

async function load() {{
  try {{
    const r = await fetch('/api/state?strategy=' + PORTFOLIO_KEY);
    const d = await r.json();
    const init = d.capital;
    const allPositions = d.positions || [];

    for (const s of STRATEGIES) {{
      const positions = s.key === PORTFOLIO_KEY ? allPositions : allPositions.filter(p => p.strategy === s.key);
      const count = positions.length;
      const eq = d.equity || init;
      const floating = positions.reduce((s, p) => s + (p.unrealized_pnl || 0), 0);
      const totalEq = eq + floating;
      const ret = ((eq/init)-1)*100;
      const cls = ret >= 0 ? 'positive' : 'negative';
      const floatCls = floating >= 0 ? 'positive' : 'negative';

      document.getElementById('stats-' + s.key).innerHTML = [
        '<div class="card"><h3>Floating PnL</h3><div class="val ' + floatCls + '">' + (floating >= 0 ? '+' : '') + floating.toFixed(0) + ' ₽</div><div class="sub">unrealized</div></div>',
        '<div class="card"><h3>Equity+UPnL</h3><div class="val ' + floatCls + '">' + totalEq.toFixed(0) + ' ₽</div><div class="sub">capital + floating</div></div>',
        '<div class="card"><h3>MTM DD</h3><div class="val">' + (d.mtm_dd_pct || 0).toFixed(2) + '%</div><div class="sub">mark-to-market</div></div>',
        '<div class="card"><h3>Equity</h3><div class="val ' + cls + '">' + eq.toLocaleString() + ' ₽</div><div class="sub">start: ' + init.toLocaleString() + ' ₽</div></div>',
        '<div class="card"><h3>Positions</h3><div class="val">' + count + '</div><div class="sub">open / ' + (d.n_trades || 0) + ' total</div></div>',
      ].join('');

      // Positions
      if (positions.length > 0) {{
        document.getElementById('positions-' + s.key).innerHTML = positions.map(p =>
          '<div style="display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid #21262d">' +
            '<span><b>' + p.ticker + '</b> ' + p.direction + '</span>' +
            '<span>entry: ' + p.entry_price + '</span>' +
            '<span class="' + (p.unrealized_pnl >= 0 ? 'positive' : 'negative') + '">' + (p.unrealized_pnl >= 0 ? '+' : '') + p.unrealized_pnl.toFixed(0) + '₽</span>' +
            '<span>bars: ' + (p.bars_held !== undefined ? p.bars_held : '—') + '</span>' +
            '<span>MTM DD: ' + (d.mtm_dd_pct || 0).toFixed(2) + '%</span>' +
          '</div>'
        ).join('');
      }} else {{
        document.getElementById('positions-' + s.key).innerHTML = '<span style="color:#484f58">— нет открытых позиций</span>';
      }}

      // Trades
      const trades = d.trades || [];
      if (trades.length > 0) {{
        document.getElementById('trades-' + s.key).innerHTML = trades.slice(0,5).map(t => {{
          const c = t.pnl >= 0 ? 'positive' : 'negative';
          const v = t.pnl >= 0 ? '+' + t.pnl.toFixed(0) : t.pnl.toFixed(0);
          return '<div style="border-bottom:1px solid #21262d;padding:2px 0">' + t.ticker + ' ' + t.direction + ' <span class="' + c + '">' + v + '₽</span></div>';
        }}).join('');
      }}
    }}

    document.getElementById('refresh-info').textContent = 'updated: ' + (d.updated_at || '—');
  }} catch(e) {{
    document.getElementById('stats-' + PORTFOLIO_KEY).innerHTML = '<div class="card"><h3>Error</h3><div class="val negative">' + e.message + '</div></div>';
    console.error('Dashboard error:', e.message, e.stack);
  }}
}}

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
            cols, rows = query(f"SELECT capital, equity, peak, mtm_equity, mtm_peak, positions_json, updated_at FROM {tbl}")
            if not rows:
                self._json({'error': 'no data'})
                return
            d = dict(zip(cols, rows[0]))
            positions = json.loads(d.get('positions_json', '[]') or '[]')
            open_positions = [p for p in positions if not p.get('closed', False)]
            if open_positions:
                tickers = list(set(p['ticker'] for p in open_positions))
                try:
                    ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')
                    prices = {}
                    for ticker in tickers:
                        row = ch.query(f"SELECT argMax(prc, bt) FROM moex.prices_5min WHERE ticker='{ticker}'").result_rows
                        if row and row[0][0]:
                            prices[ticker] = float(row[0][0])
                    ch.close()
                    specs = {}
                    pg2 = psycopg2.connect(**PG)
                    cur2 = pg2.cursor()
                    placeholders = ','.join(['%s'] * len(tickers))
                    cur2.execute(f"SELECT ticker, min_step, step_price FROM futures.ticker_specs WHERE ticker IN ({placeholders})", tickers)
                    for r in cur2.fetchall():
                        specs[r[0]] = {'ms': float(r[1]) if r[1] else 0.01, 'sp': float(r[2]) if r[2] else 1.0}
                    cur2.close(); pg2.close()
                    for p in positions:
                        if p.get('closed', False):
                            p['unrealized_pnl'] = 0; continue
                        ticker = p['ticker']
                        prc = prices.get(ticker)
                        if not prc: p['unrealized_pnl'] = 0; continue
                        s = specs.get(ticker, {'ms': 0.01, 'sp': 1.0})
                        ms, sp = s['ms'], s['sp']; entry = p['entry_price']
                        contracts = p.get('contracts', 1); pct = p.get('pct', 1.0)
                        rem = max(0.001, p.get('rem', 1)); tc = 4 * contracts
                        if p['direction'] == 'long': pnl = (prc - entry) / ms * sp * pct * rem - tc
                        else: pnl = (entry - prc) / ms * sp * pct * rem - tc
                        p['unrealized_pnl'] = round(pnl, 2)
                except Exception as e:
                    for p in positions: p['unrealized_pnl'] = 0
            equity = float(d['equity']); peak = float(d['peak']); capital = float(d['capital'])
            mtm_eq = float(d.get('mtm_equity', equity)); mtm_pk = float(d.get('mtm_peak', peak))
            mdd_pct = (peak - equity) / peak * 100 if peak > 0 else 0
            mtm_dd = (mtm_pk - mtm_eq) / mtm_pk * 100 if mtm_pk > 0 else 0
            result = {
                'capital': capital, 'equity': equity, 'peak': peak,
                'mtm_equity': mtm_eq, 'mtm_peak': mtm_pk,
                'mdd_pct': round(mdd_pct, 2), 'mtm_dd_pct': round(mtm_dd, 2),
                'positions': positions, 'n_trades': len(positions),
                'updated_at': str(d.get('updated_at', '')),
                'equity_curve': [], 'trades': [],
            }
            try:
                ch2 = cc.get_client(host='10.0.0.60', port=8123, database=CH_DB)
                eq_r = ch2.query(f"SELECT bt, equity FROM moex.futures_mtm WHERE state_key='{strategy or ''}' ORDER BY bt LIMIT 1000")
                if eq_r.result_rows:
                    result['equity_curve'] = [{'t': str(r[0])[:19], 'e': float(r[1])} for r in eq_r.result_rows]
                ch2.close()
            except: pass
            self._json(result)
        except Exception as e:
            self._json({'error': str(e)})

    def _health(self):
        try:
            ch = cc.get_client(host='10.0.0.60', port=8123, database=CH_DB)
            strategies_health = {}
            pg = psycopg2.connect(**PG)
            cur = pg.cursor()
            cur.execute("SELECT ticker, max(bt) FROM moex.prices_5min WHERE bt > now() - INTERVAL '1 day' GROUP BY ticker")
            max_bar = None
            for r in cur.fetchall():
                if max_bar is None or r[1] > max_bar: max_bar = r[1]
            cur.close(); pg.close()
            now = datetime.now(timezone.utc)
            age = (now - max_bar).total_seconds() / 60 if max_bar else None
            self._json({
                'strategies': {s['key']: str(datetime.now(timezone.utc)) for s in STRATEGIES},
                'last_bar': str(max_bar) if max_bar else None,
                'now': str(now),
                'bar_age_min': round(age, 1) if age else None,
            })
        except Exception as e:
            self._json({'error': str(e)})

    def _json(self, data):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode('utf-8'))

if __name__ == '__main__':
    http_server = http.server.HTTPServer((HOST, PORT), Handler)
    print(f'Dashboard on http://{HOST}:{PORT}')
    http_server.serve_forever()
