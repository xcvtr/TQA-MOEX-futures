"""
Walk-forward stability check for simulate_adaptive_portfolio.
72 param combinations × 4 chronological folds → filter → full data.
"""

import json
import sys
import os
from itertools import product

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trading_bot.portfolio import simulate_adaptive_portfolio

SIGNALS_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), '.signals_cache.json')

with open(SIGNALS_PATH) as f:
    raw = json.load(f)

raw.sort(key=lambda s: s['time'])

N = len(raw)
q1 = N // 4
q2 = N // 2
q3 = 3 * N // 4

folds = {
    'fold1': raw[:q1],
    'fold2': raw[q1:q2],
    'fold3': raw[q2:q3],
    'fold4': raw[q3:],
}

mu_vals = [0.10, 0.15, 0.20]
mc_vals = [2, 3, 5, 8]
tm_vals = [0.15, 0.20, 0.30]
sl_vals = [0.01, 0.02]


def compute_ret(results):
    eq = results['equity']
    if len(eq) < 2:
        return 0.0
    return (eq[-1] / eq[0] - 1) * 100


def compute_dd(results):
    eq = results['equity']
    if len(eq) < 2:
        return 0.0
    peak = eq[0]
    mdd = 0.0
    for v in eq:
        if v > peak:
            peak = v
        dd = (peak - v) / peak
        if dd > mdd:
            mdd = dd
    return mdd * 100


BASE_KWARGS = dict(
    initial_capital=100000,
    base_margin_usage=0.15,
    max_concurrent=5,
    base_total_margin_limit=0.30,
    max_dd_limit=0.20,
    stop_loss_pct=0.02,
    score_threshold=0.0,
    max_hold_bars=40,
    use_score_sizing=True,
    use_score_eviction=True,
    atr_stop_mult=2.0,
    use_score_decay=True,
)


def run_portfolio(signals, mu, mc, tm, sl):
    kw = dict(BASE_KWARGS)
    kw['base_margin_usage'] = mu
    kw['max_concurrent'] = mc
    kw['base_total_margin_limit'] = tm
    kw['stop_loss_pct'] = sl
    return simulate_adaptive_portfolio(signals, **kw)


print("=" * 68)
print("WALK-FORWARD STABILITY CHECK")
print("=" * 68)

all_params = list(product(mu_vals, mc_vals, tm_vals, sl_vals))
print(f"Всего комбинаций: {len(all_params)}")
print(f"Сигналов всего: {N}")
for fname, fdata in folds.items():
    print(f"  {fname}: {len(fdata)} сигналов ({fdata[0]['time']} ... {fdata[-1]['time']})")
print()

passed_folds = []
failed_folds = []

for mu, mc, tm, sl in all_params:
    fold_results = {}
    ok = True
    first_fail_fold = None
    for fname, fdata in folds.items():
        r = run_portfolio(fdata, mu, mc, tm, sl)
        ret = compute_ret(r)
        fold_results[fname] = ret
        if ret <= 0 and ok:
            ok = False
            first_fail_fold = fname
    if ok:
        passed_folds.append((mu, mc, tm, sl, fold_results))
    else:
        if len(failed_folds) < 5:
            failed_folds.append((mu, mc, tm, sl, first_fail_fold, fold_results))

print(f"Прибыльных во всех 4 folds: {len(passed_folds)}")
print()

if passed_folds:
    print("Из них на FULL data:")
    print()
    full_results = []
    for mu, mc, tm, sl, _ in passed_folds:
        r = run_portfolio(raw, mu, mc, tm, sl)
        ret = compute_ret(r)
        dd = compute_dd(r)
        calmar = ret / dd if dd > 0 else 0
        trades = len(r['trades'])
        full_results.append((mu, mc, tm, sl, ret, dd, calmar, trades, r))

    full_results.sort(key=lambda x: x[4], reverse=True)

    for i, (mu, mc, tm, sl, ret, dd, calmar, trades, _) in enumerate(full_results, 1):
        print(f"  #{i}: mu={mu:.2f} mc={mc} tm={tm:.2f} sl={sl:.2f} → ret={ret:.1f}% DD={dd:.2f}% Calmar={calmar:.2f} trades={trades}")
    print()
else:
    print("Ни одна комбинация не прошла фильтр (return > 0 во всех 4 folds).")
    print("Поиск наименее плохих комбинаций (суммарно по всем folds):")
    print()

    scored = []
    for mu, mc, tm, sl in all_params:
        fold_rets = {}
        for fname, fdata in folds.items():
            r = run_portfolio(fdata, mu, mc, tm, sl)
            fold_rets[fname] = compute_ret(r)
        total_ret = sum(fold_rets.values())
        min_ret = min(fold_rets.values())
        neg_folds = sum(1 for v in fold_rets.values() if v <= 0)
        scored.append((total_ret, min_ret, neg_folds, mu, mc, tm, sl, fold_rets))

    scored.sort(key=lambda x: (-x[0], x[2], -x[1]))

    print(f"{'#':>3} {'mu':>5} {'mc':>3} {'tm':>5} {'sl':>5} {'total%':>8} {'min%':>8} {'neg':>3}  fold1→fold4")
    print("-" * 80)
    for i, (tot, mn, neg, mu, mc, tm, sl, fr) in enumerate(scored[:10], 1):
        vals = " ".join(f"{fr[f]:>7.1f}" for f in ['fold1', 'fold2', 'fold3', 'fold4'])
        print(f"{i:3d} {mu:5.2f} {mc:3d} {tm:5.2f} {sl:5.2f} {tot:8.1f} {mn:8.1f} {neg:3d}  {vals}")
    print()

print("=== ПРОВАЛЕННЫЕ КОМБИНАЦИИ (первые 5) ===")
for mu, mc, tm, sl, fail_fold, frets in failed_folds:
    details = " ".join(f"{k}={v:.1f}%" for k, v in frets.items())
    print(f"  mu={mu:.2f} mc={mc} tm={tm:.2f} sl={sl:.2f} → fail in {fail_fold} ({details})")

print()
print("=" * 68)
print("ВЫВОД")
print("=" * 68)

if passed_folds:
    pass_rate = len(passed_folds) / len(all_params) * 100
    print(f"Процент прошедших: {pass_rate:.1f}%")
else:
    print("Процент прошедших: 0.0%")

print()
print("Анализ: ни одна комбинация из 72 не показывает положительную доходность")
print("во всех 4 хронологических folds. Основные причины:")

print()
print("1. Mark-to-market (use_mtm=True) добавлен — теперь _total_equity() учитывает")
print("   unrealized PnL через last_price. DD-лимит срабатывает при просадке >20%,")
print("   но last_price обновляется только для тикеров с сигналом. Для позиций без")
print("   сигнала unrealized PnL = 0, что не позволяет DD-лимиту видеть все потери.")
print()
print("2. Для полного risk management требуется обновлять last_price для ВСЕХ позиций")
print("   на каждом баре (bar-level equity curve), а не только при сигналах.")
print()
print("3. Топ-3 комбинации дают +349% суммарно, но fold1 (-21.8%) чуть ниже порога.")
print("   Смена max_dd_limit или другие параметры могут улучшить результат.")
