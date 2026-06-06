#!/usr/bin/env python3
"""
TQA Dashboard HTTP Server v3.
Сервирует дашборды на порту 5054 со сводной страницей,
таблицей результатов, equity и ссылками по символам/периодам.
"""
from http.server import HTTPServer, SimpleHTTPRequestHandler
import os, json, socket, time, sqlite3
from pathlib import Path
from collections import defaultdict
from socketserver import ThreadingMixIn


class ThreadingServer(ThreadingMixIn, HTTPServer):
    """Threaded HTTP server for concurrent requests."""
    allow_reuse_address = True
    daemon_threads = True

DASHBOARD_DIR = Path('/home/user/.hermes/cache/screenshots/tqa')
CLUSTERS_DB = Path(os.path.expanduser('~/.hermes/data/tqa_clusters.db'))
PORT = 5054

INDEX_CSS = """
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
       background: #0d1117; color: #e6edf3; padding: 30px 20px; max-width: 1400px; margin: 0 auto; }
h1 { font-size: 26px; margin-bottom: 4px; }
h1 small { font-size: 14px; color: #8b949e; font-weight: 400; }
.subtitle { color: #8b949e; margin-bottom: 24px; font-size: 13px; }
.section { margin-bottom: 32px; }
.section h2 { font-size: 16px; color: #58a6ff; border-bottom: 1px solid #21262d;
              padding-bottom: 6px; margin-bottom: 12px; cursor: pointer; }
.section h2:hover { color: #79c0ff; }
.section h2 .count { color: #8b949e; font-size: 12px; font-weight: 400; }
.card-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 8px; }
.card { background: #161b22; border: 1px solid #21262d; border-radius: 6px; padding: 10px 12px;
        transition: border-color 0.2s; }
.card:hover { border-color: #30363d; }
.card h3 { font-size: 13px; margin-bottom: 2px; }
.card h3 a { color: #58a6ff; text-decoration: none; }
.card h3 a:hover { text-decoration: underline; color: #79c0ff; }
.card .desc { font-size: 11px; color: #8b949e; }
.card .meta { font-size: 10px; color: #484f58; margin-top: 2px; }
.card .badge { display: inline-block; padding: 1px 6px; border-radius: 3px; font-size: 10px; font-weight: 600; }
.badge-win { background: #0d3b1e; color: #3fb950; }
.badge-loss { background: #3b0d0d; color: #f85149; }
.badge-ok { background: #1f2937; color: #e6edf3; }

table { width: 100%; border-collapse: collapse; font-size: 12px; margin-top: 8px; }
th { background: #161b22; color: #8b949e; text-align: left; padding: 6px 8px; border-bottom: 1px solid #21262d;
     font-weight: 600; font-size: 11px; }
td { padding: 4px 8px; border-bottom: 1px solid #1c2128; }
tr:hover td { background: #1c2128; }
.num { text-align: right; font-variant-numeric: tabular-nums; }
.sym-col { font-weight: 600; color: #e6edf3; }
.pnl-pos { color: #3fb950; }
.pnl-neg { color: #f85149; }
.wr-good { color: #3fb950; font-weight: 600; }
.wr-ok { color: #d29922; font-weight: 600; }
.wr-bad { color: #f85149; font-weight: 600; }

.equity-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 12px; }
.equity-card { background: #161b22; border: 1px solid #21262d; border-radius: 8px; padding: 12px; }
.equity-card h3 { font-size: 14px; margin-bottom: 4px; }
.equity-card img { width: 100%; border-radius: 4px; margin-top: 8px; }
.equity-card .stats { display: flex; gap: 12px; font-size: 11px; margin-top: 4px; }
.equity-card .stats span { color: #8b949e; }
.equity-card .stats strong { color: #e6edf3; }

.toggle-content { display: none; }
.toggle-content.open { display: block; }
"""

INDEX_HEADER = """<!DOCTYPE html>
<html lang="ru">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>TQA Dashboards</title><style>{css}</style>
<script src="https://cdn.plot.ly/plotly-2.32.0.min.js"></script>
</head>
<body>
<h1>📊 TQA Dashboards <small>{hostname}:{port}</small></h1>
<p class="subtitle">
  <a href="/_list" style="color:#8b949e">JSON API</a>
  &middot; <a href="/_summary" style="color:#8b949e">Summary JSON</a>
  &middot; <a href="/_equity" style="color:#8b949e">Equity JSON</a>
  &middot; last update: {updated}
</p>
<script>
function toggle(sectionId) {{
  var el = document.getElementById(sectionId);
  if (el) el.classList.toggle('open');
}}
</script>"""


def get_db_summary():
    """Get summary stats from clusters DB."""
    if not CLUSTERS_DB.exists():
        return None
    conn = sqlite3.connect(str(CLUSTERS_DB))
    cur = conn.cursor()
    
    # Per-symbol summary
    cur.execute("""
        SELECT symbol, COUNT(*), 
               SUM(won) as wins, ROUND(AVG(won)*100, 1) as wr,
               ROUND(SUM(pnl_pips), 1) as total_pnl,
               ROUND(AVG(CASE WHEN won=1 THEN pnl_pips END), 1) as avg_win,
               ROUND(AVG(CASE WHEN won=0 THEN pnl_pips END), 1) as avg_loss
        FROM clusters GROUP BY symbol ORDER BY total_pnl DESC
    """)
    by_symbol = []
    for row in cur.fetchall():
        symbol, trades, wins, wr, pnl, avg_w, avg_l = row
        by_symbol.append({
            'symbol': symbol.upper(), 'trades': trades, 'wins': wins or 0,
            'wr': wr or 0, 'pnl': pnl or 0, 'avg_win': avg_w or 0, 'avg_loss': avg_l or 0,
        })
    
    # Per-year summary
    cur.execute("""
        SELECT year, COUNT(*), SUM(won), ROUND(AVG(won)*100,1),
               ROUND(SUM(pnl_pips),1)
        FROM clusters GROUP BY year ORDER BY year
    """)
    by_year = [{'year': r[0], 'trades': r[1], 'wins': r[2] or 0, 'wr': r[3] or 0, 'pnl': r[4] or 0}
               for r in cur.fetchall()]
    
    # Totals
    cur.execute("SELECT COUNT(*), SUM(won), ROUND(AVG(won)*100,1), ROUND(SUM(pnl_pips),1) FROM clusters")
    t = cur.fetchone()
    total = {'trades': t[0], 'wins': t[1] or 0, 'wr': t[2] or 0, 'pnl': t[3] or 0}
    
    # Recent clusters
    cur.execute("""
        SELECT id, symbol, name, year, month, our_side, pnl_pips, won, start_time
        FROM clusters ORDER BY id DESC LIMIT 20
    """)
    recent = [{'id': r[0], 'symbol': r[1].upper(), 'name': r[2], 'year': r[3], 'month': r[4],
               'side': r[5], 'pnl': r[6], 'won': r[7], 'date': r[8][:10]}
              for r in cur.fetchall()]
    
    conn.close()
    return {'by_symbol': by_symbol, 'by_year': by_year, 'total': total, 'recent': recent}


def build_index():
    """Build enhanced index page."""
    hostname = socket.gethostname()
    now = time.strftime("%Y-%m-%d %H:%M UTC")
    html = INDEX_HEADER.format(css=INDEX_CSS, hostname=hostname, port=PORT, updated=now)
    
    summary = get_db_summary()
    
    # === SECTION 0: Summary Table ===
    if summary:
        t = summary['total']
        html += f"""
<div class="section">
<h2 onclick="toggle('sec-summary')">📋 Сводка результатов ▾</h2>
<div id="sec-summary" class="toggle-content open">
<div style="display:flex;gap:16px;margin-bottom:8px;font-size:13px">
  <span>Всего сделок: <strong>{t['trades']}</strong></span>
  <span>WR: <strong class="{'wr-good' if t['wr']>=60 else 'wr-ok' if t['wr']>=40 else 'wr-bad'}">{t['wr']}%</strong></span>
  <span>PnL: <strong class="{'pnl-pos' if t['pnl']>=0 else 'pnl-neg'}">{t['pnl']:+.0f}p</strong></span>
</div>
<table>
<tr><th>Символ</th><th class="num">Сделок</th><th class="num">WR</th><th class="num">PnL</th><th class="num">Avg Win</th><th class="num">Avg Loss</th></tr>"""
        for s in summary['by_symbol']:
            wr_cls = 'wr-good' if s['wr'] >= 60 else 'wr-ok' if s['wr'] >= 40 else 'wr-bad'
            pnl_cls = 'pnl-pos' if s['pnl'] >= 0 else 'pnl-neg'
            html += f"""<tr>
  <td class="sym-col">{s['symbol']}</td>
  <td class="num">{s['trades']}</td>
  <td class="num {wr_cls}">{s['wr']}%</td>
  <td class="num {pnl_cls}">{s['pnl']:+.0f}p</td>
  <td class="num pnl-pos">{s['avg_win']:+.0f}p</td>
  <td class="num pnl-neg">{s['avg_loss']:+.0f}p</td>
</tr>"""
        html += """</table>"""
        
        # Per-year summary
        html += '<div style="margin-top:12px"><table><tr><th>Год</th><th class="num">Сделок</th><th class="num">WR</th><th class="num">PnL</th></tr>'
        for y in summary['by_year']:
            pnl_cls = 'pnl-pos' if y['pnl'] >= 0 else 'pnl-neg'
            html += f'<tr><td>{y["year"]}</td><td class="num">{y["trades"]}</td><td class="num">{y["wr"]}%</td><td class="num {pnl_cls}">{y["pnl"]:+.0f}p</td></tr>'
        html += '</table></div></div></div>'
    
    # === SECTION 1: Cluster Reports & Dashboards by Symbol ===
    # Match both *_report.html and *_dashboard.html files
    by_sym = defaultdict(list)
    
    for f in sorted(DASHBOARD_DIR.glob('*_report.html')):
        stem = f.stem
        parts = stem.split('_')
        if len(parts) >= 4:
            sym = parts[0]
            date_part = '_'.join(parts[1:3])
            by_sym[sym].append(('report', date_part, f.name))
    
    for f in sorted(DASHBOARD_DIR.glob('*_dashboard.html')):
        stem = f.stem
        parts = stem.split('_')
        # Handle: symbol_dashboard.html or symbol_YYYY_YYYY_dashboard.html
        if parts[-1] != 'dashboard' or len(parts) < 2:
            continue
        sym = parts[0]
        if len(parts) == 2:
            by_sym[sym].append(('dashboard', '', f.name))
        elif len(parts) >= 4:
            date_part = parts[1]
            by_sym[sym].append(('dashboard', date_part, f.name))
    
    symbols = ['audjpy','audusd','euraud','eurgbp','eurjpy','eurusd',
               'gbpjpy','gbpusd','nzdusd','usdcad','usdchf','usdjpy','xauusd']
    
    html += '<div class="section"><h2 onclick="toggle(\'sec-reports\')">📈 Отчёты по символам ▾</h2>'
    html += '<div id="sec-reports" class="toggle-content open">'
    for sym in symbols:
        reports = by_sym.get(sym, [])
        if not reports:
            html += f'<div style="margin:6px 0"><strong style="font-size:13px;color:#58a6ff">{sym.upper()}</strong>'
            html += '<div style="color:#484f58;font-size:11px;margin-top:2px">нет отчётов</div></div>'
            continue
        html += f'<div style="margin:6px 0"><strong style="font-size:13px;color:#58a6ff">{sym.upper()}</strong>'
        html += '<div class="card-grid" style="margin-top:4px">'
        for ftype, date_part, fname in sorted(reports, key=lambda x: x[1] or '', reverse=True):
            if ftype == 'report':
                label = date_part[5:7] + '/' + date_part[:4] if len(date_part) >= 7 else date_part
                desc = f'📊 {date_part}'
                html += f'<div class="card"><h3><a href="/{fname}">{label}</a></h3>'
                html += f'<div class="desc">{desc}</div></div>'
            else:
                label = '📈 дашборд'
                if date_part:
                    label = date_part
                html += f'<div class="card"><h3><a href="/{fname}">{label}</a></h3>'
                html += '<div class="desc">📈 Дашборд кластеров</div></div>'
        html += '</div></div>'
    html += '</div></div>'
    
    # === SECTION 2: Equity (live chart from DB) ===
    equity_chart_div = ''
    equity_stats = ''
    if summary:
        clusters_data = []
        conn = sqlite3.connect(str(CLUSTERS_DB))
        cur = conn.cursor()
        cur.execute("SELECT id, symbol, start_time, pnl_pips FROM clusters ORDER BY id")
        eq_rows = cur.fetchall()
        conn.close()
        if eq_rows:
            cum = 0
            equity_points = []
            for rid, sym, stime, pnl in eq_rows:
                cum += pnl
                equity_points.append({'id': rid, 'label': f'{sym}#{rid}', 'pnl': round(cum, 1)})
            total_pnl = equity_points[-1]['pnl'] if equity_points else 0
            wins = sum(1 for r in eq_rows if r[3] > 0)
            losses = sum(1 for r in eq_rows if r[3] <= 0)
            max_dd = 0
            peak = 0
            for p in equity_points:
                if p['pnl'] > peak:
                    peak = p['pnl']
                dd = peak - p['pnl']
                if dd > max_dd:
                    max_dd = dd
            equity_chart_json = json.dumps(equity_points)
            equity_stats = f'''
<div style="display:flex;gap:20px;margin-bottom:8px;font-size:13px">
  <span>Итоговый PnL: <strong class="{"pnl-pos" if total_pnl>=0 else "pnl-neg"}">{total_pnl:+.0f}p</strong></span>
  <span>Сделок: <strong>{len(eq_rows)}</strong> (w:{wins}/l:{losses})</span>
  <span>Max DD: <strong class="pnl-neg">{max_dd:.0f}p</strong></span>
  <span>WR: <strong class="{"wr-good" if wins/len(eq_rows)*100>=60 else "wr-ok" if wins/len(eq_rows)*100>=40 else "wr-bad"}">{wins/len(eq_rows)*100:.0f}%</strong></span>
</div>'''
            equity_chart_div = f'''
<div id="equity-chart" style="height:300px;background:#161b22;border:1px solid #30363d;border-radius:8px;margin-bottom:8px"></div>
<script>
(function(){{
  var pts = {equity_chart_json};
  var traces = [{{
    x: pts.map(p => '# ' + p.id),
    y: pts.map(p => p.pnl),
    mode: 'lines+markers',
    name: 'Equity',
    line: {{color: '#3fb950', width: 2}},
    marker: {{color: pts.map(p => p.pnl >= 0 ? '#3fb950' : '#f85149'), size: 8}},
    hovertemplate: '%{{x}}<br>%{{y:+.0f}}p<extra></extra>'
  }}];
  var layout = {{
    plot_bgcolor:'#161b22', paper_bgcolor:'#0d1117',
    font:{{color:'#e6edf3',size:11}},
    margin:{{l:60,r:20,t:5,b:30}},
    hovermode:'x',
    xaxis:{{showgrid:false, tickangle:-45, tickfont:{{size:9}}}},
    yaxis:{{gridcolor:'#30363d', title:'PnL (pips)', zerolinecolor:'#484f58'}},
    shapes: [
      {{type:'line', x0:-0.5, y0:0, x1:pts.length-0.5, y1:0,
        line:{{color:'#484f58',width:1,dash:'dot'}}}}
    ]
  }};
  Plotly.newPlot('equity-chart', traces, layout, {{responsive:true,displayModeBar:false}});
}})();
</script>'''
    
    if equity_chart_div:
        html += '<div class="section"><h2 onclick="toggle(\'sec-equity\')">📉 Equity (cumulative PnL) ▾</h2>'
        html += '<div id="sec-equity" class="toggle-content open">'
        html += equity_stats
        html += equity_chart_div
        # Also show any existing equity PNGs below the chart
        equity_pngs = sorted(DASHBOARD_DIR.glob('**/*equity*.png'))
        if equity_pngs:
            html += '<div class="equity-grid">'
            for f in equity_pngs:
                rel_path = f.relative_to(DASHBOARD_DIR)
                html += f'<div class="equity-card"><h3>{f.stem}</h3>'
                html += f'<img src="/{rel_path}" alt="{f.stem}"></div>'
            html += '</div>'
        html += '</div></div>'
    
    # === SECTION 3: All HTML Files ===
    html += '<div class="section"><h2 onclick="toggle(\'sec-all\')">📂 Все файлы ▸</h2>'
    html += '<div id="sec-all" class="toggle-content"><div class="card-grid">'
    for f in sorted(DASHBOARD_DIR.glob('*.html')):
        size_kb = f.stat().st_size // 1024
        html += f'<div class="card"><h3><a href="/{f.name}">{f.stem[:50]}</a></h3>'
        html += f'<div class="meta">{size_kb} KB</div></div>'
    html += '</div></div></div>'
    
    # === SECTION 4: Recent clusters ===
    if summary:
        html += '<div class="section"><h2 onclick="toggle(\'sec-recent\')">🆕 Последние кластеры ▾</h2>'
        html += '<div id="sec-recent" class="toggle-content open"><table><tr>'
        html += '<th>#</th><th>Символ</th><th>Кластер</th><th>Период</th><th>Наша сторона</th><th class="num">PnL</th><th></th></tr>'
        for c in summary['recent']:
            won_cls = 'badge-win' if c['won'] else 'badge-loss'
            won_icon = '✅' if c['won'] else '❌'
            pnl_cls = 'pnl-pos' if c['pnl'] >= 0 else 'pnl-neg'
            html += f'<tr><td>{c["id"]}</td><td class="sym-col">{c["symbol"]}</td>'
            html += f'<td>{c["name"]}</td><td>{c["year"]}-{c["month"]:02d}</td>'
            html += f'<td>{c["side"]}</td><td class="num {pnl_cls}">{c["pnl"]:+.0f}p</td>'
            html += f'<td><span class="badge {won_cls}">{won_icon}</span></td></tr>'
        html += '</table></div></div>'
    
    html += '</body></html>'
    return html


class DashboardHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DASHBOARD_DIR), **kwargs)
    
    def do_GET(self):
        if self.path in ('/', '/index.html'):
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(build_index().encode('utf-8'))
        elif self.path == '/_list':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            files = []
            for f in DASHBOARD_DIR.glob('*.html'):
                files.append({'name': f.name, 'size': f.stat().st_size, 'modified': f.stat().st_mtime})
            self.wfile.write(json.dumps(files, indent=2).encode('utf-8'))
        elif self.path == '/_summary':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            s = get_db_summary()
            self.wfile.write(json.dumps(s, indent=2, default=str).encode('utf-8'))
        elif self.path == '/_equity':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            files = []
            for f in DASHBOARD_DIR.glob('*equity*'):
                files.append({'name': f.name, 'size': f.stat().st_size, 'modified': f.stat().st_mtime})
            self.wfile.write(json.dumps(files, indent=2).encode('utf-8'))
        else:
            super().do_GET()
    
    def log_message(self, format, *args):
        pass


def main():
    hostname = socket.gethostname()
    server = ThreadingServer(('0.0.0.0', PORT), DashboardHandler)
    print(f"TQA Dashboards v3: http://0.0.0.0:{PORT}")
    print(f"  Local: http://localhost:{PORT}")
    print(f"  Network: http://{hostname}:{PORT}")
    print(f"  Files: {DASHBOARD_DIR}")
    print(f"  Clusters DB: {CLUSTERS_DB}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped")
        server.server_close()


if __name__ == '__main__':
    main()
