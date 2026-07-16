#!/usr/bin/env python3
"""Dragon backtest на M1 — SL на M1, trailing на M5, параметры гибкие."""
import sys, os, argparse
from datetime import datetime, timezone, timedelta
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import clickhouse_connect as cc
import numpy as np

CH = dict(host='10.0.0.60', port=8123, database='moex')
TC = 4

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
    'NR': {'ms': 0.01, 'sp': 7.66647},
    'BM': {'ms': 0.01, 'sp': 7.66647},
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


def build_m5_bars(m1_bars, up_to):
    m5 = []
    for gs in range(0, up_to, 5):
        g = m1_bars[gs:min(gs+5, up_to)]
        if len(g) < 3:
            break
        m5.append({
            'opn': g[0]['opn'],
            'hi': max(b['hi'] for b in g),
            'lo': min(b['lo'] for b in g),
            'prc': g[-1]['prc'],
        })
    return m5


def calc_pnl(entry, exit_, direction, ms, sp, contracts=1):
    raw = (exit_ - entry) / ms * sp - TC
    return (raw if direction == 'long' else -raw) * contracts


def backtest(ticker, days=365, capital=200000, contracts=2,
             impulse=0.3, retrace_max=70, hump=0.1, lookback=100,
             trail_act=0.005, trail_trail=0.003, sl_pct=0.007,
             to_m1=60):
    bars = load_bars(ticker, days)
    print(f'{ticker}: {len(bars)} M1 баров', end='')
    if len(bars) < 50:
        print(' — мало данных')
        return []

    s = SPECS[ticker]
    ms, sp = s['ms'], s['sp']
    dp = {'impulse_pct': impulse, 'retrace_max_pct': retrace_max,
          'hump_extension': hump, 'lookback': lookback}

    trades, open_pos = [], None
    print(f' | imp={impulse} ret={retrace_max} hump={hump} tr_act={trail_act} tr_tr={trail_trail} sl={sl_pct}')

    for i in range(30, len(bars)):
        bar = bars[i]

        # ── Tick (M1) ──
        if open_pos:
            ep = open_pos['ep']
            exit_p = None
            reason = None

            # SL check EVERY M1 bar
            sl = ep * (1 - sl_pct) if open_pos['dir'] == 'long' else ep * (1 + sl_pct)
            if (open_pos['dir'] == 'long' and bar['lo'] <= sl) or \
               (open_pos['dir'] == 'short' and bar['hi'] >= sl):
                exit_p = sl
                reason = 'sl'

            # Trailing check ONLY on M5 bar closes (i % 5 == 4)
            if not exit_p and i % 5 == 4:
                if not open_pos.get('tr'):
                    if (open_pos['dir'] == 'long' and bar['hi'] >= ep * (1 + trail_act)) or \
                       (open_pos['dir'] == 'short' and bar['lo'] <= ep * (1 - trail_act)):
                        open_pos['tr'] = True
                        open_pos['tl'] = bar['hi'] * (1 - trail_trail) if open_pos['dir'] == 'long' else bar['lo'] * (1 + trail_trail)
                if open_pos.get('tr'):
                    if (open_pos['dir'] == 'long' and bar['lo'] <= open_pos['tl']) or \
                       (open_pos['dir'] == 'short' and bar['hi'] >= open_pos['tl']):
                        exit_p = open_pos['tl']
                        reason = 'trail'

            # Timeout
            if not exit_p and i - open_pos['bi'] >= to_m1:
                exit_p = bar['prc']
                reason = 'timeout'

            if exit_p is not None:
                pnl = calc_pnl(ep, exit_p, open_pos['dir'], ms, sp, contracts)
                trades.append({'pnl': pnl, 'reason': reason, 'ts': str(bar['ts'])})
                open_pos = None

        # ── Detect on M5 (resampled) ──
        if i % 5 == 0 and not open_pos:
            m5 = build_m5_bars(bars, i + 1)
            if len(m5) < 6:
                continue
            bd = {'prc': m5[-1]['prc'], 'bars_list': m5}
            sig = check_signal(bd, ticker, dp)
            if sig:
                open_pos = {'bi': i, 'ep': sig['entry_price'], 'dir': sig['direction'],
                            'tr': False, 'tl': None}

    if open_pos and bars:
        pnl = calc_pnl(open_pos['ep'], bars[-1]['prc'], open_pos['dir'], ms, sp, contracts)
        trades.append({'pnl': pnl, 'reason': 'eof'})

    return trades


def report(trades, label=''):
    if not trades:
        print(f'  {label}Нет сделок')
        return
    wins = [t for t in trades if t['pnl'] > 0]
    losses = [t for t in trades if t['pnl'] <= 0]
    total = sum(t['pnl'] for t in trades)
    n = len(trades)
    wr = len(wins) / n * 100 if n else 0
    cap = 200000
    peak = cap
    mdd = 0
    for t in trades:
        cap += t['pnl']
        peak = max(peak, cap)
        mdd = max(mdd, (peak - cap) / peak * 100)
    tp = sum(t['pnl'] for t in wins) if wins else 0
    tn = sum(abs(t['pnl']) for t in losses) if losses else 0
    pf = tp / tn if tn > 0 else float('inf')
    aw = tp / len(wins) if wins else 0
    al = tn / len(losses) if losses else 0
    ret = (cap - 200000) / 200000 * 100
    print(f'  {label}n={n:4d} wr={wr:5.1f}% pnl={total:+8.0f} pf={pf:.2f} mdd={mdd:.2f}% calmar={ret/mdd if mdd>0 else 0:.1f} aw={aw:+.0f} al={al:+.0f}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--tickers', type=str, default='NG')
    parser.add_argument('--days', type=int, default=365)
    parser.add_argument('--contracts', type=int, default=2)
    parser.add_argument('--impulse', type=float, default=0.3)
    parser.add_argument('--retrace', type=int, default=70)
    parser.add_argument('--hump', type=float, default=0.1)
    parser.add_argument('--lookback', type=int, default=100)
    parser.add_argument('--trail-act', type=float, default=0.005)
    parser.add_argument('--trail-trail', type=float, default=0.003)
    parser.add_argument('--sl-pct', type=float, default=0.007)
    parser.add_argument('--timeout', type=int, default=60)
    args = parser.parse_args()

    tickers = [s.strip() for s in args.tickers.split(',')]
    all_trades = []
    for t in tickers:
        trades = backtest(t, args.days, 200000, args.contracts,
                          args.impulse, args.retrace, args.hump, args.lookback,
                          args.trail_act, args.trail_trail, args.sl_pct, args.timeout)
        report(trades, f'  {t:4s} ×{args.contracts} | ')
        all_trades.extend(trades)

    if len(tickers) > 1:
        print(f'\n=== ПОРТФЕЛЬ ===')
        report(all_trades)
