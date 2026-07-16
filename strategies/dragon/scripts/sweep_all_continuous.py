#!/usr/bin/env python3 -u
"""Sweep ALL 113 FINAM indicative continuous symbols."""
import sys, os, time, json
from datetime import datetime
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import clickhouse_connect as cc
from dragon.prod.engine import check_signal
from dragon.scripts.backtest_m1 import SPECS

CH = dict(host='10.0.0.60', port=8123, database='moex')
TC = 4
CAPITAL = 200000


def load_any(ticker, days=365):
    """Load from mt5_continuous if exists, else try mt5_bars."""
    cutoff = '2025-07-16' if days >= 365 else '2026-01-16'
    ch = cc.get_client(**CH)
    # Try mt5_continuous first
    rows = ch.query(f"""
        SELECT bt, opn, hi, lo, prc
        FROM moex.mt5_continuous
        WHERE ticker = '{ticker}'
          AND bt >= '{cutoff}'
        ORDER BY bt
    """).result_rows
    if not rows:
        # Try mt5_bars
        rows = ch.query(f"""
            SELECT bt, opn, hi, lo, prc
            FROM moex.mt5_bars
            WHERE ticker = '{ticker}'
              AND bt >= '{cutoff}'
            ORDER BY bt
        """).result_rows
    ch.close()
    bars = []
    for r in rows:
        ts = r[0]
        if ts.weekday() >= 5: continue
        h, m = ts.hour, ts.minute
        if h < 15 or h > 23 or (h == 23 and m > 45): continue
        bars.append({'ts': ts, 'opn': float(r[1]), 'hi': float(r[2]), 'lo': float(r[3]), 'prc': float(r[4])})
    return bars


def bt(ticker, bars, contracts=2,
       impulse=0.3, retrace_max=70, hump=0.1, lookback=100,
       trail_act=0.015, trail_trail=0.005, sl_pct=0.01, to_m1=60):
    # Try to get specs, use defaults if not found
    if ticker in SPECS:
        s = SPECS[ticker]
        ms, sp = s['ms'], s['sp']
    else:
        ms, sp = 0.01, 1.0  # defaults for unknown tickers

    dp = {'impulse_pct': impulse, 'retrace_max_pct': retrace_max,
          'hump_extension': hump, 'lookback': lookback}
    trades, open_pos = [], None
    m5 = []
    for i in range(30, len(bars)):
        if i % 5 == 0:
            g = bars[i-5:i]
            if len(g) >= 3:
                m5.append({'opn': g[0]['opn'], 'hi': max(b['hi'] for b in g),
                           'lo': min(b['lo'] for b in g), 'prc': g[-1]['prc']})
        bar = bars[i]
        if open_pos:
            ep = open_pos['ep']
            exit_p = None
            sl = ep * (1 - sl_pct) if open_pos['dir'] == 'long' else ep * (1 + sl_pct)
            if (open_pos['dir'] == 'long' and bar['lo'] <= sl) or \
               (open_pos['dir'] == 'short' and bar['hi'] >= sl):
                exit_p = sl
            if not exit_p and i % 5 == 4:
                if not open_pos.get('tr'):
                    if (open_pos['dir'] == 'long' and bar['hi'] >= ep*(1+trail_act)) or \
                       (open_pos['dir'] == 'short' and bar['lo'] <= ep*(1-trail_act)):
                        open_pos['tr'] = True
                        open_pos['tl'] = bar['hi']*(1-trail_trail) if open_pos['dir']=='long' else bar['lo']*(1+trail_trail)
                if open_pos.get('tr'):
                    if (open_pos['dir'] == 'long' and bar['lo'] <= open_pos['tl']) or \
                       (open_pos['dir'] == 'short' and bar['hi'] >= open_pos['tl']):
                        exit_p = open_pos['tl']
            if not exit_p and i - open_pos['bi'] >= to_m1:
                exit_p = bar['prc']
            if exit_p is not None:
                raw = (exit_p - ep) / ms * sp - TC
                trades.append((raw if open_pos['dir'] == 'long' else -raw) * contracts)
                open_pos = None
        if i % 5 == 0 and not open_pos:
            if len(m5) < 6: continue
            slc = m5[-(lookback+10):]
            sig = check_signal({'prc': slc[-1]['prc'], 'bars_list': slc}, ticker, dp)
            if sig:
                open_pos = {'bi': i, 'ep': sig['entry_price'], 'dir': sig['direction'],
                            'tr': False, 'tl': None}
    return trades


def compute(pnls):
    n = len(pnls)
    if n < 5: return 0, 0, 0, 0, 0, 0
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    wr = len(wins)/n*100
    total = sum(pnls)
    tp = sum(wins)
    tn = sum(abs(p) for p in losses)
    pf = tp/tn if tn else float('inf')
    cap = CAPITAL
    peak = cap
    mdd = 0
    for p in pnls:
        cap += p
        peak = max(peak, cap)
        mdd = max(mdd, (peak - cap)/peak*100)
    ret = (cap - CAPITAL)/CAPITAL*100
    calmar = ret/mdd if mdd > 0 else 0
    return total, wr, pf, mdd, ret, calmar


# All MT5 continuous symbols we have data for
ALL_CONT = ['BR', 'Si', 'CR', 'GD', 'GZ', 'MM', 'NG', 'RN', 'SV']

# Also get indicative continuous names from FINAM
# We'll fetch them via wine python
import subprocess, json
result = subprocess.run(['wine', 'python', '-u', '-c', '''
import MetaTrader5 as mt5, json, sys
mt5.initialize(path=r"C:/Program Files/MetaTrader 5 FINAM/terminal64.exe")
symbols = mt5.symbols_get()
cont = [s.name for s in symbols if s.path.startswith("Indicative continuous")]
print(json.dumps(sorted(cont)))
mt5.shutdown()
'''], capture_output=True, text=True, timeout=30)

cont_from_mt5 = json.loads(result.stdout.strip().split('\\n')[-1])
print(f'All indicative continuous from MT5: {len(cont_from_mt5)} symbols', flush=True)

# Map continuous names to our ticker system
# Those that don't have direct tickers, we'll use the MT5 name as key
# But we need to load data for them from MT5 directly
# For now, sweep what we have in CH + test a few from MT5 directly

print(f'\\n=== Sweeping {len(ALL_CONT)} known continuous from CH ===', flush=True)
results = []
for t in ALL_CONT:
    bars = load_any(t, 365)
    if len(bars) < 100:
        print(f'  {t:10s}: only {len(bars)} bars, skip', flush=True)
        continue
    trades = bt(t, bars, 2)
    total, wr, pf, mdd, ret, calmar = compute(trades)
    results.append((t, total, wr, pf, mdd, ret, calmar, len(bars), len(trades)))
    print(f'  {t:10s} ({len(bars):>6d} bars): {len(trades):4d} trades  pnl={total:+8.0f}  wr={wr:5.1f}%  pf={pf:.2f}  mdd={mdd:.2f}%  calmar={calmar:.1f}', flush=True)

# Now test some MT5 continuous symbols directly (pull from MT5)
print(f'\\n=== Testing continuous from MT5 directly ===', flush=True)
mt5_cont_tickers = ['ALLFUTBR', 'ALLFUTSi', 'ALLFUTCNY', 'ALLFUTGAZR', 'ALLFUTGOLD',
                     'MOEXMM', 'ALLFUTNG', 'ALLFUTROSN', 'ALLFUTSILV',
                     'ALLFUTAFLT', 'ALLFUTFEES', 'ALLFUTGAZR', 'ALLFUTHYDR',
                     'ALLFUTLKOH', 'ALLFUTMGNT', 'ALLFUTMIX', 'ALLFUTMTSI',
                     'ALLFUTNASD', 'ALLFUTNOTK', 'ALLFUTRTKM', 'ALLFUTRTSI',
                     'ALLFUTSBPR', 'ALLFUTSBRF', 'ALLFUTSNGP', 'ALLFUTSNGR',
                     'ALLFUTSPYF', 'ALLFUTTATN', 'ALLFUTTRNF', 'ALLFUTVTBR',
                     'ALLFUTBTC', 'ALLFUTETH', 'ALLFUTES', 'ALLFUTHANG',
                     'ALLFUTED', 'ALLFUTEU', 'IMOEX', 'RTSI', 'RGBI', 'RVI']

for name in mt5_cont_tickers:
    # Pull from MT5 via wine python
    script = f'''
import MetaTrader5 as mt5, json, sys
from datetime import datetime
mt5.initialize(path=r"C:/Program Files/MetaTrader 5 FINAM/terminal64.exe")
rates = mt5.copy_rates_range("{name}", mt5.TIMEFRAME_M1, datetime(2025,7,16), datetime.now())
if rates is None or len(rates) == 0:
    print(json.dumps({{"error": "no data"}}))
else:
    bars = []
    for r in rates:
        ts = datetime.fromtimestamp(r[0])
        bars.append({{"ts":str(ts),"opn":float(r[1]),"hi":float(r[2]),"lo":float(r[3]),"prc":float(r[4])}})
    print(json.dumps({{"bars": bars}}))
mt5.shutdown()
'''
    r2 = subprocess.run(['wine', 'python', '-u', '-c', script], capture_output=True, text=True, timeout=60)
    out = r2.stdout.strip().split('\\n')[-1]
    try:
        data = json.loads(out)
    except:
        print(f'  {name:20s}: parse error', flush=True)
        continue
    if 'error' in data:
        print(f'  {name:20s}: no data', flush=True)
        continue
    raw = data['bars']
    bars = []
    for b in raw:
        ts = datetime.fromisoformat(b['ts'])
        if ts.weekday() >= 5: continue
        h, m = ts.hour, ts.minute
        if h < 15 or h > 23 or (h == 23 and m > 45): continue
        bars.append({'ts': ts, 'opn': b['opn'], 'hi': b['hi'], 'lo': b['lo'], 'prc': b['prc']})
    if len(bars) < 100:
        print(f'  {name:20s}: only {len(bars)} MOEX bars, skip', flush=True)
        continue
    trades = bt(name, bars, 2)
    total, wr, pf, mdd, ret, calmar = compute(trades)
    results.append((name, total, wr, pf, mdd, ret, calmar, len(bars), len(trades)))
    print(f'  {name:20s} ({len(bars):>6d} bars): {len(trades):4d} trades  pnl={total:+8.0f}  wr={wr:5.1f}%  pf={pf:.2f}  mdd={mdd:.2f}%  calmar={calmar:.1f}', flush=True)

print(f'\\n{"="*70}')
print(f'ALL RESULTS SORTED BY PnL:')
print(f'{"="*70}')
results.sort(key=lambda x: x[1], reverse=True)
for r in results:
    print(f'  {r[0]:20s} ({r[7]:>6d} bars): {r[8]:4d} tr  pnl={r[1]:+8.0f}  wr={r[2]:5.1f}%  pf={r[3]:.2f}  mdd={r[4]:.2f}%  calmar={r[5]:.1f}', flush=True)
