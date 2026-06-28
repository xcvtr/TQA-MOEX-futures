#!/usr/bin/env python3
"""Fast correlation scan: supercandles vs next-day return with correct OI."""
import subprocess
import sys

CH = ["clickhouse-client", "-h", "10.0.0.60", "-q"]

def q(sql):
    r = subprocess.run(CH + [sql], capture_output=True, text=True, timeout=60)
    if r.returncode:
        return []
    return [line.split("\t") for line in r.stdout.strip().split("\n") if line.strip()]

TICKERS = ["Si","GL","CR","BR","Eu","GD","SR","AF","RB","PD","PT","RI",
           "ED","NG","CC","MM","NM","NR","VB","X5","TN","SP","MX","GZ","GK"]

results = {}

for ticker in TICKERS:
    sql = f"""
        WITH daily AS (
            SELECT 
                tradedate,
                argMax(pr_close, tradetime) as close,
                avg(disb_mean) as disb_avg,
                argMax(disb_last, tradetime) as disb_last,
                avg(net_vol_pct) as nvp_avg,
                avg(vol_b_ratio) as vbr_avg,
                max(oi_change) as oi_chg,
                sum(net_vol) as net_vol_sum,
                avg(pr_range_pct) as pr_range_avg,
                avg(pr_change_pct) as pr_change_avg
            FROM moex.supercandles_fo
            WHERE ticker = '{ticker}'
            GROUP BY tradedate
            ORDER BY tradedate
        )
        SELECT 
            corr(disb_avg, (leadInFrame(close, 1) OVER w / close - 1) * 100) as c1,
            corr(disb_last, (leadInFrame(close, 1) OVER w / close - 1) * 100) as c2,
            corr(nvp_avg, (leadInFrame(close, 1) OVER w / close - 1) * 100) as c3,
            corr(vbr_avg, (leadInFrame(close, 1) OVER w / close - 1) * 100) as c4,
            corr(oi_chg, (leadInFrame(close, 1) OVER w / close - 1) * 100) as c5,
            corr(net_vol_sum, (leadInFrame(close, 1) OVER w / close - 1) * 100) as c6,
            corr(pr_range_avg, (leadInFrame(close, 1) OVER w / close - 1) * 100) as c7,
            corr(pr_change_avg, (leadInFrame(close, 1) OVER w / close - 1) * 100) as c8,
            count() as n
        FROM daily
        WINDOW w AS (ORDER BY tradedate)
    """
    
    rows = q(sql)
    if rows:
        r = rows[0]
        corrs = {}
        names = ["disb_next","disb_last_next","nvp_next","vbr_next",
                 "oi_next","netvol_next","range_next","prchg_next"]
        for i, name in enumerate(names):
            try:
                corrs[name] = round(float(r[i]), 4) if r[i] else None
            except:
                corrs[name] = None
        corrs["n_days"] = int(r[len(names)]) if len(r) > len(names) else 0
        results[ticker] = corrs

        print(f"{ticker:<6} disb_n={str(corrs.get('disb_next','?')):>8}  oi_n={str(corrs.get('oi_next','?')):>8}  "
              f"nvp_n={str(corrs.get('nvp_next','?')):>8}  nv_n={str(corrs.get('netvol_next','?')):>8}  "
              f"prchg_n={str(corrs.get('prchg_next','?')):>8}  days={corrs.get('n_days','?')}")

# Best by category
print("\n" + "=" * 70)
print("BEST LAGGED CORRELATIONS (feature → ret_next)")
print("=" * 70)

categories = [
    ("disb_avg → next", "disb_next"),
    ("disb_last → next", "disb_last_next"),
    ("net_vol_pct → next", "nvp_next"),
    ("vol_b_ratio → next", "vbr_next"),
    ("oi_change → next", "oi_next"),
    ("net_vol → next", "netvol_next"),
    ("pr_range → next", "range_next"),
    ("pr_change → next", "prchg_next"),
]

for name, key in categories:
    sorted_r = sorted(results.items(), key=lambda x: abs(x[1].get(key, 0) or 0), reverse=True)
    print(f"\n--- {name} ---")
    for t, r in sorted_r[:8]:
        v = r.get(key)
        if v is not None and abs(v) > 0.03:
            print(f"  {t:<6} r={v:+.4f}")
