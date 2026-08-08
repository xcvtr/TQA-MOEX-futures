#!/usr/bin/env python3 -u
"""Редкие крупные: все тикеры с маппингом futoi→mt5 (thr 8, +1д).

Проверяем какие тикеры дают сигналы и их качество. Спецификации берём из
ticker_specs если есть, иначе из известных значений (для оценки).
"""
import sys, bisect
import numpy as np
import clickhouse_connect as cc, psycopg2

sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')
TZ_SHIFT = 5 * 3600

F2M = {
 'BR':'BR','NG':'NG','SV':'SILV','RN':'RN','GZ':'GZ','Eu':'Eu','GD':'GD','MM':'MM',
 'CR':'CR','ED':'ED','TT':'TATN','VB':'VTBR','LK':'LKOH','MG':'MGNT','SN':'SNGP',
 'SR':'SNGR','VI':'SBRF','GAZPF':'GAZR','HY':'HYDR','MX':'MIX','RI':'RTSI',
 'AF':'AFLT','X5':'X5','GL':'GOLD','PT':'GOLD','PD':'GD','AU':'GD','AL':'ROSN',
 'ME':'NOTK','RM':'ROSN','NM':'MTSI','RB':'BR','UC':'CNY','KC':'MTSI','OJ':'NOTK',
 'NA':'MTSI','MN':'MM','MY':'MM','NR':'NG','GK':'GOLD','SP':'SBRF','SF':'SBRF','SE':'SBRF',
 'RL':'LKOH','TN':'TATN','YD':'YDEX','RTKM':'RTKM',
}
MT2F = {}
for f, m in F2M.items():
    MT2F.setdefault(m, f)

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

# specs из PG
pg = psycopg2.connect(host='10.0.0.60', dbname='moex', user='postgres', connect_timeout=5)
cur = pg.cursor()
cur.execute("SELECT ticker, go, min_step, step_price, fee_entry FROM futures.ticker_specs")
specs = {}
for t, go, ms, sp, fee in cur.fetchall():
    specs[t] = (float(go), float(ms), float(sp), float(fee))
pg.close()

# для тикеров без specs — пропускаем (нужны точные)
print(f"{'тикер':<8}{'mt5':<8}{'сдел':>6}{'сдел/год':>9}{'avg_тик':>9}{'WR%':>7}{'профит₽':>11}")
print("-" * 60)
all_trades = []
for fut_tk, mt_tk in sorted(F2M.items()):
    spec = specs.get(mt_tk) or specs.get(fut_tk)
    if spec is None:
        continue
    trades = []
    for y in [2022, 2023, 2024, 2025, 2026]:
        d = load_tk(fut_tk, mt_tk, y, spec)
        if d is None: continue
        net_map, prices, spc = d
        pts, pprc = prices
        go, ms, sp, fee = spc
        day_best = {}
        for ts in sorted(net_map.keys()):
            dn = net_map[ts]
            if dn > -8: continue
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
        trades.extend(day_best.values())
    if not trades:
        continue
    ticks = np.array([(t['exit_p']-t['prc'])/t['ms'] for t in trades])
    pnl = np.array([((t['exit_p']-t['prc'])/t['ms']*t['sp'] - t['fee']*2) for t in trades])
    wr = (pnl > 0).mean() * 100
    per_year = len(trades) / 5
    print(f"{fut_tk:<8}{mt_tk:<8}{len(trades):>6}{per_year:>9.1f}{ticks.mean():>9.1f}"
          f"{wr:>7.1f}{pnl.sum():>11,.0f}")
    for t in trades:
        t['tk'] = fut_tk; t['mt'] = mt_tk
    all_trades.extend(trades)

print(f"\nИТОГО: {len(all_trades)} сделок = {len(all_trades)/5:.0f}/год")
pnl_all = np.array([((t['exit_p']-t['prc'])/t['ms']*t['sp'] - t['fee']*2) for t in all_trades])
print(f"avg {pnl_all.mean():.0f}₽, WR {(pnl_all>0).mean()*100:.1f}%, суммарно {pnl_all.sum():,.0f}₽")
ch.close()
