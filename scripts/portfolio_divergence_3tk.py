#!/usr/bin/env python3
"""Портфель divergence strategy — 3 best tickers без BELU (т.к. у неё DD 73% и короткие данные).

Скрипт загружает divergence_backtest напрямую.
"""
import subprocess, sys, numpy as np
sys.path.insert(0, '/home/user/projects/TQA-MOEX')
from scripts.divergence_backtest import load_ticker, backtest, ch

CH = "10.0.0.63"
DB = "moex_algopack_v2"
SLIPPAGE = 0.0002
COMMISSION = 0.0005

TICKERS = ['AFKS', 'AFLT', 'CHMF']  # BELU excluded

def run_correct(secid, div_thr, hold, stop_pct, capital=100000.0):
    """Run backtest and return full results with MTM equity curve."""
    data = load_ticker(secid, '2024-01-01', '2026-06-18')
    if data is None or len(data['open']) < 500:
        return None
    
    # Correct for our field names -> dict
    # load_ticker returns a dict with arrays
    ds = {}
    ds['n'] = len(data['open'])
    ds['opn'] = np.array(data['open'])
    ds['close'] = np.array(data['close'])
    ds['high'] = np.array(data['high'])
    ds['low'] = np.array(data['low'])
    ds['o_imb'] = np.array(data['o_imb_sm'])
    ds['t_imb'] = np.array(data['t_imb'])
    
    n = ds['n']
    opn = ds['opn']; close = ds['close']; high = ds['high']; low = ds['low']
    o_imb = ds['o_imb']; t_imb = ds['t_imb']
    
    cash = float(capital)
    pos = 0
    entry_price = 0.0
    entry_bar = 0
    trade_count = 0
    win_count = 0
    eq_curve = [cash]
    
    for i in range(10, n - 2):
        if pos != 0:
            bars_held = i - entry_bar
            if pos == 1 and low[i] <= entry_price * (1 - stop_pct):
                cash *= (1 - stop_pct - SLIPPAGE - COMMISSION)
                trade_count += 1
                pos = 0
            elif pos == -1 and high[i] >= entry_price * (1 + stop_pct):
                cash *= (1 - stop_pct - SLIPPAGE - COMMISSION)
                trade_count += 1
                pos = 0
            elif bars_held >= hold:
                ret = (close[i] / entry_price - 1) * pos
                cash *= (1 + ret - SLIPPAGE - COMMISSION)
                if ret > 0: win_count += 1
                trade_count += 1
                pos = 0
        
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
        
        if pos == 1:
            eq_curve.append(cash * close[i] / entry_price)
        elif pos == -1:
            eq_curve.append(cash * entry_price / close[i])
        else:
            eq_curve.append(cash)
    
    if pos != 0:
        ret = (close[-1] / entry_price - 1) * pos
        cash *= (1 + ret - SLIPPAGE - COMMISSION)
        if ret > 0: win_count += 1
        trade_count += 1
    eq_curve.append(cash)
    
    total_ret = (cash / capital - 1) * 100
    eq_arr = np.array(eq_curve)
    peak = np.maximum.accumulate(eq_arr)
    dd_arr = (peak - eq_arr) / peak * 100
    max_dd = np.max(dd_arr)
    wr = win_count / max(trade_count, 1) * 100
    
    return {
        'ret': total_ret, 'dd': max_dd, 'trades': trade_count, 'wr': wr,
        'final_capital': cash, 'eq_curve': eq_curve
    }


# Best configs per ticker (from grid search above)
CONFIGS = {
    'AFKS': {'div_thr': 10, 'hold': 10, 'stop_pct': 0.01},
    'AFLT': {'div_thr': 10, 'hold': 10, 'stop_pct': 0.01},
    'CHMF': {'div_thr': 10, 'hold': 10, 'stop_pct': 0.01},
}

print("=== Портфель divergence strategy — 3 tickers (без BELU) ===")
print(f"Slippage {SLIPPAGE:.2%}, commission {COMMISSION:.2%}, full reinvest\n")

results = {}
for t in TICKERS:
    cfg = CONFIGS[t]
    res = run_correct(t, cfg['div_thr'], cfg['hold'], cfg['stop_pct'])
    if res:
        results[t] = res
        calmar = res['ret'] / max(res['dd'], 0.01)
        print(f"  {t}: {res['ret']:+.1f}% DD={res['dd']:.1f}% Calmar={calmar:.1f}x trades={res['trades']} WR={res['wr']:.0f}%")

print(f"\n=== Портфель (equal weight, {len(results)} тикера) ===")
capital_total = 100000.0
capital_per = capital_total / len(results)

pf_eq = None
min_len = min(len(r['eq_curve']) for r in results.values())

for t in TICKERS:
    if t not in results:
        continue
    eq = np.array(results[t]['eq_curve'][:min_len])
    eq_scaled = eq / eq[0] * capital_per
    if pf_eq is None:
        pf_eq = eq_scaled
    else:
        pf_eq += eq_scaled

final = pf_eq[-1]
total_ret = (final / capital_total - 1) * 100
peak_pf = np.maximum.accumulate(pf_eq)
dd_pf = (peak_pf - pf_eq) / peak_pf * 100
max_dd_pf = np.max(dd_pf)
calmar_pf = total_ret / max(max_dd_pf, 0.01)

print(f"  Capital: {capital_total:,.0f} → {final:,.0f} RUB")
print(f"  Total Return: {total_ret:+.1f}%")
print(f"  Max DD: {max_dd_pf:.1f}%")
print(f"  Calmar: {calmar_pf:.1f}x")

# DD events log
print(f"\n── Top DD events (> 5%) ──")
in_dd = False
events = []
start_idx = 0
for i, d in enumerate(dd_pf):
    if d > 5 and not in_dd:
        in_dd = True
        start_idx = i
    elif d <= 1 and in_dd:
        in_dd = False
        events.append((start_idx, i, np.max(dd_pf[start_idx:i+1])))
if in_dd:
    events.append((start_idx, len(dd_pf)-1, np.max(dd_pf[start_idx:])))

events.sort(key=lambda x: x[2], reverse=True)
for ev in events[:5]:
    print(f"  DD {ev[2]:.1f}% ({ev[1]-ev[0]} bars)")
