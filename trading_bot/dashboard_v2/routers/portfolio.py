"""Portfolio API — агрегация по всем стратегиям."""

from fastapi import APIRouter

from ..core.registry import list_strategies
from ..core.statistics import compute_stats, equity_curve
from ..adapters.moex_adapter import load_bars, load_bars_with_oi

router = APIRouter(prefix='/api/portfolio')


@router.get('/stats')
async def portfolio_stats():
    """Сводная статистика портфеля по всем стратегиям + позиции из трекера."""
    from trading_bot.tracker import get_stats as tracker_stats

    result = {'strategies': {}}
    all_signals = []

    for strategy in list_strategies():
        strategy_signals = []
        for ticker in strategy.tickers:
            days = 5
            if strategy.needs_oi:
                data = load_bars_with_oi(ticker, days)
            else:
                data = load_bars(ticker, days)
            if not data:
                continue
            config = dict(strategy.default_config)
            tc = strategy.tickers[ticker]
            for k in ('horizon', 'vol_thresh', 'div_thresh', 'strategy', 'yur_dom_ratio'):
                if k in tc:
                    config[k] = tc[k]
            signals = strategy.detect_fn(ticker, data, config)
            for s in signals:
                s['strategy'] = strategy.name
                s['ticker'] = ticker
            strategy_signals.extend(signals)
            all_signals.extend(signals)

        stats = compute_stats(strategy_signals)
        result['strategies'][strategy.name] = {
            'display_name': strategy.display_name,
            'stats': stats,
            'signal_count': len(strategy_signals),
        }

    combined = compute_stats(all_signals)
    result['combined'] = combined
    result['total_signals'] = len(all_signals)

    t_stats = tracker_stats()
    result['tracker'] = {
        'total_trades': t_stats['total_trades'],
        'winrate': t_stats['winrate'],
        'total_pnl': t_stats['total_pnl'],
        'open_positions': t_stats['open_positions'],
    }

    return result


@router.get('/equity')
async def portfolio_equity():
    """Equity кривые по всем стратегиям + портфель."""
    result = {'curves': {}}

    for strategy in list_strategies():
        all_signals = []
        for ticker in strategy.tickers:
            days = 30
            if strategy.needs_oi:
                data = load_bars_with_oi(ticker, days)
            else:
                data = load_bars(ticker, days)
            if not data:
                continue
            config = dict(strategy.default_config)
            tc = strategy.tickers[ticker]
            for k in ('horizon', 'vol_thresh', 'div_thresh', 'strategy', 'yur_dom_ratio'):
                if k in tc:
                    config[k] = tc[k]
            signals = strategy.detect_fn(ticker, data, config)
            for s in signals:
                s['strategy'] = strategy.name
                s['ticker'] = ticker
            all_signals.extend(signals)

        all_signals.sort(key=lambda s: s.get('time', ''))
        eq = equity_curve(all_signals)
        result['curves'][strategy.name] = {
            'display_name': strategy.display_name,
            'equity': eq,
            'signal_count': len(all_signals),
        }

    return result
