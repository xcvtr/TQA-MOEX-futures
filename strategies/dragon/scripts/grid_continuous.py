#!/usr/bin/env python3 -u
"""Grid search dragon params на continuous M1 — таргет MDD<20%, макс PnL."""
import sys, os, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import clickhouse_connect as cc
from dragon.prod.engine import check_signal

CH = dict(host='10.0.0.60', port=8123, database='moex')
TC = 4
CAPITAL = 200000

TICKERS = ['BR', 'NG', 'RN', 'MM']

SPECS = {
    'Si': {'ms': 1.0, 'sp': 1.0},
    'GZ': {'ms': 1.0, 'sp': 1.0},
    'CR': {'ms': 0.01, 'sp': 1.0},
    'RN': {'ms': 1.0, 'sp': 1.0},
    'GD': {'ms': 0.05, 'sp': 1.0},
    'NG': {'ms': 0.001, 'sp': 7.66647},
    'BR': {'ms': 0.01, 'sp': 7.66647},
    'MM': {'ms': 0.01, 'sp': 1.0},
    'NR': {'ms': 0.01, 'sp': 7.66647},
    'BM': {'ms': 0.01, 'sp': 7.66647},
}


def load_bars(ticker, days=365):
    cutoff = '2025-07-16' if days >= 365 else '2026-01-16'
    ch = cc.get_client(**CH)
    rows = ch.query(f"""
        SELECT bt, opn, hi, lo, prc
        FROM moex.mt5_continuous
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


def backtest(ticker, bars, contracts=2,
             impulse=0.3, retrace_max=70, hump=0.1, lookback=100,
             trail_act=0.008, trail_trail=0.01, sl_pct=0.01, to_m1=60):
    if ticker not in SPECS:
        return []
    s = SPECS[ticker]
    ms, sp = s['ms'], s['sp']
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


def metrics(all_trades):
    n = len(all_trades)
    if n == 0: return 0, 0, 0, 0, 0, 0
    wins = [p for p in all_trades if p > 0]
    losses = [p for p in all_trades if p <= 0]
    wr = len(wins)/n*100
    total = sum(all_trades)
    tp = sum(wins)
    tn = sum(abs(p) for p in losses)
    pf = tp/tn if tn else float('inf')
    cap = CAPITAL
    peak = cap
    mdd = 0
    for p in all_trades:
        cap += p
        peak = max(peak, cap)
        mdd = max(mdd, (peak - cap)/peak*100)
    ret = (cap - CAPITAL)/CAPITAL*100
    calmar = ret/mdd if mdd > 0 else 0
    return total, wr, pf, mdd, ret, calmar


if __name__ == '__main__':
    # Load bars once per ticker
    print('Loading bars...', flush=True)
    bars_cache = {}
    for t in TICKERS:
        bars_cache[t] = load_bars(t, 365)
        print(f'  {t}: {len(bars_cache[t])} bars', flush=True)

    # Grid: tick params first
    print('\n=== Grid: tick params (SL, trail_act, trail_trail) ===', flush=True)

    params = []
    for sl_pct in [0.007, 0.010, 0.015, 0.020, 0.025]:
        for trail_act in [0.005, 0.008, 0.010, 0.015, 0.020]:
            for trail_trail in [0.003, 0.005, 0.008, 0.010]:
                params.append((sl_pct, trail_act, trail_trail))

    results = []
    for i, (sl_pct, trail_act, trail_trail) in enumerate(params):
        all_trades = []
        for t in TICKERS:
            trades = backtest(t, bars_cache[t], 2, sl_pct=sl_pct, trail_act=trail_act, trail_trail=trail_trail)
            all_trades.extend(trades)
        total, wr, pf, mdd, ret, calmar = metrics(all_trades)
        if mdd > 20:  # skip if over MDD limit
            continue
        results.append((sl_pct, trail_act, trail_trail, total, pf, mdd, ret, calmar, len(all_trades)))
        print(f'[{i+1}/{len(params)}] sl={sl_pct} act={trail_act} tr={trail_trail} | pnl={total:+8.0f} pf={pf:.2f} mdd={mdd:.2f}% ret={ret:+.1f}% calmar={calmar:.1f}', flush=True)

    print(f'\n=== TOP 10 by PnL (MDD <= 20%) ===', flush=True)
    for r in sorted(results, key=lambda x: x[3], reverse=True)[:10]:
        print(f'  sl={r[0]} act={r[1]} tr={r[2]} | pnl={r[3]:+8.0f} pf={r[4]:.2f} mdd={r[5]:.2f}% ret={r[6]:+.1f}% calmar={r[7]:.1f} n={r[8]}', flush=True)

    print(f'\n=== TOP 10 by Calmar (MDD <= 20%) ===', flush=True)
    for r in sorted(results, key=lambda x: x[7], reverse=True)[:10]:
        print(f'  sl={r[0]} act={r[1]} tr={r[2]} | pnl={r[3]:+8.0f} pf={r[4]:.2f} mdd={r[5]:.2f}% ret={r[6]:+.1f}% calmar={r[7]:.1f} n={r[8]}', flush=True)
