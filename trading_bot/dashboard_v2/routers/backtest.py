"""Backtest API — запуск бэктеста через registry."""

from fastapi import APIRouter, Query
from typing import Optional

from ..core.registry import get_strategy, list_strategies
from ..core.statistics import compute_stats, equity_curve
from ..adapters.moex_adapter import load_bars, load_bars_with_oi

router = APIRouter(prefix='/api/backtest')


@router.get('/strategies')
async def backtest_strategies():
    """Список стратегий, доступных для бэктеста."""
    result = []
    for s in list_strategies():
        result.append({
            'name': s.name,
            'display_name': s.display_name,
            'description': s.description,
            'tickers': list(s.tickers.keys()),
            'default_config': s.default_config,
            'needs_oi': s.needs_oi,
        })
    return {'strategies': result}


def _split_by_time(signals, split_ratio=0.7):
    """Разделить сигналы на train/test по времени. split_ratio = доля train."""
    if not signals:
        return [], []
    times = sorted(set(s['time'] for s in signals))
    if not times:
        return signals, []
    split_idx = int(len(times) * split_ratio)
    if split_idx >= len(times):
        split_idx = len(times) - 1
    split_time = times[split_idx]
    train = [s for s in signals if s['time'] < split_time]
    test = [s for s in signals if s['time'] >= split_time]
    return train, test


@router.get('/run')
async def backtest_run(
    strategy: str = Query(..., description='Имя стратегии'),
    ticker: str = Query(..., description='Тикер'),
    days: int = Query(180, description='Дней истории'),
    horizon: Optional[int] = Query(None, description='Горизонт выхода'),
    vol_thresh: Optional[float] = Query(None, description='Порог z-score объёма'),
    div_thresh: Optional[float] = Query(None, description='Порог расхождения'),
):
    """Запустить бэктест стратегии на тикере. Out-of-sample = последние 30% по времени."""
    strat = get_strategy(strategy)
    if not strat:
        return {'error': f'Стратегия {strategy} не найдена'}, 404

    if ticker not in strat.tickers:
        return {'error': f'Тикер {ticker} не поддерживается стратегией {strategy}'}, 400

    config = dict(strat.default_config)
    if horizon is not None:
        config['horizon'] = horizon
    if vol_thresh is not None:
        config['vol_thresh'] = vol_thresh
    if div_thresh is not None:
        config['div_thresh'] = div_thresh

    if strat.needs_oi:
        data = load_bars_with_oi(ticker, days)
    else:
        data = load_bars(ticker, days)

    if not data:
        return {
            'strategy': strategy,
            'ticker': ticker,
            'error': 'Нет данных',
            'signals': [],
            'stats': compute_stats([]),
            'equity': [],
            'total_signals': 0,
            'oos_signals_count': 0,
        }

    signals = strat.detect_fn(ticker, data, config)
    for s in signals:
        s['strategy'] = strategy
        s['ticker'] = ticker

    train_signals, test_signals = _split_by_time(signals)
    oos_stats = compute_stats(test_signals)
    oos_equity = equity_curve(test_signals)

    return {
        'strategy': strategy,
        'ticker': ticker,
        'config': config,
        'total_signals': len(signals),
        'train_signals': len(train_signals),
        'oos_signals_count': len(test_signals),
        'stats': oos_stats,
        'equity': oos_equity,
        'signals': test_signals,
        'train_signals_data': train_signals[:10],
    }
