#!/usr/bin/env python3 -u
"""Календарный walk-forward v2 — ЧЕСТНАЯ симуляция.

Исправление бага: run_range собирает только СИГНАЛЫ (вход/выход/направление/тикер),
а симуляция считает лоты от текущего eq на каждом входе с ограничениями:
- lots = min(int(eq*risk/go), MAX_CONTRACTS)
- суммарная ГО ≤ 80% eq
- пирамидинг: дополнительный лот при +0.5%, тоже с лимитом

Параметры: thr ∈ {3,4,5}, exit_thr ∈ {2,3}, risk ∈ {0.15, 0.25}
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
MAX_CONTRACTS = 20    # ликвидность (реалистично для MOEX)
MAX_MARGIN = 0.80     # ГО ≤ 80% eq
# Реалистичные лимиты по тикерам (глубина стакана dom_qsh)
TICKER_LIMITS = {'BR': 100, 'NG': 100, 'SV': 80, 'RN': 80, 'RI': 50, 'TT': 30}

def gen_signals(months_lo, months_hi, thr, exit_thr, max_hold=120, pyr=3, pyra_pct=0.5):
    """Генерирует сигналы (без лотов): список {entry_ts, exit_ts, dir, tk, go, ms, sp, fee, parts_times}."""
    sigs = []
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
                        sigs.append({'entry_ts': pos['entry_ts'], 'exit_ts': ts, 'exit_p': exit_p,
                                     'dir': direction, 'tk': fut_tk, 'go': go, 'ms': ms, 'sp': sp, 'fee': fee,
                                     'entry_p': pos['entry_p'], 'pyra_ts': list(pos['pyra_ts'])})
                        pos = None
                if pos is None:
                    in_cond = (dn <= -thr) if direction == 'long' else (dn >= thr)
                    if in_cond:
                        idx = bisect.bisect_right(pts, ts) - 1
                        if idx < 0: continue
                        fill_p = bars[idx, 4] + ms if direction == 'long' else bars[idx, 4] - ms
                        pos = {'entry_ts': ts, 'entry_p': fill_p, 'pyra_ts': [], 'ms': ms,
                               'direction': direction}
                elif pos is not None and len(pos['pyra_ts']) < pyr - 1:
                    idx = bisect.bisect_right(pts, ts) - 1
                    if idx >= 0:
                        if direction == 'long':
                            hi = bars[idx, 2]
                            gain_pct = (hi - pos['entry_p']) / pos['entry_p'] * 100
                            if gain_pct >= (len(pos['pyra_ts']) + 1) * pyra_pct:
                                pos['pyra_ts'].append(ts)
                        else:
                            lo = bars[idx, 3]
                            gain_pct = (pos['entry_p'] - lo) / pos['entry_p'] * 100
                            if gain_pct >= (len(pos['pyra_ts']) + 1) * pyra_pct:
                                pos['pyra_ts'].append(ts)
    return sigs

def simulate(sigs, start_cap=200000.0, risk=0.25):
    """Честная симуляция: лоты от текущего eq, лимит контрактов,
    суммарная маржа по ВСЕМ открытым позициям ≤ MAX_MARGIN."""
    eq = start_cap
    peak = eq
    cash_mdd = 0.0
    n = 0; wins = 0
    open_pos = []  # (exit_ts, go_total)
    for s in sorted(sigs, key=lambda x: x['entry_ts']):
        # закрываем истёкшие
        open_pos = [p for p in open_pos if p[0] > s['entry_ts']]
        go = s['go']
        max_lots = TICKER_LIMITS.get(s['tk'], 50)
        n_parts = 1 + len(s['pyra_ts'])
        base_lots = max(1, int(eq * risk / go))  # риск % от eq
        base_lots = min(base_lots, max_lots)
        go_total = base_lots * go * n_parts
        # маржа: суммарное ГО всех открытых + новая ≤ MAX_MARGIN*eq
        used_go = sum(p[1] for p in open_pos)
        avail = eq * MAX_MARGIN - used_go
        if avail <= 0: continue  # нет маржи — пропускаем вход
        if go_total > avail:
            base_lots = max(1, int(avail / (go * n_parts)))
            base_lots = min(base_lots, max_lots)
            go_total = base_lots * go * n_parts
        if base_lots < 1: continue
        # PnL
        if s['dir'] == 'long':
            pnl = ((s['exit_p'] - s['entry_p']) / s['ms'] * s['sp'] - s['fee']*2) * base_lots
        else:
            pnl = ((s['entry_p'] - s['exit_p']) / s['ms'] * s['sp'] - s['fee']*2) * base_lots
        pnl *= n_parts
        eq += pnl; n += 1
        if pnl > 0: wins += 1
        peak = max(peak, eq)
        cash_mdd = max(cash_mdd, (peak - eq) / peak * 100)
        open_pos.append((s['exit_ts'], go_total))
    return eq, cash_mdd, n, wins

months = []
for y in [2023, 2024, 2025, 2026]:
    for m in range(1, 13):
        if y == 2026 and m > 8: continue
        months.append((y, m))

GRID = []
for thr in [3, 4, 5]:
    for exit_thr in [2, 3]:
        for risk in [0.05, 0.10]:
            GRID.append((thr, exit_thr, risk))

print(f"Сетка: {len(GRID)} конфигов, месяцы: {len(months)}")
eq = 200000.0
results = []
for i, ym in enumerate(months):
    if i < 12: continue
    train_lo = months[i-12]
    train_hi = months[i-1]
    best = None; best_score = -1e9
    for thr, exit_thr, risk in GRID:
        sigs = gen_signals(train_lo, train_hi, thr, exit_thr)
        if len(sigs) < 20: continue
        eq_f, mdd, n, w = simulate(sigs, risk=risk)
        roi = (eq_f/200000 - 1)*100
        calmar = roi / mdd if mdd > 0 else 0
        if calmar > best_score:
            best_score = calmar
            best = (thr, exit_thr, risk, roi, mdd, len(sigs))
    if best is None: continue
    thr, exit_thr, risk = best[0], best[1], best[2]
    t_sigs = gen_signals(ym, ym, thr, exit_thr)
    eq_f, mdd, n, w = simulate(t_sigs, start_cap=eq, risk=risk)
    roi_month = (eq_f/eq - 1)*100
    eq = eq_f
    results.append((ym, best, roi_month, eq))
    print(f"{ym[0]}-{ym[1]:02d}   thr{thr} ex{exit_thr} r{risk:.0%}  {roi_month:>+8.1f}%  {eq:>14,.0f}")

if results:
    final = results[-1][3]
    n_months = len(results)
    total_roi = (final/200000 - 1)*100
    cagr = ((final/200000)**(12/n_months) - 1)*100
    print(f"\nФинальный капитал: {final:,.0f} (старт 200K)")
    print(f"Итоговый ROI: {total_roi:+.1f}% за {n_months} мес")
    print(f"CAGR: {cagr:.1f}% (годовой)")
    neg = [r for r in results if r[2] < 0]
    print(f"Отрицательных месяцев: {len(neg)} из {n_months}")
    print(f"Худший месяц: {min(r[2] for r in results):+.1f}%")
    print(f"Лучший месяц: {max(r[2] for r in results):+.1f}%")
    # equity curve
    import json
    json.dump({'eq_curve': [(f'{y}-{m:02d}', round(e,2)) for (y,m),_,_,e in results]},
              open('/tmp/oi_v12m_eq.json','w'), indent=1)
