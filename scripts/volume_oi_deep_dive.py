#!/usr/bin/env python3
"""
Volume × OI Deep Dive: multi-ticker cross-validation + grid search.
For each candidate ticker:
  1. Load M5 OHLCV + OI from ClickHouse
  2. Compute z-scores (vol, fiz_net, yur_net)
  3. Classify spike types
  4. Cross-validate by 4 semi-annual periods
  5. Grid search over vol_z / yur_z / horizon thresholds
  6. Best-combo price impact at 1,3,6,12 bars
  7. Save per-ticker summary + overall leaderboard

Usage: python3 scripts/volume_oi_deep_dive.py
"""
import sys, os, json
from pathlib import Path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta
from collections import Counter, defaultdict
import clickhouse_connect
import pandas as pd
import numpy as np
from config import CH_HOST, CH_PORT, CH_DB

OUT = 'reports/volume_oi_deep'
os.makedirs(OUT, exist_ok=True)

ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)

TICKERS = ['NR', 'CC', 'MG', 'VB', 'PD', 'NG', 'IB', 'TN', 'SR', 'SV', 'GD', 'SN', 'BR']

DAYS = 365 * 2
since = (datetime.now() - timedelta(days=DAYS)).strftime('%Y-%m-%d')

HORIZONS = [1, 3, 6, 12]
VOL_Z_THRESHOLDS = [2.5, 3.0, 3.5, 4.0]
YUR_Z_THRESHOLDS = [1.0, 1.5, 2.0]
GRID_HORIZONS = [3, 6, 12]


def rolling_zs(s, w=20):
    mu = s.rolling(w).mean()
    sd = s.rolling(w).std().replace(0, 1)
    return ((s - mu) / sd).fillna(0)


def analyze_ticker(ticker):
    print(f"\n{'='*60}")
    print(f"Analyzing {ticker}...")
    
    rows = ch.query("""
        SELECT p.time, p.open, p.close, p.volume,
               o.fiz_buy, o.fiz_sell, o.yur_buy, o.yur_sell, o.total_oi
        FROM moex.prices_5m AS p
        INNER JOIN moex.prices_5m_oi AS o ON o.symbol = p.symbol AND o.time = p.time
        WHERE p.symbol = %(t)s AND p.time >= %(s)s AND p.volume > 0 AND o.total_oi > 0
        ORDER BY p.time
    """, parameters={'t': ticker, 's': since}).result_rows
    
    if not rows or len(rows) < 200:
        print(f"  SKIP: too few rows ({len(rows) if rows else 0})")
        return None
    
    df = pd.DataFrame(rows, columns=['time', 'open', 'close', 'volume',
                                     'fiz_buy', 'fiz_sell', 'yur_buy', 'yur_sell', 'total_oi'])
    
    df['fiz_net'] = df['fiz_buy'] - df['fiz_sell']
    df['yur_net'] = df['yur_buy'] - df['yur_sell']
    df['fiz_share'] = (df['fiz_buy'] + df['fiz_sell']) / df['total_oi'].replace(0, 1) * 100
    
    n_bars = len(df)
    md_vol = int(df['volume'].median())
    avg_fiz_all = float(df['fiz_share'].mean())
    
    # Compute all z-scores
    df['vol_z20'] = rolling_zs(df['volume'], 20)
    df['vol_z40'] = rolling_zs(df['volume'], 40)
    df['vol_z60'] = rolling_zs(df['volume'], 60)
    df['fiz_z'] = rolling_zs(df['fiz_net'], 20)
    df['yur_z'] = rolling_zs(df['yur_net'], 20)
    
    # Split into 4 semi-annual periods for cross-validation
    t_min, t_max = df['time'].min(), df['time'].max()
    total_days = (t_max - t_min).days
    period_boundaries = [t_min]
    for i in range(1, 4):
        period_boundaries.append(t_min + timedelta(days=total_days * i // 4))
    period_boundaries.append(t_max + timedelta(seconds=1))
    
    period_labels = [f"P{i+1}" for i in range(4)]
    df['period'] = 0
    for i in range(4):
        mask = (df['time'] >= period_boundaries[i]) & (df['time'] < period_boundaries[i+1])
        df.loc[mask, 'period'] = i + 1
    
    # Compute forward returns at multiple horizons
    for h in [1, 3, 6, 12]:
        df[f'ret_{h}fwd'] = df['close'].pct_change(h).shift(-h) * 100
    
    results = {}
    
    # ── Grid search ──────────────────────────────────────────────
    print("  Grid search over vol_z, yur_z, horizon...")
    best_score = -999
    best_params = None
    grid_results = []
    
    for vz in VOL_Z_THRESHOLDS:
        for yz in YUR_Z_THRESHOLDS:
            for hz in GRID_HORIZONS:
                ret_col = f'ret_{hz}fwd'
                
                # Classify yur_accum for THIS threshold combo
                conditions = [
                    (df['vol_z20'] > vz) & (df['yur_z'] > yz) & (df['fiz_z'] < 0),
                ]
                df['_entry'] = np.where(conditions[0], 1, 0)
                
                period_stats = []
                n_total = 0
                
                for p in range(1, 5):
                    p_mask = df['period'] == p
                    e_mask = df['_entry'] == 1
                    combined = p_mask & e_mask
                    idx = df[combined].index
                    idx_valid = idx[idx + hz < len(df)]
                    
                    if len(idx_valid) == 0:
                        period_stats.append({'period': p, 'n': 0, 'avg_ret': 0, 'wr': 50.0})
                        continue
                    
                    rets = df[ret_col].iloc[idx_valid]
                    n_total += len(rets)
                    period_stats.append({
                        'period': p,
                        'n': int(len(rets)),
                        'avg_ret': round(float(rets.mean()), 4),
                        'wr': round(float((rets > 0).mean() * 100), 1),
                    })
                
                avg_wr = np.mean([ps['wr'] for ps in period_stats if ps['n'] > 0]) if any(ps['n'] > 0 for ps in period_stats) else 0
                min_wr = min([ps['wr'] for ps in period_stats if ps['n'] > 0]) if any(ps['n'] > 0 for ps in period_stats) else 0
                all_above_50 = all(ps['wr'] > 50 for ps in period_stats if ps['n'] > 0)
                avg_ret = sum(ps['avg_ret'] * ps['n'] for ps in period_stats) / max(n_total, 1)
                
                # Score: prefer high WR, stability across periods, and positive returns
                score = avg_wr + (10 if all_above_50 else 0) + (avg_ret * 50 if avg_ret > 0 else avg_ret * 10)
                
                entry = {
                    'vz': vz, 'yz': yz, 'hz': hz,
                    'n_total': n_total,
                    'avg_ret': round(avg_ret, 4),
                    'avg_wr': round(avg_wr, 1),
                    'min_wr': round(min_wr, 1),
                    'all_above_50': all_above_50,
                    'score': round(score, 1),
                    'periods': period_stats,
                }
                grid_results.append(entry)
                
                if score > best_score and n_total >= 10:
                    best_score = score
                    best_params = entry
    
    if best_params is None or best_params['n_total'] < 10:
        print(f"  SKIP: no viable threshold combo ({best_params['n_total'] if best_params else 0} entries)")
        return None
    
    # ── Re-run best params for detailed analysis ────────────────
    bvz = best_params['vz']
    byz = best_params['yz']
    bhz = best_params['hz']
    print(f"  Best: vol_z>{bvz} yur_z>{byz} horizon={bhz} — avg_wr={best_params['avg_wr']:.1f}% avg_ret={best_params['avg_ret']:.4f}% n={best_params['n_total']}")
    
    # Re-classify with best params
    conditions = [
        (df['vol_z20'] > bvz) & (df['yur_z'] > byz) & (df['fiz_z'] < 0),
        (df['vol_z20'] > bvz) & (df['fiz_z'] > byz) & (df['yur_z'] < 0),
    ]
    choices = ['yur_accum', 'fiz_panic']
    df['spike_type'] = np.select(conditions, choices, default='mixed')
    spike_mask = df['vol_z20'] > bvz
    
    # Price impact at multiple horizons for yur_accum
    price_impact = {}
    for h in HORIZONS:
        ret_col = f'ret_{h}fwd'
        yur_idx = df[(df['spike_type'] == 'yur_accum')].index
        yur_idx = yur_idx[yur_idx + h < len(df)]
        
        if len(yur_idx) >= 5:
            rets = df[ret_col].iloc[yur_idx]
            price_impact[h] = {
                'n': int(len(yur_idx)),
                'avg_ret': round(float(rets.mean()), 4),
                'wr': round(float((rets > 0).mean() * 100), 1),
            }
        
        fiz_idx = df[(df['spike_type'] == 'fiz_panic')].index
        fiz_idx = fiz_idx[fiz_idx + h < len(df)]
        
        if len(fiz_idx) >= 5:
            fiz_rets = df[ret_col].iloc[fiz_idx]
            price_impact[f'fiz_{h}'] = {
                'n': int(len(fiz_idx)),
                'avg_ret': round(float(fiz_rets.mean()), 4),
                'wr': round(float((fiz_rets > 0).mean() * 100), 1),
            }
    
    # ── Portfolio simulation (simple) ──────────────────────────
    # Entry on open of bar AFTER spike, exit after horizon bars
    sim_mask = df['spike_type'] == 'yur_accum'
    sim_entries = df[sim_mask].index
    sim_entries = sim_entries[sim_entries + bhz < len(df)]
    
    sim_trades = []
    for idx in sim_entries:
        entry_price = df.loc[idx + 1, 'open'] if idx + 1 < len(df) else df.loc[idx, 'close']
        exit_idx = min(idx + bhz, len(df) - 1)
        exit_price = df.loc[exit_idx, 'close']
        ret_pct = (exit_price / entry_price - 1) * 100
        sim_trades.append({
            'entry_time': str(df.loc[idx, 'time']),
            'entry_price': float(entry_price),
            'exit_price': float(exit_price),
            'ret': round(float(ret_pct), 4),
        })
    
    sim_df = pd.DataFrame(sim_trades) if sim_trades else pd.DataFrame()
    sim_avg_ret = float(sim_df['ret'].mean()) if len(sim_df) > 0 else 0
    sim_wr = float((sim_df['ret'] > 0).mean() * 100) if len(sim_df) > 0 else 0
    
    # ── Build result ──────────────────────────────────────────
    res = {
        'ticker': ticker,
        'n_bars': n_bars,
        'n_spikes': int(spike_mask.sum()),
        'md_vol': md_vol,
        'avg_fiz_all': round(avg_fiz_all, 1),
        'avg_fiz_spike': round(float(df.loc[spike_mask, 'fiz_share'].mean()), 1) if spike_mask.any() else 0,
        'best_vz': bvz,
        'best_yz': byz,
        'best_hz': bhz,
        'best_n': best_params['n_total'],
        'best_avg_ret': best_params['avg_ret'],
        'best_avg_wr': best_params['avg_wr'],
        'best_min_wr': best_params['min_wr'],
        'best_all_above_50': best_params['all_above_50'],
        'periods': best_params['periods'],
        'price_impact': price_impact,
        'grid_results': grid_results,
        'sim_avg_ret': round(sim_avg_ret, 4),
        'sim_wr': round(sim_wr, 1),
        'sim_n': len(sim_trades),
    }
    
    return res, df


def save_ticker_report(ticker, res):
    ticker_out = os.path.join(OUT, ticker)
    os.makedirs(ticker_out, exist_ok=True)
    
    periods_str = ""
    for ps in res['periods']:
        pr = "✓" if ps['wr'] > 50 else "✗"
        periods_str += f"| {pr} | P{ps['period']} | {ps['n']} | {ps['avg_ret']:+.4f}% | {ps['wr']:.1f}% |\n"
    
    pi_str = ""
    for h in HORIZONS:
        if h in res['price_impact']:
            pi = res['price_impact'][h]
            pi_str += f"| {h} | {pi['n']} | {pi['avg_ret']:+.4f}% | {pi['wr']:.1f}% |\n"
    
    stable = "✓ YES (WR>50% in all 4 periods)" if res['best_all_above_50'] else "✗ NO"
    
    summary = f"""# {ticker} — Volume × OI Deep Dive

## Basic Stats
| Metric | Value |
|--------|-------|
| N bars | {res['n_bars']} |
| N spikes (vol_z>{res['best_vz']}) | {res['n_spikes']} |
| Median volume | {res['md_vol']} |
| Avg fiz_share (all) | {res['avg_fiz_all']:.1f}% |
| Avg fiz_share (spike) | {res['avg_fiz_spike']:.1f}% |

## Best Thresholds (Grid Search)
| vol_z | yur_z | horizon | n | avg_ret | avg_wr | min_wr | Stable |
|-------|-------|---------|---|---------|--------|--------|--------|
| {res['best_vz']} | {res['best_yz']} | {res['best_hz']} | {res['best_n']} | {res['best_avg_ret']:+.4f}% | {res['best_avg_wr']:.1f}% | {res['best_min_wr']:.1f}% | {stable} |

## Cross-Validation by Period
| Stable | Period | N | Avg Ret | WR |
|--------|--------|---|---------|----|
{periods_str}
## Price Impact — yur_accum by Horizon
| Horizon | N | Avg Ret | WR |
|---------|---|---------|----|
{pi_str}
## Portfolio Simulation (simple)
| Metric | Value |
|--------|-------|
| N trades | {res['sim_n']} |
| Avg return | {res['sim_avg_ret']:+.4f}% |
| WR | {res['sim_wr']:.1f}% |

## All Grid Combinations (sorted by score)
| vol_z | yur_z | horizon | n | avg_ret | avg_wr | min_wr | all>50 | score |
|-------|-------|---------|---|---------|--------|--------|--------|-------|
"""
    grid_sorted = sorted(res['grid_results'], key=lambda x: x['score'], reverse=True)
    for g in grid_sorted:
        ok = "✓" if g['all_above_50'] else " "
        summary += f"| {g['vz']} | {g['yz']} | {g['hz']} | {g['n_total']} | {g['avg_ret']:+.4f}% | {g['avg_wr']:.1f}% | {g['min_wr']:.1f}% | {ok} | {g['score']:.0f} |\n"
    
    with open(os.path.join(ticker_out, 'summary.md'), 'w') as f:
        f.write(summary)
    print(f"  → Saved {ticker_out}/summary.md")


def build_overall_report(results):
    stable_tickers = []
    unstable_tickers = []
    
    for r in results:
        if r['best_all_above_50']:
            stable_tickers.append(r)
        else:
            unstable_tickers.append(r)
    
    header = "| Ticker | N bars | N spikes | Best VZ | Best YZ | Best HZ | N entries | Avg Ret | Avg WR | Min WR | Stable | Sim Ret | Sim WR | Sim N |"
    sep = "|--------|--------|----------|---------|---------|---------|-----------|---------|--------|--------|--------|---------|--------|-------|"
    
    rows = ""
    for r in sorted(results, key=lambda x: x['best_avg_wr'], reverse=True):
        st = "✓" if r['best_all_above_50'] else "✗"
        rows += f"| {r['ticker']} | {r['n_bars']} | {r['n_spikes']} | {r['best_vz']} | {r['best_yz']} | {r['best_hz']} | {r['best_n']} | {r['best_avg_ret']:+.4f}% | {r['best_avg_wr']:.1f}% | {r['best_min_wr']:.1f}% | {st} | {r['sim_avg_ret']:+.4f}% | {r['sim_wr']:.1f}% | {r['sim_n']} |\n"
    
    stable_rows = ""
    for r in stable_tickers:
        periods_detail = " | ".join([f"P{p['period']}: {p['wr']:.0f}%({p['n']})" for p in r['periods']])
        stable_rows += f"| {r['ticker']} | {r['best_avg_ret']:+.4f}% | {r['best_avg_wr']:.1f}% | {r['best_min_wr']:.1f}% | {r['best_vz']}/{r['best_yz']}/{r['best_hz']} | {periods_detail} |\n"
    
    unstable_rows = ""
    for r in sorted(unstable_tickers, key=lambda x: x['best_avg_wr'], reverse=True):
        periods_detail = " | ".join([f"P{p['period']}: {p['wr']:.0f}%({p['n']})" for p in r['periods']])
        failed_periods = [f"P{p['period']}({p['wr']:.0f}%)" for p in r['periods'] if p['wr'] <= 50 and p['n'] > 0]
        fail_detail = ", ".join(failed_periods) if failed_periods else "all ok"
        unstable_rows += f"| {r['ticker']} | {r['best_avg_ret']:+.4f}% | {r['best_avg_wr']:.1f}% | {r['best_min_wr']:.1f}% | {r['best_vz']}/{r['best_yz']}/{r['best_hz']} | {periods_detail} | Fails: {fail_detail} |\n"
    
    overall = f"""# Volume × OI Deep Dive — Overall Report

Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}
Tickers analyzed: {len(results)}

---

## Leaderboard (sorted by WR)

{header}
{sep}
{rows}

---

## ✓ Stable Tickers (WR > 50% in ALL 4 periods)

| Ticker | Avg Ret | Avg WR | Min WR | Best Params | Per-period WR |
|--------|---------|--------|--------|-------------|---------------|
{stable_rows}

### Portfolio Summary (Stable Tickers)
"""
    if stable_tickers:
        overall += "| Ticker | Sim Ret | Sim WR | Sim N |\n|--------|---------|--------|-------|\n"
        for r in stable_tickers:
            overall += f"| {r['ticker']} | {r['sim_avg_ret']:+.4f}% | {r['sim_wr']:.1f}% | {r['sim_n']} |\n"

    overall += f"""
---

## Unstable Tickers (fail in at least 1 period)

| Ticker | Avg Ret | Avg WR | Min WR | Best Params | Per-period WR | Failure |
|--------|---------|--------|--------|-------------|---------------|---------|
{unstable_rows}

---

## Key Takeaways
"""
    if stable_tickers:
        overall += f"""
### Stable tickers ({len(stable_tickers)}):
"""
        for r in stable_tickers:
            overall += f"- **{r['ticker']}**: avg_ret={r['best_avg_ret']:+.4f}%, avg_wr={r['best_avg_wr']:.1f}%, params=vol_z>{r['best_vz']}, yur_z>{r['best_yz']}, horizon={r['best_hz']}\n"
    else:
        overall += "\n### Stable tickers (0):\nNone passed the WR>50% in all 4 periods filter.\n"
    
    overall += f"""
### Unstable tickers ({len(unstable_tickers)}):
"""
    for r in unstable_tickers:
        failed = [f"P{p['period']}({p['wr']:.0f}%)" for p in r['periods'] if p['wr'] <= 50 and p['n'] > 0]
        overall += f"- **{r['ticker']}**: avg_wr={r['best_avg_wr']:.1f}%, fails in {', '.join(failed)}\n"
    
    with open(os.path.join(OUT, 'overall.md'), 'w') as f:
        f.write(overall)
    print(f"\n→ Saved {OUT}/overall.md")


# ═══════════════════════════════════════════════════════════════
if __name__ == '__main__':
    print(f"Volume × OI Deep Dive — Scanning {len(TICKERS)} tickers (since {since})")
    results = []
    
    for t in TICKERS:
        try:
            res = analyze_ticker(t)
            if res is not None:
                res_data, df = res
                save_ticker_report(t, res_data)
                results.append(res_data)
        except Exception as e:
            print(f"  ERROR: {t} — {e}")
            import traceback
            traceback.print_exc()
    
    if results:
        build_overall_report(results)
        print(f"\nDone! {len(results)} tickers analyzed → {OUT}/")
    else:
        print("\nNo results to report.")
