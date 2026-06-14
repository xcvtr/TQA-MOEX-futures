#!/usr/bin/env python3
"""
MOEX OI Dashboard — Визуальный анализ толпы.
HTML-дашборд с canvas: 4 линии OI (fiz_long, fiz_short, yur_long, yur_short)
+ цена + crowd_index + сделки Volume×OI + localStorage persistence.
"""
import sys, os, json, math, http.server, socket, socketserver
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import clickhouse_connect
import numpy as np
import pandas as pd
from config import CH_HOST, CH_PORT, CH_DB

# Phase2 configs from scan
_PHASE2_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'reports/triz_phase4/phase2_fullscan.json')
_PHASE2 = None
def get_phase2():
    global _PHASE2
    if _PHASE2 is None and os.path.exists(_PHASE2_PATH):
        with open(_PHASE2_PATH) as f:
            _PHASE2 = json.load(f)
    return _PHASE2

PATTERNS = {
    'v': lambda dv,dyb,dys,dfn,dtoi: dv>0 and dtoi>0 and dyb>0,
    's': lambda dv,dyb,dys,dfn,dtoi: dv>0 and dyb>0 and dfn<0,
    'd': lambda dv,dyb,dys,dfn,dtoi: dv>0 and dtoi<0,
    'y': lambda dv,dyb,dys,dfn,dtoi: dv>0 and dyb<0 and dfn>0,
    'f': lambda dv,dyb,dys,dfn,dtoi: dv>0 and abs(dfn)>5,
}
PATTERN_NAMES = {'v':'vou','s':'sm','d':'vod','y':'vyf','f':'vd'}

def run_phase2_bt(ticker, pattern, direction, hold, atr_mult):
    """Run Phase2 backtest for given config, return trades list."""
    ch = get_ch_threadsafe()
    rows = ch.query(f"""SELECT time,open,high,low,close,volume,
        yur_buy,yur_sell,fiz_buy,fiz_sell,total_oi
        FROM moex.prices_5m p INNER JOIN moex.prices_5m_oi o
        ON p.symbol=o.symbol AND p.time=o.time
        WHERE p.symbol='{ticker}' AND p.time>='2024-01-01' AND p.time<='2026-05-01'
        ORDER BY p.time""").result_rows
    if not rows or len(rows) < 1000: return []
    N = len(rows)
    times = [str(r[0]) for r in rows]
    opn = np.array([float(r[1]) for r in rows])
    high = np.array([float(r[2]) for r in rows])
    low = np.array([float(r[3]) for r in rows])
    close = np.array([float(r[4]) for r in rows])
    vol = np.array([float(r[5]) for r in rows])
    yb = np.array([float(r[6]) for r in rows])
    ys = np.array([float(r[7]) for r in rows])
    fb = np.array([float(r[8]) for r in rows])
    fs = np.array([float(r[9]) for r in rows])
    toi = np.array([float(r[10]) if float(r[10]) > 0 else 1 for r in rows])
    tr = np.zeros(N); tr[1:] = np.maximum(high[1:]-low[1:], np.maximum(abs(high[1:]-close[:-1]), abs(low[1:]-close[:-1])))
    atr = np.full(N, np.nan)
    for i in range(14, N): atr[i] = np.mean(tr[i-13:i+1])
    v_m = np.mean(vol) + 1
    yb_m = np.mean(yb) + 1
    ys_m = np.mean(ys) + 1
    toi_m = np.mean(toi) + 1
    dv = np.diff(vol) / v_m
    dyb = np.diff(yb) / yb_m
    dys = np.diff(ys) / ys_m
    dtoi = np.diff(toi) / toi_m
    fiz_net = (fb - fs) / toi * 100
    dfn = np.diff(fiz_net)
    end_idx = N - max(hold, 2) - 1
    vm = np.full(N, np.nan)
    vc = np.cumsum(vol)
    for i in range(60, N): vm[i] = (vc[i] - vc[i-60]) / 60
    a = atr_mult; h = hold
    trades = []
    for i in range(64, end_idx):
        if i >= len(dv): break
        ep = float(opn[i+1])
        if not PATTERNS[pattern](dv[i], dyb[i], dys[i], dfn[i], dtoi[i]): continue
        if np.isnan(vm[i]) or vol[i] < vm[i] * 1.2: continue
        xi = min(i+1+h, N-1)
        if direction == 'L':
            sp = ep * (1 - min(max(atr[i]/ep*a, 0.005), 0.05)) if not np.isnan(atr[i]) else ep * 0.95
            r_h = ep; exit_idx = xi; xp = float(close[xi])
            for j in range(i+1, xi+1):
                bh = float(high[j])
                if bh > r_h: r_h = bh
                if not np.isnan(atr[j]): sp = max(sp, r_h * (1 - min(max(atr[j]/r_h*a, 0.005), 0.05)))
                if float(low[j]) <= sp: xp = sp; exit_idx = j; break
            pnl = xp - ep
        else:
            sp = ep * (1 + min(max(atr[i]/ep*a, 0.005), 0.05)) if not np.isnan(atr[i]) else ep * 1.05
            r_l = ep; exit_idx = xi; xp = float(close[xi])
            for j in range(i+1, xi+1):
                bl = float(low[j])
                if bl < r_l: r_l = bl
                if not np.isnan(atr[j]): sp = min(sp, r_l * (1 + min(max(atr[j]/r_l*a, 0.005), 0.05)))
                if float(high[j]) >= sp: xp = sp; exit_idx = j; break
            pnl = ep - xp
        trades.append({
            'entry_idx': i+1, 'exit_idx': exit_idx,
            'entry_time': str(times[i+1])[:19], 'exit_time': str(times[exit_idx])[:19],
            'entry_price': round(ep, 2), 'exit_price': round(xp, 2), 'pnl': round(pnl, 2)
        })
    return trades

PORT = 8086

TICKERS = ['SN', 'AU', 'AL', 'BR', 'AF', 'SR', 'VB', 'LK', 'NM', 'PD', 'IMOEXF', 'Eu', 'Si', 'CR']
TF_OPTIONS = {'5m': 12, '15m': 4, 'H1': 1}

_ch = None
def get_ch():
    global _ch
    if _ch is None:
        _ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)
    return _ch

def get_ch_threadsafe():
    # new client per thread to avoid concurrent query errors
    return clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)

def resample_oi(ticker, start_date, end_date, tf='5m'):
    ch = get_ch_threadsafe()
    
    rows = ch.query("""
        SELECT p.time, p.open, p.high, p.low, p.close, p.volume,
               o.fiz_buy, o.fiz_sell, o.yur_buy, o.yur_sell, o.total_oi
        FROM moex.prices_5m_oi AS o
        INNER JOIN moex.prices_5m AS p ON p.symbol = o.symbol AND p.time = o.time
        WHERE o.symbol = {t:String} AND p.time >= {s:String} AND p.time <= {e:String}
        ORDER BY p.time
    """, parameters={'t': ticker, 's': start_date, 'e': end_date}).result_rows
    
    if not rows:
        # Fallback: last 7 days of available OI data
        max_row = ch.query(
            "SELECT max(time) FROM moex.prices_5m_oi WHERE symbol = {t:String}",
            parameters={'t': ticker}
        ).result_rows
        if max_row and max_row[0][0]:
            from datetime import timedelta
            end_date = max_row[0][0]
            start_date = (end_date - timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S')
            end_date = end_date.strftime('%Y-%m-%d %H:%M:%S')
        rows = ch.query("""
            SELECT p.time, p.open, p.high, p.low, p.close, p.volume,
                   o.fiz_buy, o.fiz_sell, o.yur_buy, o.yur_sell, o.total_oi
            FROM moex.prices_5m_oi AS o
            INNER JOIN moex.prices_5m AS p ON p.symbol = o.symbol AND p.time = o.time
            WHERE o.symbol = {t:String} AND p.time >= {s:String} AND p.time <= {e:String}
            ORDER BY p.time
        """, parameters={'t': ticker, 's': start_date, 'e': end_date}).result_rows
    
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
    arr = df.values
    times = [str(t)[:19] for t in df['time']]
    closes = df['close'].values.astype(float)
    
    fiz_buy = df['fiz_buy'].values.astype(float)
    fiz_sell = df['fiz_sell'].values.astype(float)
    yur_buy = df['yur_buy'].values.astype(float)
    yur_sell = df['yur_sell'].values.astype(float)
    total_oi = df['total_oi'].values.astype(float)
    
    total_oi = np.where(total_oi <= 0, 1, total_oi)
    
    fiz_long_pct = fiz_buy / total_oi * 100
    fiz_short_pct = fiz_sell / total_oi * 100
    yur_long_pct = yur_buy / total_oi * 100
    yur_short_pct = yur_sell / total_oi * 100
    
    fiz_net = (fiz_buy - fiz_sell) / total_oi * 100
    yur_net = (yur_buy - yur_sell) / total_oi * 100
    
    crowd_share = (fiz_buy + fiz_sell) / total_oi * 100
    fiz_long_premium = (fiz_buy - fiz_sell) / (fiz_buy + fiz_sell + 1) * 100
    
    W = min(40, len(fiz_net) - 1)
    s = pd.Series(fiz_net)
    z_fiz = ((s - s.rolling(W).mean()) / s.rolling(W).std()).fillna(0).values
    
    # Compute vol_z and yur_z for trade detection
    vol_z_arr = np.zeros(len(closes))
    yur_z_arr = np.zeros(len(closes))
    if len(closes) > 20:
        vol_s = pd.Series(df['volume'].values.astype(float))
        vol_mu = vol_s.rolling(20, min_periods=10).mean()
        vol_sd = vol_s.rolling(20, min_periods=10).std().fillna(1).replace(0, 1)
        vol_z_arr = ((vol_s - vol_mu) / vol_sd).fillna(0).values
        
        yur_net_s = pd.Series(yur_net)
        yn_mu = yur_net_s.rolling(20, min_periods=10).mean()
        yn_sd = yur_net_s.rolling(20, min_periods=10).std().fillna(1).replace(0, 1)
        yur_z_arr = ((yur_net_s - yn_mu) / yn_sd).fillna(0).values
    
    # ATR
    atr_pct_arr = np.zeros(len(closes))
    if len(closes) > 15:
        high = df['high'].values.astype(float)
        low = df['low'].values.astype(float)
        close = df['close'].values.astype(float)
        tr = np.maximum(high - low, np.maximum(
            np.abs(high - np.roll(close, 1)),
            np.abs(low - np.roll(close, 1))
        ))
        tr[0] = high[0] - low[0]
        atr_series = pd.Series(tr).ewm(span=14).mean()
        atr_pct_arr = (atr_series / close * 100).fillna(0).values
    
    # Detect trades — волновой entry: TROUGH yur_net → LONG, PEAK yur_net → SHORT
    # Держим до следующего противо-разворота или SL
    trades = []
    n = len(closes)
    lookback = 12  # 1h window для поиска локальных экстремумов
    min_change = max(2.0, float(yur_net.std()) * 0.5)  # адаптивный: 50% от std yur_net
    sl_pct = 0.02
    
    # Найти все волновые развороты
    wave_turns = []
    for i in range(lookback, n - lookback):
        left = yur_net[i-lookback:i]
        right = yur_net[i:i+lookback]
        # PEAK: yur_net[i] максимален относительно окрестности
        if yur_net[i] == max(yur_net[i-lookback:i+lookback]) and yur_net[i] > np.mean(left) + min_change:
            wave_turns.append({'idx': i, 'type': 'PEAK', 'val': float(yur_net[i]), 'dir': -1})  # → SHORT
        # TROUGH: yur_net[i] минимален
        elif yur_net[i] == min(yur_net[i-lookback:i+lookback]) and yur_net[i] < np.mean(left) - min_change:
            wave_turns.append({'idx': i, 'type': 'TROUGH', 'val': float(yur_net[i]), 'dir': 1})  # → LONG
    
    # Сортируем по idx
    wave_turns.sort(key=lambda x: x['idx'])
    
    # Отбираем: только TROUGH→LONG (т.к. PEAK→SHORT нестабилен)
    # Вход на TROUGH, выход на следующем PEAK
    for i in range(len(wave_turns) - 1):
        t1 = wave_turns[i]
        t2 = wave_turns[i+1]
        
        if t1['type'] != 'TROUGH' or t2['type'] != 'PEAK':
            continue
        
        if t2['idx'] - t1['idx'] < 2:
            continue  # слишком близко
        
        entry_idx = t1['idx'] + 1  # вход на следующем баре после разворота
        exit_idx = t2['idx']  # выход на PEAK
        
        if entry_idx >= n or exit_idx >= n:
            continue
        
        direction = 1  # только LONG
        
        entry = float(df.iloc[entry_idx]['open'])
        if entry <= 0:
            continue
        
        # SL check (2%)
        stop_level = entry * (1 - sl_pct)
        exit_px = float(df.iloc[exit_idx]['close'])
        hit_stop = False
        
        for j in range(entry_idx, exit_idx + 1):
            if float(df.iloc[j]['low']) <= stop_level:
                exit_px = stop_level
                exit_idx = j
                hit_stop = True
                break
        
        pnl = (exit_px - entry) / 0.01 - 2
        
        trades.append({
            'entry_idx': int(entry_idx),
            'exit_idx': int(exit_idx),
            'entry_time': times[entry_idx],
            'exit_time': times[exit_idx],
            'entry_price': round(entry, 2),
            'exit_price': round(exit_px, 2),
            'pnl': round(pnl, 2),
            'hit_stop': hit_stop,
            'yur_net_entry': round(float(yur_net[t1['idx']]), 2),
            'yur_net_exit': round(float(yur_net[t2['idx']]), 2),
            'bars_held': exit_idx - entry_idx,
            'direction': direction,
        })
    
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
        'trades': trades,
    }

HTML = """<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>MOEX OI Dashboard</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font:14px/1.4 sans-serif;background:#0d1117;color:#c9d1d9;padding:20px}
h1{font-size:20px;margin-bottom:10px;color:#58a6ff}
.controls{margin-bottom:15px;display:flex;gap:10px;flex-wrap:wrap;align-items:center}
.controls select,.controls button,.controls input{background:#21262d;color:#c9d1d9;border:1px solid #30363d;padding:6px 12px;border-radius:4px;cursor:pointer;font:13px/1.4 sans-serif}
.controls select:hover,.controls button:hover,.controls input:hover{border-color:#58a6ff}
.controls label{font-size:12px;color:#8b949e}
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
.trade-info{font-size:12px;color:#8b949e;margin-top:8px;max-height:120px;overflow-y:auto}
.trade-info div{padding:2px 0}
.trade-info .win{color:#3fb950}
.trade-info .lose{color:#f85149}
</style>
</head>
<body>
<h1>MOEX OI Dashboard <span style="font-size:12px;color:#8b949e">+ Volume×OI Trades</span></h1>
<div class="controls">
<select id="ticker">TICKER_OPTIONS</select>
<select id="tf"><option value="5m">5m</option><option value="15m">15m</option><option value="H1">H1</option></select>
<label>Start</label>
<input type="datetime-local" id="start-date">
<label>End</label>
<input type="datetime-local" id="end-date">
<button onclick="loadData()">⟳ Update</button>
<span id="status" style="color:#8b949e;font-size:12px"></span>
<label style="color:#8b949e;font-size:12px"><input type="checkbox" id="showTrades" checked onchange="loadData()"> Показать сделки</label>
PHASE2_CONTROLS
</div>
<div class="stats" id="stats"></div>
<div class="grid">
<div class="card card-full"><h2>Price + OI (доли %)</h2><canvas id="c0"></canvas><div class="trade-info" id="tradeInfo"></div>
<div class="card card-full" id="zoomCard" style="margin-top:8px;display:none;border:none;padding:0;background:transparent">
  <canvas id="cz" style="width:100%;height:160px;display:block"></canvas>
  <div id="zoomInfo" style="font-size:10px;color:#8b949e;margin-top:4px"></div>
</div>
</div>
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
  fizLong:'#006400',fizShort:'#8b0000',
  entryLine:'rgba(63,185,80,0.5)',exitLine:'rgba(248,81,73,0.5)'};
let _lastData = null; // for click-to-select

function formatTimeLocal(tStr){
  // convert "2026-05-12 09:05:00" to datetime-local value
  const d = new Date(tStr.slice(0,10)+'T'+tStr.slice(11,19));
  return new Date(d.getTime() - d.getTimezoneOffset()*60000).toISOString().slice(0,16);
}

document.addEventListener('click', function(e){
  const c0 = document.getElementById('c0');
  if(!c0 || !_lastData || e.target !== c0) return;
  const rect = c0.getBoundingClientRect();
  const px = e.clientX - rect.left;
  const pw = rect.width;
  if(px < 0 || px > pw) return;
  // map CSS pixel to bar index — draw() uses c.width = Math.max(parent.width-24,200)*2
  // So the visible width maps to c0.width
  const canvasW = c0.width;
  const padL = 45, padR = 55;
  const cw = canvasW - padL - padR;
  const n = _lastData.n;
  // px is in CSS pixels, canvas is rendered at canvasW logical pixels across pw CSS pixels
  const logicalX = (px / pw) * canvasW;
  const idx = Math.round((logicalX - padL) / cw * (n - 1));
  const clamped = Math.max(0, Math.min(n - 1, idx));
  const tStr = _lastData.times[clamped];
  const localVal = formatTimeLocal(tStr);
  if(e.shiftKey){
    document.getElementById('end-date').value = localVal;
  } else {
    // Set start = time - 4h, end = time + 4h
    const dt = new Date(tStr.slice(0,10)+'T'+tStr.slice(11,19));
    const startDt = new Date(dt.getTime() - 4*3600000);
    const endDt = new Date(dt.getTime() + 4*3600000);
    const fmt = d => new Date(d.getTime() - d.getTimezoneOffset()*60000).toISOString().slice(0,16);
    document.getElementById('start-date').value = fmt(startDt);
    document.getElementById('end-date').value = fmt(endDt);
  }
});

function draw(canvasId,data,options){
  const c=document.getElementById(canvasId);
  const rect=c.parentElement.getBoundingClientRect();
  c.width=Math.max(rect.width-24,200)*2;c.height=440;
  const ctx=c.getContext('2d');
  const W=c.width,H=c.height,pad={t:15,b:35,l:45,r:55};
  const cw=W-pad.l-pad.r,ch=H-pad.t-pad.b;
  
  ctx.clearRect(0,0,W,H);
  ctx.fillStyle=COLORS.bg;ctx.fillRect(0,0,W,H);
  
  if(!data||!data.times||data.times.length<2){ctx.fillStyle=COLORS.text;ctx.font='14px sans-serif';ctx.textAlign='center';ctx.fillText('No data',W/2,H/2);return;}
  
  const n=data.times.length;
  const series=options.series||[];
  const hasRightScale=series.some(s=>s.rightScale);
  const trades=options.trades||[];
  const p2trades=options.p2trades||[];
  const showTrades=options.showTrades!==false;
  
  let yMin=Infinity,yMax=-Infinity;
  let rMin=Infinity,rMax=-Infinity;
  for(const s of series){
    for(const v of s.values){
      if(!isFinite(v))continue;
      if(s.rightScale){rMin=Math.min(rMin,v);rMax=Math.max(rMax,v);}
      else{yMin=Math.min(yMin,v);yMax=Math.max(yMax,v);}
    }
  }
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
  
  // Y labels
  ctx.fillStyle=COLORS.text;ctx.font='10px sans-serif';
  ctx.textAlign='right';
  for(let p=0;p<=4;p++){const v=yMin+p/4*yRange;ctx.fillText(v.toFixed(1),pad.l-4,pad.t+p/4*ch+3);}
  if(hasRightScale){
    ctx.textAlign='left';
    ctx.fillStyle=COLORS.green;
    for(let p=0;p<=4;p++){const v=rMin+p/4*rRange;ctx.fillText(v.toFixed(2),W-pad.r+6,pad.t+p/4*ch+3);}
  }
  
  // X labels
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
  
  // TRADE LINES (on Chart 0 only — Price + OI)
  if(showTrades && trades.length>0 && canvasId==='c0'){
    for(const t of trades){
      const ei=t.entry_idx;
      const xi=t.exit_idx;
      if(ei>=n||xi>=n)continue;
      
    const ex=x(ei), ey=ry(t.entry_price);
    const xx=x(xi), xy=ry(t.exit_price);
    // Entry/exit vertical lines — removed per user
      
      // Trade arrow line (entry→exit on price scale)
      const color=t.pnl>0?COLORS.green:COLORS.red;
      ctx.strokeStyle=color;
      ctx.lineWidth=3;
      ctx.beginPath();ctx.moveTo(ex,ey);ctx.lineTo(xx,xy);ctx.stroke();
      
      // Entry circle
      ctx.fillStyle=COLORS.green;
      ctx.beginPath();ctx.arc(ex,ey,5,0,Math.PI*2);ctx.fill();
      
      // Exit circle
      ctx.fillStyle=COLORS.red;
      ctx.beginPath();ctx.arc(xx,xy,5,0,Math.PI*2);ctx.fill();
      
      // PnL label at exit
      ctx.fillStyle=color;
      ctx.font='bold 10px sans-serif';
      ctx.textAlign='left';
      ctx.fillText((t.pnl>0?'+':'')+t.pnl.toFixed(0),xx+6,xy-3);
    }
  }
  
  // PHASE2 TRADE LINES (orange — on all charts)
  if(showTrades && p2trades.length>0 && data.times){
    const n=data.times.length;
    // Build a time-to-x lookup: find bar index for each trade
    // Use entry_time and exit_time to locate on visible data
    // We need bar indices into data.times, not raw CH indices
    // Map trade times to local indices
    for(const t of p2trades){
      const et=t.entry_time.slice(0,19);
      const xt=t.exit_time.slice(0,19);
      let ei=-1, xi=-1;
      for(let j=0;j<n;j++){
        const tj=data.times[j].slice(0,19);
        if(tj===et) ei=j;
        if(tj===xt) xi=j;
        if(ei>=0&&xi>=0) break;
      }
      if(ei<0||xi<0) continue;
      if(ei>=n||xi>=n) continue;
      
      const ex=x(ei), xx=x(xi);
      // Price uses right scale (ry) for Chart 0, left scale (y) for others
      const priceFn = canvasId==='c0' ? ry : y;
      const ey=priceFn(t.entry_price), xy=priceFn(t.exit_price);
      
      // Entry vertical line (orange dashed) — removed per user
      
      // Exit line — removed per user
      
      
      // Arrow
      const color=t.pnl>0?'#ffa500':'#ff4500';
      ctx.strokeStyle=color;
      ctx.lineWidth=2.5;
      ctx.beginPath();ctx.moveTo(ex,ey);ctx.lineTo(xx,xy);ctx.stroke();
      
      // Entry triangle
      ctx.fillStyle='#ffa500';
      ctx.beginPath();
      ctx.moveTo(ex,ey-5); ctx.lineTo(ex-5,ey+3); ctx.lineTo(ex+5,ey+3);
      ctx.closePath(); ctx.fill();
      
      // Exit cross
      ctx.strokeStyle='#ff4500';
      ctx.lineWidth=2;
      ctx.beginPath();
      ctx.moveTo(xx-4,xy-4); ctx.lineTo(xx+4,xy+4);
      ctx.moveTo(xx+4,xy-4); ctx.lineTo(xx-4,xy+4);
      ctx.stroke();
      
      // PnL label
      ctx.fillStyle=color;
      ctx.font='bold 9px sans-serif';
      ctx.textAlign='left';
      ctx.fillText((t.pnl>0?'+':'')+t.pnl.toFixed(0),xx+6,xy-3);
    }
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
  const startDate=document.getElementById('start-date').value;
  const endDate=document.getElementById('end-date').value;
  if(!startDate||!endDate){document.getElementById('status').textContent='Select start and end dates';return;}
  const showTrades=document.getElementById('showTrades').checked;
  const status=document.getElementById('status');
  status.textContent='Loading...';
  
  fetch('/data?'+new URLSearchParams({ticker,tf,start:startDate,end:endDate}))
    .then(r=>r.json()).then(j=>{
      if(j.error){status.textContent='Error: '+j.error;return;}
      const d=j.data;
      status.textContent=d.times.length+' bars, last: '+d.times[d.times.length-1];
      
      const lastZ=d.z_fiz[d.z_fiz.length-1];
      const lastCrowd=d.crowd_share[d.crowd_share.length-1];
      const lastFizLong=d.fiz_long[d.fiz_long.length-1];
      const lastYurLong=d.yur_long[d.yur_long.length-1];
      const lastClose=d.close[d.close.length-1];
      const trades=d.trades||[];
      const p2trades=window._phase2Trades||[];
      _lastData = {times: d.times, close: d.close, n: d.times.length, fiz_long: d.fiz_long, fiz_short: d.fiz_short, yur_long: d.yur_long, yur_short: d.yur_short};
      const tradeStats=trades.length>0?' Trades: '+trades.length+' ('+(trades.filter(t=>t.pnl>0).length)+'W/'+(trades.filter(t=>t.pnl<=0).length)+'L) Sum: '+trades.reduce((s,t)=>s+t.pnl,0).toFixed(0)+' ₽':'';
      
      document.getElementById('stats').innerHTML=
        '<div class=stat><div class=label>Close</div><div class=value style=color:'+
        (d.close[d.close.length-1]>=d.close[d.close.length-2]?COLORS.green:COLORS.red)+'>'+lastClose+'</div></div>'+
        '<div class=stat><div class=label>Fiz Long %</div><div class=value style=color:#58a6ff>'+lastFizLong.toFixed(1)+'%</div></div>'+
        '<div class=stat><div class=label>Yur Long %</div><div class=value style=color:#d29922>'+lastYurLong.toFixed(1)+'%</div></div>'+
        '<div class=stat><div class=label>Crowd Share</div><div class=value style=color:#bc8cff>'+lastCrowd.toFixed(1)+'%</div></div>'+
        '<div class=stat><div class=label>Fiz z-score</div><div class=value style=color:'+
        (lastZ>2?COLORS.red:lastZ<-2?COLORS.green:COLORS.text)+'>'+lastZ.toFixed(2)+'</div></div>'+
        '<div class=stat><div class=label>Trades</div><div class=value style=color:'+COLORS.cyan+'>'+(trades.length>0?trades.length:'0')+'</div></div>';
      
      // Chart 0: Price + OI
      draw('c0',d,{series:[
        {label:'Close',values:d.close,color:'#ffffff',width:1.5,rightScale:true},
        {label:'Yur Long%',values:d.yur_long,color:COLORS.yurLong,width:1},
        {label:'Yur Short%',values:d.yur_short,color:COLORS.yurShort,width:0.8},
        {label:'Fiz Long%',values:d.fiz_long,color:COLORS.fizLong,width:1},
        {label:'Fiz Short%',values:d.fiz_short,color:COLORS.fizShort,width:0.8},
      ],trades:trades,showTrades:showTrades,p2trades:p2trades});
      
      // Trade info
      let info='';
      if(trades.length>0){
        const winCount=trades.filter(t=>t.pnl>0).length;
        const sumPnl=trades.reduce((s,t)=>s+t.pnl,0);
        info+='<div>Trade count: '+trades.length+' | Win: '+winCount+' ('+(winCount/trades.length*100).toFixed(0)+'%) | Sum: '+(sumPnl>0?'+':'')+sumPnl.toFixed(0)+' ₽</div>';
        trades.slice(-5).reverse().forEach(t=>{
          const cls=t.pnl>0?'win':'lose';
          info+='<div class='+cls+'>'+(t.pnl>0?'▲':'▼')+' '+t.entry_time.slice(5,16)+'→'+t.exit_time.slice(11,16)+
            ' entry='+t.entry_price+' exit='+t.exit_price+' pnl='+(t.pnl>0?'+':'')+t.pnl.toFixed(0)+
            ' ('+t.bars_held+'b vz='+t.vol_z+' yz='+t.yur_z+')</div>';
        });
      }
      document.getElementById('tradeInfo').innerHTML=info;
      
      // Charts 1-4
      draw('c1',d,{series:[
        {label:'Fiz Long',values:d.fiz_long,color:COLORS.fizLong},
        {label:'Fiz Short',values:d.fiz_short,color:COLORS.fizShort},
        {label:'Fiz Net',values:d.fiz_net,color:COLORS.cyan},
      ]});
      draw('c2',d,{series:[
        {label:'Yur Long',values:d.yur_long,color:COLORS.yurLong},
        {label:'Yur Short',values:d.yur_short,color:COLORS.yurShort},
        {label:'Yur Net',values:d.yur_net,color:COLORS.cyan},
      ]});
      draw('c3',d,{series:[
        {label:'Crowd Share',values:d.crowd_share,color:COLORS.purple,width:2},
        {label:'Fiz Long Premium',values:d.fiz_long_premium,color:COLORS.blue,width:1},
      ]});
      draw('c4',d,{series:[
        {label:'Fiz z-score',values:d.z_fiz,color:COLORS.cyan,width:1.5},
      ]});
    }).catch(e=>{status.textContent='Error: '+e.message;});
  // Save state to localStorage
  localStorage.setItem('moex_oi_state', JSON.stringify({
    ticker: document.getElementById('ticker').value,
    tf: document.getElementById('tf').value,
    start: document.getElementById('start-date').value,
    end: document.getElementById('end-date').value,
    showTrades: document.getElementById('showTrades').checked
  }));
}

// Click on chart 0 to show zoom for nearest trade
document.getElementById('c0').addEventListener('click', function(e){
  if(!window._phase2Trades || !window._phase2Trades.length) return;
  const rect = c0.getBoundingClientRect();
  const px = e.clientX - rect.left;
  const pw = rect.width;
  if(px < 0 || px > pw) return;
  const canvasW = c0.width;
  const padL = 45, padR = 55;
  const cw = canvasW - padL - padR;
  const n = _lastData?.n || 0;
  const logicalX = (px / pw) * canvasW;
  const idx = Math.round((logicalX - padL) / cw * (n - 1));
  if(idx < 0 || idx >= n) return;
  // Find nearest trade
  const allTimes = _lastData?.times || [];
  const et = allTimes[idx]?.slice(0,19) || '';
  let best = null, bestDist = Infinity;
  for(const t of window._phase2Trades){
    const te = t.entry_time.slice(0,19);
    let ei = -1;
    for(let j=0;j<allTimes.length;j++){if(allTimes[j].slice(0,19)===te){ei=j;break;}}
    if(ei<0) continue;
    const d = Math.abs(ei - idx);
    if(d < bestDist){bestDist=d;best=t;}
  }
  if(!best || bestDist > Math.max(5, n/50)) return;
  const sel = document.getElementById('p2cfg');
  const cfgName = sel.options[sel.selectedIndex]?.text?.split(' Cal=')[0] || '';
  const tk = document.getElementById('ticker').value;
  const tradeIdx = window._phase2Trades.indexOf(best);
  showZoom(best, tk, cfgName, tradeIdx);
});

// Init or restore state
(function(){
  const saved = localStorage.getItem('moex_oi_state');
  if(saved){
    try{
      const s=JSON.parse(saved);
      document.getElementById('ticker').value=s.ticker||'BR';
      document.getElementById('tf').value=s.tf||'5m';
      if(s.start) document.getElementById('start-date').value=s.start;
      if(s.end) document.getElementById('end-date').value=s.end;
      if(s.showTrades!==undefined) document.getElementById('showTrades').checked=s.showTrades;
    }catch(e){/*ignore*/}
  }
  // If no saved dates, init defaults
  if(!document.getElementById('start-date').value){
    const maxDate = new Date('2026-05-22T23:50:00');
    document.getElementById('end-date').value = new Date(maxDate.getTime() - maxDate.getTimezoneOffset()*60000).toISOString().slice(0,16);
    document.getElementById('start-date').value = '2025-01-01T00:00';
  }
})();

loadData();
setInterval(loadData,60000);

function loadPhase2Configs(){
  const ticker=document.getElementById('ticker').value;
  const sel=document.getElementById('p2cfg');
  sel.innerHTML='<option value="">Loading...</option>';
  fetch('/phase2_configs?t='+ticker).then(r=>r.json()).then(j=>{
    if(!j.configs||!j.configs.length){sel.innerHTML='<option value="">— нет конфигов —</option>';return;}
    let html='<option value="">— Phase2: '+j.wfa+' WFA —</option>';
    j.configs.forEach(c=>{
      html+='<option value="'+c.p+','+c.dr+','+c.h+','+c.am+'|'+c.i+'">'
        +c.pn+' '+c.dr+' h='+c.h+' am='+c.am+' Cal='+c.oos_cal+' n='+c.oos_n+'</option>';
    });
    sel.innerHTML=html;
    // Auto-load trades for best config
    if(j.configs.length){
      sel.value = j.configs[0].p+','+j.configs[0].dr+','+j.configs[0].h+','+j.configs[0].am+'|0';
      loadPhase2();
    }
  }).catch(()=>{sel.innerHTML='<option value="">— error —</option>';});
}

function showZoom(trade, ticker, configName, tradeIdx){
  const card=document.getElementById('zoomCard');
  const cz=document.getElementById('cz');
  const info=document.getElementById('zoomInfo');
  card.style.display='block';
  
  const et=trade.entry_time.slice(0,19);
  const xt=trade.exit_time.slice(0,19);
  
  // Determine zoom window around trade
  const allTimes=_lastData.times;
  let ei=-1, xi=-1;
  for(let j=0;j<allTimes.length;j++){
    if(allTimes[j].slice(0,19)===et) ei=j;
    if(allTimes[j].slice(0,19)===xt) xi=j;
    if(ei>=0&&xi>=0) break;
  }
  if(ei<0||xi<0){info.textContent='Cannot locate trade on chart';return;}
  
  // Zoom window: 60 bars before entry, 40 after exit
  const margin=60;
  const st=Math.max(0,ei-margin);
  const en=Math.min(allTimes.length-1,xi+margin);
  const cnt=en-st+1;
  
  info.innerHTML='<div style="font-size:14px"><b>'+ticker+'</b> | Entry: '+et+' @ <b>'+trade.entry_price+'</b> | Exit: '+xt+' @ <b>'+trade.exit_price+'</b> | PnL: <b style="color:'+(trade.pnl>0?'#3fb950':'#f85149')+'">'+(trade.pnl>0?'+':'')+trade.pnl.toFixed(0)+'</b> | Bars: '+(xi-ei)+' ('+((xi-ei)*5)+'min)</div>';
  
  // Draw zoom canvas
  const rect=cz.parentElement.getBoundingClientRect();
  cz.width=Math.max(rect.width-24,200)*2;
  cz.height=500;
  const ctx=cz.getContext('2d');
  const W=cz.width,H=cz.height,pl=70,pr=20,pt=20,pb=45,cw=W-pl-pr,ch=H-pt-pb;
  ctx.clearRect(0,0,W,H);
  ctx.fillStyle='#0d1117';ctx.fillRect(0,0,W,H);
  
  // Get price data
  const closeVals=_lastData.close||[];
  const sl=closeVals.slice(st,en+1);
  if(sl.length<2){info.textContent='Not enough data';return;}
  const mn=Math.min(...sl),mx=Math.max(...sl),rg=mx-mn||1;
  const padY=rg*0.05;
  const yMin=mn-padY,yMax=mx+padY,yr=yMax-yMin;
  
  function xj(j){return pl+(j-st)/cnt*cw;}
  function yv(v){return pt+ch-(v-yMin)/yr*ch;}
  
  // Grid
  ctx.strokeStyle='#21262d';ctx.lineWidth=0.5;
  for(let p=0;p<=4;p++){const yy=pt+p/4*ch;ctx.beginPath();ctx.moveTo(pl,yy);ctx.lineTo(W-pr,yy);ctx.stroke();}
  
  // Price labels
  ctx.fillStyle='#8b949e';ctx.font='bold 16px monospace';ctx.textAlign='right';
  for(let p=0;p<=4;p++){ctx.fillText((yMin+p/4*yr).toFixed(1),pl-6,pt+p/4*ch+6);}
  
  // Price line
  ctx.strokeStyle='#58a6ff';ctx.lineWidth=1.5;ctx.beginPath();
  for(let j=0;j<sl.length;j++){j===0?ctx.moveTo(xj(st+j),yv(sl[j])):ctx.lineTo(xj(st+j),yv(sl[j]));}
  ctx.stroke();
  
  // Time labels
  ctx.fillStyle='#8b949e';ctx.font='bold 16px monospace';ctx.textAlign='center';
  const ts=Math.max(1,Math.floor(cnt/6));
  for(let j=Math.ceil(st/ts)*ts;j<=en;j+=ts){ctx.fillText(allTimes[j].slice(5,16),xj(j),H-pb+14);}
  
  // OI lines (second scale, right axis)
  const oiData={fiz_long:_lastData.fiz_long,fiz_short:_lastData.fiz_short,yur_long:_lastData.yur_long,yur_short:_lastData.yur_short};
  if(oiData.fiz_long&&oiData.fiz_long.length>st){
    // Find min/max across all 4 OI series in zoom window
    let oiMin=Infinity,oiMax=-Infinity;
    for(const k of ['fiz_long','fiz_short','yur_long','yur_short']){
      const slice=oiData[k].slice(st,en+1);
      oiMin=Math.min(oiMin,...slice);oiMax=Math.max(oiMax,...slice);
    }
    const oiRg=Math.max(oiMax-oiMin,1);
    function yoi(v){return pt+ch-(v-oiMin)/oiRg*ch;}
    const oiColors={'fiz_long':'#2ea043','fiz_short':'#da3633','yur_long':'#58a6ff','yur_short':'#d29922'};
    // Draw OI lines
    for(const [k,clr] of Object.entries(oiColors)){
      const slc=oiData[k].slice(st,en+1);
      ctx.strokeStyle=clr;ctx.lineWidth=1;ctx.globalAlpha=0.7;
      ctx.setLineDash(k.startsWith('yur')?[2,3]:[]);
      ctx.beginPath();
      for(let j=0;j<slc.length;j++){
        j===0?ctx.moveTo(xj(st+j),yoi(slc[j])):ctx.lineTo(xj(st+j),yoi(slc[j]));
      }
      ctx.stroke();
    }
    ctx.setLineDash([]);ctx.globalAlpha=1;
    // OI legend (top-right)
    ctx.font='13px sans-serif';let lx=W-pr-160,ly=pt+18;
    ctx.fillStyle='#8b949e';ctx.fillText('OI %',lx,ly);
    ly+=16;
    for(const [k,clr] of Object.entries(oiColors)){
      ctx.fillStyle=clr;ctx.fillText(k,lx,ly);ly+=14;
    }
    // OI right axis labels
    ctx.fillStyle='#8b949e';ctx.font='bold 14px monospace';ctx.textAlign='left';
    for(let p=0;p<=3;p++){ctx.fillText((oiMin+p/3*oiRg).toFixed(1),W-pr+4,pt+p/3*ch+5);}
  }
  
  // Entry line (green dashed)
  const ex=xj(ei),xx2=xj(xi);
  const ey=yv(trade.entry_price),xy2=yv(trade.exit_price);
  
  // Horizontal dashed lines from price to axis
  ctx.strokeStyle='rgba(63,185,80,0.25)';ctx.lineWidth=1;ctx.setLineDash([3,4]);
  ctx.beginPath();ctx.moveTo(pl,ey);ctx.lineTo(ex,ey);ctx.stroke();
  ctx.strokeStyle='rgba(248,81,73,0.25)';
  ctx.beginPath();ctx.moveTo(xx2,xy2);ctx.lineTo(W-pr,xy2);ctx.stroke();
  ctx.setLineDash([]);
  
  // Entry vertical line (green dashed)
  ctx.strokeStyle='rgba(63,185,80,0.6)';ctx.lineWidth=1;ctx.setLineDash([4,3]);
  ctx.beginPath();ctx.moveTo(ex,pt);ctx.lineTo(ex,pt+ch);ctx.stroke();
  ctx.fillStyle='#3fb950';ctx.font='bold 16px sans-serif';ctx.textAlign='left';
  ctx.fillText('ENTRY',ex+3,pt+14);
  
  // Exit vertical line (red dashed)
  ctx.strokeStyle='rgba(248,81,73,0.6)';
  ctx.beginPath();ctx.moveTo(xx2,pt);ctx.lineTo(xx2,pt+ch);ctx.stroke();
  ctx.fillStyle='#f85149';ctx.font='bold 16px sans-serif';ctx.fillText('EXIT',xx2+3,pt+14);
  ctx.setLineDash([]);
  
  // Trade arrow
  const clr=trade.pnl>0?'#ffa500':'#ff4500';
  ctx.strokeStyle=clr;ctx.lineWidth=4;
  ctx.beginPath();ctx.moveTo(ex,ey);ctx.lineTo(xx2,xy2);ctx.stroke();
  
  // Entry triangle
  ctx.fillStyle='#ffa500';
  ctx.beginPath();ctx.moveTo(ex,ey-10);ctx.lineTo(ex-12,ey+8);ctx.lineTo(ex+12,ey+8);ctx.closePath();ctx.fill();
  // Trade number label
  ctx.fillStyle='#fff';ctx.font='bold 13px monospace';ctx.textAlign='left';
  ctx.fillText('#'+(tradeIdx!==undefined?tradeIdx+1:''),ex+16,ey+5);
  
  // Exit cross
  ctx.strokeStyle='#ff4500';ctx.lineWidth=4;
  ctx.beginPath();ctx.moveTo(xx2-10,xy2-10);ctx.lineTo(xx2+10,xy2+10);
  ctx.moveTo(xx2+10,xy2-10);ctx.lineTo(xx2-10,xy2+10);ctx.stroke();
}

function loadPhase2(){
  const sel=document.getElementById('p2cfg');
  const val=sel.value;
  if(!val||val===''){document.getElementById('p2status').textContent='Select a config first';return;}
  const parts=val.split('|')[0].split(',');
  const ticker=document.getElementById('ticker').value;
  const p=parts[0],d=parts[1],h=parts[2],a=parts[3];
  const status=document.getElementById('p2status');
  status.textContent='Running BT...';
  fetch('/phase2_trades?t='+ticker+'&p='+p+'&d='+d+'&h='+h+'&a='+a).then(r=>r.json()).then(trades=>{
    if(!trades||!trades.length){status.textContent='0 trades';return;}
    // Store phase2 trades globally
    window._phase2Trades = trades;
    status.textContent=trades.length+' trades loaded';
    // Re-draw with phase2 trades
    loadData();
  }).catch(e=>{status.textContent='Error: '+e.message;});
}

// Load phase2 configs when ticker changes
document.addEventListener('DOMContentLoaded', function(){
  document.getElementById('ticker').addEventListener('change', loadPhase2Configs);
  document.getElementById('tf').addEventListener('change', loadPhase2Configs);
  // restore saved state then load configs
  setTimeout(loadPhase2Configs, 500);
});
</script>
</body>
</html>"""

def make_handler(tickers):
    ticker_opts = ''.join(f'<option value=\"{t}\">{t}</option>' for t in tickers)
    phase2_html = ''
    phase2_html += '<select id=\"p2cfg\" style=\"max-width:320px\"><option value=\"\">— Phase2 —</option></select>'
    phase2_html += '<button onclick=\"loadPhase2()\" style=\"background:#1f6feb\">▶ Phase2 сделки</button>'
    phase2_html += '<span id=\"p2status\" style=\"color:#8b949e;font-size:12px\"></span>'
    html = HTML.replace('TICKER_OPTIONS', ticker_opts).replace('PHASE2_CONTROLS', phase2_html)
    
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
                start = qs.get('start', [''])[0].replace('T', ' ')
                end = qs.get('end', [''])[0].replace('T', ' ')
                if start and len(start) == 16: start += ':00'
                if end and len(end) == 16: end += ':00'
                if not start or not end:
                    from datetime import datetime, timedelta
                    end = datetime.now().strftime('%Y-%m-%dT%H:%M')
                    start = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%dT%H:%M')
                
                df = resample_oi(ticker, start, end, tf)
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
            elif self.path.startswith('/phase2_configs'):
                from urllib.parse import urlparse, parse_qs
                qs = parse_qs(urlparse(self.path).query)
                ticker = qs.get('t', ['VB'])[0]
                p2 = get_phase2()
                r = p2.get('results', {}).get(ticker, {}) if p2 else {}
                cfgs = []
                for i, c in enumerate(r.get('top_configs', [])):
                    cfgs.append({
                        'i': i, 'pn': c['pattern_name'], 'dr': c['direction'],
                        'p': c['pattern'], 'h': c['hold'], 'am': c['atr_mult'],
                        'oos_cal': round(c['oos']['calmar'], 2),
                        'oos_n': c['oos']['n'],
                        'score': round(c.get('score', 0), 2)
                    })
                self._json({'wfa': r.get('wfa_passed', 0), 'configs': cfgs})
            elif self.path.startswith('/phase2_trades'):
                from urllib.parse import urlparse, parse_qs
                qs = parse_qs(urlparse(self.path).query)
                ticker = qs.get('t', ['VB'])[0]
                p = qs.get('p', ['v'])[0]
                d = qs.get('d', ['L'])[0]
                h = int(qs.get('h', [5])[0])
                a = int(qs.get('a', [2])[0])
                self._json(run_phase2_bt(ticker, p, d, h, a))
            else:
                self.send_response(404); self.end_headers()
        
        def log_message(self, *a): pass
        
        def _json(self, data, code=200):
            resp = json.dumps(data, default=str).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(resp)
    
    return Handler

def main():
    import socket
    host = '0.0.0.0'
    port = PORT
    
    class ReuseTCPServer(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    server = ReuseTCPServer((host, PORT), make_handler(TICKERS))
    print(f"MOEX OI Dashboard: http://localhost:{PORT}")
    print(f"Tickers: {', '.join(TICKERS)}")
    server.serve_forever()

if __name__ == '__main__':
    main()
