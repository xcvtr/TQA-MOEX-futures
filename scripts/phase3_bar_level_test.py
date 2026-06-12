#!/usr/bin/env python3
"""
Phase 3 — Bar-level (OHLCV) testing.

Direction 1 (MM + post-recovery):
  - OI divergence V1/V2/V3
  - Tickers with known recovery dates
Direction 2 (no MM):
  - SMA crossover, Donchian breakout on pre-recovery/exclude tickers

Fast version: precompute z-scores outside loop.
"""
import sys, os, csv, math
from datetime import datetime
from itertools import product

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import clickhouse_connect
import numpy as np
import pandas as pd
from config import CH_HOST, CH_PORT, CH_DB

SL_PCT = 0.05
COMMISSION = 2.0
CAPITAL = 100000.0
OUT_DIR = "reports/phase3"

def get_ch():
    return clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)

def zscore_series(series, window):
    s = pd.Series(series.astype(np.float64))
    mu = s.rolling(window, min_periods=window).mean()
    sd = s.rolling(window, min_periods=window).std()
    result = (s - mu) / sd
    result = result.fillna(0.0).replace([np.inf, -np.inf], 0.0)
    return result.values.astype(np.float64)

def load_data(ch, ticker, start_date=None, end_date=None):
    conditions = ["o.symbol = {t:String}", "p.symbol = {t:String}"]
    params = {"t": ticker}
    if start_date:
        conditions.append("p.time >= {start:String}")
        params["start"] = start_date
    if end_date:
        conditions.append("p.time < {end:String}")
        params["end"] = end_date
    where = " AND ".join(conditions)
    query = f"""
    SELECT p.time, p.open, p.high, p.low, p.close, p.volume,
           o.fiz_buy, o.fiz_sell, o.yur_buy, o.yur_sell, o.total_oi
    FROM moex.prices_5m_oi AS o
    INNER JOIN moex.prices_5m AS p ON p.symbol = o.symbol AND p.time = o.time
    WHERE {where} ORDER BY p.time
    """
    rows = ch.query(query, parameters=params).result_rows
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=[
        "time","open","high","low","close","volume",
        "fiz_buy","fiz_sell","yur_buy","yur_sell","total_oi"
    ])
    return df


def bar_level_backtest_fast(df, divergence_arr, T, max_hold, sl_pct=SL_PCT, commission=COMMISSION, capital=CAPITAL):
    """
    Fast bar-level backtest using precomputed divergence array.
    divergence_arr[i] = value of divergence at bar i (already z-scored).
    Long when divergence < -T, Short when divergence > T.
    """
    opens = df['open'].values.astype(np.float64)
    highs = df['high'].values.astype(np.float64)
    lows = df['low'].values.astype(np.float64)
    closes = df['close'].values.astype(np.float64)
    n = len(df)
    
    trades = []
    equity_curve = []
    cur_cap = float(capital)
    position = None  # {dir, entry_px, entry_idx, bars_held, cap_used}
    
    for i in range(n):
        op, hi, lo, cl = opens[i], highs[i], lows[i], closes[i]
        
        # Manage existing position
        if position is not None:
            pos = position
            pos['bars_held'] += 1
            should_exit = False
            exit_px = cl
            reason = None
            
            if pos['dir'] == 'LONG':
                stop = pos['entry_px'] * (1 - sl_pct)
                if lo <= stop:
                    exit_px = stop
                    should_exit = True
                    reason = 'stop_loss'
            else:
                stop = pos['entry_px'] * (1 + sl_pct)
                if hi >= stop:
                    exit_px = stop
                    should_exit = True
                    reason = 'stop_loss'
            
            if not should_exit and pos['bars_held'] >= max_hold:
                exit_px = cl
                should_exit = True
                reason = 'time_stop'
            
            if should_exit:
                if pos['dir'] == 'LONG':
                    ret = (exit_px - pos['entry_px']) / pos['entry_px']
                else:
                    ret = (pos['entry_px'] - exit_px) / pos['entry_px']
                pnl = ret * pos['cap_used']
                net_pnl = pnl - commission
                cur_cap += pos['cap_used'] + net_pnl
                trades.append({
                    'dir': pos['dir'], 'entry_px': float(pos['entry_px']),
                    'exit_px': float(exit_px), 'ret': float(ret * 100),
                    'pnl_net': float(net_pnl), 'bars_held': pos['bars_held'],
                    'reason': reason,
                })
                position = None
        
        # Enter new position (at close, signal from prev bar)
        if position is None and i > 0:
            div_prev = divergence_arr[i - 1]
            direction = None
            if div_prev > T:
                direction = 'SHORT'
            elif div_prev < -T:
                direction = 'LONG'
            
            if direction:
                entry_px = cl  # enter at this bar's close
                cap_used = cur_cap * 0.5  # mu=0.50
                if cap_used > 0 and entry_px > 0:
                    position = {
                        'dir': direction, 'entry_px': entry_px,
                        'entry_idx': i, 'bars_held': 0, 'cap_used': cap_used,
                    }
                    cur_cap -= cap_used
        
        # MTM equity
        equity = cur_cap
        if position is not None:
            if position['dir'] == 'LONG':
                mtm = (cl - position['entry_px']) / position['entry_px'] * position['cap_used']
            else:
                mtm = (position['entry_px'] - cl) / position['entry_px'] * position['cap_used']
            equity += position['cap_used'] + mtm
        equity_curve.append(float(equity))
    
    # Close remaining position
    if position is not None:
        cl = closes[-1]
        if position['dir'] == 'LONG':
            ret = (cl - position['entry_px']) / position['entry_px']
        else:
            ret = (position['entry_px'] - cl) / position['entry_px']
        pnl = ret * position['cap_used']
        net_pnl = pnl - commission
        cur_cap += position['cap_used'] + net_pnl
        trades.append({
            'dir': position['dir'], 'entry_px': float(position['entry_px']),
            'exit_px': float(cl), 'ret': float(ret * 100),
            'pnl_net': float(net_pnl), 'bars_held': position['bars_held'],
            'reason': 'end_of_data',
        })
    
    return trades, equity_curve


def compute_stats(trades, equity_curve, cap=CAPITAL):
    if not trades:
        return {"trades": 0}
    returns = np.array([t['pnl_net'] for t in trades])
    wins = returns[returns > 0]
    losses = returns[returns <= 0]
    wr = len(wins) / len(returns) * 100 if len(returns) > 0 else 0.0
    final = equity_curve[-1] if equity_curve else cap
    total_ret = (final / cap - 1) * 100
    eq = np.array(equity_curve)
    peak = np.maximum.accumulate(eq)
    dd = (peak - eq) / peak * 100
    max_dd = float(np.max(dd)) if len(dd) > 0 else 0.0
    calmar = total_ret / max_dd if max_dd > 1e-6 else 0.0
    n_bars = len(equity_curve)
    n_years = n_bars / (250 * 19 * 12)
    if n_years > 0.5 and final > 0:
        annual = ((final / cap) ** (1.0 / n_years) - 1) * 100
    else:
        annual = total_ret
    return {
        "trades": len(trades), "wr": round(wr, 2),
        "total_ret_pct": round(total_ret, 2), "max_dd_pct": round(max_dd, 2),
        "calmar": round(calmar, 4), "annual_pct": round(annual, 2),
    }


def run_direction_1(ch):
    """OI divergence on post-recovery tickers. Precompute z-scores outside loop."""
    print(f"\n{'='*60}\n  DIRECTION 1: OI Div with MM (post-recovery)\n{'='*60}")
    
    tickers = {
        'BR': '2026-04-01', 'IMOEXF': '2024-07-01',
        'AF': '2025-08-01', 'SR': '2025-07-01',
        'Eu': '2022-10-01', 'CR': None,
    }
    
    W_VALS = [20, 40]
    T_VALS = [1.5, 2.0]
    HOLD_VALS = [5, 10]
    
    results = []
    
    for ticker, start in tickers.items():
        print(f"\n  {ticker} (from {start or 'beginning'})...")
        df = load_data(ch, ticker, start_date=start)
        if df is None or len(df) < 100:
            print(f"  ⚠ {ticker}: no data")
            continue
        print(f"  {len(df)} bars")
        
        fiz_net = (df['fiz_buy'].values - df['fiz_sell'].values).astype(np.float64)
        yur_net = (df['yur_buy'].values - df['yur_sell'].values).astype(np.float64)
        
        # Precompute z-scores once
        z_fiz = {W: zscore_series(fiz_net, W) for W in W_VALS}
        z_yur = {W: zscore_series(yur_net, W) for W in W_VALS}
        
        best = None
        best_c = -999
        
        for variant in [1, 2, 3]:
            for W in W_VALS:
                fiz_z = z_fiz[W]
                yur_z = z_yur[W]
                
                if variant == 1:
                    div = fiz_z - yur_z
                elif variant == 2:
                    div = yur_z * 2.0 - fiz_z
                else:
                    div = fiz_z - yur_z
                    div[(fiz_z * yur_z) >= 0] = 0.0
                
                for T in T_VALS:
                    for hold in HOLD_VALS:
                        trades, eq = bar_level_backtest_fast(df, div, T, hold)
                        stats = compute_stats(trades, eq)
                        if stats['trades'] >= 15 and stats['calmar'] > best_c:
                            best_c = stats['calmar']
                            best = {
                                'dir': '1-MM', 'ticker': ticker,
                                'sig': f'OI Div V{variant}',
                                'params': f'W={W} T={T} hold={hold}',
                                **stats
                            }
        
        if best:
            results.append(best)
            print(f"  ★ {best['sig']} {best['params']}: WR={best['wr']:.1f}% "
                  f"Calmar={best['calmar']:.3f} Ret={best['total_ret_pct']:.1f}% "
                  f"DD={best['max_dd_pct']:.1f}% Ann={best['annual_pct']:.1f}% "
                  f"Trades={best['trades']}")
    
    return results


def run_sma_backtest(df, fast, slow, max_hold):
    """SMA crossover bar-level backtest."""
    closes = df['close'].values.astype(np.float64)
    opens, highs, lows = df['open'].values.astype(np.float64), df['high'].values.astype(np.float64), df['low'].values.astype(np.float64)
    n = len(df)
    max_w = max(fast, slow)
    
    # Precompute SMAs
    s = pd.Series(closes)
    sma_fast = s.rolling(fast).mean().values
    sma_slow = s.rolling(slow).mean().values
    
    trades = []
    eq_curve = []
    cur_cap = float(CAPITAL)
    position = None
    prev_long = False
    
    for i in range(n):
        op, hi, lo, cl = opens[i], highs[i], lows[i], closes[i]
        
        if i >= max_w and not np.isnan(sma_fast[i]) and not np.isnan(sma_slow[i]):
            long_sig = sma_fast[i] > sma_slow[i]
        else:
            long_sig = False
        
        # Manage position
        if position is not None:
            pos = position
            pos['bars_held'] += 1
            should_exit = False
            exit_px = cl
            reason = None
            
            if pos['dir'] == 'LONG':
                stop = pos['entry_px'] * (1 - SL_PCT)
                if lo <= stop:
                    exit_px = stop; should_exit = True; reason = 'stop_loss'
            else:
                stop = pos['entry_px'] * (1 + SL_PCT)
                if hi >= stop:
                    exit_px = stop; should_exit = True; reason = 'stop_loss'
            
            if not should_exit and pos['bars_held'] >= max_hold:
                exit_px = cl; should_exit = True; reason = 'time_stop'
            
            if should_exit:
                ret = ((exit_px - pos['entry_px']) / pos['entry_px']) if pos['dir'] == 'LONG' else ((pos['entry_px'] - exit_px) / pos['entry_px'])
                pnl = ret * pos['cap_used']
                cur_cap += pos['cap_used'] + pnl - COMMISSION
                trades.append({
                    'dir': pos['dir'], 'ret': float(ret * 100),
                    'pnl_net': float(pnl - COMMISSION), 'bars_held': pos['bars_held'],
                    'reason': reason,
                })
                position = None
        
        # Entry (on signal transition, enter at close)
        if position is None and i >= max_w and long_sig != prev_long and long_sig:
            dir = 'LONG' if long_sig else 'SHORT'
            cap_used = cur_cap * 0.5
            if cap_used > 0:
                position = {'dir': dir, 'entry_px': cl, 'entry_idx': i, 'bars_held': 0, 'cap_used': cap_used}
                cur_cap -= cap_used
        
        prev_long = long_sig
        
        # MTM
        eq = cur_cap
        if position is not None:
            mtm = ((cl - position['entry_px']) / position['entry_px'] * position['cap_used']) if position['dir'] == 'LONG' else ((position['entry_px'] - cl) / position['entry_px'] * position['cap_used'])
            eq += position['cap_used'] + mtm
        eq_curve.append(float(eq))
    
    if position is not None:
        cl = closes[-1]
        ret = ((cl - position['entry_px']) / position['entry_px']) if position['dir'] == 'LONG' else ((position['entry_px'] - cl) / position['entry_px'])
        pnl = ret * position['cap_used']
        cur_cap += position['cap_used'] + pnl - COMMISSION
        trades.append({'dir': position['dir'], 'ret': float(ret * 100), 'pnl_net': float(pnl - COMMISSION), 'bars_held': position['bars_held'], 'reason': 'end'})
    
    return trades, eq_curve


def run_donchian_backtest(df, period, max_hold):
    """Donchian breakout bar-level backtest."""
    opens = df['open'].values.astype(np.float64)
    highs = df['high'].values.astype(np.float64)
    lows = df['low'].values.astype(np.float64)
    closes = df['close'].values.astype(np.float64)
    n = len(df)
    
    # Precompute rolling max/min
    h_series = pd.Series(highs)
    l_series = pd.Series(lows)
    upper = h_series.rolling(period).max().shift(1).values
    lower = l_series.rolling(period).min().shift(1).values
    
    trades = []
    eq_curve = []
    cur_cap = float(CAPITAL)
    position = None
    
    for i in range(n):
        op, hi, lo, cl = opens[i], highs[i], lows[i], closes[i]
        
        if i >= period:
            long_sig = not np.isnan(upper[i]) and cl > upper[i]
            short_sig = not np.isnan(lower[i]) and cl < lower[i]
        else:
            long_sig = short_sig = False
        
        if position is not None:
            pos = position
            pos['bars_held'] += 1
            should_exit = False
            exit_px = cl
            reason = None
            
            if pos['dir'] == 'LONG':
                stop = pos['entry_px'] * (1 - SL_PCT)
                if lo <= stop:
                    exit_px = stop; should_exit = True; reason = 'stop_loss'
            else:
                stop = pos['entry_px'] * (1 + SL_PCT)
                if hi >= stop:
                    exit_px = stop; should_exit = True; reason = 'stop_loss'
            
            if not should_exit and pos['bars_held'] >= max_hold:
                exit_px = cl; should_exit = True; reason = 'time_stop'
            
            if should_exit:
                ret = ((exit_px - pos['entry_px']) / pos['entry_px']) if pos['dir'] == 'LONG' else ((pos['entry_px'] - exit_px) / pos['entry_px'])
                pnl = ret * pos['cap_used']
                cur_cap += pos['cap_used'] + pnl - COMMISSION
                trades.append({
                    'dir': pos['dir'], 'ret': float(ret * 100),
                    'pnl_net': float(pnl - COMMISSION), 'bars_held': pos['bars_held'],
                    'reason': reason,
                })
                position = None
        
        if position is None and i >= period:
            dir = None
            if long_sig:
                dir = 'LONG'
            elif short_sig:
                dir = 'SHORT'
            if dir:
                cap_used = cur_cap * 0.5
                if cap_used > 0:
                    position = {'dir': dir, 'entry_px': cl, 'bars_held': 0, 'cap_used': cap_used}
                    cur_cap -= cap_used
        
        eq = cur_cap
        if position is not None:
            mtm = ((cl - position['entry_px']) / position['entry_px'] * position['cap_used']) if position['dir'] == 'LONG' else ((position['entry_px'] - cl) / position['entry_px'] * position['cap_used'])
            eq += position['cap_used'] + mtm
        eq_curve.append(float(eq))
    
    if position is not None:
        cl = closes[-1]
        ret = ((cl - position['entry_px']) / position['entry_px']) if position['dir'] == 'LONG' else ((position['entry_px'] - cl) / position['entry_px'])
        pnl = ret * position['cap_used']
        cur_cap += position['cap_used'] + pnl - COMMISSION
        trades.append({'dir': position['dir'], 'ret': float(ret * 100), 'pnl_net': float(pnl - COMMISSION), 'bars_held': position['bars_held'], 'reason': 'end'})
    
    return trades, eq_curve


def run_direction_2(ch):
    """Trend-following on tickers WITHOUT MM."""
    print(f"\n{'='*60}\n  DIRECTION 2: Trend-following NO MM (pre-recovery)\n{'='*60}")
    
    datasets = [
        ('GD', None, None), ('PT', None, None),
        ('BR', '2022-07-01', '2026-04-01'),
        ('AF', '2022-05-01', '2025-08-01'),
        ('SR', '2024-11-01', '2025-07-01'),
    ]
    
    results = []
    
    for ticker, start, end in datasets:
        label = f'{ticker}_PRE' if start else f'{ticker}_EXCLUDE'
        print(f"\n  {label}...")
        df = load_data(ch, ticker, start_date=start, end_date=end)
        if df is None or len(df) < 100:
            print(f"  ⚠ {label}: no data")
            continue
        print(f"  {len(df)} bars")
        
        best = None
        best_c = -999
        
        # SMA crossover
        for fast, slow in [(5, 20), (10, 40), (5, 40)]:
            for hold in [5, 10, 15]:
                trades, eq = run_sma_backtest(df, fast, slow, hold)
                stats = compute_stats(trades, eq)
                if stats['trades'] >= 15 and stats['calmar'] > best_c:
                    best_c = stats['calmar']
                    best = {
                        'dir': '2-NOMM', 'ticker': label,
                        'sig': 'SMA crossover',
                        'params': f'SMA({fast},{slow}) hold={hold}',
                        **stats
                    }
        
        # Donchian breakout
        for period in [10, 20, 30]:
            for hold in [5, 10, 15]:
                trades, eq = run_donchian_backtest(df, period, hold)
                stats = compute_stats(trades, eq)
                if stats['trades'] >= 15 and stats['calmar'] > best_c:
                    best_c = stats['calmar']
                    best = {
                        'dir': '2-NOMM', 'ticker': label,
                        'sig': 'Donchian breakout',
                        'params': f'period={period} hold={hold}',
                        **stats
                    }
        
        if best:
            results.append(best)
            print(f"  ★ {best['sig']} {best['params']}: WR={best['wr']:.1f}% "
                  f"Calmar={best['calmar']:.3f} Ret={best['total_ret_pct']:.1f}% "
                  f"DD={best['max_dd_pct']:.1f}% Ann={best['annual_pct']:.1f}% "
                  f"Trades={best['trades']}")
    
    return results


def main():
    ch = get_ch()
    os.makedirs(OUT_DIR, exist_ok=True)
    print(f"PHASE 3 — Bar-level OHLCV truth | {datetime.now()}")
    
    r1 = run_direction_1(ch)
    r2 = run_direction_2(ch)
    
    print(f"\n{'='*70}")
    print("  SUMMARY: WHAT WORKS vs WHAT DOESN'T")
    print("  (bar-level OHLCV: stop on low/high, entry at close, MTM each bar)")
    print(f"{'='*70}")
    
    all_r = r1 + r2
    if all_r:
        print(f"\n{'Dir':<8} {'Ticker':<14} {'Signal':<20} {'Params':<30} "
              f"{'WR':>6} {'Ret%':>8} {'DD%':>6} {'Calmar':>8} {'Ann%':>8} {'Trades':>7}")
        print("-" * 120)
        for r in sorted(all_r, key=lambda x: x['calmar'], reverse=True):
            print(f"{r['dir']:<8} {r['ticker']:<14} {r['sig']:<20} {r['params']:<30} "
                  f"{r['wr']:>5.1f}% {r['total_ret_pct']:>7.1f}% {r['max_dd_pct']:>5.1f}% "
                  f"{r['calmar']:>7.3f} {r['annual_pct']:>7.1f}% {r['trades']:>5d}")
        
        df_out = pd.DataFrame(all_r)
        csv_path = os.path.join(OUT_DIR, "phase3_results.csv")
        df_out.to_csv(csv_path, index=False)
        print(f"\n✅ {csv_path}")
    
    print(f"\nDone: {datetime.now()}")


if __name__ == "__main__":
    main()
