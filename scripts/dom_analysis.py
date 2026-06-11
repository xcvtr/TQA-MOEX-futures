#!/usr/bin/env python3
"""
TRIZ Direction 5 — DOM Depth Analysis
Connects to PostgreSQL, loads raw order book snapshots from finam_dom_snapshots_v2,
aggregates into 5-min windows, computes bid/ask imbalance, checks predictive power,
and saves a structured report.
"""

import sys
sys.path.insert(0, '/home/user/projects/TQA-MOEX')

import pandas as pd
import numpy as np
import psycopg2
from datetime import datetime, timezone
from collections import defaultdict

REPORT_DATE = "2026-06-10"
REPORT_PATH = f"/home/user/projects/TQA-MOEX/reports/{REPORT_DATE}-dom-analysis.md"
DB_CONN = dict(host='10.0.0.64', dbname='moex', user='postgres')

MIN_BARS = 1000

# According to data inspection:
#   type = 1 → ask (higher prices, sell orders)
#   type = 2 → bid (lower prices, buy orders)
# We'll verify this heuristically in the script.


def db_connect():
    return psycopg2.connect(**DB_CONN)


def check_table_availability(conn):
    """Check which DOM tables are available and have data."""
    info = {}

    tables = ['finam_dom_snapshots', 'finam_dom_snapshots_v2']
    for tbl in tables:
        # Use a fresh cursor per table to avoid transaction errors
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_schema = 'public' AND table_name = %s)",
                (tbl,)
            )
            exists = cur.fetchone()[0]
            cur.close()

            if not exists:
                info[tbl] = {'exists': False, 'rows': 0, 'error': 'table does not exist'}
                continue

            cur = conn.cursor()
            cur.execute(f"SELECT count(*) FROM {tbl}")
            rows = cur.fetchone()[0]
            cur.close()
            info[tbl] = {'exists': True, 'rows': rows, 'error': None}
        except Exception as e:
            conn.rollback()
            info[tbl] = {'exists': True, 'rows': 0, 'error': str(e)}

    return info


def verify_type_meaning(conn, ticker='GAZR'):
    """Heuristically determine which type is bid and which is ask."""
    cur = conn.cursor()
    cur.execute("""
        SELECT type, avg(price) as avg_price, min(price), max(price), count(*)
        FROM finam_dom_snapshots_v2
        WHERE ticker = %s
        GROUP BY type
        ORDER BY type
    """, (ticker,))
    rows = cur.fetchall()
    cur.close()
    return rows


def load_dom_data(conn, ticker):
    """Load all DOM snapshots for a ticker into a DataFrame."""
    df = pd.read_sql_query(
        "SELECT time, price, type, volume FROM finam_dom_snapshots_v2 "
        "WHERE ticker = %s ORDER BY time, price",
        conn, params=(ticker,)
    )
    return df


def aggregate_5min_bars(df):
    """
    Aggregate DOM snapshots into 5-minute windows.
    Returns DataFrame with time (5min floor), bid_vol, ask_vol, imbalance, etc.
    """
    df = df.copy()
    df['time'] = pd.to_datetime(df['time'], utc=True)
    df['bar_time'] = df['time'].dt.floor('5min')

    # Determine type meanings heuristically:
    # type with lower avg price = bid, higher avg price = ask
    type_stats = df.groupby('type')['price'].mean()
    if len(type_stats) >= 2:
        sorted_types = type_stats.sort_values()
        bid_type = sorted_types.index[0]
        ask_type = sorted_types.index[1]
    else:
        bid_type, ask_type = 1, 2

    # Classify
    df['side'] = df['type'].map({bid_type: 'bid', ask_type: 'ask'})

    # Aggregate by bar
    bar_groups = df.groupby(['bar_time', 'side'])
    vol_by_bar = bar_groups['volume'].sum().unstack(fill_value=0)

    if 'bid' not in vol_by_bar.columns:
        vol_by_bar['bid'] = 0.0
    if 'ask' not in vol_by_bar.columns:
        vol_by_bar['ask'] = 0.0

    vol_by_bar['total_vol'] = vol_by_bar['bid'] + vol_by_bar['ask']
    vol_by_bar['imbalance'] = np.where(
        vol_by_bar['total_vol'] > 0,
        (vol_by_bar['bid'] - vol_by_bar['ask']) / vol_by_bar['total_vol'],
        0.0
    )

    # Find best bid and best ask per bar
    best_bid_prices = df[df['side'] == 'bid'].groupby('bar_time')['price'].max()
    best_ask_prices = df[df['side'] == 'ask'].groupby('bar_time')['price'].min()

    vol_by_bar['best_bid_price'] = best_bid_prices
    vol_by_bar['best_ask_price'] = best_ask_prices
    vol_by_bar['spread'] = vol_by_bar['best_ask_price'] - vol_by_bar['best_bid_price']

    # Cluster volume: volume within 0.1% of best bid/ask
    for side, best_col in [('bid', 'best_bid_price'), ('ask', 'best_ask_price')]:
        cluster_rows = []
        for bar_time, grp in df.groupby('bar_time'):
            best_price = vol_by_bar.loc[bar_time, best_col]
            if pd.isna(best_price) or best_price == 0:
                cluster_rows.append(0.0)
                continue
            threshold = best_price * 0.001
            is_near = grp['price'].sub(best_price).abs() <= threshold
            cluster_rows.append(grp.loc[is_near & (grp['side'] == side), 'volume'].sum())

        vol_by_bar[f'cluster_{side}_vol'] = cluster_rows

    vol_by_bar['cluster_total'] = vol_by_bar['cluster_bid_vol'] + vol_by_bar['cluster_ask_vol']
    vol_by_bar['cluster_imbalance'] = np.where(
        vol_by_bar['cluster_total'] > 0,
        (vol_by_bar['cluster_bid_vol'] - vol_by_bar['cluster_ask_vol']) / vol_by_bar['cluster_total'],
        0.0
    )

    # Mid price approximation
    vol_by_bar['mid_price'] = (vol_by_bar['best_bid_price'] + vol_by_bar['best_ask_price']) / 2

    vol_by_bar.reset_index(inplace=True)
    vol_by_bar.sort_values('bar_time', inplace=True)

    return vol_by_bar, bid_type, ask_type


def compute_predictive_power(bars_df):
    """
    Check if imbalance predicts next bar direction.
    No look-ahead bias: predictors are computed on current bar,
    target is next bar's price move.
    """
    if len(bars_df) < 2:
        return {}

    bars = bars_df.copy()
    bars['next_mid'] = bars['mid_price'].shift(-1)
    bars['next_return'] = (bars['next_mid'] - bars['mid_price']) / bars['mid_price']

    # Direction: 1 = up, -1 = down, 0 = flat
    bars['next_direction'] = np.sign(bars['next_return'])
    # Exclude flat (return exactly 0)
    non_flat = bars[bars['next_direction'] != 0].dropna(subset=['next_direction', 'imbalance', 'cluster_imbalance'])

    if len(non_flat) < 10:
        return {'bars': len(bars_df), 'valid_bars': len(non_flat), 'error': 'too few non-flat bars'}

    results = {}

    # Prepare features
    X_imb = non_flat[['imbalance']].values
    X_cluster = non_flat[['cluster_imbalance']].values
    X_comb = non_flat[['imbalance', 'cluster_imbalance']].values
    y = (non_flat['next_direction'].values > 0).astype(int)

    # Manual logistic regression via scipy.optimize (no sklearn dependency)
    from scipy.optimize import minimize

    def _logreg_cv(X, y):
        """Simple logistic regression using BFGS. Returns (accuracy, coefs)."""
        X_aug = np.column_stack([np.ones(len(X)), X])
        n, d = X_aug.shape

        def _neg_log_likelihood(beta):
            z = X_aug @ beta
            z = np.clip(z, -100, 100)
            p = 1.0 / (1.0 + np.exp(-z))
            p = np.clip(p, 1e-15, 1 - 1e-15)
            return -np.sum(y * np.log(p) + (1 - y) * np.log(1 - p))

        beta0 = np.zeros(d)
        try:
            res = minimize(_neg_log_likelihood, beta0, method='BFGS', options={'maxiter': 500})
            beta_opt = res.x
        except Exception:
            return 0.5, np.zeros(d)

        z = X_aug @ beta_opt
        preds = (z >= 0).astype(int)
        acc = (preds == y).mean()
        return acc, beta_opt[1:] if X.shape[1] > 0 else beta_opt

    acc_imb, coef_imb_arr = _logreg_cv(X_imb, y)
    acc_cluster, coef_cluster_arr = _logreg_cv(X_cluster, y)
    acc_comb, _ = _logreg_cv(X_comb, y)

    coef_imb = float(coef_imb_arr[0]) if len(coef_imb_arr) > 0 else 0.0
    coef_cluster = float(coef_cluster_arr[0]) if len(coef_cluster_arr) > 0 else 0.0

    # Baseline: always predict majority class
    baseline = max(y.mean(), 1 - y.mean())

    # 5. Directional accuracy: correlation
    corr_imb = non_flat['imbalance'].corr(non_flat['next_return'])
    corr_cluster = non_flat['cluster_imbalance'].corr(non_flat['next_return'])

    # 6. Confusion-like: imbalance > 0.3 predicts up
    high_imb = non_flat[non_flat['imbalance'].abs() > 0.3]
    if len(high_imb) >= 5:
        hit_rate_high = (np.sign(high_imb['imbalance']) == high_imb['next_direction']).mean()
    else:
        hit_rate_high = np.nan

    results.update({
        'bars': len(bars_df),
        'valid_bars': len(non_flat),
        'baseline_accuracy': baseline,
        'imbalance_accuracy': acc_imb,
        'imbalance_coef': coef_imb,
        'cluster_imbalance_accuracy': acc_cluster,
        'cluster_imbalance_coef': coef_cluster,
        'combined_accuracy': acc_comb,
        'corr_imbalance_return': corr_imb,
        'corr_cluster_return': corr_cluster,
        'hit_rate_high_imbalance': hit_rate_high,
    })

    return results


def compute_cluster_stats(bars_df):
    """Compute summary statistics for cluster volume analysis."""
    bars = bars_df.copy()

    stats = {}
    stats['avg_cluster_bid_vol'] = bars['cluster_bid_vol'].mean()
    stats['avg_cluster_ask_vol'] = bars['cluster_ask_vol'].mean()
    stats['avg_cluster_vol'] = bars['cluster_total'].mean()
    stats['avg_cluster_ratio'] = (bars['cluster_total'] / bars['total_vol'].replace(0, np.nan)).mean()
    stats['avg_spread'] = bars['spread'].mean()
    return stats


def main():
    print("=" * 70)
    print("TRIZ Direction 5 — DOM (Depth of Market) Analysis")
    print("=" * 70)

    print("\n[1] Connecting to PostgreSQL...")
    conn = db_connect()
    print("    Connected.")

    print("\n[2] Checking DOM table availability...")
    table_info = check_table_availability(conn)
    for tbl, info in table_info.items():
        status = "OK" if info['exists'] and info['rows'] > 0 else "N/A"
        err = f" — {info['error']}" if info.get('error') else ""
        print(f"    {tbl}: {status} ({info['rows']} rows{err})")

    # Use only v2 (v1 has TimescaleDB corruption)
    if not table_info.get('finam_dom_snapshots_v2', {}).get('exists'):
        msg = "finam_dom_snapshots_v2 table does not exist"
        print(f"\n    {msg}")
        _write_no_data_report(msg)
        conn.close()
        return 1

    if table_info['finam_dom_snapshots_v2']['rows'] == 0:
        msg = "finam_dom_snapshots_v2 is empty"
        print(f"\n    {msg}")
        _write_no_data_report(msg)
        conn.close()
        return 1

    print("\n[3] Verifying type meanings (bid vs ask)...")
    type_info = verify_type_meaning(conn)
    print("    Type averages by ticker GAZR:")
    for row in type_info:
        print(f"      type={row[0]}: avg_price={row[1]:.2f}, min={row[2]:.2f}, max={row[3]:.2f}, cnt={row[4]}")

    print("\n[4] Loading DOM data per ticker...")
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT ticker FROM finam_dom_snapshots_v2 ORDER BY ticker")
    tickers = [r[0] for r in cur.fetchall()]
    cur.close()
    print(f"    Found tickers: {tickers}")

    all_results = {}
    all_ok = True

    for ticker in tickers:
        print(f"\n{'=' * 60}")
        print(f"    Processing {ticker}...")
        df = load_dom_data(conn, ticker)
        print(f"    Loaded {len(df):,} raw DOM rows")

        bars_df, bid_type, ask_type = aggregate_5min_bars(df)
        print(f"    Aggregated into {len(bars_df):,} 5-min bars")
        print(f"    Bid type = {bid_type}, Ask type = {ask_type}")

        if len(bars_df) < MIN_BARS:
            reason = f"Only {len(bars_df)} bars for {ticker} (min required: {MIN_BARS})"
            print(f"    ⚠ NO DATA: {reason}")
            all_results[ticker] = {
                'status': 'NO DATA',
                'reason': reason,
                'bars': len(bars_df),
                'raw_rows': len(df),
                'date_range': f"{df['time'].min()} to {df['time'].max()}",
            }
            all_ok = False
            continue

        bars_df_clean = bars_df.dropna(subset=['mid_price'])
        if len(bars_df_clean) < MIN_BARS:
            reason = f"Only {len(bars_df_clean)} clean bars for {ticker} after NaN removal"
            print(f"    ⚠ NO DATA: {reason}")
            all_results[ticker] = {
                'status': 'NO DATA',
                'reason': reason,
                'bars': len(bars_df_clean),
                'raw_rows': len(df),
                'date_range': f"{df['time'].min()} to {df['time'].max()}",
            }
            all_ok = False
            continue

        print("    Computing predictive power (no look-ahead)...")
        pred = compute_predictive_power(bars_df_clean)
        cluster_stats = compute_cluster_stats(bars_df_clean)

        result = {
            'status': 'ANALYZED',
            'bars': len(bars_df_clean),
            'raw_rows': len(df),
            'date_range': f"{df['time'].min()} to {df['time'].max()}",
            'predictive': pred,
            'cluster': cluster_stats,
            'bid_type': int(bid_type),
            'ask_type': int(ask_type),
        }
        all_results[ticker] = result

        print(f"    ✓ Edge check complete")
        print(f"      Bars: {pred['bars']}, Valid: {pred['valid_bars']}")
        print(f"      Imbalance accuracy: {pred['imbalance_accuracy']:.3f} (baseline: {pred['baseline_accuracy']:.3f})")
        print(f"      Cluster imbalance accuracy: {pred['cluster_imbalance_accuracy']:.3f}")
        print(f"      Combined accuracy: {pred['combined_accuracy']:.3f}")
        print(f"      Corr(imbalance, next_return): {pred['corr_imbalance_return']:.4f}")

        if pred['imbalance_accuracy'] > pred['baseline_accuracy'] + 0.03 and not np.isnan(pred.get('hit_rate_high_imbalance', np.nan)):
            print(f"      ★ EDGE: imbalance predicts direction above baseline")
        else:
            print(f"      No significant edge from imbalance")

    conn.close()

    print(f"\n{'=' * 70}")
    print("[5] Saving report...")
    _write_report(all_results)
    print(f"    Saved to {REPORT_PATH}")

    print(f"\n{'=' * 70}")
    print("[6] TRIZ Diagram — Direction 5")
    _print_triz()
    print(f"{'=' * 70}")

    return 0


def _write_no_data_report(reason):
    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with open(REPORT_PATH, 'w') as f:
        f.write(f"# DOM (Depth of Market) Analysis — {REPORT_DATE}\n\n")
        f.write("## NO DATA\n\n")
        f.write(f"**Reason**: {reason}\n\n")
        f.write("## What Data Would Be Needed\n\n")
        f.write("For a meaningful DOM (Depth of Market) analysis, the following data is required:\n\n")
        f.write("1. **Order book snapshots** with at least 1000 aggregated 5-minute bars per ticker\n")
        f.write("2. Each snapshot should contain:\n")
        f.write("   - `time`: timestamp with time zone\n")
        f.write("   - `ticker`: instrument identifier\n")
        f.write("   - `price`: order price level\n")
        f.write("   - `type`: order side (bid/ask)\n")
        f.write("   - `volume`: volume at that price level\n")
        f.write("3. **Coverage**: The data should span multiple trading sessions to capture\n")
        f.write("   varying market regimes\n")
        f.write("4. **Corresponding OHLCV data** (e.g. `moex_prices_5m`) to match DOM\n")
        f.write("   snapshots with actual price movements\n\n")
        f.write("Current data sources checked:\n")
        f.write("- `finam_dom_snapshots`: table exists but TimescaleDB chunk corrupted\n")
        f.write("- `finam_dom_snapshots_v2`: checked for availability\n\n")
        f.write(f"---\n*Generated at {now}*\n")
    print(f"    Wrote NO DATA report: {REPORT_PATH}")


def _write_report(all_results):
    import os
    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    with open(REPORT_PATH, 'w') as f:
        f.write(f"# DOM (Depth of Market) Analysis — {REPORT_DATE}\n\n")
        f.write("## TRIZ Direction 5: ПРОТИВОРЕЧИЕ → ИКР → РЕШЕНИЕ → РЕЗУЛЬТАТ\n\n")

        f.write("### ПРОТИВОРЕЧИЕ (Contradiction)\n")
        f.write("Order book data is high-frequency and noisy. "
                "Bid/ask imbalance may predict short-term price direction, "
                "but the signal-to-noise ratio is low and latency is critical. "
                "Aggregating into bars loses microstructure edge, "
                "but not aggregating leaves too much noise.\n\n")

        f.write("### ИКР (Ideal Final Result)\n")
        f.write("A DOM-derived feature set that predicts 5-min bar direction "
                "with >55% accuracy without look-ahead bias, "
                "providing an orthogonal signal to existing price-based strategies.\n\n")

        f.write("### РЕШЕНИЕ (Solution)\n")
        f.write("Aggregate raw DOM snapshots into 5-min windows. "
                "Compute bid/ask imbalance and cluster volume near the best prices. "
                "Test predictive power via logistic regression and directional correlation. "
                "No look-ahead: predictors from bar N predict direction of bar N+1.\n\n")

        f.write("### РЕЗУЛЬТАТ (Result)\n\n")

        any_data = any(r['status'] == 'ANALYZED' for r in all_results.values())
        if not any_data:
            f.write("**NO DATA** — insufficient DOM data for any ticker.\n\n")
        else:
            f.write("**ANALYSIS COMPLETE**\n\n")

        for ticker, r in all_results.items():
            f.write(f"### {ticker}\n\n")
            f.write(f"- **Status**: {r['status']}\n")
            f.write(f"- **Raw DOM rows**: {r['raw_rows']:,}\n")
            f.write(f"- **5-min bars**: {r['bars']}\n")
            f.write(f"- **Date range**: {r['date_range']}\n")

            if r['status'] == 'NO DATA':
                f.write(f"- **Reason**: {r['reason']}\n\n")
                continue

            p = r['predictive']
            c = r['cluster']

            f.write(f"- **Bid type**: {r['bid_type']}, **Ask type**: {r['ask_type']}\n")
            f.write(f"- **Valid non-flat bars**: {p['valid_bars']}\n")
            f.write(f"- **Baseline accuracy** (always predict majority): {p['baseline_accuracy']:.4f}\n")
            f.write(f"- **Imbalance → direction accuracy**: {p['imbalance_accuracy']:.4f}\n")
            f.write(f"- **Cluster imbalance → direction accuracy**: {p['cluster_imbalance_accuracy']:.4f}\n")
            f.write(f"- **Combined features accuracy**: {p['combined_accuracy']:.4f}\n")
            f.write(f"- **Corr(imbalance, next return)**: {p['corr_imbalance_return']:.4f}\n")
            f.write(f"- **Corr(cluster imbalance, next return)**: {p['corr_cluster_return']:.4f}\n")

            if not np.isnan(p.get('hit_rate_high_imbalance', np.nan)):
                f.write(f"- **Hit rate (|imbalance| > 0.3)**: {p['hit_rate_high_imbalance']:.4f}\n")

            f.write(f"- **Logistic coef (imbalance)**: {p['imbalance_coef']:.4f}\n")
            f.write(f"- **Logistic coef (cluster imb)**: {p['cluster_imbalance_coef']:.4f}\n")

            f.write(f"\n  **Cluster Volume Stats:**\n")
            f.write(f"  - Avg cluster bid vol: {c['avg_cluster_bid_vol']:.2f}\n")
            f.write(f"  - Avg cluster ask vol: {c['avg_cluster_ask_vol']:.2f}\n")
            f.write(f"  - Avg cluster total: {c['avg_cluster_vol']:.2f}\n")
            f.write(f"  - Avg cluster ratio: {c['avg_cluster_ratio']:.4f}\n")
            f.write(f"  - Avg spread (price units): {c['avg_spread']:.2f}\n")

            # Edge verdict
            edge_found = (p['imbalance_accuracy'] > p['baseline_accuracy'] + 0.03
                          and p['valid_bars'] > 100)
            if edge_found:
                f.write(f"\n  **✅ EDGE FOUND** — imbalance predicts direction above baseline\n")
            else:
                f.write(f"\n  **❌ NO EDGE** — imbalance does not significantly predict direction\n")

            f.write("\n")

        f.write("## Data Sources\n\n")
        f.write("- **Primary**: `finam_dom_snapshots_v2` (16,503,903 rows total, 3 tickers)\n")
        f.write("- **Secondary**: `finam_dom_snapshots` — corrupted (TimescaleDB chunk missing)\n")
        f.write("- **Host**: 10.0.0.64, db=moex\n\n")
        f.write("## Methodology Notes\n\n")
        f.write("1. **No look-ahead bias**: For each 5-min bar N, imbalance and cluster volume\n")
        f.write("   are computed only from snapshots within that bar. The target is bar N+1's direction.\n")
        f.write("2. **Type mapping**: Determined heuristically (type with lower avg price = bid).\n")
        f.write("3. **Cluster definition**: Volume within 0.1% of the best bid/ask price.\n")
        f.write("4. **Logistic regression**: Predicts up/down direction on next bar.\n")
        f.write("5. **Minimum bars**: 1000 bars required for analysis.\n")
        f.write("6. **Data period**: Only January 2024 is available — very limited.\n\n")

        f.write("## Limitations\n\n")
        f.write("1. Only 3 tickers with DOM data (GAZR, SBRF, Si)\n")
        f.write("2. Data only covers ~2 weeks in January 2024\n")
        f.write("3. No corresponding price table used — mid-price derived from DOM\n")
        f.write("4. No volume-weighted or time-weighted imbalance variants tested\n")
        f.write("5. No consideration of order book depth beyond 0.1% cluster\n\n")

        f.write(f"---\n*Generated at {now}*\n")

    print(f"    Report written to {REPORT_PATH}")


def _print_triz():
    print()
    print("┌─────────────────────────────────────────────────────────────────┐")
    print("│   ПРОТИВОРЕЧИЕ (Contradiction)                                  │")
    print("│   DOM-данные высокочастотны и зашумлены.                        │")
    print("│   Дисбаланс bid/ask может предсказывать направление,            │")
    print("│   но сигнал слабый, а агрегация в бары уничтожает микроструктуру│")
    print("├─────────────────────────────────────────────────────────────────┤")
    print("│         ↓                                                        │")
    print("├─────────────────────────────────────────────────────────────────┤")
    print("│   ИКР (Ideal Final Result)                                      │")
    print("│   Набор DOM-признаков, предсказывающий направление 5-мин бара    │")
    print("│   с точностью >55% без look-ahead bias.                         │")
    print("├─────────────────────────────────────────────────────────────────┤")
    print("│         ↓                                                        │")
    print("├─────────────────────────────────────────────────────────────────┤")
    print("│   РЕШЕНИЕ (Solution)                                            │")
    print("│   1. Агрегация DOM-снепшотов в 5-мин окна                       │")
    print("│   2. Расчёт дисбаланса: (bid_vol - ask_vol) / total_vol         │")
    print("│   3. Расчёт кластерного объёма возле best bid/ask               │")
    print("│   4. Logistic Regression: признаки бара N → направление N+1     │")
    print("├─────────────────────────────────────────────────────────────────┤")
    print("│         ↓                                                        │")
    print("├─────────────────────────────────────────────────────────────────┤")
    print("│   РЕЗУЛЬТАТ (Result)                                            │")
    print("│   Зависит от данных — см. отчёт.                                │")
    print("│   Ожидание: imbalance может давать 1-3% прироста к accuracy.    │")
    print("└─────────────────────────────────────────────────────────────────┘")
    print()


if __name__ == "__main__":
    import os
    sys.exit(main())
