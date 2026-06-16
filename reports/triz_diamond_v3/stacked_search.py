#!/usr/bin/env python3
"""
TRIZ Diamond Search v3 — 5m stacked confirmation.
Daily паттерн + 5m fiz_net z-score как фильтр входа.
"""
import sys, os, json, time
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/../..')
os.chdir(os.path.dirname(os.path.abspath(__file__)) + '/../..')

import numpy as np
import clickhouse_connect
from datetime import datetime, timedelta
from config import CH_HOST, CH_PORT, CH_DB

OUTPUT_DIR = 'reports/triz_diamond_v3'
CAPITAL = 200_000
COMM = 4
RISK_PCT = 0.02
MAX_LOT = 5
MAX_LEV = 5.0

TICKERS = ['RI', 'GL', 'USDRUBF', 'AF', 'CC', 'NM', 'PD', 'BR', 'SV', 'IMOEXF', 'VB']
GO_MAP = {'RI':27034, 'USDRUBF':11186, 'GL':1352, 'VB':1556, 'AF':673, 'CC':506, 'PD':24487, 'NM':256, 'BR':17228, 'SV':12960, 'IMOEXF':2596}

PATTERNS = {
    'vol_up_oi_up_yb_up':    lambda dv, dyb, dys, dfn, dtoi: dv > 0 and dtoi > 0 and dyb > 0,
    'smart_money':            lambda dv, dyb, dys, dfn, dtoi: dv > 0 and dyb > 0 and dfn < 0,
    'vol_up_oi_down':        lambda dv, dyb, dys, dfn, dtoi: dv > 0 and dtoi < 0,
    'vol_up_yb_down_fiz_up': lambda dv, dyb, dys, dfn, dtoi: dv > 0 and dyb < 0 and dfn > 0,
    'fiz_extreme_vol_up':    lambda dv, dyb, dys, dfn, dtoi: dv > 0 and abs(dfn) > 5,
}

ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)

def load_data_stacked(ticker):
    """
    Load daily data + recent 5m OI for stacked confirmation.
    Returns daily OHLCV + OI, and for each daily date — the last 5m bar's OI features.
    """
    # Daily data
    d_rows = ch.query("""
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
        WHERE p.symbol = %(t)s AND p.time >= '2024-01-01' AND p.time <= '2026-05-01'
        GROUP BY d ORDER BY d
    """, parameters={'t': ticker}).result_rows
    if len(d_rows) < 60:
        return None
    
    # 5m data for stacked confirmation
    m5_rows = ch.query("""
        SELECT p.time, p.open, p.high, p.low, p.close, p.volume,
               o.yur_buy, o.yur_sell, o.fiz_buy, o.fiz_sell, o.total_oi
        FROM moex.prices_5m p
        INNER JOIN moex.prices_5m_oi o ON p.symbol = o.symbol AND p.time = o.time
        WHERE p.symbol = %(t)s AND p.time >= '2024-01-01' AND p.time <= '2026-05-01'
        ORDER BY p.time
    """, parameters={'t': ticker}).result_rows
    if len(m5_rows) < 200:
        return None
    
    # Build daily features
    a = np.array([list(r) for r in d_rows], dtype=object)
    d_dates = np.array([str(r[0]) for r in d_rows])
    d_opn = a[:, 1].astype(float); d_high = a[:, 2].astype(float)
    d_low = a[:, 3].astype(float); d_close = a[:, 4].astype(float)
    d_vol = a[:, 5].astype(float)
    d_yb = a[:, 6].astype(float); d_ys = a[:, 7].astype(float)
    d_fb = a[:, 8].astype(float); d_fs = a[:, 9].astype(float); d_toi = a[:, 10].astype(float)
    
    d_toi = np.where(d_toi <= 0, 1, d_toi)
    v_m = np.mean(d_vol) + 1; yb_m = np.mean(d_yb) + 1; ys_m = np.mean(d_ys) + 1; toi_m = np.mean(d_toi) + 1
    dv = np.diff(d_vol) / v_m; dyb = np.diff(d_yb) / yb_m; dys = np.diff(d_ys) / ys_m
    dtoi = np.diff(d_toi) / toi_m
    fiz_net = (d_fb - d_fs) / d_toi * 100; dfn = np.diff(fiz_net)
    
    sma50 = np.full(len(d_close), np.nan)
    if len(d_close) >= 50:
        cs_ = np.cumsum(d_close); sma50[49] = cs_[49] / 50; sma50[50:] = (cs_[50:] - cs_[:-50]) / 50
    
    # For each daily bar, find the last 5m bar that day
    # Build fiz_net z-score on 5m data
    m5_times = np.array([r[0] for r in m5_rows])
    m5_open = np.array([float(r[1]) for r in m5_rows])
    m5_high = np.array([float(r[2]) for r in m5_rows])
    m5_low = np.array([float(r[3]) for r in m5_rows])
    m5_close = np.array([float(r[4]) for r in m5_rows])
    m5_vol = np.array([float(r[5]) for r in m5_rows])
    m5_yb = np.array([float(r[6]) for r in m5_rows])
    m5_ys = np.array([float(r[7]) for r in m5_rows])
    m5_fb = np.array([float(r[8]) for r in m5_rows])
    m5_fs = np.array([float(r[9]) for r in m5_rows])
    m5_toi = np.array([float(r[10]) for r in m5_rows])
    
    m5_toi = np.where(m5_toi <= 0, 1, m5_toi)
    m5_fiz_net = (m5_fb - m5_fs) / m5_toi * 100
    
    # Rolling z-score of fiz_net on 5m (window=20 ~ 100min)
    def rolling_z(arr, w=20):
        z = np.zeros(len(arr))
        for i in range(w, len(arr)):
            s = arr[i-w:i]
            mu = np.mean(s)
            sd = np.std(s) + 0.001
            z[i] = (arr[i] - mu) / sd
        return z
    
    m5_fiz_z = rolling_z(m5_fiz_net, 20)
    
    # For each daily date, find the last 5m bar's fiz_z
    # Also check if ANY 5m bar today had fiz_z > threshold
    # Map: date string -> list of (time, fiz_z, m5_close)
    daily_5m = defaultdict(list)
    for i in range(len(m5_rows)):
        dt = m5_times[i]
        d_str = dt.strftime('%Y-%m-%d')
        daily_5m[d_str].append((dt, m5_fiz_z[i], m5_fs[i], m5_yb[i]))
    
    # For each daily row, find stacked OI confirmation
    # mid_z = mean of last 3 5m bars of the day
    daily_stacked = {}
    for d_str, bars in daily_5m.items():
        last3 = bars[-3:] if len(bars) >= 3 else bars
        mid_z = np.mean([b[1] for b in last3]) if last3 else 0
        max_z = max([b[1] for b in last3]) if last3 else 0
        # Also: was fiz selling intensifying? (fs change)
        daily_stacked[d_str] = {'fiz_z': mid_z, 'fiz_z_max': max_z}
    
    # 5m volume spike on the last bar of the day
    m5_vol_z = rolling_z(m5_vol, 40)  # ~3h window
    for d_str, bars in daily_5m.items():
        last = bars[-1] if bars else None
        if last and d_str in daily_stacked:
            idx = m5_times.tolist().index(last[0]) if last[0] in m5_times else -1
            if idx >= 0:
                daily_stacked[d_str]['vol_z'] = m5_vol_z[idx]
    
    return dict(
        dates=d_dates, opn=d_opn, high=d_high, low=d_low, close=d_close,
        vol=d_vol, yb=d_yb, ys=d_ys, fb=d_fb, fs=d_fs, toi=d_toi,
        dv=dv, dyb=dyb, dys=dys, dfn=dfn, dtoi=dtoi, sma50=sma50, n=len(d_rows),
        daily_stacked=daily_stacked,  # {date_str: {fiz_z, fiz_z_max, vol_z}}
    )


def backtest_stacked(data, pfunc, hold, sl_pct, cs, ticker, 
                     stacked_fiz_thr=1.5, stacked_vol_thr=2.0,
                     use_cbr=True, use_atr=False):
    """Backtest with stacked 5m confirmation filter."""
    dates = data['dates']; opn = data['opn']; high = data['high']
    low = data['low']; close = data['close']
    dv = data['dv']; dyb = data['dyb']; dys = data['dys']
    dfn = data['dfn']; dtoi = data['dtoi']; sma50 = data['sma50']
    ds = data['daily_stacked']
    n = len(close)
    go_val = GO_MAP.get(ticker, 1000)
    
    CBR_DATES = [
        '2024-02-16','2024-03-22','2024-04-26','2024-06-07','2024-07-26',
        '2024-09-13','2024-10-25','2024-12-20','2025-02-14','2025-03-21',
        '2025-04-25','2025-06-13','2025-07-25','2025-09-12','2025-10-24',
        '2025-12-19','2026-02-14','2026-03-21','2026-04-25',
    ]
    
    def is_cbr(d_str):
        dt = datetime.strptime(d_str[:10], '%Y-%m-%d')
        for cbr in CBR_DATES:
            cdt = datetime.strptime(cbr, '%Y-%m-%d')
            if abs((dt - cdt).days) <= 2:
                return True
        return False
    
    eq = float(CAPITAL)
    peak = eq
    mdd = 0.0
    trades = []
    skipped_no_stacked = 0
    skipped_cbr = 0
    
    for i in range(50, n - max(hold, 2)):
        if i >= len(dv) or i >= len(dfn) or i >= len(dtoi):
            break
        
        if not pfunc(dv[i], dyb[i], dys[i], dfn[i], dtoi[i]):
            continue
        
        # Stacked confirmation: check 5m fiz_z on the same day
        date_str = dates[i]
        stacked = ds.get(date_str, None)
        if stacked:
            fiz_z = stacked.get('fiz_z', 0)
            vol_z = stacked.get('vol_z', 0)
            if fiz_z < stacked_fiz_thr and vol_z < stacked_vol_thr:
                skipped_no_stacked += 1
                continue
        else:
            # No 5m data for this day — skip (conservative)
            skipped_no_stacked += 1
            continue
        
        # CBR filter
        if use_cbr and is_cbr(date_str):
            skipped_cbr += 1
            continue
        
        # Trend filter
        if sma50 is not None and i < len(sma50) and not np.isnan(sma50[i]):
            if close[i] <= sma50[i]:
                continue
        
        ei = i + 1
        if ei >= n - 1:
            continue
        ep = float(opn[ei])
        
        adj_hold = hold
        xi = min(ei + adj_hold, n - 1)
        sp = ep * (1 - sl_pct) if sl_pct > 0 else 0
        stop_hit = False
        xp = float(close[xi])
        if sl_pct > 0:
            for j in range(ei, xi + 1):
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
        
        eq_before = eq
        gp = nc * cs * (xp - ep)
        cm_val = nc * COMM
        npnl = gp - cm_val
        eq += npnl
        if eq > peak: peak = eq
        dd = (peak - eq) / peak * 100 if peak > 0 else 0
        mdd = max(mdd, dd)
        
        trades.append(dict(entry=dates[ei], exit=dates[xi],
            ep=round(ep,2), xp=round(xp,2), nc=nc, npnl=round(npnl,0),
            fiz_z=round(stacked['fiz_z'],2), vol_z=round(stacked.get('vol_z',0),2),
            stop=stop_hit))
    
    if not trades:
        return None
    
    ret = (eq - CAPITAL) / CAPITAL * 100
    wins = sum(1 for t in trades if t['npnl'] > 0)
    wr = wins / len(trades) * 100 if trades else 0
    gp_sum = sum(t['npnl'] for t in trades if t['npnl'] > 0)
    gl_sum = sum(t['npnl'] for t in trades if t['npnl'] < 0)
    pf = abs(gp_sum / (gl_sum + 1))
    
    return dict(ret=round(ret,2), mdd=round(mdd,2), wr=round(wr,1), pf=round(pf,2),
                trades=len(trades), wins=wins, net_pnl=round(gp_sum+gl_sum,0),
                calmar=round(ret/mdd,2) if mdd > 0 else 0,
                skipped_no_stacked=skipped_no_stacked, skipped_cbr=skipped_cbr)


# Phase 1: Sweep with stacked confirmation
print("="*60)
print("PHASE 1: 5m stacked confirmation sweep")
print(f"  Tickers: {', '.join(TICKERS)}")
print("="*60)

t0 = time.time()
results = []

for ticker in TICKERS:
    print(f"\n  {ticker}: loading data...")
    data = load_data_stacked(ticker)
    if data is None:
        print(f"    no data")
        continue
    
    ticker_results = []
    for pname, pfunc in PATTERNS.items():
        for hold in [2, 3, 5]:
            for sl_pct in [0.01, 0.02]:
                # Without stacked
                r = backtest_stacked(data, pfunc, hold, sl_pct,
                    {'RI':1, 'USDRUBF':1000, 'GL':1, 'VB':100, 'AF':1, 'CC':10, 
                     'NM':10, 'PD':1, 'BR':10, 'SV':10, 'IMOEXF':10}.get(ticker, 1),
                    ticker, stacked_fiz_thr=-999, stacked_vol_thr=-999, use_cbr=True)
                if r and r['trades'] >= 5:
                    ticker_results.append(dict(ticker=ticker, pattern=pname,
                        hold=hold, sl=sl_pct, stacked='none',
                        ret=r['ret'], mdd=r['mdd'], wr=r['wr'], pf=r['pf'],
                        trades=r['trades'], calmar=r['calmar']))
                
                # With stacked confirmation (fiz_z > 1.5)
                r2 = backtest_stacked(data, pfunc, hold, sl_pct,
                    {'RI':1, 'USDRUBF':1000, 'GL':1, 'VB':100, 'AF':1, 'CC':10, 
                     'NM':10, 'PD':1, 'BR':10, 'SV':10, 'IMOEXF':10}.get(ticker, 1),
                    ticker, stacked_fiz_thr=1.5, use_cbr=True)
                if r2 and r2['trades'] >= 3:
                    ticker_results.append(dict(ticker=ticker, pattern=pname,
                        hold=hold, sl=sl_pct, stacked='fiz_z>1.5',
                        ret=r2['ret'], mdd=r2['mdd'], wr=r2['wr'], pf=r2['pf'],
                        trades=r2['trades'], calmar=r2['calmar']))
                
                # With strict stacked (fiz_z > 2.0)
                r3 = backtest_stacked(data, pfunc, hold, sl_pct,
                    {'RI':1, 'USDRUBF':1000, 'GL':1, 'VB':100, 'AF':1, 'CC':10, 
                     'NM':10, 'PD':1, 'BR':10, 'SV':10, 'IMOEXF':10}.get(ticker, 1),
                    ticker, stacked_fiz_thr=2.0, use_cbr=True)
                if r3 and r3['trades'] >= 3:
                    ticker_results.append(dict(ticker=ticker, pattern=pname,
                        hold=hold, sl=sl_pct, stacked='fiz_z>2.0',
                        ret=r3['ret'], mdd=r3['mdd'], wr=r3['wr'], pf=r3['pf'],
                        trades=r3['trades'], calmar=r3['calmar']))
    
    results.extend(ticker_results)
    
    # Compare
    no_stack = [r for r in ticker_results if r['stacked'] == 'none']
    w_stack = [r for r in ticker_results if r['stacked'] != 'none']
    
    best_no = sorted(no_stack, key=lambda x: -x['calmar'])[0] if no_stack else None
    best_w = sorted(w_stack, key=lambda x: -x['calmar'])[0] if w_stack else None
    
    print(f"    Total: {len(ticker_results)} combos")
    if best_no:
        print(f"    Best NO stacked:  {best_no['ret']:>+7.1f}% Calmar={best_no['calmar']:>5.1f} WR={best_no['wr']:>4.1f}% {best_no['pattern']} h={best_no['hold']}")
    if best_w:
        print(f"    Best WITH stacked:{best_w['ret']:>+7.1f}% Calmar={best_w['calmar']:>5.1f} WR={best_w['wr']:>4.1f}% {best_w['stacked']} {best_w['pattern']} h={best_w['hold']}")

# Summary
print(f"\n{'='*60}")
print(f"SUMMARY")
print(f"{'='*60}")

# Group by stacked vs no stacked
no_stack_all = [r for r in results if r['stacked'] == 'none']
stacked_all = [r for r in results if r['stacked'] != 'none']

print(f"\nWithout stacked: {len(no_stack_all)} combos")
diamonds_no = [r for r in no_stack_all if r['calmar'] >= 2 and r['trades'] >= 8]
print(f"  Diamonds (Calmar>=2): {len(diamonds_no)}")
for r in sorted(diamonds_no, key=lambda x: -x['calmar'])[:5]:
    print(f"    {r['ticker']:>8} {r['ret']:>+7.1f}% Calmar={r['calmar']:>5.1f} WR={r['wr']:>4.1f}% {r['pattern']} h={r['hold']}")

print(f"\nWith stacked: {len(stacked_all)} combos")
diamonds_st = [r for r in stacked_all if r['calmar'] >= 3 and r['trades'] >= 5]
print(f"  Diamonds (Calmar>=3): {len(diamonds_st)}")
for r in sorted(diamonds_st, key=lambda x: -x['calmar'])[:10]:
    print(f"    {r['ticker']:>8} {r['ret']:>+7.1f}% Calmar={r['calmar']:>5.1f} WR={r['wr']:>4.1f}% {r['stacked']:>10} {r['pattern']} h={r['hold']}")

# Compare: stacked vs no stacked for same (ticker, pattern, hold)
print(f"\n{'='*60}")
print("STACKED IMPROVEMENT (direct comparison)")
print(f"{'='*60}")
for r_st in diamonds_st:
    # Find matching no-stacked
    for r_no in no_stack_all:
        if (r_st['ticker'] == r_no['ticker'] and 
            r_st['pattern'] == r_no['pattern'] and
            r_st['hold'] == r_no['hold'] and
            r_st['sl'] == r_no['sl']):
            imp = ((r_st['calmar'] - r_no['calmar']) / r_no['calmar'] * 100) if r_no['calmar'] != 0 else 0
            wr_imp = r_st['wr'] - r_no['wr']
            print(f"    {r_st['ticker']:>8} {r_st['pattern']:>22} h={r_st['hold']}: "
                  f"no={r_no['calmar']:>5.1f}/{r_no['wr']:>4.1f}% → "
                  f"st={r_st['calmar']:>5.1f}/{r_st['wr']:>4.1f}% "
                  f"({imp:+.0f}% Calmar, {wr_imp:+.1f}% WR)")
            break

# Save
os.makedirs(OUTPUT_DIR, exist_ok=True)
with open(f'{OUTPUT_DIR}/results.json', 'w') as f:
    json.dump(results, f, indent=2)

print(f"\nSaved to {OUTPUT_DIR}/")
print(f"Total time: {time.time()-t0:.0f}s")
