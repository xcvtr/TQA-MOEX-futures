#!/usr/bin/env python3 -u
"""Проверка: стоп-хант на ES/NASD — сигнал или антисигнал?

Если на эффективном рынке пробой = настоящий пробой (продолжение),
то разворот стоп-ханта (вход ПО НАПРАВЛЕНИЮ пробоя) даст плюс.
Тест: нормальный SH vs инвертированный SH.
"""
import sys, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
import numpy as np, clickhouse_connect as cc

ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')

COMMISSION = 0.0005
SLIPPAGE = 0.0002
LB = 20
RT = 0.3
ACT, TR, TO = 0.005, 0.003, 12
SL = 0.007

def load_m1(ticker, since, till):
    rows = ch.query(f"SELECT bt,opn,hi,lo,prc,vol FROM moex.mt5_continuous "
                    f"WHERE ticker='{ticker}' AND bt>='{since}' AND bt<'{till}' ORDER BY bt").result_rows
    return [(r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])) for r in rows]

def resample_m5(m1):
    g = {}
    for ts, opn, hi, lo, prc, vol in m1:
        tm = ts.hour * 60 + ts.minute; km = (tm // 5) * 5
        k = ts.replace(minute=km % 60, hour=km // 60, second=0)
        if k not in g:
            g[k] = {'ts': k, 'opn': opn, 'hi': hi, 'lo': lo, 'prc': prc, 'vol': vol}
        else:
            gg = g[k]; gg['hi'] = max(gg['hi'], hi); gg['lo'] = min(gg['lo'], lo)
            gg['prc'] = prc; gg['vol'] += vol
    return sorted(g.values(), key=lambda x: x['ts'])

def backtest_m5(bars, invert=False, start_capital=100000.0):
    ts = np.array([b['ts'].timestamp() for b in bars])
    opn = np.array([b['opn'] for b in bars])
    hi = np.array([b['hi'] for b in bars])
    lo = np.array([b['lo'] for b in bars])
    close = np.array([b['prc'] for b in bars])
    n = len(bars)
    cash = float(start_capital)
    pos = 0; entry_price = 0.0; entry_bar = 0; peak_price = 0.0
    trail_active = False; cd_until = 0
    trade_count = 0; win_count = 0
    rets = []; eq_curve = [cash]

    for i in range(LB + 2, n):
        if pos != 0:
            bars_held = i - entry_bar
            closed = False
            if pos == 1:
                peak_price = max(peak_price, hi[i])
                if not trail_active and peak_price >= entry_price * (1 + ACT):
                    trail_active = True
                if trail_active and close[i] <= peak_price * (1 - TR):
                    exit_price = close[i] * (1 - SLIPPAGE)
                    ret = (exit_price - entry_price) / entry_price - COMMISSION
                    rets.append(ret); trade_count += 1
                    if ret > 0: win_count += 1
                    cash *= (1 + ret); pos = 0; trail_active = False; closed = True
                elif lo[i] <= entry_price * (1 - SL):
                    exit_price = entry_price * (1 - SL) * (1 - SLIPPAGE)
                    ret = (exit_price - entry_price) / entry_price - COMMISSION
                    rets.append(ret); trade_count += 1
                    if ret > 0: win_count += 1
                    cash *= (1 + ret); pos = 0; trail_active = False; closed = True
                elif bars_held >= TO:
                    exit_price = close[i] * (1 - SLIPPAGE)
                    ret = (exit_price - entry_price) / entry_price - COMMISSION
                    rets.append(ret); trade_count += 1
                    if ret > 0: win_count += 1
                    cash *= (1 + ret); pos = 0; trail_active = False; closed = True
            else:
                peak_price = min(peak_price, lo[i]) if peak_price else lo[i]
                if not trail_active and peak_price <= entry_price * (1 - ACT):
                    trail_active = True
                if trail_active and close[i] >= peak_price * (1 + TR):
                    exit_price = close[i] * (1 + SLIPPAGE)
                    ret = (entry_price - exit_price) / entry_price - COMMISSION
                    rets.append(ret); trade_count += 1
                    if ret > 0: win_count += 1
                    cash *= (1 + ret); pos = 0; trail_active = False; closed = True
                elif hi[i] >= entry_price * (1 + SL):
                    exit_price = entry_price * (1 + SL) * (1 + SLIPPAGE)
                    ret = (entry_price - exit_price) / entry_price - COMMISSION
                    rets.append(ret); trade_count += 1
                    if ret > 0: win_count += 1
                    cash *= (1 + ret); pos = 0; trail_active = False; closed = True
                elif bars_held >= TO:
                    exit_price = close[i] * (1 + SLIPPAGE)
                    ret = (entry_price - exit_price) / entry_price - COMMISSION
                    rets.append(ret); trade_count += 1
                    if ret > 0: win_count += 1
                    cash *= (1 + ret); pos = 0; trail_active = False; closed = True
        if pos == 0 and i >= cd_until:
            lo_lb = lo[i - LB:i].min()
            hi_lb = hi[i - LB:i].max()
            sig_long = lo[i] < lo_lb and close[i] > lo[i] + RT * (hi[i] - lo[i])
            sig_short = hi[i] > hi_lb and close[i] < hi[i] - RT * (hi[i] - lo[i])
            if sig_long or sig_short:
                if not invert:
                    direction = 1 if sig_long else -1
                else:
                    # инверсия: вход по направлению пробоя (long при sig_short и наоборот)
                    direction = -1 if sig_long else 1
                if direction == 1:
                    entry_price = close[i] * (1 + SLIPPAGE)
                    pos = 1; entry_bar = i; peak_price = entry_price; trail_active = False
                else:
                    entry_price = close[i] * (1 - SLIPPAGE)
                    pos = -1; entry_bar = i; peak_price = entry_price; trail_active = False
                cd_until = i + 6
        eq_curve.append(cash)

    if trade_count == 0:
        return None
    rets = np.array(rets)
    eq = np.array(eq_curve)
    peak_eq = np.maximum.accumulate(eq)
    mdd = float(((eq - peak_eq) / peak_eq).min())
    years = (ts[-1] - ts[0]) / (365 * 24 * 3600)
    cagr = (eq[-1] / eq[0]) ** (1 / years) - 1 if eq[0] > 0 else 0
    wins = rets[rets > 0]; losses = rets[rets <= 0]
    pf = wins.sum() / abs(losses.sum()) if len(losses) else 0
    return dict(trades=trade_count, wr=win_count / trade_count, cagr=cagr, mdd=mdd,
                final=eq[-1], pf=pf, avg_ret=float(rets.mean()), n=len(eq_curve))

for tk in ["NASD", "ES"]:
    print(f"\n===== {tk} 2024-2026 =====")
    m1 = load_m1(tk, '2024-01-01', '2027-01-01')
    bars = resample_m5(m1)
    for label, inv in [("нормальный SH", False), ("ИНВЕРТИРОВАННЫЙ", True)]:
        r = backtest_m5(bars, invert=inv)
        if r:
            print(f"  {label:22s}: n={r['trades']:5d} WR={r['wr']*100:5.1f}% avg={r['avg_ret']*100:+.3f}% "
                  f"PF={r['pf']:.2f} CAGR={r['cagr']*100:+.1f}% MDD={r['mdd']*100:.1f}%")
ch.close()
