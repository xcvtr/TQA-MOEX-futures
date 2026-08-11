#!/usr/bin/env python3 -u
"""OI v9 — выход по состоянию ОИ (обратному входу) + проверка шорта.

Вход LONG:  day_net <= -thr (физ панически продают)
Выход LONG: day_net >= +exit_thr (физ начали покупать — паника кончилась)
            ИЛИ max_hold часов

Вход SHORT: day_net >= +thr (физ панически покупают)
Выход SHORT: day_net <= -exit_thr (физ начали продавать)

Симметрично. Проверяем оба направления.
"""
import sys, bisect
import numpy as np
import clickhouse_connect as cc, psycopg2
from datetime import datetime, timezone

sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')
DAY_SEC = 86400

ALL = {'BR': 'BR', 'NG': 'NG', 'SV': 'SILV', 'RI': 'RTSI', 'TT': 'TATN'}

def irk_day(ts):
    return int((ts - 7 * 3600) // DAY_SEC)

def load_tk(fut_tk, mt_tk, y):
    START, END = f'{y}-01-01', f'{y}-12-31'
    if y == 2026: END = '2026-08-07'
    r = ch.query(f"SELECT toUnixTimestamp(toDateTime(bt)), buy_fiz, sell_fiz, buy_yur, sell_yur "
                 f"FROM moex.futoi WHERE ticker='{fut_tk}' "
                 f"AND bt>='{START}' AND bt<='{END} 23:59:59'").result_rows
    day_start = {}
    net_map = {}
    for ts, fb, fs, yb, ys in r:
        d = irk_day(ts)
        if d not in day_start:
            day_start[d] = int(fb) - int(fs)
        total = int(fb) + int(fs) + int(yb) + int(ys)
        if total <= 0: continue
        net_map[ts] = (int(fb) - int(fs) - day_start[d]) / total * 100
    r2 = ch.query(f"SELECT toUnixTimestamp(toDateTime(bt)), opn, hi, lo, prc FROM moex.mt5_continuous "
                  f"WHERE ticker='{mt_tk}' AND bt>='{START}' AND bt<='{END} 23:59:59'").result_rows
    arr = np.array([(ts, o, h, l, c) for ts, o, h, l, c in r2 if c and c > 0], dtype=np.float64)
    if arr.size == 0: return None
    o = np.argsort(arr[:, 0])
    return net_map, arr[o]

def load_all(years, thr):
    """Загружаем все данные + сигналы входа (по времени, не по дням)."""
    data = {}
    for fut_tk, mt_tk in ALL.items():
        spec = specs.get(fut_tk)
        if spec is None: continue
        for y in years:
            d = load_tk(fut_tk, mt_tk, y)
            if d is None: continue
            net_map, bars = d
            data[(fut_tk, y)] = (net_map, bars, spec)
    return data

def backtest_oi_exit(data, years, risk_map, thr=5, exit_thr=3, max_hold_h=120,
                     direction='long', slip=1, pyr=3, pyra_pct=0.5, window=5,
                     min_mult=0.6, max_mult=1.2):
    """Вход по thr, выход когда day_net достиг exit_thr (обратное условие)."""
    eq = 200000.0; peak_cash = eq; peak_mtm = eq
    cash_mdd = mtm_mdd = 0.0
    n = 0; wins = 0
    eq_by_year = {}
    history = {tk: [] for tk in risk_map}
    all_ts = sorted(set(ts for (fut_tk, y), (net_map, bars, spec) in data.items() for ts in net_map))
    # Упрощение: обрабатываем по тикерам отдельно (вход/выход в пределах тикера)
    trades = []
    for (fut_tk, y), (net_map, bars, spec) in data.items():
        go, ms, sp, fee = spec
        pts = bars[:, 0]
        fts = sorted(net_map.keys())
        # идём по времени, открываем позицию при входном условии, закрываем при выходном
        pos = None  # (entry_ts, fill_p, parts)
        for i, ts in enumerate(fts):
            dn = net_map[ts]
            # проверяем выход
            if pos is not None:
                # цена на текущий момент
                idx = bisect.bisect_right(pts, ts) - 1
                if idx >= 0:
                    cur_p = bars[idx, 4]
                    # выходное условие
                    if direction == 'long':
                        exit_cond = dn >= exit_thr
                    else:
                        exit_cond = dn <= -exit_thr
                    hold_h = (ts - pos['entry_ts']) / 3600
                    if exit_cond or hold_h >= max_hold_h:
                        exit_p = cur_p - ms * slip if direction == 'long' else cur_p + ms * slip
                        pnl = 0.0
                        for lots, p_in in pos['parts']:
                            if direction == 'long':
                                pnl += ((exit_p - p_in) / ms * sp - fee * 2) * lots
                            else:
                                pnl += ((p_in - exit_p) / ms * sp - fee * 2) * lots
                        eq += pnl; n += 1
                        won = pnl > 0
                        if won: wins += 1
                        history[fut_tk].append(1.0 if won else 0.0)
                        peak_cash = max(peak_cash, eq)
                        cash_mdd = max(cash_mdd, (peak_cash - eq) / peak_cash * 100)
                        # MTM: худший бар за время позиции
                        i0 = bisect.bisect_right(pts, pos['entry_ts']) - 1
                        for bi in range(i0, idx+1):
                            lo = bars[bi, 3]
                            mtm_pnl = 0.0
                            for lots, p_in in pos['parts']:
                                if direction == 'long':
                                    mtm_pnl += ((lo - p_in) / ms * sp - fee * 2) * lots
                                else:
                                    mtm_pnl += ((p_in - lo) / ms * sp - fee * 2) * lots
                            mtm_eq = (eq - pnl) + mtm_pnl
                            peak_mtm = max(peak_mtm, mtm_eq)
                            mtm_mdd = max(mtm_mdd, (peak_mtm - mtm_eq) / peak_mtm * 100 if peak_mtm > 0 else 0)
                        eq_by_year[y] = eq
                        pos = None
            # вход (если нет позиции)
            if pos is None:
                if direction == 'long':
                    in_cond = dn <= -thr
                else:
                    in_cond = dn >= thr
                if in_cond:
                    idx = bisect.bisect_right(pts, ts) - 1
                    if idx < 0: continue
                    fill_p = bars[idx, 4] + ms * slip if direction == 'long' else bars[idx, 4] - ms * slip
                    # динамический риск
                    h = history[fut_tk][-window:]
                    risk_mult = min_mult + (max_mult - min_mult) * np.mean(h) if len(h) >= 2 else 1.0
                    risk = risk_map.get(fut_tk, 0.07) * risk_mult
                    base_lots = max(1, int(eq * risk / go))
                    parts = [(base_lots, fill_p)]
                    # пирамидинг не на баре входа — упрощённо, добавки не считаем здесь
                    pos = {'entry_ts': ts, 'fill_p': fill_p, 'parts': parts}
                    # cooldown: не входить снова 6 часов после выхода
    per_year = n / len(years)
    cagr = ((1 + (eq-200000)/200000) ** (1/len(years)) - 1) * 100 if eq > 0 else -100
    return {'n': n, 'per_year': round(per_year,1), 'roi': round((eq-200000)/200000*100,1),
            'cash_mdd': round(cash_mdd,1), 'mtm_mdd': round(mtm_mdd,1),
            'wr': round(wins/n*100,1) if n else 0, 'cagr': round(cagr,1),
            'eq_by_year': eq_by_year}

if __name__ == '__main__':
    pg = psycopg2.connect(host='10.0.0.60', dbname='moex', user='postgres', connect_timeout=5)
    cur = pg.cursor()
    cur.execute("SELECT ticker, go, min_step, step_price, fee_entry FROM futures.ticker_specs")
    global specs
    specs = {}
    for t, go, ms, sp, fee in cur.fetchall():
        specs[t] = (float(go), float(ms), float(sp), float(fee))
    pg.close()

    years = [2022, 2023, 2024, 2025, 2026]
    data = load_all(years, 5)
    risk_map = {'BR': 0.07, 'NG': 0.07, 'SV': 0.04, 'RI': 0.04, 'TT': 0.04}

    print("=== Выход по состоянию ОИ ===")
    print(f"{'напр':<6}{'exit_thr':<10}{'max_hold':<10}{'сдел':>6}{'/год':>5}{'CAGR%':>8}{'CashMDD':>9}{'MTM MDD':>9}{'WR%':>7}")
    for direction in ['long', 'short']:
        for exit_thr in [0, 2, 3, 5]:
            for mh in [48, 120]:
                r = backtest_oi_exit(data, years, risk_map, thr=5, exit_thr=exit_thr,
                                     max_hold_h=mh, direction=direction)
                print(f"{direction:<6}{exit_thr:<10}{mh:<10}{r['n']:>6}{r['per_year']:>5.0f}"
                      f"{r['cagr']:>8.1f}{r['cash_mdd']:>9.1f}{r['mtm_mdd']:>9.1f}{r['wr']:>7.1f}")
        print()
    ch.close()
