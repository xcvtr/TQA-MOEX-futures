#!/usr/bin/env python3
"""Phase1 Dashboard — сделки VB/SR/Eu с per-ticker паттернами.

Добавляет дашборд на порту 5060 с тикерами VB, SR, Eu.
Каждому тикеру — свои паттерны и направления (из Фазы 1).
"""
import sys, os, json, http.server, socketserver
from datetime import datetime, timedelta
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/..')
os.chdir(os.path.dirname(os.path.abspath(__file__)) + '/..')

import numpy as np
import clickhouse_connect

CH_HOST = '127.0.0.1'
CH_PORT = 8123
CH_DB = 'moex'

PORT = 8085

TICKERS = ['VB', 'SR', 'Eu']

# Per-ticker стратегии из Фазы 1
# Каждый тикер может иметь несколько стратегий с разными паттернами/направлениями
TICKER_STRATEGIES = {
    'VB': [
        # vol_up_oi_up_yb_up — L работает, S нет → берём L
        {'name': 'vol_up_oi_up_yb_up_L', 'pattern': 'vol_up_oi_up_yb_up', 'direction': 'L',
         'hold': 13, 'atr_mult': 3, 'capital_share': 0.5},
        # smart_money — S работает на VB (из данных Фазы 1)
        {'name': 'smart_money_S', 'pattern': 'smart_money', 'direction': 'S',
         'hold': 13, 'atr_mult': 3, 'capital_share': 0.5},
    ],
    'SR': [
        # vol_up_oi_up_yb_up S — сильный паттерн +188%
        {'name': 'vol_up_oi_up_yb_up_S', 'pattern': 'vol_up_oi_up_yb_up', 'direction': 'S',
         'hold': 21, 'atr_mult': 2, 'capital_share': 0.5},
        # smart_money S — тоже работает
        {'name': 'smart_money_S', 'pattern': 'smart_money', 'direction': 'S',
         'hold': 8, 'atr_mult': 2, 'capital_share': 0.5},
        # vol_up_oi_up_yb_up L — убыточная, но добавим для контраста (малый вес)
        {'name': 'vol_up_oi_up_yb_up_L', 'pattern': 'vol_up_oi_up_yb_up', 'direction': 'L',
         'hold': 5, 'atr_mult': 2, 'capital_share': 0.2},
    ],
    'Eu': [
        # vol_up_oi_down L — сильный паттерн +66%
        {'name': 'vol_up_oi_down_L', 'pattern': 'vol_up_oi_down', 'direction': 'L',
         'hold': 13, 'atr_mult': 3, 'capital_share': 0.5},
        # smart_money S — работает
        {'name': 'smart_money_S', 'pattern': 'smart_money', 'direction': 'S',
         'hold': 21, 'atr_mult': 3, 'capital_share': 0.5},
    ],
}

CS_MAP = {'VB': 100, 'SR': 1, 'Eu': 1}
# GO для примерного расчёта (проверим из последних цен)
GO_MAP = {'VB': 7506, 'SR': 72193, 'Eu': 84166}

PATTERNS = {
    'vol_up_oi_up_yb_up': lambda dv,dyb,dys,dfn,dtoi: dv>0 and dtoi>0 and dyb>0,
    'smart_money': lambda dv,dyb,dys,dfn,dtoi: dv>0 and dyb>0 and dfn<0,
    'vol_up_oi_down': lambda dv,dyb,dys,dfn,dtoi: dv>0 and dtoi<0,
    'vol_up_yb_down_fiz_up': lambda dv,dyb,dys,dfn,dtoi: dv>0 and dyb<0 and dfn>0,
    'fiz_extreme_vol_up': lambda dv,dyb,dys,dfn,dtoi: dv>0 and abs(dfn)>5,
}

ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)
_CACHE = {}

def load_and_backtest(ticker):
    if ticker in _CACHE:
        return _CACHE[ticker]
    
    strategies = TICKER_STRATEGIES.get(ticker)
    if not strategies:
        return None
    
    # Загружаем данные один раз для тикера
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
    opn = a[:,1].astype(float); high = a[:,2].astype(float); low = a[:,3].astype(float)
    close = a[:,4].astype(float); vol = a[:,5].astype(float)
    yb = a[:,6].astype(float); ys = a[:,7].astype(float)
    fb = a[:,8].astype(float); fs = a[:,9].astype(float); toi = a[:,10].astype(float)
    toi = np.where(toi <= 0, 1, toi)
    N = len(dates)
    
    # ATR
    tr = np.zeros(N)
    tr[1:] = np.maximum(high[1:] - low[1:], np.maximum(abs(high[1:] - close[:-1]), abs(low[1:] - close[:-1])))
    atr = np.full(N, np.nan)
    if N >= 15:
        atr_s = np.convolve(tr, np.ones(14)/14, mode='valid')
        for i in range(14, N): atr[i] = atr_s[i-14]
    
    v_m = np.mean(vol)+1; yb_m = np.mean(yb)+1; ys_m = np.mean(ys)+1; toi_m = np.mean(toi)+1
    dv = np.diff(vol)/v_m; dyb = np.diff(yb)/yb_m; dys = np.diff(ys)/ys_m
    dtoi = np.diff(toi)/toi_m
    fiz_net = (fb - fs) / toi * 100; dfn = np.diff(fiz_net)
    
    cs = CS_MAP.get(ticker, 1)
    
    # Прогоняем все стратегии тикера
    all_trades = []
    eq = 200_000.0  # общий капитал
    peak = eq
    mdd = 0.0
    
    for strat in strategies:
        pfunc = PATTERNS[strat['pattern']]
        direction = strat['direction']
        hold = strat['hold']
        atr_mult = strat['atr_mult']
        share = strat['capital_share']
        
        cap_share = 200_000 * share
        
        for i in range(max(50, 15), N - max(hold, 2)):
            if i >= len(dv): break
            ep = float(opn[i+1])
            if not pfunc(dv[i], dyb[i], dys[i], dfn[i], dtoi[i]): continue
            if vol[i] < np.mean(vol[:i]) * 1.2: continue
            
            xi = min(i+1+hold, N-1)
            if xi >= N: continue
            
            go = ep * cs
            if go <= 0: continue
            
            # Sizing
            risk_amount = cap_share * 0.02
            base_nc = max(1, int(risk_amount / go * 200))  # ~0.5% stop
            nc = min(base_nc, 10)
            max_by_go = int(eq * 5 / go) if go > 0 else 99
            nc = min(nc, max_by_go)
            if nc < 1: continue
            
            # Chandelier exit
            if direction == 'L':
                sp = ep * (1 - min(max(atr[i] / ep * atr_mult, 0.005), 0.05)) if not np.isnan(atr[i]) else ep * 0.95
                running_high = ep
                exit_bar = xi
                xp = float(close[xi])
                stop_hit = False
                for j in range(i+1, xi+1):
                    bh = float(high[j])
                    if bh > running_high:
                        running_high = bh
                        if not np.isnan(atr[j]):
                            sp = max(sp, running_high * (1 - min(max(atr[j] / running_high * atr_mult, 0.005), 0.05)))
                    if float(low[j]) <= sp:
                        xp = sp
                        exit_bar = j
                        stop_hit = True
                        break
                pnl = nc * cs * (xp - ep) - nc * 4
            else:  # S
                sp = ep * (1 + min(max(atr[i] / ep * atr_mult, 0.005), 0.05)) if not np.isnan(atr[i]) else ep * 1.05
                running_low = ep
                exit_bar = xi
                xp = float(close[xi])
                stop_hit = False
                for j in range(i+1, xi+1):
                    bl = float(low[j])
                    if bl < running_low:
                        running_low = bl
                        if not np.isnan(atr[j]):
                            sp = min(sp, running_low * (1 + min(max(atr[j] / running_low * atr_mult, 0.005), 0.05)))
                    if float(high[j]) >= sp:
                        xp = sp
                        exit_bar = j
                        stop_hit = True
                        break
                pnl = nc * cs * (ep - xp) - nc * 4
            
            eq += pnl
            if eq > peak:
                peak = eq
            dd = (peak - eq) / peak * 100 if peak > 0 else 0
            mdd = max(mdd, dd)
            
            all_trades.append({
                'entry': dates[i+1], 'exit': dates[exit_bar],
                'ep': round(ep, 2), 'xp': round(xp, 2), 'nc': nc,
                'npnl': round(pnl, 0), 'stop': stop_hit,
                'bars': exit_bar - (i+1) + 1,
                'strat': strat['name'], 'direction': direction,
            })
        
        # Добавляем equity curve для этой стратегии
    
    if not all_trades:
        return None
    
    ret = (eq - 200_000) / 200_000 * 100
    wins = sum(1 for t in all_trades if t['npnl'] > 0)
    wr = wins / len(all_trades) * 100 if all_trades else 0
    gp = sum(t['npnl'] for t in all_trades if t['npnl'] > 0)
    gl = sum(t['npnl'] for t in all_trades if t['npnl'] < 0)
    pf = abs(gp / (gl + 0.001))
    
    result = dict(
        capital=200_000, ret=round(ret, 2), mdd=round(mdd, 2),
        wr=round(wr, 1), pf=round(pf, 2), trades=all_trades, ticker=ticker,
        calmar=round(ret / mdd, 2) if mdd > 0 else 0,
        dates=dates, close=[round(c, 2) for c in close],
        strategies=[s['name'] for s in strategies],
    )
    _CACHE[ticker] = result
    return result


HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Phase1 — VB/SR/Eu Dashboard</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0d1117;color:#c9d1d9;font-family:system-ui,sans-serif;padding:20px}
.controls{display:flex;gap:10px;margin-bottom:20px;align-items:center;flex-wrap:wrap}
.controls select{padding:8px 16px;background:#21262d;border:1px solid #30363d;color:#c9d1d9;border-radius:6px;font-size:14px}
.controls .stat{background:#161b22;padding:6px 14px;border-radius:6px;font-size:13px}
.controls .stat .label{color:#8b949e;font-size:11px}
.controls .stat .value{font-weight:600}
canvas{width:100%;height:400px;background:#161b22;border-radius:8px;margin-bottom:20px}
.strat-tag{display:inline-block;padding:2px 8px;border-radius:4px;font-size:10px;margin:0 4px 4px 0}
.strat-L{background:#0d4420;color:#3fb950}
.strat-S{background:#440d0d;color:#f85149}
.trade-table{width:100%;border-collapse:collapse;font-size:11px}
.trade-table th{background:#21262d;padding:6px 10px;text-align:left;color:#8b949e;font-weight:500}
.trade-table td{padding:5px 10px;border-bottom:1px solid #21262d}
.trade-table .win{color:#3fb950}
.trade-table .lose{color:#f85149}
.info{color:#8b949e;font-size:13px;margin-bottom:10px}
.per-ticker{margin-bottom:30px;background:#161b22;border-radius:8px;padding:16px}
.per-ticker h3{color:#58a6ff;margin-bottom:8px}
</style></head><body>
<h2 style="margin-bottom:16px">Phase 1 — Multi-Ticker Dashboard</h2>
<div class=controls>
  <select id=tickerSelect onchange=loadTicker()>
    <option value=VB>VB — VTB Bank</option>
    <option value=SR>SR — USDRUB (Si)</option>
    <option value=Eu>Eu — Euro</option>
  </select>
  <div class=stat><div class=label>Return</div><div class=value id=statRet style=color:#3fb950>-</div></div>
  <div class=stat><div class=label>Max DD</div><div class=value id=statDD style=color:#f85149>-</div></div>
  <div class=stat><div class=label>Calmar</div><div class=value id=statCalmar>-</div></div>
  <div class=stat><div class=label>WR</div><div class=value id=statWR>-</div></div>
  <div class=stat><div class=label>Trades</div><div class=value id=statTrades>-</div></div>
  <div class=stat><div class=label>PF</div><div class=value id=statPF>-</div></div>
</div>
<div id=stratList style="margin-bottom:12px"></div>
<canvas id=chart></canvas>
<div class=info><span id=tradeCount></span> <span id=tradeFilter><a href=# onclick='toggleFilter()' id=filterLink style=color:#58a6ff>Show all</a></span></div>
<table class=trade-table><thead><tr>
  <th>#<th>Entry<th>Exit<th>B<th>Strat<th>Dir<th>Entry₽<th>Exit₽<th>C<th>PnL<th>Stop
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
  renderStrats()
}
function updateStats(){
  document.getElementById('statRet').textContent=(data.ret>0?'+':'')+data.ret.toFixed(1)+'%'
  document.getElementById('statDD').textContent=data.mdd.toFixed(1)+'%'
  document.getElementById('statCalmar').textContent=data.calmar.toFixed(1)
  document.getElementById('statWR').textContent=data.wr.toFixed(0)+'%'
  document.getElementById('statTrades').textContent=data.trades.length
  document.getElementById('statPF').textContent=data.pf.toFixed(2)
  document.getElementById('tradeCount').textContent=data.trades.length+' trades'
}
function renderStrats(){
  const el=document.getElementById('stratList')
  el.innerHTML=data.strategies.map(s=>{
    const dir=s.endsWith('_L')?'L':'S'
    return '<span class="strat-tag strat-'+dir+'">'+s+'</span>'
  }).join('')
}
function renderTrades(){
  const tb=document.getElementById('tradeBody')
  tb.innerHTML=''
  const list=showAll?data.trades:data.trades.filter(t=>t.npnl>0)
  list.slice().reverse().forEach((t,i)=>{
    const cls=t.npnl>0?'win':'lose'
    const arr=t.npnl>0?'▲':'▼'
    const dir=t.direction||(t.strat.endsWith('_L')?'L':'S')
    tb.innerHTML+='<tr class='+cls+'>'+
      '<td>'+(i+1)+
      '<td>'+t.entry.slice(5)+
      '<td>'+t.exit.slice(5)+
      '<td>'+t.bars+
      '<td>'+t.strat.replace('smart_money','SM').replace('vol_up_oi_up_yb_up','VOIU').replace('vol_up_oi_down','VOID')+
      '<td>'+dir+
      '<td>'+t.ep.toFixed(0)+
      '<td>'+t.xp.toFixed(0)+
      '<td>'+t.nc+
      '<td>'+arr+' '+(t.npnl>0?'+':'')+t.npnl.toFixed(0)+
      '<td>'+(t.stop?'SL':'H')+
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
  const range=maxP-minP||1
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
    ctx.strokeStyle=color; ctx.lineWidth=1.5
    ctx.beginPath()
    ctx.moveTo(x(ei),y(t.ep))
    ctx.lineTo(x(xi),y(t.xp))
    ctx.stroke()
    ctx.fillStyle=color
    ctx.beginPath()
    ctx.arc(x(xi),y(t.xp),3,0,2*Math.PI)
    ctx.fill()
    const label=(t.npnl>0?'+':'')+t.npnl.toFixed(0)
    ctx.fillStyle=color; ctx.font='9px monospace'
    ctx.fillText(label,x(xi)+5,y(t.xp)+2)
  })
}
loadTicker()
</script></body></html>"""

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith('/data'):
            from urllib.parse import urlparse, parse_qs
            qs = parse_qs(urlparse(self.path).query)
            ticker = qs.get('ticker', ['VB'])[0]
            result = load_and_backtest(ticker)
            if result:
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps(result, default=str).encode())
            else:
                self.send_response(404); self.end_headers()
                self.wfile.write(b'{"error":"no data"}')
        else:
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            self.wfile.write(HTML.encode())

class ThreadedServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

if __name__ == '__main__':
    print(f'Phase1 Dashboard: http://localhost:{PORT}')
    print(f'  Tickers: {", ".join(TICKERS)}')
    print(f'  Kill with Ctrl+C')
    server = ThreadedServer(('0.0.0.0', PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
