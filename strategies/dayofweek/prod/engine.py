"""Адаптер dayofweek → контракт фреймворка check_signal(bar_data, ticker, params).

Оборачивает чистый движок MOEX-stocks-1 (strategies.dayofweek.engine):
- LONG в Пн, если prev_week_return > 0 (close-to-close прошлой календарной недели)
- SHORT в Чт, если prev_week_return < 0
- skip июль (дивидендная отсечка Сбера)
- signal_ts: вход по open дня → детектор шлёт сигнал утром дня сделки

Параметры из PG (params JSONB):
  skip_july: bool (default True)
  direction: 'long'/'short'/'both' (default 'both')
  tf: ignore (dayofweek работает по дням)
"""
import os, sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, '/home/user/projects/MOEX-stocks-1')

DEFAULT_PARAMS = {'skip_july': True, 'direction': 'both'}


def prev_week_return_from_history(ts, close_hist):
    """Доходность календарной недели ДО дня ts, по close_hist (без look-ahead).

    close_hist — хронологический список (bt, prc) за последние дни.
    Берём только дни ДО ts. Неделя = Пн..Вс.
    """
    # Строим даты и closes только до ts
    dates, closes = [], []
    for bt, prc in close_hist:
        if bt < ts:
            dates.append(datetime.fromtimestamp(bt, tz=timezone.utc))
            closes.append(prc)
    if len(closes) < 2:
        return None
    mon = datetime.fromtimestamp(ts, tz=timezone.utc) - timedelta(days=datetime.fromtimestamp(ts, tz=timezone.utc).weekday())
    prev_mon = mon - timedelta(days=7)
    prev_sun = prev_mon + timedelta(days=6)
    idx = [i for i, d in enumerate(dates) if prev_mon <= d <= prev_sun]
    if len(idx) < 2:
        return None
    return closes[idx[-1]] / closes[idx[0]] - 1.0


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
    # skip июль
    if skip_july and now_utc.month == 7:
        return None
    dow = now_utc.weekday()  # 0=Пн, 3=Чт

    # close_hist: список баров с ts — собираем (bt, prc)
    bars_list = bar_data.get('bars_list') or []
    if not bars_list:
        return None
    close_hist = [(b['ts'], b['prc']) for b in bars_list]
    prev_ret = prev_week_return_from_history(ts, close_hist)
    if prev_ret is None:
        return None

    if dow == 0 and prev_ret > 0 and direction_mode in ('both', 'long'):
        return {'direction': 'long', 'reason': f'dow_mon_{prev_ret:+.3f}', 'score': 0.7,
                'entry_price': bar_data.get('prc')}
    if dow == 3 and prev_ret < 0 and direction_mode in ('both', 'short'):
        return {'direction': 'short', 'reason': f'dow_thu_{prev_ret:+.3f}', 'score': 0.7,
                'entry_price': bar_data.get('prc')}
    return None
