#!/usr/bin/env python3
"""CVD divergence correlation — pandas vectorised."""
import clickhouse_connect
import pandas as pd
import numpy as np
import json, sys

ch = clickhouse_connect.get_client(host='10.0.0.64', database='moex')

BEST = {'NG':(10,3,0.8),'BR':(20,1,0.8),'Si':(10,5,0.7),'MXI':(20,1,0.6)}
overlap = {}  # ts -> set of symbols

for SYM in ['NG','BR','Si','MXI']:
    sys.stdout.write(f"\n--- {SYM} ---\n"); sys.stdout.flush()
    lk, hold, q = BEST[SYM]
    
    df = ch.query_df(f"""
        SELECT toDateTime(tradedate || ' ' || tradetime) AS ts,
               pr_close AS close, vol_b, vol_s
        FROM moex.tradestats_fo
        WHERE asset_code = '{SYM}' AND vol > 0
        ORDER BY tradedate, tradetime
    """)
    sys.stdout.write(f"  Loaded {len(df):,} bars\n"); sys.stdout.flush()
    
    df['cvd'] = df['vol_b'].fillna(0) - df['vol_s'].fillna(0)
    df['date'] = df['ts'].dt.strftime('%Y-%m-%d')
    dates = sorted(df['date'].unique())
    
    all_exit_ts = []
    
    # Walk-forward
    for i in range(180, len(dates), 60):
        if i + 60 > len(dates): break
        train_dates = set(dates[i-180:i])
        test_dates = set(dates[i:i+60])
        
        train_mask = df['date'].isin(train_dates).values
        test_mask = df['date'].isin(test_dates).values
        
        if train_mask.sum() < 200 or test_mask.sum() < 20:
            continue
        
        train = df.loc[train_mask].copy()
        test = df.loc[test_mask].copy().reset_index(drop=True)
        
        train['cvd_cum'] = train['cvd'].cumsum()
        train['pchg'] = train['close'].diff(lk)
        train['cchg'] = train['cvd_cum'].diff(lk)
        train_v = train.dropna()
        
        if len(train_v) < 100: continue
        
        p_thr = train_v['pchg'].abs().quantile(q)
        c_thr = train_v['cchg'].abs().quantile(q)
        if p_thr == 0 or c_thr == 0: continue
        
        last_cvd = train['cvd_cum'].iloc[-1]
        test['cvd_cum'] = last_cvd + test['cvd'].cumsum()
        test['pchg'] = test['close'].diff(lk)
        test['cchg'] = test['cvd_cum'].diff(lk)
        test_v = test.dropna()
        
        if len(test_v) < 20: continue
        
        bearish = (test_v['pchg'] > p_thr) & (test_v['cchg'] < -c_thr)
        bullish = (test_v['pchg'] < -p_thr) & (test_v['cchg'] > c_thr)
        sig_idx = bearish | bullish
        
        if sig_idx.any():
            # Для каждого сигнала — bar закрытия (entry + hold)
            sig_times = test_v.index[sig_idx]
            for idx in sig_times:
                pos = test_v.index.get_loc(idx)
                exit_pos = min(pos + hold - 1, len(test_v) - 1)
                exit_ts = test_v.iloc[exit_pos]['ts']
                all_exit_ts.append(exit_ts)
    
    sys.stdout.write(f"  Signals: {len(all_exit_ts):,}\n"); sys.stdout.flush()
    
    for t in all_exit_ts:
        key = str(t)
        if key not in overlap:
            overlap[key] = set()
        overlap[key].add(SYM)

sys.stdout.write(f"\nTotal signal moments: {len(overlap):,}\n")
total = len(overlap)
one = sum(1 for v in overlap.values() if len(v)==1)
two = sum(1 for v in overlap.values() if len(v)==2)
three = sum(1 for v in overlap.values() if len(v)==3)
four = sum(1 for v in overlap.values() if len(v)==4)

sys.stdout.write(f"1 symbol:  {one:,} ({100*one/total:.1f}%)\n")
sys.stdout.write(f"2 symbols: {two:,} ({100*two/total:.1f}%)\n")
sys.stdout.write(f"3 symbols: {three:,} ({100*three/total:.1f}%)\n")
sys.stdout.write(f"4 symbols: {four:,} ({100*four/total:.1f}%)\n")

from collections import Counter
pair_counts = Counter()
for v in overlap.values():
    if len(v) >= 2:
        sl = sorted(v)
        for i in range(len(sl)):
            for j in range(i+1, len(sl)):
                pair_counts[(sl[i], sl[j])] += 1

for (a,b),c in pair_counts.most_common(10):
    sys.stdout.write(f"  {a}+{b}: {c:,} ({100*c/total:.1f}%)\n")

result = {
    'total': total,
    'single': one, 'two': two, 'three': three, 'four': four,
    'pct_1': round(100*one/total,1), 'pct_2': round(100*two/total,1),
    'pct_3': round(100*three/total,1), 'pct_4': round(100*four/total,1),
    'top_pairs': {f'{a}+{b}': c for (a,b),c in pair_counts.most_common(10)}
}

with open('reports/wf_divergence_correlation.json','w') as f:
    json.dump(result, f, indent=2)
sys.stdout.write(f"\nSaved: reports/wf_divergence_correlation.json\nDone.\n")
