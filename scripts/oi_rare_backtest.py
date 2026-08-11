#!/usr/bin/env python3 -u
"""Редкие крупные сделки: накопленный day_net + длинный hold.

Гипотеза: накопленные за день продажи физ (day_net накопительный) + выход
EOD/+1д даёт крупные движения (десятки тиков) вместо 5 тиков.

Проверяем бэктест: порог 8/10/15/20, hold EOD/+1д, LONG only, risk 2-5%,
общий пул. Цель: 20-150 сделок/год, avg PnL в сотнях тиков.
"""
import sys, bisect
import numpy as np
import clickhouse_connect as cc

sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
from scripts.oi_vol_filter_backtest import load, MAX_MARGIN

ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')
TZ_SHIFT = 5 * 3600
FT = {'SV': 'SV', 'NG': 'NG', 'BR': 'BR'}

def load_daily_net_full(tk, years):
    """Накопленный day_net + цены за годы."""
    data = {}
    MT = {'SV': 'SILV', 'NG': 'NG', 'BR': 'BR'}
    for y in years:
        START, END = f'{y}-01-01', f'{y}-12-31'
        if y == 2026: END = '2026-08-07'
        r = ch.query(f"SELECT bt, buy_fiz, sell_fiz, buy_yur, sell_yur FROM moex.futoi "
                     f"WHERE ticker='{FT[tk]}' AND bt>='{START}' AND bt<='{END} 23:59:59'").result_rows
        day_start = {}
        net_map = {}
        for bt, fb, fs, yb, ys in r:
            ts = bt.replace(tzinfo=None).timestamp() + TZ_SHIFT
            d = int((ts - TZ_SHIFT) // 86400)
            if d not in day_start:
                day_start[d] = int(fb) - int(fs)
            total = int(fb) + int(fs) + int(yb) + int(ys)
            if total <= 0: continue
            net_map[ts] = (int(fb) - int(fs) - day_start[d]) / total * 100
        # цены
        r2 = ch.query(f"SELECT toUnixTimestamp(toDateTime(bt)), prc FROM moex.mt5_continuous "
                      f"WHERE ticker='{MT[tk]}' AND bt>='{START}' AND bt<='{END} 23:59:59'").result_rows
        arr = np.array([(ts, c) for ts, c in r2 if c and c > 0], dtype=np.float64)
        if arr.size == 0:
            continue
        o = np.argsort(arr[:, 0])
        prices = (arr[o, 0], arr[o, 1])
        # spec из PG
        import psycopg2
        pg = psycopg2.connect(host='10.0.0.60', dbname='moex', user='postgres', connect_timeout=5)
        cur = pg.cursor()
        cur.execute("SELECT go, min_step, step_price, fee_entry FROM futures.ticker_specs WHERE ticker=%s", (tk,))
        row = cur.fetchone()
        pg.close()
        spec = (float(row[0]), float(row[1]), float(row[2]), float(row[3]))
        data[y] = (net_map, prices, spec)
    return data

def run(tk, years, thr, hold_mode, risk_pct):
    """hold_mode: 'eod' — до конца дня, '+1d' — следующий день открытие."""
    data = load_daily_net_full(tk, years)
    eq = 200000.0; peak = eq; mdd = 0.0
    trades = []
    for y in years:
        if y not in data:
            continue
        net_map, prices, spec = data[y]
        pts, pprc = prices
        go, ms, sp, fee = spec[1-1] if False else (spec[0], spec[1], spec[2], spec[3])
        for ts in sorted(net_map.keys()):
            dn = net_map[ts]
            if dn > -thr: continue
            idx = bisect.bisect_right(pts, ts) - 1
            if idx < 0: continue
            prc = pprc[idx]
            if prc <= 0 or (ts - pts[idx]) > 600: continue
            # выход
            if hold_mode == 'eod':
                day_end = int((ts - TZ_SHIFT) // 86400) + 1
                cutoff = day_end * 86400 - TZ_SHIFT
                j = bisect.bisect_right(pts, cutoff) - 1
                if j <= idx: continue
                exit_p = pprc[j]
            else:  # +1d
                day_end = int((ts - TZ_SHIFT) // 86400) + 1
                cutoff = day_end * 86400 - TZ_SHIFT
                j = bisect.bisect_left(pts, cutoff + 86400)
                if j >= len(pts): continue
                exit_p = pprc[j]
            # сделка (одна на день — берём первый сигнал дня, дальше не дублируем)
            pnl_rub = (exit_p - prc) / ms * sp - fee * 2
            ticks = round((exit_p - prc) / ms)
            trades.append({'ts': ts, 'y': y, 'pnl_rub': pnl_rub, 'ticks': ticks,
                           'prc': prc, 'dn': dn})
    # уникальные дни (одна сделка на день — берём максимум |dn|)
    by_day = {}
    for t in trades:
        d = int((t['ts'] - TZ_SHIFT) // 86400)
        if d not in by_day or abs(t['dn']) > abs(by_day[d]['dn']):
            by_day[d] = t
    trades = list(by_day.values())
    # симуляция
    for t in trades:
        shares = max(1, int(eq * risk_pct / (spec[0])))
        # грубая маржа
        pnl = t['pnl_rub'] * shares
        eq += pnl
        peak = max(peak, eq)
        mdd = max(mdd, (peak - eq) / peak * 100)
    return {'n': len(trades), 'roi': round((eq-200000)/200000*100,1),
            'mdd': round(mdd,1), 'avg_ticks': round(np.mean([t['ticks'] for t in trades]),1) if trades else 0,
            'avg_pnl': round(np.mean([t['pnl_rub'] for t in trades]),0) if trades else 0,
            'wr': round(np.mean([t['pnl_rub']>0 for t in trades])*100,1) if trades else 0}

print(f"{'тикер':<5}{'порог':<7}{'выход':<6}{'risk':<6}{'сдел/год':<10}{'ROI%':>10}{'MDD%':>8}{'avg_тик':>9}{'avg₽':>8}{'WR%':>7}")
print("-" * 90)
for tk in ['NG', 'BR', 'SV']:
    for thr in [8, 10, 15]:
        for hold in ['eod', '+1d']:
            for rp in [0.03]:
                res = run(tk, [2023, 2024, 2025, 2026], thr, hold, rp)
                per_year = res['n'] / 4
                print(f"{tk:<5}{thr:<7}{hold:<6}{rp:<6.0%}{per_year:<10.0f}"
                      f"{res['roi']:>+10.1f}{res['mdd']:>8.1f}{res['avg_ticks']:>9.1f}"
                      f"{res['avg_pnl']:>8.0f}{res['wr']:>7.1f}")

ch.close()
