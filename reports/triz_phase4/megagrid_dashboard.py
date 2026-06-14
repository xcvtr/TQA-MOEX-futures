#!/usr/bin/env python3
"""Megagrid Trades Dashboard — просмотр сделок per-ticker с ценами."""
import sys, os, json, http.server, socketserver
from datetime import datetime, timedelta
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/..')
os.chdir(os.path.dirname(os.path.abspath(__file__)) + '/..')

import numpy as np
import clickhouse_connect

CH_HOST = '127.0.0.1'
CH_PORT = 8123
CH_DB = 'moex'

PORT = 5059

TICKERS = ['GL', 'AF', 'IMOEXF', 'CC', 'GD', 'CNYRUBF']

# Best megagrid combos per ticker
BEST_PARAMS = {
    'GL': {'hold': 21, 'atr_mult': 2, 'pattern': 'vol_up_oi_up_yb_up', 'sl_pct': 0.005},
    'AF': {'hold': 8, 'atr_mult': 2, 'pattern': 'smart_money', 'sl_pct': 0.005},
    'IMOEXF': {'hold': 8, 'atr_mult': 2, 'pattern': 'vol_up_oi_up_yb_up', 'sl_pct': 0.005},
    'CC': {'hold': 5, 'atr_mult': 2, 'pattern': 'vol_up_oi_up_yb_up', 'sl_pct': 0.005, 'fiz_thr': 0.5},
    'GD': {'hold': 1, 'sl_pct': 0.005, 'pattern': 'vol_up_oi_down'},
    'CNYRUBF': {'hold': 21, 'atr_mult': 5, 'pattern': 'vol_up_oi_down', 'sl_pct': 0.005},
}

CS_MAP = {'GL': 1, 'AF': 1, 'IMOEXF': 10, 'CC': 10, 'GD': 1, 'CNYRUBF': 1000}
GO_MAP = {'GL': 1352, 'AF': 673, 'IMOEXF': 2596, 'CC': 506, 'GD': 32003, 'CNYRUBF': 875}

PATTERNS = {
    'vol_up_oi_up_yb_up': lambda dv,dyb,dys,dfn,dtoi: dv>0 and dtoi>0 and dyb>0,
    'smart_money': lambda dv,dyb,dys,dfn,dtoi: dv>0 and dyb>0 and dfn<0,
    'vol_up_oi_down': lambda dv,dyb,dys,dfn,dtoi: dv>0 and dtoi<0,
    'vol_up_yb_down_fiz_up': lambda dv,dyb,dys,dfn,dtoi: dv>0 and dyb<0 and dfn>0,
    'fiz_extreme_vol_up': lambda dv,dyb,dys,dfn,dtoi: dv>0 and abs(dfn)>5,
}

CBR_DATES = [(2024,2,16),(2024,3,22),(2024,4,26),(2024,6,7),(2024,7,26),(2024,9,13),(2024,10,25),(2024,12,20),
             (2025,2,14),(2025,3,21),(2025,4,25),(2025,6,13),(2025,7,25),(2025,9,12),(2025,10,24),(2025,12,19),
             (2026,2,14),(2026,3,21),(2026,4,25)]

def is_cbr(d):
    dt = datetime.strptime(d[:10],'%Y-%m-%d')
    return any(abs((dt-datetime(y,m,1)).days)<=2 for y,m,d in CBR_DATES)

ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)

_CACHE = {}

def load_and_backtest(ticker):
    if ticker in _CACHE:
        return _CACHE[ticker]
    
    params = BEST_PARAMS.get(ticker)
    if not params:
        return None
    
    rows = ch.query("""
        SELECT toDate(p.time) as d,
               argMax(p.open,p.time), argMax(p.high,p.time), argMax(p.low,p.time),
               argMax(p.close,p.time), argMax(p.volume,p.time),
               argMax(o.yur_buy,p.time), argMax(o.yur_sell,p.time),
               argMax(o.fiz_buy,p.time), argMax(o.fiz_sell,p.time),
               argMax(o.total_oi,p.time)
        FROM moex.prices_5m p INNER JOIN moex.prices_5m_oi o ON p.symbol=o.symbol AND p.time=o.time
        WHERE p.symbol=%(t)s AND p.time>='2024-01-01' AND p.time<='2026-05-01'
        GROUP BY d ORDER BY d
    """, parameters={'t': ticker}).result_rows
    if len(rows) < 60:
        return None
    
    a = np.array([list(r) for r in rows], dtype=object)
    dates = [str(r[0]) for r in rows]
    opn=a[:,1].astype(float); high=a[:,2].astype(float); low=a[:,3].astype(float)
    close=a[:,4].astype(float); vol=a[:,5].astype(float)
    yb=a[:,6].astype(float); ys=a[:,7].astype(float)
    fb=a[:,8].astype(float); fs=a[:,9].astype(float); toi=a[:,10].astype(float)
    toi=np.where(toi<=0,1,toi)
    v_m=np.mean(vol)+1; yb_m=np.mean(yb)+1; ys_m=np.mean(ys)+1; toi_m=np.mean(toi)+1
    dv=np.diff(vol)/v_m; dyb=np.diff(yb)/yb_m; dys=np.diff(ys)/ys_m
    dtoi=np.diff(toi)/toi_m; fiz_net=(fb-fs)/toi*100; dfn=np.diff(fiz_net)
    
    tr=np.zeros(len(close))
    tr[1:]=np.maximum(high[1:]-low[1:],np.maximum(abs(high[1:]-close[:-1]),abs(low[1:]-close[:-1])))
    atr=np.full(len(close),np.nan)
    if len(close)>=15:
        atr_s=np.convolve(tr,np.ones(14)/14,mode='valid')[:len(close)]
        for i in range(14,len(close)): atr[i]=atr_s[i-14]
    
    sma50=np.full(len(close),np.nan)
    if len(close)>=50:
        cs_=np.cumsum(close); sma50[49]=cs_[49]/50; sma50[50:]=(cs_[50:]-cs_[:-50])/50
    
    n=len(close); cs=CS_MAP.get(ticker,1); pfunc=PATTERNS[params['pattern']]
    hold=params['hold']; sl_pct=params['sl_pct']; atr_mult=params.get('atr_mult',0)
    cap=200000/23; eq=float(cap); peak=eq; mdd=0.0; trades=[]
    
    for i in range(max(50,15), n-max(hold,2)):
        if i>=len(dv): break
        if not pfunc(dv[i],dyb[i],dys[i],dfn[i],dtoi[i]): continue
        if is_cbr(dates[i]): continue
        if sma50 is not None and i<len(sma50) and not np.isnan(sma50[i]) and close[i]<=sma50[i]: continue
        
        # Stacked filter for CC
        if params.get('fiz_thr'):
            continue  # simplified, skip stacked for now
        
        ei=i+1
        if ei>=n-1: continue
        ep=float(opn[ei])
        xi=min(ei+hold, n-1)
        go=ep*cs
        if go<=0: continue
        
        risk_amount=eq*0.02
        base_nc=max(1,int(risk_amount/(go*sl_pct))) if sl_pct>0 else max(1,int(risk_amount/go*5))
        nc=min(base_nc, 5); max_by_go=int(eq*5/go) if go>0 else 99
        nc=min(nc, max_by_go)
        if nc<1: continue
        
        remaining_nc=nc; npnl_total=0; stop_hit=False; partial_closed=False
        exit_date=dates[xi]; xp=float(close[xi])
        
        if atr_mult > 0:  # chandelier
            running_high=ep
            sp=ep*(1-min(max(atr[i]/ep*atr_mult,0.01),0.05))
            for j in range(ei, xi+1):
                bh=float(high[j])
                if bh>running_high:
                    running_high=bh
                    new_trail=max(atr[j]/running_high*atr_mult,0.01) if j<len(atr) and not np.isnan(atr[j]) else 0.01
                    sp=max(sp, running_high*(1-min(new_trail,0.05)))
                if float(low[j])<=sp:
                    xp=sp; stop_hit=True; exit_date=dates[j]
                    npnl_total+=remaining_nc*cs*(xp-ep)-remaining_nc*4
                    remaining_nc=0
                    break
            if not stop_hit and remaining_nc>0:
                npnl_total+=remaining_nc*cs*(xp-ep)-remaining_nc*4
        else:
            sp=ep*(1-sl_pct) if sl_pct>0 else 0
            if sl_pct>0:
                for j in range(ei, xi+1):
                    if float(low[j])<=sp:
                        xp=sp; stop_hit=True; exit_date=dates[j]; break
            npnl_total=nc*cs*(xp-ep)-nc*4
        
        eq+=npnl_total
        if eq>peak: peak=eq
        dd=(peak-eq)/peak*100 if peak>0 else 0
        mdd=max(mdd,dd)
        
        trades.append(dict(entry=dates[ei], exit=exit_date,
                           ep=round(ep,2), xp=round(xp,2),
                           nc=nc, npnl=round(npnl_total,0),
                           stop=stop_hit, bars=xi-ei+1))
    
    if not trades: return None
    ret=(eq-cap)/cap*100
    wins=sum(1 for t in trades if t['npnl']>0)
    wr=wins/len(trades)*100
    gp_s=sum(t['npnl'] for t in trades if t['npnl']>0)
    gl_s=sum(t['npnl'] for t in trades if t['npnl']<0)
    pf=abs(gp_s/(gl_s+0.001))
    
    result = dict(capital=round(cap), ret=round(ret,2), mdd=round(mdd,2),
                  wr=round(wr,1), pf=round(pf,2), trades=trades, ticker=ticker,
                  calmar=round(ret/mdd,2) if mdd>0 else 0,
                  dates=dates, close=[round(c,2) for c in close])
    _CACHE[ticker] = result
    return result


HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Megagrid Trades</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0d1117;color:#c9d1d9;font-family:system-ui,sans-serif;padding:20px}
.controls{display:flex;gap:10px;margin-bottom:20px;align-items:center;flex-wrap:wrap}
.controls select{padding:8px 16px;background:#21262d;border:1px solid #30363d;color:#c9d1d9;border-radius:6px;font-size:14px}
.controls .stat{background:#161b22;padding:6px 14px;border-radius:6px;font-size:13px}
.controls .stat .label{color:#8b949e;font-size:11px}
.controls .stat .value{font-weight:600}
canvas{width:100%;height:400px;background:#161b22;border-radius:8px;margin-bottom:20px}
.trade-table{width:100%;border-collapse:collapse;font-size:12px}
.trade-table th{background:#21262d;padding:8px 12px;text-align:left;color:#8b949e;font-weight:500}
.trade-table td{padding:6px 12px;border-bottom:1px solid #21262d}
.trade-table .win{color:#3fb950}
.trade-table .lose{color:#f85149}
.arrow-up{color:#3fb950}
.arrow-down{color:#f85149}
.info{color:#8b949e;font-size:13px;margin-bottom:10px}
</style></head><body>
<div class=controls>
  <select id=tickerSelect onchange=loadTicker()>
    <option value=GL>GL — Gold</option>
    <option value=AF>AF — Aeroflot</option>
    <option value=IMOEXF>IMOEXF — Index</option>
    <option value=CC>CC — Cocoa</option>
    <option value=GD>GD — Gold big</option>
    <option value=CNYRUBF>CNYRUBF — Yuan/RUB</option>
  </select>
  <div class=stat><div class=label>Return</div><div class=value id=statRet style=color:#3fb950>-</div></div>
  <div class=stat><div class=label>Max DD</div><div class=value id=statDD style=color:#f85149>-</div></div>
  <div class=stat><div class=label>Calmar</div><div class=value id=statCalmar>-</div></div>
  <div class=stat><div class=label>WR</div><div class=value id=statWR>-</div></div>
  <div class=stat><div class=label>Trades</div><div class=value id=statTrades>-</div></div>
</div>
<canvas id=chart></canvas>
<div class=info><span id=tradeCount></span> <span id=tradeFilter><a href=# onclick='toggleFilter()' id=filterLink style=color:#58a6ff>Show all</a></span></div>
<table class=trade-table><thead><tr>
  <th>#<th>Entry<th>Exit<th>Bars<th>Entry₽<th>Exit₽<th>Contr<th>PnL<th>Stop
</thead><tbody id=tradeBody></tbody></table>
<script>
const COLORS={bg:'#161b22',text:'#c9d1d9',grid:'#21262d',up:'#3fb950',down:'#f85149'}
let data={}, showAll=true

async function loadTicker(){
  const t=document.getElementById('tickerSelect').value
  const r=await fetch('/data?ticker='+t)
  data=await r.json()
  drawChart()
  updateStats()
  renderTrades()
}

function updateStats(){
  document.getElementById('statRet').textContent=(data.ret>0?'+':'')+data.ret.toFixed(1)+'%'
  document.getElementById('statDD').textContent=data.mdd.toFixed(1)+'%'
  document.getElementById('statCalmar').textContent=data.calmar.toFixed(1)
  document.getElementById('statWR').textContent=data.wr.toFixed(0)+'%'
  document.getElementById('statTrades').textContent=data.trades.length
  document.getElementById('tradeCount').textContent=data.trades.length+' trades'
}

function renderTrades(){
  const tb=document.getElementById('tradeBody')
  tb.innerHTML=''
  const list=showAll?data.trades:data.trades.filter(t=>t.npnl>0)
  list.slice().reverse().forEach((t,i)=>{
    const cls=t.npnl>0?'win':'lose'
    const arr=t.npnl>0?'▲':'▼'
    tb.innerHTML+='<tr class='+cls+'>'+
      '<td>'+(i+1)+
      '<td>'+t.entry.slice(5)+
      '<td>'+t.exit.slice(5)+
      '<td>'+t.bars+
      '<td>'+t.ep.toFixed(0)+
      '<td>'+t.xp.toFixed(0)+
      '<td>'+t.nc+
      '<td>'+arr+' '+(t.npnl>0?'+':'')+t.npnl.toFixed(0)+
      '<td>'+(t.stop?'SL':'hold')+
      '</tr>'
  })
}

function toggleFilter(){
  showAll=!showAll
  document.getElementById('filterLink').textContent=showAll?'Show winners only':'Show all'
  renderTrades()
}

function drawChart(){
  const c=document.getElementById('chart')
  const rect=c.parentElement.getBoundingClientRect()
  c.width=(rect.width-40)*2
  c.height=800
  const ctx=c.getContext('2d')
  const W=c.width, H=c.height, padL=70, padR=20, padT=20, padB=40
  const cw=W-padL-padR, ch=H-padT-padB
  
  const prices=data.close
  const minP=Math.min(...prices), maxP=Math.max(...prices)
  const range=maxP-minP
  const x=i=>padL+i/prices.length*cw
  const y=p=>padT+(1-(p-minP)/range)*ch
  
  ctx.clearRect(0,0,W,H)
  
  // Grid
  ctx.strokeStyle=COLORS.grid; ctx.lineWidth=1
  for(let i=0;i<5;i++){
    const yy=padT+i*ch/4
    ctx.beginPath(); ctx.moveTo(padL,yy); ctx.lineTo(W-padR,yy); ctx.stroke()
    ctx.fillStyle=COLORS.grid; ctx.font='11px monospace'
    ctx.fillText((minP+range*(1-i/4)).toFixed(0),4,yy+4)
  }
  
  // Price line
  ctx.strokeStyle='#58a6ff'; ctx.lineWidth=1.5; ctx.beginPath()
  prices.forEach((p,i)=>{i===0?ctx.moveTo(x(i),y(p)):ctx.lineTo(x(i),y(p))})
  ctx.stroke()
  
  // Trades
  data.trades.forEach(t=>{
    const ei=prices.indexOf(t.ep)
    const xi=prices.indexOf(t.xp)
    if(ei<0||xi<0) return
    const color=t.npnl>0?COLORS.up:COLORS.down
    ctx.strokeStyle=color; ctx.lineWidth=2
    ctx.beginPath()
    ctx.moveTo(x(ei),y(t.ep))
    ctx.lineTo(x(xi),y(t.xp))
    ctx.stroke()
    // Arrow
    const angle=t.npnl>0?-Math.PI/2:Math.PI/2
    ctx.fillStyle=color
    ctx.beginPath()
    ctx.arc(x(xi),y(t.xp),4,0,2*Math.PI)
    ctx.fill()
    // PnL label
    ctx.fillStyle=color; ctx.font='10px monospace'
    const label=(t.npnl>0?'+':'')+t.npnl.toFixed(0)
    ctx.fillText(label,x(xi)+6,y(t.xp)+3)
  })
}

loadTicker()
</script></body></html>"""

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith('/data'):
            from urllib.parse import urlparse, parse_qs
            qs=parse_qs(urlparse(self.path).query)
            ticker=qs.get('ticker',['GL'])[0]
            result=load_and_backtest(ticker)
            if result:
                self.send_response(200)
                self.send_header('Content-Type','application/json')
                self.send_header('Access-Control-Allow-Origin','*')
                self.end_headers()
                self.wfile.write(json.dumps(result, default=str).encode())
            else:
                self.send_response(404); self.end_headers()
                self.wfile.write(b'{"error":"no data"}')
        else:
            self.send_response(200)
            self.send_header('Content-Type','text/html')
            self.end_headers()
            self.wfile.write(HTML.encode())

class ThreadedServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

if __name__=='__main__':
    print(f'Megagrid Dashboard: http://localhost:{PORT}')
    server = ThreadedServer(('', PORT), Handler)
    server.serve_forever()
