#!/usr/bin/env python3
"""
MOEX OI Dashboard — Визуальный анализ толпы.

HTML-дашборд с canvas: 4 линии OI (fiz_long, fiz_short, yur_long, yur_short)
+ цена + crowd_index.

Запуск: python3 moex_oi_dashboard.py
Открыть: http://localhost:5058
"""
import sys, os, json, math, http.server, socket
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import clickhouse_connect
import numpy as np
import pandas as pd
from config import CH_HOST, CH_PORT, CH_DB

PORT = 5058

# ── Tickers ──
TICKERS = ['BR', 'AF', 'SR', 'VB', 'AL', 'LK', 'NM', 'PD', 'IMOEXF', 'Eu', 'Si', 'CR']
TF_OPTIONS = {'5m': 12, '15m': 4, 'H1': 1}  # bars per hour

# ── Helpers ──
_ch = None
def get_ch():
    global _ch
    if _ch is None:
        _ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)
    return _ch


def resample_oi(ticker, days=7, tf='5m'):
    """Load OI + price, resample to TF."""
    ch = get_ch()
    
    # Определяем последнюю дату с данными
    max_row = ch.query(
        "SELECT max(time) FROM moex.prices_5m_oi WHERE symbol = {t:String}",
        parameters={'t': ticker}
    ).result_rows
    max_date = max_row[0][0] if max_row and max_row[0][0] else datetime.now()
    
    since = (max_date - timedelta(days=days)).strftime('%Y-%m-%d')
    print(f"  {ticker} {tf} {days}d: max={str(max_date)[:10]} since={since}", file=sys.stderr)
    
    rows = ch.query("""
        SELECT p.time, p.open, p.high, p.low, p.close, p.volume,
               o.fiz_buy, o.fiz_sell, o.yur_buy, o.yur_sell, o.total_oi
        FROM moex.prices_5m_oi AS o
        INNER JOIN moex.prices_5m AS p ON p.symbol = o.symbol AND p.time = o.time
        WHERE o.symbol = {t:String} AND p.time >= {s:String}
        ORDER BY p.time
    """, parameters={'t': ticker, 's': since}).result_rows
    
    if not rows:
        return None
    
    df = pd.DataFrame(rows, columns=[
        'time','open','high','low','close','volume',
        'fiz_buy','fiz_sell','yur_buy','yur_sell','total_oi'
    ])
    
    if tf != '5m':
        rule_map = {'15m': '15min', 'H1': '1h'}
        rule = rule_map.get(tf, '5min')
        df['time'] = pd.to_datetime(df['time'])
        df = df.set_index('time').resample(rule).agg({
            'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last',
            'volume': 'sum', 'fiz_buy': 'last', 'fiz_sell': 'last',
            'yur_buy': 'last', 'yur_sell': 'last', 'total_oi': 'last'
        }).dropna().reset_index()
    
    return df


def compute_indicators(df):
    """Compute crowd_index + z-score fiz_net."""
    arr = df.values
    times = [str(t)[:19] for t in df['time']]
    closes = df['close'].values.astype(float)
    
    fiz_buy = df['fiz_buy'].values.astype(float)
    fiz_sell = df['fiz_sell'].values.astype(float)
    yur_buy = df['yur_buy'].values.astype(float)
    yur_sell = df['yur_sell'].values.astype(float)
    total_oi = df['total_oi'].values.astype(float)
    
    total_oi = np.where(total_oi <= 0, 1, total_oi)
    
    # Доли
    fiz_long_pct = fiz_buy / total_oi * 100
    fiz_short_pct = fiz_sell / total_oi * 100
    yur_long_pct = yur_buy / total_oi * 100
    yur_short_pct = yur_sell / total_oi * 100
    
    # Fiz позиция (net long)
    fiz_net = (fiz_buy - fiz_sell) / total_oi * 100
    yur_net = (yur_buy - yur_sell) / total_oi * 100
    
    # Crowd index: отношение fiz объёма к total
    # > 50% = толпа доминирует, < 50% = киты доминируют
    crowd_share = (fiz_buy + fiz_sell) / total_oi * 100
    
    # Fiz long premium: насколько fiz_buy > fiz_sell
    fiz_long_premium = (fiz_buy - fiz_sell) / (fiz_buy + fiz_sell + 1) * 100
    
    # z-score fiz_net за W=40
    W = min(40, len(fiz_net) - 1)
    s = pd.Series(fiz_net)
    z_fiz = ((s - s.rolling(W).mean()) / s.rolling(W).std()).fillna(0).values
    
    return {
        'times': times,
        'close': [round(c, 2) for c in closes],
        'fiz_long': [round(x, 1) for x in fiz_long_pct],
        'fiz_short': [round(x, 1) for x in fiz_short_pct],
        'yur_long': [round(x, 1) for x in yur_long_pct],
        'yur_short': [round(x, 1) for x in yur_short_pct],
        'fiz_net': [round(x, 2) for x in fiz_net],
        'yur_net': [round(x, 2) for x in yur_net],
        'crowd_share': [round(x, 1) for x in crowd_share],
        'fiz_long_premium': [round(x, 1) for x in fiz_long_premium],
        'z_fiz': [round(x, 3) for x in z_fiz],
    }


# ── HTML ──
HTML = """<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>MOEX OI Dashboard</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font:14px/1.4 sans-serif;background:#0d1117;color:#c9d1d9;padding:20px}
h1{font-size:20px;margin-bottom:10px;color:#58a6ff}
.controls{margin-bottom:15px;display:flex;gap:10px;flex-wrap:wrap;align-items:center}
.controls select,.controls button{background:#21262d;color:#c9d1d9;border:1px solid #30363d;padding:6px 12px;border-radius:4px;cursor:pointer}
.controls select:hover,.controls button:hover{border-color:#58a6ff}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:15px}
.card{background:#161b22;border:1px solid #30363d;border-radius:6px;padding:12px}
.card h2{font-size:14px;margin-bottom:8px;color:#8b949e}
.card canvas{width:100%;height:220px;display:block}
.card-full{grid-column:1/-1}
.card-full canvas{height:300px}
.stats{display:flex;gap:15px;flex-wrap:wrap;margin-bottom:15px}
.stat{background:#161b22;border:1px solid #30363d;border-radius:6px;padding:10px 15px;min-width:120px}
.stat .label{font-size:11px;color:#8b949e}
.stat .value{font-size:20px;font-weight:700;margin-top:2px}
</style>
</head>
<body>
<h1>MOEX OI Dashboard</h1>
<div class="controls">
<select id="ticker">TICKER_OPTIONS</select>
<select id="tf"><option value="5m">5m</option><option value="15m">15m</option><option value="H1">H1</option></select>
<select id="days"><option value="1">1d</option><option value="3">3d</option><option value="7" selected>7d</option><option value="14">14d</option><option value="30">30d</option></select>
<button onclick="loadData()">⟳ Update</button>
<span id="status" style="color:#8b949e;font-size:12px"></span>
</div>
<div class="stats" id="stats"></div>
<div class="grid">
<div class="card card-full"><h2>Price + OI (доли %)</h2><canvas id="c0"></canvas></div>
<div class="card"><h2>Fiz Long / Short %</h2><canvas id="c1"></canvas></div>
<div class="card"><h2>Yur Long / Short %</h2><canvas id="c2"></canvas></div>
<div class="card"><h2>Crowd Share (fiz/total) %</h2><canvas id="c3"></canvas></div>
<div class="card"><h2>Fiz Net Premium % + z-score</h2><canvas id="c4"></canvas></div>
</div>
<script>
const COLORS={bg:'#0d1117',text:'#c9d1d9',grid:'#21262d',
  green:'#2ea043',red:'#da3633',blue:'#58a6ff',orange:'#d29922',
  purple:'#bc8cff',cyan:'#39d2c0',yellow:'#e3b341',
  yurLong:'#00ff41',yurShort:'#ff0040',
  fizLong:'#006400',fizShort:'#8b0000'};

function draw(canvasId,data,options){
  const c=document.getElementById(canvasId);
  const rect=c.parentElement.getBoundingClientRect();
  c.width=Math.max(rect.width-24,200)*2;c.height=440;
  const ctx=c.getContext('2d');
  const W=c.width,H=c.height,pad={t:15,b:30,l:45,r:55};
  const cw=W-pad.l-pad.r,ch=H-pad.t-pad.b;
  
  ctx.clearRect(0,0,W,H);
  ctx.fillStyle=COLORS.bg;ctx.fillRect(0,0,W,H);
  
  if(!data||!data.times||data.times.length<2){ctx.fillStyle=COLORS.text;ctx.font='14px sans-serif';ctx.textAlign='center';ctx.fillText('No data',W/2,H/2);return;}
  
  const n=data.times.length;
  const series=options.series||[];
  const hasRightScale=series.some(s=>s.rightScale);
  
  // Scale — separate for left and right
  let yMin=Infinity,yMax=-Infinity;
  let rMin=Infinity,rMax=-Infinity;
  for(const s of series){
    for(const v of s.values){
      if(!isFinite(v))continue;
      if(s.rightScale){rMin=Math.min(rMin,v);rMax=Math.max(rMax,v);}
      else{yMin=Math.min(yMin,v);yMax=Math.max(yMax,v);}
    }
  }
  // Defaults if no left data
  if(!isFinite(yMin)){yMin=0;yMax=100;}
  if(!isFinite(rMin)){rMin=0;rMax=1;}
  if(yMin==yMax){yMin-=10;yMax+=10;}
  if(rMin==rMax){rMin-=1;rMax+=1;}
  const yRange=yMax-yMin||1;
  const rRange=rMax-rMin||1;
  const padY=yRange*0.05;const padR=rRange*0.05;
  yMin-=padY;yMax+=padY;
  rMin-=padR;rMax+=padR;
  
  function x(i){return pad.l+i/(n-1)*cw;}
  function y(v){return pad.t+ch-(v-yMin)/(yMax-yMin)*ch;}
  function ry(v){return pad.t+ch-(v-rMin)/(rMax-rMin)*ch;}
  
  // Grid
  ctx.strokeStyle=COLORS.grid;ctx.lineWidth=1;
  for(let p=0;p<=4;p++){ctx.beginPath();ctx.moveTo(pad.l,pad.t+p/4*ch);ctx.lineTo(W-pad.r,pad.t+p/4*ch);ctx.stroke();}
  
  // Y labels (left)
  ctx.fillStyle=COLORS.text;ctx.font='10px sans-serif';
  ctx.textAlign='right';
  for(let p=0;p<=4;p++){const v=yMin+p/4*yRange;ctx.fillText(v.toFixed(1),pad.l-4,pad.t+p/4*ch+3);}
  
  // Y labels (right)
  if(hasRightScale){
    ctx.textAlign='left';
    ctx.fillStyle=COLORS.green;
    for(let p=0;p<=4;p++){const v=rMin+p/4*rRange;ctx.fillText(v.toFixed(2),W-pad.r+6,pad.t+p/4*ch+3);}
  }
  
  // X labels (last 6)
  ctx.fillStyle=COLORS.text;ctx.textAlign='center';
  const step=Math.max(1,Math.floor(n/6));
  for(let i=0;i<n;i+=step){ctx.fillText(data.times[i].slice(5,16),x(i),H-pad.b+14);}
  
  // Series
  for(const s of series){
    ctx.strokeStyle=s.color;ctx.lineWidth=s.width||1.5;
    ctx.beginPath();let started=false;
    const scaleFn=s.rightScale?ry:y;
    for(let i=0;i<n;i++){
      const v=s.values[i];
      if(!isFinite(v))continue;
      if(!started){ctx.moveTo(x(i),scaleFn(v));started=true;}
      else ctx.lineTo(x(i),scaleFn(v));
    }
    ctx.stroke();
  }
  
  // Legend
  if(series.length>1){
    ctx.font='11px sans-serif';
    series.forEach((s,i)=>{
      const lx=pad.l+10+i*90;
      const ly=pad.t+14;
      ctx.fillStyle=s.color;ctx.fillRect(lx,ly-8,12,2);
      ctx.fillStyle=COLORS.text;ctx.textAlign='left';ctx.fillText(s.label||'',lx+16,ly);
    });
  }
}

function loadData(){
  const ticker=document.getElementById('ticker').value;
  const tf=document.getElementById('tf').value;
  const days=document.getElementById('days').value;
  const status=document.getElementById('status');
  status.textContent='Loading...';
  
  fetch('/data?'+new URLSearchParams({ticker,tf,days}))
    .then(r=>r.json()).then(j=>{
      if(j.error){status.textContent='Error: '+j.error;return;}
      const d=j.data;
      status.textContent=d.times.length+' bars, last: '+d.times[d.times.length-1];
      
      // Stats
      const lastZ=d.z_fiz[d.z_fiz.length-1];
      const lastCrowd=d.crowd_share[d.crowd_share.length-1];
      const lastFizLong=d.fiz_long[d.fiz_long.length-1];
      const lastYurLong=d.yur_long[d.yur_long.length-1];
      const lastClose=d.close[d.close.length-1];
      document.getElementById('stats').innerHTML=
        '<div class=stat><div class=label>Close</div><div class=value style=color:'+
        (d.close[d.close.length-1]>=d.close[d.close.length-2]?COLORS.green:COLORS.red)+'>'+lastClose+'</div></div>'+
        '<div class=stat><div class=label>Fiz Long %</div><div class=value style=color:'+COLORS.blue+'>'+lastFizLong.toFixed(1)+'%</div></div>'+
        '<div class=stat><div class=label>Yur Long %</div><div class=value style=color:'+COLORS.orange+'>'+lastYurLong.toFixed(1)+'%</div></div>'+
        '<div class=stat><div class=label>Crowd Share</div><div class=value style=color:'+COLORS.purple+'>'+lastCrowd.toFixed(1)+'%</div></div>'+
        '<div class=stat><div class=label>Fiz z-score</div><div class=value style=color:'+
        (lastZ>2?COLORS.red:lastZ<-2?COLORS.green:COLORS.text)+'>'+lastZ.toFixed(2)+'</div></div>';
      
      // Chart 0: Price + OI — price in own scale (right axis)
      draw('c0',d,{series:[
        {label:'Close',values:d.close,color:COLORS.green,width:1.5,rightScale:true},
        {label:'Yur Long%',values:d.yur_long,color:COLORS.yurLong,width:1},
        {label:'Yur Short%',values:d.yur_short,color:COLORS.yurShort,width:0.8},
        {label:'Fiz Long%',values:d.fiz_long,color:COLORS.fizLong,width:1},
        {label:'Fiz Short%',values:d.fiz_short,color:COLORS.fizShort,width:0.8},
      ]});
      
      // Chart 1: Fiz Long/Short
      draw('c1',d,{series:[
        {label:'Fiz Long',values:d.fiz_long,color:COLORS.fizLong},
        {label:'Fiz Short',values:d.fiz_short,color:COLORS.fizShort},
        {label:'Fiz Net',values:d.fiz_net,color:COLORS.cyan},
      ]});
      
      // Chart 2: Yur Long/Short
      draw('c2',d,{series:[
        {label:'Yur Long',values:d.yur_long,color:COLORS.yurLong},
        {label:'Yur Short',values:d.yur_short,color:COLORS.yurShort},
        {label:'Yur Net',values:d.yur_net,color:COLORS.cyan},
      ]});
      
      // Chart 3: Crowd Share
      draw('c3',d,{series:[
        {label:'Crowd Share',values:d.crowd_share,color:COLORS.purple,width:2},
        {label:'Fiz Long Premium',values:d.fiz_long_premium,color:COLORS.blue,width:1},
      ]});
      
      // Chart 4: Fiz z-score
      draw('c4',d,{series:[
        {label:'Fiz z-score',values:d.z_fiz,color:COLORS.cyan,width:1.5},
      ]});
    }).catch(e=>{status.textContent='Error: '+e.message;});
}

loadData();
setInterval(loadData,60000);
</script>
</body>
</html>"""


def make_handler(tickers):
    ticker_opts = ''.join(f'<option value="{t}">{t}</option>' for t in tickers)
    html = HTML.replace('TICKER_OPTIONS', ticker_opts)
    
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == '/' or self.path.startswith('/?'):
                self.send_response(200)
                self.send_header('Content-Type', 'text/html;charset=utf-8')
                self.end_headers()
                self.wfile.write(html.encode())
            elif self.path.startswith('/data'):
                from urllib.parse import urlparse, parse_qs
                qs = parse_qs(urlparse(self.path).query)
                ticker = qs.get('ticker', ['BR'])[0]
                tf = qs.get('tf', ['5m'])[0]
                days = int(qs.get('days', ['7'])[0])
                
                df = resample_oi(ticker, days, tf)
                if df is None or len(df) < 2:
                    resp = json.dumps({'error': 'no data'})
                else:
                    data = compute_indicators(df)
                    resp = json.dumps({'data': data})
                
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(resp.encode())
            else:
                self.send_response(404); self.end_headers()
        
        def log_message(self, *a): pass
    
    return Handler


def main():
    import socket
    host = '0.0.0.0'
    port = PORT
    
    server = http.server.HTTPServer((host, PORT), make_handler(TICKERS))
    print(f"MOEX OI Dashboard: http://localhost:{PORT}")
    print(f"Tickers: {', '.join(TICKERS)}")
    server.serve_forever()


if __name__ == '__main__':
    main()
