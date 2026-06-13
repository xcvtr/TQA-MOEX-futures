#!/usr/bin/env python3
"""
TRIZ Diamond v4 — Score-based entry system.
Каждый сигнал получает score от компонентов, вход при score ≥ threshold.
"""
import sys, os, json, time
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/../..')
os.chdir(os.path.dirname(os.path.abspath(__file__)) + '/../..')

import numpy as np
import clickhouse_connect
from datetime import datetime
from config import CH_HOST, CH_PORT, CH_DB

OUTPUT_DIR = 'reports/triz_diamond_v4'
CAPITAL = 200_000; COMM = 4; RISK_PCT = 0.02; MAX_LOT = 5; MAX_LEV = 5.0

TICKERS = ['RI', 'GL', 'USDRUBF', 'AF', 'BR', 'IMOEXF', 'CC', 'NM', 'PD', 'SV', 'VB', 'GD']
GO_MAP = {'RI':27034, 'GL':1352, 'USDRUBF':11186, 'AF':673, 'BR':17228, 'IMOEXF':2596, 'CC':506, 'NM':256, 'PD':24487, 'SV':12960, 'VB':1556, 'GD':32003}

PATTERNS = {
    'vol_up':  lambda dv, dyb, dys, dfn, dtoi: dv > 0,
    'vol_up_oi_up': lambda dv, dyb, dys, dfn, dtoi: dv > 0 and dtoi > 0,
    'vol_up_oi_down': lambda dv, dyb, dys, dfn, dtoi: dv > 0 and dtoi < 0,
    'smart_money': lambda dv, dyb, dys, dfn, dtoi: dv > 0 and dyb > 0 and dfn < 0,
}

ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)

CBR_DATES = [
    '2024-02-16','2024-03-22','2024-04-26','2024-06-07','2024-07-26',
    '2024-09-13','2024-10-25','2024-12-20','2025-02-14','2025-03-21',
    '2025-04-25','2025-06-13','2025-07-25','2025-09-12','2025-10-24',
    '2025-12-19','2026-02-14','2026-03-21','2026-04-25',
]

def is_cbr(d):
    dt = datetime.strptime(d[:10], '%Y-%m-%d')
    for c in CBR_DATES:
        cdt = datetime.strptime(c, '%Y-%m-%d')
        if abs((dt - cdt).days) <= 2: return True
    return False

def load_score_data(ticker):
    """Load daily + 5m with all score components."""
    d_rows = ch.query("""
        SELECT toDate(p.time) as d,
               argMax(p.open, p.time) as open, argMax(p.high, p.time) as high,
               argMax(p.low, p.time) as low, argMax(p.close, p.time) as close,
               argMax(p.volume, p.time) as volume,
               argMax(o.yur_buy, p.time) as yur_buy,
               argMax(o.yur_sell, p.time) as yur_sell,
               argMax(o.fiz_buy, p.time) as fiz_buy,
               argMax(o.fiz_sell, p.time) as fiz_sell,
               argMax(o.total_oi, p.time) as total_oi
        FROM moex.prices_5m p INNER JOIN moex.prices_5m_oi o ON p.symbol=o.symbol AND p.time=o.time
        WHERE p.symbol=%(t)s AND p.time>='2024-01-01' AND p.time<='2026-05-01'
        GROUP BY d ORDER BY d
    """, parameters={'t': ticker}).result_rows
    if len(d_rows) < 60: return None
    
    a = np.array([list(r) for r in d_rows], dtype=object)
    d_dates = [str(r[0]) for r in d_rows]
    d_opn = a[:,1].astype(float); d_high = a[:,2].astype(float)
    d_low = a[:,3].astype(float); d_close = a[:,4].astype(float)
    d_vol = a[:,5].astype(float); d_yb = a[:,6].astype(float)
    d_ys = a[:,7].astype(float); d_fb = a[:,8].astype(float)
    d_fs = a[:,9].astype(float); d_toi = a[:,10].astype(float)
    
    d_toi = np.where(d_toi<=0, 1, d_toi)
    v_m = np.mean(d_vol)+1; yb_m = np.mean(d_yb)+1
    ys_m = np.mean(d_ys)+1; toi_m = np.mean(d_toi)+1
    dv = np.diff(d_vol)/v_m; dyb = np.diff(d_yb)/yb_m
    dys = np.diff(d_ys)/ys_m; dtoi = np.diff(d_toi)/toi_m
    fiz_net = (d_fb-d_fs)/d_toi*100; dfn = np.diff(fiz_net)
    
    # Volume z-score
    def rolling_z(arr, w=20):
        z = np.zeros(len(arr))
        for i in range(w, len(arr)):
            s = arr[i-w:i]; mu = np.mean(s); sd = np.std(s)+0.001
            z[i] = (arr[i]-mu)/sd
        return z
    
    vol_z = rolling_z(d_vol, 20)[1:]  # align with diff
    
    sma50 = np.full(len(d_close), np.nan)
    if len(d_close) >= 50:
        cs_ = np.cumsum(d_close); sma50[49]=cs_[49]/50; sma50[50:]=(cs_[50:]-cs_[:-50])/50
    
    # 5m stacked: fiz_z on last 3 bars of day
    m5_rows = ch.query("""
        SELECT p.time, o.fiz_buy, o.fiz_sell, o.total_oi, o.yur_buy, o.yur_sell
        FROM moex.prices_5m_oi o INNER JOIN moex.prices_5m p ON p.symbol=o.symbol AND p.time=o.time
        WHERE p.symbol=%(t)s AND p.time>='2024-01-01' AND p.time<='2026-05-01'
        ORDER BY p.time
    """, parameters={'t': ticker}).result_rows
    if len(m5_rows) < 200: return None
    
    m5_toi = np.array([float(r[3]) for r in m5_rows])
    m5_fb = np.array([float(r[1]) for r in m5_rows])
    m5_fs = np.array([float(r[2]) for r in m5_rows])
    m5_toi = np.where(m5_toi<=0, 1, m5_toi)
    m5_fn = (m5_fb-m5_fs)/m5_toi*100
    m5_fiz_z = rolling_z(m5_fn, 20)
    
    daily_5m = defaultdict(list)
    for i in range(len(m5_rows)):
        daily_5m[m5_rows[i][0].strftime('%Y-%m-%d')].append(m5_fiz_z[i])
    
    daily_fiz_z = {}
    for ds, zs in daily_5m.items():
        daily_fiz_z[ds] = np.mean(zs[-3:]) if len(zs)>=3 else 0
    
    # yur_change filter
    yb_change = np.diff(d_yb)
    
    return dict(dates=d_dates, opn=d_opn, high=d_high, low=d_low, close=d_close,
                dv=dv, dyb=dyb, dys=dys, dfn=dfn, dtoi=dtoi, sma50=sma50,
                vol_z=vol_z, yb_change=yb_change, daily_fiz_z=daily_fiz_z, n=len(d_rows))


def score_backtest(data, pname, hold, sl_pct, cs, ticker,
                   score_threshold=3, use_cbr=True, trend_filter=True):
    """Score-based entry: each component adds to score."""
    pfunc = PATTERNS[pname]
    dates = data['dates']; opn = data['opn']; high = data['high']
    low = data['low']; close = data['close']
    dv = data['dv']; dyb = data['dyb']; dys = data['dys']
    dfn = data['dfn']; dtoi = data['dtoi']; sma50 = data['sma50']
    vol_z = data['vol_z']; yb_ch = data['yb_change']
    fiz_z = data['daily_fiz_z']
    n = len(close)
    go_val = GO_MAP.get(ticker, 1000)
    eq = float(CAPITAL); peak = eq; mdd = 0.0; trades = []
    
    for i in range(50, n - max(hold, 2)):
        if i >= len(dv): break
        
        # Compute score
        score = 0
        
        # 1. Pattern base
        if not pfunc(dv[i], dyb[i], dys[i], dfn[i], dtoi[i]):
            continue
        score += 2  # pattern hit
        
        # 2. Volume z-score magnitude
        if i < len(vol_z):
            if vol_z[i] > 2.0: score += 2
            elif vol_z[i] > 1.5: score += 1
        
        # 3. Stacked fiz_z
        fz = fiz_z.get(dates[i], 0)
        if fz > 2.0: score += 2
        elif fz > 1.0: score += 1
        
        # 4. yur_buy direction (institutional buying)
        if i < len(yb_ch) and yb_ch[i] > 0: score += 1
        
        # 5. dv magnitude (signal strength)
        dv_mag = abs(dv[i])
        if dv_mag > 3.0: score += 1
        
        # 6. Trend filter
        if trend_filter and sma50 is not None and i < len(sma50) and not np.isnan(sma50[i]):
            if close[i] <= sma50[i]:
                score -= 2
        
        # 7. CBR penalty
        if use_cbr and is_cbr(dates[i]):
            score -= 3
        
        if score < score_threshold:
            continue
        
        ei = i + 1
        if ei >= n - 1: continue
        ep = float(opn[ei])
        xi = min(ei + hold, n - 1)
        sp = ep * (1 - sl_pct) if sl_pct > 0 else 0
        stop_hit = False
        xp = float(close[xi])
        if sl_pct > 0:
            for j in range(ei, xi+1):
                if float(low[j]) <= sp:
                    xp = sp; stop_hit = True; break
        
        go = ep * cs
        if go <= 0: continue
        
        risk_amount = eq * RISK_PCT
        if sl_pct > 0:
            base_nc = risk_amount / (go * sl_pct)
        else:
            base_nc = risk_amount / go * 5
        base_nc = max(1, int(base_nc))
        nc = min(base_nc, MAX_LOT)
        max_by_go = int(eq * MAX_LEV / go) if go > 0 else 99
        nc = min(nc, max_by_go)
        if nc < 1: continue
        
        gp = nc * cs * (xp - ep)
        npnl = gp - nc * COMM
        eq += npnl
        if eq > peak: peak = eq
        dd = (peak - eq) / peak * 100 if peak > 0 else 0
        mdd = max(mdd, dd)
        
        trades.append(dict(entry=dates[ei], score=score, fz=round(fz,1)))
    
    if not trades: return None
    ret = (eq-CAPITAL)/CAPITAL*100
    wins = sum(1 for t in trades if True)
    # Actually check pnl
    # need npnl in trades
    
    return None  # placeholder - rewrite below


# Simplier: just test vol_z filter instead of strict stacked
print("="*60)
print("PHASE: vol_z score-based entry (relaxed stacked)")
print("="*60)

# Quick test: RI vol_up with vol_z>1.5 filter
def run_simple(ticker, pname, hold, sl_pct):
    cs = {'RI':1, 'GL':1, 'USDRUBF':1000, 'AF':1, 'BR':10, 'IMOEXF':10}.get(ticker, 1)
    data = load_score_data(ticker)
    if data is None: return None
    
    dates = data['dates']; opn = data['opn']; high = data['high']
    low = data['low']; close = data['close']; vol_z = data['vol_z']
    dv = data['dv']; dyb = data['dyb']; dys = data['dys']
    dfn = data['dfn']; dtoi = data['dtoi']; sma50 = data['sma50']
    fiz_z = data['daily_fiz_z']; n = len(close)
    pfunc = PATTERNS[pname]
    go_val = GO_MAP.get(ticker, 1000)
    
    eq = float(CAPITAL); peak = eq; mdd = 0.0; trades = []
    
    for i in range(50, n - max(hold, 2)):
        if i >= len(dv): break
        if not pfunc(dv[i], dyb[i], dys[i], dfn[i], dtoi[i]): continue
        
        # Relaxed stacked: vol_z > 1.5 OR fiz_z > 1.0
        fz = fiz_z.get(dates[i], 0)
        v = vol_z[i] if i < len(vol_z) else 0
        if v < 1.5 and fz < 1.0: continue
        
        if is_cbr(dates[i]): continue
        if sma50 is not None and i < len(sma50) and not np.isnan(sma50[i]):
            if close[i] <= sma50[i]: continue
        
        ei = i + 1; ep = float(opn[ei])
        xi = min(ei+hold, n-1)
        sp = ep*(1-sl_pct) if sl_pct>0 else 0
        xp = float(close[xi])
        if sl_pct>0:
            for j in range(ei, xi+1):
                if float(low[j]) <= sp: xp=sp; break
        
        go = ep*cs; risk_amount = eq*RISK_PCT
        if sl_pct>0: base_nc = risk_amount/(go*sl_pct)
        else: base_nc = risk_amount/go*5
        base_nc = max(1,int(base_nc))
        nc = min(base_nc, MAX_LOT)
        max_by_go = int(eq*MAX_LEV/go) if go>0 else 99
        nc = min(nc, max_by_go)
        if nc < 1:
            continue
        
        gp = nc*cs*(xp-ep); npnl = gp - nc*COMM
        eq += npnl; trades.append(npnl)
        if eq > peak: peak=eq
        dd = (peak-eq)/peak*100 if peak>0 else 0
        mdd = max(mdd, dd)
    
    if not trades: return None
    ret = (eq-CAPITAL)/CAPITAL*100
    wins = sum(1 for t in trades if t>0)
    wr = wins/len(trades)*100 if trades else 0
    gp_s = sum(t for t in trades if t>0)
    gl_s = sum(t for t in trades if t<0)
    pf = abs(gp_s/(gl_s+1))
    return dict(ret=round(ret,2), mdd=round(mdd,2), wr=round(wr,1), pf=round(pf,2),
                trades=len(trades), calmar=round(ret/mdd,2) if mdd>0 else 0)


for ticker in ['RI', 'GL', 'AF', 'BR', 'IMOEXF', 'USDRUBF', 'CC']:
    print(f"\n--- {ticker} ---")
    best = None
    for pname in PATTERNS:
        for hold in [2, 3, 5]:
            for sl_pct in [0.01, 0.02]:
                r = run_simple(ticker, pname, hold, sl_pct)
                if r and r.get('trades', 0) >= 5:
                    if best is None or r.get('calmar', 0) > best.get('calmar', 0):
                        best = r
                        best['pname'] = pname
                        best['hold'] = hold
                        best['sl'] = sl_pct
    if best:
        print(f"  Best: {best['ret']:+.1f}% Calmar={best['calmar']:.1f} WR={best['wr']:.0f}% "
              f"tr={best['trades']} {best['pname']} h={best['hold']} sl={best['sl']:.0%}")

print("\nDone.")
