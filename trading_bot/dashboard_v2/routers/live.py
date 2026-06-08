"""Live signals + positions API."""

from fastapi import APIRouter

from ..core.registry import list_strategies
from ..adapters.moex_adapter import load_bars, load_bars_with_oi

router = APIRouter(prefix='/api/live')


@router.get('/signals')
async def live_signals():
    """Последние сигналы от всех стратегий (за последний час = ~12 баров)."""
    all_signals = []
    for strategy in list_strategies():
        for ticker in strategy.tickers:
            days = 2
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

    all_signals.sort(key=lambda s: s.get('time', ''), reverse=True)
    return {'signals': all_signals[:50], 'total': len(all_signals)}


@router.get('/positions')
async def live_positions():
    """Открытые позиции из бумажного трейдера."""
    from trading_bot.tracker import load_positions
    positions = load_positions()
    open_pos = [p for p in positions if p.get('status') == 'open']
    return {'positions': open_pos, 'total': len(open_pos)}
