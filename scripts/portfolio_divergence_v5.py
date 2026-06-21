#!/usr/bin/env python3
"""Портфельный тест divergence strategy с grid search под каждый тикер.

Двухэтапный подход:
1. Grid search per ticker → best config (max Calmar)
2. Портфель equal-weight с найденными конфигами

v5 improvements:
  — Volume filter: skip signal if put_orders below median volume
  — Divergence strength sizing: position size scales with divergence strength
  — Dynamic hold: hold period adjusted by ATR volatility ratio

Факторы реализма:
- Slippage 0.02% на entry + exit
- Комиссия 0.05% MOEX equity на entry + exit
- MTM equity curve для честного DD
- Полный реинвест

Usage:
  python3 scripts/portfolio_divergence_v5.py --skip-grid
  python3 scripts/portfolio_divergence_v5.py --skip-grid --mode base|vol-filter|div-strength|dyn-hold|all
"""
import subprocess, sys, numpy as np

CH = "10.0.0.63"
DB = "moex_algopack_v2"

SLIPPAGE = 0.0002
COMMISSION = 0.0005

# Grid search params
GRID_DIV = [10, 15, 20, 30]
GRID_HOLD = [3, 5, 10]
GRID_STOP = [0.01, 0.02, 0.03, 0.05]

# Tickers
TICKERS = ['AFKS', 'AFLT', 'CHMF']

# Fallback configs from checkpoint 070
FALLBACK = {
    'AFKS': {'div_thr': 15, 'hold': 10, 'stop_pct': 0.01},
    'AFLT': {'div_thr': 15, 'hold': 10, 'stop_pct': 0.01},
    'CHMF': {'div_thr': 20, 'hold': 5,  'stop_pct': 0.02},
    'BELU': {'div_thr': 30, 'hold': 10, 'stop_pct': 0.02},
}


def ch(sql):
    r = subprocess.run(['clickhouse-client', '--host', CH, '-d', DB, '--query', sql],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise Exception(r.stderr.strip())
    lines = r.stdout.strip().split('\n')
    return [l.split('\t') for l in lines if l.strip()]


def load_ticker(secid, start, end):
    """Load aligned orderstats + tradestats data."""
    sql = f"""
        SELECT o.tradedate, o.tradetime,
               o.put_orders_b, o.put_orders_s,
               t.pr_open, t.pr_close, t.pr_high, t.pr_low,
               t.trades_b, t.trades_s
        FROM orderstats_local o
        JOIN tradestats_local t
          ON o.tradedate = t.tradedate AND o.secid = t.ticker AND o.tradetime = t.tradetime
        WHERE o.secid = '{secid}'
          AND o.tradedate >= '{start}' AND o.tradedate <= '{end}'
        ORDER BY o.tradedate, o.tradetime
        FORMAT TabSeparated
    """
    raw = ch(sql)
    if not raw or len(raw) < 500:
        return None
    
    n = len(raw)
    
    def ci(i): return np.array([int(r[i]) if r[i] and r[i] != '\\N' else 0 for r in raw])
    def cf(i): return np.array([float(r[i]) if r[i] and r[i] != '\\N' else 0.0 for r in raw])
    
    put_b = ci(2); put_s = ci(3)
    opn = cf(4); close = cf(5); high = cf(6); low = cf(7)
    tb = cf(8); ts = cf(9)
    
    # Imbalances
    tot_put = put_b + put_s
    tot_trades = tb + ts
    o_imb = np.where(tot_put > 0, (put_b - put_s) / tot_put * 100, 0)
    t_imb = np.where(tot_trades > 0, (tb - ts) / tot_trades * 100, 0)
    
    # Smooth o_imb
    w = 5
    o_imb_sm = np.copy(o_imb)
    for i in range(w, n):
        o_imb_sm[i] = np.mean(o_imb[i-w:i])
    
    # --- Volume filter data ---
    put_vol = put_b + put_s
    med_put = float(np.median(put_vol))
    put_above_med = put_vol >= med_put
    
    # --- ATR for dynamic hold ---
    atr_period = 20
    true_ranges = np.zeros(n)
    true_ranges[1:] = np.maximum(
        high[1:] - low[1:],
        np.maximum(
            np.abs(high[1:] - close[:-1]),
            np.abs(low[1:] - close[:-1])
        )
    )
    atr_arr = np.zeros(n)
    for i in range(atr_period - 1, n):
        atr_arr[i] = np.mean(true_ranges[i-atr_period+1:i+1])
    
    base_atr = 0.003  # средняя относительная волатильность (0.3% за бар)
    
    return dict(opn=opn, close=close, high=high, low=low,
                o_imb=o_imb_sm, t_imb=t_imb, n=n, dates=np.array([r[0] for r in raw]),
                put_vol=put_vol, med_put=med_put, put_above_med=put_above_med,
                atr=atr_arr, base_atr=base_atr)


def backtest(data, div_thr, hold, stop_pct, start_capital=100000.0,
             put_volume_filter=False, div_strength_sizing=False, dynamic_hold=False):
    """Run backtest with MTM equity curve.
    
    Parameters:
      put_volume_filter: skip signal if put_orders below median volume
      div_strength_sizing: position size scales with divergence / div_thr
      dynamic_hold: hold period adjusted by ATR (base_atr / current_atr)
    
    Returns dict with ret, dd, trades, wr, eq_curve, final_capital.
    """
    n = data['n']
    opn = data['opn']; close = data['close']; high = data['high']; low = data['low']
    o_imb = data['o_imb']; t_imb = data['t_imb']
    
    put_above_med = data['put_above_med']
    atr_arr = data['atr']
    base_atr = data['base_atr']
    
    cash = float(start_capital)
    pos = 0         # 0=none, 1=long, -1=short
    entry_price = 0.0
    entry_bar = 0
    allocated = 0.0  # capital allocated to open position
    base_alloc = start_capital * 0.40  # 40% per position (conservative for multi-ticker)
    
    trade_count = 0
    win_count = 0
    eq_curve = [cash]
    
    for i in range(10, n - 2):
        # --- Close position ---
        if pos != 0:
            bars_held = i - entry_bar
            cur_price = close[i]
            
            # Dynamic hold period: scale by ATR volatility ratio
            # atr_rel = atr / close_price (relative volatility)
            # When atr_rel > base_atr (volatile) → hold shorter
            # When atr_rel < base_atr (calm) → hold longer
            hold_actual = hold
            if dynamic_hold and atr_arr[i] > 0 and close[i] > 0:
                atr_rel = atr_arr[i] / close[i]
                ratio = base_atr / atr_rel
                # Clamp ratio to [0.3, 3.0] to avoid extreme holds
                ratio = max(0.3, min(3.0, ratio))
                hold_actual = max(1, int(round(hold * ratio)))
            
            # Check stop
            stopped = False
            exit_price = cur_price
            if pos == 1 and low[i] <= entry_price * (1 - stop_pct):
                stopped = True
                exit_price = entry_price * (1 - stop_pct)
            elif pos == -1 and high[i] >= entry_price * (1 + stop_pct):
                stopped = True
                exit_price = entry_price * (1 + stop_pct)
            
            if stopped:
                ret = (exit_price / entry_price - 1) * pos
                costs = allocated * (SLIPPAGE + COMMISSION)
                cash += allocated * (1 + ret) - costs
                trade_count += 1
                pos = 0
                allocated = 0.0
            elif bars_held >= hold_actual:
                ret = (cur_price / entry_price - 1) * pos
                costs = allocated * (SLIPPAGE + COMMISSION)
                cash += allocated * (1 + ret) - costs
                if ret > 0:
                    win_count += 1
                trade_count += 1
                pos = 0
                allocated = 0.0
        
        # --- New signal ---
        if pos == 0 and i > 10:
            o = o_imb[i]; t = t_imb[i]
            div = abs(o - t)
            
            # Volume filter: skip if put volume below median
            vol_ok = True
            if put_volume_filter and not put_above_med[i]:
                vol_ok = False
            
            if vol_ok and div > div_thr:
                # Position size
                pos_size = base_alloc
                if div_strength_sizing:
                    strength = min(3.0, div / div_thr)
                    pos_size = base_alloc * strength
                
                entry_size = min(pos_size, cash)
                
                if t > abs(o) * 0.5 and t > 5:
                    pos = 1
                    entry_price = opn[i + 1]
                    entry_bar = i + 1
                    allocated = entry_size
                    costs = entry_size * (SLIPPAGE + COMMISSION)
                    cash -= allocated + costs
                elif t < -abs(o) * 0.5 and t < -5:
                    pos = -1
                    entry_price = opn[i + 1]
                    entry_bar = i + 1
                    allocated = entry_size
                    costs = entry_size * (SLIPPAGE + COMMISSION)
                    cash -= allocated + costs
        
        # --- MTM equity ---
        if pos != 0:
            cur_val = allocated * (close[i] / entry_price) if pos == 1 else allocated * (entry_price / close[i])
            eq_curve.append(cash + cur_val)
        else:
            eq_curve.append(cash)
    
    # --- Close last position ---
    if pos != 0:
        ret = (close[-1] / entry_price - 1) * pos
        costs = allocated * (SLIPPAGE + COMMISSION)
        cash += allocated * (1 + ret) - costs
        if ret > 0:
            win_count += 1
        trade_count += 1
        pos = 0
        allocated = 0.0
    
    eq_curve.append(cash)
    
    total_ret = (cash / start_capital - 1) * 100
    eq_arr = np.array(eq_curve)
    peak = np.maximum.accumulate(eq_arr)
    dd_arr = (peak - eq_arr) / peak * 100
    max_dd = float(np.max(dd_arr))
    wr = win_count / max(trade_count, 1) * 100 if trade_count > 0 else 0
    
    return {
        'ret': total_ret, 'dd': max_dd, 'trades': trade_count,
        'wr': wr, 'eq_curve': list(eq_curve), 'final_capital': cash
    }


def grid_search(data, put_volume_filter=False, div_strength_sizing=False, dynamic_hold=False):
    """Find best config by max Calmar."""
    best = {'calmar': 0}
    
    for div_thr in GRID_DIV:
        for hold in GRID_HOLD:
            for stop in GRID_STOP:
                res = backtest(data, div_thr, hold, stop,
                               put_volume_filter=put_volume_filter,
                               div_strength_sizing=div_strength_sizing,
                               dynamic_hold=dynamic_hold)
                if res['trades'] < 5:
                    continue
                calmar = res['ret'] / max(res['dd'], 0.01)
                if calmar > best['calmar']:
                    best = {
                        'div_thr': div_thr, 'hold': hold, 'stop_pct': stop,
                        'ret': res['ret'], 'dd': res['dd'],
                        'trades': res['trades'], 'wr': res['wr'],
                        'calmar': calmar
                    }
    return best


def run_mode(mode_name, all_data, skip_grid):
    """Run a single backtest mode and return portfolio results + per-ticker results."""
    put_volume_filter = (mode_name in ('vol-filter', 'all'))
    div_strength_sizing = (mode_name in ('div-strength', 'all'))
    dynamic_hold = (mode_name in ('dyn-hold', 'all'))
    
    label = {
        'base': 'BASE (без улучшений)',
        'vol-filter': 'VOL-FILTER (put volume filter)',
        'div-strength': 'DIV-STRENGTH (divergence sizing)',
        'dyn-hold': 'DYN-HOLD (ATR dynamic hold)',
        'all': 'ALL (все улучшения)',
    }
    
    print(f"\n{'='*60}")
    print(f"  MODE: {label[mode_name]}")
    print(f"{'='*60}")
    
    # Phase 1: find configs
    print(f"── Phase 1: {'Grid search' if not skip_grid else 'Fallback configs'} ──")
    configs = {}
    per_ticker = {}
    
    for t in TICKERS:
        if t not in all_data:
            continue
        
        if skip_grid:
            cfg = FALLBACK[t]
            res = backtest(all_data[t], cfg['div_thr'], cfg['hold'], cfg['stop_pct'],
                           put_volume_filter=put_volume_filter,
                           div_strength_sizing=div_strength_sizing,
                           dynamic_hold=dynamic_hold)
            configs[t] = cfg
            res['calmar'] = res['ret'] / max(res['dd'], 0.01)
            per_ticker[t] = res
            print(f"  {t}: div={cfg['div_thr']} h={cfg['hold']} s={cfg['stop_pct']:.0%} "
                  f"→ {res['ret']:+.1f}% DD={res['dd']:.1f}% Calmar={res['calmar']:.1f}x")
        else:
            best = grid_search(all_data[t],
                               put_volume_filter=put_volume_filter,
                               div_strength_sizing=div_strength_sizing,
                               dynamic_hold=dynamic_hold)
            if best.get('calmar', 0) > 0:
                cfg = {'div_thr': best['div_thr'], 'hold': best['hold'], 'stop_pct': best['stop_pct']}
                configs[t] = cfg
                # Re-run with full eq_curve
                res = backtest(all_data[t], cfg['div_thr'], cfg['hold'], cfg['stop_pct'],
                               put_volume_filter=put_volume_filter,
                               div_strength_sizing=div_strength_sizing,
                               dynamic_hold=dynamic_hold)
                per_ticker[t] = res
                print(f"  {t}: div={cfg['div_thr']} h={cfg['hold']} s={cfg['stop_pct']:.0%} "
                      f"→ {best['ret']:+.1f}% DD={best['dd']:.1f}% Calmar={best['calmar']:.1f}x "
                      f"trades={best['trades']} WR={best['wr']:.0f}%")
            else:
                print(f"  {t}: ❌ no profitable config")
    
    if not per_ticker:
        print("No profitable tickers")
        return None, None
    
    # Phase 2: Portfolio
    print(f"\n═══ Phase 2: Portfolio (equal weight) ═══")
    capital_total = 100000.0
    capital_per = capital_total / len(per_ticker)
    
    portfolio_eq = None
    min_len = min(len(v['eq_curve']) for v in per_ticker.values())
    
    for t in TICKERS:
        if t not in per_ticker:
            continue
        eq = np.array(per_ticker[t]['eq_curve'][:min_len])
        eq_scaled = eq / eq[0] * capital_per
        if portfolio_eq is None:
            portfolio_eq = eq_scaled
        else:
            portfolio_eq += eq_scaled
    
    final = portfolio_eq[-1] if portfolio_eq is not None else capital_total
    total_ret = (final / capital_total - 1) * 100
    peak_pf = np.maximum.accumulate(portfolio_eq)
    dd_pf = (peak_pf - portfolio_eq) / peak_pf * 100
    max_dd_pf = float(np.max(dd_pf))
    calmar_pf = total_ret / max(max_dd_pf, 0.01)
    
    print(f"  Capital: {capital_total:,.0f} → {final:,.0f} RUB")
    print(f"  Total Return: {total_ret:+.1f}%")
    print(f"  Max DD: {max_dd_pf:.1f}%")
    print(f"  Calmar: {calmar_pf:.1f}x")
    
    pf_results = {
        'mode': mode_name,
        'final': final,
        'ret': total_ret,
        'dd': max_dd_pf,
        'calmar': calmar_pf,
        'eq_curve': list(portfolio_eq) if portfolio_eq is not None else [],
    }
    
    return pf_results, per_ticker


def main():
    start = '2024-01-01'
    end = '2026-06-18'
    skip_grid = '--skip-grid' in sys.argv
    
    mode = 'all'
    if '--mode' in sys.argv:
        mode = sys.argv[sys.argv.index('--mode') + 1]
    if '--start' in sys.argv:
        start = sys.argv[sys.argv.index('--start') + 1]
    if '--end' in sys.argv:
        end = sys.argv[sys.argv.index('--end') + 1]
    
    modes_to_run = ['base', 'vol-filter', 'div-strength', 'dyn-hold', 'all'] if mode == 'all' else [mode]
    
    print(f"=== Портфель divergence strategy v5 ===")
    print(f"Тикеры: {', '.join(TICKERS)}")
    print(f"Период: {start} → {end}")
    print(f"Факторы: slippage={SLIPPAGE:.2%}, comm={COMMISSION:.2%}, full reinvest, MTM DD")
    print(f"Grid search: {'skip (fallback)' if skip_grid else 'ON'}")
    print()
    
    # Load data once
    all_data = {}
    for t in TICKERS:
        print(f"  {t}...", end=' ', flush=True)
        data = load_ticker(t, start, end)
        if data:
            all_data[t] = data
            print(f"{len(data['opn']):,} bars ✅")
        else:
            print(f"❌")
    
    if not all_data:
        print("No data")
        return
    
    all_results = {}
    all_per_ticker = {}
    
    for m in modes_to_run:
        pf_res, per_t = run_mode(m, all_data, skip_grid)
        if pf_res:
            all_results[m] = pf_res
            all_per_ticker[m] = per_t
    
    # --- Comparison table ---
    print(f"\n{'='*70}")
    print(f"  СРАВНЕНИЕ ПОРТФЕЛЬНЫХ РЕЗУЛЬТАТОВ (equal weight)")
    print(f"{'='*70}")
    print(f"  {'Mode':<20} {'Return':>10} {'Max DD':>10} {'Calmar':>10}")
    print(f"  {'-'*20} {'-'*10} {'-'*10} {'-'*10}")
    
    sorted_modes = sorted(all_results.items(), key=lambda x: x[1]['calmar'], reverse=True)
    label_map = {
        'base': 'BASE', 'vol-filter': 'VOL-FILTER',
        'div-strength': 'DIV-STRENGTH', 'dyn-hold': 'DYN-HOLD', 'all': 'ALL'
    }
    for m, r in sorted_modes:
        print(f"  {label_map[m]:<20} {r['ret']:>+8.1f}% {r['dd']:>8.1f}% {r['calmar']:>8.1f}x")
    
    # --- Per-ticker comparison ---
    print(f"\n── Per-ticker comparison ──")
    for t in TICKERS:
        if t not in all_data:
            continue
        print(f"\n  {t}")
        print(f"  {'Mode':<20} {'Return':>10} {'Max DD':>10} {'Calmar':>10} {'Trades':>8} {'WR':>6}")
        print(f"  {'-'*20} {'-'*10} {'-'*10} {'-'*10} {'-'*8} {'-'*6}")
        for m in modes_to_run:
            if m in all_per_ticker and t in all_per_ticker[m]:
                r = all_per_ticker[m][t]
                cal = r['ret'] / max(r['dd'], 0.01)
                print(f"  {label_map[m]:<20} {r['ret']:>+8.1f}% {r['dd']:>8.1f}% {cal:>8.1f}x {r['trades']:>8} {r['wr']:>5.0f}%")
    
    # --- Winners ---
    print(f"\n═══ Итог ═══")
    best_mode = sorted_modes[0][0]
    print(f"  Лучший режим: {label_map[best_mode]} (Calmar={sorted_modes[0][1]['calmar']:.1f}x)")
    if len(sorted_modes) > 1:
        print(f"  Прирост Calmar от BASE: +{sorted_modes[0][1]['calmar'] - sorted_modes[-1][1]['calmar']:.1f}x (последний = BASE)" if sorted_modes[-1][0] == 'base' else "")
    
    print(f"\n  Done ✅")


if __name__ == '__main__':
    main()
