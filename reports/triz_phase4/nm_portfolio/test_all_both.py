"""Test all megagrid tickers: long vs short vs both."""
import sys, os
sys.path.insert(0, '/home/user/projects/TQA-MOEX')
os.chdir('/home/user/projects/TQA-MOEX')
import numpy as np
import clickhouse_connect
from config import CH_HOST, CH_PORT, CH_DB

ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)
TICKERS = ['CC','NM','PD','SV','VB','GD','SR','LK','PT','Si','Eu','CNYRUBF','CR','NG','MX','AL','RN','GL','AF','BR','IMOEXF']

CAP, COMM, MAX_LOT, RISK_PCT = 200_000, 4, 5, 0.02

def bt(ticker, direction, hold, atr_mult):
    rows = ch.query("""
        SELECT toDate(p.time) as d, argMax(p.open,p.time), argMax(p.high,p.time),
               argMax(p.low,p.time), argMax(p.close,p.time), argMax(p.volume,p.time)
        FROM moex.prices_5m p WHERE p.symbol=%(t)s AND p.time>='2024-01-01' AND p.time<='2026-05-01'
        GROUP BY d ORDER BY d
    """, parameters={'t': ticker}).result_rows
    if len(rows) < 60: return None
    
    a = np.array([list(r) for r in rows], dtype=object)
    opn=a[:,1].astype(float); high=a[:,2].astype(float); low=a[:,3].astype(float)
    close=a[:,4].astype(float); vol=a[:,5].astype(float)
    
    tr = np.zeros(len(close))
    tr[1:] = np.maximum(high[1:]-low[1:], np.maximum(abs(high[1:]-close[:-1]), abs(low[1:]-close[:-1])))
    atr = np.full(len(close), np.nan)
    for i in range(14, len(close)): atr[i] = np.mean(tr[i-13:i+1])
    
    eq = float(CAP); peak = eq; mdd = 0.0; trades = []
    
    for i in range(50, len(close)-hold-1):
        ep = float(opn[i+1])
        if vol[i] < np.mean(vol[:i]) * 1.2: continue
        nc = min(max(1, int(eq*RISK_PCT/(ep*0.005))), MAX_LOT)
        
        if direction in ('long','both'):
            sp = ep * (1 - min(max(atr[i]/ep*atr_mult, 0.005), 0.05)) if not np.isnan(atr[i]) else ep*0.95
            r_h = ep
            hit = False
            for j in range(i+1, min(i+hold+1, len(close))):
                bh = float(high[j])
                if bh > r_h:
                    r_h = bh
                    if not np.isnan(atr[j]): sp = max(sp, r_h*(1-min(max(atr[j]/r_h*atr_mult,0.005),0.05)))
                if float(low[j]) <= sp:
                    eq += nc*1*(sp-ep) - nc*COMM
                    trades.append({'d':'L','p':nc*1*(sp-ep)-nc*COMM})
                    hit = True; break
            if not hit:
                xp = float(close[min(i+hold, len(close)-1)])
                eq += nc*1*(xp-ep) - nc*COMM
                trades.append({'d':'L','p':nc*1*(xp-ep)-nc*COMM})
        
        if direction in ('short','both'):
            sp = ep * (1 + min(max(atr[i]/ep*atr_mult, 0.005), 0.05)) if not np.isnan(atr[i]) else ep*1.05
            r_l = ep
            hit = False
            for j in range(i+1, min(i+hold+1, len(close))):
                bl = float(low[j])
                if bl < r_l:
                    r_l = bl
                    if not np.isnan(atr[j]): sp = min(sp, r_l*(1+min(max(atr[j]/r_l*atr_mult,0.005),0.05)))
                if float(high[j]) >= sp:
                    eq += nc*1*(ep-sp) - nc*COMM
                    trades.append({'d':'S','p':nc*1*(ep-sp)-nc*COMM})
                    hit = True; break
            if not hit:
                xp = float(close[min(i+hold, len(close)-1)])
                eq += nc*1*(ep-xp) - nc*COMM
                trades.append({'d':'S','p':nc*1*(ep-xp)-nc*COMM})
        
        if eq > peak: peak = eq
        dd = (peak-eq)/peak*100 if peak>0 else 0
        mdd = max(mdd, dd)
    
    if not trades: return None
    ret = (eq-CAP)/CAP*100
    wins = sum(1 for t in trades if t['p']>0)
    gp = sum(t['p'] for t in trades if t['p']>0)
    gl = sum(t['p'] for t in trades if t['p']<0)
    return dict(ret=round(ret,2), dd=round(mdd,2), wr=round(wins/len(trades)*100,1),
                tr=len(trades), cal=round(ret/mdd,2) if mdd>0 else 0)

# Test all tickers long vs short vs both
print(f"{'Ticker':>8} {'LongRet':>8} {'LongDD':>6} {'LongCal':>7} {'ShortRet':>9} {'ShortDD':>6} {'ShortCal':>8} {'BothRet':>8} {'BothDD':>6} {'BothCal':>7} {'Diff':>6}")
print("-"*90)

for t in TICKERS:
    r_long = bt(t, 'long', 13, 2)
    r_short = bt(t, 'short', 13, 2)
    r_both = bt(t, 'both', 13, 2)
    
    lr = f"{r_long['ret']:+.1f}%" if r_long else "N/A"
    ld = f"{r_long['dd']:.1f}%" if r_long else "N/A"
    lc = f"{r_long['cal']:.1f}" if r_long else "N/A"
    sr = f"{r_short['ret']:+.1f}%" if r_short else "N/A"
    sd = f"{r_short['dd']:.1f}%" if r_short else "N/A"
    sc = f"{r_short['cal']:.1f}" if r_short else "N/A"
    br = f"{r_both['ret']:+.1f}%" if r_both else "N/A"
    bd = f"{r_both['dd']:.1f}%" if r_both else "N/A"
    bc = f"{r_both['cal']:.1f}" if r_both else "N/A"
    
    diff = ""
    if r_long and r_short:
        if r_long['ret'] > 10 and r_short['ret'] > 10:
            diff = "BOTH"
        elif r_long['ret'] > 10 and r_short['ret'] < -10:
            diff = "TREND"
        elif abs(r_long['ret']) < 10 and abs(r_short['ret']) < 10:
            diff = "WEAK"
    
    print(f"{t:>8} {lr:>8} {ld:>6} {lc:>7} {sr:>9} {sd:>6} {sc:>8} {br:>8} {bd:>6} {bc:>7} {diff:>6}")
