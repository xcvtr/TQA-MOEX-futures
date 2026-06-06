#!/home/user/venvs/tqa/main/bin/python
"""Test COT filter thresholds + combined calendar+COT."""
import json, warnings
from pathlib import Path
import pandas as pd
import numpy as np
import psycopg2

warnings.filterwarnings('ignore')

DB = dict(host="10.0.0.60", port=5432, dbname="forex", user="postgres", password="postgres")
OUTDIR = Path("/home/user/.hermes/cache/screenshots/tqa/equity_cluster/2025")
SYMBOLS = ['audjpy','audusd','euraud','eurgbp','eurjpy','eurusd',
           'gbpjpy','gbpusd','nzdusd','usdcad','usdchf','usdjpy','xauusd']
COT_MAP = {
    'audjpy':[('cftc-aud-non-commercial-net-positions',1),('cftc-jpy-non-commercial-net-positions',-1)],
    'audusd':[('cftc-aud-non-commercial-net-positions',1)],
    'euraud':[('cftc-eur-non-commercial-net-positions',1),('cftc-aud-non-commercial-net-positions',-1)],
    'eurgbp':[('cftc-eur-non-commercial-net-positions',1),('cftc-gbp-non-commercial-net-positions',-1)],
    'eurjpy':[('cftc-eur-non-commercial-net-positions',1),('cftc-jpy-non-commercial-net-positions',-1)],
    'eurusd':[('cftc-eur-non-commercial-net-positions',1)],
    'gbpjpy':[('cftc-gbp-non-commercial-net-positions',1),('cftc-jpy-non-commercial-net-positions',-1)],
    'gbpusd':[('cftc-gbp-non-commercial-net-positions',1)],
    'nzdusd':[('cftc-nzd-non-commercial-net-positions',1)],
    'usdcad':[('cftc-cad-non-commercial-net-positions',-1)],
    'usdchf':[('cftc-chf-non-commercial-net-positions',-1)],
    'usdjpy':[('cftc-jpy-non-commercial-net-positions',-1)],
    'xauusd':[('cftc-gold-non-commercial-net-positions',1)],
}

def load_cot(conn):
    cur = conn.cursor()
    cur.execute("SELECT event_code, event_time, actual_value FROM economic_calendar WHERE event_code LIKE 'cftc-%' AND actual_value IS NOT NULL ORDER BY event_code, event_time")
    data = {}
    for c, t, v in cur.fetchall():
        data.setdefault(c, []).append({'time': pd.Timestamp(t), 'value': float(v)})
    return data

def zscore(data, code, trade_time, lookback=52):
    s = data.get(code, [])
    before = [x for x in s if x['time'] <= trade_time]
    if len(before) < lookback + 1: return None
    cur = before[-1]['value']
    hist = [x['value'] for x in before[-(lookback+1):-1]]
    if len(hist) < lookback: return None
    m, std = np.mean(hist), np.std(hist)
    return (cur - m) / std if std > 0 else 0.0

def main():
    import psycopg2 as pg2
    with open(OUTDIR/"equity_results.json") as f: all_data = json.load(f)
    conn = pg2.connect(**DB)
    cot = load_cot(conn)
    print(f"COT data: {sum(len(v) for v in cot.values())} points\n")
    for lookback in [26, 52, 104]:
        print(f"Lookback {lookback}wk:")
        for sym in SYMBOLS:
            trades = all_data.get(sym,{}).get('trades',[])
            if not trades: continue
            base_p = sum(float(t['pnl_pips']) for t in trades)
            for thresh in [1.0, 1.5, 2.0, 2.5, 3.0]:
                passed = []
                for t in trades:
                    entry = pd.Timestamp(t['entry'])
                    blocked = False
                    for code, sign in COT_MAP.get(sym,[]):
                        z = zscore(cot, code, entry, lookback)
                        if z is None: continue
                        if abs(z * sign) > thresh: blocked = True; break
                    if not blocked: passed.append(t)
                if not passed: continue
                p = [float(t['pnl_pips']) for t in passed]
                wr = sum(1 for t in passed if t['won'])/len(passed)*100
                print(f"  {sym:8s} z>{thresh:.1f} {len(p):2d}tr WR={wr:.1f}% PnL={sum(p):+.0f}p blocked {len(trades)-len(p)}/{len(trades)}")

if __name__ == '__main__': main()
