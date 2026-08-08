#!/usr/bin/env python3 -u
"""КАНОНИЧЕСКИЙ бэктестер v2 — ЧИСТЫЕ таймзоны (UTC epoch).

ПРЕДЫДУЩИЙ БАГ: futoi и mt5_continuous ОБА в IRK (+8), а скрипты добавляли
TZ_SHIFT=+5ч к futoi → сигналы уезжали на 5ч вперёд от цен. Плюс формула
'next_day_start' вычисляла неправильный день (выход до входа).

ФИКС: работаем в UTC epoch (toUnixTimestamp) — одинаков для обеих таблиц.
День считаем по IRK-дате (торговая сессия MOEX).

Спецификация:
  СИГНАЛ: day_net = (cur_b - day_start_b) / total_oi * 100 <= -thr
          (накопленная паника физ за день), один сигнал на тикер в день (max |dn|)
  ВХОД:   close бара на момент сигнала + 1 тик
  ВЫХОД:  (выбирается параметром) open след. торгового дня / close того же дня / N часов
  КОМИССИЯ: fee × 2 × contracts
  РАЗМЕР:  contracts = equity × RISK / go (компаунд)
"""
import sys, bisect, argparse
import numpy as np
import clickhouse_connect as cc, psycopg2
from datetime import datetime, timezone, timedelta

sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')

# Торговые сессии MOEX в IRK (+8):
#   утренняя 07:00-08:45 MSK = 12:00-13:45 IRK
#   основная 10:00-18:45 MSK = 15:00-23:45 IRK  ← 'открытие дня' = 15:00 IRK
#   вечерняя 19:00-23:50 MSK = 00:00-04:50 IRK (след. сутки)
SESSION_OPEN_IRK = 15 * 3600  # 15:00 IRK = открытие основной сессии
DAY_SEC = 86400

VALID = {'BR':'BR', 'NG':'NG', 'SV':'SILV', 'RN':'RN', 'GZ':'GZ', 'Eu':'Eu',
         'RI':'RTSI', 'LK':'LKOH', 'SN':'SNGP', 'SP':'SBRF', 'MG':'MGNT',
         'VB':'VTBR', 'TT':'TATN', 'AF':'AFLT', 'HY':'HYDR'}

def irk_day(ts_utc):
    """Торговый день по IRK: 15:00 IRK = граница дня. ts_utc → номер дня."""
    # ts в IRK = ts_utc + 8ч. День N начинается в 15:00 IRK = 07:00 UTC.
    return int((ts_utc - 7 * 3600) // DAY_SEC)

def irk_day_start_utc(day):
    """Начало торгового дня (15:00 IRK) в UTC."""
    return day * DAY_SEC + 7 * 3600

def load_tk(fut_tk, mt_tk, y):
    START, END = f'{y}-01-01', f'{y}-12-31'
    if y == 2026: END = '2026-08-07'
    # toUnixTimestamp → UTC epoch (одинаково для обеих таблиц)
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

def gen_signals(years, thr):
    sigs = []
    for fut_tk, mt_tk in VALID.items():
        spec = specs.get(mt_tk)
        if spec is None: continue
        for y in years:
            d = load_tk(fut_tk, mt_tk, y)
            if d is None: continue
            net_map, bars = d
            pts = bars[:, 0]
            go, ms, sp, fee = spec
            day_best = {}
            for ts in sorted(net_map.keys()):
                dn = net_map[ts]
                if dn > -thr: continue
                idx = bisect.bisect_right(pts, ts) - 1
                if idx < 0: continue
                prc = bars[idx, 4]
                if prc <= 0 or (ts - pts[idx]) > 600: continue
                dnum = irk_day(ts)
                if dnum not in day_best or abs(dn) > abs(day_best[dnum]['dn']):
                    day_best[dnum] = {'ts': ts, 'prc': prc, 'ms': ms, 'sp': sp,
                                      'fee': fee, 'go': go, 'dn': dn}
            for dnum, t in day_best.items():
                t['tk'] = fut_tk; t['dnum'] = dnum; t['bars'] = bars
                t['y'] = datetime.fromtimestamp(ts, tz=timezone.utc).year if False else \
                         datetime.fromtimestamp(irk_day_start_utc(dnum) + 3*3600).year
                sigs.append(t)
    return sigs

def backtest(sigs, years, risk, slip=1, exit_mode='open_next'):
    """exit_mode: open_next (open след. дня), close_day (close того же дня),
                   hours_N (через N часов после входа)"""
    eq = 200000.0; peak = eq; mdd = 0.0
    n = 0; wins = 0
    trades = []
    for t in sorted(sigs, key=lambda x: x['ts']):
        ms = t['ms']; sp = t['sp']; fee = t['fee']; go = t['go']
        bars = t['bars']; pts = bars[:, 0]
        i0 = bisect.bisect_right(pts, t['ts']) - 1
        if i0 < 0: continue
        fill_p = bars[i0, 4] + ms * slip
        dnum = t['dnum']
        if exit_mode == 'open_next':
            # открытие следующего торгового дня = первый бар с ts >= начало след. дня
            t_exit = irk_day_start_utc(dnum + 1)
            j = bisect.bisect_left(pts, t_exit)
            if j >= len(bars): continue
            exit_p = bars[j, 1] - ms * slip  # open − slippage
        elif exit_mode == 'close_day':
            # конец дня сигнала = начало следующего дня
            t_exit = irk_day_start_utc(dnum + 1)
            j = bisect.bisect_right(pts, t_exit) - 1
            if j <= i0: continue
            exit_p = bars[j, 4] - ms * slip  # close − slippage
        elif exit_mode.startswith('hours'):
            h = int(exit_mode.split('_')[1])
            j = bisect.bisect_left(pts, t['ts'] + h * 3600)
            if j >= len(bars): continue
            exit_p = bars[j, 4] - ms * slip
        contracts = max(1, int(eq * risk / go))
        pnl = ((exit_p - fill_p) / ms * sp - fee * 2) * contracts
        eq += pnl; n += 1
        if pnl > 0: wins += 1
        trades.append({'tk': t['tk'], 'dnum': dnum, 'pnl': pnl})
        peak = max(peak, eq)
        mdd = max(mdd, (peak - eq) / peak * 100)
    per_year = n / len(years)
    cagr = ((1 + (eq-200000)/200000) ** (1/len(years)) - 1) * 100 if eq > 0 else -100
    return {'n': n, 'per_year': round(per_year,1), 'roi': round((eq-200000)/200000*100,1),
            'mdd': round(mdd,1), 'wr': round(wins/n*100,1) if n else 0, 'cagr': round(cagr,1),
            'trades': trades}

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--thr', type=float, default=8.0)
    ap.add_argument('--risk', type=float, default=0.10)
    ap.add_argument('--slip', type=int, default=1)
    ap.add_argument('--exit', default='open_next', help='open_next|close_day|hours_2|hours_4|hours_24')
    args = ap.parse_args()

    pg = psycopg2.connect(host='10.0.0.60', dbname='moex', user='postgres', connect_timeout=5)
    cur = pg.cursor()
    cur.execute("SELECT ticker, go, min_step, step_price, fee_entry FROM futures.ticker_specs")
    global specs
    specs = {}
    for t, go, ms, sp, fee in cur.fetchall():
        specs[t] = (float(go), float(ms), float(sp), float(fee))
    pg.close()

    years = [2022, 2023, 2024, 2025, 2026]
    sigs = gen_signals(years, args.thr)
    print(f"Сигналов: {len(sigs)} = {len(sigs)/len(years):.0f}/год (thr={args.thr})")
    r = backtest(sigs, years, args.risk, slip=args.slip, exit_mode=args.exit)
    print(f"Выход: {args.exit} | ROI {r['roi']:+.1f}% за 5 лет, CAGR {r['cagr']:.1f}%, "
          f"MDD {r['mdd']:.1f}%, WR {r['wr']:.1f}%, {r['n']} сделок ({r['per_year']:.0f}/год)")
    # по тикерам
    by_tk = {}
    for t in r['trades']:
        by_tk.setdefault(t['tk'], []).append(t['pnl'])
    print(f"\n{'тикер':<6}{'сдел':>6}{'сум₽':>12}{'WR%':>7}")
    for tk in sorted(by_tk, key=lambda x: -sum(by_tk[x])):
        p = np.array(by_tk[tk])
        print(f"{tk:<6}{len(p):>6}{p.sum():>12,.0f}{(p>0).mean()*100:>7.1f}")
    ch.close()
