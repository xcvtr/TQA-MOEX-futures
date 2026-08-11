#!/usr/bin/env python3 -u
"""OI v8 — динамический риск по результатам последних сделок тикера.

Идея пользователя: чем успешнее последние N сделок на тикере, тем больше
риск в следующую сделку этого тикера. И наоборот.

Механика:
  - для каждого тикера ведём окно последних N сделок
  - risk_mult = f(win_rate окна): 0.5× при 0% WR, 2.0× при 100% WR (линейно)
  - риск сделки = base_risk × risk_mult
  - компаунд поверх

Варианты окна N: 3, 5, 8. Проверяем на 5 годах + по годам.
"""
import sys, bisect
import numpy as np
import clickhouse_connect as cc, psycopg2
from datetime import datetime, timezone

sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
import scripts.oi_v7_mgmt as v7

pg = psycopg2.connect(host='10.0.0.60', dbname='moex', user='postgres', connect_timeout=5)
cur = pg.cursor()
cur.execute("SELECT ticker, go, min_step, step_price, fee_entry FROM futures.ticker_specs")
v7.specs = {}
for t, go, ms, sp, fee in cur.fetchall():
    v7.specs[t] = (float(go), float(ms), float(sp), float(fee))
pg.close()

years = [2022, 2023, 2024, 2025, 2026]
sigs = v7.gen_signals(years, 5)

def backtest_dyn(sigs, years, risk_map, slip=1, pyr=3, pyra_pct=0.5, horizon_h=24,
                 window=5, min_mult=0.4, max_mult=2.0):
    """Динамический риск: risk_mult по win_rate последних window сделок тикера."""
    eq = 200000.0; peak_cash = eq; peak_mtm = eq
    cash_mdd = mtm_mdd = 0.0
    n = 0; wins = 0
    eq_by_year = {}
    # история результатов по тикерам (для динамики)
    history = {tk: [] for tk in risk_map}
    for t in sorted(sigs, key=lambda x: x['ts']):
        tk = t['tk']
        ms = t['ms']; sp = t['sp']; fee = t['fee']; go = t['go']
        bars = t['bars']; pts = bars[:, 0]
        i0 = bisect.bisect_right(pts, t['ts']) - 1
        if i0 < 0: continue
        fill_p = bars[i0, 4] + ms * slip
        risk_base = risk_map.get(tk, 0.08)
        # ДИНАМИКА: множитель по win_rate последних window сделок тикера
        h = history[tk][-window:]
        if len(h) >= 2:
            wr = np.mean(h)
            risk_mult = min_mult + (max_mult - min_mult) * wr  # 0%WR→min, 100%WR→max
        else:
            risk_mult = 1.0  # нет истории — базовый риск
        risk = risk_base * risk_mult
        base_lots = max(1, int(eq * risk / go))
        parts = [(base_lots, fill_p)]
        i_max = bisect.bisect_right(pts, t['ts'] + horizon_h * 3600)
        for k in range(1, pyr):
            level = fill_p * (1 + k * pyra_pct / 100)
            found = False
            for bi in range(i0, min(i_max, len(bars))):
                if bars[bi, 2] >= level:
                    parts.append((base_lots, bars[bi, 2] + ms * slip))
                    found = True
                    break
            if not found: break
        j = bisect.bisect_left(pts, t['ts'] + 24 * 3600)
        if j >= len(bars): continue
        exit_p = bars[j, 4] - ms * slip
        pnl = 0.0
        for lots, p_in in parts:
            pnl += ((exit_p - p_in) / ms * sp - fee * 2) * lots
        eq += pnl; n += 1
        won = pnl > 0
        if won: wins += 1
        history[tk].append(1.0 if won else 0.0)
        peak_cash = max(peak_cash, eq)
        cash_mdd = max(cash_mdd, (peak_cash - eq) / peak_cash * 100)
        # MTM
        for bi in range(i0, min(j, len(bars))):
            lo = bars[bi, 3]
            mtm_pnl = 0.0
            for lots, p_in in parts:
                mtm_pnl += ((lo - p_in) / ms * sp - fee * 2) * lots
            mtm_eq = (eq - pnl) + mtm_pnl
            peak_mtm = max(peak_mtm, mtm_eq)
            mtm_mdd = max(mtm_mdd, (peak_mtm - mtm_eq) / peak_mtm * 100 if peak_mtm > 0 else 0)
        y = datetime.fromtimestamp(t['ts'], tz=timezone.utc).year
        eq_by_year[y] = eq
    per_year = n / len(years)
    cagr = ((1 + (eq-200000)/200000) ** (1/len(years)) - 1) * 100 if eq > 0 else -100
    return {'n': n, 'per_year': round(per_year,1), 'roi': round((eq-200000)/200000*100,1),
            'cash_mdd': round(cash_mdd,1), 'mtm_mdd': round(mtm_mdd,1),
            'wr': round(wins/n*100,1) if n else 0, 'cagr': round(cagr,1),
            'eq_by_year': eq_by_year}

risk_map = {'BR': 0.08, 'NG': 0.08, 'SV': 0.05, 'RI': 0.05, 'TT': 0.05}

print("=== База (фикс риск 8/5%) ===")
r = v7.backtest(sigs, years, risk_map, pyr=3, pyra_pct=0.5, horizon_h=24)
print(f"CAGR {r['cagr']:.1f}%, CashMDD {r['cash_mdd']:.1f}%, MTM {r['mtm_mdd']:.1f}%, WR {r['wr']:.1f}%")

print("\n=== Динамический риск (окно N сделок тикера) ===")
print(f"{'окно':<6}{'mult':<16}{'CAGR%':>8}{'CashMDD':>9}{'MTM MDD':>9}{'WR%':>7}")
for w in [3, 5, 8]:
    for mn, mx in [(0.4, 2.0), (0.5, 1.5), (0.3, 2.5)]:
        r = backtest_dyn(sigs, years, risk_map, pyr=3, pyra_pct=0.5, horizon_h=24,
                         window=w, min_mult=mn, max_mult=mx)
        print(f"{w:<6}{mn}-{mx:<14}{r['cagr']:>8.1f}{r['cash_mdd']:>9.1f}{r['mtm_mdd']:>9.1f}{r['wr']:>7.1f}")
