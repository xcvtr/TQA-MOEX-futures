#!/usr/bin/env python3
"""
CVD Momentum Strategy — Backtester
====================================
Uses 1-min DOM-derived data (moex.dom_min1) with rolling CVD z-score.
Entry: |CVD z| > 2.0 → price continues in same direction (momentum).

Usage:
  .venv/bin/python strategies/cvd_momentum/backtest.py \\
      --tickers SNGP,MIX,RTKM,SBRF --start 2024-06-01 --end 2026-06-01

Run `--help` for full options.
"""

import sys, os, argparse, json, logging
from datetime import datetime, timedelta
from collections import deque

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from config import CH_HOST, CH_PORT, CH_DB

import numpy as np
import clickhouse_connect as cc

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger('cvd_momentum')

# ── Ticker specs (fallback if not in CH.securities) ─────────────────────────
DEFAULT_SPECS = {
    'Si':    {'step_price': 1, 'min_step': 1,  'lot': 1000, 'go': 12440},
    'SBRF':  {'step_price': 1, 'min_step': 1,  'lot': 100,  'go': 5708},
    'SR':    {'step_price': 1, 'min_step': 1,  'lot': 100,  'go': 5708},
    'GAZR':  {'step_price': 1, 'min_step': 1,  'lot': 10,   'go': 5000},
    'SNGP':  {'step_price': 1, 'min_step': 1,  'lot': 100,  'go': 4000},
    'SNGR':  {'step_price': 1, 'min_step': 1,  'lot': 100,  'go': 4000},
    'MIX':   {'step_price': 1, 'min_step': 1,  'lot': 10,   'go': 3500},
    'HYDR':  {'step_price': 1, 'min_step': 1,  'lot': 100,  'go': 2500},
    'LKOH':  {'step_price': 1, 'min_step': 1,  'lot': 10,   'go': 8183},
    'RTKM':  {'step_price': 1, 'min_step': 1,  'lot': 100,  'go': 2000},
    'FEES':  {'step_price': 1, 'min_step': 1,  'lot': 1000, 'go': 2000},
    'RTSI':  {'step_price': 15, 'min_step': 10, 'lot': 1,   'go': 22318},
    'RI':    {'step_price': 15, 'min_step': 10, 'lot': 1,   'go': 22318},
    'AFLT':  {'step_price': 1, 'min_step': 1,  'lot': 100,  'go': 3000},
    'ED':    {'step_price': 7.72, 'min_step': 0.0001, 'lot': 1000, 'go': 5883},
    'EU':    {'step_price': 1, 'min_step': 1,  'lot': 1000, 'go': 14183},
    'MGNT':  {'step_price': 1, 'min_step': 1,  'lot': 10,   'go': 5000},
    'MTSI':  {'step_price': 1, 'min_step': 1,  'lot': 100,  'go': 3000},
    'NOTK':  {'step_price': 1, 'min_step': 1,  'lot': 100,  'go': 4000},
    'ROSN':  {'step_price': 1, 'min_step': 1,  'lot': 100,  'go': 5000},
    'SBPR':  {'step_price': 1, 'min_step': 1,  'lot': 100,  'go': 3000},
    'TATN':  {'step_price': 1, 'min_step': 1,  'lot': 100,  'go': 3500},
    'TRNF':  {'step_price': 1, 'min_step': 1,  'lot': 1,    'go': 8000},
    'VTBR':  {'step_price': 1, 'min_step': 1,  'lot': 100,  'go': 1578},
}

ALL_TICKERS = ['SNGP', 'MIX', 'RTKM', 'SBRF', 'HYDR', 'GAZR', 'Si', 'LKOH',
               'FEES', 'RTSI', 'SNGR', 'AFLT', 'ED', 'EU', 'MGNT', 'MTSI',
               'NOTK', 'ROSN', 'SBPR', 'TATN', 'TRNF', 'VTBR']


def load_specs_from_ch():
    """Load specs from ClickHouse moex.securities + PG futures.ticker_specs."""
    ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')
    rows = ch.query("SELECT ticker, stepprice, minstep, go_rub, lot FROM moex.securities")
    specs = {}
    for r in rows.result_rows:
        t = r[0]
        specs[t] = {
            'step_price': float(r[1]) if r[1] else 1,
            'min_step': float(r[2]) if r[2] else 1,
            'go': float(r[3]) if r[3] else 5000,
            'lot': int(r[4]) if r[4] else 1,
        }
    ch.close()
    return specs


def get_specs(ticker, ch_specs=None):
    """Get ticker specs: from CH, from PG, or default."""
    if ch_specs and ticker in ch_specs:
        return ch_specs[ticker]
    # Map DOM ticker → PG ticker
    pg_map = {'SBRF': 'SR', 'LKOH': 'LK', 'RTSI': 'RI'}
    if ticker in pg_map:
        return DEFAULT_SPECS.get(pg_map[ticker], DEFAULT_SPECS.get(ticker, {}))
    return DEFAULT_SPECS.get(ticker, {'step_price': 1, 'min_step': 1, 'lot': 100, 'go': 5000})


def load_data(tickers, start, end):
    """Load 1-min bars + CVD from moex.dom_min1."""
    ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')
    tickers_str = ', '.join(f"'{t}'" for t in tickers)
    query = f"""
        SELECT ticker, bt, prc, cvd, opn
        FROM moex.dom_min1
        WHERE ticker IN ({tickers_str})
          AND bt >= '{start}' AND bt < '{end}'
          AND prc > 0
        ORDER BY ticker, bt
    """
    log.info("Loading data from CH: %s → %s, %d tickers", start, end, len(tickers))
    result = ch.query(query)
    data = {}
    for r in result.result_rows:
        t, bt, prc, cvd, opn = r[0], r[1], float(r[2]), float(r[3]), float(r[4]) if r[4] else float(r[2])
        data.setdefault(t, []).append({'bt': bt, 'prc': prc, 'cvd': cvd, 'opn': opn})
    ch.close()
    log.info("Loaded: %s rows for %d tickers",
             sum(len(v) for v in data.values()), len(data))
    return data


class RollingZ:
    """Rolling z-score with configurable window."""
    def __init__(self, window=720):
        self.window = window
        self.buffer = deque(maxlen=window)

    def update(self, value):
        self.buffer.append(value)
        if len(self.buffer) < self.window // 2:
            return 0.0
        arr = list(self.buffer)
        m = np.mean(arr)
        s = np.std(arr)
        if s < 1e-8:
            return 0.0
        return (value - m) / s


def backtest_ticker(bars, ticker, params, specs):
    """Run backtest for one ticker. Returns list of trade dicts."""
    zp = RollingZ(params['z_window'])
    
    trades = []
    n = len(bars)
    min_step = specs.get('min_step', 1)
    
    for i in range(n):
        b = bars[i]
        cvd_z = zp.update(b['cvd'])
        if i < params['z_window'] // 2:
            continue
        if i < 5 or i > n - params['fwd_min'] - 1:
            continue
        
        prc_5m_ago = bars[i - params['lookback_min']]['prc']
        prc_chg_pct = (b['prc'] / prc_5m_ago - 1) * 100
        
        # Entry at next bar open (realistic — signal forms at bar close, entry next bar)
        entry_price = bars[i + 1]['opn'] if i + 1 < n else b['prc']
        # Slippage: 1 tick (min_step) in direction of trade
        # Exit at open of bar after forward horizon
        exit_idx = i + params['fwd_min'] + 1
        if exit_idx >= n:
            continue
        fwd_prc = bars[exit_idx]['opn'] if 'opn' in bars[exit_idx] else bars[exit_idx]['prc']
        fwd_chg_pct = (fwd_prc / entry_price - 1) * 100
        
        # Momentum signals
        mom_short = cvd_z < -params['z_thresh']
        mom_long = cvd_z > params['z_thresh']
        
        # Divergence signals
        bear_div = prc_chg_pct > params['prc_chg_thresh'] and cvd_z < -params['div_z_thresh']
        bull_div = prc_chg_pct < -params['prc_chg_thresh'] and cvd_z > params['div_z_thresh']
        
        if params.get('signal_type', 'mom') == 'div':
            signals = []
            if bear_div: signals.append(-1)
            if bull_div: signals.append(1)
        else:
            signals = []
            if mom_short: signals.append(-1)
            if mom_long: signals.append(1)
        
        for direction in signals:
            if direction == -1:
                correct = fwd_chg_pct < 0
            else:
                correct = fwd_chg_pct > 0
            
            trades.append({
                'ticker': ticker,
                'bt': b['bt'].isoformat() if hasattr(b['bt'], 'isoformat') else str(b['bt']),
                'entry': entry_price,
                'exit': fwd_prc,
                'direction': 'SHORT' if direction == -1 else 'LONG',
                'cvd_z': round(cvd_z, 2),
                'prc_chg_5m': round(prc_chg_pct, 3),
                'fwd_chg_15m': round(fwd_chg_pct, 3),
                'correct': correct,
            })
    
    return trades


def compute_pnl(trades, specs, capital=100_000):
    """Compute PnL in RUB for trades."""
    results = []
    for t in trades:
        sp = specs['step_price']
        ms = specs['min_step']
        lot = specs['lot']
        go = specs['go']
        
        price_diff = abs(t['entry'] - t['exit'])
        ticks = price_diff / ms if ms > 0 else price_diff
        pnl_rub = ticks * sp
        if t['direction'] == 'SHORT':
            pnl_rub = pnl_rub if t['entry'] > t['exit'] else -pnl_rub
        else:
            pnl_rub = pnl_rub if t['exit'] > t['entry'] else -pnl_rub
        
        # Commission: 4 RUB round-trip per contract via MOEX
        pnl_rub -= 4
        
        max_contracts = int(capital * 0.99 / go) if go > 0 else 1
        contracts = min(1, max_contracts)  # 1 contract fixed for now
        
        t['pnl_rub'] = round(pnl_rub * contracts, 2)
        t['contracts'] = contracts
        results.append(t)
    
    return results


def summary(trades_all, ticker_specs, capital=100_000):
    """Print summary statistics."""
    by_ticker = {}
    for t in trades_all:
        by_ticker.setdefault(t['ticker'], []).append(t)
    
    total_pnl = 0
    
    headers = ['Ticker', 'Trades', 'WR%', 'AvgPnl₽', 'Total₽', 'PF', 'MaxDD₽']
    sep = '-' * 80
    print(f"\n{'='*80}")
    print(f"  CVD MOMENTUM BACKTEST RESULTS")
    print(f"{'='*80}")
    print(f"  {' | '.join(h.rjust(10) for h in headers)}")
    print(f"  {sep}")
    
    for ticker in sorted(by_ticker.keys()):
        tlist = by_ticker[ticker]
        n = len(tlist)
        wins = sum(1 for t in tlist if t.get('correct', False))
        wr = wins / n * 100 if n else 0
        pnls = [t.get('pnl_rub', 0) for t in tlist]
        total = sum(pnls)
        avg_pnl = total / n if n else 0
        wins_pnl = sum(p for p in pnls if p > 0)
        losses_pnl = sum(p for p in pnls if p < 0)
        pf = abs(wins_pnl / losses_pnl) if losses_pnl != 0 else float('inf')
        
        # Max drawdown (simple: cumulative)
        cum = np.cumsum(pnls)
        peak = np.maximum.accumulate(cum)
        dd = peak - cum
        max_dd = np.max(dd) if len(dd) > 0 else 0
        
        total_pnl += total
        
        print(f"  {ticker:>10} | {n:>6} | {wr:>5.1f} | {avg_pnl:>8.1f} | {total:>8.0f} | {pf:>5.2f} | {max_dd:>8.0f}")
    
    print(f"  {sep}")
    print(f"  {'TOTAL':>10} | {' ':>6} | {' ':>5} | {' ':>8} | {total_pnl:>8.0f} | {' ':>5} | {' ':>8}")
    print(f"  Capital: {capital:,.0f} ₽ | Return: {total_pnl/capital*100:+.1f}%")
    print(f"{'='*80}\n")
    
    return total_pnl


def run_backtest(args):
    """Main backtest runner."""
    ch_specs = load_specs_from_ch()
    
    params = {
        'z_window': args.z_window,
        'z_thresh': args.z_thresh,
        'div_z_thresh': args.div_z_thresh,
        'lookback_min': args.lookback,
        'fwd_min': args.fwd,
        'prc_chg_thresh': args.prc_chg,
        'signal_type': args.signal_type,
    }
    
    log.info("Parameters: %s", params)
    
    tickers = args.tickers.split(',') if args.tickers else ALL_TICKERS
    data = load_data(tickers, args.start, args.end)
    
    all_trades = []
    for ticker in sorted(data.keys()):
        bars = data[ticker]
        if len(bars) < args.z_window:
            log.warning("Skipping %s: only %d bars (need %d)", ticker, len(bars), args.z_window)
            continue
        
        specs = get_specs(ticker, ch_specs)
        log.info("Backtesting %s: %d bars", ticker, len(bars))
        trades = backtest_ticker(bars, ticker, params, specs)
        
        # Compute PnL in RUB
        trades = compute_pnl(trades, specs, args.capital)
        
        # Report
        n = len(trades)
        if n > 0:
            wins = sum(1 for t in trades if t['correct'])
            wr = wins / n * 100
            pnl_sum = sum(t['pnl_rub'] for t in trades)
            log.info("  → %5d trades, WR=%5.1f%%, PnL=%+9.0f ₽", n, wr, pnl_sum)
        else:
            log.info("  → no trades")
        
        all_trades.extend(trades)
    
    # Full summary
    summary(all_trades, ch_specs, args.capital)
    
    # Save detailed trades
    if args.output:
        with open(args.output, 'w') as f:
            json.dump(all_trades, f, indent=2, default=str)
        log.info("Saved %d trades to %s", len(all_trades), args.output)
    
    return all_trades


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='CVD Momentum Backtester')
    parser.add_argument('--tickers', type=str, default='SNGP,MIX,RTKM,SBRF',
                        help='Comma-separated tickers')
    parser.add_argument('--start', type=str, default='2024-06-01',
                        help='Start date (YYYY-MM-DD)')
    parser.add_argument('--end', type=str, default='2026-06-01',
                        help='End date (YYYY-MM-DD)')
    parser.add_argument('--z-window', type=int, default=720,
                        help='Rolling z-score window in minutes (default: 720 = 12h)')
    parser.add_argument('--z-thresh', type=float, default=2.0,
                        help='Z-score threshold for momentum (default: 2.0)')
    parser.add_argument('--div-z-thresh', type=float, default=1.5,
                        help='Z-score threshold for divergence (default: 1.5)')
    parser.add_argument('--lookback', type=int, default=5,
                        help='Lookback minutes for price change (default: 5)')
    parser.add_argument('--fwd', type=int, default=15,
                        help='Forward horizon minutes (default: 15)')
    parser.add_argument('--prc-chg', type=float, default=0.15,
                        help='Price change threshold %% (default: 0.15)')
    parser.add_argument('--signal-type', type=str, default='mom',
                        choices=['mom', 'div'],
                        help='Signal type: mom (momentum) or div (divergence)')
    parser.add_argument('--capital', type=float, default=100_000,
                        help='Starting capital RUB (default: 100,000)')
    parser.add_argument('--output', type=str, default='',
                        help='Save trades JSON to file')
    args = parser.parse_args()
    
    log.info("CVD Momentum Backtest: tickers=%s %s→%s", args.tickers, args.start, args.end)
    run_backtest(args)
