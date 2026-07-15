#!/usr/bin/env python3
"""Dragon backtest на M1 барах из moex.mt5_bars — detect на M5, tick на M1."""
import sys, os, argparse
from datetime import datetime, timezone, timedelta
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import clickhouse_connect as cc
import numpy as np

CH = dict(host='10.0.0.60', port=8123, database='moex')
TC = 4          # trade cost per contract
TO_M5 = 12      # timeout in M5 bars = 60 min
TO_M1 = 60      # timeout in M1 bars
TRAIL_ACT, TRAIL_TRAIL = 0.005, 0.003

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from dragon.prod.engine import check_signal

SPECS = {
    'Si': {'ms': 1.0, 'sp': 1.0, 'a': 'Si'},
    'GZ': {'ms': 1.0, 'sp': 1.0, 'a': 'GAZR'},
    'CR': {'ms': 0.01, 'sp': 1.0, 'a': 'CNY'},
    'RN': {'ms': 1.0, 'sp': 1.0, 'a': 'ROSN'},
    'GD': {'ms': 0.05, 'sp': 1.0, 'a': 'GOLD'},
    'NG': {'ms': 0.001, 'sp': 7.66647, 'a': 'NG'},
    'BR': {'ms': 0.01, 'sp': 7.66647, 'a': 'BR'},
    'MM': {'ms': 0.01, 'sp': 1.0, 'a': 'MM'},
    'NR': {'ms': 0.01, 'sp': 7.66647, 'a': 'NR'},
    'BM': {'ms': 0.01, 'sp': 7.66647, 'a': 'BM'},
}


def load_bars(ticker, days=365):
    """Load M1 bars from moex.mt5_bars, filter MOEX hours + weekdays."""
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
        if h < 7 or (h == 15 and m > 45) or h > 15: continue
        bars.append({'ts': ts, 'opn': float(r[1]), 'hi': float(r[2]), 'lo': float(r[3]), 'prc': float(r[4])})
    return bars


def calc_pnl(entry, exit_, direction, ms, sp):
    return ((exit_ - entry) / ms * sp - TC) * (1 if direction == 'long' else -1)


def backtest(ticker, params=None, capital=200000):
    """Run dragon backtest on M1 bars with M5 detect + M1 tick."""
    bars = load_bars(ticker, days=365)
    print(f'{ticker}: {len(bars)} M1 баров')

    s = SPECS[ticker]
    ms, sp = s['ms'], s['sp']

    trades, open_pos = [], None
    DETECT_INTERVAL = 5  # detect every 5 M1 bars (= M5)

    for i in range(30, len(bars)):
        bar = bars[i]

        # ── Tick (M1): manage positions ──
        if open_pos:
            if i - open_pos['bi'] >= TO_M1:
                pnl = calc_pnl(open_pos['ep'], bar['prc'], open_pos['dir'], ms, sp) * 2
                trades.append({'ts': bar['ts'], 'ticker': ticker, 'pnl': pnl, 'reason': 'timeout'})
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
                    pnl = calc_pnl(ep, exit_p, open_pos['dir'], ms, sp) * 2
                    trades.append({'ts': bar['ts'], 'ticker': ticker, 'pnl': pnl, 'reason': 'exit'})
                    open_pos = None

        # ── Detect (M5): check signals every 5th bar ──
        if i % DETECT_INTERVAL == 0 and not open_pos:
            # Build bars_list up to current bar for dragon
            bars_list = [{'opn': b['opn'], 'hi': b['hi'], 'lo': b['lo'], 'prc': b['prc']} for b in bars[:i+1]]
            bd = {'prc': bar['prc'], 'bars_list': bars_list}
            sig = check_signal(bd, ticker)
            if sig:
                open_pos = {'bi': i, 'ep': sig['entry_price'], 'dir': sig['direction'], 'tr': False, 'tl': None, 'ts': bar['ts']}

    if open_pos and bars:
        pnl = calc_pnl(open_pos['ep'], bars[-1]['prc'], open_pos['dir'], ms, sp) * 2
        trades.append({'ts': bars[-1]['ts'], 'ticker': ticker, 'pnl': pnl, 'reason': 'eof'})

    return trades


def report(trades, capital=200000):
    if not trades:
        print('  Нет сделок')
        return
    wins = [t for t in trades if t['pnl'] > 0]
    losses = [t for t in trades if t['pnl'] <= 0]
    total = sum(t['pnl'] for t in trades)
    wr = len(wins) / len(trades) * 100

    # MDD
    cap = capital
    peak = cap
    mdd = 0
    for t in trades:
        cap += t['pnl']
        peak = max(peak, cap)
        mdd = max(mdd, (peak - cap) / peak * 100)

    aw = sum(t['pnl'] for t in wins) / len(wins) if wins else 0
    al = sum(t['pnl'] for t in losses) / len(losses) if losses else 0
    tp = sum(t['pnl'] for t in wins)
    tn = sum(abs(t['pnl']) for t in losses)
    pf = tp / tn if tn > 0 else float('inf')

    reasons = {}
    for t in trades:
        r = t.get('reason', '?')
        reasons[r] = reasons.get(r, 0) + 1

    print(f'  Сделок: {len(trades)} | WR: {wr:.1f}% | PnL: {total:+.0f}₽')
    print(f'  MDD: {mdd:.2f}% | PF: {pf:.2f} | AvgWin: {aw:.0f} | AvgLoss: {al:.0f}')
    print(f'  Причины: {reasons}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--tickers', type=str, default='NG,MM,GZ')
    args = parser.parse_args()

    tickers = [s.strip() for s in args.tickers.split(',')]
    all_trades = []
    for t in tickers:
        trades = backtest(t)
        report(trades)
        all_trades.extend(trades)

    print(f'\n=== ПОРТФЕЛЬ ({len(tickers)} тикеров) ===')
    report(all_trades)
