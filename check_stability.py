#!/usr/bin/env python3
"""Check strategy stability: WR by year for each ticker."""
import psycopg2, numpy as np
from datetime import datetime, timedelta

DB = dict(host='10.0.0.64', port=5432, dbname='moex', user='postgres', password='***')

TICKERS = ['ME','GK','CC','SP','SS','CE','AL','NM','NG','GZ','MG','RN','HS','SN','HY','GL',
           'BR','RI','Si']  # + the ones user asked about

GO_DATA = {
    'BR':{'lev':3.8},'RI':{'lev':6.6},'Si':{'lev':6.7},
    'CC':{'lev':6.4},'PD':{'lev':4.6},'SS':{'lev':2.0},'GZ':{'lev':5.7},
    'NG':{'lev':3.5},'GL':{'lev':8.7},'SE':{'lev':1.4},'SN':{'lev':4.9},
    'HY':{'lev':4.9},'IB':{'lev':3.5},'NM':{'lev':5.8},
    'GK':{'lev':5.8},'MG':{'lev':5.8},'RN':{'lev':4.9},'AL':{'lev':3.9},
    'SP':{'lev':2.0},'ME':{'lev':5.8},'CE':{'lev':11.9},'HS':{'lev':114.9},
}

def run_strategy(sym, since):
    go_info = GO_DATA.get(sym, {'lev': 5.0})
    lev = go_info.get('lev', 5.0)
    
    conn = psycopg2.connect(**DB)
    cur = conn.cursor()
    cur.execute("SELECT time, open, high, low, close, volume FROM moex_prices_5m WHERE symbol = %s AND time >= %s AND volume > 0 ORDER BY time", (sym, since))
    rows = cur.fetchall()
    conn.close()
    if len(rows) < 100:
        return None
    
    h4 = {}
    for t, o, h, l, c, v in rows:
        h4_key = t.replace(minute=0, second=0, microsecond=0) - timedelta(hours=t.hour % 4)
        if h4_key not in h4:
            h4[h4_key] = [t, o, h, l, c, v]
        else:
            prev = h4[h4_key]
            h4[h4_key] = [prev[0], prev[1], max(prev[2], h), min(prev[3], l), c, prev[5] + v]
    h4_bars = sorted(h4.values(), key=lambda x: x[0])
    if len(h4_bars) < 35:
        return None
    
    data = []
    for i, (t, o, h, l, c, v) in enumerate(h4_bars):
        d = {'time': t, 'open': o, 'high': h, 'low': l, 'close': c, 'volume': v,
             'range_pct': (h - l) / l * 100 if l else 0}
        if i >= 20:
            window = h4_bars[i - 20:i]
            vols = [w[5] for w in window]
            med_vol = np.median(vols) if vols else 1
            d['vol_ratio'] = v / max(med_vol, 1)
            ranges = [(w[2] - w[3]) / w[3] * 100 for w in window if w[3] > 0]
            d['avg_range_pct'] = np.mean(ranges) if ranges else 0
            d['close_pos'] = (c - l) / (h - l) if h != l else 0.5
        else:
            d['vol_ratio'] = 0; d['avg_range_pct'] = 0; d['close_pos'] = 0.5
        data.append(d)
    
    sigs = []
    for i, d in enumerate(data):
        if d['vol_ratio'] <= 2 or d['range_pct'] <= d.get('avg_range_pct', 0):
            continue
        is_red = d['close'] < d['open']
        is_green = d['close'] > d['open']
        is_bear = is_red and d['close_pos'] <= 0.35
        is_bull = is_green and d['close_pos'] >= 0.65
        if not is_bear and not is_bull: continue
        if i + 1 + 2 >= len(data): continue
        
        entry = data[i+1]['open'] * 1.001
        hold = [data[i+1+k] for k in range(2)]
        
        if is_bear:
            tp = entry * 1.004; sl = entry * 0.992; trail_be = entry * 1.001
            trail_sl = sl; trailed = False; reason = 'timeout'
            for bar in hold:
                if bar['high'] >= tp: reason = 'tp'; break
                if bar['low'] <= trail_sl: reason = 'sl'; break
                if not trailed and bar['high'] >= entry * 1.005:
                    trail_sl = trail_be; trailed = True
            exit_p = {'tp': tp, 'sl': trail_sl, 'timeout': hold[-1]['close']}[reason]
            ret = (exit_p - entry) / entry * 100
        else:
            tp = entry * 0.996; sl = entry * 1.008; trail_be = entry * 0.999
            trail_sl = sl; trailed = False; reason = 'timeout'
            for bar in hold:
                if bar['low'] <= tp: reason = 'tp'; break
                if bar['high'] >= trail_sl: reason = 'sl'; break
                if not trailed and bar['low'] <= entry * 0.995:
                    trail_sl = trail_be; trailed = True
            exit_p = {'tp': tp, 'sl': trail_sl, 'timeout': hold[-1]['close']}[reason]
            ret = (entry - exit_p) / entry * 100
        
        sigs.append({'ret': ret, 'win': ret > 0, 'reason': reason, 'time': d['time']})
    
    return sigs

for sym in TICKERS:
    sigs = run_strategy(sym, '2024-01-01')
    if not sigs or len(sigs) < 20:
        print(f"{sym:>6}: no data")
        continue
    
    # Split by year
    years = {}
    for s in sigs:
        y = s['time'].year
        years.setdefault(y, []).append(s)
    
    parts = []
    go_info = GO_DATA.get(sym, {'lev': 5.0})
    lev = go_info.get('lev', 5.0)
    total_wr = sum(1 for s in sigs if s['win']) / len(sigs) * 100
    total_go = sum(s['ret'] for s in sigs) * lev
    
    for y in sorted(years.keys()):
        ys = years[y]
        n = len(ys)
        wr = sum(1 for s in ys if s['win']) / n * 100
        go = sum(s['ret'] for s in ys) * lev
        parts.append(f"{y}: {n} sig WR {wr:.0f}% GO {go:+.0f}%")
    
    print(f"{sym:>6} | всего {len(sigs)} sig | WR {total_wr:.0f}% GO {total_go:+.0f}%")
    for p in parts:
        print(f"         {p}")
    print()
