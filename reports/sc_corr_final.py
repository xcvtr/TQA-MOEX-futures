#!/usr/bin/env python3
"""Full correlation scan: supercandles features vs next-day return, with correct OI and tickers."""
import subprocess
import sys

CH = ["clickhouse-client", "-h", "10.0.0.60", "-q"]

def q(sql):
    r = subprocess.run(CH + [sql], capture_output=True, text=True, timeout=120)
    if r.returncode:
        print(f"  SQL ERROR: {r.stderr[:200]}", file=sys.stderr)
        return []
    lines = [line for line in r.stdout.strip().split("\n") if line.strip()]
    return [line.split("\t") for line in lines]

# Топ-тикеры (все с достаточным количеством данных)
TICKERS = ["Si","GL","CR","BR","Eu","GD","SR","AF","RB","PD","PT","RI",
           "ED","NG","CC","MM","NM","NR","VB","X5","TN","SP","MX","GZ","GK",
           "OJ","KC","FF","SF","SV","BM","NA"]

results = {}

for ticker in TICKERS:
    sql = f"""
        WITH daily AS (
            SELECT 
                tradedate,
                argMax(pr_close, tradetime) as close,
                avg(disb_mean) as disb_avg,
                argMax(disb_last, tradetime) as disb_last,
                avg(disb_std) as disb_std_avg,
                avg(net_vol_pct) as nvp_avg,
                avg(vol_b_ratio) as vbr_avg,
                avg(trades_b_ratio) as tbr_avg,
                avg(val_b_ratio) as valbr_avg,
                max(oi_change) as oi_chg,
                argMax(oi_close, tradetime) as oi_close,
                argMax(oi_open, tradetime) as oi_open,
                sum(net_vol) as net_vol_sum,
                avg(pr_range_pct) as pr_range_avg,
                avg(pr_change_pct) as pr_change_avg,
                avg(vwap) as vwap_avg,
                sum(vol_sum) as volume,
                argMax(im, tradetime) as im
            FROM moex.supercandles_fo
            WHERE ticker = '{ticker}'
            GROUP BY tradedate
            ORDER BY tradedate
        ),
        daily2 AS (
            SELECT 
                *,
                close / lagInFrame(close, 1) OVER (ORDER BY tradedate) - 1 as ret,
                close / lagInFrame(close, 5) OVER (ORDER BY tradedate) - 1 as ret_5d,
                leadInFrame(close, 1) OVER (ORDER BY tradedate) / close - 1 as ret_next,
                oi_close - oi_open as oi_chg_day
            FROM daily
        )
        SELECT 
            -- Lagged: feature → ret_next
            toString(corr(disb_avg, ret_next * 100)),
            toString(corr(disb_last, ret_next * 100)),
            toString(corr(disb_std_avg, ret_next * 100)),
            toString(corr(nvp_avg, ret_next * 100)),
            toString(corr(vbr_avg, ret_next * 100)),
            toString(corr(tbr_avg, ret_next * 100)),
            toString(corr(oi_chg, ret_next * 100)),
            toString(corr(net_vol_sum, ret_next * 100)),
            toString(corr(pr_range_avg, ret_next * 100)),
            toString(corr(pr_change_avg, ret_next * 100)),
            toString(corr(im, ret_next * 100)),
            toString(corr(volume, ret_next * 100)),
            -- Lagged: ret_5d → ret_next (mean reversion)
            toString(corr(ret_5d, ret_next * 100)),
            -- Count + mean reversion stats
            toString(count()),
            toString(countIf(ret_5d > 0.03 AND ret_next < 0)),
            toString(countIf(ret_5d > 0.03)),
            toString(countIf(ret_5d < -0.03 AND ret_next > 0)),
            toString(countIf(ret_5d < -0.03))
        FROM daily2
    """
    
    rows = q(sql)
    if not rows or not rows[0]:
        continue
    
    r = rows[0]
    names = ["disb_avg","disb_last","disb_std","nvp","vbr","tbr",
             "oi_chg","net_vol","pr_range","pr_change","im","volume","ret_5d"]
    
    corrs = {}
    for i, name in enumerate(names):
        val = r[i] if i < len(r) else None
        try:
            corrs[name] = round(float(val), 4) if val and val != "nan" else None
        except:
            corrs[name] = None
    
    corrs["n"] = int(r[13]) if len(r) > 13 else 0
    corrs["mr_short_wins"] = int(r[14]) if len(r) > 14 else 0
    corrs["mr_short_total"] = int(r[15]) if len(r) > 15 else 0
    corrs["mr_long_wins"] = int(r[16]) if len(r) > 16 else 0
    corrs["mr_long_total"] = int(r[17]) if len(r) > 17 else 0
    
    results[ticker] = corrs

# Print
print(f"\n{'Ticker':<8} {'N':<6} {'disb→n':<10} {'oi→n':<10} {'nvp→n':<10} {'vbr→n':<10} {'pr_chg→n':<10} {'netv→n':<10} {'ret5d→n':<10} {'MR Short':<12} {'MR Long':<12}")
print("-" * 110)

for t in sorted(TICKERS):
    if t not in results:
        continue
    r = results[t]
    def f(v):
        return f"{v:+.4f}" if v is not None else "  -   "
    
    mr_s = f"{r['mr_short_wins']}/{r['mr_short_total']}" if r['mr_short_total'] > 0 else "-"
    mr_l = f"{r['mr_long_wins']}/{r['mr_long_total']}" if r['mr_long_total'] > 0 else "-"
    mr_s_wr = f"({r['mr_short_wins']/r['mr_short_total']*100:.0f}%)" if r['mr_short_total'] > 5 else ""
    mr_l_wr = f"({r['mr_long_wins']/r['mr_long_total']*100:.0f}%)" if r['mr_long_total'] > 5 else ""
    
    print(f"{t:<8} {r['n']:<6} {f(r['disb_avg']):<10} {f(r['oi_chg']):<10} {f(r['nvp']):<10} {f(r['vbr']):<10} {f(r['pr_change']):<10} {f(r['net_vol']):<10} {f(r['ret_5d']):<10} {mr_s+mr_s_wr:<12} {mr_l+mr_l_wr:<12}")

# Best by category
print("\n\n=== BEST LAGGED CORRELATIONS ===\n")

for cat_name, cat_key in [("disb_avg → next", "disb_avg"), ("oi_change → next", "oi_chg"),
                           ("net_vol_pct → next", "nvp"), ("vol_b_ratio → next", "vbr"),
                           ("trades_b_ratio → next", "tbr"), ("pr_change → next", "pr_change"),
                           ("net_vol → next", "net_vol"), ("ret_5d → next", "ret_5d"),
                           ("im → next", "im"), ("volume → next", "volume")]:
    
    sorted_r = sorted(results.items(), key=lambda x: abs(x[1].get(cat_key, 0) or 0), reverse=True)
    top = [f"{t}:{r.get(cat_key):+.4f}" for t, r in sorted_r[:5] if r.get(cat_key) is not None and abs(r.get(cat_key, 0)) > 0.03]
    if top:
        print(f"  {cat_name:<25}: {', '.join(top)}")

print("\n\n=== BEST MEAN REVERSION (ret_5d → ret_next) ===")
sorted_r = sorted(results.items(), key=lambda x: abs(x[1].get("ret_5d", 0) or 0), reverse=True)
for t, r in sorted_r[:20]:
    if r.get("ret_5d") is not None and abs(r["ret_5d"]) > 0.03:
        mr_s_wr = f"{r['mr_short_wins']/r['mr_short_total']*100:.0f}%" if r['mr_short_total'] > 5 else "-"
        mr_l_wr = f"{r['mr_long_wins']/r['mr_long_total']*100:.0f}%" if r['mr_long_total'] > 5 else "-"
        print(f"  {t:<6} r={r['ret_5d']:+.4f}  SHORT_WR={mr_s_wr} ({r['mr_short_wins']}/{r['mr_short_total']})  LONG_WR={mr_l_wr} ({r['mr_long_wins']}/{r['mr_long_total']})")
