#!/usr/bin/env python3 -u
"""Скан Stop Hunt на NASD (Nasdaq MOEX) и ES (CME S&P 500).

Формула (из trading-strategy-workflow):
  LONG:  lo[i] < min(lo[i-lb:i]) AND close[i] > lo[i] + rt*(hi[i]-lo[i])
  SHORT: hi[i] > max(hi[i-lb:i]) AND close[i] < hi[i] - rt*(hi[i]-lo[i])

Exit: trailing TP (activation, trail, timeout), SL.
Данные: mt5_continuous M1 → ресемпл M5.
OOS по годам (2024-2026 — доступное покрытие).
"""
import sys, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
import numpy as np, clickhouse_connect as cc

ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')

COMMISSION = 0.0005
SLIPPAGE = 0.0002
LB = 20       # lookback M5
RT = 0.3      # retrace
ACT, TR, TO = 0.005, 0.003, 12   # activation 0.5%, trail 0.3%, timeout 12 баров (60 мин)
SL = 0.007    # страховочный SL

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

def backtest_m5(bars, evening_only=False, start_capital=100000.0):
    ts = np.array([b['ts'].timestamp() for b in bars])
    opn = np.array([b['opn'] for b in bars])
    hi = np.array([b['hi'] for b in bars])
    lo = np.array([b['lo'] for b in bars])
    close = np.array([b['prc'] for b in bars])
    n = len(bars)
    if evening_only:
        hours = np.array([b['ts'].hour for b in bars])
        in_session = (hours >= 19) | (hours < 2)
    else:
        in_session = np.ones(n, dtype=bool)

    cash = float(start_capital)
    pos = 0; entry_price = 0.0; entry_bar = 0; peak_price = 0.0
    trail_active = False; cd_until = 0
    trade_count = 0; win_count = 0
    rets = []; eq_curve = [cash]
    reasons = {"trail": 0, "sl": 0, "timeout": 0}

    for i in range(LB + 2, n):
        if not in_session[i]:
            continue
        if pos != 0:
            bars_held = i - entry_bar
            closed = False; reason = None
            if pos == 1:
                peak_price = max(peak_price, hi[i])
                if not trail_active and peak_price >= entry_price * (1 + ACT):
                    trail_active = True
                if trail_active and close[i] <= peak_price * (1 - TR):
                    exit_price = close[i] * (1 - SLIPPAGE)
                    ret = (exit_price - entry_price) / entry_price - COMMISSION
                    rets.append(ret); trade_count += 1
                    if ret > 0: win_count += 1
                    cash *= (1 + ret); pos = 0; trail_active = False; closed = True; reason = "trail"
                elif lo[i] <= entry_price * (1 - SL):
                    exit_price = entry_price * (1 - SL) * (1 - SLIPPAGE)
                    ret = (exit_price - entry_price) / entry_price - COMMISSION
                    rets.append(ret); trade_count += 1
                    if ret > 0: win_count += 1
                    cash *= (1 + ret); pos = 0; trail_active = False; closed = True; reason = "sl"
                elif bars_held >= TO:
                    exit_price = close[i] * (1 - SLIPPAGE)
                    ret = (exit_price - entry_price) / entry_price - COMMISSION
                    rets.append(ret); trade_count += 1
                    if ret > 0: win_count += 1
                    cash *= (1 + ret); pos = 0; trail_active = False; closed = True; reason = "timeout"
            else:
                peak_price = min(peak_price, lo[i]) if peak_price else lo[i]
                if not trail_active and peak_price <= entry_price * (1 - ACT):
                    trail_active = True
                if trail_active and close[i] >= peak_price * (1 + TR):
                    exit_price = close[i] * (1 + SLIPPAGE)
                    ret = (entry_price - exit_price) / entry_price - COMMISSION
                    rets.append(ret); trade_count += 1
                    if ret > 0: win_count += 1
                    cash *= (1 + ret); pos = 0; trail_active = False; closed = True; reason = "trail"
                elif hi[i] >= entry_price * (1 + SL):
                    exit_price = entry_price * (1 + SL) * (1 + SLIPPAGE)
                    ret = (entry_price - exit_price) / entry_price - COMMISSION
                    rets.append(ret); trade_count += 1
                    if ret > 0: win_count += 1
                    cash *= (1 + ret); pos = 0; trail_active = False; closed = True; reason = "sl"
                elif bars_held >= TO:
                    exit_price = close[i] * (1 + SLIPPAGE)
                    ret = (entry_price - exit_price) / entry_price - COMMISSION
                    rets.append(ret); trade_count += 1
                    if ret > 0: win_count += 1
                    cash *= (1 + ret); pos = 0; trail_active = False; closed = True; reason = "timeout"
            if reason:
                reasons[reason] += 1
        if pos == 0 and i >= cd_until and in_session[i]:
            if close[i] > opn[i - LB] * (1 + 0.0001):
                pass
            lo_lb = lo[i - LB:i].min()
            hi_lb = hi[i - LB:i].max()
            # LONG: ложный пробой минимума
            if lo[i] < lo_lb and close[i] > lo[i] + RT * (hi[i] - lo[i]):
                entry_price = close[i] * (1 + SLIPPAGE)
                pos = 1; entry_bar = i; peak_price = entry_price; trail_active = False
                cd_until = i + 6
            # SHORT: ложный пробой максимума
            elif hi[i] > hi_lb and close[i] < hi[i] - RT * (hi[i] - lo[i]):
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
                final=eq[-1], pf=pf, avg_ret=float(rets.mean()), reasons=reasons,
                n=len(eq_curve))

def run(ticker, evening_only=False):
    print(f"\n===== {ticker} (вечерняя сессия={evening_only}) =====")
    print(f"{'Период':<22}{'n':>6}{'WR%':>7}{'avg%':>8}{'PF':>6}{'CAGR%':>8}{'MDD%':>7}{'сделок':>7}")
    print("-" * 72)
    total_trades = 0
    for label, lo, hi in [("2024", '2024-01-01', '2025-01-01'),
                          ("2025", '2025-01-01', '2026-01-01'),
                          ("2026", '2026-01-01', '2027-01-01')]:
        m1 = load_m1(ticker, lo, hi)
        if not m1:
            continue
        bars = resample_m5(m1)
        r = backtest_m5(bars, evening_only=evening_only)
        if r:
            total_trades += r["trades"]
            print(f"{label:<22}{r['n']:>6}{r['wr']*100:>7.1f}{r['avg_ret']*100:>+8.3f}"
                  f"{r['pf']:>6.2f}{r['cagr']*100:>+8.1f}{r['mdd']*100:>7.1f}{r['trades']:>7}")
        else:
            print(f"{label:<22}{'нет сделок':>30}")
    # весь период
    m1 = load_m1(ticker, '2024-01-01', '2027-01-01')
    bars = resample_m5(m1)
    r = backtest_m5(bars, evening_only=evening_only)
    if r:
        print("-" * 72)
        print(f"{'2024-2026 весь':<22}{r['n']:>6}{r['wr']*100:>7.1f}{r['avg_ret']*100:>+8.3f}"
              f"{r['pf']:>6.2f}{r['cagr']*100:>+8.1f}{r['mdd']*100:>7.1f}{r['trades']:>7}")
        print(f"  выходы: trail={r['reasons']['trail']} sl={r['reasons']['sl']} timeout={r['reasons']['timeout']}")

for tk in ["NASD", "ES"]:
    run(tk, evening_only=False)
    run(tk, evening_only=True)

ch.close()
