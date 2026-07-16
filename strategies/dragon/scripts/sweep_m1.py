#!/usr/bin/env python3
"""Dragon sweep по всем M1 тикерам — M5 detect + M1 tick."""
import sys, os, argparse, json
from datetime import datetime, timezone, timedelta
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import clickhouse_connect as cc
import numpy as np

CH = dict(host='10.0.0.60', port=8123, database='moex')
TC = 4
TO_M1 = 60
TRAIL_ACT, TRAIL_TRAIL = 0.005, 0.003

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from dragon.prod.engine import check_signal

SPECS = {
    'Si': {'ms': 1.0, 'sp': 1.0},
    'GZ': {'ms': 1.0, 'sp': 1.0},
    'CR': {'ms': 0.01, 'sp': 1.0},
    'RN': {'ms': 1.0, 'sp': 1.0},
    'GD': {'ms': 0.05, 'sp': 1.0},
    'NG': {'ms': 0.001, 'sp': 7.66647},
    'BR': {'ms': 0.01, 'sp': 7.66647},
    'MM': {'ms': 0.01, 'sp': 1.0},
}


def load_bars(ticker, days=365):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    ch = cc.get_client(**CH)
    rows = ch.query(f"""
        SELECT bt, opn, hi, lo, prc
        FROM moex.mt5_bars
        WHERE ticker = '{ticker}'
          AND bt >= %(cutoff)s
        ORDER BY bt
    """, parameters={'cutoff': cutoff}).result_rows
    ch.close()
    bars = []
    for r in rows:
        ts = r[0]
        if ts.weekday() >= 5: continue
        h, m = ts.hour, ts.minute
        if h < 15 or h > 23 or (h == 23 and m > 45): continue
        bars.append({'ts': ts, 'opn': float(r[1]), 'hi': float(r[2]), 'lo': float(r[3]), 'prc': float(r[4])})
    return bars


def calc_pnl(entry, exit_, direction, ms, sp):
    return ((exit_ - entry) / ms * sp - TC) * (1 if direction == 'long' else -1)


def backtest_one(ticker, days=365):
    bars = load_bars(ticker, days)
    if len(bars) < 50:
        return []
    s = SPECS[ticker]
    ms, sp = s['ms'], s['sp']
    trades, open_pos = [], None

    for i in range(30, len(bars)):
        bar = bars[i]

        if open_pos:
            if i - open_pos['bi'] >= TO_M1:
                pnl = calc_pnl(open_pos['ep'], bar['prc'], open_pos['dir'], ms, sp)
                trades.append({'pnl': pnl, 'reason': 'timeout', 'ts': str(bar['ts'])})
                open_pos = None
            else:
                ep = open_pos['ep']
                if not open_pos.get('tr'):
                    if (open_pos['dir'] == 'long' and bar['hi'] >= ep * (1 + TRAIL_ACT)) or \
                       (open_pos['dir'] == 'short' and bar['lo'] <= ep * (1 - TRAIL_ACT)):
                        open_pos['tr'] = True
                        open_pos['tl'] = bar['hi'] * (1 - TRAIL_TRAIL) if open_pos['dir'] == 'long' else bar['lo'] * (1 + TRAIL_TRAIL)
                exit_p = None
                if open_pos.get('tr'):
                    if (open_pos['dir'] == 'long' and bar['lo'] <= open_pos['tl']) or \
                       (open_pos['dir'] == 'short' and bar['hi'] >= open_pos['tl']):
                        exit_p = open_pos['tl']
                if not exit_p:
                    sl = ep * 0.993 if open_pos['dir'] == 'long' else ep * 1.007
                    if (open_pos['dir'] == 'long' and bar['lo'] <= sl) or \
                       (open_pos['dir'] == 'short' and bar['hi'] >= sl):
                        exit_p = sl
                if exit_p:
                    pnl = calc_pnl(ep, exit_p, open_pos['dir'], ms, sp)
                    trades.append({'pnl': pnl, 'reason': 'exit', 'ts': str(bar['ts'])})
                    open_pos = None

        if i % 5 == 0 and not open_pos:
            bars_list = [{'opn': b['opn'], 'hi': b['hi'], 'lo': b['lo'], 'prc': b['prc']} for b in bars[:i+1]]
            bd = {'prc': bar['prc'], 'bars_list': bars_list}
            sig = check_signal(bd, ticker)
            if sig:
                open_pos = {'bi': i, 'ep': sig['entry_price'], 'dir': sig['direction'], 'tr': False, 'tl': None}

    if open_pos and bars:
        pnl = calc_pnl(open_pos['ep'], bars[-1]['prc'], open_pos['dir'], ms, sp)
        trades.append({'pnl': pnl, 'reason': 'eof', 'ts': str(bars[-1]['ts'])})

    return trades


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--days', type=int, default=365)
    parser.add_argument('--min-trades', type=int, default=10)
    args = parser.parse_args()

    all_tickers = sorted(SPECS.keys())
    results = {}

    for t in all_tickers:
        trades = backtest_one(t, args.days)
        n = len(trades)
        if n < args.min_trades:
            print(f"  {t:4s} n={n:4d} — пропущен (<{args.min_trades})")
            continue
        pnl = sum(t['pnl'] for t in trades)
        wins = [t for t in trades if t['pnl'] > 0]
        losses = [t for t in trades if t['pnl'] <= 0]
        wr = len(wins) / n * 100
        tp = sum(t['pnl'] for t in wins)
        tn = sum(abs(t['pnl']) for t in losses)
        pf = tp / tn if tn > 0 else float('inf')
        aw = tp / len(wins) if wins else 0
        al = tn / len(losses) if losses else 0

        results[t] = {'n': n, 'wr': round(wr, 1), 'pnl': round(pnl), 'pf': round(pf, 2), 'aw': round(aw), 'al': round(al)}
        print(f"  {t:4s} n={n:5d} wr={wr:5.1f}% pnl={pnl:+8.0f} pf={pf:.2f} aw={aw:+6.0f} al={al:+6.0f}")

    good = {t: r for t, r in results.items() if r['pf'] > 1.2 and r['n'] >= args.min_trades}
    print(f"\n=== PF>1.2 ({len(good)}/{len(results)}) ===")
    for t in sorted(good, key=lambda x: good[x]['pnl'], reverse=True):
        r = good[t]
        print(f"  {t:4s} n={r['n']:5d} wr={r['wr']:5.1f}% pnl={r['pnl']:+8.0f} pf={r['pf']:.2f}")
