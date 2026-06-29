#!/usr/bin/env python3
"""Minimal dashboard for TQA-MOEX-futures PaperTrader.

Serves HTTP on :8080, reads state from PG.
"""
import os, json
from http.server import HTTPServer, BaseHTTPRequestHandler
import psycopg2

PG = dict(host='10.0.0.60', port=5432, dbname='moex', user='user')

def get_state():
    conn = psycopg2.connect(**PG, connect_timeout=3)
    cur = conn.cursor()
    cur.execute("SELECT key, value FROM futures.paper_state")
    state = {r[0]: r[1] for r in cur.fetchall()}
    cur.close()
    conn.close()
    return state

def get_portfolio():
    conn = psycopg2.connect(**PG, connect_timeout=3)
    cur = conn.cursor()
    cur.execute("""
        SELECT p.ticker, p.strategy, p.enabled
        FROM futures.portfolio p
        WHERE p.enabled = true
        ORDER BY p.ticker, p.strategy
    """)
    rows = cur.fetchall()
    # Add latest price per ticker
    result = []
    for r in rows:
        cur.execute("SELECT prc, bt FROM futures.prices WHERE ticker=%s ORDER BY bt DESC LIMIT 1", (r[0],))
        pr = cur.fetchone()
        result.append([r[0], r[1], r[2], float(pr[0]) if pr else 0, str(pr[1])[:19] if pr and pr[1] else '-'])
    cur.close()
    conn.close()
    return result

HTML = """<!DOCTYPE html>
<html><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>TQA-MOEX-futures Dashboard</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:system-ui,-apple-system,sans-serif;background:#0d1117;color:#c9d1d9;padding:20px;max-width:900px;margin:auto}
h1{color:#58a6ff;margin-bottom:20px}
.card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px;margin-bottom:16px}
.card h2{color:#8b949e;font-size:14px;text-transform:uppercase;margin-bottom:8px}
.metric{display:inline-block;margin-right:32px;margin-bottom:8px}
.metric .val{font-size:24px;font-weight:600;color:#58a6ff}
.metric .label{font-size:12px;color:#8b949e}
.metric.pos .val{color:#3fb950}
.metric.neg .val{color:#f85149}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;color:#8b949e;border-bottom:1px solid #30363d;padding:6px 4px}
td{padding:4px;border-bottom:1px solid #21262d}
.enabled{color:#3fb950}.disabled{color:#8b949e}
.status-ok{color:#3fb950}.status-paused,.status-error{color:#f85149}
</style>
</head><body>
<h1>📊 TQA-MOEX-futures</h1>
<div class=card>
<h2>PaperTrader</h2>
<div class="metric"><div class="val" id=equity>--</div><div class=label>Equity (RUB)</div></div>
<div class="metric"><div class="val" id=return>--</div><div class=label>Return %</div></div>
<div class="metric"><div class="val" id=trades>--</div><div class=label>Trades</div></div>
<div class="metric"><div class="val" id=open_pos>--</div><div class=label>Open</div></div>
<div class="metric" id=mdd_c><div class="val" id=mdd>--</div><div class=label>Max DD %</div></div>
</div>

<div class=card>
<h2>Позиции</h2>
<table><thead><tr><th>Тикер</th><th>Направление</th><th>Стратегия</th><th>Вход</th><th>Контр</th><th>PnL</th></tr></thead>
<tbody id=positions></tbody></table>
</div>

<div class=card>
<h2>Портфель</h2>
<table><thead><tr><th>Тикер</th><th>Стратегия</th><th>Статус</th><th>Цена</th><th>Обновлено</th></tr></thead>
<tbody id=portfolio></tbody></table>
</div>

<script>
async function load(){
 try{
  let r=await fetch('/api/state'); let d=await r.json()
  document.getElementById('equity').textContent=d.equity.toLocaleString()
  document.getElementById('return').textContent=(d.return_pct||0).toFixed(1)+'%'
  document.getElementById('return').parentElement.className=d.return_pct>=0?'metric pos':'metric neg'
  document.getElementById('trades').textContent=d.n_trades||0
  document.getElementById('open_pos').textContent=(d.positions||[]).length
  document.getElementById('mdd').textContent=(d.mdd_pct||0).toFixed(1)+'%'
  document.getElementById('mdd_c').className=(d.mdd_pct||0)>20?'metric neg':'metric'

  let tbody=document.getElementById('positions'); tbody.innerHTML=''
  for(let p of d.positions||[]){
   let row=tbody.insertRow()
   row.insertCell().textContent=p.ticker
   row.insertCell().textContent=p.direction
   let pnlCell=row.insertCell(); pnlCell.textContent=p.strategy
   row.insertCell().textContent=p.entry.toFixed?p.entry.toFixed(2):p.entry
   row.insertCell().textContent=p.shares
   let pnl=row.insertCell(); pnl.textContent=(p.pnl||0).toFixed(0)
   pnl.style.color=(p.pnl||0)>=0?'#3fb950':'#f85149'
  }
 }catch(e){}
}

async function loadPortfolio(){
 try{
  let r=await fetch('/api/portfolio'); let rows=await r.json()
  let tbody=document.getElementById('portfolio'); tbody.innerHTML=''
  for(let r of rows){
   let tr=tbody.insertRow()
   tr.insertCell().textContent=r[0]
   tr.insertCell().textContent=r[1]
   let s=tr.insertCell(); s.textContent=r[2]?'active':'off'; s.className=r[2]?'enabled':'disabled'
   tr.insertCell().textContent=r[3]
   tr.insertCell().textContent=r[4]
  }
 }catch(e){}
}

load();loadPortfolio();setInterval(load,5000);setInterval(loadPortfolio,15000)
</script>
</body></html>"""

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            if self.path == '/':
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.end_headers()
                self.wfile.write(HTML.encode())
            elif self.path == '/api/state':
                state = get_state()
                data = {'equity': 100000, 'return_pct': 0, 'n_trades': 0, 'mdd_pct': 0, 'positions': []}
                if 'capital' in state:
                    data['equity'] = float(state['capital'])
                if 'positions' in state:
                    data['positions'] = json.loads(state['positions'])
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(data).encode())
            elif self.path == '/api/portfolio':
                rows = get_portfolio()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(rows).encode())
            else:
                self.send_response(404)
                self.end_headers()
        except Exception as e:
            self.send_response(500)
            self.send_header('Content-Type', 'text/plain')
            self.end_headers()
            self.wfile.write(str(e).encode())
            self.end_headers()

if __name__ == '__main__':
    port = int(os.getenv('PORT', '8080'))
    server = HTTPServer(('0.0.0.0', port), Handler)
    print(f'Dashboard on http://0.0.0.0:{port}')
    server.serve_forever()
