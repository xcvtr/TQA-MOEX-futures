#!/usr/bin/env python3
"""
GD Daily Smart Money Dashboard — визуализация сделок smart_money (yb↑ + fiz↓).
Отдельный порт: 5059.
"""
import sys, os, json, http.server, socketserver
from datetime import datetime, timedelta
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import clickhouse_connect
import numpy as np
from config import CH_HOST, CH_PORT, CH_DB

PORT = 5059
CS = 10
COMM = 4
HOLD = 10
SL = 0.01
CAPITAL = 100_000

ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)

def get_data():
    rows = ch.query("""
        SELECT toDate(p.time) as d,
               argMax(p.open, p.time) as open,
               argMax(p.high, p.time) as high,
               argMax(p.low, p.time) as low,
               argMax(p.close, p.time) as close,
               argMax(p.volume, p.time) as volume,
               argMax(o.yur_buy, p.time) as yur_buy,
               argMax(o.yur_sell, p.time) as yur_sell,
               argMax(o.fiz_buy, p.time) as fiz_buy,
               argMax(o.fiz_sell, p.time) as fiz_sell,
               argMax(o.total_oi, p.time) as total_oi
        FROM moex.prices_5m p
        INNER JOIN moex.prices_5m_oi o ON p.symbol = o.symbol AND p.time = o.time
        WHERE p.symbol = 'GD' AND p.time >= '2024-01-01' AND p.time <= '2026-05-01'
        GROUP BY d ORDER BY d
    """).result_rows
    
    dates = [str(r[0]) for r in rows]
    opn = np.array([r[1] for r in rows], dtype=float)
    high = np.array([r[2] for r in rows], dtype=float)
    low = np.array([r[3] for r in rows], dtype=float)
    close = np.array([r[4] for r in rows], dtype=float)
    volume = np.array([r[5] for r in rows], dtype=float)
    yb = np.array([r[6] for r in rows], dtype=float)
    ys = np.array([r[7] for r in rows], dtype=float)
    fb = np.array([r[8] for r in rows], dtype=float)
    fs = np.array([r[9] for r in rows], dtype=float)
    toi = np.array([r[10] for r in rows], dtype=float)
    toi = np.where(toi <= 0, 1, toi)
    
    dyb = np.diff(yb)
    fiz_net = (fb - fs) / toi * 100
    dfiz = np.diff(fiz_net)
    ret = np.diff(close) / close[:-1] * 100
    
    # Сигналы smart_money
    signals = []
    for i in range(1, len(dates) - HOLD - 1):
        if dyb[i] > 0 and dfiz[i] < 0:
            ei = i + 1
            xi = min(ei + HOLD, len(dates) - 1)
            ep = float(opn[ei])
            sp = ep * (1 - SL)
            stop_hit = False
            xp = float(close[xi])
            
            for j in range(ei, xi + 1):
                if float(low[j]) <= sp:
                    xp = sp
                    stop_hit = True
                    break
            
            go = ep * CS
            nc = max(1, int(CAPITAL // go)) if go > 0 else 1
            gp = nc * CS * (xp - ep)
            cm = nc * COMM
            npnl = round(gp - cm, 0)
            
            signals.append({
                'entry_idx': int(ei),
                'exit_idx': int(xi) if not stop_hit else min([j for j in range(ei, xi+1) if float(low[j]) <= sp]),
                'entry_time': dates[ei],
                'exit_time': dates[xi] if not stop_hit else dates[[j for j in range(ei, xi+1) if float(low[j]) <= sp][0]],
                'entry_price': float(ep),
                'exit_price': float(xp),
                'pnl': npnl,
                'pnl_pct': round(npnl / CAPITAL * 100, 2),
                'stop_hit': stop_hit,
                'contracts': nc,
                'dyb': float(dyb[i]),
                'dfiz': float(dfiz[i])
            })
    
    return {
        'dates': dates,
        'close': [round(c, 2) for c in close],
        'volume': [round(v, 0) for v in volume],
        'yur_buy': [round(y, 0) for y in yb],
        'yur_sell': [round(y, 0) for y in ys],
        'fiz_net': [round(f, 2) for f in fiz_net],
        'signals': signals
    }

HTML = """<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>GD Daily Smart Money</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font:14px/1.4 sans-serif;background:#0d1117;color:#c9d1d9;padding:20px}
h1{font-size:20px;margin-bottom:10px;color:#58a6ff}
h2{font-size:16px;margin:15px 0 8px;color:#8b949e}
.controls{margin-bottom:15px;display:flex;gap:10px;align-items:center}
.controls button,.controls input{background:#21262d;color:#c9d1d9;border:1px solid #30363d;padding:6px 12px;border-radius:4px;cursor:pointer;font:13px/1.4 sans-serif}
.controls button:hover{border-color:#58a6ff}
.stats{display:flex;gap:15px;flex-wrap:wrap;margin-bottom:15px}
.stat{background:#161b22;border:1px solid #30363d;border-radius:6px;padding:10px 15px;min-width:120px}
.stat .lbl{font-size:11px;color:#8b949e}
.stat .val{font-size:20px;font-weight:700;margin-top:2px}
canvas{width:100%;height:350px;display:block;background:#161b22;border:1px solid #30363d;border-radius:6px;margin-bottom:15px}
table{width:100%;border-collapse:collapse;font-size:12px}
th{text-align:left;padding:6px 8px;background:#21262d;color:#8b949e;position:sticky;top:0}
td{padding:5px 8px;border-bottom:1px solid #21262d}
tr:hover{background:#161b22}
.win{color:#3fb950}
.lose{color:#f85149}
</style>
</head>
<body>
<h1>GD Daily Smart Money <span style="font-size:12px;color:#8b949e">hold=10 sl=1% reinvest</span></h1>
<div class="stats" id="stats"></div>
<div class="controls">
<input type="date" id="dateFrom">
<input type="date" id="dateTo">
<button onclick="loadData()">⟳ Update</button>
<span id="status" style="color:#8b949e;font-size:12px"></span>
</div>
<canvas id="chart"></canvas>
<h2>Сделки</h2>
<div style="max-height:400px;overflow-y:auto">
<table id="tradesTable"><thead><tr>
<th>#</th><th>Entry</th><th>Exit</th><th>Entry ₽</th><th>Exit ₽</th><th>P&L</th><th>P&L%</th><th>Contr</th><th>Stop</th><th>dyb</th><th>dfiz</th>
</tr></thead><tbody></tbody></table>
</div>
<script>
let _allData = null;

function fmt(d){return d.slice(0,10)}

function loadData(){
  const f=document.getElementById('dateFrom').value;
  const t=document.getElementById('dateTo').value;
  document.getElementById('status').textContent='Loading...';
  
  fetch('/data?'+new URLSearchParams({from:f||'',to:t||''}))
    .then(r=>r.json()).then(j=>{
      if(j.error){document.getElementById('status').textContent='Error: '+j.error;return;}
      _allData = j;
      const d=j;
      const sigs=d.signals;
      const dates=d.dates;
      const close=d.close;
      
      // Filter by date
      let si=0, ei=dates.length-1;
      if(f) si=dates.findIndex(x=>x>=f);
      if(t) ei=dates.findIndex(x=>x>t);
      if(ei<0) ei=dates.length-1;
      if(si<0) si=0;
      
      document.getElementById('status').textContent=dates.length+' days, '+sigs.length+' trades';
      
      // Stats
      const wins=sigs.filter(s=>s.pnl>0);
      const totalPnl=sigs.reduce((s,x)=>s+x.pnl,0);
      const maxPnl=Math.max(...sigs.map(s=>Math.abs(s.pnl)));
      document.getElementById('stats').innerHTML=
        '<div class=stat><div class=lbl>Trades</div><div class=val style=color:#58a6ff>'+sigs.length+'</div></div>'+
        '<div class=stat><div class=lbl>Win Rate</div><div class=val style=color:#3fb950>'+(sigs.length?(wins.length/sigs.length*100).toFixed(0):'0')+'%</div></div>'+
        '<div class=stat><div class=lbl>Total P&L</div><div class=val style=color:'+(totalPnl>0?'#3fb950':'#f85149')+'>'+(totalPnl>0?'+':'')+totalPnl.toLocaleString()+'</div></div>'+
        '<div class=stat><div class=lbl>Last Price</div><div class=val style=color:#c9d1d9>'+close[close.length-1]+'</div></div>';
      
      // Filter sigs by date range
      const fSigs=sigs.filter(s=>s.entry_time>=dates[si]&&s.entry_time<=dates[ei]);
      
      // Table
      let html='';
      fSigs.forEach((s,i)=>{
        const cls=s.pnl>0?'win':'lose';
        html+='<tr class='+cls+'><td>'+(i+1)+'</td><td>'+s.entry_time+'</td><td>'+s.exit_time+
          '</td><td>'+s.entry_price+'</td><td>'+s.exit_price+'</td><td>'+(s.pnl>0?'+':'')+s.pnl.toLocaleString()+
          '</td><td>'+(s.pnl_pct>0?'+':'')+s.pnl_pct+'%</td><td>'+s.contracts+'</td><td>'+(s.stop_hit?'Y':'N')+
          '</td><td>'+s.dyb.toLocaleString()+'</td><td>'+s.dfiz.toFixed(2)+'</td></tr>';
      });
      document.querySelector('#tradesTable tbody').innerHTML=html;
      
      // Draw chart
      drawChart(dates, close, sigs, si, ei);
    }).catch(e=>{document.getElementById('status').textContent='Error: '+e.message;});
}

function drawChart(dates, close, sigs, si, ei){
  const c=document.getElementById('chart');
  const rect=c.parentElement.getBoundingClientRect();
  c.width=Math.max(rect.width-24,400)*2;
  c.height=700;
  const ctx=c.getContext('2d');
  const W=c.width, H=c.height, pad={t:20,b:40,l:55,r:20};
  const cw=W-pad.l-pad.r, ch=H-pad.t-pad.b;
  
  ctx.clearRect(0,0,W,H);
  ctx.fillStyle='#161b22';ctx.fillRect(0,0,W,H);
  
  const slice=dates.slice(si,ei+1);
  const prices=close.slice(si,ei+1);
  if(prices.length<2){ctx.fillStyle='#c9d1d9';ctx.font='14px sans-serif';ctx.textAlign='center';ctx.fillText('No data',W/2,H/2);return;}
  
  const n=prices.length;
  const yMin=Math.min(...prices)*0.995, yMax=Math.max(...prices)*1.005;
  const yR=yMax-yMin||1;
  
  function x(i){return pad.l+(i/n)*cw;}
  function y(v){return pad.t+ch-(v-yMin)/yR*ch;}
  
  // Grid
  ctx.strokeStyle='#21262d';ctx.lineWidth=1;
  for(let p=0;p<=4;p++){ctx.beginPath();ctx.moveTo(pad.l,pad.t+p/4*ch);ctx.lineTo(W-pad.r,pad.t+p/4*ch);ctx.stroke();}
  
  // Y labels
  ctx.fillStyle='#8b949e';ctx.font='10px sans-serif';ctx.textAlign='right';
  for(let p=0;p<=4;p++){ctx.fillText((yMin+p/4*yR).toFixed(0),pad.l-4,pad.t+p/4*ch+3);}
  
  // Price line
  ctx.strokeStyle='#ffffff';ctx.lineWidth=1.5;
  ctx.beginPath();
  for(let i=0;i<n;i++){ctx.lineTo(x(i),y(prices[i]));}
  ctx.stroke();
  
  // Trade markers
  sigs.forEach(s=>{
    const ei=s.entry_time;
    const xi=s.exit_time;
    const eIdx=dates.indexOf(ei);
    const xIdx=dates.indexOf(xi);
    if(eIdx<si||eIdx>ei)return;
    
    const ex=x(eIdx-si), xx=x(xIdx-si);
    const ep=s.entry_price, xp=s.exit_price;
    
    // Entry green line
    ctx.strokeStyle='rgba(63,185,80,0.6)';ctx.lineWidth=2;ctx.setLineDash([5,4]);
    ctx.beginPath();ctx.moveTo(ex,pad.t);ctx.lineTo(ex,pad.t+ch);ctx.stroke();
    ctx.setLineDash([]);
    
    // Exit red line
    ctx.strokeStyle='rgba(248,81,73,0.6)';ctx.lineWidth=2;ctx.setLineDash([5,4]);
    ctx.beginPath();ctx.moveTo(xx,pad.t);ctx.lineTo(xx,pad.t+ch);ctx.stroke();
    ctx.setLineDash([]);
    
    // Arrow
    const color=s.pnl>0?'#3fb950':'#f85149';
    ctx.strokeStyle=color;ctx.lineWidth=3;
    ctx.beginPath();ctx.moveTo(ex,y(ep));ctx.lineTo(xx,y(xp));ctx.stroke();
    
    // Entry dot
    ctx.fillStyle='#3fb950';
    ctx.beginPath();ctx.arc(ex,y(ep),5,0,Math.PI*2);ctx.fill();
    
    // PnL label
    ctx.fillStyle=color;ctx.font='bold 10px sans-serif';ctx.textAlign='left';
    ctx.fillText((s.pnl>0?'+':'')+s.pnl.toLocaleString(),xx+6,y(xp)-3);
  });
  
  // Date labels
  ctx.fillStyle='#8b949e';ctx.textAlign='center';ctx.font='10px sans-serif';
  const step=Math.max(1,Math.floor(n/8));
  for(let i=0;i<n;i+=step){ctx.fillText(dates[si+i].slice(5),x(i),H-pad.b+14);}
}

// Init
(function(){
  const d=new Date();
  const e=new Date(d.getTime()-d.getTimezoneOffset()*60000).toISOString().slice(0,10);
  const s='2025-01-01';
  document.getElementById('dateFrom').value=s;
  document.getElementById('dateTo').value='2026-05-01';
  loadData();
})();
</script>
</body>
</html>"""

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/' or self.path.startswith('/?'):
            self.send_response(200)
            self.send_header('Content-Type', 'text/html;charset=utf-8')
            self.end_headers()
            self.wfile.write(HTML.encode())
        elif self.path.startswith('/data'):
            from urllib.parse import urlparse, parse_qs
            qs = parse_qs(urlparse(self.path).query)
            resp = json.dumps(get_data())
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(resp.encode())
        else:
            self.send_response(404); self.end_headers()
    def log_message(self, *a): pass

def main():
    class ReuseTCPServer(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True
    
    server = ReuseTCPServer(('0.0.0.0', PORT), Handler)
    print(f"GD Smart Money Dashboard: http://localhost:{PORT}")
    server.serve_forever()

if __name__ == '__main__':
    main()
