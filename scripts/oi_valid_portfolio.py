#!/usr/bin/env python3 -u
"""Честный портфель редких крупных сделок — только валидные тикеры.

Валидные (ликвидные фьючерсы MOEX, проверенный маппинг):
  BR, NG, SV(SILV), RN, GZ, Eu, RI(RTSI), PD(GD? палладий), ME(NOTK? медь),
  LK(LKOH), SN(SNGP), SP(SBRF), MG(MGNT), VB(VTBR), TT(TATN), AF(AFLT), HY(HYDR)

Исключены мусорные маппинги: OJ, KC, VI, AL, RM, RB, UC, MY, MN, MX, NA, NM, NR, SE, SF, SR, TN, X5, GK, GL, PT, AU(неоднозначно), CR, ED, GD, MM (убыточные/неверные).

Портфельный бэктест: общий капитал, risk% на сделку, reinvest, опционально пирамидинг.
"""
import sys, bisect
import numpy as np
import clickhouse_connect as cc, psycopg2

sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')
TZ_SHIFT = 5 * 3600

# ВАЛИДНЫЕ пары futoi -> mt5
VALID = {
 'BR':'BR', 'NG':'NG', 'SV':'SILV', 'RN':'RN', 'GZ':'GZ', 'Eu':'Eu',
 'RI':'RTSI', 'LK':'LKOH', 'SN':'SNGP', 'SP':'SBRF', 'MG':'MGNT',
 'VB':'VTBR', 'TT':'TATN', 'AF':'AFLT', 'HY':'HYDR',
}

def load_tk(fut_tk, mt_tk, y, spec):
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
    r2 = ch.query(f"SELECT toUnixTimestamp(toDateTime(bt)), prc FROM moex.mt5_continuous "
                  f"WHERE ticker='{mt_tk}' AND bt>='{START}' AND bt<='{END} 23:59:59'").result_rows
    arr = np.array([(ts, c) for ts, c in r2 if c and c > 0], dtype=np.float64)
    if arr.size == 0: return None
    o = np.argsort(arr[:, 0])
    return net_map, (arr[o, 0], arr[o, 1]), spec

pg = psycopg2.connect(host='10.0.0.60', dbname='moex', user='postgres', connect_timeout=5)
cur = pg.cursor()
cur.execute("SELECT ticker, go, min_step, step_price, fee_entry FROM futures.ticker_specs")
specs = {}
for t, go, ms, sp, fee in cur.fetchall():
    specs[t] = (float(go), float(ms), float(sp), float(fee))
pg.close()

def gen_signals(years, thr=8):
    sigs = []
    for fut_tk, mt_tk in VALID.items():
        spec = specs.get(mt_tk)
        if spec is None:
            print(f"  ⚠️ нет specs для {mt_tk}")
            continue
        for y in years:
            d = load_tk(fut_tk, mt_tk, y, spec)
            if d is None: continue
            net_map, prices, spc = d
            pts, pprc = prices
            go, ms, sp, fee = spc
            day_best = {}
            for ts in sorted(net_map.keys()):
                dn = net_map[ts]
                if dn > -thr: continue
                idx = bisect.bisect_right(pts, ts) - 1
                if idx < 0: continue
                prc = pprc[idx]
                if prc <= 0 or (ts - pts[idx]) > 600: continue
                cutoff = (int((ts - TZ_SHIFT)//86400)+1)*86400 - TZ_SHIFT
                j = bisect.bisect_left(pts, cutoff + 86400)
                if j >= len(pts): continue
                exit_p = pprc[j]
                day = int((ts - TZ_SHIFT)//86400)
                if day not in day_best or abs(dn) > abs(day_best[day]['dn']):
                    day_best[day] = {'ts': ts, 'exit_p': exit_p, 'prc': prc, 'ms': ms,
                                     'sp': sp, 'fee': fee, 'go': go, 'dn': dn}
            for day, t in day_best.items():
                t['tk'] = fut_tk; t['day'] = day
                sigs.append(t)
    return sigs

def run(sigs, years, risk=0.10, pyr_max=1, pyra_ticks=20):
    eq = 200000.0; peak = eq; mdd = 0.0
    n = 0; wins = 0
    for t in sorted(sigs, key=lambda x: x['ts']):
        ms = t['ms']; sp = t['sp']; fee = t['fee']; go = t['go']
        fill_p = t['prc'] + ms
        exit_p = t['exit_p'] + ms  # выход тоже +1 тик slippage (худшая сторона)
        lots = max(1, int(eq * risk / go))
        # ЧЕСТНЫЙ пирамидинг: добавки входят по цене входа + k*pyra_ticks (как реально),
        # НЕ по exit цене. total PnL = Σ по каждой части.
        total_pnl = 0.0
        n_parts = 0
        if pyr_max <= 1:
            total_pnl = ((exit_p - fill_p) / ms * sp - fee * 2) * lots
        else:
            # части: базовая + добавки каждые pyra_ticks (вход добавки выше входа)
            ticks_move = (exit_p - fill_p) / ms
            n_adds = 0
            if ticks_move > 0:
                n_adds = min(pyr_max - 1, int(ticks_move / pyra_ticks))
            for k in range(n_adds + 1):
                if k == 0:
                    add_entry = fill_p
                else:
                    add_entry = fill_p + ms * k * pyra_ticks  # вход добавки по текущей цене
                part_pnl = ((exit_p - add_entry) / ms * sp - fee * 2) * lots
                total_pnl += part_pnl
                n_parts += 1
        eq += total_pnl; n += 1
        if total_pnl > 0: wins += 1
        peak = max(peak, eq)
        mdd = max(mdd, (peak - eq) / peak * 100)
    per_year = n / len(years)
    return {'n': n, 'per_year': round(per_year,1), 'roi': round((eq-200000)/200000*100,1),
            'mdd': round(mdd,1), 'wr': round(wins/n*100,1) if n else 0}

years = [2022, 2023, 2024, 2025, 2026]
sigs = gen_signals(years)
print(f"\nСигналов: {len(sigs)} = {len(sigs)/len(years):.0f}/год")

# по тикерам
print(f"\n{'тикер':<6}{'сдел':>6}{'сдел/год':>9}{'avg₽':>9}{'WR%':>7}")
by_tk = {}
for s in sigs:
    by_tk.setdefault(s['tk'], []).append(s)
for tk, ss in sorted(by_tk.items()):
    pnls = [((t['exit_p']-t['prc'])/t['ms']*t['sp'] - t['fee']*2) for t in ss]
    print(f"{tk:<6}{len(ss):>6}{len(ss)/5:>9.1f}{np.mean(pnls):>9.0f}{(np.array(pnls)>0).mean()*100:>7.1f}")

print(f"\n{'конфиг':<26}{'сдел/год':<9}{'ROI%':>10}{'MDD%':>8}{'WR%':>7}{'Calmar':>8}")
print("-" * 68)
for risk in [0.05, 0.10, 0.15, 0.20]:
    for pyr in [1, 2, 3]:
        r = run(sigs, years, risk=risk, pyr_max=pyr)
        calmar = r['roi']/r['mdd'] if r['mdd'] > 0 else 0
        print(f"risk{risk:.0%} pyr{pyr}  {r['per_year']:<9.0f}{r['roi']:>+10.1f}"
              f"{r['mdd']:>8.1f}{r['wr']:>7.1f}{calmar:>8.1f}")
ch.close()
