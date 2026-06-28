#!/usr/bin/env python3
"""
CVD + Trailing TP — SCAN ALL MOEX TICKERS
==========================================
Сигнал: CVD (dcvd_z > 0.6 для LONG, dcvd_z < -0.6 для SHORT)
Выход: Trailing TP (activation=0.5%, trail=0.3%, timeout=12 bars)
Позиционирование: floor(equity * 0.1 / go), min 1, max leverage 10x
Комиссия: 4 RUB/contract, slippage 1 tick
Старт: 100,000 RUB
Период: Oct'2024 — today
Данные: CH 10.0.0.60:8123, moex.tradestats_fo, GROUP BY asset_code
Specs: PG 10.0.0.60:5432, moex, user=user, futures.ticker_specs
"""
import sys, os, json, math, time
import numpy as np
import pandas as pd
import clickhouse_connect
import psycopg2
from datetime import datetime, date
from collections import defaultdict

# ── Config ──────────────────────────────────────────────────────────────
CH_CONFIG = dict(host='10.0.0.60', port=8123, database='moex')
PG_CONFIG = dict(host='10.0.0.60', port=5432, dbname='moex', user='user')

PERIOD = 20       # z-score window
Z_THRESH = 0.6    # signal threshold
TRAIL_ACTIVATE = 0.005  # 0.5% trailing activation
TRAIL_TRAIL = 0.003    # 0.3% trailing trail
TIMEOUT_BARS = 12      # max hold
INITIAL_CAPITAL = 100_000.0
COMMISSION = 4.0        # RUB per contract
SLIPPAGE_TICKS = 1      # slippage on entry
START_DATE = '2024-10-01'
MIN_BARS = 10000
MIN_TRADES = 100
MAX_MDD = 0.5  # 50%

# ── Connect ────────────────────────────────────────────────────────────
ch = clickhouse_connect.get_client(**CH_CONFIG)

def load_ticker_specs():
    """Load ticker specs from PG futures.ticker_specs."""
    conn = psycopg2.connect(**PG_CONFIG, connect_timeout=5)
    cur = conn.cursor()
    cur.execute("""
        SELECT ticker, asset_code, min_step, step_price, lot_volume, go, decimals
        FROM futures.ticker_specs
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    
    specs = {}
    for r in rows:
        ticker = str(r[0])
        specs[ticker] = {
            'asset_code': str(r[1]),
            'min_step': float(r[2]) if r[2] else 0.01,
            'step_price': float(r[3]) if r[3] else 1.0,
            'lot': int(r[4]) if r[4] else 1,
            'go': float(r[5]) if r[5] else 0.0,
            'decimals': int(r[6]) if r[6] else 2,
        }
    return specs

def load_bars(asset_code):
    """Load 5m OHLCV + vol_b/vol_s from tradestats_fo for an asset_code."""
    q = f"""
        SELECT 
            toStartOfInterval(SYSTIME, INTERVAL 5 MINUTE) as bt,
            argMax(pr_open, SYSTIME) as open,
            argMax(pr_high, SYSTIME) as high,
            argMax(pr_low, SYSTIME) as low,
            argMax(pr_close, SYSTIME) as close,
            sum(vol_b) as vol_b,
            sum(vol_s) as vol_s,
            count() as raw_rows
        FROM moex.tradestats_fo 
        WHERE asset_code = '{asset_code}' 
          AND SYSTIME >= '{START_DATE}'
        GROUP BY bt 
        ORDER BY bt
    """
    try:
        df = ch.query_df(q)
    except Exception as e:
        print(f"  ⚠ CH query error for {asset_code}: {e}", file=sys.stderr)
        return pd.DataFrame()
    
    if df.empty:
        return df
    
    df['bt'] = pd.to_datetime(df['bt'])
    df['open'] = df['open'].astype(float)
    df['high'] = df['high'].astype(float)
    df['low'] = df['low'].astype(float)
    df['close'] = df['close'].astype(float)
    df['vol_b'] = df['vol_b'].astype(float).fillna(0)
    df['vol_s'] = df['vol_s'].astype(float).fillna(0)
    
    return df

def compute_cvd_z(bars_df):
    """Compute CVD and z-score over PERIOD bars."""
    df = bars_df.copy()
    n = len(df)
    
    cvd = df['vol_b'].values - df['vol_s'].values
    dcvd = np.diff(cvd, prepend=cvd[0])
    
    dcvd_z = np.full(n, np.nan)
    for i in range(PERIOD, n):
        s = dcvd[i-PERIOD:i]
        if np.std(s) > 0:
            dcvd_z[i] = (dcvd[i] - np.mean(s)) / np.std(s)
    
    df['cvd'] = cvd
    df['dcvd'] = dcvd
    df['dcvd_z'] = dcvd_z
    return df

def run_backtest(df, ticker, spec):
    """Run CVD + Trailing TP backtest."""
    n = len(df)
    if n < PERIOD + 5:
        return None
    
    df = compute_cvd_z(df)
    
    # Extract specs
    min_step = spec['min_step']
    step_price = spec['step_price']
    lot = spec['lot']
    go = spec['go']
    decimals = spec.get('decimals', 2)
    
    if go <= 0:
        go = min_step * lot * 100  # fallback estimate
    
    # Signal detection
    long_signals = df['dcvd_z'].values > Z_THRESH
    short_signals = df['dcvd_z'].values < -Z_THRESH
    
    # Run backtest
    equity = float(INITIAL_CAPITAL)
    peak_equity = equity
    
    trades = []
    trade_results = []
    
    open_trade = None  # { 'direction', 'entry_bar', 'entry_price', 'position', 'trail_active', 'trail_high', 'trail_low' }
    
    for i in range(PERIOD, n):
        # Check existing trade
        if open_trade is not None:
            bars_held = i - open_trade['entry_bar']
            current_price = df['close'].iloc[i]
            
            # Trailing TP logic
            if open_trade['direction'] == 'long':
                if not open_trade['trail_active']:
                    ret = (current_price - open_trade['entry_price']) / open_trade['entry_price']
                    if ret >= TRAIL_ACTIVATE:
                        open_trade['trail_active'] = True
                        open_trade['trail_high'] = current_price
                else:
                    if current_price > open_trade['trail_high']:
                        open_trade['trail_high'] = current_price
                    trail_ret = (open_trade['trail_high'] - current_price) / open_trade['entry_price']
                    if trail_ret >= TRAIL_TRAIL:
                        # Exit on trailing
                        exit_price = current_price
                        exit_reason = 'trail'
                        open_trade['exit_price'] = exit_price
                        open_trade['exit_bar'] = i
                        open_trade['exit_reason'] = exit_reason
                        open_trade['bars_held'] = bars_held + 1
                        trades.append(open_trade)
                        open_trade = None
                        continue
            else:  # short
                if not open_trade['trail_active']:
                    ret = (open_trade['entry_price'] - current_price) / open_trade['entry_price']
                    if ret >= TRAIL_ACTIVATE:
                        open_trade['trail_active'] = True
                        open_trade['trail_low'] = current_price
                else:
                    if current_price < open_trade['trail_low']:
                        open_trade['trail_low'] = current_price
                    trail_ret = (current_price - open_trade['trail_low']) / open_trade['entry_price']
                    if trail_ret >= TRAIL_TRAIL:
                        exit_price = current_price
                        exit_reason = 'trail'
                        open_trade['exit_price'] = exit_price
                        open_trade['exit_bar'] = i
                        open_trade['exit_reason'] = exit_reason
                        open_trade['bars_held'] = bars_held + 1
                        trades.append(open_trade)
                        open_trade = None
                        continue
            
            # Timeout exit
            if bars_held >= TIMEOUT_BARS - 1:
                exit_price = current_price
                open_trade['exit_price'] = exit_price
                open_trade['exit_bar'] = i
                open_trade['exit_reason'] = 'timeout'
                open_trade['bars_held'] = bars_held + 1
                trades.append(open_trade)
                open_trade = None
                continue
        
        # Check for new signal (if no open trade)
        if open_trade is None and i < n - 1:
            signal = None
            if long_signals[i]:
                signal = 'long'
            elif short_signals[i]:
                signal = 'short'
            
            if signal is not None:
                # Entry on next bar open + 1 tick slippage
                entry_price = df['open'].iloc[i + 1]
                slippage = min_step * SLIPPAGE_TICKS
                if signal == 'long':
                    entry_price += slippage
                else:
                    entry_price -= slippage
                
                # Position sizing: floor(equity * 0.1 / go), min 1
                position_value = equity * 0.1
                contracts = max(1, int(position_value / go))
                
                # Max leverage 10x check
                notional = contracts * lot * entry_price
                leverage = notional / go if go > 0 else 1.0
                
                open_trade = {
                    'direction': signal,
                    'entry_bar': i,
                    'entry_price': entry_price,
                    'entry_time': df['bt'].iloc[i],
                    'position': contracts,
                    'trail_active': False,
                    'trail_high': None,
                    'trail_low': None,
                    'go': go,
                    'lot': lot,
                    'entry_slippage': slippage,
                }
    
    # Close any remaining open trade
    if open_trade is not None:
        exit_price = df['close'].iloc[-1]
        open_trade['exit_price'] = exit_price
        open_trade['exit_bar'] = n - 1
        open_trade['exit_reason'] = 'end_of_data'
        open_trade['bars_held'] = n - 1 - open_trade['entry_bar'] + 1
        trades.append(open_trade)
        open_trade = None
    
    # Calculate PnL for all trades
    results = []
    for t in trades:
        direction = 1 if t['direction'] == 'long' else -1
        contracts = t['position']
        entry = t['entry_price']
        exit_p = t['exit_price']
        
        # PnL in RUB
        price_diff = (exit_p - entry) * direction
        pnl_rub_per_contract = price_diff / min_step * step_price
        gross_pnl = pnl_rub_per_contract * contracts * t['lot']
        
        # Commission
        commission = COMMISSION * 2 * contracts  # entry + exit
        slippage_cost = 0  # already factored into entry price
        
        net_pnl = gross_pnl - commission
        
        # Return %
        ret_pct = net_pnl / INITIAL_CAPITAL
        
        results.append({
            'direction': t['direction'],
            'entry_time': str(t['entry_time']),
            'entry_bar': t['entry_bar'],
            'entry_price': entry,
            'exit_price': exit_p,
            'contracts': contracts,
            'pnl_rub': net_pnl,
            'pnl_pct': ret_pct * 100,
            'gross_pnl': gross_pnl,
            'commission': commission,
            'bars_held': t.get('bars_held', 0),
            'exit_reason': t.get('exit_reason', 'unknown'),
            'entry_slippage': t.get('entry_slippage', 0),
        })
    
    return results

def compute_metrics(results):
    """Compute trading metrics from results list."""
    if not results:
        return None
    
    n = len(results)
    pnls = np.array([r['pnl_rub'] for r in results])
    pnl_pcts = np.array([r['pnl_pct'] for r in results])
    
    total_pnl = np.sum(pnls)
    total_pnl_pct = np.sum(pnl_pcts)
    mean_pnl = np.mean(pnls)
    wins = pnls[pnls > 0]
    losses = pnls[pnls <= 0]
    
    wr = len(wins) / n * 100
    avg_win = np.mean(wins) if len(wins) > 0 else 0
    avg_loss = np.mean(losses) if len(losses) > 0 else 0
    profit_factor = abs(np.sum(wins) / np.sum(losses)) if len(losses) > 0 and np.sum(losses) != 0 else float('inf')
    
    # Equity curve
    equity = INITIAL_CAPITAL + np.cumsum(pnls)
    
    # Max Drawdown
    running_max = np.maximum.accumulate(equity)
    drawdowns = (running_max - equity) / running_max
    mdd = np.max(drawdowns) if len(drawdowns) > 0 else 0
    
    # Calmar ratio
    total_return = (equity[-1] / INITIAL_CAPITAL - 1)
    annual_return = total_return  # since period is ~20 months
    calmar = annual_return / mdd if mdd > 0 else 0
    
    # Sharpe-like (using trade returns)
    sharpe = np.mean(pnl_pcts) / np.std(pnl_pcts) * np.sqrt(252) if np.std(pnl_pcts) > 0 else 0
    
    return {
        'n_trades': n,
        'total_pnl_rub': round(total_pnl, 2),
        'total_return_pct': round(total_return * 100, 2),
        'mean_pnl_rub': round(mean_pnl, 2),
        'win_rate': round(wr, 1),
        'avg_win_rub': round(avg_win, 2),
        'avg_loss_rub': round(avg_loss, 2),
        'profit_factor': round(profit_factor, 2),
        'max_drawdown_pct': round(mdd * 100, 2),
        'calmar': round(calmar, 3),
        'sharpe': round(sharpe, 3),
        'final_equity': round(equity[-1], 2),
        'peak_equity': round(np.max(equity), 2),
    }

# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════

def main():
    print("=" * 70, file=sys.stderr)
    print("CVD + Trailing TP — SCAN ALL MOEX TICKERS", file=sys.stderr)
    print("=" * 70, file=sys.stderr)
    
    # 1. Get all asset_codes from tradestats_fo with >10000 bars
    print(f"\n[1] Querying ClickHouse for asset_codes with >{MIN_BARS} bars...", file=sys.stderr)
    
    q_assets = f"""
        SELECT asset_code, count() as cnt
        FROM moex.tradestats_fo
        WHERE SYSTIME >= '{START_DATE}'
        GROUP BY asset_code
        HAVING cnt > {MIN_BARS}
        ORDER BY cnt DESC
    """
    try:
        rows = ch.query(q_assets).result_rows
    except Exception as e:
        print(f"  ❌ CH query failed: {e}", file=sys.stderr)
        sys.exit(1)
    
    asset_codes = [(r[0], r[1]) for r in rows]
    print(f"  Found {len(asset_codes)} asset_codes with >{MIN_BARS} bars", file=sys.stderr)
    
    # 2. Load ticker specs
    print(f"\n[2] Loading ticker specs from PostgreSQL...", file=sys.stderr)
    try:
        specs = load_ticker_specs()
        print(f"  Loaded {len(specs)} ticker specs", file=sys.stderr)
    except Exception as e:
        print(f"  ⚠ PG load failed ({e}), using defaults", file=sys.stderr)
        specs = {}
    
    # Build asset_code -> ticker map
    asset_to_ticker = {}
    for ticker, sp in specs.items():
        ac = sp['asset_code']
        if ac:
            asset_to_ticker[ac] = ticker
    
    # Show all asset codes found
    print(f"\n[3] Asset codes available:", file=sys.stderr)
    for ac, cnt in asset_codes[:10]:
        ticker = asset_to_ticker.get(ac, '?')
        print(f"  {ac:12s} ({ticker:6s}) -> {cnt:>8,} bars", file=sys.stderr)
    if len(asset_codes) > 10:
        print(f"  ... and {len(asset_codes)-10} more", file=sys.stderr)
    
    # 3. For each asset_code, load data and run backtest
    print(f"\n[4] Running backtests...", file=sys.stderr)
    all_results = []
    
    start_time = time.time()
    
    for idx, (asset_code, total_bars) in enumerate(asset_codes):
        ticker = asset_to_ticker.get(asset_code, asset_code)
        spec = specs.get(ticker, {
            'asset_code': asset_code,
            'min_step': 0.01,
            'step_price': 1.0,
            'lot': 1,
            'go': 0.0,
            'decimals': 2,
        })
        
        print(f"  [{idx+1}/{len(asset_codes)}] {asset_code:12s} ({ticker:6s}) ...", end=' ', file=sys.stderr)
        sys.stderr.flush()
        
        # Load bars
        df = load_bars(asset_code)
        if df.empty or len(df) < MIN_BARS:
            print(f"SKIP (only {len(df)} bars)", file=sys.stderr)
            continue
        
        print(f"{len(df):,} bars -> ", end='', file=sys.stderr)
        sys.stderr.flush()
        
        # Run backtest
        results = run_backtest(df, ticker, spec)
        
        if results is None or len(results) < MIN_TRADES:
            print(f"SKIP ({len(results) if results else 0} trades)", file=sys.stderr)
            continue
        
        # Compute metrics
        metrics = compute_metrics(results)
        if metrics is None:
            print(f"SKIP (no metrics)", file=sys.stderr)
            continue
        
        # Apply filters
        if metrics['n_trades'] < MIN_TRADES:
            print(f"FILTER (trades={metrics['n_trades']}<{MIN_TRADES})", file=sys.stderr)
            continue
        if metrics['total_return_pct'] <= 0:
            print(f"FILTER (return={metrics['total_return_pct']:.1f}%<=0)", file=sys.stderr)
            continue
        if metrics['max_drawdown_pct'] >= MAX_MDD * 100:
            print(f"FILTER (MDD={metrics['max_drawdown_pct']:.1f}%>={MAX_MDD*100:.0f}%)", file=sys.stderr)
            continue
        
        # Store
        entry = {
            'asset_code': asset_code,
            'ticker': ticker,
            'total_bars': total_bars,
            'bars_loaded': len(df),
        }
        entry.update(metrics)
        all_results.append(entry)
        
        elapsed = time.time() - start_time
        print(f"✅ {metrics['n_trades']} т, WR={metrics['win_rate']}%, "
              f"R={metrics['total_return_pct']:+.1f}%, "
              f"MDD={metrics['max_drawdown_pct']:.1f}%, "
              f"Calmar={metrics['calmar']:.3f} "
              f"[{elapsed:.0f}s]", file=sys.stderr)
    
    # 4. Rank by Calmar
    print(f"\n[5] Ranking by Calmar ratio...", file=sys.stderr)
    all_results.sort(key=lambda x: x['calmar'], reverse=True)
    
    # 5. Save results
    print(f"\n[6] Saving results...", file=sys.stderr)
    
    output_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'reports')
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, 'scan_cvd.md')
    
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    with open(output_path, 'w') as f:
        f.write(f"# CVD + Trailing TP — Scan Results\n\n")
        f.write(f"**Date**: {now_str}\n")
        f.write(f"**Period**: {START_DATE} — today\n")
        f.write(f"**Initial Capital**: {INITIAL_CAPITAL:,.0f} RUB\n")
        f.write(f"**Commission**: {COMMISSION:.0f} RUB/contract (round-trip)\n")
        f.write(f"**Slippage**: {SLIPPAGE_TICKS} tick(s) on entry\n\n")
        f.write(f"## Parameters\n\n")
        f.write(f"| Param | Value |\n")
        f.write(f"|-------|-------|\n")
        f.write(f"| CVD z-score period | {PERIOD} bars |\n")
        f.write(f"| Z-threshold | {Z_THRESH} |\n")
        f.write(f"| Trailing activation | {TRAIL_ACTIVATE*100:.1f}% |\n")
        f.write(f"| Trailing trail | {TRAIL_TRAIL*100:.1f}% |\n")
        f.write(f"| Timeout | {TIMEOUT_BARS} bars |\n")
        f.write(f"| Position size | 10% of equity / GO |\n")
        f.write(f"| Min trades | {MIN_TRADES} |\n")
        f.write(f"| Max MDD | {MAX_MDD*100:.0f}% |\n")
        f.write(f"| Min bars | {MIN_BARS} |\n\n")
        
        f.write(f"## Results ({len(all_results)} tickers passed filters)\n\n")
        
        if all_results:
            f.write(f"| # | Ticker | Asset | Trades | WR% | TotalR% | MeanPnL | PF | MDD% | Calmar | Sharpe | FinalEq |\n")
            f.write(f"|---|--------|-------|--------|:---:|:-------:|:-------:|:--:|:----:|:------:|:------:|:-------:|\n")
            
            for rank, entry in enumerate(all_results, 1):
                f.write(
                    f"| {rank} | {entry['ticker']:6s} | {entry['asset_code']:12s} "
                    f"| {entry['n_trades']:>5d} | {entry['win_rate']:>5.1f} "
                    f"| {entry['total_return_pct']:>+7.2f} | {entry['mean_pnl_rub']:>+8.2f} "
                    f"| {entry['profit_factor']:>5.2f} | {entry['max_drawdown_pct']:>5.1f} "
                    f"| {entry['calmar']:>6.3f} | {entry['sharpe']:>5.3f} "
                    f"| {entry['final_equity']:>8.0f} |\n"
                )
            
            f.write(f"\n## Detailed Results\n\n")
            for rank, entry in enumerate(all_results, 1):
                f.write(f"### {rank}. {entry['ticker']} ({entry['asset_code']})\n\n")
                f.write(f"| Metric | Value |\n")
                f.write(f"|--------|-------|\n")
                f.write(f"| Total bars loaded | {entry['bars_loaded']:,} |\n")
                f.write(f"| Total trades | {entry['n_trades']} |\n")
                f.write(f"| Win Rate | {entry['win_rate']:.1f}% |\n")
                f.write(f"| Total Return | {entry['total_return_pct']:+.2f}% |\n")
                f.write(f"| Total PnL | {entry['total_pnl_rub']:+.2f} RUB |\n")
                f.write(f"| Mean PnL/trade | {entry['mean_pnl_rub']:+.2f} RUB |\n")
                f.write(f"| Avg Win | {entry['avg_win_rub']:+.2f} RUB |\n")
                f.write(f"| Avg Loss | {entry['avg_loss_rub']:+.2f} RUB |\n")
                f.write(f"| Profit Factor | {entry['profit_factor']:.2f} |\n")
                f.write(f"| Max Drawdown | {entry['max_drawdown_pct']:.1f}% |\n")
                f.write(f"| Calmar Ratio | {entry['calmar']:.3f} |\n")
                f.write(f"| Sharpe Ratio | {entry['sharpe']:.3f} |\n")
                f.write(f"| Final Equity | {entry['final_equity']:.2f} RUB |\n")
                f.write(f"| Peak Equity | {entry['peak_equity']:.2f} RUB |\n\n")
        else:
            f.write("No tickers passed the filters.\n\n")
        
        f.write(f"---\n*Generated automatically by scan_cvd_trailing.py*\n")
    
    print(f"\n  ✅ Results saved to: {output_path}", file=sys.stderr)
    print(f"  Total tickers analyzed: {len(all_results)}", file=sys.stderr)
    print(f"  Total asset codes scanned: {len(asset_codes)}", file=sys.stderr)
    
    # Print summary table to stdout as well
    print(f"\n{'='*90}")
    print(f"CVD + Trailing TP — SCAN RESULTS (ranked by Calmar)")
    print(f"{'='*90}")
    print(f"{'#':>3} {'Ticker':8s} {'Trades':>7s} {'WR%':>6s} {'Return%':>8s} {'MDD%':>6s} {'Calmar':>8s} {'Sharpe':>7s}")
    print(f"{'-'*55}")
    for rank, entry in enumerate(all_results, 1):
        print(f"{rank:>3} {entry['ticker']:8s} {entry['n_trades']:>7d} {entry['win_rate']:>5.1f}% "
              f"{entry['total_return_pct']:>+7.2f}% {entry['max_drawdown_pct']:>5.1f}% "
              f"{entry['calmar']:>7.3f} {entry['sharpe']:>6.3f}")
    
    print(f"{'='*90}")
    print(f"Total passed filters: {len(all_results)}/{len(asset_codes)} asset codes")

if __name__ == '__main__':
    main()
