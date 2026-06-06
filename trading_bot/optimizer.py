"""
Walk-forward оптимизатор с ADX для HS/KC/DX/HY.

Алгоритм:
1. Загрузить ~2 года данных (500 дней)
2. Разделить: первые 70% train, последние 30% test
3. Grid search по vol_thresh, div_thresh (vol_surge), horizon
4. Для каждой комбинации: detect_signals → ADX filter → метрики
5. Фильтр: n>=20, WR>=55%, PF>=1.3
6. Score = WR * PF / DD * 10
7. Top-3 на train → валидация на test
8. Сохранить в optimized_configs.json
"""

import json
import os
from typing import List, Dict

from . import StrategyConfig, TICKERS, SCAN_SYMBOLS, DB_CREDENTIALS, DEFAULT_CONFIG
from .engine import detect_signals
from .filters import add_regime_filter, calc_adx
from .scanner import load_data


# ── metrics ────────────────────────────────────────────────────────────────────


def compute_metrics(signals: List[dict]) -> dict:
    """Рассчитать метрики по списку сигналов."""
    if not signals:
        return {'n': 0, 'wr': 0, 'pf': 0, 'dd': 0, 'avg_ret': 0}
    returns = [s['return_pct'] for s in signals]
    n = len(returns)
    wins = sum(1 for r in returns if r > 0)
    wr = wins / n * 100
    gains = sum(r for r in returns if r > 0)
    losses = abs(sum(r for r in returns if r < 0))
    pf = gains / losses if losses > 0 else (99.9 if gains > 0 else 0)
    cum = 0
    peak = 0
    dd = 0
    for r in returns:
        cum += r
        if cum > peak:
            peak = cum
        d = peak - cum
        if d > dd:
            dd = d
    return {
        'n': n,
        'wr': round(wr, 1),
        'pf': round(pf, 2),
        'dd': round(dd, 1),
        'avg_ret': round(sum(returns) / n, 3),
    }


# ── grid search ────────────────────────────────────────────────────────────────


def run_grid(
    symbol: str,
    rows: list,
    close_prices: list,
    strategy: str,
    params_grid: List[dict],
) -> List[Dict]:
    """
    Запустить grid search по заданным параметрам.

    Для каждой комбинации:
        detect_signals → add_regime_filter (ADX>20) → compute_metrics
    Возвращает список словарей с результатами, отсортированный по Score (убыв).
    """
    results = []
    for params in params_grid:
        config: StrategyConfig = {
            'strategy': strategy,
            'vol_thresh': params['vol_thresh'],
            'horizon': params['horizon'],
        }
        if strategy == 'vol_surge':
            config['div_thresh'] = params['div_thresh']

        # Сигналы
        signals = detect_signals(rows, config)
        if not signals:
            continue

        # ADX фильтр
        signal_indices = [sig['idx'] for sig in signals]
        filtered = add_regime_filter(
            signals, close_prices, signal_indices, adx_threshold=20
        )
        if not filtered:
            continue

        # Метрики
        m = compute_metrics(filtered)
        if m['n'] < 20 or m['wr'] < 55.0 or m['pf'] < 1.3:
            continue

        # Score
        dd = m['dd'] if m['dd'] > 0 else 1.0
        score = round(m['wr'] * m['pf'] / dd * 10, 1)

        results.append({
            **params,
            'metrics': m,
            'score': score,
        })

    # Сортировка по score убыванию
    results.sort(key=lambda r: r['score'], reverse=True)
    return results


def format_result(symbol: str, label: str, result: dict) -> str:
    """Форматировать один результат для вывода."""
    m = result['metrics']
    vol = result.get('vol_thresh', '?')
    div = result.get('div_thresh', '-')
    h = result.get('horizon', '?')
    return (
        f"vol={vol} div={div} h={h}: "
        f"n={m['n']} WR={m['wr']}% PF={m['pf']} DD={m['dd']}% Score={result['score']}"
    )


# ── main ──────────────────────────────────────────────────────────────────────


def main():
    symbols = ['HS', 'KC', 'DX', 'HY']
    output_lines = []
    all_optimized = {}

    for symbol in symbols:
        ticker_cfg = TICKERS.get(symbol, {})
        strategy = ticker_cfg.get('strategy', 'vol_surge')

        output_lines.append(f"\n{'='*50}")
        output_lines.append(f"=== {symbol} (strategy={strategy}) ===")
        output_lines.append('=' * 50)

        # 1. Загрузка данных (~2 года = 500 дней для 5-минуток)
        print(f"[{symbol}] Loading data (500 days)...")
        rows = load_data(symbol, days=500)
        if len(rows) < 100:
            output_lines.append(f"[WARN] {symbol}: only {len(rows)} rows, skipping")
            continue
        print(f"[{symbol}] Loaded {len(rows)} rows")

        close_prices = [r[5] for r in rows]  # close is index 5

        # 2. Разделение 70/30
        split = int(len(rows) * 0.7)
        rows_train = rows[:split]
        rows_test = rows[split:]
        close_train = close_prices[:split]
        close_test = close_prices[split:]

        output_lines.append(
            f"   Train: {len(rows_train)} rows, Test: {len(rows_test)} rows"
        )

        # 3. Grid параметров
        vol_thresholds = [1.5, 2.0, 2.5, 3.0]
        horizons = [3, 6, 12, 24]

        if strategy == 'vol_surge':
            div_thresholds = [1.0, 1.5, 2.0]
            params_grid = [
                {'vol_thresh': v, 'div_thresh': d, 'horizon': h}
                for v in vol_thresholds
                for d in div_thresholds
                for h in horizons
            ]
        else:  # yur_dom — без div_thresh
            params_grid = [
                {'vol_thresh': v, 'horizon': h}
                for v in vol_thresholds
                for h in horizons
            ]

        # 4. Grid search на train
        print(f"[{symbol}] Running grid search on train ({len(params_grid)} combinations)...")
        train_results = run_grid(symbol, rows_train, close_train, strategy, params_grid)

        if not train_results:
            output_lines.append("   No valid configs on train")
            all_optimized[symbol] = []
            continue

        # 5. Top-3
        top3 = train_results[:3]

        output_lines.append(f"\n--- {symbol} (train) ---")
        for i, r in enumerate(top3):
            output_lines.append(f"{i+1}. {format_result(symbol, 'train', r)}")

        # 6. Валидация на test
        output_lines.append(f"\n--- {symbol} (test) ---")
        test_validated = []
        for r in top3:
            config: StrategyConfig = {
                'strategy': strategy,
                'vol_thresh': r['vol_thresh'],
                'horizon': r['horizon'],
            }
            if strategy == 'vol_surge':
                config['div_thresh'] = r['div_thresh']

            signals_test = detect_signals(rows_test, config)
            if signals_test:
                signal_indices_test = [sig['idx'] for sig in signals_test]
                filtered_test = add_regime_filter(
                    signals_test, close_test, signal_indices_test, adx_threshold=20
                )
                m_test = compute_metrics(filtered_test)
                dd = m_test['dd'] if m_test['dd'] > 0 else 1.0
                score_test = round(m_test['wr'] * m_test['pf'] / dd * 10, 1) if m_test['n'] > 0 else 0
            else:
                m_test = {'n': 0, 'wr': 0, 'pf': 0, 'dd': 0, 'avg_ret': 0}
                score_test = 0

            test_entry = {
                'vol_thresh': r['vol_thresh'],
                'horizon': r['horizon'],
                'metrics': m_test,
                'score': score_test,
            }
            if strategy == 'vol_surge':
                test_entry['div_thresh'] = r['div_thresh']
            test_validated.append(test_entry)

            output_lines.append(
                f"{format_result(symbol, 'test', {**r, 'metrics': m_test, 'score': score_test})}"
            )

        # Сохраняем
        all_optimized[symbol] = {
            'strategy': strategy,
            'train': [
                {
                    'vol_thresh': r['vol_thresh'],
                    'horizon': r['horizon'],
                    'metrics': r['metrics'],
                    'score': r['score'],
                }
                | ({'div_thresh': r['div_thresh']} if strategy == 'vol_surge' else {})
                for r in top3
            ],
            'test': test_validated,
        }

    # Вывод
    print('\n'.join(output_lines))

    # Сохранение
    output_path = os.path.join(os.path.dirname(__file__), 'optimized_configs.json')
    with open(output_path, 'w') as f:
        json.dump(all_optimized, f, indent=2, ensure_ascii=False)
    print(f"\nSaved optimized configs to {output_path}")


if __name__ == '__main__':
    main()
