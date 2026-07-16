#!/usr/bin/env python3 -u
"""Fast grid search for dragon M1 params — bars loaded once."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from dragon.scripts.backtest_m1 import load_bars, build_m5_bars, SPECS
from dragon.prod.engine import check_signal

TC = 4


def backtest_fast(ticker, bars, contracts=2,
                  impulse=0.3, retrace_max=70, hump=0.1, lookback=100,
                  trail_act=0.005, trail_trail=0.003, sl_pct=0.007, to_m1=60):
    if ticker not in SPECS:
        return []
    s = SPECS[ticker]
    ms, sp = s['ms'], s['sp']
    dp = {'impulse_pct': impulse, 'retrace_max_pct': retrace_max,
          'hump_extension': hump, 'lookback': lookback}
    trades, open_pos = [], None
    for i in range(30, len(bars)):
        bar = bars[i]
        if open_pos:
            ep = open_pos['ep']
            exit_p = None
            reason = None
            sl = ep * (1 - sl_pct) if open_pos['dir'] == 'long' else ep * (1 + sl_pct)
            if (open_pos['dir'] == 'long' and bar['lo'] <= sl) or \
               (open_pos['dir'] == 'short' and bar['hi'] >= sl):
                exit_p = sl; reason = 'sl'
            if not exit_p and i % 5 == 4:
                if not open_pos.get('tr'):
                    if (open_pos['dir'] == 'long' and bar['hi'] >= ep * (1 + trail_act)) or \
                       (open_pos['dir'] == 'short' and bar['lo'] <= ep * (1 - trail_act)):
                        open_pos['tr'] = True
                        open_pos['tl'] = bar['hi'] * (1 - trail_trail) if open_pos['dir'] == 'long' else bar['lo'] * (1 + trail_trail)
                if open_pos.get('tr'):
                    if (open_pos['dir'] == 'long' and bar['lo'] <= open_pos['tl']) or \
                       (open_pos['dir'] == 'short' and bar['hi'] >= open_pos['tl']):
                        exit_p = open_pos['tl']; reason = 'trail'
            if not exit_p and i - open_pos['bi'] >= to_m1:
                exit_p = bar['prc']; reason = 'timeout'
            if exit_p is not None:
                raw = (exit_p - ep) / ms * sp - TC
                pnl = (raw if open_pos['dir'] == 'long' else -raw) * contracts
                trades.append(pnl)
                open_pos = None
        if i % 5 == 0 and not open_pos:
            m5 = build_m5_bars(bars, i + 1)
            if len(m5) < 6: continue
            sig = check_signal({'prc': m5[-1]['prc'], 'bars_list': m5}, ticker, dp)
            if sig:
                open_pos = {'bi': i, 'ep': sig['entry_price'], 'dir': sig['direction'], 'tr': False, 'tl': None}
    if open_pos and bars:
        raw = (bars[-1]['prc'] - open_pos['ep']) / ms * sp - TC
        pnl = (raw if open_pos['dir'] == 'long' else -raw) * contracts
        trades.append(pnl)
    return trades


def metrics(pnls):
    n = len(pnls)
    if n == 0: return 0, 0, 0, 0, 0, 0
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    wr = len(wins)/n*100
    total = sum(pnls)
    tp = sum(wins)
    tn = sum(abs(p) for p in losses)
    pf = tp/tn if tn else float('inf')
    cap = 200000
    peak = cap
    mdd = 0
    for p in pnls:
        cap += p
        peak = max(peak, cap)
        mdd = max(mdd, (peak - cap)/peak*100)
    return total, wr, pf, mdd, len(wins), len(losses)


if __name__ == '__main__':
    ticker = 'BR'
    bars = load_bars(ticker, 365)
    print(f'{ticker}: {len(bars)} bars loaded', flush=True)

    params = []
    for sl_pct in [0.007, 0.010, 0.015, 0.020, 0.030]:
        for trail_act in [0.005, 0.008, 0.010, 0.015, 0.020]:
            for trail_trail in [0.003, 0.005, 0.008, 0.010]:
                params.append((sl_pct, trail_act, trail_trail))

    results = []
    for i, (sl_pct, trail_act, trail_trail) in enumerate(params):
        pnls = backtest_fast('BR', bars, 2, sl_pct=sl_pct, trail_act=trail_act, trail_trail=trail_trail)
        total, wr, pf, mdd, nw, nl = metrics(pnls)
        calmar = (total/200000*100) / mdd if mdd > 0 else 0
        results.append((sl_pct, trail_act, trail_trail, total, pf, mdd, calmar, len(pnls), wr))
        print(f'[{i+1}/{len(params)}] sl={sl_pct} act={trail_act} tr={trail_trail} | n={len(pnls):4d} wr={wr:5.1f}% pnl={total:+8.0f} pf={pf:.2f} mdd={mdd:.2f}% calmar={calmar:.1f}', flush=True)

    print('\n=== TOP 10 by PnL ===', flush=True)
    for r in sorted(results, key=lambda x: x[3], reverse=True)[:10]:
        print(f'  sl={r[0]} act={r[1]} tr={r[2]} | n={r[7]:4d} wr={r[8]:5.1f}% pnl={r[3]:+8.0f} pf={r[4]:.2f} mdd={r[5]:.2f}% calmar={r[6]:.1f}', flush=True)

    print('\n=== TOP 10 by Calmar ===', flush=True)
    for r in sorted(results, key=lambda x: x[6], reverse=True)[:10]:
        print(f'  sl={r[0]} act={r[1]} tr={r[2]} | n={r[7]:4d} wr={r[8]:5.1f}% pnl={r[3]:+8.0f} pf={r[4]:.2f} mdd={r[5]:.2f}% calmar={r[6]:.1f}', flush=True)

    print('\n=== TOP 10 by PF ===', flush=True)
    for r in sorted(results, key=lambda x: x[4], reverse=True)[:10]:
        print(f'  sl={r[0]} act={r[1]} tr={r[2]} | n={r[7]:4d} wr={r[8]:5.1f}% pnl={r[3]:+8.0f} pf={r[4]:.2f} mdd={r[5]:.2f}% calmar={r[6]:.1f}', flush=True)
