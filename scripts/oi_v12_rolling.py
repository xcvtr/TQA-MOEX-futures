#!/usr/bin/env python3 -u
"""Календарный walk-forward: реоптимизация параметров раз в месяц.

Логика (как в live):
- Окно: 12 месяцев истории
- Каждый месяц: прогоняем сетку параметров на окне, выбираем лучший по Calmar
- Применяем лучший к следующему месяцу (OOS)
- Сдвигаем окно

Параметры оптимизации: thr ∈ {3,4,5}, exit_thr ∈ {2,3}, risk ∈ {0.20, 0.30}
Фикс: pyr=3, pyra=0.5%, hold=120ч, LONG+SHORT.
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
DATA = v9.load_all(ALL_YEARS, 3)  # thr=3 минимальный, чтобы покрыть все сигналы

def run_range(years, months_lo, months_hi, risk_ng, risk_small, thr, exit_thr, max_hold=120, pyr=3, pyra_pct=0.5):
    """Прогон по диапазону месяцев (включительно). Возвращает сделки + equity."""
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
                # фильтр по месяцу
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
                        base_lots = max(1, int(200000 * risk_map.get(fut_tk, 0.2) / go))  # стартовый капитал
                        pos = {'entry_ts': ts, 'fill_p': fill_p, 'parts': [(base_lots, fill_p)],
                               'added': 0, 'ms': ms, 'sp': sp, 'fee': fee, 'go': go,
                               'direction': direction, 'bars': bars, 'pts': pts}
                elif pos['added'] < pyr - 1:
                    idx = bisect.bisect_right(pts, ts) - 1
                    if idx >= 0:
                        lots_add = max(1, int(200000 * risk_map.get(fut_tk, 0.2) / pos['go']))
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

def simulate(trades, start_cap=200000.0):
    """Симуляция с компаундом (риск % от текущего eq)."""
    eq = start_cap
    peak = eq
    cash_mdd = 0.0
    n = 0; wins = 0
    for t in sorted(trades, key=lambda x: x['ts']):
        # масштабируем лоты под текущий eq (компаунд)
        scale = eq / 200000.0
        pnl = t['pnl'] * scale  # pnl был посчитан от 200K, масштабируем
        eq += pnl; n += 1
        if pnl > 0: wins += 1
        peak = max(peak, eq)
        cash_mdd = max(cash_mdd, (peak - eq) / peak * 100)
    return eq, cash_mdd, n, wins

# Месяцы: 2023-01 .. 2026-08 (после 12-мес окна)
months = []
for y in [2023, 2024, 2025, 2026]:
    for m in range(1, 13):
        if y == 2023 and m < 1: continue
        if y == 2026 and m > 8: continue
        months.append((y, m))

GRID = []
for thr in [3, 4, 5]:
    for exit_thr in [2, 3]:
        for risk in [0.20, 0.30]:
            GRID.append((thr, exit_thr, risk))

print(f"Сетка: {len(GRID)} конфигов, месяцы: {len(months)}")
print(f"{'месяц':<10}{'выбран':<22}{'ROI_мес':>9}{'WR%':>7}")
eq = 200000.0
results = []
first_train_done = False
for i, ym in enumerate(months):
    if i < 12: continue  # нужно 12 мес истории
    train_lo = months[i-12]
    train_hi = months[i-1]
    # оптимизация на окне (без компаунда — Calmar по фикс 200K)
    best = None; best_score = -1e9
    for thr, exit_thr, risk in GRID:
        trades = run_range(ALL_YEARS, train_lo, train_hi, risk, risk*2/3, thr, exit_thr)
        if len(trades) < 20: continue
        # ROI без компаунда
        eq_f, mdd, n, w = simulate(trades)
        roi = (eq_f/200000 - 1)*100
        calmar = roi / mdd if mdd > 0 else 0
        if calmar > best_score:
            best_score = calmar
            best = (thr, exit_thr, risk, roi, mdd, len(trades))
    # применяем к текущему месяцу (OOS)
    thr, exit_thr, risk = best[0], best[1], best[2]
    t_trades = run_range(ALL_YEARS, ym, ym, risk, risk*2/3, thr, exit_thr)
    # компаунд
    scale = eq / 200000.0
    pnl_month = sum(t['pnl'] for t in t_trades) * scale
    eq += pnl_month
    results.append((ym, best, pnl_month, len(t_trades), eq))
    print(f"{ym[0]}-{ym[1]:02d}   thr{thr} ex{exit_thr} r{risk:.0%}  {pnl_month/ (eq-pnl_month)*100:>+8.1f}%  WR n/a")

print(f"\nФинальный капитал: {eq:,.0f} (старт 200K)")
print(f"Итоговый ROI: {(eq/200000-1)*100:+.1f}%")
n_months = len(results)
print(f"CAGR: {((eq/200000)**(12/n_months)-1)*100:.1f}% (годовой)")
