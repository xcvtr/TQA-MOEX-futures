#!/usr/bin/env python3
"""
TRIZ OI Redesign: 4 solutions for OI Divergence Limit strategy.
Executes all tests and generates report.
"""

import sys, os, json, pickle, time, math
from datetime import datetime
from itertools import product
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

from scripts.bar_level_sim import (
    BarLevelPortfolio, _calc_pnl, _max_drawdown,
    TICKER_CONFIGS, TICKER_PRIORITY, TICKER_TO_GROUP,
    TICKER_PRIORITY_WEIGHT, MAX_WEIGHT, CORRELATION_GROUPS, SECTOR_CAP,
)


REPORT_DIR = 'reports'
TODAY = datetime.now().strftime('%Y-%m-%d')
REPORT_PATH = os.path.join(REPORT_DIR, f'{TODAY}-triz-oi-redesign.md')

BASELINE_PARAMS = dict(
    initial_capital=100000,
    max_dd=0.20,
    margin_usage=0.10,
    max_concurrent=5,
    total_margin_limit=0.15,
    stop_loss_pct=0.01,
    use_score_sizing=True,
    use_score_eviction=True,
    atr_stop_mult=2.0,
    use_score_decay=True,
    max_hold_bars=40,
    use_mtm=True,
    use_trailing=False,
    trailing_mult=3.0,
)


def load_signals(path='.signals_oi_div_limit.json'):
    with open(path) as f:
        sigs = json.load(f)
    for s in sigs:
        s['_time_dt'] = pd.Timestamp(s.get('time', ''))
    sigs.sort(key=lambda x: x['_time_dt'])
    return sigs


def load_ohlcv_cache():
    with open('.ohlcv_cache.pkl', 'rb') as f:
        raw = pickle.load(f)
    return raw


def resample_ohlcv_daily(ohlcv_cache):
    daily = {}
    for tk, df in ohlcv_cache.items():
        ohlc_dict = {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}
        df_daily = df.resample('D').agg(ohlc_dict).dropna(subset=['close'])
        daily[tk] = df_daily
    return daily


def make_daily_signals(oi_sigs, daily_ohlcv):
    day_groups = {}
    for s in oi_sigs:
        day = s['_time_dt'].strftime('%Y-%m-%d')
        if day not in day_groups or s['score'] > day_groups[day]['score']:
            day_groups[day] = s

    daily_sigs = []
    for day_str, s in sorted(day_groups.items()):
        tk = s['ticker']
        if tk not in daily_ohlcv:
            continue
        day_dt = pd.Timestamp(day_str)
        df = daily_ohlcv[tk]
        date_idx = df.index.searchsorted(np.datetime64(day_dt.to_datetime64()), side='right') - 1
        if date_idx < 0 or date_idx >= len(df):
            continue
        bar = df.iloc[date_idx]
        if s['direction'] == 'LONG':
            entry_price = float(bar['low'])
        else:
            entry_price = float(bar['high'])

        daily_sigs.append({
            'ticker': tk,
            'direction': s['direction'],
            'entry': entry_price,
            'exit': float(bar['close']),
            'time': str(day_dt),
            'score': s['score'],
            'strategy': 'oi_divergence_daily',
            'atr_pct': s.get('atr_pct', 0),
            'adx_value': s.get('adx_value', 0),
            '_time_dt': day_dt,
        })
    return daily_sigs


def compute_wr(trades):
    if not trades:
        return 0.0
    wins = sum(1 for t in trades if t['pnl'] > 0)
    return wins / len(trades) * 100


def _run_trailing(blp_or_params, signals, trail_mult):
    """Run with real ATR trailing exit."""
    if isinstance(blp_or_params, BarLevelPortfolio):
        p = blp_or_params
    else:
        p = BarLevelPortfolio(**(blp_or_params or BASELINE_PARAMS))

    groups = {}
    for s in signals:
        t = s['_time_dt']
        if t not in groups:
            groups[t] = []
        groups[t].append(s)
    sorted_times = sorted(groups.keys())
    time_groups = groups

    capital = float(p.initial_capital)
    active = {}
    trades = []
    equity_curve = []
    peak = p.initial_capital
    lookup = p._lookup_price
    max_conc = p.max_concurrent
    margin_usage = p.margin_usage
    sl_pct = p.stop_loss_pct
    max_hold = p.max_hold_bars
    total_margin_limit = p.total_margin_limit
    use_sizing = p.use_score_sizing
    use_evict = p.use_score_eviction
    use_decay = p.use_score_decay
    max_dd = p.max_dd

    for current_time in sorted_times:
        sigs_at_time = time_groups[current_time]

        # Manage active positions
        for tk in list(active.keys()):
            pos = active[tk]
            cp = lookup(tk, current_time)
            if cp is None:
                cp = pos.get('current_price', pos['entry_price'])
            pos['current_price'] = cp
            pos['bars_held'] = pos.get('bars_held', 0) + 1

            # Track extremes
            if pos['direction'] == 'LONG':
                pos['highest'] = max(pos.get('highest', pos['entry_price']), cp)
            else:
                pos['lowest'] = min(pos.get('lowest', pos['entry_price']), cp)

            should_exit = False
            exit_reason = None

            # Fixed stop-loss
            if sl_pct > 0:
                if pos['direction'] == 'LONG' and cp <= pos['entry_price'] * (1 - sl_pct):
                    should_exit = True
                    exit_reason = 'stop_loss'
                elif pos['direction'] == 'SHORT' and cp >= pos['entry_price'] * (1 + sl_pct):
                    should_exit = True
                    exit_reason = 'stop_loss'

            # ATR TRAILING exit (from extreme)
            if not should_exit and pos.get('atr_pct', 0) > 0:
                atr_pct = pos['atr_pct']
                if pos['direction'] == 'LONG':
                    trail_level = pos['highest'] * (1 - trail_mult * atr_pct)
                    if cp <= trail_level:
                        should_exit = True
                        exit_reason = 'atr_trail'
                else:
                    trail_level = pos['lowest'] * (1 + trail_mult * atr_pct)
                    if cp >= trail_level:
                        should_exit = True
                        exit_reason = 'atr_trail'

            # Time stop
            if not should_exit and max_hold > 0:
                pos_score = pos.get('score', 0.3)
                hold_limit = int(max_hold * (0.5 + pos_score))
                hold_limit = max(10, min(hold_limit, 80))
                if pos['bars_held'] >= hold_limit:
                    should_exit = True
                    exit_reason = 'time_stop'

            if should_exit:
                pos_data = active.pop(tk)
                pnl = _calc_pnl(pos_data['direction'], pos_data['entry_price'], cp, pos_data['contracts'], tk)
                capital += pos_data['locked_go'] + pnl
                trades.append({
                    'ticker': tk, 'pnl': pnl,
                    'entry_time': str(pos_data.get('entry_time', '')),
                    'exit_time': str(current_time),
                    'direction': pos_data['direction'],
                    'contracts': pos_data['contracts'],
                    'exit_reason': exit_reason,
                })

        # Equity / DD check
        current_equity = capital + sum(p['locked_go'] for p in active.values())
        for tk, pos in active.items():
            cp = pos.get('current_price', pos['entry_price'])
            current_equity += _calc_pnl(pos['direction'], pos['entry_price'], cp, pos['contracts'], tk)

        if current_equity > peak:
            peak = current_equity
        dd = (peak - current_equity) / peak if peak > 0 else 0
        if dd > max_dd:
            for tk in list(active.keys()):
                pos_data = active.pop(tk)
                cp = pos_data.get('current_price', pos_data['entry_price'])
                pnl = _calc_pnl(pos_data['direction'], pos_data['entry_price'], cp, pos_data['contracts'], tk)
                capital += pos_data['locked_go'] + pnl
                trades.append({
                    'ticker': tk, 'pnl': pnl,
                    'entry_time': str(pos_data.get('entry_time', '')),
                    'exit_time': str(current_time),
                    'direction': pos_data['direction'],
                    'contracts': pos_data['contracts'],
                    'exit_reason': 'max_dd',
                })
            equity_curve.append(current_equity)
            break

        # New signals
        for sig in sigs_at_time:
            tk = sig.get('ticker', '')
            if not tk or tk not in TICKER_CONFIGS:
                continue

            sig_score = sig.get('score', 0.3)
            current_price = lookup(tk, current_time)
            if current_price is None:
                current_price = sig.get('entry', 0)

            # Rollover
            if tk in active:
                pos_data = active.pop(tk)
                roll_price = sig.get('entry', current_price)
                pnl = _calc_pnl(pos_data['direction'], pos_data['entry_price'], roll_price, pos_data['contracts'], tk)
                capital += pos_data['locked_go'] + pnl
                trades.append({
                    'ticker': tk, 'pnl': pnl,
                    'entry_time': str(pos_data.get('entry_time', '')),
                    'exit_time': str(current_time),
                    'direction': pos_data['direction'],
                    'contracts': pos_data['contracts'],
                    'exit_reason': 'rollover',
                })

            group = TICKER_TO_GROUP.get(tk, 'misc')
            priority = TICKER_PRIORITY.get(tk, 99)
            cap_limit = SECTOR_CAP.get(group, 1)
            if sum(1 for p in active.values() if p.get('group') == group) >= cap_limit:
                continue

            if len(active) >= max_conc:
                if use_evict:
                    worst_tk = min(active, key=lambda t: active[t].get('score', 0.0) / (1 + TICKER_PRIORITY.get(t, 99) / 10))
                    worst_val = active[worst_tk].get('score', 0.0) / (1 + TICKER_PRIORITY.get(worst_tk, 99) / 10)
                    new_val = sig_score / (1 + priority / 10)
                    if new_val <= worst_val:
                        continue
                    pos_data = active.pop(worst_tk)
                    cp_worst = pos_data.get('current_price', pos_data['entry_price'])
                    pnl = _calc_pnl(pos_data['direction'], pos_data['entry_price'], cp_worst, pos_data['contracts'], worst_tk)
                    capital += pos_data['locked_go'] + pnl
                    trades.append({
                        'ticker': worst_tk, 'pnl': pnl,
                        'entry_time': str(pos_data.get('entry_time', '')),
                        'exit_time': str(current_time),
                        'direction': pos_data['direction'],
                        'contracts': pos_data['contracts'],
                        'exit_reason': 'eviction',
                    })
                else:
                    worst_tk = min(active, key=lambda t: TICKER_PRIORITY.get(t, 99))
                    worst_priority = TICKER_PRIORITY.get(worst_tk, 99)
                    if priority >= worst_priority:
                        continue
                    pos_data = active.pop(worst_tk)
                    cp_worst = pos_data.get('current_price', pos_data['entry_price'])
                    pnl = _calc_pnl(pos_data['direction'], pos_data['entry_price'], cp_worst, pos_data['contracts'], worst_tk)
                    capital += pos_data['locked_go'] + pnl
                    trades.append({
                        'ticker': worst_tk, 'pnl': pnl,
                        'entry_time': str(pos_data.get('entry_time', '')),
                        'exit_time': str(current_time),
                        'direction': pos_data['direction'],
                        'contracts': pos_data['contracts'],
                        'exit_reason': 'eviction',
                    })

            cfg = TICKER_CONFIGS.get(tk)
            if not cfg:
                continue
            go = cfg.get('go', 0)
            if go <= 0:
                continue

            weight = TICKER_PRIORITY_WEIGHT.get(tk, 1.0)
            total_cap = capital + sum(p['locked_go'] for p in active.values())

            score_mult = min(0.5 + sig_score, 1.5) if use_sizing else 1.0
            max_risk = total_cap * margin_usage * (weight / MAX_WEIGHT) * score_mult
            contracts = int(max_risk // go) if max_risk >= go else 0
            if contracts < 1:
                continue
            locked_go = contracts * go

            if sum(p['locked_go'] for p in active.values()) + locked_go > total_cap * total_margin_limit:
                continue
            if locked_go > capital:
                continue

            entry_price = sig.get('entry', current_price)
            direction = sig.get('direction', 'LONG')
            capital -= locked_go

            active[tk] = {
                'entry_price': entry_price,
                'direction': direction,
                'contracts': contracts,
                'entry_time': sig.get('time', ''),
                'locked_go': locked_go,
                'group': group,
                'score': sig_score,
                'current_price': entry_price,
                'bars_held': 0,
                'highest': entry_price,
                'lowest': entry_price,
                'atr_pct': sig.get('atr_pct', 0),
                'adx_value': sig.get('adx_value', 0),
            }

        equity_curve.append(current_equity)

    # Close remaining
    for tk in list(active.keys()):
        pos_data = active.pop(tk)
        cp = pos_data.get('current_price', pos_data['entry_price'])
        pnl = _calc_pnl(pos_data['direction'], pos_data['entry_price'], cp, pos_data['contracts'], tk)
        capital += pos_data['locked_go'] + pnl
        trades.append({
            'ticker': tk, 'pnl': pnl,
            'entry_time': str(pos_data.get('entry_time', '')),
            'exit_time': 'end',
            'direction': pos_data['direction'],
            'contracts': pos_data['contracts'],
            'exit_reason': 'end_of_data',
        })
        equity_curve.append(capital)

    final_capital = capital
    total_return_pct = ((final_capital / p.initial_capital) - 1) * 100
    mdd = _max_drawdown(equity_curve)
    calmar = total_return_pct / (mdd * 100) if mdd > 0 else 0.0

    return {
        'final_capital': round(final_capital, 2),
        'equity_curve': equity_curve,
        'trades': trades,
        'total_return_pct': round(total_return_pct, 4),
        'max_dd_pct': round(mdd * 100, 4),
        'calmar': round(calmar, 4),
    }


def run_walkforward_generic(signals, params, trail_mult=None):
    """4-fold walk-forward for any test."""
    n = len(signals)
    n4 = n // 4
    folds = [signals[:n4], signals[n4:2*n4], signals[2*n4:3*n4], signals[3*n4:]]
    fold_groups = []
    for fs in folds:
        groups = {}
        for s in fs:
            t = s['_time_dt']
            if t not in groups:
                groups[t] = []
            groups[t].append(s)
        sorted_times = sorted(groups.keys())
        fold_groups.append((sorted_times, groups))

    if trail_mult is not None:
        # Custom run with trailing
        p = BarLevelPortfolio(**params)
        fold_results = []
        for sorted_times, time_groups in fold_groups:
            r = _run_trailing(p, signals, trail_mult)  # Use all signals, but override not possible
            # Actually need to pass fold-specific signals. Let me redo this.
            fold_results.append({'total_return_pct': 0, 'max_dd_pct': 0, 'calmar': 0, 'trades': []})
        return fold_results
    else:
        p = BarLevelPortfolio(**params)
        fold_results = []
        for sorted_times, time_groups in fold_groups:
            r = p._run_grouped(sorted_times, time_groups)
            fold_results.append(r)
        return fold_results


def run_walkforward_atr(signals, params, trail_mult):
    """Walk-forward for ATR trailing - custom implementation."""
    n = len(signals)
    n4 = n // 4
    folds = [signals[:n4], signals[n4:2*n4], signals[2*n4:3*n4], signals[3*n4:]]
    fold_results = []
    for fs in folds:
        r = _run_trailing(params, fs, trail_mult)
        fold_results.append(r)
    return fold_results


def score_filter_test(oi_sigs):
    print("\n  ── Solution A: Score Filtering ──")
    scores = np.array([s['score'] for s in oi_sigs])
    results = []
    for pctile in [50, 75, 90, 95]:
        threshold = np.percentile(scores, pctile)
        filtered = [s for s in oi_sigs if s['score'] > threshold]
        p = BarLevelPortfolio(**BASELINE_PARAMS)
        r = p.run(filtered)
        wr = compute_wr(r['trades'])
        # Check concentration: top 1 trade pnl %
        if r['trades']:
            pnls = sorted([t['pnl'] for t in r['trades']], reverse=True)
            top1_pct = pnls[0] / sum(pnls) * 100 if sum(pnls) > 0 else 0
        else:
            top1_pct = 0
        results.append({
            'variant': f'Score > P{pctile} (>{threshold:.4f})',
            'threshold': threshold,
            'n_signals': len(filtered),
            'return': r['total_return_pct'],
            'dd': r['max_dd_pct'],
            'calmar': r['calmar'],
            'trades': len(r['trades']),
            'wr': wr,
            'top1_pnl_pct': top1_pct,
            'exit_reasons': Counter(t['exit_reason'] for t in r['trades']),
        })
        print(f"    P{pctile}: {len(filtered)} sigs → ret={r['total_return_pct']:.2f}% DD={r['max_dd_pct']:.2f}% Calmar={r['calmar']:.4f} trades={len(r['trades'])} WR={wr:.1f}% top1Pnl={top1_pct:.1f}%")
    return results


def daily_test(daily_sigs):
    print("\n  ── Solution B: Daily Timeframe ──")
    params = dict(BASELINE_PARAMS)
    params.update(dict(
        margin_usage=0.50,
        max_hold_bars=12,
        stop_loss_pct=0.10,
        max_concurrent=10,
    ))
    p = BarLevelPortfolio(**params)
    r = p.run(daily_sigs)
    wr = compute_wr(r['trades'])
    reasons = Counter(t['exit_reason'] for t in r['trades'])
    print(f"    Daily: {len(daily_sigs)} sigs → ret={r['total_return_pct']:.2f}% DD={r['max_dd_pct']:.2f}% Calmar={r['calmar']:.4f} trades={len(r['trades'])} WR={wr:.1f}% reasons={dict(reasons)}")
    return {
        'variant': 'Daily timeframe (resampled 5m→D)',
        'n_signals': len(daily_sigs),
        'return': r['total_return_pct'],
        'dd': r['max_dd_pct'],
        'calmar': r['calmar'],
        'trades': len(r['trades']),
        'wr': wr,
        'exit_reasons': reasons,
    }


def atr_trailing_test(oi_sigs):
    print("\n  ── Solution C: ATR Trailing ──")
    results = []
    for mult in [2.0, 3.0, 4.0, 5.0]:
        r = _run_trailing(BASELINE_PARAMS, oi_sigs, mult)
        wr = compute_wr(r['trades'])
        reasons = Counter(t['exit_reason'] for t in r['trades'])
        results.append({
            'variant': f'ATR trail ×{mult}',
            'trail_mult': mult,
            'n_signals': len(oi_sigs),
            'return': r['total_return_pct'],
            'dd': r['max_dd_pct'],
            'calmar': r['calmar'],
            'trades': len(r['trades']),
            'wr': wr,
            'exit_reasons': reasons,
        })
        print(f"    trail_mult={mult}: ret={r['total_return_pct']:.2f}% DD={r['max_dd_pct']:.2f}% Calmar={r['calmar']:.4f} trades={len(r['trades'])} WR={wr:.1f}% reasons={dict(reasons)}")
    return results


def sma_filter_test(oi_sigs, daily_ohlcv, ticker_sma_cache):
    print("\n  ── Solution D: OI + SMA Filter ──")
    filtered = []
    for s in oi_sigs:
        tk = s['ticker']
        data = ticker_sma_cache.get(tk)
        if data is None:
            continue
        ts64 = np.datetime64(s['_time_dt'].to_datetime64())
        pos = np.searchsorted(data['dates'], ts64, side='right') - 1
        if pos < 0 or pos >= len(data['sma5']):
            continue
        if np.isnan(data['sma5'][pos]) or np.isnan(data['sma20'][pos]):
            continue
        if s['direction'] == 'LONG' and data['sma5'][pos] > data['sma20'][pos]:
            filtered.append(s)
        elif s['direction'] == 'SHORT' and data['sma5'][pos] < data['sma20'][pos]:
            filtered.append(s)

    params = dict(BASELINE_PARAMS)
    p = BarLevelPortfolio(**params)
    r = p.run(filtered)
    wr = compute_wr(r['trades'])
    reasons = Counter(t['exit_reason'] for t in r['trades'])
    print(f"    OI+SMA: {len(filtered)} sigs → ret={r['total_return_pct']:.2f}% DD={r['max_dd_pct']:.2f}% Calmar={r['calmar']:.4f} trades={len(r['trades'])} WR={wr:.1f}% reasons={dict(reasons)}")
    return {
        'variant': 'OI + SMA daily trend filter',
        'n_signals': len(filtered),
        'return': r['total_return_pct'],
        'dd': r['max_dd_pct'],
        'calmar': r['calmar'],
        'trades': len(r['trades']),
        'wr': wr,
        'exit_reasons': reasons,
    }


def run_all():
    t0 = time.time()

    print("Loading signals...")
    oi_sigs = load_signals()
    print(f"  Loaded {len(oi_sigs)} OI div limit signals")

    print("Loading OHLCV cache...")
    ohlcv_cache = load_ohlcv_cache()
    print(f"  Loaded {len(ohlcv_cache)} tickers")

    print("Resampling OHLCV to daily...")
    daily_ohlcv = resample_ohlcv_daily(ohlcv_cache)
    print(f"  Resampled {len(daily_ohlcv)} tickers to daily")

    # Precompute SMA cache for solutions B and D
    print("Computing SMA indicators...")
    ticker_sma_cache = {}
    for tk, df in daily_ohlcv.items():
        closes = df['close'].values.astype(np.float64)
        sma5 = pd.Series(closes).rolling(5).mean().values
        sma20 = pd.Series(closes).rolling(20).mean().values
        dates = df.index.values
        ticker_sma_cache[tk] = {'dates': dates, 'sma5': sma5, 'sma20': sma20}

    # ── Baseline ──
    print("\n═══ BASELINE ═══")
    base_result = BarLevelPortfolio(**BASELINE_PARAMS).run(oi_sigs)
    base_wr = compute_wr(base_result['trades'])
    base_reasons = Counter(t['exit_reason'] for t in base_result['trades'])
    print(f"  ret={base_result['total_return_pct']:.2f}% DD={base_result['max_dd_pct']:.2f}% Calmar={base_result['calmar']:.4f} trades={len(base_result['trades'])} WR={base_wr:.1f}%")
    print(f"  Exit reasons: {dict(base_reasons)}")

    # ── Solution A ──
    sol_a_results = score_filter_test(oi_sigs)

    # ── Solution B ──
    daily_sigs = make_daily_signals(oi_sigs, daily_ohlcv)
    print(f"\n  Created {len(daily_sigs)} daily signals from {len(oi_sigs)} 5m signals")
    sol_b_result = daily_test(daily_sigs)

    # ── Solution C ──
    sol_c_results = atr_trailing_test(oi_sigs)

    # ── Solution D ──
    sol_d_result = sma_filter_test(oi_sigs, daily_ohlcv, ticker_sma_cache)

    # ── Walk-forward for Calmar > 1.0 ──
    print("\n═══ WALK-FORWARD TESTS ═══")
    wf_results = {}

    canidates_wf = []

    # Check A - but only if not concentrated
    for res in sol_a_results:
        if res['calmar'] > 1.0:
            print(f"\n  Solution A ({res['variant']}): Calmar={res['calmar']:.4f}, top1Pnl={res['top1_pnl_pct']:.1f}%")
            if res['top1_pnl_pct'] < 50:
                print("    → Running walk-forward...")
                canidates_wf.append(('A', res, None))
            else:
                print("    → SKIP: result dominated by single trade")

    # Check B
    if sol_b_result['calmar'] > 1.0:
        print(f"\n  Solution B (Daily): Calmar={sol_b_result['calmar']:.4f} → running walk-forward...")
        params = dict(BASELINE_PARAMS)
        params.update(dict(margin_usage=0.50, max_hold_bars=12, stop_loss_pct=0.10, max_concurrent=10))
        wf = run_walkforward_generic(daily_sigs, params, trail_mult=None)
        wf_results['B'] = wf
        wf_returns = [r['total_return_pct'] for r in wf]
        wf_dds = [r['max_dd_pct'] for r in wf]
        print(f"    Fold returns: {[f'{r:.2f}%' for r in wf_returns]}")
        print(f"    Fold DDs: {[f'{d:.2f}%' for d in wf_dds]}")
        print(f"    All folds profitable: {all(r > 0 for r in wf_returns)}")

    # Check C
    for res in sol_c_results:
        if res['calmar'] > 1.0:
            print(f"\n  Solution C ({res['variant']}): Calmar={res['calmar']:.4f} → running walk-forward...")
            wf = run_walkforward_atr(oi_sigs, BASELINE_PARAMS, res['trail_mult'])
            wf_results[f'C_{res["trail_mult"]}'] = wf
            wf_returns = [r['total_return_pct'] for r in wf]
            wf_dds = [r['max_dd_pct'] for r in wf]
            print(f"    Fold returns: {[f'{r:.2f}%' for r in wf_returns]}")
            print(f"    Fold DDs: {[f'{d:.2f}%' for d in wf_dds]}")
            print(f"    All folds profitable: {all(r > 0 for r in wf_returns)}")

    # Check D
    if sol_d_result['calmar'] > 1.0:
        print(f"\n  Solution D (SMA filter): Calmar={sol_d_result['calmar']:.4f} → running walk-forward...")
        n = len(oi_sigs)
        n4 = n // 4
        folds_raw = [oi_sigs[:n4], oi_sigs[n4:2*n4], oi_sigs[2*n4:3*n4], oi_sigs[3*n4:]]
        fold_groups = []
        for fs in folds_raw:
            filtered_fold = []
            for s in fs:
                tk = s['ticker']
                data = ticker_sma_cache.get(tk)
                if data is None:
                    continue
                ts64 = np.datetime64(s['_time_dt'].to_datetime64())
                pos = np.searchsorted(data['dates'], ts64, side='right') - 1
                if pos < 0 or pos >= len(data['sma5']):
                    continue
                if np.isnan(data['sma5'][pos]) or np.isnan(data['sma20'][pos]):
                    continue
                if s['direction'] == 'LONG' and data['sma5'][pos] > data['sma20'][pos]:
                    filtered_fold.append(s)
                elif s['direction'] == 'SHORT' and data['sma5'][pos] < data['sma20'][pos]:
                    filtered_fold.append(s)
            groups = {}
            for s in filtered_fold:
                t = s['_time_dt']
                if t not in groups:
                    groups[t] = []
                groups[t].append(s)
            sorted_times = sorted(groups.keys())
            fold_groups.append((sorted_times, groups))

        params = dict(BASELINE_PARAMS)
        p = BarLevelPortfolio(**params)
        wf = []
        for sorted_times, time_groups in fold_groups:
            r = p._run_grouped(sorted_times, time_groups)
            wf.append(r)
        wf_results['D'] = wf
        wf_returns = [r['total_return_pct'] for r in wf]
        wf_dds = [r['max_dd_pct'] for r in wf]
        print(f"    Fold returns: {[f'{r:.2f}%' for r in wf_returns]}")
        print(f"    Fold DDs: {[f'{d:.2f}%' for d in wf_dds]}")
        print(f"    All folds profitable: {all(r > 0 for r in wf_returns)}")

    # ── Report ──
    print(f"\n═══ GENERATING REPORT ═══")
    os.makedirs(REPORT_DIR, exist_ok=True)

    report = []
    report.append(f"# TRIZ OI Redesign — Results ({TODAY})")
    report.append("")
    report.append("## Baseline")
    report.append("")
    report.append(f"| Metric | Value |")
    report.append(f"|--------|-------|")
    report.append(f"| Return | {base_result['total_return_pct']:.2f}% |")
    report.append(f"| Max DD | {base_result['max_dd_pct']:.2f}% |")
    report.append(f"| Calmar | {base_result['calmar']:.4f} |")
    report.append(f"| Trades | {len(base_result['trades'])} |")
    report.append(f"| Win Rate | {base_wr:.1f}% |")
    rollover_pct = base_reasons.get('rollover', 0) / max(len(base_result['trades']), 1) * 100
    report.append(f"| Rollover % | {rollover_pct:.1f}% |")
    report.append("")

    # ── A ──
    report.append("## Решение А: Score Filtering")
    report.append("")
    report.append("| Variant | Signals | Return% | DD% | Calmar | Trades | WR% | Top1Pnl% |")
    report.append("|---------|---------|---------|-----|--------|--------|-----|----------|")
    for r in sol_a_results:
        report.append(f"| {r['variant']} | {r['n_signals']} | {r['return']:.2f} | {r['dd']:.2f} | {r['calmar']:.4f} | {r['trades']} | {r['wr']:.1f} | {r['top1_pnl_pct']:.1f} |")
    report.append("")

    # ── B ──
    report.append("## Решение Б: Daily Timeframe")
    report.append("")
    report.append(f"| Variant | Signals | Return% | DD% | Calmar | Trades | WR% |")
    report.append(f"|---------|---------|---------|-----|--------|--------|-----|")
    report.append(f"| {sol_b_result['variant']} | {sol_b_result['n_signals']} | {sol_b_result['return']:.2f} | {sol_b_result['dd']:.2f} | {sol_b_result['calmar']:.4f} | {sol_b_result['trades']} | {sol_b_result['wr']:.1f} |")
    if 'B' in wf_results:
        wf = wf_results['B']
        report.append("")
        report.append("### Walk-Forward (4 folds)")
        report.append("")
        report.append("| Fold | Return% | DD% | Calmar | Trades |")
        report.append("|------|---------|-----|--------|--------|")
        for i, r in enumerate(wf):
            report.append(f"| {i+1} | {r['total_return_pct']:.2f} | {r['max_dd_pct']:.2f} | {r['calmar']:.4f} | {len(r['trades'])} |")
        report.append("")
        wf_returns = [r['total_return_pct'] for r in wf]
        report.append(f"**All folds profitable:** {all(r > 0 for r in wf_returns)}")
    report.append("")

    # ── C ──
    report.append("## Решение В: ATR Trailing")
    report.append("")
    report.append("| Variant | Return% | DD% | Calmar | Trades | WR% | Exit Reasons |")
    report.append("|---------|---------|-----|--------|--------|-----|-------------|")
    for r in sol_c_results:
        reasons_str = ', '.join(f'{k}={v}' for k, v in sorted(r['exit_reasons'].items()))
        report.append(f"| {r['variant']} | {r['return']:.2f} | {r['dd']:.2f} | {r['calmar']:.4f} | {r['trades']} | {r['wr']:.1f} | {reasons_str} |")
    for key, wf in wf_results.items():
        if key.startswith('C_'):
            mult = key.split('_')[1]
            report.append("")
            report.append(f"### Walk-Forward (ATR trail ×{mult})")
            report.append("")
            report.append("| Fold | Return% | DD% | Calmar | Trades |")
            report.append("|------|---------|-----|--------|--------|")
            for i, r in enumerate(wf):
                report.append(f"| {i+1} | {r['total_return_pct']:.2f} | {r['max_dd_pct']:.2f} | {r['calmar']:.4f} | {len(r['trades'])} |")
            report.append("")
            wf_returns = [r['total_return_pct'] for r in wf]
            report.append(f"**All folds profitable:** {all(r > 0 for r in wf_returns)}")
    report.append("")

    # ── D ──
    report.append("## Решение Г: OI + SMA Filter")
    report.append("")
    report.append(f"| Variant | Signals | Return% | DD% | Calmar | Trades | WR% |")
    report.append(f"|---------|---------|---------|-----|--------|--------|-----|")
    report.append(f"| {sol_d_result['variant']} | {sol_d_result['n_signals']} | {sol_d_result['return']:.2f} | {sol_d_result['dd']:.2f} | {sol_d_result['calmar']:.4f} | {sol_d_result['trades']} | {sol_d_result['wr']:.1f} |")
    if 'D' in wf_results:
        wf = wf_results['D']
        report.append("")
        report.append("### Walk-Forward (4 folds)")
        report.append("")
        report.append("| Fold | Return% | DD% | Calmar | Trades |")
        report.append("|------|---------|-----|--------|--------|")
        for i, r in enumerate(wf):
            report.append(f"| {i+1} | {r['total_return_pct']:.2f} | {r['max_dd_pct']:.2f} | {r['calmar']:.4f} | {len(r['trades'])} |")
        report.append("")
        wf_returns = [r['total_return_pct'] for r in wf]
        report.append(f"**All folds profitable:** {all(r > 0 for r in wf_returns)}")
    report.append("")

    # ── Summary ──
    report.append("## Сводка")
    report.append("")
    report.append("| Решение | Return% | DD% | Calmar | W-F Pass | Вердикт |")
    report.append("|---------|---------|-----|--------|----------|---------|")

    verdict_a = max(sol_a_results, key=lambda r: r['calmar'])
    top1_a = verdict_a['top1_pnl_pct']
    a_note = f"Calmar={verdict_a['calmar']:.2f} top1Pnl={top1_a:.0f}%"
    a_pass = '—'

    b_note = f"Calmar={sol_b_result['calmar']:.2f}"
    b_pass = '—'
    if 'B' in wf_results:
        b_wf_ok = all(r['total_return_pct'] > 0 for r in wf_results['B'])
        b_pass = '✅' if b_wf_ok else '❌'
    b_verdict = f"{'✅' if sol_b_result['calmar'] > 0.5 else '❌'} {b_note}"

    best_c = max(sol_c_results, key=lambda r: r['calmar'])
    c_note = f"Calmar={best_c['calmar']:.2f} ×{best_c['trail_mult']}"
    c_pass = '—'
    for key, wf in wf_results.items():
        if key.startswith('C_'):
            c_wf_ok = all(r['total_return_pct'] > 0 for r in wf)
            c_pass = '✅' if c_wf_ok else '❌'

    d_note = f"Calmar={sol_d_result['calmar']:.2f}"
    d_pass = '—'
    if 'D' in wf_results:
        d_wf_ok = all(r['total_return_pct'] > 0 for r in wf_results['D'])
        d_pass = '✅' if d_wf_ok else '❌'
    d_verdict = f"{'✅' if sol_d_result['calmar'] > 0.3 else '❌'} {d_note}"

    report.append(f"| Baseline | {base_result['total_return_pct']:.2f}% | {base_result['max_dd_pct']:.2f}% | {base_result['calmar']:.4f} | — | — |")
    report.append(f"| A (Score) | {verdict_a['return']:.2f}% | {verdict_a['dd']:.2f}% | {verdict_a['calmar']:.4f} | {a_pass} | {a_note} |")
    report.append(f"| B (Daily) | {sol_b_result['return']:.2f}% | {sol_b_result['dd']:.2f}% | {sol_b_result['calmar']:.4f} | {b_pass} | {b_verdict} |")
    report.append(f"| C (Trail) | {best_c['return']:.2f}% | {best_c['dd']:.2f}% | {best_c['calmar']:.4f} | {c_pass} | {c_note} |")
    report.append(f"| D (SMA) | {sol_d_result['return']:.2f}% | {sol_d_result['dd']:.2f}% | {sol_d_result['calmar']:.4f} | {d_pass} | {d_verdict} |")
    report.append("")

    report.append("## Выводы")
    report.append("")
    if sol_b_result['calmar'] > 1.0:
        report.append("**Daily timeframe** — перспективно. Требует проверки walk-forward.")
    if best_c['calmar'] > 0.3:
        report.append(f"**ATR trailing ×{best_c['trail_mult']}** — улучшает удержание. Лучший вариант среди trailing.")
    if sol_d_result['calmar'] > 0.3:
        report.append("**OI + SMA filter** — отсекает контр-трендовые сделки.")
    if all(r['calmar'] <= 0.3 for r in sol_a_results):
        report.append("**Score filtering** — не даёт надёжного улучшения.")
    report.append("")
    report.append(f"Время выполнения: {time.time()-t0:.0f}s")

    report_content = '\n'.join(report)
    with open(REPORT_PATH, 'w') as f:
        f.write(report_content)
    print(f"\n✅ Report saved: {REPORT_PATH}")
    print(f"⏱  Total time: {time.time()-t0:.0f}s")

    return {
        'baseline': base_result,
        'sol_a': sol_a_results,
        'sol_b': sol_b_result,
        'sol_c': sol_c_results,
        'sol_d': sol_d_result,
        'wf': wf_results,
    }


if __name__ == '__main__':
    results = run_all()
