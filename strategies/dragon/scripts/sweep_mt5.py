#!/usr/bin/env python3 -u
"""MT5 M1 sweep — честный, на корректных continuous данных."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from dragon.scripts.backtest_m1 import load_bars, build_m5_bars, calc_pnl, SPECS
from dragon.prod.engine import check_signal

TC = 4

MT5_TICKERS = ['BR', 'CR', 'GD', 'GZ', 'MM', 'NG', 'RN', 'Si']


def backtest_one(ticker, days=365, contracts=1,
                 impulse=0.3, retrace_max=70, hump=0.1, lookback=100,
                 trail_act=0.008, trail_trail=0.01, sl_pct=0.01, to_m1=60):
    bars = load_bars(ticker, days)
    n_bars = len(bars)
    if n_bars < 100:
        return [], n_bars
    if ticker not in SPECS:
        return [], n_bars
    s = SPECS[ticker]
    ms, sp = s['ms'], s['sp']
    dp = {'impulse_pct': impulse, 'retrace_max_pct': retrace_max,
          'hump_extension': hump, 'lookback': lookback}
    trades, open_pos = [], None
    for i in range(30, n_bars):
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
                pnl = calc_pnl(open_pos['ep'], exit_p, open_pos['dir'], ms, sp) * contracts
                trades.append(pnl)
                open_pos = None
        if i % 5 == 0 and not open_pos:
            m5 = build_m5_bars(bars, i + 1)
            if len(m5) < 6: continue
            sig = check_signal({'prc': m5[-1]['prc'], 'bars_list': m5}, ticker, dp)
            if sig:
                open_pos = {'bi': i, 'ep': sig['entry_price'], 'dir': sig['direction'],
                            'tr': False, 'tl': None}
    return trades, n_bars


def report(trades, label=''):
    n = len(trades)
    if n == 0:
        print(f'  {label}0 сделок')
        return
    wins = [t for t in trades if t > 0]
    losses = [t for t in trades if t <= 0]
    total = sum(trades)
    wr = len(wins)/n*100
    cap = 200000
    peak = cap
    mdd = 0
    for t in trades:
        cap += t
        peak = max(peak, cap)
        mdd = max(mdd, (peak - cap)/peak*100)
    tp = sum(wins)
    tn = sum(abs(t) for t in losses)
    pf = tp/tn if tn else float('inf')
    aw = tp/len(wins) if wins else 0
    al = tn/len(losses) if losses else 0
    ret = (cap - 200000)/200000*100
    print(f'  {label}n={n:4d} wr={wr:5.1f}% pnl={total:+8.0f} pf={pf:.2f} mdd={mdd:.2f}% calmar={ret/mdd if mdd>0 else 0:.1f} aw={aw:+6.0f} al={al:+6.0f}')


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--days', type=int, default=365)
    parser.add_argument('--contracts', type=int, default=2)
    args = parser.parse_args()

    print(f'\n🐉 Dragon MT5 M1 sweep — {args.days}д, ×{args.contracts}')
    print(f'  Параметры: SL=1.0% trail_act=0.8% trail_trail=1.0%')
    print(f'  Detect: M5 resampled')
    print(f'  Tick: M1 (SL на каждом баре, trailing на M5 закрытиях)')
    print(f'{"="*70}')

    all_trades = []
    for t in MT5_TICKERS:
        trades, nb = backtest_one(t, args.days, args.contracts)
        print(f'  {t:4s} ({nb:5d} bars)', end=' | ')
        report(trades, '')
        all_trades.extend(trades)

    print(f'\n{"="*70}')
    print(f'ПОРТФЕЛЬ ({len(MT5_TICKERS)} тикеров)')
    report(all_trades, '')
