#!/usr/bin/env python3
"""5m multi-strategy scanner — find best per-ticker strategy.

Tests 4 core hypotheses on each ticker with 5m data:
  1. YUR-FOLLOW: trade with YUR when |yur_z| > thresh (≡ ANTI-FIZ)
  2. VOL-SURGE: Volume Surge + YUR direction (vol_z > vt, |yur_z| > dt)
  3. YUR-DOM: trade when YUR volume >> FIZ volume
  4. FIZ-FOLLOW: trade with FIZ (benchmark — should lose)

For each: multiple thresholds, exit horizons, LONG/SHORT/both.
"""

import psycopg2, sys, math
from collections import defaultdict
import numpy as np

DB = dict(host='10.0.0.64', port=5432, dbname='moex', user='postgres', password='postgres')

# Best candidates from asymmetry scan
CANDIDATES = ['SN','UC','HY','AL','LK','RN','MX',  # YUR-dominant
              'KC','CC','BM','MC','SE','AF',          # FIZ-dominant
              'HS','DX',                               # known workers
              'BR','GD','NG','VB','PD','GAZPF']        # other interesting

THRESHOLDS = [1.5, 2.0, 2.5, 3.0]
EXITS = [3, 6, 12, 24, 48]  # 5m bars
MIN_SIGNALS = 20

def zs(vals, w=20):
    out = [0.0] * len(vals)
    for i in range(w, len(vals)):
        chunk = vals[i-w:i]
        mu = sum(chunk) / w
        var = sum((x - mu)**2 for x in chunk) / w
        sd = var ** 0.5
        out[i] = (vals[i] - mu) / sd if sd > 0 else 0.0
    return out

def calc_dd(rets):
    if not rets:
        return 0.0
    cum = 0.0; peak = 0.0; dd = 0.0
    for r in rets:
        cum += r
        if cum > peak: peak = cum
        d = peak - cum
        if d > dd: dd = d
    return dd

def load_5m_oi(symbol):
    """Load 5m OI + price data."""
    conn = psycopg2.connect(**DB)
    cur = conn.cursor()
    cur.execute("""
        SELECT oi.time, oi.fiz_buy, oi.fiz_sell, oi.yur_buy, oi.yur_sell,
               oi.total_oi, p.close, p.volume, p.open
        FROM moex_prices_5m_oi oi
        JOIN moex_prices_5m p ON p.symbol=oi.symbol AND p.time=oi.time
        WHERE oi.symbol=%s AND oi.time >= '2023-01-01'
        ORDER BY oi.time
    """, (symbol,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows

def test_strategy(rows, signal_fn, exit_horizons, label):
    """Generic strategy tester.
    signal_fn(i, fiz_z, yur_z, vol_z, fiz_vol, yur_vol) -> 'LONG', 'SHORT', or None
    """
    if len(rows) < 500:
        return []
    
    n = len(rows)
    
    # Extract data
    close = [float(r[6] or 0) for r in rows]
    volume = [float(r[7] or 0) for r in rows]
    open_px = [float(r[8] or 0) for r in rows]
    fiz_net = [float((r[1] or 0) - (r[2] or 0)) for r in rows]
    yur_net = [float((r[3] or 0) - (r[4] or 0)) for r in rows]
    fiz_vol = [float((r[1] or 0) + (r[2] or 0)) for r in rows]
    yur_vol = [float((r[3] or 0) + (r[4] or 0)) for r in rows]
    
    # Z-scores
    vol_z = zs(volume, 20)
    fiz_z = zs(fiz_net, 20)
    yur_z = zs(yur_net, 20)
    
    results = []
    max_hor = max(exit_horizons)
    
    for h in exit_horizons:
        rets = []
        for i in range(20, n - max_hor - 1):
            sig = signal_fn(i, fiz_z, yur_z, vol_z, fiz_vol, yur_vol, fiz_net, yur_net, close)
            if sig is None:
                continue
            
            entry = open_px[i + 1]
            if entry <= 0:
                continue
            
            exit_i = i + 1 + h - 1
            if exit_i >= n:
                continue
            exit_px = close[exit_i]
            if exit_px <= 0:
                continue
            
            if sig == 'LONG':
                ret = (exit_px - entry) / entry * 100.0
            else:
                ret = (entry - exit_px) / entry * 100.0
            
            rets.append(ret)
        
        if len(rets) < MIN_SIGNALS:
            continue
        
        wins = sum(1 for r in rets if r > 0)
        n_sig = len(rets)
        wr = wins / n_sig * 100.0
        gains = sum(r for r in rets if r > 0)
        losses = abs(sum(r for r in rets if r < 0))
        pf = gains / losses if losses > 0 else (99.9 if gains > 0 else 0.0)
        avg_ret = sum(rets) / n_sig
        dd = calc_dd(rets)
        
        results.append({
            'label': label,
            'horizon': h,
            'n': n_sig,
            'wr': round(wr, 1),
            'pf': round(pf, 2),
            'avg': round(avg_ret, 2),
            'dd': round(dd, 2),
            'score': round(wr * pf / 100, 1),
        })
    
    return results


def make_yur_follow(thresh):
    """Trade with YUR when |yur_z| > thresh."""
    def fn(i, fz, yz, vz, fv, yv, fn2, yn2, close):
        if abs(yz[i]) < thresh:
            return None
        return 'LONG' if yz[i] > thresh else 'SHORT'
    return fn

def make_vol_surge(vol_t, div_t):
    """Volume Surge when FIZ/YUR diverge."""
    def fn(i, fz, yz, vz, fv, yv, fn2, yn2, close):
        if vz[i] < vol_t:
            return None
        if abs(fz[i]) < div_t or abs(yz[i]) < div_t:
            return None
        if fz[i] * yz[i] >= 0:
            return None
        return 'LONG' if yz[i] > 0 else 'SHORT'
    return fn

def make_yur_dom(vol_t, dom_ratio):
    """YUR dominance: trade YUR direction when YUR volume >> FIZ AND volume spike."""
    def fn(i, fz, yz, vz, fv, yv, fn2, yn2, close):
        if vz[i] < vol_t:
            return None
        if abs(yz[i]) < 1.0:
            return None
        # YUR must have more volume than FIZ
        if yv[i] <= fv[i] * dom_ratio:
            return None
        return 'LONG' if yz[i] > 0 else 'SHORT'
    return fn

def make_fiz_follow(thresh):
    """Trade with FIZ (benchmark — should lose)."""
    def fn(i, fz, yz, vz, fv, yv, fn2, yn2, close):
        if abs(fz[i]) < thresh:
            return None
        return 'LONG' if fz[i] > thresh else 'SHORT'
    return fn


def main():
    all_results = []
    
    for sym in CANDIDATES:
        print(f"\n{'='*60}")
        print(f"📊 {sym}")
        print(f"{'='*60}")
        print("  Loading...", end=' ', flush=True)
        rows = load_5m_oi(sym)
        print(f"{len(rows)} rows")
        
        if len(rows) < 500:
            print("  ❌ Too few rows")
            continue
        
        ticker_results = []
        
        # H1: YUR-FOLLOW
        for t in THRESHOLDS:
            rs = test_strategy(rows, make_yur_follow(t), EXITS, f"YUR-FOLLOW|z≥{t}")
            ticker_results.extend(rs)
        
        # H2: Volume Surge + Divergence
        for vt in THRESHOLDS:
            for dt in [1.0, 1.5, 2.0]:
                if vt < dt: continue
                rs = test_strategy(rows, make_vol_surge(vt, dt), EXITS, f"VOL-SURGE|v≥{vt}d≥{dt}")
                ticker_results.extend(rs)
        
        # H3: YUR Dominance
        for vt in [2.0, 2.5]:
            for dom in [1.5, 2.0]:
                rs = test_strategy(rows, make_yur_dom(vt, dom), EXITS, f"YUR-DOM|v≥{vt}r>{dom}")
                ticker_results.extend(rs)
        
        # H4: FIZ-FOLLOW (benchmark)
        for t in [2.0, 2.5]:
            rs = test_strategy(rows, make_fiz_follow(t), EXITS, f"FIZ-FOLLOW|z≥{t}")
            ticker_results.extend(rs)
        
        # Filter: meaningful results
        passing = [r for r in ticker_results if r['n'] >= MIN_SIGNALS and r['wr'] >= 55.0 and r['pf'] >= 1.3 and r['dd'] <= 30.0]
        passing.sort(key=lambda x: -x['score'])
        
        if passing:
            print(f"\n  ✅ PASSING ({len(passing)} combos):")
            for r in passing[:5]:
                print(f"    {r['label']:28s} h={r['horizon']:2d} | "
                      f"n={r['n']:4d} WR={r['wr']:5.1f}% PF={r['pf']:5.2f} "
                      f"avg={r['avg']:+6.2f}% DD={r['dd']:5.1f}% "
                      f"Score={r['score']:5.1f}")
            # Save best per ticker
            best = passing[0]
            best['symbol'] = sym
            all_results.append(best)
        else:
            # Show what came closest
            best_overall = sorted(ticker_results, key=lambda x: -x['score'])[:3]
            if best_overall:
                print(f"  ❌ Best of the worst:")
                for r in best_overall:
                    print(f"    {r['label']:28s} h={r['horizon']:2d} | "
                          f"n={r['n']:4d} WR={r['wr']:5.1f}% PF={r['pf']:5.2f} "
                          f"avg={r['avg']:+6.2f}% DD={r['dd']:5.1f}%")
    
    # FINAL REPORT
    print(f"\n\n{'='*100}")
    print("ФИНАЛЬНЫЙ ОТЧЁТ: лучшая стратегия на тикер")
    print(f"{'='*100}")
    print(f"{'Тикер':>5s} | {'Стратегия':28s} | {'h':>2s} | {'n':>4s} | "
          f"{'WR%':>5s} | {'PF':>5s} | {'Avg%':>6s} | {'DD%':>5s} | {'Score':>5s}")
    print("-" * 85)
    
    all_results.sort(key=lambda x: -x['score'])
    for r in all_results:
        print(f"{r['symbol']:>5s} | {r['label']:28s} | {r['horizon']:2d} | "
              f"{r['n']:4d} | {r['wr']:>5.1f}% | {r['pf']:>5.2f} | "
              f"{r['avg']:+>5.2f}% | {r['dd']:>5.1f}% | {r['score']:>5.1f}")
    
    print(f"\nРабочих тикеров: {len(all_results)}/{len(CANDIDATES)}")

if __name__ == '__main__':
    main()
