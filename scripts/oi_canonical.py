#!/usr/bin/env python3 -u
"""КАНОНИЧЕСКИЙ бэктестер редких крупных OI-сделок. ОДНА модель, без вариаций.

Спецификация (зафиксирована, не меняется между прогонами):
──────────────────────────────────────────────────────────────────────────
СИГНАЛ:   накопленный day_net = (cur_b - day_start_b) / total_oi * 100
          day_start_b = buy_fiz первой записи дня (первый бар futoi)
          Сигнал: day_net <= -thr  (физлица панически продали за день)
ВХОД:     close бара на момент сигнала + SLIP_TICKS × ms  (только LONG)
ВЫХОД:    open первого бара следующего торгового дня − SLIP_TICKS × ms
          (держим ночь, выходим на открытии — фиксация отскока после паники)
SLIPPAGE: SLIP_TICKS = 1 на вход и выход (лимитка по текущей цене)
КОМИССИЯ: fee_entry × 2 × контрактов
РАЗМЕР:   contracts = equity × RISK / go  (компаунд: риск % от текущего капитала)
ПИРАМИДА: off (базовая модель). Отдельный флаг --pyr для исследования.
ДАННЫЕ:   futoi (накопление) + mt5_continuous (цены, IRK) 2022-2026
ТИКЕРЫ:   только валидные: BR NG SV RN GZ Eu RI LK SN SP MG VB TT AF HY
──────────────────────────────────────────────────────────────────────────
"""
import sys, bisect, argparse
import numpy as np
import clickhouse_connect as cc, psycopg2
from datetime import datetime

sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')
TZ_SHIFT = 5 * 3600  # futoi MSK → цены IRK

VALID = {'BR':'BR', 'NG':'NG', 'SV':'SILV', 'RN':'RN', 'GZ':'GZ', 'Eu':'Eu',
         'RI':'RTSI', 'LK':'LKOH', 'SN':'SNGP', 'SP':'SBRF', 'MG':'MGNT',
         'VB':'VTBR', 'TT':'TATN', 'AF':'AFLT', 'HY':'HYDR'}

def load_tk(fut_tk, mt_tk, y, spec):
    """futoi (накопленный day_net) + mt5_continuous OHLC бары."""
    START, END = f'{y}-01-01', f'{y}-12-31'
    if y == 2026: END = '2026-08-07'
    r = ch.query(f"SELECT bt, buy_fiz, sell_fiz, buy_yur, sell_yur FROM moex.futoi "
                 f"WHERE ticker='{fut_tk}' AND bt>='{START}' AND bt<='{END} 23:59:59'").result_rows
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
    r2 = ch.query(f"SELECT toUnixTimestamp(toDateTime(bt)), opn, hi, lo, prc FROM moex.mt5_continuous "
                  f"WHERE ticker='{mt_tk}' AND bt>='{START}' AND bt<='{END} 23:59:59'").result_rows
    arr = np.array([(ts, o, h, l, c) for ts, o, h, l, c in r2 if c and c > 0], dtype=np.float64)
    if arr.size == 0: return None
    o = np.argsort(arr[:, 0])
    return net_map, arr[o], spec

def gen_signals(years, thr):
    """Один сигнал на тикер в день: max |day_net| среди баров, где day_net <= -thr."""
    sigs = []
    for fut_tk, mt_tk in VALID.items():
        spec = specs.get(mt_tk)
        if spec is None: continue
        for y in years:
            d = load_tk(fut_tk, mt_tk, y, spec)
            if d is None: continue
            net_map, bars, spc = d
            pts = bars[:, 0]
            go, ms, sp, fee = spc
            day_best = {}
            for ts in sorted(net_map.keys()):
                dn = net_map[ts]
                if dn > -thr: continue
                idx = bisect.bisect_right(pts, ts) - 1
                if idx < 0: continue
                prc = bars[idx, 4]
                if prc <= 0 or (ts - pts[idx]) > 600: continue
                day = int((ts - TZ_SHIFT)//86400)
                if day not in day_best or abs(dn) > abs(day_best[day]['dn']):
                    day_best[day] = {'ts': ts, 'prc': prc, 'ms': ms, 'sp': sp,
                                     'fee': fee, 'go': go, 'dn': dn}
            for day, t in day_best.items():
                t['tk'] = fut_tk; t['bars'] = bars
                t['y'] = datetime.fromtimestamp(t['ts'] - TZ_SHIFT).year
                sigs.append(t)
    return sigs

def backtest(sigs, years, risk, slip=1, pyr=1, pyra_ticks=30):
    """Канонический бэктест. Вход close+slip, выход open след. дня −slip, компаунд."""
    eq = 200000.0; peak = eq; mdd = 0.0
    n = 0; wins = 0
    trades = []
    for t in sorted(sigs, key=lambda x: x['ts']):
        ms = t['ms']; sp = t['sp']; fee = t['fee']; go = t['go']
        bars = t['bars']; pts = bars[:, 0]
        entry_ts = t['ts']
        # индекс бара сигнала
        i0 = bisect.bisect_right(pts, entry_ts) - 1
        if i0 < 0: continue
        fill_p = bars[i0, 4] + ms * slip  # вход: close + slippage
        # выход: open первого бара следующего дня
        day = int((entry_ts - TZ_SHIFT)//86400)
        next_day_start = (day + 1) * 86400 - TZ_SHIFT
        j = bisect.bisect_left(pts, next_day_start)
        if j >= len(bars): continue
        exit_p = bars[j, 1] - ms * slip  # open след. дня − slippage
        # лоты
        contracts = max(1, int(eq * risk / go))
        pnl = ((exit_p - fill_p) / ms * sp - fee * 2) * contracts
        eq += pnl; n += 1
        if pnl > 0: wins += 1
        trades.append({'tk': t['tk'], 'y': t['y'], 'pnl': pnl, 'ticks': (exit_p - fill_p)/ms})
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
    ap.add_argument('--pyr', type=int, default=1)
    ap.add_argument('--pyra-ticks', type=int, default=30)
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
    print(f"Сигналов: {len(sigs)} = {len(sigs)/len(years):.0f}/год (thr={args.thr}, {len(VALID)} тикеров)")
    print(f"Модель: вход close+{args.slip}т, выход open след.дня −{args.slip}т, риск {args.risk:.0%}, компаунд")

    r = backtest(sigs, years, args.risk, slip=args.slip, pyr=args.pyr, pyra_ticks=args.pyra_ticks)
    print(f"\nИТОГ: {r['n']} сделок ({r['per_year']:.0f}/год), ROI {r['roi']:+.1f}% за 5 лет, "
          f"CAGR {r['cagr']:.1f}%, MDD {r['mdd']:.1f}%, WR {r['wr']:.1f}%")

    # по годам
    by_y = {}
    for t in r['trades']:
        by_y.setdefault(t['y'], []).append(t['pnl'])
    print(f"\n{'год':<6}{'сдел':>6}{'сум₽':>12}{'WR%':>7}")
    for y in sorted(by_y):
        p = np.array(by_y[y])
        print(f"{y:<6}{len(p):>6}{p.sum():>12,.0f}{(p>0).mean()*100:>7.1f}")

    # по тикерам
    by_tk = {}
    for t in r['trades']:
        by_tk.setdefault(t['tk'], []).append(t['pnl'])
    print(f"\n{'тикер':<6}{'сдел':>6}{'сум₽':>12}{'avg₽':>9}{'WR%':>7}")
    for tk in sorted(by_tk, key=lambda x: -sum(by_tk[x])):
        p = np.array(by_tk[tk])
        print(f"{tk:<6}{len(p):>6}{p.sum():>12,.0f}{p.mean():>9,.0f}{(p>0).mean()*100:>7.1f}")
    ch.close()
