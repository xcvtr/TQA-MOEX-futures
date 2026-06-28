#!/usr/bin/env python3
"""Final correlation: supercandles features vs next-day return, via Pandas."""
import subprocess
import sys

import numpy as np
import pandas as pd

CH = ["clickhouse-client", "-h", "10.0.0.60", "-q"]

def q_df(sql):
    """Run SQL and return DataFrame."""
    r = subprocess.run(CH + [sql], capture_output=True, text=True, timeout=120)
    if r.returncode:
        print(f"SQL ERROR: {r.stderr[:200]}", file=sys.stderr)
        return None
    if not r.stdout.strip():
        return None
    lines = [line.split("\t") for line in r.stdout.strip().split("\n") if line.strip()]
    if not lines:
        return None
    # First line is header
    cols = lines[0]
    data = lines[1:]
    return pd.DataFrame(data, columns=cols)


TICKERS = ["Si","GL","CR","BR","Eu","GD","SR","AF","RB","PD","PT","RI",
           "ED","NG","CC","MM","NM","NR","VB","X5","TN","SP","MX","GZ","GK",
           "OJ","KC","FF","SF","SV","BM","NA"]

results = []

for ticker in TICKERS:
    sql = f"""
        SELECT 
            toString(tradedate) as dt,
            toString(argMax(pr_close, tradetime)) as close,
            toString(avg(disb_mean)) as disb_avg,
            toString(argMax(disb_last, tradetime)) as disb_last,
            toString(avg(disb_std)) as disb_std,
            toString(avg(net_vol_pct)) as nvp,
            toString(avg(vol_b_ratio)) as vbr,
            toString(max(oi_change)) as oi_chg,
            toString(sum(net_vol)) as net_vol_sum,
            toString(avg(pr_change_pct)) as pr_chg,
            toString(avg(pr_range_pct)) as pr_range,
            toString(sum(vol_sum)) as volume,
            toString(argMax(im, tradetime)) as im,
            toString(argMax(oi_close, tradetime)) as oi_close,
            toString(argMax(oi_open, tradetime)) as oi_open,
            toString(count()) as n_bars
        FROM moex.supercandles_fo
        WHERE ticker = '{ticker}'
        GROUP BY tradedate
        ORDER BY tradedate
        FORMAT TabSeparatedWithNames
    """
    
    df = q_df(sql)
    if df is None or len(df) < 50:
        print(f"{ticker:<6} SKIP ({len(df) if df is not None else 0} days)")
        continue
    
    # Convert types
    for c in df.columns:
        if c in ('dt',):
            continue
        df[c] = pd.to_numeric(df[c], errors='coerce')
    
    df['ret'] = df['close'].pct_change() * 100
    df['ret_next'] = df['ret'].shift(-1)
    df['ret_5d'] = df['close'].pct_change(5) * 100
    
    df = df.dropna(subset=['ret_next'])
    
    n = len(df)
    
    # Correlations
    feat_cols = ['disb_avg','disb_last','disb_std','nvp','vbr','oi_chg',
                 'net_vol_sum','pr_chg','pr_range','im','volume','ret_5d']
    
    corrs = {}
    for col in feat_cols:
        if col in df.columns and df[col].notna().sum() > 10:
            c = df[col].corr(df['ret_next'])
            corrs[col] = round(c, 4) if not np.isnan(c) else None
        else:
            corrs[col] = None
    
    corrs['n'] = n
    
    # Mean reversion: short after rise, long after fall
    rise = df['ret_5d'] > 3.0
    fall = df['ret_5d'] < -3.0
    
    mr_short = rise & (df['ret_next'] < 0)
    mr_long = fall & (df['ret_next'] > 0)
    
    corrs['mr_short_wr'] = f"{mr_short.sum()}/{rise.sum()} ({mr_short.sum()/rise.sum()*100:.0f}%)" if rise.sum() > 0 else "-"
    corrs['mr_long_wr'] = f"{mr_long.sum()}/{fall.sum()} ({mr_long.sum()/fall.sum()*100:.0f}%)" if fall.sum() > 0 else "-"
    corrs['mr_short_ret'] = round(df.loc[rise, 'ret_next'].mean(), 2) if rise.sum() > 3 else None
    corrs['mr_long_ret'] = round(df.loc[fall, 'ret_next'].mean(), 2) if fall.sum() > 3 else None
    
    results.append({'ticker': ticker, **corrs})
    
    print(f"{ticker:<6} n={n:<4} disb={corrs.get('disb_avg',''):>8} oi={corrs.get('oi_chg',''):>8} "
          f"nvp={corrs.get('nvp',''):>8} vbr={corrs.get('vbr',''):>8} "
          f"prchg={corrs.get('pr_chg',''):>8} ret5d={corrs.get('ret_5d',''):>8} "
          f"MR_S={corrs.get('mr_short_wr',''):<16} ({corrs.get('mr_short_ret',''):>+.2f}) "
          f"MR_L={corrs.get('mr_long_wr',''):<16} ({corrs.get('mr_long_ret',''):>+.2f})")

# Summary
print("\n\n" + "=" * 100)
print("BEST FEATURES — sorted by |correlation|")
print("=" * 100)

categories = [
    ("disb_avg → next (predictive)", "disb_avg"),
    ("oi_chg → next (predictive)", "oi_chg"),
    ("nvp → next (predictive)", "nvp"),
    ("vbr → next (predictive)", "vbr"),
    ("pr_chg → next (predictive)", "pr_chg"),
    ("ret_5d → next (mean rev)", "ret_5d"),
    ("net_vol → next", "net_vol_sum"),
    ("im → next (margin)", "im"),
]

for name, key in categories:
    sorted_r = sorted(results, key=lambda x: abs(x.get(key, 0) or 0), reverse=True)
    top = [f"{r['ticker']}:{r[key]:+.4f}" for r in sorted_r[:5] if r.get(key) is not None and abs(r.get(key, 0)) > 0.03]
    if top:
        print(f"\n  {name:<35}: {', '.join(top)}")

# Best mean reversion signals
print("\n\n=== BEST MEAN REVERSION SIGNALS (ret_5d > 3% → short next day) ===")
sorted_r = sorted(results, key=lambda x: abs(x.get('ret_5d', 0) or 0), reverse=True)
print(f"{'Ticker':<8} {'r_ret5d':<10} {'Short WR':<20} {'Short ret%':<12} {'Long WR':<20} {'Long ret%':<12}")
print("-" * 85)
for r in sorted_r:
    if r.get('ret_5d') is not None and abs(r['ret_5d']) > 0.03:
        print(f"{r['ticker']:<8} {r['ret_5d']:+.4f}    {r.get('mr_short_wr','-'):<20} {str(r.get('mr_short_ret','-')):<12} {r.get('mr_long_wr','-'):<20} {str(r.get('mr_long_ret','-')):<12}")

print("\nDone!")
