#!/usr/bin/env python3
"""
Portfolio v4 — Chandelier + Partial + Score sizing.
Baseline: RI/GL/NM vol_up_oi_up_yb_up + USDRUBF vol_up_yb_down_fiz_up hold=5 sl=1%.
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/../..')
os.chdir(os.path.dirname(os.path.abspath(__file__)) + '/../..')

import numpy as np
from datetime import datetime
from collections import defaultdict

from reports.triz_diamond_v4.diamond_search_v4 import (
    load_daily_wfcbr, backtest_signal_v4, PATTERNS, CS_MAP, GO_MAP
)

COMM = 4; RISK_PCT = 0.02; MAX_LOT = 5; MAX_LEV = 5.0

TOP_SIGNALS = [
    ('RI', 'vol_up_oi_up_yb_up', 5, 0.01, 0),
    ('GL', 'vol_up_oi_up_yb_up', 5, 0.01, 0),
    ('USDRUBF', 'vol_up_yb_down_fiz_up', 5, 0.01, 0),
    ('NM', 'vol_up_oi_up_yb_up', 5, 0.01, 0),
]

CACHE = {}

def get_data(ticker):
    if ticker not in CACHE:
        CACHE[ticker] = load_daily_wfcbr(ticker)
    return CACHE[ticker]

def run_signal(sig, capital, use_chandelier=False, use_partial_exit=False,
               atr_mult=3.0, min_stop=0.01, max_loss=0.05,
               partial_atr_mult=0.5):
    ticker, pname, hold, sl_pct, dv_thr = sig
    data = get_data(ticker)
    if data is None:
        return None, None
    cs = CS_MAP.get(ticker, 1)
    pfunc = PATTERNS[pname]
    r = backtest_signal_v4(data, pfunc, hold, sl_pct, cs, ticker,
                           use_cbr_filter=True,
                           dv_threshold=dv_thr,
                           use_chandelier=use_chandelier,
                           use_partial_exit=use_partial_exit,
                           atr_mult=atr_mult,
                           min_stop=min_stop, max_loss=max_loss,
                           partial_atr_mult=partial_atr_mult,
                           capital=capital)
    return r, ticker

def run_portfolio(signals, total_capital, use_chandelier=False,
                  use_partial_exit=False, use_score_sizing=False,
                  atr_mult=3.0, min_stop=0.01, max_loss=0.05,
                  partial_atr_mult=0.5):
    signal_results = []

    if use_score_sizing:
        calmar_scores = {}
        for sig in signals:
            r, tk = run_signal(sig, 100_000, use_chandelier, use_partial_exit,
                               atr_mult=atr_mult,
                               partial_atr_mult=partial_atr_mult)
            if r and r['trades'] >= 5:
                calmar_scores[tk] = max(r['calmar'], 0.1)
            else:
                calmar_scores[tk] = 0.1
        total_score = sum(calmar_scores.values())
        weights = {tk: s/total_score for tk, s in calmar_scores.items()}
        for sig in signals:
            tk = sig[0]
            w = weights.get(tk, 1.0/len(signals))
            sig_cap = total_capital * w
            r, _ = run_signal(sig, sig_cap, use_chandelier, use_partial_exit,
                              atr_mult=atr_mult, min_stop=min_stop, max_loss=max_loss,
                              partial_atr_mult=partial_atr_mult)
            if r:
                signal_results.append((tk, r, sig_cap, w))
    else:
        sig_cap = total_capital / len(signals)
        for sig in signals:
            r, tk = run_signal(sig, sig_cap, use_chandelier, use_partial_exit,
                               atr_mult=atr_mult, min_stop=min_stop, max_loss=max_loss,
                               partial_atr_mult=partial_atr_mult)
            if r:
                signal_results.append((tk, r, sig_cap, 1.0/len(signals)))

    if not signal_results:
        return None

    all_trades = []
    for tk, r, cap, w in signal_results:
        trades = r.get('trade_list', [])
        for t in trades:
            t['ticker'] = tk
            t['weight'] = round(w, 3)
        all_trades.extend(trades)

    all_trades.sort(key=lambda t: t['entry'])

    equity = total_capital; peak = equity; mdd = 0.0
    for t in all_trades:
        equity += t['npnl']
        if equity > peak: peak = equity
        dd = (peak - equity) / peak * 100 if peak > 0 else 0
        mdd = max(mdd, dd)

    total_ret = (equity - total_capital) / total_capital * 100
    wins = sum(1 for t in all_trades if t['npnl'] > 0)
    wr = wins / len(all_trades) * 100 if all_trades else 0
    gp_sum = sum(t['npnl'] for t in all_trades if t['npnl'] > 0)
    gl_sum = sum(t['npnl'] for t in all_trades if t['npnl'] < 0)
    pf = abs(gp_sum / (gl_sum + 1))
    calmar = total_ret / mdd if mdd > 0 else 0

    days = 0
    ann = 0
    if all_trades:
        dates_list = [t['entry'] for t in all_trades] + [t['exit'] for t in all_trades]
        days = (datetime.strptime(max(dates_list), '%Y-%m-%d') -
                datetime.strptime(min(dates_list), '%Y-%m-%d')).days
        ann = ((1+total_ret/100)**(365/max(days,1))-1)*100 if days>0 else 0

    by_t = defaultdict(lambda: {'t':0, 'w':0, 'p':0})
    for t in all_trades:
        by_t[t['ticker']]['t'] += 1
        by_t[t['ticker']]['w'] += 1 if t['npnl']>0 else 0
        by_t[t['ticker']]['p'] += t['npnl']

    return dict(ret=round(total_ret,2), mdd=round(mdd,2), wr=round(wr,1),
                pf=round(pf,2), calmar=round(calmar,2), ann=round(ann,2),
                trades=len(all_trades), wins=wins, days=days,
                by_ticker=dict(by_t))


if __name__ == '__main__':
    capital = 100_000
    configs = [
        ('Baseline', False, False, False),
        ('Chandelier', True, False, False),
        ('Chandelier+Partial', True, True, False),
        ('ScoreSizing', True, True, True),
    ]
    results = {}
    for name, use_ch, use_pe, use_ss in configs:
        print(f"\n{'='*60}")
        print(f"PORTFOLIO: {name}, Capital={capital:,}")
        print(f"{'='*60}")
        r = run_portfolio(TOP_SIGNALS, capital, use_ch, use_pe, use_ss)
        if r:
            print(f"  Ret={r['ret']:+.1f}% DD={r['mdd']:.1f}% "
                  f"Calmar={r['calmar']:.1f} WR={r['wr']:.0f}% "
                  f"PF={r['pf']:.2f} Ann={r['ann']:+.1f}% "
                  f"Tr={r['trades']} Days={r['days']}")
            for tk, v in sorted(r['by_ticker'].items(), key=lambda x: -x[1]['p']):
                print(f"    {tk}: {v['t']}tr WR={v['w']/v['t']*100:.0f}% PnL={v['p']:>+8,.0f}")
            results[name] = r
        else:
            print(f"  No trades")
            results[name] = None

    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    print(f"{'Config':<22} {'Ret':>8} {'DD':>6} {'Calmar':>8} {'WR':>5} {'PF':>6} {'Ann':>8} {'Tr':>5}")
    print("-"*70)
    for name in [c[0] for c in configs]:
        r = results.get(name)
        if r:
            print(f"{name:<22} {r['ret']:>+7.1f}% {r['mdd']:>5.1f}% "
                  f"{r['calmar']:>7.1f} {r['wr']:>4.0f}% {r['pf']:>5.2f} "
                  f"{r['ann']:>+7.1f}% {r['trades']:>5d}")
        else:
            print(f"{name:<22} {'—':>8}")

    os.makedirs('reports/triz_diamond_v4', exist_ok=True)
    with open('reports/triz_diamond_v4/portfolio_results.json', 'w') as f:
        json.dump(results, f, indent=2, default=str)
    print("\nSaved to reports/triz_diamond_v4/portfolio_results.json")
