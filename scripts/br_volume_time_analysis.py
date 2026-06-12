#!/usr/bin/env python3
"""
Time analysis of BR volume spikes on M5.
Loads data from ClickHouse -> computes volume z-score -> finds spikes -> analyzes time distribution.

Usage: python3 scripts/br_volume_time_analysis.py
Output: reports/br_volume_time_analysis/ with report + charts
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta
from collections import Counter, defaultdict
import clickhouse_connect
import pandas as pd
import numpy as np
from config import CH_HOST, CH_PORT, CH_DB

OUT = 'reports/br_volume_time_analysis'
os.makedirs(OUT, exist_ok=True)

# ── 1. Load data ──────────────────────────────────────────────
ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)
DAYS = 365 * 3  # 3 years
since = (datetime.now() - timedelta(days=DAYS)).strftime('%Y-%m-%d')

print(f"[1] Loading BR M5 bars since {since}...")
rows = ch.query("""
    SELECT time, open, high, low, close, volume
    FROM moex.prices_5m
    WHERE symbol = 'BR' AND time >= %(s)s AND volume > 0
    ORDER BY time
""", parameters={'s': since}).result_rows

df = pd.DataFrame(rows, columns=['time','open','high','low','close','volume'])
print(f"  Loaded {len(df)} bars from {df['time'].min()} to {df['time'].max()}")

# ── 2. Volume z-score ─────────────────────────────────────────
def rolling_zs(vals, w=20):
    s = pd.Series(vals)
    mu = s.rolling(w).mean()
    sd = s.rolling(w).std()
    return ((s - mu) / sd).fillna(0).values

print("[2] Computing volume z-scores...")
df['vol_z'] = rolling_zs(df['volume'].values, w=20)
df['vol_z40'] = rolling_zs(df['volume'].values, w=40)
df['vol_z60'] = rolling_zs(df['volume'].values, w=60)

# ── 3. Spike detection ────────────────────────────────────────
print("[3] Detecting spikes...")
spikes_2sig = df[df['vol_z'] > 2.0].copy()
spikes_3sig = df[df['vol_z'] > 3.0].copy()
spikes_4sig = df[df['vol_z'] > 4.0].copy()

print(f"  >2σ: {len(spikes_2sig)} bars ({len(spikes_2sig)/len(df)*100:.1f}%)")
print(f"  >3σ: {len(spikes_3sig)} bars ({len(spikes_3sig)/len(df)*100:.1f}%)")
print(f"  >4σ: {len(spikes_4sig)} bars ({len(spikes_4sig)/len(df)*100:.1f}%)")

# ── 4. Time distribution ──────────────────────────────────────
print("[4] Analyzing time distribution...")
df_t = spikes_3sig.copy()
df_t['hour'] = df_t['time'].dt.hour
df_t['dow'] = df_t['time'].dt.dayofweek  # Mon=0
df_t['month'] = df_t['time'].dt.month
df_t['dow_name'] = df_t['time'].dt.day_name()

# Per hour
hour_counts = df_t['hour'].value_counts().sort_index()
print("\n  Spikes by hour (MSK):")
for h in range(24):
    c = hour_counts.get(h, 0)
    bar = '█' * (c // 5)
    print(f"  {h:02d}:00 {bar} {c}")

# Per day of week
dow_map = {0:'Mon',1:'Tue',2:'Wed',3:'Thu',4:'Fri',5:'Sat',6:'Sun'}
dow_ct = df_t['dow'].value_counts().sort_index()
print("\n  Spikes by day of week:")
for d in range(7):
    c = dow_ct.get(d, 0)
    bar = '█' * (c // 10)
    print(f"  {dow_map[d]}: {bar} {c}")

# Trading sessions (MSK)
def session(h):
    if 3 <= h < 10: return 'Asia'
    elif 10 <= h < 16: return 'Europe'
    elif 16 <= h < 24: return 'America'
    else: return 'Off-hours'

df_t['session'] = df_t['hour'].apply(session)
sess_ct = df_t['session'].value_counts()
print("\n  By session:")
for s in ['Asia','Europe','America','Off-hours']:
    c = sess_ct.get(s, 0)
    pct = c / len(df_t) * 100
    bar = '█' * (c // 5)
    print(f"  {s:>10}: {bar} {c} ({pct:.0f}%)")

# ── 5. Price movement after spike ────────────────────────────
print("\n[5] Price movement after spike (next bar)...")
df['next_return'] = df['close'].pct_change(1).shift(-1) * 100
df['next_3bar_return'] = df['close'].pct_change(3).shift(-3) * 100

# Overall stats
print(f"\n  Volume vs next bar return correlation: {df['volume'].corr(df['next_return']):.4f}")

# Buckets by volume z-score
buckets = [(-np.inf, 0), (0, 1), (1, 2), (2, 3), (3, np.inf)]
labels = ['below_avg', '0-1σ', '1-2σ', '2-3σ', '>3σ']
for lo, hi, lab in zip([-np.inf, 0, 1, 2, 3], [0, 1, 2, 3, np.inf], labels):
    mask = (df['vol_z'] >= lo) & (df['vol_z'] < hi)
    subset = df[mask]
    if len(subset) == 0:
        continue
    avg_ret_1 = subset['next_return'].mean()
    avg_ret_3 = subset['next_3bar_return'].mean()
    pos_1 = (subset['next_return'] > 0).mean() * 100
    pos_3 = (subset['next_3bar_return'] > 0).mean() * 100
    print(f"  {lab:>10}: n={len(subset):>6} ret1={avg_ret_1:>+6.3f}% ret3={avg_ret_3:>+6.3f}% pos1={pos_1:>5.1f}% pos3={pos_3:>5.1f}%")

# ── 6. Spike clusters (multiple spikes in short time) ────────
print("\n[6] Spike clustering (bars between consecutive >2σ spikes)...")
spike_times = df[df['vol_z'] > 2.0]['time'].values
spike_t0 = pd.Timestamp(spike_times[0])
gaps = np.diff([(pd.Timestamp(t) - spike_t0).total_seconds() / 60 for t in spike_times])
cluster_bars = [5, 15, 30, 60, 120]
for cb in [5, 15, 30, 60, 120]:
    cluster_ct = sum(1 for g in gaps if g <= cb)
    print(f"  {cb:>3}min gap: {cluster_ct} cluster pairs")

# ── 7. Save report ───────────────────────────────────────────
print(f"\n[7] Saving report to {OUT}/...")
report = {
    'ticker': 'BR',
    'period': f"{df['time'].min()} to {df['time'].max()}",
    'total_bars': len(df),
    'days_covered': (df['time'].max() - df['time'].min()).days,
    'spikes_2sig': int(len(spikes_2sig)),
    'spikes_3sig': int(len(spikes_3sig)),
    'spikes_4sig': int(len(spikes_4sig)),
    'spike_3sig_pct': round(len(spikes_3sig)/len(df)*100, 2),
    'peak_volume': float(df['volume'].max()),
    'median_volume': float(df['volume'].median()),
    'mean_volume': float(df['volume'].mean()),
    'hour_distribution': {str(h): int(hour_counts.get(h, 0)) for h in range(24)},
    'dow_distribution': {dow_map[d]: int(dow_ct.get(d, 0)) for d in range(7)},
    'session_distribution': {s: int(sess_ct.get(s, 0)) for s in ['Asia','Europe','America','Off-hours']},
    'vol_corr_ret': float(df['volume'].corr(df['next_return'])),
    'bucket_stats': [],
}

for lo, hi, lab in zip([-np.inf, 0, 1, 2, 3], [0, 1, 2, 3, np.inf], labels):
    mask = (df['vol_z'] >= lo) & (df['vol_z'] < hi)
    subset = df[mask]
    if len(subset) == 0:
        continue
    report['bucket_stats'].append({
        'bucket': lab,
        'n': len(subset),
        'mean_ret_1bar': float(subset['next_return'].mean()),
        'mean_ret_3bar': float(subset['next_3bar_return'].mean()),
        'winrate_1bar': float((subset['next_return'] > 0).mean()),
        'winrate_3bar': float((subset['next_3bar_return'] > 0).mean()),
    })

with open(f'{OUT}/report.json', 'w') as f:
    json.dump(report, f, indent=2, default=str)

# ── 8. Summary text ──────────────────────────────────────────
summary = f"""
# BR Volume Time Analysis

**Period:** {df['time'].min().strftime('%Y-%m-%d')} → {df['time'].max().strftime('%Y-%m-%d')} ({ (df['time'].max() - df['time'].min()).days } days)
**Total M5 bars:** {len(df):,}
**Median volume:** {df['volume'].median():.0f}
**Peak volume:** {df['volume'].max():.0f}

## Volume Spikes (z-score 20-bar window)

| Threshold | Count | % of bars |
|-----------|-------|-----------|
| >2σ | {len(spikes_2sig)} | {len(spikes_2sig)/len(df)*100:.2f}% |
| >3σ | {len(spikes_3sig)} | {len(spikes_3sig)/len(df)*100:.2f}% |
| >4σ | {len(spikes_4sig)} | {len(spikes_4sig)/len(df)*100:.2f}% |

## Spike Distribution by Session (MSK)

| Session | Hours | Count | % |
|---------|-------|-------|---|
| Asia | 03-09 | {sess_ct.get('Asia',0)} | {sess_ct.get('Asia',0)/len(df_t)*100:.0f}% |
| Europe | 10-15 | {sess_ct.get('Europe',0)} | {sess_ct.get('Europe',0)/len(df_t)*100:.0f}% |
| America | 16-23 | {sess_ct.get('America',0)} | {sess_ct.get('America',0)/len(df_t)*100:.0f}% |
| Off-hours | 00-02 | {sess_ct.get('Off-hours',0)} | {sess_ct.get('Off-hours',0)/len(df_t)*100:.0f}% |

## Price Impact by Volume Regime

| Volume regime | Bars | Avg next ret% | Avg 3-bar ret% | Win% 1bar | Win% 3bar |
|--------------|------|--------------|----------------|-----------|-----------|
"""
for b in report['bucket_stats']:
    summary += f"| {b['bucket']:>10} | {b['n']:>6} | {b['mean_ret_1bar']:>+8.4f} | {b['mean_ret_3bar']:>+8.4f} | {b['winrate_1bar']*100:>5.1f}% | {b['winrate_3bar']*100:>5.1f}% |\n"

summary += f"""
## Key Findings
1. Volume-volatility correlation: {report['vol_corr_ret']:.4f}
2. Prime spike hours: {dict(df_t['hour'].value_counts().head(3).to_dict())}
3. Most active day: {dict(df_t['dow_name'].value_counts().head(1).to_dict())}
"""

with open(f'{OUT}/summary.md', 'w') as f:
    f.write(summary)

print(summary)
print(f"\nReport saved: {OUT}/summary.md")
"""
Скрипт для разового анализа объёмов BR. Просто запусти его:
python3 scripts/br_volume_time_analysis.py

Он сам подключится к ClickHouse (config.py), вычислит z-score volumes на M5, 
найдёт всплески, построит распределение по часам/дням/сессиям, 
проверит связь с движением цены.
"""
