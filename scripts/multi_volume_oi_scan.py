#!/usr/bin/env python3
"""
Multi-ticker Volume + OI analysis: scan all MOEX instruments.
For each ticker loads M5 OHLCV + OI, finds volume spikes, classifies by OI composition,
measures price impact. Produces leaderboard.

Usage: python3 scripts/multi_volume_oi_scan.py
See config.py for TICKERS list.
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta
from collections import Counter
import clickhouse_connect
import pandas as pd
import numpy as np
from config import CH_HOST, CH_PORT, CH_DB

OUT = 'reports/volume_oi_scan'
os.makedirs(OUT, exist_ok=True)

# Tickers: all with OI data
TICKERS = ['AF','AL','AU','BM','BR','CC','CE','CH','CNYRUBF','CR','DX','ED','Eu','EURRUBF',
           'FF','GAZPF','GD','GK','GL','GLDRUBF','GZ','HS','HY','IB','IMOEXF','KC','LK',
           'MC','ME','MG','MM','MN','MX','MY','NA','NG','NM','NR','OJ','PD','PT','RB',
           'RI','RL','RM','RN','SBERF','SE','SF','Si','SN','SP','SR','SS','SV','TN',
           'TT','UC','USDRUBF','VB','VI','W4','X5','YD']

ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)
DAYS = 365 * 2  # 2 years — enough to see patterns, not overwhelming
since = (datetime.now() - timedelta(days=DAYS)).strftime('%Y-%m-%d')

def rolling_zs(s, w=20):
    mu = s.rolling(w).mean()
    sd = s.rolling(w).std().replace(0, 1)
    return ((s - mu) / sd).fillna(0)

def analyze_ticker(ticker):
    """Return dict with stats or None if no data."""
    try:
        rows = ch.query("""
            SELECT p.time, p.close, p.volume,
                   o.fiz_buy, o.fiz_sell, o.yur_buy, o.yur_sell, o.total_oi
            FROM moex.prices_5m AS p
            INNER JOIN moex.prices_5m_oi AS o ON o.symbol = p.symbol AND o.time = p.time
            WHERE p.symbol = %(t)s AND p.time >= %(s)s AND p.volume > 0 AND o.total_oi > 0
            ORDER BY p.time
        """, parameters={'t': ticker, 's': since}).result_rows
    except Exception as e:
        return None, str(e)
    
    if not rows or len(rows) < 200:
        return None, f"too few rows ({len(rows) if rows else 0})"
    
    df = pd.DataFrame(rows, columns=['time','close','volume','fiz_buy','fiz_sell','yur_buy','yur_sell','total_oi'])
    df['fiz_net'] = df['fiz_buy'] - df['fiz_sell']
    df['yur_net'] = df['yur_buy'] - df['yur_sell']
    df['fiz_share'] = (df['fiz_buy'] + df['fiz_sell']) / df['total_oi'] * 100
    df['vol_z'] = rolling_zs(df['volume'], 20)
    
    spikes = df[df['vol_z'] > 3.0]
    
    if len(spikes) < 10:
        return None, f"too few spikes ({len(spikes)})"
    
    # Spike time dist
    spike_hours = spikes['time'].dt.hour
    spike_sessions = spike_hours.apply(lambda h: 'Europe' if 10 <= h < 16 else ('Asia' if 3 <= h < 10 else ('America' if 16 <= h < 24 else 'Off')))
    sess_dist = Counter(spike_sessions)
    
    # OI on spikes
    avg_fiz_all = df['fiz_share'].mean()
    avg_fiz_spike = spikes['fiz_share'].mean()
    fiz_long_pct = (spikes['fiz_net'] > 0).mean() * 100
    
    # Spike types
    df['fiz_z'] = rolling_zs(df['fiz_net'], 20)
    df['yur_z'] = rolling_zs(df['yur_net'], 20)
    
    conditions = [
        (df['vol_z'] > 3.0) & (df['yur_z'] > 1.5) & (df['fiz_z'] < 0),
        (df['vol_z'] > 3.0) & (df['fiz_z'] > 1.5) & (df['yur_z'] < 0),
    ]
    choices = ['yur_accum', 'fiz_panic']
    df['spike_type'] = np.select(conditions, choices, default='mixed')
    spike_types = df[df['vol_z'] > 3.0]['spike_type'].value_counts()
    
    # Price impact after spike (3 bars)
    df['ret_3fwd'] = df['close'].pct_change(3).shift(-3) * 100
    
    # By spike type
    price_impact = {}
    for st in ['yur_accum', 'fiz_panic', 'mixed']:
        mask = (df['vol_z'] > 3.0) & (df['spike_type'] == st)
        idx = df[mask].index
        idx = idx[idx + 3 < len(df)]
        if len(idx) >= 5:
            rets = df['ret_3fwd'].iloc[idx]
            price_impact[st] = {
                'n': int(len(idx)),
                'avg_ret': round(float(rets.mean()), 4),
                'wr': round(float((rets > 0).mean() * 100), 1),
            }
    
    # Physical volume stats
    vol_median = int(df['volume'].median())
    vol_mean = int(df['volume'].mean())
    vol_peak = int(df['volume'].max())
    
    return {
        'ticker': ticker,
        'bars': len(df),
        'days_covered': (df['time'].max() - df['time'].min()).days,
        'vol_median': vol_median,
        'vol_mean': vol_mean,
        'vol_peak': vol_peak,
        'spikes_3sig': int(len(spikes)),
        'spike_pct': round(len(spikes) / len(df) * 100, 2),
        'avg_fiz_all': round(avg_fiz_all, 1),
        'avg_fiz_spike': round(avg_fiz_spike, 1),
        'fiz_long_pct': round(fiz_long_pct, 1),
        'yur_accum_pct': round(float(spike_types.get('yur_accum', 0)) / len(spikes) * 100, 1),
        'fiz_panic_pct': round(float(spike_types.get('fiz_panic', 0)) / len(spikes) * 100, 1),
        'sess_europe': round(sess_dist.get('Europe', 0) / len(spikes) * 100, 1),
        'sess_asia': round(sess_dist.get('Asia', 0) / len(spikes) * 100, 1),
        'sess_america': round(sess_dist.get('America', 0) / len(spikes) * 100, 1),
        'price_impact_yur_accum': price_impact.get('yur_accum', {}).get('avg_ret', None),
        'price_impact_fiz_panic': price_impact.get('fiz_panic', {}).get('avg_ret', None),
        'pi_wr_yur_accum': price_impact.get('yur_accum', {}).get('wr', None),
        'pi_wr_fiz_panic': price_impact.get('fiz_panic', {}).get('wr', None),
    }, None

# Run
print(f"Scanning {len(TICKERS)} tickers (since {since})...")
results = []
errors = []

for i, t in enumerate(TICKERS):
    print(f"  [{i+1}/{len(TICKERS)}] {t}...", end=' ', flush=True)
    r, err = analyze_ticker(t)
    if r:
        results.append(r)
        print(f"✓ {r['bars']} bars, {r['spikes_3sig']} spikes", flush=True)
    else:
        errors.append((t, err))
        print(f"✗ {err}", flush=True)

# ── Build leaderboard ──
print(f"\nResults: {len(results)} tickers, {len(errors)} errors")

df_r = pd.DataFrame(results)
df_r = df_r.sort_values('spikes_3sig', ascending=False)

# Key metrics
print("\n" + "="*120)
print(f"{'Ticker':>10} {'Bars':>8} {'Spikes':>7} {'Spk%':>5} {'Vol_md':>7} {'Vol_pk':>7} "
      f"| {'FizAll':>6} {'FizSpk':>6} {'FizLng':>6} | {'YurAcc':>7} {'FizPan':>7} "
      f"| {'SesEur':>7} {'SesAsia':>7} {'SesUSA':>7} | {'PI_Yur':>8} {'PI_Fiz':>8}"
      f"| {'WR_Yu':>6} {'WR_Fz':>6}")
print("="*120)

for _, r in df_r.iterrows():
    sp = f"{r['spikes_3sig']}"
    spct = f"{r['spike_pct']}"
    fa = f"{r['avg_fiz_all']}"
    fs = f"{r['avg_fiz_spike']}"
    fl = f"{r['fiz_long_pct']}"
    ya = f"{r['yur_accum_pct']}"
    fp = f"{r['fiz_panic_pct']}"
    se = f"{r['sess_europe']}"
    sa = f"{r['sess_asia']}"
    su = f"{r['sess_america']}"
    pya = f"{r['price_impact_yur_accum']:+.4f}" if r['price_impact_yur_accum'] is not None else '   N/A'
    pfp = f"{r['price_impact_fiz_panic']:+.4f}" if r['price_impact_fiz_panic'] is not None else '   N/A'
    wya = f"{r['pi_wr_yur_accum']}" if r['pi_wr_yur_accum'] is not None else '  N/A'
    wfp = f"{r['pi_wr_fiz_panic']}" if r['pi_wr_fiz_panic'] is not None else '  N/A'
    
    print(f"{r['ticker']:>10} {r['bars']:>8} {sp:>7} {spct:>5} {r['vol_median']:>7} {r['vol_peak']:>7} "
          f"| {fa:>6} {fs:>6} {fl:>6} | {ya:>7} {fp:>7} "
          f"| {se:>7} {sa:>7} {su:>7} | {pya:>8} {pfp:>8}"
          f"| {wya:>6} {wfp:>6}")

print("\nErrors:")
for t, e in errors:
    print(f"  {t}: {e}")

# ── Save ──
print(f"\nSaving to {OUT}/...")
df_r.to_csv(f'{OUT}/leaderboard.csv', index=False)

# Summary text
cols_of_interest = ['ticker','bars','spikes_3sig','spike_pct','avg_fiz_all','avg_fiz_spike',
                    'fiz_long_pct','yur_accum_pct','fiz_panic_pct',
                    'sess_europe','sess_asia','sess_america',
                    'price_impact_yur_accum','price_impact_fiz_panic','pi_wr_yur_accum','pi_wr_fiz_panic']
summary = f"""# Multi-Ticker Volume + OI Scan

**Period:** {DAYS//365} years ({since}) · **Tickers scanned:** {len(TICKERS)} / {len(results)} loaded

---

## Leaderboard by Spike Activity

Sorted by number of >3σ volume spikes:
"""
summary += df_r[['ticker','bars','spikes_3sig','spike_pct','avg_fiz_all','avg_fiz_spike',
                 'yur_accum_pct','fiz_panic_pct',
                 'price_impact_yur_accum','price_impact_fiz_panic','pi_wr_yur_accum','pi_wr_fiz_panic']].to_string(index=False)

# Find tickers where yur_accum gives positive returns
pos_ya = df_r[df_r['price_impact_yur_accum'].notna() & (df_r['price_impact_yur_accum'] > 0)]
neg_ya = df_r[df_r['price_impact_yur_accum'].notna() & (df_r['price_impact_yur_accum'] <= 0)]

summary += f"""

---

## Summary Statistics

| Metric | Value |
|--------|-------|
| Total tickers with OI data | {len(results)} |
| Tickers with yur_accum WR > 50% | {len(pos_ya)} of {len(pos_ya)+len(neg_ya)} |
| Tickers with fiz_panic WR > 50% | {len(df_r[df_r['pi_wr_fiz_panic'].notna() & (df_r['pi_wr_fiz_panic'] > 50)])} |
| Avg fiz_share on spike bars | {df_r['avg_fiz_spike'].mean():.1f}% |
| Avg fiz_share all bars | {df_r['avg_fiz_all'].mean():.1f}% |
| Avg % of spikes classed as yur_accum | {df_r['yur_accum_pct'].mean():.1f}% |
| Avg % of spikes classed as fiz_panic | {df_r['fiz_panic_pct'].mean():.1f}% |

## Top Tickers by Yur Accumulation WR (best → worst)
"""
top_yur = df_r[df_r['pi_wr_yur_accum'].notna()].sort_values('pi_wr_yur_accum', ascending=False)
for _, r in top_yur.head(15).iterrows():
    summary += f"| {r['ticker']:>10} | yur_accum ret={r['price_impact_yur_accum']:+.4f}% WR={r['pi_wr_yur_accum']:.0f}% | n/spk={r['spikes_3sig']:>5} |\n"

with open(f'{OUT}/summary.md', 'w') as f:
    f.write(summary)
print(f"Done → {OUT}/summary.md")
