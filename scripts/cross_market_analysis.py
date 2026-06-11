#!/usr/bin/env python3
"""
Cross-Market Correlation Analysis — TRIZ Direction 1
Connects to PostgreSQL, loads OHLCV 5m data for 8 key MOEX tickers,
computes rolling correlations, detects divergence events, and checks
for mean-reversion edges.
"""

import sys
sys.path.insert(0, '/home/user/projects/TQA-MOEX')

import pandas as pd
import numpy as np
import psycopg2
from datetime import datetime
from itertools import combinations
import os

REPORT_DATE = "2026-06-10"
REPORT_PATH = f"/home/user/projects/TQA-MOEX/reports/{REPORT_DATE}-cross-market.md"

TICKERS = ['Si', 'BR', 'RI', 'GL', 'ED', 'FF', 'AU', 'CNYRUBF']
DB_CONN = dict(host='10.0.0.64', dbname='moex', user='postgres')
ROLLING_PERIODS = 60
LOOKAHEAD_BARS = [5, 10, 20]


def load_data():
    conn = psycopg2.connect(**DB_CONN)
    data = {}
    for t in TICKERS:
        df = pd.read_sql_query(
            "SELECT time, close FROM moex_prices_5m WHERE symbol = %s ORDER BY time",
            conn, params=(t,)
        )
        df.set_index('time', inplace=True)
        df.columns = [f'close_{t}']
        data[t] = df
    conn.close()
    return data


def detect_divergences(corr_series, z_thresh=2.0, min_periods=60):
    corr_clean = corr_series.dropna()
    if len(corr_clean) < min_periods + 1:
        return pd.DataFrame()
    exp_mean = corr_clean.expanding(min_periods=min_periods).mean()
    exp_std = corr_clean.expanding(min_periods=min_periods).std()
    exp_std = exp_std.replace(0, np.nan)
    z = (corr_clean - exp_mean) / exp_std
    flags = z.abs() > z_thresh
    return pd.DataFrame({
        'corr': corr_clean,
        'z_score': z,
        'exp_mean': exp_mean,
        'exp_std': exp_std,
        'divergence': flags
    })


def find_divergence_events(div_df):
    events = []
    in_event = False
    for idx, row in div_df.iterrows():
        if row['divergence'] and not in_event:
            events.append((idx, row['z_score'], row['corr']))
            in_event = True
        elif not row['divergence']:
            in_event = False
    return events


def main():
    print("=" * 70)
    print("TRIZ Direction 1 — Cross-Market Correlation & Mean Reversion")
    print("=" * 70)

    print("\n[1] Loading data from PostgreSQL...")
    data = load_data()
    print(f"    Loaded {len(data)} tickers:")
    for t in TICKERS:
        print(f"      {t}: {len(data[t])} rows  [{data[t].index[0]} .. {data[t].index[-1]}]")

    pairs = list(combinations(TICKERS, 2))
    print(f"\n[2] Processing {len(pairs)} pairs (per-pair alignment, rolling 60-bar correlation)...")

    all_results = []
    edge_found = False
    edge_details = []

    for a, b in pairs:
        pair_df = pd.concat([data[a], data[b]], axis=1, join='inner')
        pair_df.sort_index(inplace=True)
        pair_len = len(pair_df)
        if pair_len < ROLLING_PERIODS + 200:
            print(f"    {a}-{b}: {pair_len} aligned bars — skipping (insufficient data)")
            continue

        pair_df['corr'] = pair_df[f'close_{a}'].rolling(
            ROLLING_PERIODS
        ).corr(pair_df[f'close_{b}'])

        corr_series = pair_df['corr'].dropna()
        div_df = detect_divergences(corr_series)
        if div_df.empty:
            print(f"    {a}-{b}: {pair_len} bars, corr {pair_len - ROLLING_PERIODS + 1} obs — no divergences")
            continue

        events = find_divergence_events(div_df)
        print(f"    {a}-{b}: {pair_len} bars, {len(events)} divergence events", end="")

        for lookahead in LOOKAHEAD_BARS:
            wins = 0
            total = 0
            for div_time, z_score, corr_val in events:
                idx_loc = pair_df.index.get_loc(div_time)
                if idx_loc + lookahead >= pair_len:
                    continue

                prior_ret_a = (pair_df.iloc[idx_loc][f'close_{a}'] /
                               pair_df.iloc[max(0, idx_loc - ROLLING_PERIODS + 1)][f'close_{a}'] - 1)
                prior_ret_b = (pair_df.iloc[idx_loc][f'close_{b}'] /
                               pair_df.iloc[max(0, idx_loc - ROLLING_PERIODS + 1)][f'close_{b}'] - 1)

                if prior_ret_a < prior_ret_b:
                    under = a
                    other = b
                    under_prior = prior_ret_a
                    other_prior = prior_ret_b
                else:
                    under = b
                    other = a
                    under_prior = prior_ret_b
                    other_prior = prior_ret_a

                fwd_under = (pair_df.iloc[idx_loc + lookahead][f'close_{under}'] /
                             pair_df.iloc[idx_loc][f'close_{under}'] - 1)
                fwd_other = (pair_df.iloc[idx_loc + lookahead][f'close_{other}'] /
                             pair_df.iloc[idx_loc][f'close_{other}'] - 1)

                reverted = fwd_under > fwd_other
                if reverted:
                    wins += 1
                total += 1

            if total > 0:
                wr = wins / total * 100
                all_results.append({
                    'pair': f'{a}-{b}',
                    'lookahead': lookahead,
                    'events': total,
                    'wins': wins,
                    'win_rate': wr
                })
                if wr > 55.0:
                    edge_found = True
                    edge_details.append(f"{a}-{b} (L{lookahead}: {wr:.1f}%, {wins}/{total})")

        print(f"  — done")

    print("\n[3] Generating report...")
    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)

    min_ts = min(d.index[0] for d in data.values())
    max_ts = max(d.index[-1] for d in data.values())
    tot_rows = sum(len(d) for d in data.values())

    lines = []
    lines.append(f"# Cross-Market Correlation Analysis — {REPORT_DATE}")
    lines.append("")
    lines.append("## TRIZ Direction 1: ПРОТИВОРЕЧИЕ → ИКР → РЕШЕНИЕ → РЕЗУЛЬТАТ")
    lines.append("")
    lines.append("### ПРОТИВОРЕЧИЕ (Contradiction)")
    lines.append("Требуется одновременно высокая точность входа и низкая задержка сигнала. "
                 "Корреляция между инструментами нестабильна: частые ложные схождения/расхождения "
                 "маскируют истинные точки входа.")
    lines.append("")
    lines.append("### ИКР (Ideal Final Result)")
    lines.append("Система сама определяет моменты статистически значимого расхождения корреляции "
                 "и подтверждает mean-reversion паттерн с win rate > 55%.")
    lines.append("")
    lines.append("### РЕШЕНИЕ (Solution)")
    lines.append("Rolling корреляция 60 баров (5 часов) по 8 ключевым тикерам MOEX. "
                 "Выявление расхождений > 2σ от среднего. Проверка возврата отстающего инструмента "
                 "за 5/10/20 баров.")
    lines.append("")
    lines.append("### РЕЗУЛЬТАТ (Result)")
    lines.append("")
    if edge_found:
        lines.append(f"**✅ EDGE FOUND** — {len(edge_details)} edge(s) detected:")
        for ed in edge_details:
            lines.append(f"  - {ed}")
    else:
        lines.append("❌ No edge found above 55% win rate threshold.")
    lines.append("")

    lines.append("## Data Summary")
    lines.append("")
    lines.append(f"- **Data source**: `moex_prices_5m` on 10.0.0.64")
    lines.append(f"- **Tickers**: {', '.join(TICKERS)}")
    lines.append(f"- **Period**: {min_ts} to {max_ts}")
    lines.append(f"- **Total rows (all tickers)**: {tot_rows}")
    lines.append(f"- **Correlation window**: {ROLLING_PERIODS} bars (5 hours)")
    lines.append(f"- **Reversion windows**: {', '.join(f'{b} bars' for b in LOOKAHEAD_BARS)}")
    lines.append("")

    lines.append("## Pair-by-Pair Results (sorted by win rate)")
    lines.append("")
    lines.append("| Pair | Lookahead | Events | Wins | Win Rate |")
    lines.append("|------|-----------|--------|------|----------|")
    for r in sorted(all_results, key=lambda x: x['win_rate'], reverse=True):
        lines.append(f"| {r['pair']} | {r['lookahead']} bar | {r['events']} | {r['wins']} | {r['win_rate']:.1f}% |")
    lines.append("")
    lines.append("---")
    lines.append(f"*Generated at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*")

    report_text = '\n'.join(lines)
    with open(REPORT_PATH, 'w') as f:
        f.write(report_text)
    print(f"    Written to {REPORT_PATH}")

    print("\n" + "=" * 70)
    print("TRIZ DIAGRAM — Cross-Market Correlation Analysis")
    print("=" * 70)
    print()
    print("┌──────────────────────────────────────────────────────────────┐")
    print("│                     ПРОТИВОРЕЧИЕ                             │")
    print("│  Точность входа ↔ Задержка сигнала                          │")
    print("│  Корреляция нестабильна → ложные схождения                  │")
    print("└───────────────────────┬──────────────────────────────────────┘")
    print("                        │")
    print("                        ▼")
    print("┌──────────────────────────────────────────────────────────────┐")
    print("│                       ИКР                                     │")
    print("│  Система сама находит статистически значимые расхождения     │")
    print("│  и подтверждает mean-reversion с win rate > 55%              │")
    print("└───────────────────────┬──────────────────────────────────────┘")
    print("                        │")
    print("                        ▼")
    print("┌──────────────────────────────────────────────────────────────┐")
    print("│                     РЕШЕНИЕ                                   │")
    print("│  Rolling 60-bar correlation на 8 тикерах MOEX                │")
    print("│  Детекция расхождений > 2σ от среднего                       │")
    print("│  Проверка возврата отстающего за 5/10/20 баров               │")
    print("└───────────────────────┬──────────────────────────────────────┘")
    print("                        │")
    print("                        ▼")
    print("┌──────────────────────────────────────────────────────────────┐")
    print("│                     РЕЗУЛЬТАТ                                 │")
    if edge_found:
        print(f"│  ✅ EDGE FOUND — {len(edge_details)} edge(s)                           │")
        for ed in edge_details:
            pad = 64 - len(ed) - 4
            print(f"│    {ed}{' ' * pad}│")
    else:
        print("│  ❌ No edge detected above 55% win rate threshold           │")
    print("└──────────────────────────────────────────────────────────────┘")
    print()
    print("=" * 70)

    print(f"\nFull report: {REPORT_PATH}")
    print("=" * 70)


if __name__ == '__main__':
    main()
