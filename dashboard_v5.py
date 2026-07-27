#!/usr/bin/env python3
"""Dashboard for portfolio_v5 — общий портфель + по стратегиям."""
import os, json
from http.server import HTTPServer, BaseHTTPRequestHandler
from collections import defaultdict
import psycopg2

PG = dict(host='10.0.0.60', port=5432, dbname='moex', user='postgres')
SK = 'portfolio_v5'

def q(sql, p=None):
    conn = psycopg2.connect(**PG, connect_timeout=3)
    cur = conn.cursor()
    cur.execute(sql, p or ())
    r = cur.fetchall()
    cur.close(); conn.close()
    return r

def get_state():
    rows = q(f"SELECT capital, equity, peak, mtm_equity, mtm_peak, positions_json, updated_at FROM futures.paper_state_{SK} ORDER BY updated_at DESC LIMIT 1")
    if not rows: return {}
    r = rows[0]
    return {'eq': float(r[0]), 'eq_cash': float(r[1]), 'peak': float(r[2]),
            'mtm_eq': float(r[3] or r[1]), 'mtm_pk': float(r[4] or r[2]),
            'positions': json.loads(r[5]) if r[5] else [], 'ts': str(r[6])[:19]}

def get_trades():
    return q(f"SELECT ticker, strategy, direction, pnl_rub, exit_reason, entry_time FROM futures.paper_trades_{SK} ORDER BY exit_time DESC NULLS LAST LIMIT 50")

def get_strategy_summary():
    rows = q(f"""
        SELECT strategy, 
               count(*) as n,
               sum(pnl_rub) as pnl,
               sum(CASE WHEN pnl_rub>0 THEN 1 ELSE 0 END) as wins,
               sum(CASE WHEN pnl_rub>0 THEN pnl_rub ELSE 0 END) as wp,
               sum(CASE WHEN pnl_rub<0 THEN abs(pnl_rub) ELSE 0 END) as lp
        FROM futures.paper_trades_{SK}
        GROUP BY strategy ORDER BY strategy
    """)
    result = []
    for r in rows:
        n = int(r[1])
        wins = int(r[3])
        wr = wins/n*100 if n > 0 else 0
        pf = round(float(r[4])/float(r[5]), 2) if r[5] and float(r[5]) > 0 else 0
        result.append({'s': r[0], 'n': n, 'pnl': float(r[2] or 0), 'wr': round(wr, 1), 'pf': pf})
    return result

HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>MOEX Portfolio v5</title><style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:system-ui,-apple-system,sans-serif;background:#0d1117;color:#c9d1d9;padding:20px;max-width:1100px;margin:auto}
h1{color:#58a6ff;margin-bottom:20px;font-size:22px}
.card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px;margin-bottom:16px}
.card h2{color:#8b949e;font-size:13px;text-transform:uppercase;margin-bottom:10px;letter-spacing:0.5px}
.m{display:inline-block;margin-right:28px;margin-bottom:6px}
.m .v{font-size:20px;font-weight:600;color:#58a6ff}
.m .l{font-size:11px;color:#8b949e;margin-top:1px}
.pos .v{color:#3fb950}.neg .v{color:#f85149}
table{width:100%;border-collapse:collapse;font-size:13px}
th{color:#8b949e;border-bottom:1px solid #30363d;padding:6px 4px;text-align:left;font-weight:500}
td{padding:4px;border-bottom:1px solid #21262d}
.p-pos{color:#3fb950}.p-neg{color:#f85149}
.strat-bar{display:flex;gap:12px;flex-wrap:wrap}
.strat-card{background:#0d1117;border:1px solid #21262d;border-radius:6px;padding:12px;min-width:160px;flex:1}
.strat-card .sn{color:#c9d1d9;font-size:14px;font-weight:600;margin-bottom:6px}
.strat-card .sm{font-size:12px;color:#8b949e;line-height:1.6}
.strat-card .sv{font-weight:600}
.tt{color:#8b949e;font-size:11px;margin-top:8px}
</style></head><body>
<h1>MOEX Portfolio v5</h1>

<div class=card><h2>Общий портфель</h2>
<div class="m"><div class="v" id=eq>--</div><div class=l>Cash (Capital+Closed)</div></div>
<div class="m"><div class="v" id=meq>--</div><div class=l>MTM Equity (+Floating)</div></div>
<div class="m" id=rc><div class="v" id=ret>--</div><div class=l>Return %</div></div>
<div class="m" id=mddc><div class="v" id=mdd>--</div><div class=l>MTM DD %</div></div>
<div class="m"><div class="v" id=floating>--</div><div class=l>Floating PnL</div></div>
<div class="m"><div class="v" id=closed_pnl>--</div><div class=l>Closed PnL</div></div>
<div class="m"><div class="v" id=cdd>--</div><div class=l>Cash DD %</div></div>
<div class="m"><div class="v" id=open>--</div><div class=l>Open</div></div>
<div class="m"><div class="v" id=closed>--</div><div class=l>Closed</div></div>
<div class="tt" id=ts></div>
</div>

<div class=card><h2>По стратегиям</h2>
<div class=strat-bar id=strats></div>
</div>

<div class=card><h2>Открытые позиции</h2>
<table><thead><tr><th>Тикер</th><th>Стратегия</th><th>Dir</th><th>Вход</th><th>Контр</th><th>PnL</th><th>PnL%</th></tr></thead>
<tbody id=pos></tbody></table></div>

<div class=card><h2>История сделок</h2>
<table><thead><tr><th>Тикер</th><th>Стратегия</th><th>Dir</th><th>PnL</th><th>Выход</th><th>Время</th></tr></thead>
<tbody id=trades></tbody></table></div>

<script>
async function load(){
 try{
  let r=await fetch('/api/state'); let d=await r.json()
  document.getElementById('eq').textContent=d.eq.toLocaleString()
  document.getElementById('meq').textContent=d.meq.toLocaleString()
  document.getElementById('rc').className='m'+(d.ret>=0?' pos':' neg')
  document.getElementById('ret').textContent=d.ret.toFixed(1)+'%'
  document.getElementById('mdd').textContent=d.mdd.toFixed(1)+'%'
  document.getElementById('mddc').className=d.mdd>20?'m neg':'m'
  document.getElementById('cdd').textContent=d.cdd.toFixed(1)+'%'
  let fl=d.meq-d.eq; document.getElementById('floating').textContent=(fl>=0?'+':'')+fl.toFixed(0)+'₽'
  document.getElementById('floating').parentElement.className='m'+(fl>=0?' pos':' neg')
  let cp=d.closed_pnl||0; document.getElementById('closed_pnl').textContent=(cp>=0?'+':'')+cp.toFixed(0)+'₽'
  document.getElementById('closed_pnl').parentElement.className='m'+(cp>=0?' pos':' neg')
  document.getElementById('open').textContent=d.open
  document.getElementById('closed').textContent=d.closed
  document.getElementById('ts').textContent='Updated: '+d.ts

  let tb=document.getElementById('pos'); tb.innerHTML=''
  for(let p of d.positions){
   let tr=tb.insertRow()
   tr.insertCell().textContent=p.ticker||'?'
   tr.insertCell().textContent=p.strategy||'?'
   tr.insertCell().textContent=p.direction||'?'
   tr.insertCell().textContent=(p.entry_price||0).toFixed(1)
   tr.insertCell().textContent=p.shares||p.contracts||1
   let pnl=tr.insertCell(); pnl.textContent=(p.pnl||0).toFixed(0); pnl.className=(p.pnl||0)>=0?'p-pos':'p-neg'
   let pp=tr.insertCell()
   if(p.entry_price>0){let s=p.shares||p.contracts||1;let pct=((p.pnl||0)/(p.entry_price*s)*100).toFixed(2)+'%';pp.textContent=pct}
  }
 }catch(e){}
}

async function loadStrats(){
 try{
  let r=await fetch('/api/strats'); let d=await r.json()
  let div=document.getElementById('strats'); div.innerHTML=''
  for(let s of d.strats){
   let c=document.createElement('div'); c.className='strat-card'
   c.innerHTML='<div class=sn>'+s.s+'</div><div class=sm>'
   +'Closed: <span class=sv>'+(s.n||0)+'</span><br>'
   +'PnL: <span class=sv style=color:'+(s.pnl>=0?'#3fb950':'#f85149')+'>'+s.pnl.toFixed(0)+'₽</span><br>'
   +'WR: <span class=sv>'+s.wr+'%</span><br>'
   +'PF: <span class=sv>'+s.pf+'</span>'
   div.appendChild(c)
  }
 }catch(e){}
}

async function loadTrades(){
 try{
  let r=await fetch('/api/trades'); let rows=await r.json()
  let tb=document.getElementById('trades'); tb.innerHTML=''
  for(let t of rows){
   let tr=tb.insertRow()
   tr.insertCell().textContent=t.t
   tr.insertCell().textContent=t.s||''
   tr.insertCell().textContent=t.d
   let pnl=tr.insertCell(); pnl.textContent=(t.p||0).toFixed(0); pnl.className=(t.p||0)>=0?'p-pos':'p-neg'
   tr.insertCell().textContent=t.r||''
   tr.insertCell().textContent=(t.time||'').slice(0,19)
  }
 }catch(e){}
}

load();loadStrats();loadTrades()
setInterval(load,5000);setInterval(loadTrades,10000)
</script></body></html>"""

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            if self.path == '/':
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.end_headers()
                self.wfile.write(HTML.encode())
            elif self.path == '/api/state':
                s = get_state()
                eq = s.get('eq', 200000); pk = s.get('peak', eq)
                meq = s.get('mtm_eq', eq); mpk = s.get('mtm_pk', pk)
                positions = s.get('positions', [])
                d = {'eq': eq, 'meq': meq, 'ret': (eq/200000-1)*100,
                     'mdd': (mpk-meq)/mpk*100 if mpk > 0 else 0,
                     'cdd': (pk-eq)/pk*100 if pk > 0 else 0,
                     'open': len(positions), 'closed': 0, 'positions': positions, 'ts': s.get('ts','')}
                r = q(f"SELECT count(*), COALESCE(sum(pnl_rub),0) FROM futures.paper_trades_{SK}")
                d['closed'] = r[0][0] if r else 0
                d['closed_pnl'] = float(r[0][1] or 0) if r else 0
                self.send_json(d)
            elif self.path == '/api/strats':
                rows = q(f"""
                    SELECT strategy, count(*), sum(pnl_rub),
                           sum(CASE WHEN pnl_rub>0 THEN 1 ELSE 0 END),
                           sum(CASE WHEN pnl_rub>0 THEN pnl_rub ELSE 0 END),
                           sum(CASE WHEN pnl_rub<0 THEN abs(pnl_rub) ELSE 0 END)
                    FROM futures.paper_trades_{SK} GROUP BY strategy ORDER BY strategy
                """)
                strats = []
                for r in rows:
                    n = int(r[1]); wins = int(r[3])
                    wr = round(wins/n*100, 1) if n > 0 else 0
                    pf = round(float(r[4])/float(r[5]), 2) if r[5] and float(r[5]) > 0 else 0
                    strats.append({'s': r[0], 'n': n, 'pnl': float(r[2] or 0), 'wr': wr, 'pf': pf})
                # Add open positions per strategy
                s = get_state()
                for p in s.get('positions', []):
                    st = p.get('strategy', '?')
                    for x in strats:
                        if x['s'] == st:
                            x['open'] = x.get('open', 0) + 1
                            break
                    else:
                        strats.append({'s': st, 'n': 0, 'pnl': 0, 'wr': 0, 'pf': 0, 'open': 1})
                for x in strats:
                    if 'open' not in x: x['open'] = 0
                self.send_json({'strats': strats})
            elif self.path == '/api/trades':
                rows = q(f"SELECT ticker, strategy, direction, pnl_rub, exit_reason, entry_time FROM futures.paper_trades_{SK} ORDER BY exit_time DESC LIMIT 30")
                data = [{'t': r[0], 's': r[1], 'd': r[2], 'p': float(r[3] or 0), 'r': r[4], 'time': str(r[5])[:19] if r[5] else ''} for r in rows]
                self.send_json(data)
            else:
                self.send_response(404); self.end_headers()
        except Exception as e:
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain'); self.end_headers()
            self.wfile.write(str(e).encode())

    def send_json(self, data):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

if __name__ == '__main__':
    port = int(os.getenv('PORT', '8080'))
    print(f'Dashboard v5: http://0.0.0.0:{port}')
    HTTPServer(('0.0.0.0', port), Handler).serve_forever()
