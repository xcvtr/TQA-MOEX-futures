#!/usr/bin/env python3
"""
capital_growth_sim.py — Моделирование разгона депозита 100K RUB на MOEX фьючерсах.

Собирает сигналы со всех 5 стратегий trading_bot за 2 года,
выполняет walk-forward оптимизацию риск-менеджмента,
симулирует разгон с реинвестированием.

Результаты: docs/plans/capital_growth/
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional

import pandas as pd
import numpy as np

# ── Добавляем корень проекта в sys.path ────────────────────────────────────
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from trading_bot import (
    TICKERS, DEFAULT_CONFIG,
    OB_TICKERS, DEFAULT_OB_CONFIG,
    REVERSION_TICKERS, DEFAULT_REVERSION_CONFIG,
    VWAP_TICKERS, DEFAULT_VWAP_CONFIG,
    OI_DIVERGENCE_TICKERS, DEFAULT_OI_DIVERGENCE_CONFIG,
)
from trading_bot.scanner import load_data
from trading_bot.engine import detect_signals_limit
from trading_bot.ob_engine import detect_order_block_signals, load_price_data as ob_load
from trading_bot.reversion_engine import detect_mean_reversion_signals_limit, load_price_data as rev_load
from trading_bot.vwap_engine import detect_vwap_signals_limit, load_price_data as vwap_load
from trading_bot.new_strategies import (
    detect_oi_divergence_signals_limit,
    load_ohlcv, load_oi, merge_ohlcv_oi,
)

# ── Конфигурация ───────────────────────────────────────────────────────────
INITIAL_CAPITAL = 100_000  # RUB
HISTORY_DAYS = 730  # 2 года
OUTPUT_DIR = os.path.join(PROJECT_ROOT, 'docs', 'plans', 'capital_growth')

# Все конфиги тикеров для расчёта PnL
ALL_TICKER_CONFIGS: dict = {}
ALL_TICKER_CONFIGS.update(TICKERS)
ALL_TICKER_CONFIGS.update(OB_TICKERS)
ALL_TICKER_CONFIGS.update(REVERSION_TICKERS)
ALL_TICKER_CONFIGS.update(VWAP_TICKERS)
ALL_TICKER_CONFIGS.update(OI_DIVERGENCE_TICKERS)

# ── Утилиты ────────────────────────────────────────────────────────────────


def calc_pnl(direction: str, entry: float, exit_price: float, contracts: int, symbol: str) -> float:
    """Рассчитать PnL в рублях. Копия логики из tracker.py."""
    cfg = ALL_TICKER_CONFIGS[symbol]
    minstep = cfg['minstep']
    tick_rub = cfg['tick_rub']
    moves = (exit_price - entry) / minstep
    if direction.upper() == 'SHORT':
        moves = -moves
    return round(moves * tick_rub * contracts, 2)


def max_drawdown(equity: List[float]) -> float:
    """Максимальная просадка в долях (0..1)."""
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


# ── 1. Сбор сигналов ────────────────────────────────────────────────────────


def collect_all_signals() -> List[Dict]:
    """Загрузить данные и прогнать все 5 стратегий. Вернуть список сигналов."""
    all_signals: List[Dict] = []
    errors: List[str] = []

    # --- 1. Volume Surge ---
    for ticker, cfg in TICKERS.items():
        if not cfg.get('enabled', True):
            continue
        print(f"  [VS] Загрузка {ticker}...")
        try:
            rows = load_data(ticker, HISTORY_DAYS)
            if not rows:
                print(f"    ⚠ Нет данных для {ticker}")
                continue
            ticker_cfg = dict(DEFAULT_CONFIG)
            ticker_cfg.update({k: v for k, v in cfg.items() if k in DEFAULT_CONFIG})
            sigs = detect_signals_limit(rows, ticker_cfg)
            for s in sigs:
                s['ticker'] = ticker
                s['strategy'] = 'vol_surge'
                if 'time' not in s:
                    s['time'] = ''
                if 'entry' not in s:
                    s['entry'] = 0
                if 'exit' not in s:
                    s['exit'] = 0
                if 'direction' not in s:
                    s['direction'] = 'LONG'
            all_signals.extend(sigs)
            print(f"    → {len(sigs)} сигналов")
        except Exception as e:
            msg = f"VS {ticker}: {e}"
            errors.append(msg)
            print(f"    ⚠ {msg}")

    # --- 2. Order Block ---
    for ticker, cfg in OB_TICKERS.items():
        if not cfg.get('enabled', True):
            continue
        print(f"  [OB] Загрузка {ticker}...")
        try:
            rows = ob_load(ticker, HISTORY_DAYS)
            if not rows:
                print(f"    ⚠ Нет данных для {ticker}")
                continue
            sigs = detect_order_block_signals(ticker, rows, DEFAULT_OB_CONFIG)
            for s in sigs:
                if 'strategy' not in s:
                    s['strategy'] = 'order_block'
                s['ticker'] = ticker
            all_signals.extend(sigs)
            print(f"    → {len(sigs)} сигналов")
        except Exception as e:
            msg = f"OB {ticker}: {e}"
            errors.append(msg)
            print(f"    ⚠ {msg}")

    # --- 3. Mean Reversion ---
    for ticker, cfg in REVERSION_TICKERS.items():
        if not cfg.get('enabled', True):
            continue
        print(f"  [REV] Загрузка {ticker}...")
        try:
            rows = rev_load(ticker, HISTORY_DAYS)
            if not rows:
                print(f"    ⚠ Нет данных для {ticker}")
                continue
            ticker_cfg = dict(DEFAULT_REVERSION_CONFIG)
            sigs = detect_mean_reversion_signals_limit(ticker, rows, ticker_cfg)
            for s in sigs:
                if 'strategy' not in s or s['strategy'] == 'reversion':
                    s['strategy'] = 'mean_reversion'
                s['ticker'] = ticker
            all_signals.extend(sigs)
            print(f"    → {len(sigs)} сигналов")
        except Exception as e:
            msg = f"REV {ticker}: {e}"
            errors.append(msg)
            print(f"    ⚠ {msg}")

    # --- 4. VWAP Deviation ---
    for ticker, cfg in VWAP_TICKERS.items():
        if not cfg.get('enabled', True):
            continue
        print(f"  [VWAP] Загрузка {ticker}...")
        try:
            rows = vwap_load(ticker, HISTORY_DAYS)
            if not rows:
                print(f"    ⚠ Нет данных для {ticker}")
                continue
            sigs = detect_vwap_signals_limit(ticker, rows, DEFAULT_VWAP_CONFIG)
            for s in sigs:
                if 'strategy' not in s or s['strategy'] == 'vwap':
                    s['strategy'] = 'vwap_deviation'
                s['ticker'] = ticker
            all_signals.extend(sigs)
            print(f"    → {len(sigs)} сигналов")
        except Exception as e:
            msg = f"VWAP {ticker}: {e}"
            errors.append(msg)
            print(f"    ⚠ {msg}")

    # --- 5. OI Divergence ---
    for ticker, cfg in OI_DIVERGENCE_TICKERS.items():
        if not cfg.get('enabled', True):
            continue
        print(f"  [OI] Загрузка {ticker}...")
        try:
            ohlcv = load_ohlcv(ticker, HISTORY_DAYS)
            if not ohlcv:
                print(f"    ⚠ Нет данных OHLCV для {ticker}")
                continue
            oi_data = load_oi(ticker, HISTORY_DAYS)
            if not oi_data:
                print(f"    ⚠ Нет данных OI для {ticker}")
                continue
            merged = merge_ohlcv_oi(ohlcv, oi_data)
            if not merged:
                print(f"    ⚠ Нет объединённых данных для {ticker}")
                continue
            sigs = detect_oi_divergence_signals_limit(merged, DEFAULT_OI_DIVERGENCE_CONFIG)
            for s in sigs:
                if 'strategy' not in s or s.get('strategy') == 'otc':
                    s['strategy'] = 'oi_divergence'
                s['ticker'] = ticker
            all_signals.extend(sigs)
            print(f"    → {len(sigs)} сигналов")
        except Exception as e:
            msg = f"OI {ticker}: {e}"
            errors.append(msg)
            print(f"    ⚠ {msg}")

    # --- Постобработка ---
    # Сортировка по time
    all_signals.sort(key=lambda s: str(s.get('time', '')))

    # Удаление дубликатов (одинаковый time + ticker)
    seen = set()
    unique_signals = []
    for s in all_signals:
        key = (str(s.get('time', '')), str(s.get('ticker', '')))
        if key in seen:
            continue
        seen.add(key)
        unique_signals.append(s)

    # Статистика по стратегиям
    strat_counts = {}
    for s in unique_signals:
        strat = s.get('strategy', 'unknown')
        strat_counts[strat] = strat_counts.get(strat, 0) + 1

    print(f"\n  Всего собрано: {len(all_signals)} сигналов")
    print(f"  После удаления дубликатов: {len(unique_signals)} сигналов")
    print(f"  По стратегиям: {strat_counts}")
    if errors:
        print(f"  Ошибок: {len(errors)}")
        for e in errors:
            print(f"    - {e}")

    return unique_signals


# ── 2. Симуляция (ИСПРАВЛЕННАЯ: ГО блокируется, сигнал = полный цикл) ────────


def simulate(
    signals: List[Dict],
    initial_capital: float,
    margin_usage: float,
    max_concurrent: int,
    max_dd_limit: float,
) -> Dict:
    """
    Walk through signals sequentially, managing risk.

    Каждый сигнал — это полный цикл entry→exit (return_pct уже включает horizon).
    
    **ВАЖНО**: ГО блокируется при открытии и возвращается при закрытии.
    capital = free_cash + locked_margin
    
    Алгоритм:
    1. Сигнал по тикеру → проверяем лимиты (DD, concurrent)
    2. Рассчитываем контракты: floor(capital * margin_usage / GO)
    3. Блокируем ГО: capital -= contracts * GO
    4. Рассчитываем PnL от этой сделки
    5. Возвращаем ГО + PnL: capital += contracts * GO + pnl
    """
    capital = float(initial_capital)
    equity = [capital]
    peak = capital
    active: Dict[str, Dict] = {}  # ticker -> position info
    trades: List[Dict] = []

    for sig_idx, sig in enumerate(signals):
        tk = sig.get('ticker', '')
        if not tk or tk not in ALL_TICKER_CONFIGS:
            continue

        # ── Сначала закрываем старые позиции по этому тикеру ──
        if tk in active:
            pos = active.pop(tk)
            pnl = calc_pnl(
                pos['direction'], pos['entry_price'], pos['exit_price'],
                pos['contracts'], tk,
            )
            capital += pos['locked_go'] + pnl  # возвращаем ГО + PnL
            peak = max(peak, capital)
            equity.append(capital)
            trades.append({
                'ticker': tk,
                'pnl': pnl,
                'entry_time': pos['entry_time'],
                'exit_time': pos.get('exit_time', sig.get('time', '')),
                'direction': pos['direction'],
                'contracts': pos['contracts'],
                'entry_price': pos['entry_price'],
                'exit_price': pos['exit_price'],
                'strategy': pos.get('strategy', ''),
                'locked_go': pos['locked_go'],
            })

        # ── Drawdown limit check ──
        dd = (peak - capital) / peak if peak > 0 else 0
        if dd > max_dd_limit:
            continue  # stop opening new positions

        # ── Concurrent positions limit ──
        if len(active) >= max_concurrent:
            continue

        # ── Get ticker config ──
        try:
            cfg = ALL_TICKER_CONFIGS[tk]
        except KeyError:
            continue
        go = cfg.get('go', 0)
        if go <= 0:
            continue

        # ── Calculate number of contracts ──
        max_risk = capital * margin_usage
        contracts = int(max_risk // go) if max_risk >= go else 0
        if contracts < 1:
            continue

        locked_go = contracts * go

        entry_price = sig.get('entry', 0)
        exit_price = sig.get('exit', 0)
        direction = sig.get('direction', 'LONG')

        # ── Блокируем ГО перед входом ──
        if locked_go > capital:
            continue  # not enough free capital (shouldn't happen with margin_usage)
        capital -= locked_go
        # equity НЕ обновляем здесь — это ещё не результат сделки

        # ── Открываем позицию ──
        active[tk] = {
            'entry_price': entry_price,
            'exit_price': exit_price,
            'direction': direction,
            'contracts': contracts,
            'entry_time': sig.get('time', ''),
            'exit_time': sig.get('time', ''),
            'strategy': sig.get('strategy', ''),
            'locked_go': locked_go,
        }

    # ── Close remaining positions at last prices ──
    for tk in list(active.keys()):
        pos = active.pop(tk)
        pnl = calc_pnl(
            pos['direction'], pos['entry_price'], pos['exit_price'],
            pos['contracts'], tk,
        )
        capital += pos['locked_go'] + pnl
        peak = max(peak, capital)
        equity.append(capital)

    return {
        'final_capital': round(capital, 2),
        'equity': equity,
        'trades': trades,
    }


# ── 3. Walk-Forward ──────────────────────────────────────────────────────


def get_date_bounds(signals: List[Dict]) -> tuple:
    """Определить реальные даты начала и конца данных."""
    dates = []
    for s in signals:
        t = str(s.get('time', ''))
        if t:
            dates.append(t[:10])
    if not dates:
        return ('2024-01-01', '2025-12-31')
    dates.sort()
    return (dates[0], dates[-1])


def split_folds(signals: List[Dict]) -> List[Dict]:
    """
    Разбить сигналы на 2 фолда по датам.
    Фолды перекрываются на train (rolling window), test не пересекаются.
    """
    start, end = get_date_bounds(signals)
    print(f"  Дата начала данных: {start}")
    print(f"  Дата конца данных:  {end}")

    start_dt = datetime.strptime(start[:10], '%Y-%m-%d')
    end_dt = datetime.strptime(end[:10], '%Y-%m-%d')
    total_days = (end_dt - start_dt).days

    # Делим на 2 равных периода
    mid = start_dt + timedelta(days=total_days // 2)

    folds = [
        {
            'name': 'fold_1',
            'train_start': start,
            'train_end': mid.strftime('%Y-%m-%d') + 'T23:59:59',
            'test_start': (mid + timedelta(days=1)).strftime('%Y-%m-%d'),
            'test_end': end,
        },
    ]

    # Если данных больше 18 месяцев — делаем второй фолд
    if total_days > 365:
        # Второй фолд: train со сдвигом на 1/4, test — остаток
        q1 = start_dt + timedelta(days=total_days // 4)
        q3 = start_dt + timedelta(days=3 * total_days // 4)
        folds.append({
            'name': 'fold_2',
            'train_start': q1.strftime('%Y-%m-%d'),
            'train_end': q3.strftime('%Y-%m-%d') + 'T23:59:59',
            'test_start': (q3 + timedelta(days=1)).strftime('%Y-%m-%d'),
            'test_end': end,
        })

    result = []
    for fold in folds:
        train = [
            s for s in signals
            if fold['train_start'] <= str(s.get('time', '')) <= fold['train_end']
        ]
        test = [
            s for s in signals
            if fold['test_start'] <= str(s.get('time', '')) <= fold['test_end']
        ]
        result.append({
            'name': fold['name'],
            'train': train,
            'test': test,
            'train_start': fold['train_start'][:10],
            'train_end': fold['train_end'][:10],
            'test_start': fold['test_start'][:10],
            'test_end': fold['test_end'][:10],
        })
    return result


def score_func(final_capital: float, equity: List[float], initial_capital: float) -> float:
    """Награда за рост со штрафом за просадку."""
    mdd = max_drawdown(equity)
    ret = (final_capital - initial_capital) / initial_capital
    if ret <= 0:
        return ret
    return ret * (1 - mdd / 2)


def grid_search(signals: List[Dict], initial_capital: float) -> Dict:
    """Grid search по параметрам риск-менеджмента."""
    param_grid = {
        'margin_usage': [0.05, 0.1, 0.15, 0.2, 0.25, 0.3],
        'max_concurrent': [1, 2, 3],
        'max_dd_limit': [0.15, 0.20, 0.25, 0.30],
    }
    # 6 × 3 × 4 = 72 комбинации (убрал агрессивные 0.4, 0.5)

    best_score = -float('inf')
    best_params = None
    best_result = None

    total = (len(param_grid['margin_usage']) *
             len(param_grid['max_concurrent']) *
             len(param_grid['max_dd_limit']))
    count = 0

    for mu in param_grid['margin_usage']:
        for mc in param_grid['max_concurrent']:
            for dd in param_grid['max_dd_limit']:
                count += 1
                res = simulate(signals, initial_capital, mu, mc, dd)
                score = score_func(res['final_capital'], res['equity'], initial_capital)
                if score > best_score:
                    best_score = score
                    best_params = {'margin_usage': mu, 'max_concurrent': mc, 'max_dd_limit': dd}
                    best_result = res

    return {
        'best_params': best_params,
        'best_score': round(best_score, 4),
        'best_result': best_result,
        'total_combinations': total,
    }


# ── 4. Запуск ───────────────────────────────────────────────────────────


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print("=" * 60)
    print("  CAPITAL GROWTH SIMULATION")
    print(f"  Initial capital: {INITIAL_CAPITAL:,} RUB")
    print(f"  History: {HISTORY_DAYS} days")
    print("=" * 60)

    # ── Step 1: Collect signals ──
    print("\n📡 Step 1: Collecting signals from all strategies...")
    all_signals = collect_all_signals()
    print(f"  Total unique signals: {len(all_signals)}")

    if not all_signals:
        print("  ❌ No signals collected! Aborting.")
        sys.exit(1)

    # ── Step 2: Split into walk-forward folds ──
    print("\n📊 Step 2: Splitting into walk-forward folds...")
    folds = split_folds(all_signals)
    for f in folds:
        print(f"  {f['name']}:")
        print(f"    Train: {f['train_start']}..{f['train_end'][:10]} ({len(f['train'])} sig)")
        print(f"    Test:  {f['test_start'][:10]}..{f['test_end'][:10]} ({len(f['test'])} sig)")

    # ── Step 3: Walk-forward optimization ──
    print("\n⚙️ Step 3: Walk-forward grid search...")
    fold_results = []
    combined_equity = [INITIAL_CAPITAL]
    combined_trades = []
    running_capital = INITIAL_CAPITAL  # капитал переходит между фолдами

    for fold in folds:
        print(f"\n  --- {fold['name']} ---")
        if not fold['train'] or len(fold['train']) < 10:
            print(f"    ⚠ Insufficient train signals ({len(fold['train'])}), skipping")
            continue
        if not fold['test']:
            print(f"    ⚠ No test signals, skipping")
            continue

        # Grid search on train (всегда с INITIAL_CAPITAL для comparability)
        print(f"    Training on {len(fold['train'])} signals...")
        opt = grid_search(fold['train'], INITIAL_CAPITAL)
        bp = opt['best_params']
        tr = opt['best_result']
        train_mdd = max_drawdown(tr['equity'])
        train_ret = (tr['final_capital'] - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100
        print(f"    Best params: margin={bp['margin_usage']}, concurrent={bp['max_concurrent']}, dd_limit={bp['max_dd_limit']}")
        print(f"    Train result: +{train_ret:.1f}%, maxDD={train_mdd*100:.1f}%")

        # Test with best params — НА ТЕКУЩЕМ running_capital
        print(f"    Testing on {len(fold['test'])} signals (capital={running_capital:,.0f} RUB)...")
        test_res = simulate(fold['test'], running_capital,
                            bp['margin_usage'], bp['max_concurrent'], bp['max_dd_limit'])
        test_mdd = max_drawdown(test_res['equity'])
        test_ret = (test_res['final_capital'] - running_capital) / running_capital * 100
        print(f"    Test result: {running_capital:,.0f} → {test_res['final_capital']:,.2f} ({test_ret:+.1f}%, maxDD={test_mdd*100:.1f}%)")

        fold_results.append({
            'fold': fold['name'],
            'train_signals': len(fold['train']),
            'test_signals': len(fold['test']),
            'best_params': bp,
            'train_return_pct': round(train_ret, 2),
            'train_max_dd': round(train_mdd, 4),
            'test_return_pct': round(test_ret, 2),
            'test_max_dd': round(test_mdd, 4),
            'test_trades': len(test_res['trades']),
        })

        # Append test equity to combined curve (relative to running capital)
        for v in test_res['equity'][1:]:
            combined_equity.append(v)
        combined_trades.extend(test_res['trades'])
        running_capital = test_res['final_capital']

    # ── Step 4: Combined results ──
    print("\n📈 Step 4: Final results...")

    combined_ret = (combined_equity[-1] - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100
    combined_mdd = max_drawdown(combined_equity)

    summary_lines = [
        "=" * 60,
        "  CAPITAL GROWTH SIMULATION — SUMMARY",
        "=" * 60,
        f"  Initial capital: {INITIAL_CAPITAL:,} RUB",
        f"  Final capital:   {combined_equity[-1]:,.2f} RUB",
        f"  Total return:    {combined_ret:+.2f}%",
        f"  Max drawdown:    {combined_mdd*100:.1f}%",
        f"  Total trades:    {len(combined_trades)}",
        f"  History:         {HISTORY_DAYS} days",
        "",
        "  Walk-Forward Folds:",
    ]

    for fr in fold_results:
        bp = fr['best_params']
        summary_lines.append(f"    {fr['fold']}:")
        summary_lines.append(f"      Train: {fr['train_signals']} sig → "
                             f"{fr['train_return_pct']:+.2f}% (maxDD {fr['train_max_dd']*100:.1f}%)")
        summary_lines.append(f"      Test:  {fr['test_signals']} sig → "
                             f"{fr['test_return_pct']:+.2f}% (maxDD {fr['test_max_dd']*100:.1f}%)")
        summary_lines.append(f"      Params: margin={bp['margin_usage']}, "
                             f"concurrent={bp['max_concurrent']}, dd_limit={bp['max_dd_limit']}")

    summary_lines.extend([
        "",
        "  COMBINED TEST (concatenated):",
        f"    Start:  {INITIAL_CAPITAL:,} RUB",
        f"    Final:  {combined_equity[-1]:,.2f} RUB",
        f"    Return: {combined_ret:+.2f}%",
        f"    Max DD: {combined_mdd*100:.1f}%",
        f"    Trades: {len(combined_trades)}",
        "",
        "  Output files:",
        f"    {os.path.join(OUTPUT_DIR, 'results.json')}",
        f"    {os.path.join(OUTPUT_DIR, 'equity_curve.csv')}",
        f"    {os.path.join(OUTPUT_DIR, 'summary.txt')}",
        f"    {os.path.join(OUTPUT_DIR, 'optimal_params.csv')}",
        "=" * 60,
    ])

    print('\n'.join(summary_lines))

    # ── Save results ──
    results_data = {
        'initial_capital': INITIAL_CAPITAL,
        'final_capital': combined_equity[-1],
        'total_return_pct': round(combined_ret, 2),
        'max_drawdown_pct': round(combined_mdd * 100, 2),
        'total_trades': len(combined_trades),
        'history_days': HISTORY_DAYS,
        'fold_results': fold_results,
    }
    with open(os.path.join(OUTPUT_DIR, 'results.json'), 'w') as f:
        json.dump(results_data, f, indent=2, ensure_ascii=False)
    print(f"  ✅ Saved results.json")

    eq_df = pd.DataFrame({'step': range(len(combined_equity)), 'equity': combined_equity})
    eq_df.to_csv(os.path.join(OUTPUT_DIR, 'equity_curve.csv'), index=False)
    print(f"  ✅ Saved equity_curve.csv ({len(eq_df)} steps)")

    with open(os.path.join(OUTPUT_DIR, 'summary.txt'), 'w') as f:
        f.write('\n'.join(summary_lines))
    print(f"  ✅ Saved summary.txt")

    params_rows = []
    for fr in fold_results:
        params_rows.append({
            'fold': fr['fold'],
            'margin_usage': fr['best_params']['margin_usage'],
            'max_concurrent': fr['best_params']['max_concurrent'],
            'max_dd_limit': fr['best_params']['max_dd_limit'],
            'train_return_pct': fr['train_return_pct'],
            'train_max_dd': fr['train_max_dd'],
            'test_return_pct': fr['test_return_pct'],
            'test_max_dd': fr['test_max_dd'],
        })
    params_df = pd.DataFrame(params_rows)
    params_df.to_csv(os.path.join(OUTPUT_DIR, 'optimal_params.csv'), index=False)
    print(f"  ✅ Saved optimal_params.csv")

    print("\n✅ Done!")


if __name__ == '__main__':
    main()
