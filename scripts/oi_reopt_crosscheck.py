#!/usr/bin/env python3 -u
"""Сверка реоптимизатора с честным аудитом на одинаковых 12 мес."""
import sys, bisect
sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
import numpy as np
import clickhouse_connect as cc, psycopg2
from datetime import datetime, timezone, timedelta

ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')
DAY_SEC = 86400
ALL = {'BR': 'BR', 'NG': 'NG', 'SV': 'SILV', 'RI': 'RTSI', 'TT': 'TATN'}
TICKER_LIMITS = {'BR': 100, 'NG': 100, 'SV': 80, 'RN': 80, 'RI': 50, 'TT': 30}
MAX_MARGIN = 0.80

conn = psycopg2.connect(host='10.0.0.60', dbname='moex', user='postgres', connect_timeout=5)
cur = conn.cursor()
cur.execute("SELECT ticker, go, min_step, step_price, fee_entry FROM futures.ticker_specs")
specs = {}
for t, go, ms, sp, fee in cur.fetchall(): specs[t] = (float(go), float(ms), float(sp), float(fee))
conn.close()

def irk_day(ts): return int((ts - 7*3600) // DAY_SEC)

def load_period(months=12):
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=30*months)
    s, e = start.strftime('%Y-%m-%d'), end.strftime('%Y-%m-%d')
    data = {}
    for fut_tk, mt_tk in ALL.items():
        r = ch.query(f"SELECT toUnixTimestamp(toDateTime(bt)), buy_fiz, sell_fiz, buy_yur, sell_yur "
                     f"FROM moex.futoi WHERE ticker='{fut_tk}' AND bt>='{s}' AND bt<='{e}'").result_rows
        day_start = {}; net_map = {}
        for ts, fb, fs, yb, ys in r:
            d = irk_day(ts)
            if d not in day_start: day_start[d] = int(fb) - int(fs)
            total = int(fb)+int(fs)+int(yb)+int(ys)
            if total <= 0: continue
            net_map[ts] = (int(fb)-int(fs)-day_start[d]) / total * 100
        r2 = ch.query(f"SELECT toUnixTimestamp(toDateTime(bt)), opn, hi, lo, prc "
                      f"FROM moex.mt5_continuous WHERE ticker='{mt_tk}' AND bt>='{s}' AND bt<='{e}'").result_rows
        arr = np.array([(ts,o,h,l,c) for ts,o,h,l,c in r2 if c and c>0], dtype=np.float64)
        if arr.size == 0: continue
        o = np.argsort(arr[:,0])
        data[fut_tk] = (net_map, arr[o])
    return data

def gen(data, specs, thr, exit_thr, pyr=3, pyra_pct=0.5):
    sigs = []
    for fut_tk, (net_map, bars) in data.items():
        if fut_tk not in specs: continue
        go, ms, sp, fee = specs[fut_tk]
        pts = bars[:,0]; fts = sorted(net_map.keys())
        for direction in ['long','short']:
            pos = None
            for ts in fts:
                dn = net_map[ts]
                if pos is not None:
                    idx = bisect.bisect_right(pts, ts) - 1
                    if idx < 0: continue
                    cur_p = bars[idx,4]
                    exit_cond = (dn >= exit_thr) if direction=='long' else (dn <= -exit_thr)
                    hold_h = (ts - pos['entry_ts'])/3600
                    if exit_cond or hold_h >= 120:
                        exit_p = cur_p - ms if direction=='long' else cur_p + ms
                        sigs.append({'entry_ts':pos['entry_ts'],'exit_ts':ts,'exit_p':exit_p,
                                     'dir':direction,'tk':fut_tk,'go':go,'ms':ms,'sp':sp,'fee':fee,
                                     'entry_p':pos['entry_p'],'pyra_prices':list(pos['pyra_prices'])})
                        pos = None
                if pos is None:
                    in_cond = (dn <= -thr) if direction=='long' else (dn >= thr)
                    if in_cond:
                        idx = bisect.bisect_right(pts, ts) - 1
                        if idx < 0: continue
                        fill_p = bars[idx,4] + ms if direction=='long' else bars[idx,4] - ms
                        pos = {'entry_ts':ts,'entry_p':fill_p,'pyra_prices':[]}
                elif pos is not None and len(pos['pyra_prices']) < pyr-1:
                    idx = bisect.bisect_right(pts, ts) - 1
                    if idx >= 0:
                        if direction=='long':
                            hi = bars[idx,2]
                            if (hi-pos['entry_p'])/pos['entry_p']*100 >= (len(pos['pyra_prices'])+1)*pyra_pct:
                                pos['pyra_prices'].append(hi+ms)
                        else:
                            lo = bars[idx,3]
                            if (pos['entry_p']-lo)/pos['entry_p']*100 >= (len(pos['pyra_prices'])+1)*pyra_pct:
                                pos['pyra_prices'].append(lo-ms)
    return sigs

def sim(sigs, risk=0.10, eq0=200000.0):
    eq = eq0; peak = eq; mdd = 0.0; n=0; wins=0
    open_pos = []
    for s in sorted(sigs, key=lambda x:x['entry_ts']):
        open_pos = [p for p in open_pos if p[0] > s['entry_ts']]
        go = s['go']; max_lots = TICKER_LIMITS.get(s['tk'],50)
        n_parts = 1 + len(s['pyra_prices'])
        base = max(1, int(eq*risk/go)); base = min(base, max_lots)
        gt = base*go*n_parts
        used = sum(p[1] for p in open_pos)
        avail = eq*MAX_MARGIN - used
        if avail <= 0: continue
        if gt > avail:
            base = max(1, int(avail/(go*n_parts))); base = min(base, max_lots); gt = base*go*n_parts
        if base < 1: continue
        pnl = 0.0
        for p_in in [s['entry_p']]+s['pyra_prices']:
            if s['dir']=='long': pnl += ((s['exit_p']-p_in)/s['ms']*s['sp'] - s['fee']*2)*base
            else: pnl += ((p_in-s['exit_p'])/s['ms']*s['sp'] - s['fee']*2)*base
        eq += pnl; n += 1
        if pnl>0: wins += 1
        peak = max(peak, eq); mdd = max(mdd, (peak-eq)/peak*100)
        open_pos.append((s['exit_ts'], gt))
    return eq, mdd, n, wins/n*100 if n else 0

data = load_period(12)
print(f"Тикеров: {len(data)}")
for thr, ex in [(3,2),(4,2),(4,3),(5,2)]:
    sigs = gen(data, specs, thr, ex)
    eq, mdd, n, wr = sim(sigs, risk=0.10)
    print(f"12мес thr{thr} ex{ex}: ROI {(eq/200000-1)*100:+.1f}%  MDD {mdd:.1f}%  N={n}  WR={wr:.1f}%")
# Без пирамидинга для сравнения
sigs = gen(data, specs, 4, 2, pyr=1)
eq, mdd, n, wr = sim(sigs, risk=0.10)
print(f"12мес thr4 ex2 pyr1: ROI {(eq/200000-1)*100:+.1f}%  MDD {mdd:.1f}%  N={n}  WR={wr:.1f}%")
