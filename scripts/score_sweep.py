#!/usr/bin/env python3
"""
score_sweep.py — Score-based Cascade sweep on OI Divergence signals.

TRIZ Principle 6: Universality — one score instead of binary filters.

Шаг 1: Собрать OI Divergence сигналы со всех тикеров (WR>52%)
Шаг 2: Score threshold sweep (0.3, 0.4, 0.5, 0.6) — WR/avgRet/DD
Шаг 3: Capital growth sweep — TOP-10 по Calmar для DD≤10%, DD≤15%, DD≤20%

Usage:
    python3 scripts/score_sweep.py
"""

import os, sys
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from datetime import datetime
from typing import List, Dict, Optional

from trading_bot.new_strategies import (
    load_ohlcv, load_oi, merge_ohlcv_oi,
    detect_oi_divergence_signals_limit,
)
from trading_bot.strategy_cascade import compute_quality_score

OUT_DIR = os.path.join(PROJECT_ROOT, 'docs', 'plans', 'strategy_v3')
os.makedirs(OUT_DIR, exist_ok=True)

HISTORY_DAYS = 365

QUALIFIED_TICKERS = [
    'AF', 'AU', 'BR', 'CC', 'CE', 'CH', 'CNYRUBF', 'CR', 'DX', 'ED',
    'EURRUBF', 'FF', 'GD', 'GK', 'GL', 'GLDRUBF', 'GZ', 'HS', 'HY',
    'IMOEXF', 'KC', 'MC', 'ME', 'MG', 'MN', 'MX', 'NA', 'NM', 'PD',
    'RB', 'RI', 'RL', 'RN', 'SBERF', 'SE', 'SF', 'SN', 'SP', 'SR',
    'SS', 'SV', 'Si', 'TN', 'TT', 'UC', 'VI', 'W4',
]

ALL_TICKER_CONFIGS = {
    'AF': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'AU': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'BR': {'go': 3000, 'minstep': 0.01, 'tick_rub': 1.0},
    'CC': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'CE': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'CH': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'CNYRUBF': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'CR': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'DX': {'go': 3000, 'minstep': 1, 'tick_rub': 1.0},
    'ED': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'EURRUBF': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'FF': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'GD': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'GK': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'GL': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'GLDRUBF': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'GZ': {'go': 2065, 'minstep': 0.01, 'tick_rub': 0.01},
    'HS': {'go': 5000, 'minstep': 1, 'tick_rub': 1.0},
    'HY': {'go': 3000, 'minstep': 1, 'tick_rub': 1.0},
    'IMOEXF': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'KC': {'go': 2500, 'minstep': 0.01, 'tick_rub': 80.0},
    'MC': {'go': 3149, 'minstep': 0.01, 'tick_rub': 1.0},
    'ME': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'MG': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'MN': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'MX': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'NA': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'NM': {'go': 1405, 'minstep': 1, 'tick_rub': 1.0},
    'PD': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'RB': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'RI': {'go': 5000, 'minstep': 1, 'tick_rub': 1.0},
    'RL': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'RN': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'SBERF': {'go': 2500, 'minstep': 1, 'tick_rub': 1.0},
    'SE': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'SF': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'SN': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'SP': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'SR': {'go': 5719, 'minstep': 0.01, 'tick_rub': 1.0},
    'SS': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'SV': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'Si': {'go': 1000, 'minstep': 0.01, 'tick_rub': 1.0},
    'TN': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'TT': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'UC': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'VI': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'W4': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
}


# ── Helpers ────────────────────────────────────────────────────────────────


def compute_stats(signals):
    if not signals:
        return {'n': 0, 'wr': 0.0, 'pf': 0.0, 'dd': 0.0, 'avg_ret': 0.0}
    returns = [s['return_pct'] for s in signals]
    n = len(returns)
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r < 0]
    wr = len(wins) / n * 100 if n > 0 else 0.0
    sum_wins = sum(wins) if wins else 0.0
    sum_losses = abs(sum(losses)) if losses else 0.0
    pf = sum_wins / sum_losses if sum_losses > 0 else (sum_wins if sum_wins > 0 else 0.0)
    cum, peak, max_dd = 0.0, 0.0, 0.0
    for r in returns:
        cum += r
        if cum > peak:
            peak = cum
        dd = peak - cum
        if peak > 0 and dd > max_dd:
            max_dd = dd
    return {
        'n': n, 'wr': round(wr, 1), 'pf': round(pf, 2),
        'dd': round(max_dd, 1), 'avg_ret': round(sum(returns) / n, 2),
    }


def max_drawdown(equity):
    if not equity:
        return 0.0
    peak = equity[0]
    mdd = 0.0
    for v in equity:
        if v > peak:
            peak = v
        dd = (peak - v) / peak if peak > 0 else 0
        if dd > mdd:
            mdd = dd
    return mdd


def calc_pnl(direction, entry, exit_price, contracts, symbol):
    cfg = ALL_TICKER_CONFIGS.get(symbol, {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0})
    minstep = cfg['minstep']
    tick_rub = cfg['tick_rub']
    moves = (exit_price - entry) / minstep
    if direction.upper() == 'SHORT':
        moves = -moves
    return round(moves * tick_rub * contracts, 2)


# ── Step 1: Collect signals ─────────────────────────────────────────────


def collect_signals_with_scores() -> List[Dict]:
    """Load OI Divergence signals + compute quality score for each."""
    all_signals = []
    errors = []

    for sym in QUALIFIED_TICKERS:
        print(f"  [{sym}] Loading data...")
        try:
            ohlcv = load_ohlcv(sym, HISTORY_DAYS)
            if not ohlcv or len(ohlcv) < 100:
                print(f"    skip — insufficient OHLCV ({len(ohlcv) if ohlcv else 0})")
                continue
            oi = load_oi(sym, HISTORY_DAYS)
            if not oi:
                print(f"    skip — no OI data")
                continue
            merged = merge_ohlcv_oi(ohlcv, oi)
            if not merged or len(merged) < 100:
                print(f"    skip — insufficient merged data ({len(merged) if merged else 0})")
                continue

            sigs = detect_oi_divergence_signals_limit(merged, {'horizon': 12})
            if not sigs:
                print(f"    → 0 signals")
                continue

            scored = 0
            for s in sigs:
                idx = s.get('idx')
                if idx is None or idx >= len(merged):
                    continue
                quality = compute_quality_score(merged, idx)
                s['score'] = quality['total']
                s['score_components'] = quality['components']
                s['ticker'] = sym
                scored += 1

            kept = [s for s in sigs if s.get('score', 0) >= 0.3]
            all_signals.extend(kept)
            print(f"    → {len(sigs)} signals, {scored} scored, {len(kept)} ≥ 0.3")
        except Exception as e:
            errors.append(f"{sym}: {e}")
            print(f"    ERROR: {e}")

    all_signals.sort(key=lambda s: str(s.get('time', '')))

    print(f"\n  Total collected: {len(all_signals)} signals (score ≥ 0.3)")
    if errors:
        print(f"  Errors: {len(errors)}")
        for e in errors[:5]:
            print(f"    - {e}")

    return all_signals


# ── Step 2: Score threshold analysis ─────────────────────────────────


def run_score_analysis(all_signals: List[Dict]):
    """Evaluate WR/avgRet/DD at each score threshold."""
    print("\n" + "=" * 60)
    print("  STEP 2: SCORE THRESHOLD ANALYSIS")
    print("=" * 60)

    thresholds = [0.0, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]
    results = []

    for th in thresholds:
        filtered = [s for s in all_signals if s.get('score', 0) >= th]
        stats = compute_stats(filtered)
        results.append({'threshold': th, 'stats': stats, 'n': stats['n']})
        print(f"  score ≥ {th:.1f}: {stats['n']:>5} sig, WR={stats['wr']:>5.1f}%, "
              f"avgRet={stats['avg_ret']:>6.2f}%, DD={stats['dd']:>5.1f}%")

    lines = []
    lines.append("=" * 70)
    lines.append("  SCORE-BASED CASCADE — Threshold Analysis")
    lines.append(f"  Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"  Tickers: {len(QUALIFIED_TICKERS)} (WR>52%)")
    lines.append(f"  Data window: {HISTORY_DAYS} days")
    lines.append(f"  Strategy: OI Divergence (limit-order variant, horizon=12)")
    lines.append("=" * 70)
    lines.append("")
    lines.append(f"  {'Threshold':<12} {'Signals':<8} {'WR%':<8} {'AvgRet%':<10} {'DD%':<8}")
    lines.append(f"  {'-'*46}")
    for r in results:
        s = r['stats']
        lines.append(f"  {'≥ ' + str(r['threshold']):<12} {s['n']:<8} {s['wr']:<8} {s['avg_ret']:<10} {s['dd']:<8}")
    lines.append("")

    # Score distribution
    lines.append("── Score distribution ──")
    lines.append("")
    all_scores = sorted([s.get('score', 0) for s in all_signals])
    if all_scores:
        for pct in [10, 25, 50, 75, 90]:
            idx = int(len(all_scores) * pct / 100)
            lines.append(f"  P{pct:>2}: {all_scores[min(idx, len(all_scores)-1)]:.3f}")
        lines.append(f"  Mean: {sum(all_scores)/len(all_scores):.3f}")
        lines.append(f"  Min:  {all_scores[0]:.3f}")
        lines.append(f"  Max:  {all_scores[-1]:.3f}")

    out_path = os.path.join(OUT_DIR, 'score_analysis.txt')
    with open(out_path, 'w') as f:
        f.write('\n'.join(lines))
    print(f"\n  ✅ Saved {out_path}")

    return results


# ── Step 3: Capital growth simulation (adaptive risk) ────────────────


def simulate_adaptive(
    signals: List[Dict],
    initial_capital: float,
    base_margin_usage: float,
    max_concurrent: int,
    base_total_margin_limit: float,
    max_dd_limit: float,
    stop_loss_pct: float = 0.02,
) -> Dict:
    """Adaptive risk simulation: compression reduces margin as equity drops."""
    capital = float(initial_capital)
    equity = [capital]
    margin_ratio_history = [0.0]
    peak = capital
    compression_history = [1.0]
    active: Dict[str, Dict] = {}
    trades: List[Dict] = []

    def _total_equity():
        return capital + sum(p['locked_go'] for p in active.values())

    def _record_margin_usage():
        te = _total_equity()
        if te > 0:
            locked = sum(p['locked_go'] for p in active.values())
            margin_ratio_history.append(locked / te)
        else:
            margin_ratio_history.append(0.0)

    for sig in signals:
        tk = sig.get('ticker', '')
        if not tk or tk not in ALL_TICKER_CONFIGS:
            continue

        te = _total_equity()
        if te > peak:
            peak = te
        compression = te / peak if peak > 0 else 1.0
        compression = max(min(compression, 1.0), 0.3)
        compression_history.append(compression)

        adaptive_margin = base_margin_usage * compression
        adaptive_tm_limit = base_total_margin_limit * compression

        dd = (peak - te) / peak if peak > 0 else 0
        if dd > max_dd_limit:
            for t in list(active.keys()):
                pos = active.pop(t)
                pnl = calc_pnl(pos['direction'], pos['entry_price'], pos['exit_price'], pos['contracts'], t)
                capital += pos['locked_go'] + pnl
            equity.append(_total_equity())
            _record_margin_usage()
            break

        if tk in active:
            pos = active.pop(tk)
            pnl = calc_pnl(pos['direction'], pos['entry_price'], pos['exit_price'], pos['contracts'], tk)
            capital += pos['locked_go'] + pnl
            peak = max(peak, _total_equity())
            equity.append(_total_equity())
            _record_margin_usage()
            trades.append({
                'ticker': tk, 'pnl': pnl,
                'entry_time': pos.get('entry_time', ''),
                'exit_time': sig.get('time', ''),
                'direction': pos['direction'],
                'contracts': pos['contracts'],
            })

        if len(active) >= max_concurrent:
            continue

        cfg = ALL_TICKER_CONFIGS.get(tk)
        if not cfg:
            continue
        go = cfg.get('go', 0)
        if go <= 0:
            continue

        total_cap = _total_equity()
        max_risk = total_cap * adaptive_margin
        contracts = int(max_risk // go) if max_risk >= go else 0
        if contracts < 1:
            continue
        locked_go = contracts * go

        total_locked = sum(p['locked_go'] for p in active.values())
        if total_locked + locked_go > total_cap * adaptive_tm_limit:
            continue

        entry_price = sig.get('entry', 0)
        exit_price = sig.get('exit', 0)
        direction = sig.get('direction', 'LONG')

        if stop_loss_pct > 0:
            if direction == 'LONG':
                stop_price = entry_price * (1 - stop_loss_pct)
                if exit_price < stop_price:
                    exit_price = stop_price
            else:
                stop_price = entry_price * (1 + stop_loss_pct)
                if exit_price > stop_price:
                    exit_price = stop_price

        if locked_go > capital:
            continue
        capital -= locked_go

        active[tk] = {
            'entry_price': entry_price,
            'exit_price': exit_price,
            'direction': direction,
            'contracts': contracts,
            'entry_time': sig.get('time', ''),
            'locked_go': locked_go,
        }
        _record_margin_usage()

    for tk in list(active.keys()):
        pos = active.pop(tk)
        pnl = calc_pnl(pos['direction'], pos['entry_price'], pos['exit_price'], pos['contracts'], tk)
        capital += pos['locked_go'] + pnl
        peak = max(peak, _total_equity())
        equity.append(_total_equity())
        _record_margin_usage()

    return {
        'final_capital': round(_total_equity(), 2),
        'equity': equity,
        'trades': trades,
        'margin_ratio': margin_ratio_history,
        'compression': compression_history,
    }


def run_capital_growth_sweep(all_signals: List[Dict], best_threshold: float):
    """Step 3: Capital growth grid search with best score threshold."""
    print("\n" + "=" * 60)
    print("  STEP 3: CAPITAL GROWTH SWEEP")
    print(f"  Score threshold: ≥ {best_threshold:.1f}")
    print(f"  Signals: {len(all_signals)}")
    print(f"  Capital: 100,000 RUB")
    print("=" * 60)

    initial_capital = 100_000

    param_grid = {
        'mu': [0.05, 0.08, 0.10, 0.15, 0.20],
        'mc': [2, 3, 5],
        'tm': [0.10, 0.15, 0.20, 0.30],
        'sl': [0.01, 0.02, 0.03],
    }

    total = len(param_grid['mu']) * len(param_grid['mc']) * len(param_grid['tm']) * len(param_grid['sl'])
    print(f"  Grid size: {total} combinations")
    count = 0

    all_results = []
    for mu in param_grid['mu']:
        for mc in param_grid['mc']:
            for tm in param_grid['tm']:
                for sl in param_grid['sl']:
                    count += 1
                    if count % 50 == 0:
                        print(f"    Progress: {count}/{total} ({count*100//total}%)")
                    res = simulate_adaptive(
                        all_signals, initial_capital,
                        base_margin_usage=mu,
                        max_concurrent=mc,
                        base_total_margin_limit=tm,
                        max_dd_limit=0.20,
                        stop_loss_pct=sl,
                    )
                    mdd = max_drawdown(res['equity'])
                    final_cap = res['final_capital']
                    ret_pct = (final_cap - initial_capital) / initial_capital * 100
                    calmar = ret_pct / (mdd * 100) if mdd > 0.001 else 0

                    all_results.append({
                        'params': {'mu': mu, 'mc': mc, 'tm': tm, 'sl': sl},
                        'max_dd': mdd,
                        'final_capital': final_cap,
                        'return_pct': ret_pct,
                        'calmar': calmar,
                        'n_trades': len(res['trades']),
                    })

    print(f"    Total evaluated: {count}")

    # TOP-10 for each DD level
    dd_levels = [0.10, 0.15, 0.20]
    all_top10 = {}

    for dd_level in dd_levels:
        qualified = [r for r in all_results if r['max_dd'] <= dd_level]
        qualified.sort(key=lambda r: r['calmar'], reverse=True)
        top10 = qualified[:10]
        all_top10[dd_level] = top10

        dd_pct = int(dd_level * 100)
        print(f"\n  ── TOP-10 by Calmar (DD ≤ {dd_pct}%) ──")
        header = f"  {'Rank':<5} {'FinalCap':>10} {'Ret%':>8} {'DD%':<8} {'Calmar':<8} {'Trades':<7} {'Params'}"
        print(header)
        print(f"  {'-'*55}")
        for i, r in enumerate(top10):
            p = r['params']
            print(f"  {i+1:<5} {r['final_capital']:>10,.0f} {r['return_pct']:>8.1f} "
                  f"{r['max_dd']*100:<8.2f} {r['calmar']:<8.2f} {r['n_trades']:<7} "
                  f"mu={p['mu']} mc={p['mc']} tm={p['tm']} sl={p['sl']}")

    # Save to file
    lines = []
    lines.append("=" * 70)
    lines.append("  SCORE-BASED CASCADE — Capital Growth Pareto")
    lines.append(f"  Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"  Score threshold: ≥ {best_threshold:.1f}")
    lines.append(f"  Signals: {len(all_signals)}")
    lines.append(f"  Initial capital: {initial_capital:,} RUB")
    lines.append(f"  Grid: mu={param_grid['mu']}, mc={param_grid['mc']}, "
                 f"tm={param_grid['tm']}, sl={param_grid['sl']}")
    lines.append(f"  Total evaluated: {count}")
    lines.append("=" * 70)
    lines.append("")

    for dd_level in dd_levels:
        top10 = all_top10[dd_level]
        dd_pct = int(dd_level * 100)
        lines.append(f"── TOP-10 by Calmar (DD ≤ {dd_pct}%) ──")
        lines.append("")
        header = f"  {'Rank':<5} {'FinalCap':>10} {'Ret%':>8} {'DD%':<8} {'Calmar':<8} {'Trades':<7} {'mu':<6} {'mc':<4} {'tm':<6} {'sl':<5}"
        lines.append(header)
        lines.append(f"  {'-'*70}")
        for i, r in enumerate(top10):
            p = r['params']
            lines.append(f"  {i+1:<5} {r['final_capital']:>10,.0f} {r['return_pct']:>8.1f} "
                         f"{r['max_dd']*100:<8.2f} {r['calmar']:<8.2f} {r['n_trades']:<7} "
                         f"{p['mu']:<6} {p['mc']:<4} {p['tm']:<6} {p['sl']:<5}")
        lines.append("")

    out_path = os.path.join(OUT_DIR, 'score_pareto.txt')
    with open(out_path, 'w') as f:
        f.write('\n'.join(lines))
    print(f"\n  ✅ Saved {out_path}")

    return all_top10


# ── Main ────────────────────────────────────────────────────────────────


def main():
    print("=" * 60)
    print("  SCORE-BASED CASCADE SWEEP — OI Divergence")
    print(f"  Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  Tickers: {len(QUALIFIED_TICKERS)} (WR>52%)")
    print(f"  Data window: {HISTORY_DAYS} days")
    print("=" * 60)

    # Step 1: Collect
    print("\n📡 Step 1: Collecting OI Divergence signals with quality scores...")
    all_signals = collect_signals_with_scores()

    if not all_signals:
        print("  ❌ No signals collected! Aborting.")
        sys.exit(1)

    # Step 2: Score analysis
    print("\n📊 Step 2: Score threshold analysis...")
    threshold_results = run_score_analysis(all_signals)

    # Determine best threshold: highest WR with at least 20 signals
    best_th = 0.3
    best_wr = 0
    for r in threshold_results:
        th = r['threshold']
        if th == 0.0:
            continue  # skip raw (no filter)
        if r['stats']['n'] >= 20 and r['stats']['wr'] > best_wr:
            best_wr = r['stats']['wr']
            best_th = th

    if best_th < 0.3:
        best_th = 0.3

    # Filter to best threshold for step 3
    best_signals = [s for s in all_signals if s.get('score', 0) >= best_th]
    print(f"\n  Best threshold: ≥ {best_th:.1f} (WR={best_wr:.1f}%, {len(best_signals)} signals)")

    # Step 3: Capital growth sweep
    print("\n💰 Step 3: Capital growth sweep...")
    run_capital_growth_sweep(best_signals, best_th)

    print("\n✅ Score sweep complete!")


if __name__ == '__main__':
    main()
