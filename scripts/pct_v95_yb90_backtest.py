#!/usr/bin/env python3
"""
PCT_v95_yb90 Backtest: volume >= 95%ile AND yur_buy >= 90%ile (rolling 20).
Entry: next bar open. Exit: N bars or 2% stop-loss.
Walk-forward 4 folds. Per-ticker capital = 100K RUB.
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import clickhouse_connect
import pandas as pd
import numpy as np
from config import CH_HOST, CH_PORT, CH_DB

OUT = 'reports/oi_volume_backtest'
os.makedirs(OUT, exist_ok=True)

ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)

TICKERS = ["BM", "DX", "IB", "GD", "CE", "AF", "Eu", "SN", "AL", "AU"]
CAPITAL = 100_000
MAX_NOTIONAL_MULTIPLE = 10
BARS_PER_YEAR = 30240  # ~10h * 60m / 5m * 252d

CONTRACT_SIZES = {
    'Eu': 1000, 'AF': 100, 'CE': 100, 'GD': 10,
    'DX': 10000, 'BM': 10, 'IB': 100, 'AL': 25, 'AU': 1,
}
FUTURES_TICKERS = {'Eu', 'AF', 'CE', 'GD', 'DX', 'BM', 'IB', 'AL', 'AU'}

FOLDS = [
    ('2024-07-01', '2025-01-01', '2025-01-01', '2025-07-01'),
    ('2025-01-01', '2025-07-01', '2025-07-01', '2026-01-01'),
    ('2025-07-01', '2026-01-01', '2026-01-01', '2026-04-01'),
    ('2026-01-01', '2026-04-01', '2026-04-01', '2026-06-01'),
]
FOLD_NAMES = ['2025H1', '2025H2', '2026Q1', '2026Q2']
HOLD_PERIODS = [40, 80]
STOP_LOSS = 0.02
VOL_THRESHOLD = 0.95
YUR_THRESHOLD = 0.90
ROLLING_WINDOW = 20

_cache = {}


def load_ticker_data(ticker):
    if ticker in _cache:
        return _cache[ticker]
    rows = ch.query("""
        SELECT p.time, p.open, p.high, p.low, p.close, p.volume,
               o.yur_buy
        FROM moex.prices_5m AS p
        INNER JOIN moex.prices_5m_oi AS o
          ON o.symbol = p.symbol AND o.time = p.time
        WHERE p.symbol = %(t)s AND p.volume > 0 AND o.total_oi > 0
        ORDER BY p.time
    """, parameters={'t': ticker}).result_rows
    if not rows:
        _cache[ticker] = None
        return None
    df = pd.DataFrame(rows, columns=[
        'time', 'open', 'high', 'low', 'close', 'volume', 'yur_buy'
    ])
    vol_rank = df['volume'].rolling(ROLLING_WINDOW, min_periods=ROLLING_WINDOW).rank(pct=True)
    yur_rank = df['yur_buy'].rolling(ROLLING_WINDOW, min_periods=ROLLING_WINDOW).rank(pct=True)
    df['vol_pct'] = vol_rank
    df['yur_pct'] = yur_rank
    df['signal'] = (df['vol_pct'] >= VOL_THRESHOLD) & (df['yur_pct'] >= YUR_THRESHOLD)
    _cache[ticker] = df
    return df


def get_go(ticker):
    if ticker not in CONTRACT_SIZES:
        return 0
    df = load_ticker_data(ticker)
    if df is None or len(df) == 0:
        return 0
    return df['close'].mean() * CONTRACT_SIZES[ticker]


def calc_commission(ticker, entry_price, contracts):
    if ticker in FUTURES_TICKERS:
        return contracts * 2
    return max(10.0, entry_price * 100 * contracts * 0.001) * 2


def size_position(ticker, entry_price, capital):
    if ticker in FUTURES_TICKERS:
        go = get_go(ticker)
        if go <= 0:
            return 0
        cs = CONTRACT_SIZES[ticker]
        raw = max(1, int(capital / go)) if go > 0 else 1
        notional = entry_price * cs * raw
        if notional > capital * MAX_NOTIONAL_MULTIPLE:
            return 0
        return raw
    else:
        raw = max(1, int(capital / (entry_price * 100)))
        notional = entry_price * 100 * raw
        if notional > capital * MAX_NOTIONAL_MULTIPLE:
            return 0
        return raw


def run_backtest(df, ticker, hold, sl):
    if df is None or len(df) < ROLLING_WINDOW + 2:
        return []

    trades = []
    capital = float(CAPITAL)
    in_position = False
    entry_price = 0.0
    entry_idx = -1
    contracts = 0
    entry_time = None

    for i in range(1, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i - 1]

        if capital <= 0:
            break

        if in_position:
            bars_held = i - entry_idx
            stop_level = entry_price * (1 - sl)
            exit_reason = None
            exit_price = 0.0

            if row['low'] <= stop_level:
                exit_reason = 'stop'
                exit_price = row['close']
            elif bars_held >= hold:
                exit_reason = 'time'
                exit_price = row['close']

            if exit_reason is not None:
                if ticker in FUTURES_TICKERS:
                    pnl = (exit_price - entry_price) * CONTRACT_SIZES[ticker] * contracts
                else:
                    pnl = (exit_price - entry_price) * 100 * contracts
                comm = calc_commission(ticker, entry_price, contracts)
                net_pnl = pnl - comm
                capital += net_pnl

                trades.append({
                    'entry_time': str(entry_time),
                    'exit_time': str(row['time']),
                    'entry_price': round(entry_price, 2),
                    'exit_price': round(exit_price, 2),
                    'contracts': contracts,
                    'pnl': round(pnl, 2),
                    'commission': round(comm, 2),
                    'net_pnl': round(net_pnl, 2),
                    'exit_reason': exit_reason,
                    'bars_held': bars_held,
                })
                in_position = False

        if not in_position and prev['signal']:
            entry_price = row['open']
            if entry_price <= 0:
                continue
            contracts = size_position(ticker, entry_price, capital)
            if contracts == 0:
                continue
            entry_time = row['time']
            entry_idx = i
            in_position = True

    return trades


def compute_metrics(trades, initial_cap, n_bars=0):
    if not trades:
        return {
            'trades': 0, 'return_pct': 0.0, 'max_dd_pct': 0.0,
            'calmar': 0.0, 'sharpe': 0.0, 'win_rate': 0.0,
            'profit_factor': 0.0, 'total_pnl': 0.0,
            'total_commission': 0.0, 'net_pnl': 0.0,
            'final_capital': initial_cap, 'avg_bars_held': 0.0,
        }

    total_pnl = sum(t['pnl'] for t in trades)
    total_comm = sum(t['commission'] for t in trades)
    net_pnl = sum(t['net_pnl'] for t in trades)
    final_cap = initial_cap + net_pnl
    ret_pct = (final_cap / initial_cap - 1) * 100 if initial_cap > 0 else 0

    wins = [t for t in trades if t['net_pnl'] > 0]
    losses = [t for t in trades if t['net_pnl'] <= 0]
    win_rate = len(wins) / len(trades) * 100 if trades else 0
    gross_win = sum(t['net_pnl'] for t in wins) if wins else 0
    gross_loss = abs(sum(t['net_pnl'] for t in losses)) if losses else 0
    profit_factor = gross_win / gross_loss if gross_loss > 0 else (gross_win if gross_win > 0 else 0)

    cum = np.cumsum([t['net_pnl'] for t in trades])
    eq = np.insert(cum, 0, 0) + initial_cap
    running_max = np.maximum.accumulate(eq)
    dd = (running_max - eq) / running_max * 100
    max_dd = float(np.max(dd)) if len(dd) > 0 else 0
    calmar = ret_pct / max_dd if max_dd > 0 else 0

    trade_rets = np.array([t['net_pnl'] for t in trades]) / initial_cap
    sharpe = 0.0
    if len(trade_rets) > 1 and np.std(trade_rets, ddof=1) > 1e-10:
        years = max(n_bars / BARS_PER_YEAR, 1/252)
        trades_per_year = len(trades) / years
        rf_per_trade = 0.10 / max(trades_per_year, 1)
        excess = trade_rets - rf_per_trade
        sharpe = float(np.mean(excess) / np.std(excess, ddof=1) * np.sqrt(trades_per_year))

    avg_bars = float(np.mean([t['bars_held'] for t in trades]))

    return {
        'trades': len(trades),
        'return_pct': round(ret_pct, 2),
        'max_dd_pct': round(max_dd, 2),
        'calmar': round(calmar, 4),
        'sharpe': round(sharpe, 4),
        'win_rate': round(win_rate, 1),
        'profit_factor': round(profit_factor, 2),
        'total_pnl': round(total_pnl, 2),
        'total_commission': round(total_comm, 2),
        'net_pnl': round(net_pnl, 2),
        'final_capital': round(final_cap, 2),
        'avg_bars_held': round(avg_bars, 1),
    }


def main():
    print("=" * 60)
    print("  PCT_v95_yb90 Backtest")
    print("=" * 60)
    print(f"Tickers: {TICKERS}")
    print(f"Capital: {CAPITAL:,} RUB/ticker  MaxNotionalMultiple: {MAX_NOTIONAL_MULTIPLE}x")
    print(f"Signal: vol >= {VOL_THRESHOLD*100:.0f}%ile & yur_buy >= {YUR_THRESHOLD*100:.0f}%ile (w={ROLLING_WINDOW})")
    print(f"Holds: {HOLD_PERIODS}  Stop: {STOP_LOSS*100:.0f}%")

    all_results = {}

    for ticker in TICKERS:
        print(f"\n  --- {ticker} ---")
        df = load_ticker_data(ticker)
        if df is None or len(df) < ROLLING_WINDOW + 2:
            print(f"  SKIP: insufficient data")
            continue
        print(f"  {len(df):,} bars | {df['time'].min()} .. {df['time'].max()}")

        go = get_go(ticker)
        cs = CONTRACT_SIZES.get(ticker, '-')
        if go > 0:
            print(f"  GO ~ {go:,.0f} | cs={cs}")
            if go >= CAPITAL:
                print(f"  Note: GO > capital, position cap may limit trades")
        else:
            print(f"  SN stock")

        ticker_results = {}
        for hold in HOLD_PERIODS:
            fold_results = []
            all_trades = []

            for fi, (_, _, test_start, test_end) in enumerate(FOLDS):
                test_df = df[(df['time'] >= test_start) & (df['time'] < test_end)].copy()
                n_bars_test = len(test_df)
                if n_bars_test < ROLLING_WINDOW + 5:
                    fr = {'fold': FOLD_NAMES[fi], 'trades': 0, 'return_pct': 0.0,
                          'max_dd_pct': 0.0, 'calmar': 0.0, 'sharpe': 0.0,
                          'win_rate': 0.0, 'profit_factor': 0.0,
                          'total_pnl': 0.0, 'total_commission': 0.0,
                          'net_pnl': 0.0, 'final_capital': CAPITAL, 'avg_bars_held': 0.0}
                    fold_results.append(fr)
                    continue

                trades = run_backtest(test_df, ticker, hold, STOP_LOSS)
                metrics = compute_metrics(trades, CAPITAL, n_bars=n_bars_test)
                metrics['fold'] = FOLD_NAMES[fi]
                fold_results.append(metrics)
                all_trades.extend(trades)

            combined = compute_metrics(all_trades, CAPITAL, n_bars=sum(
                len(df[(df['time'] >= FOLDS[fi][2]) & (df['time'] < FOLDS[fi][3])])
                for fi in range(4) if len(df[(df['time'] >= FOLDS[fi][2]) & (df['time'] < FOLDS[fi][3])]) >= ROLLING_WINDOW + 5
            ))
            ticker_results[hold] = {
                'folds': fold_results,
                'combined': combined,
                'trades': all_trades,
            }
            n = sum(f['trades'] for f in fold_results)
            tr = sum(f['return_pct'] for f in fold_results)
            dd = max(f['max_dd_pct'] for f in fold_results)
            print(f"    h={hold:2d}: {n:>3} tr | ret={tr:>+7.2f}% | DD={dd:.1f}%")

        all_results[ticker] = ticker_results

    print(f"\n  Generating report...")

    results_json = {'tickers': {}, 'summary': {}}
    md = []
    md.append("# Backtest Report: PCT_v95_yb90\n")
    md.append(f"**Signal:** volume >= {VOL_THRESHOLD*100:.0f}%ile AND yur_buy >= {YUR_THRESHOLD*100:.0f}%ile (rolling {ROLLING_WINDOW})")
    md.append(f"**Entry:** next bar open  **Exit:** {HOLD_PERIODS} bars or {STOP_LOSS*100:.0f}% stop-loss")
    md.append(f"**Capital:** {CAPITAL:,} RUB per ticker  **Max notional/capital:** {MAX_NOTIONAL_MULTIPLE}x")
    md.append(f"**Tickers:** {', '.join(TICKERS)}")
    md.append(f"**Walk-forward:** {', '.join(FOLD_NAMES)}\n")
    md.append("## Overall Results\n")
    md.append("| Hold | Trades | Return% | Max DD% | Calmar | Sharpe | WR% | PF | Comm |")
    md.append("|------|-------:|--------:|--------:|-------:|------:|----:|---:|-----:|")

    for hold in HOLD_PERIODS:
        all_t = []
        total_bars = 0
        for tk, tres in all_results.items():
            if hold in tres:
                all_t.extend(tres[hold]['trades'])
                for fi, (_, _, ts, te) in enumerate(FOLDS):
                    df_tk = load_ticker_data(tk)
                    if df_tk is not None:
                        total_bars += len(df_tk[(df_tk['time'] >= ts) & (df_tk['time'] < te)])
        if all_t:
            c = compute_metrics(all_t, CAPITAL, n_bars=total_bars)
            md.append(
                f"| {hold}b | {c['trades']} | {c['return_pct']:>+7.2f}% | {c['max_dd_pct']:.2f}% | "
                f"{c['calmar']:.4f} | {c['sharpe']:.4f} | {c['win_rate']:.1f}% | {c['profit_factor']:.2f} | {c['total_commission']:>8.0f} |"
            )
            results_json['summary'][f'hold_{hold}'] = c

    md.append("\n## Per Ticker\n")
    for ticker in TICKERS:
        if ticker not in all_results:
            md.append(f"\n### {ticker}\nNo data\n")
            results_json['tickers'][ticker] = {'error': 'no_data'}
            continue

        tk_go = get_go(ticker)
        cs = CONTRACT_SIZES.get(ticker, '-')
        md.append(f"\n### {ticker}  (cs={cs}, GO~{tk_go:,.0f} RUB)\n")
        md.append("| Fold | Hold | Trades | Return% | Max DD% | Calmar | Sharpe | WR% | PF |")
        md.append("|------|-----:|-------:|--------:|--------:|-------:|------:|----:|---:|")

        ticker_json = {}
        for hold in HOLD_PERIODS:
            if hold not in all_results[ticker]:
                continue
            hd = all_results[ticker][hold]
            for fi, fn in enumerate(FOLD_NAMES):
                fr = hd['folds'][fi]
                md.append(
                    f"| {fn} | {hold}b | {fr['trades']} | {fr['return_pct']:>+7.2f}% | "
                    f"{fr['max_dd_pct']:.2f}% | {fr['calmar']:.4f} | {fr['sharpe']:.4f} | "
                    f"{fr['win_rate']:.1f}% | {fr['profit_factor']:.2f} |"
                )
            comb = hd['combined']
            md.append(
                f"| **All** | **{hold}b** | **{comb['trades']}** | **{comb['return_pct']:>+7.2f}%** | "
                f"**{comb['max_dd_pct']:.2f}%** | **{comb['calmar']:.4f}** | **{comb['sharpe']:.4f}** | "
                f"**{comb['win_rate']:.1f}%** | **{comb['profit_factor']:.2f}** |"
            )
            ticker_json[str(hold)] = {
                'folds': [{k: v for k, v in fr.items() if k != 'fold'} for fr in hd['folds']],
                'combined': comb,
            }
        results_json['tickers'][ticker] = ticker_json

    md.append("\n## Walk-Forward Stability\n")
    for metric, mlabel in [('return_pct', 'Return%'), ('max_dd_pct', 'Max DD%'),
                           ('sharpe', 'Sharpe'), ('win_rate', 'WR%')]:
        md.append(f"\n**{mlabel}**\n")
        md.append("| Hold | Fold1 | Fold2 | Fold3 | Fold4 | Mean | Std |")
        md.append("|------|------:|------:|------:|------:|-----:|----:|")
        for hold in HOLD_PERIODS:
            fvals = {fn: [] for fn in FOLD_NAMES}
            for tk, tres in all_results.items():
                if hold in tres:
                    for fi, fn in enumerate(FOLD_NAMES):
                        fvals[fn].append(tres[hold]['folds'][fi][metric])
            if any(fvals[fn] for fn in FOLD_NAMES):
                means = [np.mean(v) if v else 0 for v in [fvals[fn] for fn in FOLD_NAMES]]
                all_v = [x for fn in FOLD_NAMES for x in fvals[fn] if fvals[fn]]
                gm = np.mean(all_v) if all_v else 0
                gs = np.std(all_v, ddof=1) if len(all_v) > 1 else 0
                md.append(
                    f"| {hold}b | {means[0]:>+7.2f} | {means[1]:>+7.2f} | {means[2]:>+7.2f} | {means[3]:>+7.2f} | "
                    f"{gm:>+7.2f} | {gs:.2f} |"
                )

    with open(f'{OUT}/report.md', 'w') as f:
        f.write('\n'.join(md))
    with open(f'{OUT}/results.json', 'w') as f:
        json.dump(results_json, f, indent=2, default=str)

    print(f"\nReport -> {OUT}/report.md  JSON -> {OUT}/results.json")
    print(f"\n{'='*60}\n  PER-TICKER SUMMARY\n{'='*60}")
    for tk in sorted(all_results.keys()):
        tr = all_results[tk]
        for hold in HOLD_PERIODS:
            if hold in tr:
                c = tr[hold]['combined']
                print(f"  {tk:>4} h={hold:2d}: {c['trades']:>3} tr | ret={c['return_pct']:>+7.2f}% | "
                      f"DD={c['max_dd_pct']:>5.2f}% | Calmar={c['calmar']:.4f} | "
                      f"WR={c['win_rate']:.0f}% | PF={c['profit_factor']:.2f}")


if __name__ == '__main__':
    main()
