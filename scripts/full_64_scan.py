#!/usr/bin/env python3
"""
FULL scan: ALL 64 tickers, Volume × OI yur_accumulation.
Best PD params: vz=3.0, yz=1.5, ATR≤0.75%, exit yur_z<0.5, hold=24, flat 1c.

Outputs DETAILED report for tickers that PASS (all 4 folds PnL>0),
including trimmed PnL (without best trade per fold) and PnL distribution.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta
import clickhouse_connect
import pandas as pd
import numpy as np
from config import CH_HOST, CH_PORT, CH_DB

ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)

TICKERS = ['AF','AL','AU','BM','BR','CC','CE','CH','CNYRUBF','CR','DX','ED','Eu','EURRUBF',
           'FF','GAZPF','GD','GK','GL','GLDRUBF','GZ','HS','HY','IB','IMOEXF','KC','LK',
           'MC','ME','MG','MM','MN','MX','MY','NA','NG','NM','NR','OJ','PD','PT','RB',
           'RI','RL','RM','RN','SBERF','SE','SF','Si','SN','SP','SR','SS','SV','TN',
           'TT','UC','USDRUBF','VB','VI','W4','X5','YD']

DAYS = 730
since = (datetime.now() - timedelta(days=DAYS)).strftime('%Y-%m-%d')

def zs(s,w=20):
    return ((s - s.rolling(w,min_periods=10).mean()) / s.rolling(w,min_periods=10).std().replace(0,1)).fillna(0)

# Store all results
all_results = []
passed = []

for ticker in TICKERS:
    rows = ch.query("""
        SELECT p.time, p.open, p.high, p.low, p.close, p.volume,
               o.fiz_buy, o.fiz_sell, o.yur_buy, o.yur_sell, o.total_oi
        FROM moex.prices_5m AS p
        INNER JOIN moex.prices_5m_oi AS o ON o.symbol = p.symbol AND o.time = p.time
        WHERE p.symbol = %(t)s AND p.time >= %(s)s AND p.volume > 0 AND o.total_oi > 0
        ORDER BY p.time
    """, parameters={'t': ticker, 's': since}).result_rows
    
    if not rows or len(rows) < 500:
        continue
    
    df = pd.DataFrame(rows, columns=['time','open','high','low','close','volume',
        'fiz_buy','fiz_sell','yur_buy','yur_sell','total_oi'])
    df['fiz_net'] = df['fiz_buy'] - df['fiz_sell']
    df['yur_net'] = df['yur_buy'] - df['yur_sell']
    df['vol_z'] = zs(df['volume'])
    df['fiz_z'] = zs(df['fiz_net'])
    df['yur_z'] = zs(df['yur_net'])
    tr = pd.concat([df['high']-df['low'], (df['high']-df['close'].shift(1)).abs(), (df['low']-df['close'].shift(1)).abs()], axis=1).max(axis=1)
    df['atr_pct'] = tr.ewm(span=14).mean() / df['close'] * 100
    
    n = len(df)
    mask = (df['vol_z'] > 3.0) & (df['yur_z'] > 1.5) & (df['fiz_z'] < 0) & (df['atr_pct'] <= 0.75)
    sigs = df[mask].index.tolist()
    
    if len(sigs) < 10:
        continue
    
    cuts = [n//4, n//2, 3*n//4]
    folds = [(0, cuts[0]), (cuts[0], cuts[1]), (cuts[1], cuts[2]), (cuts[2], n)]
    
    fold_data = []
    all_trade_pnls = []
    total_abs = 0  # sum of |pnl|
    
    for lo, hi in folds:
        f_sigs = [i for i in sigs if lo <= i < hi and i+1 < n]
        if len(f_sigs) < 3:
            fold_data.append(None)
            continue
        
        pnls = []
        for idx in f_sigs:
            entry = float(df.iloc[idx+1]['open'])
            if entry <= 0: continue
            for off in range(1, min(25, n-idx-1)):
                if df.iloc[idx+off]['yur_z'] < 0.5: break
            eidx = idx + off
            if eidx >= n: continue
            ep = float(df.iloc[eidx]['close'])
            stop = entry * 0.98
            for j in range(idx+1, eidx+1):
                if float(df.iloc[j]['low']) <= stop: ep = stop; break
            pnl = (ep - entry) / 0.01 - 2
            pnls.append(pnl)
            all_trade_pnls.append(pnl)
            total_abs += abs(pnl)
        
        pnls_sorted = sorted(pnls)
        fold_data.append({
            'n': len(pnls),
            'total': sum(pnls),
            'wr': sum(1 for p in pnls if p > 0) / len(pnls) * 100,
            'min': pnls_sorted[0],
            'max': pnls_sorted[-1],
            'median': np.median(pnls),
            'mean': np.mean(pnls),
            'trimmed': sum(pnls) - max(pnls),  # without best trade
            'q1': np.percentile(pnls, 25),
            'q3': np.percentile(pnls, 75),
        })
    
    all_pnls_sorted = sorted(all_trade_pnls)
    total_pnl = sum(all_trade_pnls)
    total_wr = sum(1 for p in all_trade_pnls if p > 0) / len(all_trade_pnls) * 100 if all_trade_pnls else 0
    total_trimmed = sum(all_trade_pnls) - max(all_trade_pnls) if len(all_trade_pnls) > 1 else 0
    # Trimmed 2 best
    total_trimmed2 = sum(sorted(all_trade_pnls)[:-2]) if len(all_trade_pnls) > 2 else 0
    # Trimmed 3 best
    total_trimmed3 = sum(sorted(all_trade_pnls)[:-3]) if len(all_trade_pnls) > 3 else 0
    
    all_pos = all(fd and fd['total'] > 0 for fd in fold_data if fd)
    all_pos_trimmed = all(fd and fd['trimmed'] > 0 for fd in fold_data if fd)
    
    result = {
        'ticker': ticker,
        'bars': len(df),
        'sigs': len(sigs),
        'folds': fold_data,
        'total_pnl': total_pnl,
        'total_wr': total_wr,
        'total_trimmed': total_trimmed,
        'total_trimmed2': total_trimmed2,
        'total_trimmed3': total_trimmed3,
        'all_pos': all_pos,
        'all_pos_trimmed': all_pos_trimmed,
        'total_abs': total_abs,
        'price_median': df['close'].median(),
        'all_pnls': all_trade_pnls,
    }
    all_results.append(result)
    
    if all_pos:
        passed.append(result)

print(f"Total tickers with ≥10 signals: {len(all_results)}")
print(f"Tickers with ALL 4 folds > 0:    {len(passed)}")
print()

# Sort by total PnL descending
passed_sorted = sorted(passed, key=lambda x: -x['total_pnl'])

print(f"{'Ticker':>10} {'Price':>8} {'Sigs':>5} {'PnL':>10} {'Trim':>10} {'T2':>10} {'T3':>10} {'WR':>5} {'Abs':>10} {'F1':>10} {'F2':>10} {'F3':>10} {'F4':>10} {'F1tr':>10} {'F2tr':>10} {'F3tr':>10} {'F4tr':>10}")
print("="*180)
for r in passed_sorted:
    f = r['folds']
    print(f"{r['ticker']:>10} {r['price_median']:>8.0f} {r['sigs']:>5} {r['total_pnl']:>10.0f} {r['total_trimmed']:>10.0f} {r['total_trimmed2']:>10.0f} {r['total_trimmed3']:>10.0f} {r['total_wr']:>5.0f} {r['total_abs']:>10.0f} "
          f"{f[0]['total']:>10.0f} {f[1]['total']:>10.0f} {f[2]['total']:>10.0f} {f[3]['total']:>10.0f} "
          f"{f[0]['trimmed']:>10.0f} {f[1]['trimmed']:>10.0f} {f[2]['trimmed']:>10.0f} {f[3]['trimmed']:>10.0f}")

print()
print("DETAILED — tickers with all folds positive AND trimmed > 0 in ALL folds:")
print(f"{'Ticker':>10} {'F1':>12} {'F2':>12} {'F3':>12} {'F4':>12} {'F1wr':>6} {'F2wr':>6} {'F3wr':>6} {'F4wr':>6} {'F1q1':>8} {'F1med':>8} {'F1q3':>8}")
print("="*110)
for r in passed_sorted:
    f = r['folds']
    # Check if all folds trimmed > 0
    if all(fd['trimmed'] > 0 for fd in f):
        print(f"{r['ticker']:>10} {f[0]['trimmed']:>12.0f} {f[1]['trimmed']:>12.0f} {f[2]['trimmed']:>12.0f} {f[3]['trimmed']:>12.0f} "
              f"{f[0]['wr']:>6.0f} {f[1]['wr']:>6.0f} {f[2]['wr']:>6.0f} {f[3]['wr']:>6.0f} "
              f"{f[0]['q1']:>8.0f} {f[0]['median']:>8.0f} {f[0]['q3']:>8.0f}")

# Show those that pass trimmed check
strict_pass = [r for r in passed_sorted if all(fd['trimmed'] > 0 for fd in r['folds'])]
print(f"\nStrict pass (all folds positive even without best trade): {len(strict_pass)}")
for r in strict_pass:
    print(f"  ✅ {r['ticker']}: sigs={r['sigs']} PnL={r['total_pnl']:.0f} (trimmed={r['total_trimmed']:.0f}) WR={r['total_wr']:.0f}%")

# Save report
OUT = 'reports/full_scan_64'
os.makedirs(OUT, exist_ok=True)
with open(f'{OUT}/report.txt', 'w') as f:
    f.write(f"Total tickers with ≥10 signals: {len(all_results)}\n")
    f.write(f"Tickers with ALL 4 folds > 0: {len(passed)}\n")
    f.write(f"Strict pass (all folds trimmed>0): {len(strict_pass)}\n\n")
    for r in strict_pass:
        f.write(f"✅ {r['ticker']}: sigs={r['sigs']} total={r['total_pnl']:.0f} trim={r['total_trimmed']:.0f} WR={r['total_wr']:.0f}%\n")
    for r in passed_sorted:
        if r not in strict_pass:
            f.write(f"⚠️ {r['ticker']}: sigs={r['sigs']} total={r['total_pnl']:.0f} trim={r['total_trimmed']:.0f} WR={r['total_wr']:.0f}\n")

print(f"\nReport saved: {OUT}/report.txt")
