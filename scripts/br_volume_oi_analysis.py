#!/usr/bin/env python3
"""
Time analysis of BR volume spikes on M5 + OI behaviour.
Loads OHLCV from ClickHouse, OI from ClickHouse (moex.prices_5m_oi),
finds volume spikes, analyzes:
  - time distribution (hour/dow/session)
  - OI composition on spike bars (who pushes volume — fiz or yur?)
  - OI change direction after spike (confirmation or fade)
  - price impact

Usage: python3 scripts/br_volume_oi_analysis.py
Output: reports/br_volume_oi_analysis/summary.md
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

OUT = 'reports/br_volume_oi_analysis'
os.makedirs(OUT, exist_ok=True)

ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)
DAYS = 365 * 3  # 3 years
since = (datetime.now() - timedelta(days=DAYS)).strftime('%Y-%m-%d')

print(f"[1] Loading BR M5 bars + OI since {since}...")
rows = ch.query("""
    SELECT p.time, p.open, p.high, p.low, p.close, p.volume,
           o.fiz_buy, o.fiz_sell, o.yur_buy, o.yur_sell, o.total_oi
    FROM moex.prices_5m AS p
    INNER JOIN moex.prices_5m_oi AS o ON o.symbol = p.symbol AND o.time = p.time
    WHERE p.symbol = 'BR' AND p.time >= %(s)s AND p.volume > 0 AND o.total_oi > 0
    ORDER BY p.time
""", parameters={'s': since}).result_rows

df = pd.DataFrame(rows, columns=[
    'time','open','high','low','close','volume',
    'fiz_buy','fiz_sell','yur_buy','yur_sell','total_oi'
])
print(f"  Loaded {len(df)} bars from {df['time'].min()} to {df['time'].max()}")

# Compute derived OI fields
df['fiz_net'] = df['fiz_buy'] - df['fiz_sell']
df['yur_net'] = df['yur_buy'] - df['yur_sell']
df['fiz_share'] = (df['fiz_buy'] + df['fiz_sell']) / df['total_oi'] * 100
df['d_oi'] = df['total_oi'].diff()  # change in total OI from prev bar
df['d_fiz_net'] = df['fiz_net'].diff()
df['d_yur_net'] = df['yur_net'].diff()

# ── 2. Volume z-score ─────────────────────────────────────────
def rolling_zs(vals, w=20):
    s = pd.Series(vals)
    mu = s.rolling(w).mean()
    sd = s.rolling(w).std()
    return ((s - mu) / sd).fillna(0).values

print("[2] Computing volume z-scores...")
df['vol_z'] = rolling_zs(df['volume'].values, w=20)
df['vol_z40'] = rolling_zs(df['volume'].values, w=40)

# ── 3. Spike detection ────────────────────────────────────────
print("[3] Detecting spikes (>3σ)...")
spikes = df[df['vol_z'] > 3.0].copy()
print(f"  {len(spikes)} spike bars ({len(spikes)/len(df)*100:.2f}%)")

# ── 4. Time distribution ──────────────────────────────────────
print("[4] Time distribution...")
spikes['hour'] = spikes['time'].dt.hour
spikes['dow'] = spikes['time'].dt.dayofweek
sess_map = {'Asia': (3,10), 'Europe': (10,16), 'America': (16,24), 'Off': (0,3)}
def session(h):
    for name, (lo, hi) in sess_map.items():
        if lo <= h < hi: return name
    return 'Off'

spike_sessions = Counter(spikes['hour'].apply(session))
spike_dow = Counter(spikes['dow'].map({0:'Mon',1:'Tue',2:'Wed',3:'Thu',4:'Fri',5:'Sat',6:'Sun'}))
spike_hours = Counter(spikes['hour'])

print("\n  Spikes by session:")
for s in ['Europe','Asia','America','Off']:
    c = spike_sessions.get(s, 0)
    print(f"    {s:>8}: {c} ({c/len(spikes)*100:.0f}%)")
print("\n  Spikes by DOW:")
for d in ['Mon','Tue','Wed','Thu','Fri','Sat','Sun']:
    c = spike_dow.get(d, 0)
    print(f"    {d}: {c}")

# ── 5. ✓ OI ANALYSIS ── What happens on spike bars ──────────
print("\n[5] ★ OI ANALYSIS on spike bars...")

# 5a — who provides the volume? fiz_share distribution
avg_fiz_share_all = df['fiz_share'].mean()
avg_fiz_share_spike = spikes['fiz_share'].mean()
print(f"\n  Average fiz_share (all bars): {avg_fiz_share_all:.1f}%")
print(f"  Average fiz_share (spike):    {avg_fiz_share_spike:.1f}%")

# Buckets of fiz_share on spikes
for lo, hi, lab in [(0,30,'Yur>70%'), (30,50,'Yur>50%'), (50,70,'Fiz>50%'), (70,101,'Fiz>70%')]:
    subset = spikes[(spikes['fiz_share'] >= lo) & (spikes['fiz_share'] < hi)]
    if len(subset) > 0:
        print(f"    {lab}: {len(subset)} spikes ({len(subset)/len(spikes)*100:.0f}%)")

# 5b — which direction spike bars typically are (fiz_net sign on spike)
fiz_positive_on_spike = (spikes['fiz_net'] > 0).mean() * 100
yur_positive_on_spike = (spikes['yur_net'] > 0).mean() * 100
print(f"\n  Spike bars with fiz_net>0 (phys long): {fiz_positive_on_spike:.0f}%")
print(f"  Spike bars with yur_net>0 (yur long):  {yur_positive_on_spike:.0f}%")

# 5c — OI change AFTER spike (next 1-6 bars)
print("\n  OI change direction after spike: spike bar → next bars...")
W = 6
results = []
for offset in range(1, W+1):
    col_oi = df['total_oi'].diff(offset).shift(-offset)
    col_fn = df['fiz_net'].diff(offset).shift(-offset)
    col_yn = df['yur_net'].diff(offset).shift(-offset)
    
    aligned = spikes.copy()
    spike_idx = spikes.index
    aligned['d_oi_fwd'] = col_oi.loc[spike_idx].values if spike_idx[-1] + offset < len(df) else np.nan
    aligned['d_fiz_fwd'] = col_fn.loc[spike_idx].values if spike_idx[-1] + offset < len(df) else np.nan
    aligned['d_yur_fwd'] = col_yn.loc[spike_idx].values if spike_idx[-1] + offset < len(df) else np.nan
    
    aligned = aligned.dropna(subset=['d_oi_fwd'])
    if len(aligned) < 5:
        continue
    
    oi_increase = (aligned['d_oi_fwd'] > aligned['d_oi_fwd'].median()).mean() * 100
    fiz_increase = (aligned['d_fiz_fwd'] > 0).mean() * 100
    yur_increase = (aligned['d_yur_fwd'] > 0).mean() * 100
    
    print(f"    {offset}bar ahead: OI>median={oi_increase:.0f}% fiz_net_up={fiz_increase:.0f}% yur_net_up={yur_increase:.0f}%")

# 5d — classify spike bars by type:
#   Yur accumulation (yur_net spikes up + volume spike)
#   Fiz panic (fiz_net spikes up sharply + volume spike)
#   Neutral (mixed)
print("\n  Spike classification by OI composition:")
spikes['fiz_z'] = (spikes['fiz_net'] - df['fiz_net'].rolling(20).mean()) / df['fiz_net'].rolling(20).std().replace(0, 1)
spikes['yur_z'] = (spikes['yur_net'] - df['yur_net'].rolling(20).mean()) / df['yur_net'].rolling(20).std().replace(0, 1)

spikes['spike_type'] = 'mixed'
spikes.loc[(spikes['yur_z'] > 1.5) & (spikes['fiz_z'] < 0), 'spike_type'] = 'yur_accumulation'
spikes.loc[(spikes['fiz_z'] > 1.5) & (spikes['yur_z'] < 0), 'spike_type'] = 'fiz_panic'
spikes.loc[(spikes['fiz_z'] > 1.5) & (spikes['yur_z'] > 1.5), 'spike_type'] = 'both_accumulation'
spikes.loc[(spikes['fiz_z'] < -1.5) & (spikes['yur_z'] < -1.5), 'spike_type'] = 'both_liquidation'

stype_ct = Counter(spikes['spike_type'])
for st in ['yur_accumulation','fiz_panic','both_accumulation','both_liquidation','mixed']:
    c = stype_ct.get(st, 0)
    print(f"    {st:>20}: {c} ({c/len(spikes)*100:.0f}%)")

# 5e — price outcome after each spike type
print("\n  Price outcome by spike type (next 3 bars):")
df['ret_3fwd'] = df['close'].pct_change(3).shift(-3) * 100
for st in ['yur_accumulation','fiz_panic','both_accumulation','both_liquidation','mixed']:
    idx = spikes[spikes['spike_type'] == st].index
    idx = idx[idx + 3 < len(df)]
    if len(idx) == 0: continue
    rets = df['ret_3fwd'].iloc[idx]
    avg = rets.mean()
    wr = (rets > 0).mean() * 100
    print(f"    {st:>20}: avg_ret={avg:>+7.3f}% WR={wr:.0f}% (n={len(idx)})")

# ── 6. Price impact summary (quick) ──────────────────────────
print("\n[6] Price impact by volume regime:")
for lab, th in [('0-1σ', 1), ('1-2σ', 2), ('2-3σ', 3), ('>3σ', 99)]:
    if th < 99:
        mask = (df['vol_z'].abs() >= (th-1 if th>1 else 0)) & (df['vol_z'].abs() < th)
    else:
        mask = df['vol_z'].abs() >= 3
    s = df[mask]
    print(f"    {lab:>6}: n={len(s):>6} avg_vol={s['volume'].mean():>7.0f}")

# ── 7. Save ──────────────────────────────────────────────────
print(f"\n[7] Saving to {OUT}/...")
report = {
    'ticker': 'BR',
    'period': f"{df['time'].min()} to {df['time'].max()}",
    'total_bars': len(df),
    'spikes_3sig': len(spikes),
    'spike_pct': round(len(spikes)/len(df)*100, 2),
    'avg_fiz_share_all': round(avg_fiz_share_all, 1),
    'avg_fiz_share_spike': round(avg_fiz_share_spike, 1),
    'spike_fiz_long_pct': round(fiz_positive_on_spike, 1),
    'spike_types': dict(stype_ct),
    'session_dist': dict(spike_sessions),
}

# Build text summary
summary = f"""# BR Volume + OI Analysis (M5)

**Period:** {df['time'].min().strftime('%Y-%m-%d')} → {df['time'].max().strftime('%Y-%m-%d')} · **Bars:** {len(df):,}

---

## 1. Volume Spikes: Time Distribution

Total >3σ spikes: **{len(spikes)}** ({len(spikes)/len(df)*100:.2f}% of bars)

### By Session
| Session | Count | % |
|---------|-------|---|
| Europe | {spike_sessions.get('Europe',0)} | {spike_sessions.get('Europe',0)/len(spikes)*100:.0f}% |
| Asia | {spike_sessions.get('Asia',0)} | {spike_sessions.get('Asia',0)/len(spikes)*100:.0f}% |
| America | {spike_sessions.get('America',0)} | {spike_sessions.get('America',0)/len(spikes)*100:.0f}% |

### Peak Hours
"""
top_hours = spike_hours.most_common(5)
for h, c in top_hours:
    pct = c / len(spikes) * 100
    bar = '█' * (c // 10)
    summary += f"  {h:02d}:00 MSK {bar} {c} ({pct:.0f}%)\n"

summary += f"""

## 2. ★ OI on Spike Bars

**Fiz share of total OI:** avg all bars = {avg_fiz_share_all:.1f}% · **on spike bars = {avg_fiz_share_spike:.1f}%**

| Component | All bars | On spikes |
|-----------|----------|-----------|
| Fiz share | {avg_fiz_share_all:.1f}% | {avg_fiz_share_spike:.1f}% |
| Spikes with fiz_net>0 (phys long) | — | {fiz_positive_on_spike:.0f}% |
| Spikes with yur_net>0 (yur long) | — | {yur_positive_on_spike:.0f}% |

### Spike Types (by OI z-score)
"""
for st in ['yur_accumulation','fiz_panic','both_accumulation','both_liquidation','mixed']:
    c = stype_ct.get(st, 0)
    summary += f"| {st:>20} | {c:>4} | {c/len(spikes)*100:.0f}% |\n"

summary += """

### Price Impact by Spike Type (next 3 bars)
| Type | Avg ret% | WR% | n |
|------|----------|-----|---|
"""
for st in ['yur_accumulation','fiz_panic','both_accumulation','both_liquidation','mixed']:
    idx = spikes[spikes['spike_type'] == st].index
    idx = idx[idx + 3 < len(df)]
    if len(idx) == 0: continue
    rets = df['ret_3fwd'].iloc[idx]
    summary += f"| {st:>20} | {rets.mean():>+7.3f}% | {(rets>0).mean()*100:.0f}% | {len(idx)} |\n"

summary += """
---

## Key Takeaways
"""
# Generate takeaways
if avg_fiz_share_spike > avg_fiz_share_all:
    summary += "1. **Volume spikes driven by FIZ** — fiz share выше на spike-барах, толпа генерирует всплески\n"
else:
    summary += "1. **Volume spikes driven by YUR** — на spike барах выше доля юрлиц\n"

yur_accum = stype_ct.get('yur_accumulation', 0)
fiz_panic = stype_ct.get('fiz_panic', 0)
if yur_accum > fiz_panic:
    summary += f"2. **Yur accumulation is {yur_accum/fiz_panic:.1f}x more common** than fiz panic on spikes\n"
elif fiz_panic > yur_accum:
    summary += f"2. **Fiz panic is {fiz_panic/yur_accum:.1f}x more common** than yur accumulation on spikes\n"
else:
    summary += "2. Yur accumulation and fiz panic equally common\n"

with open(f'{OUT}/summary.md', 'w') as f:
    f.write(summary)

print(summary)
print(f"\nDone → {OUT}/summary.md")
