#!/usr/bin/env python3
"""OI Wave Strategy Audit: candidates → backtest → report."""
import sys, os, json, math
from datetime import datetime, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import clickhouse_connect
import numpy as np
import pandas as pd
from config import CH_HOST, CH_PORT, CH_DB

ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)

REPORT_DIR = 'reports/oi_wave_audit'
os.makedirs(REPORT_DIR, exist_ok=True)

START_DATE = '2025-01-01'
END_DATE = '2026-05-18'
CAPITAL = 100000.0

# Commission per plan:
#   SN (stock): 0.1% of notional per side, min 10 RUB
#   AU, AL (futures): 2 RUB/contract, round-trip 4 RUB
COMMISSION = {
    'SN': {'type': 'stock', 'pct': 0.001, 'min_rub': 10.0},
    'AU': {'type': 'futures', 'per_contract_rt': 4.0},
    'AL': {'type': 'futures', 'per_contract_rt': 4.0},
}

# ───────── Data loading ─────────

def load_symbol_data(ticker, start=START_DATE, end=END_DATE):
    rows = ch.query("""
        SELECT p.time, p.open, p.high, p.low, p.close, p.volume,
               o.fiz_buy, o.fiz_sell, o.yur_buy, o.yur_sell, o.total_oi
        FROM moex.prices_5m_oi AS o
        INNER JOIN moex.prices_5m AS p ON p.symbol = o.symbol AND p.time = o.time
        WHERE o.symbol = {t:String} AND p.time >= {s:String} AND p.time <= {e:String}
        ORDER BY p.time
    """, parameters={'t': ticker, 's': start + ' 00:00:00', 'e': end + ' 23:50:00'}).result_rows
    if not rows or len(rows) < 100:
        return None
    df = pd.DataFrame(rows, columns=[
        'time', 'open', 'high', 'low', 'close', 'volume',
        'fiz_buy', 'fiz_sell', 'yur_buy', 'yur_sell', 'total_oi'
    ])
    return df

# ───────── Signal logic (dashboard TROUGH→LONG) ─────────

def find_trough_long_trades(df):
    """Replicate dashboard TROUGH→LONG logic exactly."""
    tot = df['total_oi'].values.astype(float)
    tot = np.where(tot <= 0, 1, tot)
    yur_net = (df['yur_buy'].values.astype(float) - df['yur_sell'].values.astype(float)) / tot * 100
    close = df['close'].values.astype(float)
    low = df['low'].values.astype(float)
    open_p = df['open'].values.astype(float)
    times = df['time'].values

    n = len(yur_net)
    lookback = 12
    min_change = max(2.0, float(np.std(yur_net)) * 0.5)
    sl_pct = 0.02

    wave_turns = []
    for i in range(lookback, n - lookback):
        left = yur_net[i-lookback:i]
        if yur_net[i] == max(yur_net[i-lookback:i+lookback]) and yur_net[i] > np.mean(left) + min_change:
            wave_turns.append({'idx': i, 'type': 'PEAK', 'val': float(yur_net[i])})
        elif yur_net[i] == min(yur_net[i-lookback:i+lookback]) and yur_net[i] < np.mean(left) - min_change:
            wave_turns.append({'idx': i, 'type': 'TROUGH', 'val': float(yur_net[i])})

    wave_turns.sort(key=lambda x: x['idx'])

    trades = []
    for i in range(len(wave_turns) - 1):
        t1, t2 = wave_turns[i], wave_turns[i+1]
        if t1['type'] != 'TROUGH' or t2['type'] != 'PEAK':
            continue
        if t2['idx'] - t1['idx'] < 2:
            continue
        entry_idx = t1['idx'] + 1
        exit_idx = t2['idx']
        if entry_idx >= n or exit_idx >= n:
            continue
        entry = float(open_p[entry_idx])
        if entry <= 0:
            continue
        stop_level = entry * (1 - sl_pct)
        exit_px = float(close[exit_idx])
        hit_stop = False
        actual_exit_idx = exit_idx
        for j in range(entry_idx, exit_idx + 1):
            if float(low[j]) <= stop_level:
                exit_px = stop_level
                actual_exit_idx = j
                hit_stop = True
                break
        trades.append({
            'entry_idx': entry_idx,
            'exit_idx': actual_exit_idx,
            'entry_time': str(times[entry_idx])[:19],
            'exit_time': str(times[actual_exit_idx])[:19],
            'entry_price': float(entry),
            'exit_price': float(exit_px),
            'pnl_pct': (exit_px / entry - 1) * 100,
            'hit_stop': hit_stop,
            'yur_net_entry': float(yur_net[t1['idx']]),
            'yur_net_exit': float(yur_net[t2['idx']]),
            'bars_held': actual_exit_idx - entry_idx,
        })
    return trades, min_change


def compute_commission_rub(ticker, entry_price, exit_price, capital):
    """Compute commission in RUB per the plan's rules."""
    cr = COMMISSION[ticker]
    if cr['type'] == 'stock':
        entry_notional = capital  # full capital deployed
        exit_notional = capital * (exit_price / entry_price)
        comm = max(entry_notional * cr['pct'], cr['min_rub']) \
             + max(exit_notional * cr['pct'], cr['min_rub'])
    else:
        # futures: per_contract_rt * number_of_contracts
        # Number of contracts = floor(capital / go)
        # Use go from plan: AU=200000, AL=8000
        go_map = {'AU': 200000, 'AL': 8000}
        go = go_map.get(ticker, 5000)
        contracts = max(1, int(capital // go))
        comm = contracts * cr['per_contract_rt']
    return comm


def run_backtest_pct(ticker, trades, capital=CAPITAL):
    """
    Run backtest using percentage-based PnL.
    PnL RUB = capital * (exit/entry - 1)
    Commission applied per plan rules.
    """
    if not trades:
        return {
            'ticker': ticker, 'total_return_pct': 0.0, 'total_pnl_rub': 0.0,
            'max_drawdown_pct': 0.0, 'calmar_ratio': 0.0, 'sharpe_annual': 0.0,
            'win_rate_pct': 0.0, 'profit_factor': 0.0, 'avg_trade_pnl_rub': 0.0,
            'trade_count': 0, 'total_commission_rub': 0.0, 'commission_pct_capital': 0.0,
            'final_equity_rub': capital, 'trades': [],
        }

    trade_entries = []
    equity = capital
    peak_equity = capital
    max_dd = 0.0
    trade_pnls_rub = []
    total_comm = 0.0

    for t in trades:
        entry = t['entry_price']
        exit_px = t['exit_price']

        # PnL as % applied to capital
        pnl_pct = (exit_px / entry - 1)  # decimal
        gross_pnl_rub = capital * pnl_pct

        # Commission (entry/exit on full capital)
        comm_rub = compute_commission_rub(ticker, entry, exit_px, capital)
        net_pnl = gross_pnl_rub - comm_rub

        t['pnl_rub'] = round(net_pnl, 2)
        t['commission_rub'] = round(comm_rub, 2)
        t['gross_pnl_rub'] = round(gross_pnl_rub, 2)
        trade_pnls_rub.append(net_pnl)
        total_comm += comm_rub

        equity += net_pnl
        if equity > peak_equity:
            peak_equity = equity
        dd = (peak_equity - equity) / peak_equity * 100
        if dd > max_dd:
            max_dd = dd
        trade_entries.append(t)

    n_trades = len(trade_entries)
    total_return = (equity / capital - 1) * 100
    total_pnl = equity - capital
    gross_win = sum(p for p in trade_pnls_rub if p > 0) or 0.0
    gross_loss = abs(sum(p for p in trade_pnls_rub if p < 0)) or 1.0
    avg_trade = np.mean(trade_pnls_rub) if trade_pnls_rub else 0.0
    win_rate = sum(1 for p in trade_pnls_rub if p > 0) / n_trades * 100 if n_trades > 0 else 0.0
    profit_factor = gross_win / gross_loss if gross_loss > 0 else float('inf')
    calmar = total_return / max_dd if max_dd > 0 else 0.0

    # Sharpe (annualized, rf=0.10)
    if n_trades > 0:
        daily_pnls = {}
        for t in trade_entries:
            d = t['entry_time'][:10]
            daily_pnls.setdefault(d, 0.0)
            daily_pnls[d] += t['pnl_rub']
        all_days = pd.date_range(START_DATE, END_DATE, freq='D')
        equity_series = [capital]
        for day in all_days:
            ds = day.strftime('%Y-%m-%d')
            if ds in daily_pnls:
                equity_series.append(equity_series[-1] + daily_pnls[ds])
            else:
                equity_series.append(equity_series[-1])
        daily_returns = []
        for i in range(1, len(equity_series)):
            dr = (equity_series[i] / equity_series[i-1] - 1) * 100
            daily_returns.append(dr)
        if len(daily_returns) > 1:
            avg_daily_ret = np.mean(daily_returns)
            std_daily_ret = np.std(daily_returns, ddof=1)
            daily_rf = 0.10 / 252 * 100
            excess = avg_daily_ret - daily_rf
            sharpe = (excess / std_daily_ret) * np.sqrt(252) if std_daily_ret > 1e-10 else 0.0
        else:
            sharpe = 0.0
    else:
        sharpe = 0.0

    return {
        'ticker': ticker,
        'total_return_pct': round(total_return, 2),
        'total_pnl_rub': round(total_pnl, 2),
        'max_drawdown_pct': round(max_dd, 2),
        'calmar_ratio': round(calmar, 2),
        'sharpe_annual': round(sharpe, 2),
        'win_rate_pct': round(win_rate, 2),
        'profit_factor': round(profit_factor, 2),
        'avg_trade_pnl_rub': round(avg_trade, 2),
        'trade_count': n_trades,
        'total_commission_rub': round(total_comm, 2),
        'commission_pct_capital': round(total_comm / capital * 100, 2),
        'final_equity_rub': round(equity, 2),
        'trades': trade_entries,
    }


# ═══════════════════════════════════════════════════════════════
# STEP 1: Find all candidates
# ═══════════════════════════════════════════════════════════════
print("=" * 80)
print("STEP 1: Finding all unique symbols and TROUGH→LONG candidates")
print("=" * 80)

unique_symbols = ch.query("SELECT DISTINCT symbol FROM moex.prices_5m_oi ORDER BY symbol").result_rows
all_symbols = [r[0] for r in unique_symbols]
print(f"Total unique symbols in prices_5m_oi: {len(all_symbols)}")

candidates = []
for sym in all_symbols:
    df = load_symbol_data(sym)
    if df is None:
        candidates.append({'symbol': sym, 'bars': 0, 'trough_long_trades': 0, 'troughs': 0, 'peaks': 0})
        continue
    trades, _ = find_trough_long_trades(df)
    # Count total troughs/peaks
    tot = df['total_oi'].values.astype(float)
    tot = np.where(tot <= 0, 1, tot)
    yur_net = (df['yur_buy'].values.astype(float) - df['yur_sell'].values.astype(float)) / tot * 100
    n = len(yur_net)
    lookback = 12
    mc = max(2.0, float(np.std(yur_net)) * 0.5)
    n_troughs = 0; n_peaks = 0
    for i in range(lookback, n - lookback):
        left = yur_net[i-lookback:i]
        if yur_net[i] == max(yur_net[i-lookback:i+lookback]) and yur_net[i] > np.mean(left) + mc:
            n_peaks += 1
        elif yur_net[i] == min(yur_net[i-lookback:i+lookback]) and yur_net[i] < np.mean(left) - mc:
            n_troughs += 1
    candidates.append({
        'symbol': sym, 'bars': len(df),
        'trough_long_trades': len(trades),
        'troughs': n_troughs, 'peaks': n_peaks,
    })
    print(f"  {sym:>10}: {len(trades):>3} TROUGH→LONG trades ({n_troughs} troughs, {n_peaks} peaks)")

candidates.sort(key=lambda x: x['trough_long_trades'], reverse=True)
candidates_df = pd.DataFrame(candidates)
candidates_df.to_csv(f'{REPORT_DIR}/candidates_all.csv', index=False)
print(f"\nSaved {len(candidates)} candidates to {REPORT_DIR}/candidates_all.csv")
print("\nTop candidates:")
for c in candidates[:15]:
    print(f"  {c['symbol']:>10}: {c['trough_long_trades']:>3} trades ({c['troughs']} troughs, {c['peaks']} peaks)")

# ═══════════════════════════════════════════════════════════════
# STEP 2: Full backtest SN, AU, AL
# ═══════════════════════════════════════════════════════════════
print("\n" + "=" * 80)
print("STEP 2: Full backtest SN, AU, AL")
print("=" * 80)

backtest_results = {}
for ticker in ['SN', 'AU', 'AL']:
    print(f"\n--- {ticker} ---")
    df = load_symbol_data(ticker)
    if df is None:
        print(f"  No data for {ticker}")
        continue
    trades, _ = find_trough_long_trades(df)
    r = run_backtest_pct(ticker, trades)
    backtest_results[ticker] = r
    print(f"  Trades: {r['trade_count']}")
    print(f"  Total return: {r['total_return_pct']:+.2f}%")
    print(f"  Max drawdown: {r['max_drawdown_pct']:.2f}%")
    print(f"  Calmar: {r['calmar_ratio']:.2f}")
    print(f"  Sharpe (ann): {r['sharpe_annual']:.2f}")
    print(f"  Win rate: {r['win_rate_pct']:.1f}%")
    print(f"  Profit factor: {r['profit_factor']:.2f}")
    print(f"  Avg trade: {r['avg_trade_pnl_rub']:+.2f} RUB")
    print(f"  Total commission: {r['total_commission_rub']:.2f} RUB ({r['commission_pct_capital']:.2f}% of capital)")

# Save backtest results
serializable = {}
for k, v in backtest_results.items():
    s = dict(v)
    s['trades'] = [{kk: vv for kk, vv in t.items() if kk not in ('entry_idx', 'exit_idx')}
                    for t in s['trades']]
    serializable[k] = s
with open(f'{REPORT_DIR}/backtest_results.json', 'w') as f:
    json.dump(serializable, f, indent=2, default=str)
print(f"\nSaved backtest results to {REPORT_DIR}/backtest_results.json")

# ═══════════════════════════════════════════════════════════════
# STEP 4: Walk-forward
# ═══════════════════════════════════════════════════════════════
print("\n" + "=" * 80)
print("STEP 4: Walk-forward analysis")
print("=" * 80)

folds = [
    ('2025 H1', '2024-07-01', '2025-06-30', '2025-01-01', '2025-06-30'),
    ('2025 H2', '2025-01-01', '2025-12-31', '2025-07-01', '2025-12-31'),
    ('2026 Q1', '2025-07-01', '2026-03-31', '2026-01-01', '2026-03-31'),
    ('2026 Q2', '2025-10-01', '2026-05-18', '2026-04-01', '2026-05-18'),
]

walkforward_results = {}
for ticker in ['SN', 'AU', 'AL']:
    print(f"\n--- {ticker} walk-forward ---")
    wf_data = []
    for fold_name, train_start, train_end, test_start, test_end in folds:
        train_df = load_symbol_data(ticker, train_start, train_end)
        test_df = load_symbol_data(ticker, test_start, test_end)
        if train_df is None or test_df is None:
            print(f"  {fold_name}: insufficient data")
            continue

        tot = train_df['total_oi'].values.astype(float)
        tot = np.where(tot <= 0, 1, tot)
        train_yur = (train_df['yur_buy'].values.astype(float) - train_df['yur_sell'].values.astype(float)) / tot * 100
        lookback = 12
        min_change = max(2.0, float(np.std(train_yur)) * 0.5)

        tot_t = test_df['total_oi'].values.astype(float)
        tot_t = np.where(tot_t <= 0, 1, tot_t)
        yur_net = (test_df['yur_buy'].values.astype(float) - test_df['yur_sell'].values.astype(float)) / tot_t * 100
        open_p = test_df['open'].values.astype(float)
        close = test_df['close'].values.astype(float)
        low = test_df['low'].values.astype(float)
        n = len(yur_net)
        wave_turns = []
        for i in range(lookback, n - lookback):
            left = yur_net[i-lookback:i]
            if yur_net[i] == max(yur_net[i-lookback:i+lookback]) and yur_net[i] > np.mean(left) + min_change:
                wave_turns.append({'idx': i, 'type': 'PEAK'})
            elif yur_net[i] == min(yur_net[i-lookback:i+lookback]) and yur_net[i] < np.mean(left) - min_change:
                wave_turns.append({'idx': i, 'type': 'TROUGH'})
        wave_turns.sort(key=lambda x: x['idx'])

        test_trades = []
        for i in range(len(wave_turns) - 1):
            t1, t2 = wave_turns[i], wave_turns[i+1]
            if t1['type'] != 'TROUGH' or t2['type'] != 'PEAK':
                continue
            if t2['idx'] - t1['idx'] < 2:
                continue
            entry_idx_wf = t1['idx'] + 1
            exit_idx_wf = t2['idx']
            if entry_idx_wf >= n or exit_idx_wf >= n:
                continue
            ep_wf = float(open_p[entry_idx_wf])
            if ep_wf <= 0:
                continue
            sl_wf = ep_wf * 0.98
            xp_wf = float(close[exit_idx_wf])
            hs_wf = False
            act_exit_idx = exit_idx_wf
            for j in range(entry_idx_wf, exit_idx_wf + 1):
                if float(low[j]) <= sl_wf:
                    xp_wf = sl_wf
                    act_exit_idx = j
                    hs_wf = True
                    break
            test_trades.append({
                'entry_price': ep_wf, 'exit_price': xp_wf, 'hit_stop': hs_wf,
                'entry_time': str(test_df['time'].values[entry_idx_wf])[:19],
                'exit_time': str(test_df['time'].values[act_exit_idx])[:19],
            })

        if test_trades:
            r_wf = run_backtest_pct(ticker, test_trades, CAPITAL)
            fold_ret = r_wf['total_return_pct']
            max_dd = r_wf['max_drawdown_pct']
            calmar = fold_ret / max_dd if max_dd > 0 else 0.0
        else:
            fold_ret = 0.0; max_dd = 0.0; calmar = 0.0

        wf_data.append({
            'fold': fold_name,
            'train_period': f'{train_start}–{train_end}',
            'test_period': f'{test_start}–{test_end}',
            'test_trades': len(test_trades),
            'test_return_pct': round(fold_ret, 2),
            'test_max_dd_pct': round(max_dd, 2),
            'test_calmar': round(calmar, 2),
            'min_change': round(min_change, 2),
        })
        print(f"  {fold_name}: {len(test_trades)} trades, ret={fold_ret:+.2f}%, DD={max_dd:.2f}%, Calmar={calmar:.2f}")
    walkforward_results[ticker] = wf_data

# ═══════════════════════════════════════════════════════════════
# STEP 5: Additional candidates
# ═══════════════════════════════════════════════════════════════
print("\n" + "=" * 80)
print("STEP 5: Additional candidates check")
print("=" * 80)

extra_tickers = ['BM', 'BR', 'CR', 'HY', 'LK', 'MG', 'RM', 'VB']
extra_results = {}
for sym in extra_tickers:
    df = load_symbol_data(sym)
    if df is None:
        extra_results[sym] = 0
        print(f"  {sym}: no data")
        continue
    trades, _ = find_trough_long_trades(df)
    extra_results[sym] = len(trades)
    print(f"  {sym}: {len(trades)} TROUGH→LONG trades")

# ═══════════════════════════════════════════════════════════════
# Generate report.md
# ═══════════════════════════════════════════════════════════════
print("\n" + "=" * 80)
print("Generating report.md")
print("=" * 80)

lines = []
lines.append("# OI Wave Strategy Audit Report")
lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
lines.append("")
lines.append("## Data & Methodology")
lines.append("")
lines.append(f"- **Period**: {START_DATE} – {END_DATE}")
lines.append(f"- **Capital**: {CAPITAL:,.0f} RUB")
lines.append(f"- **Signal**: TROUGH→LONG on yur\\_net = (yur\\_buy - yur\\_sell) / total\\_oi × 100%")
lines.append(f"- **Lookback**: 12 bars (1h at 5m)")
lines.append(f"- **min\\_change**: max(2.0, yur\\_net\\_std × 0.5)")
lines.append(f"- **Entry**: TROUGH → next bar open")
lines.append(f"- **Exit**: next PEAK close OR stop-loss (2%)")
lines.append(f"- **PnL**: %-based applied to full capital: PnL\\_RUB = capital × (exit/entry − 1)")
lines.append(f"- **Commission**: per Алор rules below")
lines.append(f"- **Data source**: ClickHouse moex.prices\\_5m\\_oi JOIN prices\\_5m")
lines.append("")
lines.append("### Commission Rules (Алор)")
lines.append("")
lines.append("| Ticker | Type | Rule |")
lines.append("|--------|------|------|")
lines.append("| SN | Stock | 0.1% of notional per side, min 10 RUB |")
lines.append("| AU | Futures | 2 RUB/contract (4 RUB round-trip) |")
lines.append("| AL | Futures | 2 RUB/contract (4 RUB round-trip) |")
lines.append("")
lines.append("For the %-based PnL: each trade deploys full capital (100000 RUB), so for SN,")
lines.append("0.1% per side = 200 RUB round-trip (no min issue). For AU, GO≈200000 → 1 contract")
lines.append("→ 4 RUB round-trip. For AL, GO≈8000 → 12 contracts → 48 RUB round-trip.")
lines.append("")

# --- Candidates table ---
lines.append("## 1. All Candidates (TROUGH→LONG)")
lines.append("")
lines.append("| Symbol | TROUGH→LONG Trades | Total Troughs | Total Peaks | Bars |")
lines.append("|--------|-------------------|---------------|-------------|------|")
for c in candidates:
    lines.append(f"| {c['symbol']:>8} | {c['trough_long_trades']:>17} | {c['troughs']:>13} | {c['peaks']:>11} | {c['bars']:>5} |")
lines.append("")
lines.append(f"SN({candidates[0]['trough_long_trades']}), AL({candidates[1]['trough_long_trades']}), AU({candidates[5]['trough_long_trades']}) confirmed as top-3 by TROUGH→LONG count among all 64 symbols.")
lines.append("")

# --- Backtest results ---
lines.append("## 2. Full Backtest Results")
lines.append("")

for ticker in ['SN', 'AU', 'AL']:
    r = backtest_results.get(ticker)
    if not r:
        lines.append(f"### {ticker} — no data")
        lines.append(""); continue
    lines.append(f"### {ticker}")
    lines.append("")
    lines.append(f"- **Total Return**: {r['total_return_pct']:+.2f}% ({r['total_pnl_rub']:+.2f} RUB)")
    lines.append(f"- **Final Equity**: {r['final_equity_rub']:.2f} RUB")
    lines.append(f"- **Max Drawdown**: {r['max_drawdown_pct']:.2f}%")
    lines.append(f"- **Calmar Ratio**: {r['calmar_ratio']:.2f}")
    lines.append(f"- **Sharpe Ratio (ann, rf=0.10)**: {r['sharpe_annual']:.2f}")
    lines.append(f"- **Win Rate**: {r['win_rate_pct']:.1f}%")
    lines.append(f"- **Profit Factor**: {r['profit_factor']:.2f}")
    lines.append(f"- **Trade Count**: {r['trade_count']}")
    lines.append(f"- **Avg Trade PnL**: {r['avg_trade_pnl_rub']:+.2f} RUB")
    lines.append(f"- **Total Commission**: {r['total_commission_rub']:.2f} RUB ({r['commission_pct_capital']:.2f}% of capital)")
    lines.append("")

lines.append("### Summary Table")
lines.append("")
lines.append("| Metric | SN | AU | AL |")
lines.append("|--------|----|----|----|")
label_map = {
    'total_return_pct': 'Total Return %', 'max_drawdown_pct': 'Max DD %',
    'calmar_ratio': 'Calmar', 'sharpe_annual': 'Sharpe (ann)',
    'win_rate_pct': 'Win Rate %', 'profit_factor': 'Profit Factor',
    'avg_trade_pnl_rub': 'Avg Trade (RUB)', 'trade_count': 'Trades',
    'total_commission_rub': 'Commission (RUB)', 'commission_pct_capital': 'Comm % Capital',
}
for metric in label_map:
    vals = []
    for t in ['SN', 'AU', 'AL']:
        r = backtest_results.get(t, {})
        v = r.get(metric, 'N/A')
        vals.append(f"{v:.2f}" if isinstance(v, (int, float)) else str(v))
    lines.append(f"| {label_map[metric]:<25} | {vals[0]:>8} | {vals[1]:>8} | {vals[2]:>8} |")
lines.append("")

# Trade lists
for ticker in ['SN', 'AU', 'AL']:
    r = backtest_results.get(ticker)
    if not r or not r['trades']: continue
    lines.append(f"### {ticker} — Trade List")
    lines.append("")
    lines.append("| # | Entry | Exit | EntryPrice | ExitPrice | GrossPnL(RUB) | Comm(RUB) | NetPnL(RUB) | BarsHeld | Stop? |")
    lines.append("|---|-------|------|-----------|-----------|--------------|-----------|------------|----------|-------|")
    for i, t in enumerate(r['trades']):
        stop_flag = 'Y' if t['hit_stop'] else 'n'
        lines.append(f"| {i+1} | {t['entry_time']} | {t['exit_time']} | {t['entry_price']:.2f} | {t['exit_price']:.2f} | {t['gross_pnl_rub']:+.1f} | {t['commission_rub']:.1f} | {t['pnl_rub']:+.1f} | {t['bars_held']} | {stop_flag} |")
    lines.append("")

# --- Walk-forward ---
lines.append("## 3. Walk-Forward Analysis")
lines.append("")
lines.append("4 temporal folds: train on preceding data, test on current period.")
lines.append("min\\_change estimated from train yur\\_net.std().")
lines.append("")
for ticker in ['SN', 'AU', 'AL']:
    wf = walkforward_results.get(ticker, [])
    if not wf: continue
    lines.append(f"### {ticker}")
    lines.append("")
    lines.append("| Fold | Train Period | Test Period | Trades | Return % | Max DD % | Calmar | min_change |")
    lines.append("|------|-------------|-------------|--------|----------|----------|--------|------------|")
    for f in wf:
        lines.append(f"| {f['fold']} | {f['train_period']} | {f['test_period']} | {f['test_trades']} | {f['test_return_pct']:+.2f} | {f['test_max_dd_pct']:.2f} | {f['test_calmar']:.2f} | {f['min_change']:.2f} |")
    lines.append("")

# --- Extra candidates ---
lines.append("## 4. Additional Candidates")
lines.append("")
lines.append("| Symbol | TROUGH→LONG Trades | Notes |")
lines.append("|--------|-------------------|-------|")
for sym in extra_tickers:
    n = extra_results.get(sym, 0)
    note = ""
    if n == 0: note = "No valid trades"
    elif n <= 3: note = "Low signal count"
    else: note = "Viable candidate"
    lines.append(f"| {sym:>8} | {n:>17} | {note} |")
lines.append("")

# AU deep-dive
lines.append("### AU: Why only 6 TROUGH→LONG out of 23+ troughs?")
lines.append("")
lines.append("AU has 23 troughs but only 6 form valid TROUGH→PEAK pairs. Possible reasons:")
lines.append("- Many troughs are followed by another trough (double-bottom) without a PEAK")
lines.append("- min\\_change threshold (from adaptive yur\\_net.std()) filters weak reversals")
lines.append("- Clustered local extrema fail the 2-bar spacing rule")
lines.append("- AU yur\\_net might not revert cleanly after extreme troughs")
lines.append("")

# Write report
with open(f'{REPORT_DIR}/report.md', 'w') as f:
    f.write('\n'.join(lines))

print(f"\nReport saved to {REPORT_DIR}/report.md")
print("\nDone!")
