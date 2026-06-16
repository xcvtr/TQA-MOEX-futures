#!/usr/bin/env python3
"""
Systematic scan: SMA mean reversion on ALL 47 MOEX tickers.
Plan: docs/plans/systematic-scan.md

Uses DailyPortfolio-compatible simulation:
  - GO-based position sizing (contracts = capital * mu / GO)
  - Simple PnL: (exit - entry) * contracts (DailyPortfolio convention)
  - Commission: 2 RUB/contract per side
  - Max DD: 20% portfolio-level stop
"""

import psycopg2
import numpy as np
import pandas as pd
from datetime import datetime, timedelta, timezone
from itertools import product
import os
import sys
import warnings
warnings.filterwarnings('ignore')

DB = dict(host='10.0.0.64', port=5432, dbname='moex', user='postgres', password='postgres')

QUALIFIED_TICKERS = [
    'AF', 'AU', 'BR', 'CC', 'CE', 'CH', 'CNYRUBF', 'CR', 'DX', 'ED',
    'EURRUBF', 'FF', 'GD', 'GK', 'GL', 'GLDRUBF', 'GZ', 'HS', 'HY',
    'IMOEXF', 'KC', 'MC', 'ME', 'MG', 'MN', 'MX', 'NA', 'NM', 'PD',
    'RB', 'RI', 'RL', 'RN', 'SBERF', 'SE', 'SF', 'SN', 'SP', 'SR',
    'SS', 'SV', 'Si', 'TN', 'TT', 'UC', 'VI', 'W4',
]

TICKER_CONFIGS = {
    'AF': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'AU': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'BR': {'go': 3000, 'minstep': 0.01, 'tick_rub': 1.0},
    'CC': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'CE': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'CH': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'CNYRUBF': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'CR': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'DX': {'go': 3000, 'minstep': 1, 'tick_rub': 1.0},
    'ED': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'EURRUBF': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'FF': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'GD': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'GK': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'GL': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'GLDRUBF': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'GZ': {'go': 2065, 'minstep': 0.01, 'tick_rub': 0.01},
    'HS': {'go': 5000, 'minstep': 1, 'tick_rub': 1.0},
    'HY': {'go': 3000, 'minstep': 1, 'tick_rub': 1.0},
    'IMOEXF': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'KC': {'go': 2500, 'minstep': 0.01, 'tick_rub': 80.0},
    'MC': {'go': 3149, 'minstep': 0.01, 'tick_rub': 1.0},
    'ME': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'MG': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'MN': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'MX': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'NA': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'NM': {'go': 1405, 'minstep': 1, 'tick_rub': 1.0},
    'PD': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'RB': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'RI': {'go': 5000, 'minstep': 1, 'tick_rub': 1.0},
    'RL': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'RN': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'SBERF': {'go': 2500, 'minstep': 1, 'tick_rub': 1.0},
    'SE': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'SF': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'SN': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'SP': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'SR': {'go': 5719, 'minstep': 0.01, 'tick_rub': 1.0},
    'SS': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'SV': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'Si': {'go': 1000, 'minstep': 0.01, 'tick_rub': 1.0},
    'TN': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'TT': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'UC': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'VI': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'W4': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
}


def load_5m(ticker, days=1095):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    conn = psycopg2.connect(**DB)
    cur = conn.cursor()
    cur.execute("""
        SELECT time, open, high, low, close, volume
        FROM moex_prices_5m
        WHERE symbol = %s AND time >= %s
        ORDER BY time
    """, (ticker, since))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def resample_to_daily(rows):
    if not rows or len(rows) < 200:
        return None
    df = pd.DataFrame(rows, columns=['time', 'open', 'high', 'low', 'close', 'volume'])
    df['time'] = pd.to_datetime(df['time'])
    df['date'] = df['time'].dt.date
    daily = df.groupby('date').agg(
        open=('open', 'first'),
        high=('high', 'max'),
        low=('low', 'min'),
        close=('close', 'last'),
        volume=('volume', 'sum'),
    ).reset_index()
    daily['date'] = pd.to_datetime(daily['date'])
    daily = daily.sort_values('date').reset_index(drop=True)
    return daily


def generate_signals(daily, fast=5, slow=20):
    sma_fast = daily['close'].rolling(fast).mean()
    sma_slow = daily['close'].rolling(slow).mean()
    return np.where(sma_fast < sma_slow, 1, 0).astype(int)


def run_portfolio(daily, signal, cfg, capital=100000.0, mu=0.50,
                  hold=5, sl=0.10, comm=2.0):
    """
    DailyPortfolio-compatible simulation.
    - GO-based position sizing (contracts = capital * mu / GO)
    - Simple PnL: (exit - entry) * contracts
    - Commission: comm RUB/contract per side
    - Max DD: 20% portfolio-level stop
    """
    go = cfg['go']
    open_arr = daily['open'].values.astype(float)
    high_arr = daily['high'].values.astype(float)
    low_arr = daily['low'].values.astype(float)
    close_arr = daily['close'].values.astype(float)
    n = len(daily)

    cap = float(capital)
    position = None
    trades = []
    equity_curve = []
    peak = capital
    max_dd_lim = 0.20

    for i in range(n):
        op = open_arr[i]
        hi = high_arr[i]
        lo = low_arr[i]
        cl = close_arr[i]

        # Entry at today's open based on yesterday's signal
        if position is None and i > 0 and signal[i - 1] == 1:
            entry_price = op
            max_risk = cap * mu
            contracts = int(max_risk / go) if go > 0 else 0
            if contracts > 0:
                margin = contracts * go
                entry_comm = comm * contracts
                if margin + entry_comm <= cap:
                    cap -= margin + entry_comm
                    position = {
                        'entry_price': entry_price,
                        'entry_idx': i,
                        'bars_held': 0,
                        'highest': entry_price,
                        'contracts': contracts,
                        'commission': entry_comm,
                    }

        # Position management
        if position is not None:
            pos = position
            pos['bars_held'] += 1
            entry_price = pos['entry_price']
            contracts = pos['contracts']
            margin = contracts * go

            should_exit = False
            exit_price = cl
            exit_reason = None

            # Stop-loss
            stop_level = entry_price * (1 - sl)
            if lo <= stop_level:
                exit_price = min(stop_level, cl)
                should_exit = True
                exit_reason = 'stop_loss'

            # Time stop
            if not should_exit and pos['bars_held'] >= hold:
                exit_price = cl
                should_exit = True
                exit_reason = 'time_stop'

            if should_exit:
                pnl = (exit_price - entry_price) * contracts
                entry_comm = pos.get('commission', 0)
                exit_comm = comm * contracts
                cap += margin + pnl - exit_comm
                pnl_pct = pnl / margin * 100 if margin > 0 else 0
                trades.append({
                    'entry_date': daily.iloc[pos['entry_idx']]['date'],
                    'exit_date': daily.iloc[i]['date'],
                    'entry_price': entry_price,
                    'exit_price': exit_price,
                    'contracts': contracts,
                    'pnl': pnl,
                    'pnl_pct': pnl_pct,
                    'commission': entry_comm + exit_comm,
                    'reason': exit_reason,
                    'bars_held': pos['bars_held'],
                })
                position = None
            else:
                if hi > pos['highest']:
                    pos['highest'] = hi

        # Equity
        current_equity = cap
        if position is not None:
            margin = position['contracts'] * go
            current_equity += margin
            current_equity += (cl - position['entry_price']) * position['contracts']

        equity_curve.append(current_equity)

        if current_equity > peak:
            peak = current_equity
        dd = (peak - current_equity) / peak if peak > 0 else 0
        if dd > max_dd_lim:
            if position is not None:
                pos = position
                contracts = pos['contracts']
                margin = contracts * go
                pnl = (cl - pos['entry_price']) * contracts
                entry_comm = pos.get('commission', 0)
                exit_comm = comm * contracts
                cap += margin + pnl - exit_comm
                pnl_pct = pnl / margin * 100 if margin > 0 else 0
                trades.append({
                    'entry_date': daily.iloc[pos['entry_idx']]['date'],
                    'exit_date': daily.iloc[i]['date'],
                    'entry_price': pos['entry_price'],
                    'exit_price': cl,
                    'contracts': contracts,
                    'pnl': pnl,
                    'pnl_pct': pnl_pct,
                    'commission': entry_comm + exit_comm,
                    'reason': 'max_dd',
                    'bars_held': pos['bars_held'],
                })
                position = None
                equity_curve[-1] = cap
            break

    # Close remaining
    if position is not None:
        pos = position
        contracts = pos['contracts']
        margin = contracts * go
        pnl = (close_arr[-1] - pos['entry_price']) * contracts
        entry_comm = pos.get('commission', 0)
        exit_comm = comm * contracts
        cap += margin + pnl - exit_comm
        pnl_pct = pnl / margin * 100 if margin > 0 else 0
        trades.append({
            'entry_date': daily.iloc[pos['entry_idx']]['date'],
            'exit_date': daily.iloc[-1]['date'],
            'entry_price': pos['entry_price'],
            'exit_price': close_arr[-1],
            'contracts': contracts,
            'pnl': pnl,
            'pnl_pct': pnl_pct,
            'commission': entry_comm + exit_comm,
            'reason': 'end_of_data',
            'bars_held': pos['bars_held'],
        })

    total_return_pct = (cap / capital - 1) * 100
    mdd = 0.0
    peak_eq = equity_curve[0] if equity_curve else capital
    for v in equity_curve:
        if v > peak_eq:
            peak_eq = v
        dd = (peak_eq - v) / peak_eq if peak_eq > 0 else 0
        if dd > mdd:
            mdd = dd

    calmar = total_return_pct / (mdd * 100) if mdd > 0 else 0.0
    total_comm_paid = sum(t.get('commission', 0) for t in trades)
    avg_contracts = sum(t['contracts'] for t in trades) / len(trades) if trades else 0
    comm_pct = total_comm_paid / capital * 100

    return {
        'final_capital': round(cap, 2),
        'total_return_pct': round(total_return_pct, 4),
        'max_dd_pct': round(mdd * 100, 4),
        'calmar': round(calmar, 4),
        'n_trades': len(trades),
        'trades': trades,
        'equity_curve': equity_curve,
        'total_commission': round(total_comm_paid, 2),
        'avg_contracts': round(avg_contracts, 1),
        'comm_pct': round(comm_pct, 4),
    }


def walk_forward(daily, signal, cfg, capital=100000.0, mu=0.50,
                 hold=5, sl=0.10, comm=2.0, n_folds=4):
    n = len(daily)
    fold_size = n // n_folds
    fold_results = []
    for f in range(n_folds):
        start = f * fold_size
        end = n if f == n_folds - 1 else (f + 1) * fold_size
        fold_daily = daily.iloc[start:end].reset_index(drop=True)
        fold_signal = signal[start:end]
        if len(fold_daily) < 30:
            continue
        result = run_portfolio(fold_daily, fold_signal, cfg, capital=capital,
                               mu=mu, hold=hold, sl=sl, comm=comm)
        fold_results.append(result)
    return fold_results


def scan_ticker(ticker, days=1095, capital=100000.0, mu=0.50,
                hold=5, sl=0.10, comm=2.0):
    cfg = TICKER_CONFIGS.get(ticker)
    if cfg is None:
        return {'status': 'NO CONFIG'}

    rows = load_5m(ticker, days)
    if not rows or len(rows) < 200:
        return {'status': 'NO DATA'}

    daily = resample_to_daily(rows)
    if daily is None or len(daily) < 50:
        return {'status': 'NO DATA'}

    signal = generate_signals(daily)
    result = run_portfolio(daily, signal, cfg, capital=capital,
                           mu=mu, hold=hold, sl=sl, comm=comm)

    status = 'OK'
    if result['n_trades'] < 5:
        status = f'OK (<5 trades: {result["n_trades"]})'

    result['status'] = status
    result['ticker'] = ticker
    result['go'] = cfg['go']
    result['daily_bars'] = len(daily)
    result['signal_on_pct'] = round(signal.sum() / len(signal) * 100, 1)
    return result


def sweep_ticker(daily, signal, cfg, capital=100000.0, comm=2.0):
    mus = [0.10, 0.20, 0.35, 0.50]
    holds = [5, 10, 21, 30]
    sls = [0.05, 0.10]

    best = None
    best_params = None
    best_res = None
    for mu, h, sl in product(mus, holds, sls):
        res = run_portfolio(daily, signal, cfg, capital=capital,
                            mu=mu, hold=h, sl=sl, comm=comm)
        if res['n_trades'] < 3:
            continue
        if best is None or res['calmar'] > best:
            best = res['calmar']
            best_params = (mu, h, sl)
            best_res = res
    return best, best_params, best_res


def main():
    start_time = datetime.now()
    date_str = start_time.strftime('%Y-%m-%d')
    print(f"{'='*60}")
    print(f"  Systematic SMA Mean Reversion Scan")
    print(f"  Date: {date_str}")
    print(f"  Tickers: {len(QUALIFIED_TICKERS)}")
    print(f"  Data: 5m OHLCV ({3*365} days) → daily resample")
    print(f"  Commission: 2 RUB/contract")
    print(f"  Params: mu=0.50, hold=5, sl=0.10, max_dd=20%")
    print(f"  Portfolio: DailyPortfolio (GO-based sizing, simple PnL)")
    print(f"{'='*60}")
    print()

    results = {}
    for i, ticker in enumerate(QUALIFIED_TICKERS):
        print(f"  [{i+1}/{len(QUALIFIED_TICKERS)}] {ticker}...", end=' ', flush=True)
        try:
            res = scan_ticker(ticker)
            results[ticker] = res
            s = res['status']
            if 'OK' in s:
                c = res['calmar']
                cal_str = f"Calmar={c:.4f}" if c > 0 else f"calmar={c:.4f}"
                print(f"ret={res['total_return_pct']:.2f}% DD={res['max_dd_pct']:.2f}% "
                      f"{cal_str} trades={res['n_trades']}")
            else:
                print(s)
        except Exception as e:
            results[ticker] = {'status': f'ERROR: {e}'}
            print(f"ERROR: {e}")

    elapsed_scan = (datetime.now() - start_time).total_seconds()
    print(f"\nScan completed in {elapsed_scan:.0f}s")
    print()

    # Sweep & Walk-forward for Calmar > 0
    positives = {t: r for t, r in results.items()
                 if isinstance(r.get('calmar', -1), (int, float)) and r.get('calmar', -1) > 0}

    print(f"{'='*60}")
    print(f"  Sweep & Walk-Forward ({len(positives)} Calmar>0)")
    print(f"{'='*60}")

    sweep_results = {}
    for ticker in sorted(positives.keys(), key=lambda t: results[t]['calmar'], reverse=True):
        base_res = results[ticker]
        print(f"  {ticker} (base Calmar={base_res['calmar']:.4f}): ", end='', flush=True)
        try:
            rows = load_5m(ticker)
            daily = resample_to_daily(rows)
            signal = generate_signals(daily)
            cfg = TICKER_CONFIGS[ticker]

            best_calmar, best_params, best_res = sweep_ticker(daily, signal, cfg)
            if best_params is None:
                best_calmar = base_res['calmar']
                best_params = (0.50, 5, 0.10)
                best_res = base_res

            mu, h, sl = best_params
            wf = walk_forward(daily, signal, cfg, mu=mu, hold=h, sl=sl)
            wf_returns = [r['total_return_pct'] for r in wf if r['n_trades'] >= 2]
            wf_positive = all(r > 0 for r in wf_returns) if len(wf_returns) == 4 else False
            wf_avg = np.mean(wf_returns) if wf_returns else 0
            wf_std = np.std(wf_returns) if len(wf_returns) > 1 else 0

            sweep_results[ticker] = {
                'best_calmar': round(best_calmar, 4) if best_calmar else base_res['calmar'],
                'best_params': best_params,
                'best_return': round(best_res['total_return_pct'], 4) if best_res else base_res['total_return_pct'],
                'best_dd': round(best_res['max_dd_pct'], 4) if best_res else base_res['max_dd_pct'],
                'wf_positive': wf_positive,
                'wf_avg': round(wf_avg, 4),
                'wf_std': round(wf_std, 4),
                'wf_returns': [round(r, 4) for r in wf_returns],
            }

            print(f"best Calmar={best_calmar:.4f} "
                  f"mu={mu} h={h} sl={sl} "
                  f"WF={'OK' if wf_positive else 'MIXED'}")
        except Exception as e:
            sweep_results[ticker] = {'error': str(e)}
            print(f"ERROR: {e}")

    generate_report(results, sweep_results, elapsed_scan)
    total_elapsed = (datetime.now() - start_time).total_seconds()
    print(f"\nTotal time: {total_elapsed:.0f}s")


def generate_report(results, sweep_results, elapsed):
    os.makedirs('reports', exist_ok=True)
    date_str = datetime.now().strftime('%Y-%m-%d')

    lines = []
    lines.append(f"# Systematic SMA Mean Reversion Scan — {date_str}")
    lines.append("")
    lines.append(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"**Elapsed:** {elapsed:.0f}s")
    lines.append(f"**Tickers:** {len(QUALIFIED_TICKERS)}")
    lines.append("**Data:** 5m OHLCV (3 years) → daily resample")
    lines.append("**Signal:** SMA5(close) < SMA20(close) → LONG (mean reversion)")
    lines.append("**Portfolio:** DailyPortfolio-compatible (GO-based sizing, simple PnL)")
    lines.append("**Commission:** 2 RUB/contract per side (4 RUB round-trip)")
    lines.append("**Max DD limit:** 20% (portfolio-level stop)")
    lines.append("")
    lines.append("## Base Results (mu=0.50, hold=5, sl=0.10)")
    lines.append("")
    lines.append("| # | ticker | GO | return% | DD% | Calmar | trades | avg_ctr | comm% | sig% | status |")
    lines.append("|---|--------|----|---------|-----|--------|--------|---------|-------|------|--------|")

    sorted_tickers = sorted(
        QUALIFIED_TICKERS,
        key=lambda t: (
            results.get(t, {}).get('calmar', -999)
            if isinstance(results.get(t, {}).get('calmar', -999), (int, float))
            else -999
        ),
        reverse=True
    )

    for rank, ticker in enumerate(sorted_tickers, 1):
        r = results.get(ticker, {})
        status = r.get('status', '?')
        go = TICKER_CONFIGS.get(ticker, {}).get('go', '?')

        calmar_val = r.get('calmar') if isinstance(r.get('calmar'), (int, float)) else None
        if calmar_val is not None and calmar_val > 0:
            cal_str = f"**{calmar_val:.4f}**"
        elif calmar_val is not None:
            cal_str = f"{calmar_val:.4f}"
        else:
            cal_str = "—"

        if status == 'NO DATA':
            lines.append(f"| {rank} | {ticker} | {go} | — | — | — | — | — | — | — | NO DATA |")
        elif 'NO CONFIG' in status:
            lines.append(f"| {rank} | {ticker} | — | — | — | — | — | — | — | — | NO CONFIG |")
        elif status.startswith('ERROR'):
            lines.append(f"| {rank} | {ticker} | {go} | — | — | — | — | — | — | — | ERROR |")
        else:
            ret = r.get('total_return_pct', 0)
            dd = r.get('max_dd_pct', 0)
            nt = r.get('n_trades', 0)
            ac = r.get('avg_contracts', 0)
            cp = r.get('comm_pct', 0)
            sp = r.get('signal_on_pct', 0)
            st = 'OK'
            if nt < 5:
                st = f"<5 trades"
            lines.append(f"| {rank} | {ticker} | {go} | {ret:.2f}% | {dd:.2f}% | {cal_str} | {nt} | {ac:.1f} | {cp:.2f}% | {sp}% | {st} |")

    lines.append("")
    lines.append("## Sweep & Walk-Forward (Calmar > 0)")
    lines.append("")
    lines.append("| ticker | GO | base Calmar | best Calmar | params | ret% | DD% | WF+ | WF avg% | WF std% |")
    lines.append("|--------|----|-------------|-------------|--------|------|-----|-----|---------|---------|")

    positives = {t: r for t, r in results.items()
                 if isinstance(r.get('calmar'), (int, float)) and r.get('calmar', -1) > 0}

    for ticker in sorted(positives.keys(), key=lambda t: results[t]['calmar'], reverse=True):
        r = results[ticker]
        sw = sweep_results.get(ticker, {})
        go = TICKER_CONFIGS.get(ticker, {}).get('go', '?')
        base_cal = r['calmar']

        if sw and 'best_params' in sw:
            mu, h, sl = sw['best_params']
            params = f"mu={mu} h={h} sl={sl}"
            bc = sw.get('best_calmar', 0)
            br = sw.get('best_return', 0)
            bd = sw.get('best_dd', 0)
            wf_ok = sw.get('wf_positive', False)
            wf_avg = sw.get('wf_avg', 0)
            wf_std = sw.get('wf_std', 0)
            wf_str = "✅" if wf_ok else "❌"
            lines.append(f"| {ticker} | {go} | {base_cal:.4f} | {bc:.4f} | {params} | {br:.2f}% | {bd:.2f}% | {wf_str} | {wf_avg:.2f}% | {wf_std:.2f}% |")
        else:
            lines.append(f"| {ticker} | {go} | {base_cal:.4f} | — | — | — | — | — | — | — |")

    total_ok = sum(1 for r in results.values() if 'OK' in str(r.get('status', '')))
    total_pos = len(positives)
    total_neg = sum(1 for r in results.values()
                    if isinstance(r.get('calmar'), (int, float))
                    and r.get('calmar', 0) <= 0 and 'OK' in str(r.get('status', '')))
    no_data = sum(1 for r in results.values() if r.get('status') == 'NO DATA')

    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Total tickers: {len(QUALIFIED_TICKERS)}")
    lines.append(f"- With sufficient data: {total_ok}")
    lines.append(f"- Positive Calmar: **{total_pos}**")
    lines.append(f"- Negative Calmar: {total_neg}")
    lines.append(f"- No data: {no_data}")
    lines.append(f"- Scan time: {elapsed:.0f}s")
    lines.append("")

    if total_pos > 0:
        lines.append("## Top Tickers by Calmar")
        lines.append("")
        top = sorted(positives.items(), key=lambda x: x[1]['calmar'], reverse=True)[:10]
        for rank, (t, r) in enumerate(top, 1):
            lines.append(f"{rank}. **{t}**: Calmar={r['calmar']:.4f}, "
                        f"return={r['total_return_pct']:.2f}%, "
                        f"DD={r['max_dd_pct']:.2f}%, "
                        f"trades={r['n_trades']}")

    lines.append("")
    lines.append("---")
    lines.append(f"*Report generated by scripts/systematic_scan.py*")

    report_path = f'reports/{date_str}-systematic-scan.md'
    with open(report_path, 'w') as f:
        f.write('\n'.join(lines) + '\n')

    print(f"\nReport written to {report_path}")

    print(f"\n{'='*60}")
    print(f"  SUMMARY")
    print(f"{'='*60}")
    print(f"  Positive Calmar: {total_pos}/{len(QUALIFIED_TICKERS)}")
    if total_pos > 0:
        print(f"  Top tickers:")
        top = sorted(positives.items(), key=lambda x: x[1]['calmar'], reverse=True)[:10]
        for t, r in top:
            print(f"    {t}: Calmar={r['calmar']:.4f} ret={r['total_return_pct']:.2f}% "
                  f"DD={r['max_dd_pct']:.2f}% trades={r['n_trades']}")
    print(f"  Negative Calmar: {total_neg}")
    print(f"  No data: {no_data}")


if __name__ == '__main__':
    main()
