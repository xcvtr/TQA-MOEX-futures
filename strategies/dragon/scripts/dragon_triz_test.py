#!/usr/bin/env python3 -u
"""Dragon TRIZ — trend, volume, loss-skip filters for best tickers."""
import sys, os, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
import clickhouse_connect as cc
from strategies.dragon.prod.engine import check_signal as dragon_check

SPECS = {
    'BR': {'ms': 0.01, 'sp': 7.70611, 'go': 9001},
    'GD': {'ms': 0.1, 'sp': 7.84756, 'go': 15685},
    'RN': {'ms': 1.0, 'sp': 1.0, 'go': 4002},
    'MM': {'ms': 0.05, 'sp': 0.5, 'go': 1404},
    'NG': {'ms': 0.001, 'sp': 7.70611, 'go': 20519},
}
TA, TT, SL, TO = 0.015, 0.005, 0.01, 60
TC = 4

ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')
all_m1 = {}
for t in ['BR', 'GD', 'RN', 'MM', 'NG']:
    rows = ch.query("SELECT bt,opn,hi,lo,prc,vol FROM moex.mt5_continuous WHERE ticker='" + t + "' AND bt>='2025-07-16' ORDER BY bt").result_rows
    bars = []
    for r in rows:
        ts = r[0]; h, m = ts.hour, ts.minute
        if ts.weekday() >= 5: continue
        if h < 15 or h > 23 or (h == 23 and m > 45): continue
        bars.append({'ts': ts, 'opn': float(r[1]), 'hi': float(r[2]), 'lo': float(r[3]), 'prc': float(r[4]), 'vol': float(r[5])})
    all_m1[t] = bars
    print(f'{t}: {len(bars)} M1 bars', flush=True)
ch.close()

def resample_to_N(m1_bars, N):
    g = {}
    for b in m1_bars:
        total_m = b['ts'].hour * 60 + b['ts'].minute
        k_min = (total_m // N) * N
        k = b['ts'].replace(minute=k_min % 60, hour=k_min // 60, second=0)
        if k not in g:
            g[k] = {'ts': k, 'opn': b['opn'], 'hi': b['hi'], 'lo': b['lo'], 'prc': b['prc'], 'vol': b['vol']}
        else:
            gg = g[k]; gg['hi'] = max(gg['hi'], b['hi']); gg['lo'] = min(gg['lo'], b['lo']); gg['prc'] = b['prc']; gg['vol'] += b['vol']
    return sorted(g.values(), key=lambda x: x['ts'])

BEST = [
    ('GD', 10, 10, 'GD 10m risk=10%'),
    ('MM', 5, 5, 'MM 5m risk=5%'),
    ('BR', 15, 10, 'BR 15m risk=10%'),
    ('RN', 5, 10, 'RN 5m risk=10%'),
    ('NG', 3, 10, 'NG 3m risk=10%'),
]

for ticker, tf, risk_pct, label in BEST:
    m1 = all_m1[ticker]
    dbars = resample_to_N(m1, tf)
    sp = SPECS[ticker]['sp']; ms = SPECS[ticker]['ms']; go = SPECS[ticker]['go']
    risk = risk_pct / 100.0
    
    d2m = {}; di = 0
    for mi in range(len(m1)):
        if di < len(dbars) and m1[mi]['ts'] >= dbars[di]['ts']: d2m[di] = mi; di += 1
    
    for filt_name, use_trend, use_vol, use_lskip in [
        ('BASELINE',    False, False, False),
        ('TREND',       True,  False, False),
        ('VOL>80%',     False, True,  False),
        ('LOSS_SKIP2',  False, False, True),
        ('TREND+VOL',   True,  True,  False),
        ('ALL',         True,  True,  True),
    ]:
        eq = 200000.0; peak = eq; mtm_pk = eq; cdd = 0; mdd = 0; pos = None; tr = []
        df = set(); dxi = 0; cls = 0
        
        for mi in range(60, len(m1)):
            b = m1[mi]
            
            # Skip detect after losing streak
            if use_lskip and cls >= 2:
                # Still check positions but skip new signals
                pass
            
            if not pos and dxi < len(dbars) and dxi not in df and mi >= d2m.get(dxi, 999999999):
                df.add(dxi); db = dbars[dxi]; dh = dbars[:dxi]
                if len(dh) >= 30 and not (use_lskip and cls >= 2):
                    sig = dragon_check({'bars_list': dh, 'prc': db['prc']}, ticker, {'impulse_pct': 0.3, 'retrace_max_pct': 70, 'hump_extension': 0.1, 'lookback': 100})
                    if sig and use_trend and len(dh) >= 50:
                        sma50 = sum(x['prc'] for x in dh[-50:]) / 50
                        if sig['direction'] == 'long' and db['prc'] < sma50: sig = None
                        elif sig['direction'] == 'short' and db['prc'] > sma50: sig = None
                    if sig and use_vol:
                        m1_idx = d2m.get(dxi, mi)
                        if m1_idx > 0 and m1_idx < len(m1):
                            cur_vol = m1[m1_idx]['vol']
                            vols = [m1[j]['vol'] for j in range(max(0, m1_idx-20), m1_idx)]
                            if vols and sum(vols) / len(vols) > 0:
                                med_v = sorted(vols)[len(vols)//2]
                                if cur_vol < med_v * 0.8: sig = None
                    if sig:
                        sh = max(1, int(eq * risk / go))
                        if go * sh <= eq:
                            pos = {'dir': sig['direction'], 'ep': sig['entry_price'], 'bi': mi, 'shares': sh, 'tr': False}
                            cls = 0
                dxi += 1
            
            if pos:
                ex = None; slv = pos['ep'] * (1 - SL) if pos['dir'] == 'long' else pos['ep'] * (1 + SL)
                if (pos['dir'] == 'long' and b['lo'] <= slv) or (pos['dir'] == 'short' and b['hi'] >= slv): ex = slv
                if not ex:
                    if not pos.get('tr'):
                        act = pos['ep'] * (1 + TA) if pos['dir'] == 'long' else pos['ep'] * (1 - TA)
                        if (pos['dir'] == 'long' and b['hi'] >= act) or (pos['dir'] == 'short' and b['lo'] <= act):
                            pos['tr'] = True; pos['tl'] = b['hi'] * (1 - TT) if pos['dir'] == 'long' else b['lo'] * (1 + TT)
                    if pos.get('tr') and ((pos['dir'] == 'long' and b['lo'] <= pos['tl']) or (pos['dir'] == 'short' and b['hi'] >= pos['tl'])): ex = pos['tl']
                if not ex and mi - pos['bi'] >= TO: ex = b['prc']
                if ex:
                    pnl = (ex - pos['ep']) / ms * sp * (-1 if pos['dir'] == 'short' else 1) * pos['shares'] - TC * pos['shares']
                    eq += pnl; tr.append(pnl); peak = max(peak, eq); cdd = max(cdd, (peak - eq) / peak * 100)
                    if pnl < 0: cls += 1
                    else: cls = 0
                    pos = None
            
            fl = (b['prc'] - pos['ep']) / ms * sp * (-1 if pos['dir'] == 'short' else 1) * pos['shares'] if pos else 0
            mv = eq + fl; mtm_pk = max(mtm_pk, mv)
            if mtm_pk > 0: mdd = max(mdd, (mtm_pk - mv) / mtm_pk * 100)
        
        tnn = len(tr)
        if tnn:
            w = [p for p in tr if p > 0]; l = [p for p in tr if p <= 0]
            wr = len(w) / tnn * 100; pf = sum(w) / sum(abs(p) for p in l) if l else 0; rt = (eq - 200000) / 200000 * 100
            print(f'{label:25s} {filt_name:12s}: n={tnn:4d} WR={wr:5.1f}% PF={pf:>5.2f} ROI={rt:>+7.1f}% MDD={mdd:5.2f}%', flush=True)
