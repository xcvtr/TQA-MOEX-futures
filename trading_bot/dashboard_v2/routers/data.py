"""Data API — OHLCV, freshness, стратегии, рынки."""

from fastapi import APIRouter, Query
from datetime import datetime, timezone

from ..core.registry import list_strategies, list_markets
from ..adapters.moex_adapter import load_bars, load_bars_with_oi, get_freshness, get_db_status

router = APIRouter()


@router.get('/api/strategies')
async def api_strategies():
    """Список зарегистрированных стратегий."""
    result = []
    for s in list_strategies():
        result.append({
            'name': s.name,
            'display_name': s.display_name,
            'description': s.description,
            'tickers': {k: {'label': v.get('label', k), 'go': v.get('go', 0)} for k, v in s.tickers.items()},
            'needs_oi': s.needs_oi,
            'default_config': s.default_config,
        })
    return {'strategies': result}


@router.get('/api/markets')
async def api_markets():
    """Список зарегистрированных рынков."""
    result = []
    for m in list_markets():
        result.append({
            'name': m.name,
            'display_name': m.display_name,
            'symbols': m.symbols,
        })
    return {'markets': result}


@router.get('/api/bars')
async def api_bars(
    symbol: str = Query(..., description='Тикер'),
    days: int = Query(5, description='Дней истории'),
    tf: str = Query('5m', description='Таймфрейм'),
):
    """OHLCV бары для графика."""
    data = load_bars(symbol, days)
    if not data:
        return {'bars': [], 'symbol': symbol}
    bars = []
    for r in data:
        bars.append({
            'time': r[0],
            'open': r[1],
            'high': r[2],
            'low': r[3],
            'close': r[4],
            'volume': r[5],
        })
    return {'bars': bars, 'symbol': symbol, 'count': len(bars)}


@router.get('/api/data/freshness')
async def api_freshness():
    """Свежесть данных по тикерам."""
    freshness = get_freshness()
    db_status = get_db_status()
    now = datetime.now(timezone.utc)
    result = []
    for ticker, last_bar in sorted(freshness.items()):
        try:
            last_dt = datetime.fromisoformat(last_bar)
            hours_behind = round((now - last_dt).total_seconds() / 3600, 1)
        except (ValueError, TypeError):
            hours_behind = None
        result.append({
            'ticker': ticker,
            'last_bar': last_bar,
            'hours_behind': hours_behind,
        })
    return {'freshness': result, 'db_status': db_status}
