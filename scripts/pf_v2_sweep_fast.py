#!/usr/bin/env python3
"""Fast PF v2 sweep — loads signals from cache, runs portfolio simulations only."""
import json, os, sys, time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from trading_bot.portfolio import simulate_adaptive_portfolio
from scripts.portfolio_sweep import max_drawdown as calc_mdd

CACHE_PATH = os.path.join(PROJECT_ROOT, '.signals_cache.json')
OUT_DIR = os.path.join(PROJECT_ROOT, 'docs', 'plans', 'strategy_v3')
os.makedirs(OUT_DIR, exist_ok=True)

print("Loading signals from cache...", flush=True)
t0 = time.time()
all_signals = json.load(open(CACHE_PATH))
print(f"Loaded {len(all_signals)} signals in {time.time()-t0:.1f}s", flush=True)

initial_capital = 100_000

# Test ONE combo first (the best FIFO params)
print("\n--- Single combo test ---", flush=True)
res = simulate_adaptive_portfolio(
    all_signals, initial_capital,
    base_margin_usage=0.1, max_concurrent=2,
    base_total_margin_limit=0.15, max_dd_limit=0.20,
    stop_loss_pct=0.01, score_threshold=0.0,
    use_score_sizing=True, use_score_eviction=True,
    atr_stop_mult=2.0, use_score_decay=True,
)
mdd = calc_mdd(res['equity'])
ret = (res['final_capital'] - initial_capital) / initial_capital * 100
calmar = ret / (mdd * 100) if mdd > 0.001 else 0
print(f"mu=0.1 mc=2 tm=0.15 sl=0.01: ret={ret:.1f}% DD={mdd*100:.2f}% Calmar={calmar:.2f} trades={len(res['trades'])}", flush=True)
print(f"  Equity: min={min(res['equity']):.0f} max={max(res['equity']):.0f} final={res['final_capital']:.0f}", flush=True)

# Test WITHOUT score decay (it's broken with threshold=0)
res2 = simulate_adaptive_portfolio(
    all_signals, initial_capital,
    base_margin_usage=0.1, max_concurrent=2,
    base_total_margin_limit=0.15, max_dd_limit=0.20,
    stop_loss_pct=0.01, score_threshold=0.0,
    use_score_sizing=False, use_score_eviction=False,
    atr_stop_mult=0.0, use_score_decay=False,
)
mdd2 = calc_mdd(res2['equity'])
ret2 = (res2['final_capital'] - initial_capital) / initial_capital * 100
calmar2 = ret2 / (mdd2 * 100) if mdd2 > 0.001 else 0
print(f"NO v2 (plain PF): ret={ret2:.1f}% DD={mdd2*100:.2f}% Calmar={calmar2:.2f} trades={len(res2['trades'])}", flush=True)

# Test each feature individually
for label, kwargs in [
    ("score_sizing",  dict(use_score_sizing=True, use_score_eviction=False, atr_stop_mult=0.0, use_score_decay=False)),
    ("score_eviction", dict(use_score_sizing=False, use_score_eviction=True, atr_stop_mult=0.0, use_score_decay=False)),
    ("atr_stop=2.0",  dict(use_score_sizing=False, use_score_eviction=False, atr_stop_mult=2.0, use_score_decay=False)),
    ("ALL v2",        dict(use_score_sizing=True, use_score_eviction=True, atr_stop_mult=2.0, use_score_decay=True)),
]:
    r = simulate_adaptive_portfolio(all_signals, initial_capital,
        base_margin_usage=0.1, max_concurrent=2,
        base_total_margin_limit=0.15, max_dd_limit=0.20,
        stop_loss_pct=0.01, score_threshold=0.0, **kwargs)
    md = calc_mdd(r['equity'])
    rt = (r['final_capital'] - initial_capital) / initial_capital * 100
    ca = rt / (md * 100) if md > 0.001 else 0
    print(f"  {label:15s}: ret={rt:>7.1f}% DD={md*100:.2f}% Calmar={ca:.2f} trades={len(r['trades'])}", flush=True)

# Quick grid: vary mu and mc (fixed tm=0.15, sl=0.01, all v2 features)
print("\n--- Quick grid (mu x mc, tm=0.15, sl=0.01, ALL v2) ---", flush=True)
print(f"  {'mu':<5} {'mc':<4} {'Ret%':>7} {'DD%':<8} {'Calmar':<8} {'Trades':<7}", flush=True)
print(f"  {'-'*39}", flush=True)
for mu in [0.10, 0.15, 0.20]:
    for mc in [2, 3, 5, 8]:
        r = simulate_adaptive_portfolio(all_signals, initial_capital,
            base_margin_usage=mu, max_concurrent=mc,
            base_total_margin_limit=0.15, max_dd_limit=0.20,
            stop_loss_pct=0.01, score_threshold=0.0,
            use_score_sizing=True, use_score_eviction=True,
            atr_stop_mult=2.0, use_score_decay=True)
        md = calc_mdd(r['equity'])
        rt = (r['final_capital'] - initial_capital) / initial_capital * 100
        ca = rt / (md * 100) if md > 0.001 else 0
        print(f"  {mu:<5.2f} {mc:<4} {rt:>7.1f} {md*100:<8.2f} {ca:<8.2f} {len(r['trades']):<7}", flush=True)
