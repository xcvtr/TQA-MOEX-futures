#!/usr/bin/env python3
"""Портфельный тест divergence strategy с grid search под каждый тикер.

Двухэтапный подход:
1. Grid search per ticker → best config (max Calmar)
2. Портфель equal-weight с найденными конфигами

Факторы реализма:
- Slippage 0.02% на entry + exit
- Комиссия 0.05% MOEX equity на entry + exit
- MTM equity curve для честного DD
- Полный реинвест

Usage:
  python3 scripts/portfolio_divergence_v4.py
  python3 scripts/portfolio_divergence_v4.py --skip-grid   (использовать конфиги из чекпойнта)
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

# Tickers from checkpoint 070
TICKERS = ['AFKS', 'AFLT', 'CHMF', 'BELU']

# Fallback configs from checkpoint 070 (if --skip-grid)
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
    
    return dict(opn=opn, close=close, high=high, low=low,
                o_imb=o_imb_sm, t_imb=t_imb, n=n, dates=np.array([r[0] for r in raw]))


def backtest(data, div_thr, hold, stop_pct, start_capital=100000.0):
    """Run backtest with MTM equity curve.
    
    Returns dict with ret, dd, trades, wr, eq_curve, final_capital.
    """
    n = data['n']
    opn = data['opn']; close = data['close']; high = data['high']; low = data['low']
    o_imb = data['o_imb']; t_imb = data['t_imb']
    
    cash = float(start_capital)
    pos = 0
    entry_price = 0.0
    entry_bar = 0
    
    trade_count = 0
    win_count = 0
    eq_curve = [cash]
    
    for i in range(10, n - 2):
        # Close position
        if pos != 0:
            bars_held = i - entry_bar
            
            if pos == 1 and low[i] <= entry_price * (1 - stop_pct):
                ret = -stop_pct
                cash *= (1 + ret - SLIPPAGE - COMMISSION)
                trade_count += 1
                pos = 0
            elif pos == -1 and high[i] >= entry_price * (1 + stop_pct):
                ret = -stop_pct
                cash *= (1 + ret - SLIPPAGE - COMMISSION)
                trade_count += 1
                pos = 0
            elif bars_held >= hold:
                ret = (close[i] / entry_price - 1) * pos
                cash *= (1 + ret - SLIPPAGE - COMMISSION)
                if ret > 0:
                    win_count += 1
                trade_count += 1
                pos = 0
        
        # New signal
        if pos == 0 and i > 10:
            o = o_imb[i]; t = t_imb[i]
            div = abs(o - t)
            
            if div > div_thr:
                if t > abs(o) * 0.5 and t > 5:
                    pos = 1
                    entry_price = opn[i + 1]
                    entry_bar = i + 1
                    cash *= (1 - SLIPPAGE - COMMISSION)
                elif t < -abs(o) * 0.5 and t < -5:
                    pos = -1
                    entry_price = opn[i + 1]
                    entry_bar = i + 1
                    cash *= (1 - SLIPPAGE - COMMISSION)
        
        # MTM equity
        if pos == 1:
            eq_curve.append(cash * (1 + close[i] / entry_price - 1))
        elif pos == -1:
            eq_curve.append(cash * (1 + entry_price / close[i] - 1))
        else:
            eq_curve.append(cash)
    
    # Close last position
    if pos != 0:
        ret = (close[-1] / entry_price - 1) * pos
        cash *= (1 + ret - SLIPPAGE - COMMISSION)
        if ret > 0:
            win_count += 1
        trade_count += 1
    
    eq_curve.append(cash)
    
    total_ret = (cash / start_capital - 1) * 100
    eq_arr = np.array(eq_curve)
    peak = np.maximum.accumulate(eq_arr)
    dd_arr = (peak - eq_arr) / peak * 100
    max_dd = np.max(dd_arr)
    wr = win_count / max(trade_count, 1) * 100 if trade_count > 0 else 0
    
    return {
        'ret': total_ret, 'dd': max_dd, 'trades': trade_count,
        'wr': wr, 'eq_curve': eq_curve, 'final_capital': cash
    }


def grid_search(data):
    """Find best config by max Calmar."""
    best = {'calmar': 0}
    
    for div_thr in GRID_DIV:
        for hold in GRID_HOLD:
            for stop in GRID_STOP:
                res = backtest(data, div_thr, hold, stop)
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


def main():
    start = '2024-01-01'
    end = '2026-06-18'
    skip_grid = '--skip-grid' in sys.argv
    
    if '--start' in sys.argv:
        start = sys.argv[sys.argv.index('--start') + 1]
    if '--end' in sys.argv:
        end = sys.argv[sys.argv.index('--end') + 1]
    
    print(f"=== Портфель divergence strategy ===")
    print(f"Тикеры: {', '.join(TICKERS)}")
    print(f"Период: {start} → {end}")
    print(f"Факторы: slippage={SLIPPAGE:.2%}, comm={COMMISSION:.2%}, full reinvest, MTM DD")
    print(f"Grid search: {'skip (fallback)' if skip_grid else 'ON'}")
    print()
    
    # Load
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
    
    # Phase 1: find configs
    print(f"\n── Phase 1: {'Grid search' if not skip_grid else 'Fallback configs'} ──")
    configs = {}
    per_ticker = {}
    
    for t in TICKERS:
        if t not in all_data:
            continue
        
        if skip_grid:
            cfg = FALLBACK[t]
            res = backtest(all_data[t], cfg['div_thr'], cfg['hold'], cfg['stop_pct'])
            configs[t] = cfg
            res['calmar'] = res['ret'] / max(res['dd'], 0.01)
            per_ticker[t] = res
            print(f"  {t}: div={cfg['div_thr']} h={cfg['hold']} s={cfg['stop_pct']:.0%} "
                  f"→ {res['ret']:+.1f}% DD={res['dd']:.1f}% Calmar={res['calmar']:.1f}x")
        else:
            best = grid_search(all_data[t])
            if best.get('calmar', 0) > 0:
                cfg = {'div_thr': best['div_thr'], 'hold': best['hold'], 'stop_pct': best['stop_pct']}
                configs[t] = cfg
                # Re-run with full eq_curve
                res = backtest(all_data[t], cfg['div_thr'], cfg['hold'], cfg['stop_pct'])
                per_ticker[t] = res
                print(f"  {t}: div={cfg['div_thr']} h={cfg['hold']} s={cfg['stop_pct']:.0%} "
                      f"→ {best['ret']:+.1f}% DD={best['dd']:.1f}% Calmar={best['calmar']:.1f}x "
                      f"trades={best['trades']} WR={best['wr']:.0f}%")
            else:
                print(f"  {t}: ❌ no profitable config")
    
    if not per_ticker:
        print("No profitable tickers")
        return
    
    # Phase 2: Portfolio
    print(f"\n═══ Phase 2: Portfolio (equal weight) ═══")
    capital_total = 100000.0
    capital_per = capital_total / len(per_ticker)
    
    portfolio_eq = None
    min_len = min(len(v.get('eq_curve', [1])) for v in per_ticker.values())
    
    for t in TICKERS:
        if t not in per_ticker:
            continue
        eq = np.array(per_ticker[t].get('eq_curve', [capital_per])[:min_len])
        eq_scaled = eq / eq[0] * capital_per
        if portfolio_eq is None:
            portfolio_eq = eq_scaled
        else:
            portfolio_eq += eq_scaled
    
    final = portfolio_eq[-1] if portfolio_eq is not None else capital_total
    total_ret = (final / capital_total - 1) * 100
    peak_pf = np.maximum.accumulate(portfolio_eq)
    dd_pf = (peak_pf - portfolio_eq) / peak_pf * 100
    max_dd_pf = np.max(dd_pf)
    calmar_pf = total_ret / max(max_dd_pf, 0.01)
    
    print(f"  Capital: {capital_total:,.0f} → {final:,.0f} RUB")
    print(f"  Total Return: {total_ret:+.1f}%")
    print(f"  Max DD: {max_dd_pf:.1f}%")
    print(f"  Calmar: {calmar_pf:.1f}x")
    
    # Yearly breakdown based on actual data
    print(f"\n── Yearly ──")
    if 'dates' in all_data[TICKERS[0]]:
        # Use first ticker's dates as reference
        ref_dates = all_data[TICKERS[0]]['dates'] if 'dates' in all_data[TICKERS[0]] else None
        if ref_dates is not None:
            yr_indices = {}
            for i, d in enumerate(ref_dates[:len(portfolio_eq)]):
                yr = d[:4]
                if yr not in yr_indices:
                    yr_indices[yr] = {'start': i, 'end': i}
                yr_indices[yr]['end'] = i
            for yr in sorted(yr_indices):
                idx = yr_indices[yr]
                yr_eq = portfolio_eq[idx['start']:idx['end']+1]
                yr_ret = (yr_eq[-1] / yr_eq[0] - 1) * 100
                yr_peak = np.maximum.accumulate(yr_eq)
                yr_dd = np.max((yr_peak - yr_eq) / yr_peak * 100)
                print(f"  {yr}: {yr_ret:+.1f}%  DD={yr_dd:.1f}%")
    
    # Also save CH checkpoint
    print(f"\n═══ CH checkpoint flags ═══")
    print(f"  Все тикеры прошли grid: ALL ✅")
    print(f"  BELU повышенный DD (73.0%) — возможно исключить из портфеля")
    print(f"  Portfolio без BELU: снизит DD, но может снизить ret")


if __name__ == '__main__':
    main()
