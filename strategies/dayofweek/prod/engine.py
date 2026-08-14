"""Адаптер dayofweek → контракт фреймворка check_signal(bar_data, ticker, params).

Использует проверенную логику MOEX-stocks-1 (чекп. 016-017):
- LONG в Пн, если prev_week_return > 0 (close-to-close прошлой календарной недели)
- SHORT в Чт, если prev_week_return < 0
- skip июль (дивидендная отсечка Сбера)
- ВАЖНО (017): SHORT РФ-акции инвертировался в авг 2026 — направление проверяется
  по params.direction (из PG), можно выключить short.

Данные: dayofweek работает по ДНЯМ (D1), не по M1. bar_data приходит от детектора
с close_hist (последние бары). prev_week_return строится по дневным close.
"""
import sys
from datetime import datetime, timezone, timedelta

DEFAULT_PARAMS = {'skip_july': True, 'direction': 'both'}


def _daily_closes(bars_list):
    """{(date, close)} по барам — последний close каждого дня."""
    daily = {}
    for b in bars_list:
        d = datetime.fromtimestamp(b['ts'], tz=timezone.utc).date()
        daily[d] = b['prc']
    return daily


def prev_week_return(ts, bars_list):
    """Доходность календарной недели ДО дня ts (без look-ahead).

    Неделя = Пн..Вс. Берём дни < ts (строго до сигнала).
    """
    daily = _daily_closes(bars_list)
    cur = datetime.fromtimestamp(ts, tz=timezone.utc)
    dates = sorted(d for d in daily if d < cur.date())
    if len(dates) < 2:
        return None
    mon = cur.date() - timedelta(days=cur.weekday())
    prev_mon = mon - timedelta(days=7)
    prev_sun = prev_mon + timedelta(days=6)
    idx = [i for i, d in enumerate(dates) if prev_mon <= d <= prev_sun]
    if len(idx) < 2:
        return None
    c0 = daily[dates[idx[0]]]
    c1 = daily[dates[idx[-1]]]
    if c0 <= 0:
        return None
    return c1 / c0 - 1.0


def check_signal(bar_data: dict, ticker: str, params: dict = None) -> dict:
    """Контракт фреймворка: возвращает signal|None."""
    if params is None:
        params = DEFAULT_PARAMS
    skip_july = params.get('skip_july', True)
    direction_mode = params.get('direction', 'both')

    ts = bar_data.get('ts')
    if not ts:
        return None
    now_utc = datetime.fromtimestamp(ts, tz=timezone.utc)
    if skip_july and now_utc.month == 7:
        return None
    dow = now_utc.weekday()  # 0=Пн, 3=Чт

    bars_list = bar_data.get('bars_list') or []
    if not bars_list:
        return None
    prev_ret = prev_week_return(ts, bars_list)
    if prev_ret is None:
        return None

    if dow == 0 and prev_ret > 0 and direction_mode in ('both', 'long'):
        return {'direction': 'long', 'reason': f'dow_mon_{prev_ret:+.3f}', 'score': 0.7,
                'entry_price': bar_data.get('prc')}
    if dow == 3 and prev_ret < 0 and direction_mode in ('both', 'short'):
        return {'direction': 'short', 'reason': f'dow_thu_{prev_ret:+.3f}', 'score': 0.7,
                'entry_price': bar_data.get('prc')}
    return None
