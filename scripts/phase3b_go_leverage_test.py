#!/usr/bin/env python3
"""
Phase 3b — Bar-level (OHLCV) testing с учётом ГО/плеча MOEX фьючерсов.

Принципиальное отличие: вместо "купил на 50% капитала" используем
реальное плечо через гарантийное обеспечение (ГО).
Количество контрактов = floor(капитал * mu / ГО)

Direction 1: OI divergence на post-recovery тикерах
Direction 2: Trend-following на pre-recovery тикерах

Сравнение: без плеча (старый подход) vs с плечом (ГО).
"""
import sys, os, math
from datetime import datetime
from itertools import product

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import clickhouse_connect
import numpy as np
import pandas as pd
from config import CH_HOST, CH_PORT, CH_DB

SL_PCT = 0.05
COMMISSION = 2.0  # RUB per trade (flat, simplified)
CAPITAL = 100000.0
MU = 0.50  # fraction of capital per position
OUT_DIR = "reports/phase3"


def get_ch():
    return clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)


def get_go_map(ch, tickers):
    """Get GO (гарантийное обеспечение) for tickers from moex.securities."""
    if not tickers:
        return {}
    rows = ch.query(
        f"SELECT ticker, go_rub, lot, stepprice, minstep, leverage FROM moex.securities "
        f"WHERE ticker IN {tuple(tickers)}"
    ).result_rows
    go = {}
    for r in rows:
        go[r[0]] = {'go': float(r[1]), 'lot': int(r[2]), 'stepprice': float(r[3]),
                     'minstep': float(r[4]), 'leverage': float(r[5])}
    return go


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


def bar_level_with_go(df, divergence_arr, T, max_hold, go_info,
                       sl_pct=SL_PCT, capital=CAPITAL, mu=MU):
    """
    Bar-level backtest с ГО (реальное плечо).
    
    Размер позиции:
      contracts = floor(capital * mu / go)  # число контрактов
      capital_locked = contracts * go        # заблокировано под ГО
      остальной капитал свободен
    
    P&L на 1 контракт:
      long: (exit - entry) * stepprice / minstep * lot
      short: (entry - exit) * stepprice / minstep * lot
    
    Стоп: если цена прошла против нас на sl_pct * entry_price
    """
    opens = df['open'].values.astype(np.float64)
    highs = df['high'].values.astype(np.float64)
    lows = df['low'].values.astype(np.float64)
    closes = df['close'].values.astype(np.float64)
    n = len(df)
    
    go = go_info['go']
    lot = go_info['lot']
    stepprice = go_info['stepprice']
    minstep = go_info['minstep']
    
    # Стоимость 1 пункта = stepprice / minstep * lot
    # Но проще: delta_price * lot * (stepprice / minstep)
    price_to_rub = lot * stepprice / minstep if minstep > 0 else 1.0
    
    trades = []
    equity_curve = []
    cur_cap = float(capital)
    position = None
    
    for i in range(n):
        op, hi, lo, cl = opens[i], highs[i], lows[i], closes[i]
        
        # Manage position
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
                # P&L in RUB
                if pos['dir'] == 'LONG':
                    ret = (exit_px - pos['entry_px']) * price_to_rub
                else:
                    ret = (pos['entry_px'] - exit_px) * price_to_rub
                
                pnl = ret * pos['contracts']
                net_pnl = pnl - COMMISSION
                cur_cap += pos['locked_capital'] + net_pnl
                
                trades.append({
                    'dir': pos['dir'],
                    'entry_px': float(pos['entry_px']),
                    'exit_px': float(exit_px),
                    'contracts': pos['contracts'],
                    'ret_rub': float(pnl),
                    'pnl_net': float(net_pnl),
                    'bars_held': pos['bars_held'],
                    'ret_pct_capital': float(net_pnl / capital * 100),
                    'reason': reason,
                })
                position = None
        
        # Enter at this bar's close (signal from prev bar)
        if position is None and i > 0:
            div_prev = divergence_arr[i - 1]
            direction = None
            if div_prev > T:
                direction = 'SHORT'
            elif div_prev < -T:
                direction = 'LONG'
            
            if direction and go > 0:
                max_contracts = int(cur_cap * mu / go)
                if max_contracts >= 1:
                    locked = max_contracts * go
                    position = {
                        'dir': direction,
                        'entry_px': cl,
                        'entry_idx': i,
                        'bars_held': 0,
                        'contracts': max_contracts,
                        'locked_capital': locked,
                    }
                    cur_cap -= locked
        
        # MTM equity
        equity = cur_cap
        if position is not None:
            if position['dir'] == 'LONG':
                mtm = (cl - position['entry_px']) * price_to_rub * position['contracts']
            else:
                mtm = (position['entry_px'] - cl) * price_to_rub * position['contracts']
            equity += position['locked_capital'] + mtm
        equity_curve.append(float(equity))
    
    # Close remaining
    if position is not None:
        cl = closes[-1]
        if position['dir'] == 'LONG':
            ret = (cl - position['entry_px']) * price_to_rub
        else:
            ret = (position['entry_px'] - cl) * price_to_rub
        pnl = ret * position['contracts']
        net_pnl = pnl - COMMISSION
        cur_cap += position['locked_capital'] + net_pnl
        trades.append({
            'dir': position['dir'], 'entry_px': float(position['entry_px']),
            'exit_px': float(cl), 'contracts': position['contracts'],
            'ret_rub': float(pnl), 'pnl_net': float(net_pnl),
            'bars_held': position['bars_held'],
            'ret_pct_capital': float(net_pnl / capital * 100),
            'reason': 'end',
        })
    
    return trades, equity_curve


def bar_level_no_leverage(df, divergence_arr, T, max_hold,
                           sl_pct=SL_PCT, capital=CAPITAL, mu=MU):
    """Старый подход: покупаем на mu*capital, без ГО."""
    closes = df['close'].values.astype(np.float64)
    lows = df['low'].values.astype(np.float64)
    highs = df['high'].values.astype(np.float64)
    n = len(df)
    
    trades = []
    equity_curve = []
    cur_cap = float(capital)
    position = None
    
    for i in range(n):
        cl = closes[i]
        hi = highs[i]
        lo = lows[i]
        
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
                net_pnl = pnl - COMMISSION
                cur_cap += pos['cap_used'] + net_pnl
                trades.append({
                    'dir': pos['dir'], 'entry_px': float(pos['entry_px']),
                    'exit_px': float(exit_px),
                    'ret_pct': float(ret * 100),
                    'pnl_net': float(net_pnl),
                    'ret_pct_capital': float(net_pnl / capital * 100),
                    'bars_held': pos['bars_held'],
                    'reason': reason,
                })
                position = None
        
        if position is None and i > 0:
            div_prev = divergence_arr[i - 1]
            direction = None
            if div_prev > T:
                direction = 'SHORT'
            elif div_prev < -T:
                direction = 'LONG'
            
            if direction:
                entry_px = cl
                cap_used = cur_cap * mu
                if cap_used > 0 and entry_px > 0:
                    position = {
                        'dir': direction, 'entry_px': entry_px,
                        'bars_held': 0, 'cap_used': cap_used,
                    }
                    cur_cap -= cap_used
        
        equity = cur_cap
        if position is not None:
            if position['dir'] == 'LONG':
                mtm = (cl - position['entry_px']) / position['entry_px'] * position['cap_used']
            else:
                mtm = (position['entry_px'] - cl) / position['entry_px'] * position['cap_used']
            equity += position['cap_used'] + mtm
        equity_curve.append(float(equity))
    
    if position is not None:
        cl = closes[-1]
        if position['dir'] == 'LONG':
            ret = (cl - position['entry_px']) / position['entry_px']
        else:
            ret = (position['entry_px'] - cl) / position['entry_px']
        pnl = ret * position['cap_used']
        net_pnl = pnl - COMMISSION
        cur_cap += position['cap_used'] + net_pnl
        trades.append({
            'dir': position['dir'], 'ret_pct': float(ret * 100),
            'pnl_net': float(net_pnl),
            'ret_pct_capital': float(net_pnl / capital * 100),
            'bars_held': position['bars_held'], 'reason': 'end',
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
    # примерно 250 торговых дней * 19 часов * 12 баров/час = 57000 баров/год для 5m
    n_years = max(n_bars / 57000, 0.01)
    if final > 0:
        annual = ((final / cap) ** (1.0 / n_years) - 1) * 100
    else:
        annual = total_ret
    return {
        "trades": len(trades), "wr": round(wr, 2),
        "total_ret_pct": round(total_ret, 2), "max_dd_pct": round(max_dd, 2),
        "calmar": round(calmar, 4), "annual_pct": round(annual, 2),
        "avg_win": round(float(np.mean(wins)), 2) if len(wins) > 0 else 0.0,
        "avg_loss": round(float(np.mean(losses)), 2) if len(losses) > 0 else 0.0,
    }


# ═══════════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════════

def run_direction_1(ch, go_map, use_go=True):
    suffix = "WITH_GO" if use_go else "NO_LEVERAGE"
    label = f"OI Div with MМ, {'ГО/плечо' if use_go else 'без плеча (50% капитала)'}"
    
    print(f"\n{'='*60}\n  DIRECTION 1: {label}\n{'='*60}")
    
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
        
        z_fiz = {W: zscore_series(fiz_net, W) for W in W_VALS}
        z_yur = {W: zscore_series(yur_net, W) for W in W_VALS}
        
        best = None
        best_c = -999
        
        go_info = go_map.get(ticker, {'go': 10000, 'lot': 1, 'stepprice': 1, 'minstep': 1, 'leverage': 5})
        
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
                        if use_go:
                            trades, eq = bar_level_with_go(df, div, T, hold, go_info)
                        else:
                            trades, eq = bar_level_no_leverage(df, div, T, hold)
                        stats = compute_stats(trades, eq)
                        if stats['trades'] >= 15 and stats['calmar'] > best_c:
                            best_c = stats['calmar']
                            best = {
                                'dir': f'1-MM_{suffix}', 'ticker': ticker,
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


def run_sma_no_leverage(df, fast, slow, max_hold, capital=CAPITAL, mu=MU):
    """SMA crossover, без ГО."""
    closes = df['close'].values.astype(np.float64)
    highs, lows = df['high'].values.astype(np.float64), df['low'].values.astype(np.float64)
    n = len(df)
    max_w = max(fast, slow)
    s = pd.Series(closes)
    sma_fast = s.rolling(fast).mean().values
    sma_slow = s.rolling(slow).mean().values
    
    trades, eq_curve, cur_cap, position = [], [], float(capital), None
    prev_long = False
    
    for i in range(n):
        hi, lo, cl = highs[i], lows[i], closes[i]
        
        if i >= max_w and not np.isnan(sma_fast[i]) and not np.isnan(sma_slow[i]):
            long_sig = sma_fast[i] > sma_slow[i]
        else:
            long_sig = False
        
        if position is not None:
            pos = position
            pos['bars_held'] += 1
            should_exit, exit_px, reason = False, cl, None
            if pos['dir'] == 'LONG':
                stop = pos['entry_px'] * (1 - SL_PCT)
                if lo <= stop: exit_px = stop; should_exit = True; reason = 'stop_loss'
            else:
                stop = pos['entry_px'] * (1 + SL_PCT)
                if hi >= stop: exit_px = stop; should_exit = True; reason = 'stop_loss'
            if not should_exit and pos['bars_held'] >= max_hold:
                exit_px = cl; should_exit = True; reason = 'time_stop'
            if should_exit:
                ret = ((exit_px - pos['entry_px'])/pos['entry_px']) if pos['dir'] == 'LONG' else ((pos['entry_px']-exit_px)/pos['entry_px'])
                pnl = ret * pos['cap_used']
                cur_cap += pos['cap_used'] + pnl - COMMISSION
                trades.append({'dir': pos['dir'], 'pnl_net': float(pnl-COMMISSION), 'ret_pct_capital': float((pnl-COMMISSION)/capital*100), 'bars_held': pos['bars_held'], 'reason': reason})
                position = None
        
        if position is None and i >= max_w and long_sig != prev_long and long_sig:
            cap_used = cur_cap * mu
            if cap_used > 0: position = {'dir': 'LONG', 'entry_px': cl, 'bars_held': 0, 'cap_used': cap_used}; cur_cap -= cap_used
        prev_long = long_sig
        
        eq = cur_cap
        if position is not None:
            mtm = ((cl-position['entry_px'])/position['entry_px']*position['cap_used']) if position['dir']=='LONG' else ((position['entry_px']-cl)/position['entry_px']*position['cap_used'])
            eq += position['cap_used'] + mtm
        eq_curve.append(float(eq))
    
    if position is not None:
        cl = closes[-1]
        ret = ((cl-position['entry_px'])/position['entry_px']) if position['dir']=='LONG' else ((position['entry_px']-cl)/position['entry_px'])
        pnl = ret * position['cap_used']
        cur_cap += position['cap_used'] + pnl - COMMISSION
        trades.append({'dir': position['dir'], 'pnl_net': float(pnl-COMMISSION), 'ret_pct_capital': float((pnl-COMMISSION)/capital*100), 'bars_held': position['bars_held'], 'reason': 'end'})
    return trades, eq_curve


def run_direction_2(ch, use_go=False):
    print(f"\n{'='*60}\n  DIRECTION 2: Trend-following NO MM\n{'='*60}")
    
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
            print(f"  ⚠ {label}: no data"); continue
        print(f"  {len(df)} bars")
        
        best, best_c = None, -999
        for fast, slow in [(5, 20), (10, 40), (5, 40)]:
            for hold in [5, 10, 15]:
                trades, eq = run_sma_no_leverage(df, fast, slow, hold)
                stats = compute_stats(trades, eq)
                if stats['trades'] >= 15 and stats['calmar'] > best_c:
                    best_c = stats['calmar']
                    best = {'dir': '2-NOMM', 'ticker': label, 'sig': 'SMA crossover', 'params': f'SMA({fast},{slow}) hold={hold}', **stats}
        
        if best:
            results.append(best)
            print(f"  ★ {best['sig']} {best['params']}: WR={best['wr']:.1f}% Calmar={best['calmar']:.3f} Ret={best['total_ret_pct']:.1f}% Ann={best['annual_pct']:.1f}% Trades={best['trades']}")
    return results


def print_table(all_results, title):
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}")
    if all_results:
        print(f"{'Ticker':<14} {'Signal':<20} {'Params':<25} {'WR':>6} {'Ret%':>8} {'DD%':>6} {'Calmar':>8} {'Ann%':>8} {'Trades':>7}")
        print("-" * 110)
        for r in sorted(all_results, key=lambda x: x['calmar'], reverse=True):
            print(f"{r['ticker']:<14} {r['sig']:<20} {r['params']:<25} "
                  f"{r['wr']:>5.1f}% {r['total_ret_pct']:>7.1f}% {r['max_dd_pct']:>5.1f}% "
                  f"{r['calmar']:>7.3f} {r['annual_pct']:>7.1f}% {r['trades']:>5d}")
    else:
        print("  (no results)")


def main():
    ch = get_ch()
    os.makedirs(OUT_DIR, exist_ok=True)
    
    print(f"PHASE 3b — Bar-level с ГО/плечом | {datetime.now()}")
    print(f"Капитал: {CAPITAL:,} RUB, mu={MU}, стоп={SL_PCT*100:.0f}%")
    
    # Get GO data for our tickers
    all_tickers = ['BR', 'AF', 'IMOEXF', 'SR', 'Eu', 'CR', 'GD', 'PT']
    go_map = get_go_map(ch, all_tickers)
    print("\nГО по тикерам:")
    for t, v in sorted(go_map.items()):
        print(f"  {t}: ГО={v['go']:.0f} RUB, lot={v['lot']}, плечо={v['leverage']:.1f}x")
    
    # Direction 1: без плеча (старый подход)
    r1_no_go = run_direction_1(ch, go_map, use_go=False)
    
    # Direction 1: с ГО/плечом
    r1_with_go = run_direction_1(ch, go_map, use_go=True)
    
    # Direction 2: без плеча (SMA crossover не использует ГО)
    r2 = run_direction_2(ch)
    
    # ── Summary ──
    print(f"\n{'#'*70}")
    print(f"# ИТОГОВАЯ ТАБЛИЦА — СРАВНЕНИЕ С ПЛЕЧОМ И БЕЗ")
    print(f"{'#'*70}")
    
    print_table(r1_no_go, "Direction 1: OI Divergence БЕЗ плеча (50% капитала)")
    print_table(r1_with_go, "Direction 1: OI Divergence С ГО/плечом")
    print_table(r2, "Direction 2: Trend-following без MM")
    
    # Save combined
    all_r = r1_no_go + r1_with_go + r2
    if all_r:
        df_out = pd.DataFrame(all_r)
        csv_path = os.path.join(OUT_DIR, "phase3b_results.csv")
        df_out.to_csv(csv_path, index=False)
        print(f"\n✅ {csv_path} ({len(df_out)} rows)")
    
    print(f"\nDone: {datetime.now()}")


if __name__ == "__main__":
    main()
