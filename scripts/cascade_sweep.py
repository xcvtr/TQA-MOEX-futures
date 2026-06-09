#!/usr/bin/env python3
"""
cascade_sweep.py — Phase 3 & 4: Cascade filter sweep + Capital growth simulation.

Phase 3: Apply all filter combinations to OI Divergence signals,
         measure WR/avgRet/DD at each cascade level.

Phase 4: Capital growth grid search with cascade filters.
         Grid: mu, mc, tm, sl, filters_combinations.
         Find combo with +900%/yr at DD<=15%.

Usage:
    python3 scripts/cascade_sweep.py --phase3
    python3 scripts/cascade_sweep.py --phase4
    python3 scripts/cascade_sweep.py --all
"""

import os, sys, itertools, json
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional, Tuple
from trading_bot.new_strategies import load_ohlcv, load_oi, merge_ohlcv_oi, detect_oi_divergence_signals
from trading_bot.strategy_cascade import adx_filter, volume_filter, whale_filter, hvn_filter, atr_filter, apply_filters
from trading_bot.filters import calc_adx, calc_atr
from trading_bot.strategy_profile import _find_hvn_levels


def _zs(vals, w=20):
    out = [0.0] * len(vals)
    for i in range(w, len(vals)):
        chunk = vals[i-w:i]
        mu = sum(chunk) / w
        var = sum((x-mu)**2 for x in chunk) / w
        sd = var**0.5
        out[i] = (vals[i] - mu) / sd if sd > 0 else 0.0
    return out

OUT_DIR = os.path.join(PROJECT_ROOT, 'docs', 'plans', 'strategy_v3')
os.makedirs(OUT_DIR, exist_ok=True)

HISTORY_DAYS = 365

# Qualified tickers from Phase 1 (WR>52%, sig>=20)
QUALIFIED_TICKERS = [
    'AF', 'AU', 'BR', 'CC', 'CE', 'CH', 'CNYRUBF', 'CR', 'DX', 'ED',
    'EURRUBF', 'FF', 'GD', 'GK', 'GL', 'GLDRUBF', 'GZ', 'HS', 'HY',
    'IMOEXF', 'KC', 'MC', 'ME', 'MG', 'MN', 'MX', 'NA', 'NM', 'PD',
    'RB', 'RI', 'RL', 'RN', 'SBERF', 'SE', 'SF', 'SN', 'SP', 'SR',
    'SS', 'SV', 'Si', 'TN', 'TT', 'UC', 'VI', 'W4',
]

# Best horizon per ticker from screening
BEST_HORIZON = {
    'AF': 12, 'AU': 24, 'BR': 24, 'CC': 6, 'CE': 24, 'CH': 6,
    'CNYRUBF': 12, 'CR': 12, 'DX': 12, 'ED': 6, 'EURRUBF': 24,
    'FF': 24, 'GD': 12, 'GK': 24, 'GL': 6, 'GLDRUBF': 24, 'GZ': 24,
    'HS': 24, 'HY': 24, 'IMOEXF': 24, 'KC': 24, 'MC': 24, 'ME': 6,
    'MG': 24, 'MN': 24, 'MX': 6, 'NA': 24, 'NM': 24, 'PD': 6,
    'RB': 24, 'RI': 24, 'RL': 24, 'RN': 24, 'SBERF': 12, 'SE': 24,
    'SF': 24, 'SN': 24, 'SP': 24, 'SR': 12, 'SS': 24, 'SV': 24,
    'Si': 6, 'TN': 12, 'TT': 24, 'UC': 24, 'VI': 12, 'W4': 6,
}

# All ticker configs for PnL calc
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

_filter_names = ['adx', 'volume', 'whale', 'hvn', 'atr']


def compute_stats(signals):
    if not signals: return {'n':0,'wr':0.0,'pf':0.0,'dd':0.0,'avg_ret':0.0}
    returns = [s['return_pct'] for s in signals]
    n = len(returns)
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r < 0]
    wr = len(wins)/n*100 if n>0 else 0.0
    sum_wins = sum(wins) if wins else 0.0
    sum_losses = abs(sum(losses)) if losses else 0.0
    pf = sum_wins/sum_losses if sum_losses>0 else (sum_wins if sum_wins>0 else 0.0)
    cum = 0.0; peak = 0.0; max_dd = 0.0
    for r in returns:
        cum += r
        if cum > peak: peak = cum
        dd = peak - cum
        if peak > 0 and dd > max_dd: max_dd = dd
    return {'n':n,'wr':round(wr,1),'pf':round(pf,2),'dd':round(max_dd,1),'avg_ret':round(sum(returns)/n,2)}


def collect_all_oi_signals() -> Tuple[List[Dict], Dict[str, List[Dict]]]:
    """Load data and run OI Divergence on all qualified tickers."""
    all_signals = []
    ticker_data = {}

    for sym in QUALIFIED_TICKERS:
        print(f"  Loading {sym}...")
        try:
            ohlcv = load_ohlcv(sym, HISTORY_DAYS)
            if not ohlcv or len(ohlcv) < 100:
                continue
            oi = load_oi(sym, HISTORY_DAYS)
            if not oi:
                continue
            merged = merge_ohlcv_oi(ohlcv, oi)
            if not merged or len(merged) < 100:
                continue
            ticker_data[sym] = merged
            h = BEST_HORIZON.get(sym, 6)
            sigs = detect_oi_divergence_signals(merged, {'horizon': h})
            for s in sigs:
                s['ticker'] = sym
                s['horizon'] = h
            all_signals.extend(sigs)
            print(f"    → {len(sigs)} signals (h={h})")
        except Exception as e:
            print(f"    ERROR: {e}")
            continue

    all_signals.sort(key=lambda s: str(s.get('time', '')))
    print(f"\n  Total OI Divergence signals: {len(all_signals)}")
    return all_signals, ticker_data


def build_filter_combinations(max_filters=5):
    """Build all filter combinations: 1 filter, 2 filters, ..., max_filters."""
    combos = []
    for n in range(1, max_filters + 1):
        for combo in itertools.combinations(_filter_names, n):
            combos.append(list(combo))
    return combos


def format_combination(combo):
    """Format filter list as display string."""
    if len(combo) == 5:
        return "ALL 5 filters"
    return "+".join(combo)


def precompute_ticker_arrays(ticker_data):
    """Precompute ADX, ATR, z-score arrays per ticker (once per ticker, not per signal)."""
    print("  Precomputing per-ticker arrays (ADX, ATR, z-scores)...")
    ticker_arrays = {}
    for tk, data in ticker_data.items():
        n = len(data)
        closes = [r['close'] for r in data]
        highs = [r['high'] for r in data]
        lows = [r['low'] for r in data]
        volumes = [r['volume'] for r in data]

        arrays = {'n': n}
        arrays['adx'] = calc_adx(closes)

        arrays['atr'] = calc_atr(highs, lows, closes)
        arrays['closes'] = closes

        # Volume SMA
        vol_sma = [0.0] * n
        for i in range(20, n):
            vol_sma[i] = sum(volumes[i-20:i]) / 20
        arrays['vol_sma20'] = vol_sma
        arrays['volumes'] = volumes

        # Whale z-scores
        if 'yur_buy' in data[0]:
            yb = [r['yur_buy'] for r in data]
            ys = [r['yur_sell'] for r in data]
            arrays['yur_buy_z'] = _zs(yb)
            arrays['yur_sell_z'] = _zs(ys)
        else:
            arrays['yur_buy_z'] = [0.0] * n
            arrays['yur_sell_z'] = [0.0] * n

        ticker_arrays[tk] = arrays

    return ticker_arrays


def precompute_hvn(ticker_data):
    """Precompute HVN filter: for each signal index, is price near HVN?"""
    print("  Precomputing HVN filters...")
    hvn_cache = {}
    for tk, data in ticker_data.items():
        n = len(data)
        f_hvn = [False] * n
        for idx in range(20 + 5, n):
            segment = data[max(0, idx - 20):idx + 1]
            hvn_level, _ = _find_hvn_levels(segment, lookback=20)
            if hvn_level is not None:
                close = data[idx]['close']
                f_hvn[idx] = abs(close - hvn_level) / max(hvn_level, 1) <= 0.01
        hvn_cache[tk] = f_hvn
    return hvn_cache


def lookup_precomputed(sig, ticker_arrays, hvn_cache):
    """Look up all 5 filter results for a given signal using precomputed arrays."""
    tk = sig.get('ticker', '')
    idx = sig.get('idx', 0)
    arr = ticker_arrays.get(tk)
    if arr is None or idx >= arr['n']:
        return {}

    f_adx = arr['adx'][idx] > 25 if idx < len(arr['adx']) else False
    f_vol = (arr['volumes'][idx] > 1.5 * arr['vol_sma20'][idx]
             if idx < len(arr['vol_sma20']) and arr['vol_sma20'][idx] > 0 else False)
    f_whale = (abs(arr['yur_buy_z'][idx]) > 1.5 or abs(arr['yur_sell_z'][idx]) > 1.5
               if idx < len(arr['yur_buy_z']) else False)
    f_hvn = hvn_cache.get(tk, [False] * arr['n'])[idx] if idx < len(hvn_cache.get(tk, [])) else False
    f_atr = (arr['atr'][idx] < 0.02 * arr['closes'][idx]
             if idx < len(arr['atr']) and arr['closes'][idx] > 0 else False)

    return {'adx': f_adx, 'volume': f_vol, 'whale': f_whale, 'hvn': f_hvn, 'atr': f_atr}


def run_phase3(all_signals, ticker_data):
    """Phase 3: Cascade analysis — WR at each filter combination."""
    print("\n" + "=" * 60)
    print("  PHASE 3: CASCADE FILTER ANALYSIS")
    print("=" * 60)

    ticker_arrays = precompute_ticker_arrays(ticker_data)
    hvn_cache = precompute_hvn(ticker_data)
    combos = build_filter_combinations(5)
    results = []

    # Lookup precomputed for each signal
    print("  Looking up filter results for all signals...")
    precomputed = []
    for sig in all_signals:
        precomputed.append(lookup_precomputed(sig, ticker_arrays, hvn_cache))

    # Raw OI Divergence stats
    raw_stats = compute_stats(all_signals)
    print(f"\n  OI raw: {raw_stats['n']} sig, WR={raw_stats['wr']}%, avgRet={raw_stats['avg_ret']}%")

    for combo in combos:
        filtered = []
        for i, sig in enumerate(all_signals):
            if i >= len(precomputed):
                continue
            fr = precomputed[i]
            if all(fr.get(f, False) for f in combo):
                filtered.append(sig)

        stats = compute_stats(filtered)
        label = format_combination(combo)
        filtered_out = raw_stats['n'] - stats['n']
        print(f"  +{label}: {stats['n']} sig (отсеял {filtered_out}), WR={stats['wr']}%, avgRet={stats['avg_ret']}%")
        results.append({'combo': combo, 'label': label, 'stats': stats})

    # Phase 3 results to file
    lines = []
    lines.append("=" * 70)
    lines.append("  CASCADE FILTER ANALYSIS — OI Divergence + Filters")
    lines.append(f"  Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"  Tickers: {len(QUALIFIED_TICKERS)}")
    lines.append(f"  Data window: {HISTORY_DAYS} days")
    lines.append("=" * 70)
    lines.append("")
    lines.append(f"  OI raw:                       {raw_stats['n']:<6} sig, WR={raw_stats['wr']}%, avgRet={raw_stats['avg_ret']}%")

    # Sort by WR ascending to show progression
    results_sorted = sorted(results, key=lambda r: len(r['combo']))
    for r in results_sorted:
        s = r['stats']
        filtered_out = raw_stats['n'] - s['n']
        if filtered_out > 0:
            lines.append(f"  +{r['label']:<35} {s['n']:<6} sig (отсеял {filtered_out}), WR={s['wr']}%, avgRet={s['avg_ret']}%")

    lines.append("")
    lines.append("── Filter effectiveness (each filter alone) ──")
    lines.append("")
    for f_name in _filter_names:
        filtered = []
        for i, sig in enumerate(all_signals):
            if i >= len(precomputed): continue
            fr = precomputed[i]
            if fr.get(f_name, False):
                filtered.append(sig)
        fs = compute_stats(filtered)
        lines.append(f"  {f_name:<10} pass={fs['n']:<6} WR={fs['wr']}% avgRet={fs['avg_ret']}%")

    # Best combo
    best_by_wr = max(results, key=lambda r: r['stats']['wr'])
    bs = best_by_wr['stats']
    lines.append("")
    lines.append(f"── Best by WR ──")
    lines.append(f"  {best_by_wr['label']}: WR={bs['wr']}%, n={bs['n']}, avgRet={bs['avg_ret']}%")

    out_path = os.path.join(OUT_DIR, 'cascade_analysis.txt')
    with open(out_path, 'w') as f:
        f.write('\n'.join(lines))
    print(f"\n✅ Saved {out_path}")
    return results, raw_stats


# ── Phase 4: Capital Growth ──────────────────────────────────────────

def calc_pnl(direction, entry, exit_price, contracts, symbol):
    cfg = ALL_TICKER_CONFIGS.get(symbol, {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0})
    minstep = cfg['minstep']
    tick_rub = cfg['tick_rub']
    moves = (exit_price - entry) / minstep
    if direction.upper() == 'SHORT':
        moves = -moves
    return round(moves * tick_rub * contracts, 2)


def max_drawdown(equity):
    if not equity: return 0.0
    peak = equity[0]; mdd = 0.0
    for v in equity:
        if v > peak: peak = v
        dd = (peak - v) / peak if peak > 0 else 0
        if dd > mdd: mdd = dd
    return mdd


def simulate_cascade(signals, initial_capital, margin_usage, max_concurrent,
                     total_margin_limit, stop_loss_pct, max_dd_limit):
    """
    Walk through cascade-filtered signals sequentially with risk management.
    GO locked at entry, released at exit + PnL.
    """
    capital = float(initial_capital)
    equity = [capital]
    peak = capital
    active = {}
    trades = []

    def _total_equity():
        return capital + sum(p['locked_go'] for p in active.values())

    for sig_idx, sig in enumerate(signals):
        tk = sig.get('ticker', '')
        if not tk or tk not in ALL_TICKER_CONFIGS:
            continue

        te = _total_equity()
        dd = (peak - te) / peak if peak > 0 else 0
        if dd > max_dd_limit:
            for t in list(active.keys()):
                pos = active.pop(t)
                pnl = calc_pnl(pos['direction'], pos['entry_price'], pos['exit_price'], pos['contracts'], t)
                capital += pos['locked_go'] + pnl
            equity.append(_total_equity())
            break

        # Close existing position for this ticker
        if tk in active:
            pos = active.pop(tk)
            pnl = calc_pnl(pos['direction'], pos['entry_price'], pos['exit_price'], pos['contracts'], tk)
            capital += pos['locked_go'] + pnl
            peak = max(peak, _total_equity())
            equity.append(_total_equity())
            trades.append({'ticker': tk, 'pnl': pnl, 'contracts': pos['contracts']})

        if len(active) >= max_concurrent:
            continue

        cfg = ALL_TICKER_CONFIGS.get(tk)
        if cfg is None: continue
        go = cfg.get('go', 0)
        if go <= 0: continue

        total_cap = _total_equity()
        max_risk = total_cap * margin_usage
        contracts = int(max_risk // go) if max_risk >= go else 0
        if contracts < 1: continue

        locked_go = contracts * go
        total_locked = sum(p['locked_go'] for p in active.values())
        if total_locked + locked_go > total_cap * total_margin_limit:
            continue

        entry_price = sig.get('entry', 0)
        exit_price = sig.get('exit', 0)
        direction = sig.get('direction', 'LONG')

        # Stop-loss check
        if stop_loss_pct > 0:
            if direction == 'LONG':
                stop_price = entry_price * (1 - stop_loss_pct)
                if exit_price < stop_price:
                    exit_price = stop_price
                raw_ret = (exit_price - entry_price) / entry_price
            else:
                stop_price = entry_price * (1 + stop_loss_pct)
                if exit_price > stop_price:
                    exit_price = stop_price
                raw_ret = (entry_price - exit_price) / entry_price

        if locked_go > capital: continue
        capital -= locked_go

        active[tk] = {
            'entry_price': entry_price, 'exit_price': exit_price,
            'direction': direction, 'contracts': contracts,
            'locked_go': locked_go,
        }

    # Close remaining
    for tk in list(active.keys()):
        pos = active.pop(tk)
        pnl = calc_pnl(pos['direction'], pos['entry_price'], pos['exit_price'], pos['contracts'], tk)
        capital += pos['locked_go'] + pnl
        peak = max(peak, _total_equity())
        equity.append(_total_equity())

    return {'final_capital': round(_total_equity(), 2), 'equity': equity, 'trades': trades}


def run_phase4(all_signals, ticker_data):
    """Phase 4: Capital growth grid search with cascade filters."""
    print("\n" + "=" * 60)
    print("  PHASE 4: CAPITAL GROWTH SWEEP — CASCADE")
    print("=" * 60)

    ticker_arrays = precompute_ticker_arrays(ticker_data)
    hvn_cache = precompute_hvn(ticker_data)
    combos = build_filter_combinations(5)
    initial_capital = 100_000

    param_grid = {
        'base_margin_usage': [0.05, 0.08, 0.10, 0.15, 0.20],
        'max_concurrent': [2, 3, 5],
        'base_total_margin_limit': [0.10, 0.15, 0.20, 0.30],
        'stop_loss_pct': [0.01, 0.02, 0.03],
    }

    total = (len(param_grid['base_margin_usage']) *
             len(param_grid['max_concurrent']) *
             len(param_grid['base_total_margin_limit']) *
             len(param_grid['stop_loss_pct']) *
             len(combos))
    print(f"  Grid size: {total} combinations")

    # Precompute filter results for all signals
    print("  Precomputing filter results...")
    precomputed = []
    for sig in all_signals:
        precomputed.append(lookup_precomputed(sig, ticker_arrays, hvn_cache))

    all_results = []
    count = 0

    for combo in combos:
        combo_sigs = []
        for i, sig in enumerate(all_signals):
            if i >= len(precomputed): continue
            fr = precomputed[i]
            if all(fr.get(f, False) for f in combo):
                combo_sigs.append(sig)

        if not combo_sigs:
            continue

        for mu in param_grid['base_margin_usage']:
            for mc in param_grid['max_concurrent']:
                for tm in param_grid['base_total_margin_limit']:
                    for sl in param_grid['stop_loss_pct']:
                        for dd_limit in [0.05, 0.10, 0.15, 0.20]:
                            count += 1
                            if count % 500 == 0:
                                print(f"    Progress: {count}/{total} ({count*100//total}%)")
                            res = simulate_cascade(
                                combo_sigs, initial_capital, mu, mc, tm, sl, dd_limit
                            )
                            mdd = max_drawdown(res['equity'])
                            final_cap = res['final_capital']
                            ret_pct = (final_cap - initial_capital) / initial_capital * 100
                            n_trades = len(res['trades'])
                            calmar = ret_pct / (mdd * 100) if mdd > 0.001 else 0

                            all_results.append({
                                'filters': format_combination(combo),
                                'combo': combo,
                                'params': {
                                    'margin_usage': mu,
                                    'max_concurrent': mc,
                                    'total_margin_limit': tm,
                                    'stop_loss_pct': sl,
                                    'max_dd_limit': dd_limit,
                                },
                                'max_dd': mdd,
                                'final_capital': res['final_capital'],
                                'return_pct': ret_pct,
                                'calmar': calmar,
                                'n_trades': n_trades,
                                'n_signals': len(combo_sigs),
                            })

    print(f"    Total evaluated: {count}")

    # Find best by Calmar for each DD level
    dd_levels = [0.05, 0.10, 0.15, 0.20]
    best_per_dd = {}
    for dd_level in dd_levels:
        qualified = [r for r in all_results if r['max_dd'] <= dd_level]
        if qualified:
            best = max(qualified, key=lambda r: r['calmar'])
            best_per_dd[f"DD<={dd_level*100:.0f}%"] = best

    # Also find best +900% with DD<=15%
    target = [r for r in all_results if r['max_dd'] <= 0.15 and r['return_pct'] >= 900]
    target.sort(key=lambda r: r['calmar'], reverse=True)

    # Save Phase 4 results
    lines = []
    lines.append("=" * 70)
    lines.append("  CASCADE CAPITAL GROWTH SWEEP — Pareto Optimal")
    lines.append(f"  Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"  Initial capital: {initial_capital:,} RUB")
    lines.append(f"  Grid: mu={param_grid['base_margin_usage']}, mc={param_grid['max_concurrent']}, "
                 f"tm={param_grid['base_total_margin_limit']}, sl={param_grid['stop_loss_pct']}")
    lines.append(f"  Filters: {len(combos)} combinations")
    lines.append(f"  Total evaluated: {count}")
    lines.append("=" * 70)
    lines.append("")

    lines.append("── Best by Calmar per DD level ──")
    lines.append("")
    header = f"  {'DD Level':<15} {'Filters':<20} {'FinalCap':>10} {'Ret%':>8} {'DD%':<7} {'Calmar':<8} {'Trades':<7} {'Params'}"
    lines.append(header)
    lines.append("-" * 90)
    for label, r in best_per_dd.items():
        p = r['params']
        lines.append(f"  {label:<15} {r['filters']:<20} {r['final_capital']:>10,.0f} {r['return_pct']:>8.1f} "
                     f"{r['max_dd']*100:<7.2f} {r['calmar']:<8.2f} {r['n_trades']:<7} "
                     f"mu={p['margin_usage']} mc={p['max_concurrent']} tm={p['total_margin_limit']} sl={p['stop_loss_pct']}")

    lines.append("")
    lines.append("── Target: +900% with DD<=15% ──")
    lines.append("")
    if target:
        lines.append(f"  Found {len(target)} combinations meeting +900% @ DD<=15%")
        lines.append("")
        for i, r in enumerate(target[:10]):
            p = r['params']
            lines.append(f"  {i+1}. {r['filters']:<20} final={r['final_capital']:>10,.0f} "
                         f"ret={r['return_pct']:>8.1f}% DD={r['max_dd']*100:.2f}% "
                         f"calmar={r['calmar']:.2f} sig={r['n_signals']} trades={r['n_trades']} "
                         f"mu={p['margin_usage']} mc={p['max_concurrent']} "
                         f"tm={p['total_margin_limit']} sl={p['stop_loss_pct']}")
    else:
        # Show closest to 900%
        closest = [r for r in all_results if r['max_dd'] <= 0.15]
        closest.sort(key=lambda r: r['return_pct'], reverse=True)
        lines.append(f"  No combination hit +900%. Top by return (DD<=15%):")
        for i, r in enumerate(closest[:5]):
            p = r['params']
            lines.append(f"  {i+1}. {r['filters']:<20} final={r['final_capital']:>10,.0f} "
                         f"ret={r['return_pct']:>8.1f}% DD={r['max_dd']*100:.2f}% "
                         f"calmar={r['calmar']:.2f} sig={r['n_signals']} trades={r['n_trades']}")

    lines.append("")
    lines.append("── Top-10 by Calmar (DD<=15%) ──")
    lines.append("")
    calmar_qualified = [r for r in all_results if r['max_dd'] <= 0.15]
    calmar_qualified.sort(key=lambda r: r['calmar'], reverse=True)
    header2 = f"  {'Rank':<5} {'Filters':<20} {'FinalCap':>10} {'Ret%':>8} {'DD%':<7} {'Calmar':<8} {'Sig':<6} {'Trades':<7} {'Params'}"
    lines.append(header2)
    lines.append("-" * 100)
    for i, r in enumerate(calmar_qualified[:10]):
        p = r['params']
        lines.append(f"  {i+1:<5} {r['filters']:<20} {r['final_capital']:>10,.0f} {r['return_pct']:>8.1f} "
                     f"{r['max_dd']*100:<7.2f} {r['calmar']:<8.2f} {r['n_signals']:<6} {r['n_trades']:<7} "
                     f"mu={p['margin_usage']} mc={p['max_concurrent']} tm={p['total_margin_limit']} sl={p['stop_loss_pct']}")

    out_path = os.path.join(OUT_DIR, 'pareto_cascade.txt')
    with open(out_path, 'w') as f:
        f.write('\n'.join(lines))
    print(f"\n✅ Saved {out_path}")

    return all_results


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--phase3', action='store_true', help='Run cascade filter analysis')
    parser.add_argument('--phase4', action='store_true', help='Run capital growth sweep')
    parser.add_argument('--all', action='store_true', help='Run all phases')
    args = parser.parse_args()

    if not any([args.phase3, args.phase4, args.all]):
        parser.print_help()
        sys.exit(1)

    print("=" * 60)
    print("  CASCADE SWEEP — Data Collection")
    print("=" * 60)
    all_signals, ticker_data = collect_all_oi_signals()

    if args.phase3 or args.all:
        run_phase3(all_signals, ticker_data)

    if args.phase4 or args.all:
        run_phase4(all_signals, ticker_data)
