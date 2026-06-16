#!/usr/bin/env python3
"""Scan ALL tickers with OI for Volume × OI yur_accumulation.
Tests best PD params: vz=3.0, yz=1.5, ATR≤0.75%, exit yur_z<0.5, hold=24, flat 1c.

Output: for each ticker with ≥10 signals, 4-fold WF. PASS = all folds PnL > 0.
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

# All 64 tickers from the dashboard
TICKERS = ['AF','AL','AU','BM','BR','CC','CE','CH','CNYRUBF','CR','DX','ED','Eu','EURRUBF',
           'FF','GAZPF','GD','GK','GL','GLDRUBF','GZ','HS','HY','IB','IMOEXF','KC','LK',
           'MC','ME','MG','MM','MN','MX','MY','NA','NG','NM','NR','OJ','PD','PT','RB',
           'RI','RL','RM','RN','SBERF','SE','SF','Si','SN','SP','SR','SS','SV','TN',
           'TT','UC','USDRUBF','VB','VI','W4','X5','YD']

DAYS = 730
since = (datetime.now() - timedelta(days=DAYS)).strftime('%Y-%m-%d')

def zs(s,w=20):
    return ((s - s.rolling(w,min_periods=10).mean()) / s.rolling(w,min_periods=10).std().replace(0,1)).fillna(0)

results = []
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
        print(f"{ticker}: too few bars ({len(rows) if rows else 0})")
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
        print(f"{ticker}: only {len(sigs)} signals")
        continue
    
    # 4-fold WF
    cuts = [n//4, n//2, 3*n//4]
    folds = [(0, cuts[0]), (cuts[0], cuts[1]), (cuts[1], cuts[2]), (cuts[2], n)]
    
    fold_pnls = []
    fold_wrs = []
    total_sigs = 0
    for lo, hi in folds:
        f_sigs = [i for i in sigs if lo <= i < hi and i+1 < n]
        if len(f_sigs) < 3:
            fold_pnls.append(None)
            fold_wrs.append(None)
            continue
        total_sigs += len(f_sigs)
        pnl = 0
        wins = 0
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
            pnl += (ep - entry) / 0.01 - 2
            if pnl > 0: wins += 1
        fold_pnls.append(pnl)
        fold_wrs.append(wins/len(f_sigs)*100 if f_sigs else 0)
    
    all_pos = all(p and p > 0 for p in fold_pnls)
    total = sum(p for p in fold_pnls if p)
    
    # Compute total WR
    all_pnls = []
    for lo, hi in folds:
        f_sigs = [i for i in sigs if lo <= i < hi and i+1 < n]
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
            all_pnls.append((ep - entry) / 0.01 - 2)
    
    total_wr = sum(1 for p in all_pnls if p > 0) / len(all_pnls) * 100 if all_pnls else 0
    # Trim best trade check
    trimmed = sum(sorted(all_pnls)[:-1]) if len(all_pnls) > 1 else 0
    
    status = '✅' if all_pos else '⚠️' if total > 0 else '❌'
    if all_pos or total > 5000:
        print(f"{status} {ticker}: sigs={len(sigs)} WFs={','.join(f'{p:.0f}' if p else 'x' for p in fold_pnls)} WR={total_wr:.0f}% total={total:.0f} trim={trimmed:.0f}")
    else:
        print(f"  {ticker}: {len(sigs)} sigs, total={total:.0f}")
    
    if all_pos:
        results.append((ticker, len(sigs), fold_pnls, total_wr, total, trimmed))

print(f"\n{'='*60}")
print("RESULTS — TICKERS THAT PASSED ALL 4 FOLDS:")
print(f"{'='*60}")
if results:
    for tk, ns, fp, wr, tot, trim in sorted(results, key=lambda x: -x[4]):
        print(f"  ✅ {tk:>8}: sigs={ns:>3} WR={wr:.0f}% PnL={tot:>8.0f} (trim={trim:.0f}) folds={','.join(f'{p:.0f}' for p in fp)}")
else:
    print("  None.")
