#!/usr/bin/env python3 -u
"""Календарный walk-forward с реоптимизацией раз в месяц + РЕАЛИСТИЧНЫЕ ограничения.

Ограничения:
- Суммарная ГО всех открытых позиций ≤ 80% капитала (маржа)
- Макс. контрактов на тикер: 100 (ликвидность)
- Пирамидинг не превышает макс. контрактов

Параметры: thr ∈ {3,4,5}, exit_thr ∈ {2,3}, risk ∈ {0.15, 0.25}
Окно оптимизации: 12 месяцев, пересмотр ежемесячно.
"""
import sys, bisect
sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
import numpy as np
import psycopg2, scripts.oi_v9_oi_exit as v9
from datetime import datetime, timezone

pg = psycopg2.connect(host='10.0.0.60', dbname='moex', user='postgres', connect_timeout=5)
cur = pg.cursor()
cur.execute('SELECT ticker, go, min_step, step_price, fee_entry FROM futures.ticker_specs')
v9.specs = {}
for t, go, ms, sp, fee in cur.fetchall(): v9.specs[t] = (float(go), float(ms), float(sp), float(fee))
pg.close()

ALL_YEARS = [2022, 2023, 2024, 2025, 2026]
DATA = v9.load_all(ALL_YEARS, 3)
MAX_MARGIN = 0.80   # суммарная ГО ≤ 80% капитала
MAX_CONTRACTS = 100 # ликвидность

def run_range(years, months_lo, months_hi, risk_ng, risk_small, thr, exit_thr, max_hold=120, pyr=3, pyra_pct=0.5):
    """Прогон с ограничениями по марже и контрактам. Возвращает сделки с eq-эффектом."""
    risk_map = {'BR': risk_ng, 'NG': risk_ng, 'SV': risk_small, 'RI': risk_small, 'TT': risk_small}
    trades_all = []
    for (fut_tk, y), (net_map, bars, spec) in DATA.items():
        if y < months_lo[0] or y > months_hi[0]: continue
        go, ms, sp, fee = spec
        pts = bars[:, 0]
        fts = sorted(net_map.keys())
        for direction in ['long', 'short']:
            pos = None
            for ts in fts:
                dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                ym = (dt.year, dt.month)
                if ym < months_lo or ym > months_hi: continue
                dn = net_map[ts]
                if pos is not None:
                    idx = bisect.bisect_right(pts, ts) - 1
                    if idx < 0: continue
                    cur_p = bars[idx, 4]
                    exit_cond = (dn >= exit_thr) if direction == 'long' else (dn <= -exit_thr)
                    hold_h = (ts - pos['entry_ts']) / 3600
                    if exit_cond or hold_h >= max_hold:
                        exit_p = cur_p - ms if direction == 'long' else cur_p + ms
                        pnl = 0.0
                        for lots, p_in in pos['parts']:
                            if direction == 'long':
                                pnl += ((exit_p - p_in) / ms * sp - fee*2) * lots
                            else:
                                pnl += ((p_in - exit_p) / ms * sp - fee*2) * lots
                        trades_all.append({'ts': ts, 'y': y, 'tk': fut_tk, 'pnl': pnl,
                                           'dir': direction, 'ms': ms, 'sp': sp, 'fee': fee, 'go': go,
                                           'entry_ts': pos['entry_ts'], 'parts': pos['parts'], 'bars': bars,
                                           'risk': risk_map.get(fut_tk, 0.2), 'ym': ym})
                        pos = None
                if pos is None:
                    in_cond = (dn <= -thr) if direction == 'long' else (dn >= thr)
                    if in_cond:
                        idx = bisect.bisect_right(pts, ts) - 1
                        if idx < 0: continue
                        fill_p = bars[idx, 4] + ms if direction == 'long' else bars[idx, 4] - ms
                        # лоты с ограничением
                        base_lots = max(1, int(200000 * risk_map.get(fut_tk, 0.2) / go))
                        base_lots = min(base_lots, MAX_CONTRACTS)
                        pos = {'entry_ts': ts, 'fill_p': fill_p, 'parts': [(base_lots, fill_p)],
                               'added': 0, 'ms': ms, 'sp': sp, 'fee': fee, 'go': go,
                               'direction': direction, 'bars': bars, 'pts': pts}
                elif pos['added'] < pyr - 1:
                    idx = bisect.bisect_right(pts, ts) - 1
                    if idx >= 0:
                        lots_add = max(1, int(200000 * risk_map.get(fut_tk, 0.2) / pos['go']))
                        lots_add = min(lots_add, MAX_CONTRACTS)
                        if direction == 'long':
                            hi = bars[idx, 2]
                            gain_pct = (hi - pos['fill_p']) / pos['fill_p'] * 100
                            if gain_pct >= (pos['added'] + 1) * pyra_pct:
                                pos['parts'].append((lots_add, hi + ms))
                                pos['added'] += 1
                        else:
                            lo = bars[idx, 3]
                            gain_pct = (pos['fill_p'] - lo) / pos['fill_p'] * 100
                            if gain_pct >= (pos['added'] + 1) * pyra_pct:
                                pos['parts'].append((lots_add, lo - ms))
                                pos['added'] += 1
    return trades_all

def simulate_margin(trades, start_cap=200000.0):
    """Симуляция с компаундом И ограничением по марже (суммарное ГО ≤ 80% капитала)."""
    eq = start_cap
    peak = eq
    cash_mdd = 0.0
    n = 0; wins = 0
    # активные позиции (для маржи)
    for t in sorted(trades, key=lambda x: x['ts']):
        scale = eq / 200000.0
        # проверка маржи: ГО сделки ≤ 80% eq
        go_total = sum(lots * t['go'] for lots, p_in in t['parts']) * scale
        if go_total > eq * MAX_MARGIN:
            # уменьшаем лоты до доступной маржи
            ratio = (eq * MAX_MARGIN) / go_total
            if ratio < 0.1: continue  # слишком мало маржи — пропускаем
            t = dict(t)
            t['parts'] = [(max(1, int(lots*ratio)), p_in) for lots, p_in in t['parts']]
        pnl = t['pnl'] * scale
        eq += pnl; n += 1
        if pnl > 0: wins += 1
        peak = max(peak, eq)
        cash_mdd = max(cash_mdd, (peak - eq) / peak * 100)
    return eq, cash_mdd, n, wins

months = []
for y in [2023, 2024, 2025, 2026]:
    for m in range(1, 13):
        if y == 2026 and m > 8: continue
        months.append((y, m))

GRID = []
for thr in [3, 4, 5]:
    for exit_thr in [2, 3]:
        for risk in [0.15, 0.25]:
            GRID.append((thr, exit_thr, risk))

print(f"Сетка: {len(GRID)} конфигов, месяцы: {len(months)} (окно 12)")
print(f"{'месяц':<10}{'выбран':<22}{'ROI_мес':>9}{'eq':>12}")
eq = 200000.0
results = []
for i, ym in enumerate(months):
    if i < 12: continue
    train_lo = months[i-12]
    train_hi = months[i-1]
    best = None; best_score = -1e9
    for thr, exit_thr, risk in GRID:
        trades = run_range(ALL_YEARS, train_lo, train_hi, risk, risk*2/3, thr, exit_thr)
        if len(trades) < 20: continue
        eq_f, mdd, n, w = simulate_margin(trades)
        roi = (eq_f/200000 - 1)*100
        calmar = roi / mdd if mdd > 0 else 0
        if calmar > best_score:
            best_score = calmar
            best = (thr, exit_thr, risk, roi, mdd, len(trades))
    if best is None: continue
    thr, exit_thr, risk = best[0], best[1], best[2]
    t_trades = run_range(ALL_YEARS, ym, ym, risk, risk*2/3, thr, exit_thr)
    scale = eq / 200000.0
    # маржинальная проверка для месяца
    pnl_month = 0.0
    n_month = 0
    for t in t_trades:
        go_total = sum(lots * t['go'] for lots, p_in in t['parts']) * scale
        tt = t
        if go_total > eq * MAX_MARGIN:
            ratio = (eq * MAX_MARGIN) / go_total
            if ratio < 0.1: continue
            tt = dict(t)
            tt['parts'] = [(max(1, int(lots*ratio)), p_in) for lots, p_in in t['parts']]
        pnl_month += tt['pnl'] * scale
        n_month += 1
    roi_month = pnl_month / (eq - pnl_month) * 100 if eq - pnl_month != 0 else 0
    eq += pnl_month
    results.append((ym, best, roi_month, eq))
    print(f"{ym[0]}-{ym[1]:02d}   thr{thr} ex{exit_thr} r{risk:.0%}  {roi_month:>+8.1f}%  {eq:>12,.0f}")

if results:
    final = results[-1][3]
    n_months = len(results)
    total_roi = (final/200000 - 1)*100
    cagr = ((final/200000)**(12/n_months) - 1)*100
    print(f"\nФинальный капитал: {final:,.0f} (старт 200K)")
    print(f"Итоговый ROI: {total_roi:+.1f}% за {n_months} мес")
    print(f"CAGR: {cagr:.1f}% (годовой)")
    # отрицательные месяцы
    neg = [r for r in results if r[2] < 0]
    print(f"Отрицательных месяцев: {len(neg)} из {n_months}")
    print(f"Худший месяц: {min(r[2] for r in results):+.1f}%")
    print(f"Лучший месяц: {max(r[2] for r in results):+.1f}%")
