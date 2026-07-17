"""Impulse Return — вход на коррекции после импульса.

Импульс: цена прошла N% за K баров (0.5% за 4 бара = 20 мин).
Вход: когда цена откатилась на X% от экстремума импульса (62% Фибо).
Exit: trailing TP (0.5%/0.3%, timeout=12 bars).
"""
import numpy as np
from collections import deque


_cooldown_state = {}


def check_signal(bar_data: dict, ticker: str, params: dict = None) -> dict:
    if params is None:
        params = get_default_params()
    
    prc = bar_data.get('prc', 0)
    hi = bar_data.get('hi', 0)
    lo = bar_data.get('lo', 0)
    vol = bar_data.get('vol', 0)
    close_hist = bar_data.get('close_hist', [])
    hi_hist = bar_data.get('hi_hist', [])
    lo_hist = bar_data.get('lo_hist', [])
    vol_hist = bar_data.get('vol_hist', [])
    
    if prc <= 0 or hi <= 0 or lo <= 0 or vol <= 0:
        return None
    
    # Cooldown
    cd = _cooldown_state.get(ticker, 0)
    if cd > 0:
        _cooldown_state[ticker] = cd - 1
        return None
    
    imp_bars = params.get('impulse_bars', 4)
    imp_pct = params.get('impulse_pct', 0.5)
    retrace = params.get('retrace', 0.618)
    cooldown = params.get('cooldown', 24)
    min_vol_pct = params.get('min_vol_pct', 0.8)
    
    if len(close_hist) < imp_bars + 1:
        return None
    if len(hi_hist) < imp_bars:
        return None
    if len(lo_hist) < imp_bars:
        return None
    
    start_prc = close_hist[-imp_bars - 1]
    if start_prc <= 0:
        return None
    
    all_hi = hi_hist[-imp_bars:] + [hi]
    all_lo = lo_hist[-imp_bars:] + [lo]
    max_prc = max(all_hi)
    min_prc = min(all_lo)
    
    imp_vol = [vol] + (list(vol_hist[-imp_bars:]) if vol_hist else [])
    avg_vol = np.mean(imp_vol) if imp_vol else 0
    
    # Median volume from history
    full_vol_hist = bar_data.get('vol_hist', [])
    median_vol = np.median(full_vol_hist) if len(full_vol_hist) > 10 else avg_vol
    if median_vol <= 0:
        median_vol = avg_vol
    
    if avg_vol < median_vol * min_vol_pct:
        return None
    
    direction = None
    score = 0.0
    
    # Бычий импульс: цена выросла на imp_pct%
    up_move = (max_prc - start_prc) / start_prc * 100
    if up_move >= imp_pct and avg_vol >= median_vol * min_vol_pct:
        impulse_range = max_prc - start_prc
        if impulse_range > 0:
            current_retrace = (max_prc - prc) / impulse_range
            if current_retrace >= retrace:
                direction = 'long'
                score = min(current_retrace * 2, 5.0)
    
    # Медвежий импульс
    if direction is None:
        down_move = (start_prc - min_prc) / start_prc * 100
        if down_move >= imp_pct and avg_vol >= median_vol * min_vol_pct:
            impulse_range = start_prc - min_prc
            if impulse_range > 0:
                current_retrace = (prc - min_prc) / impulse_range
                if current_retrace >= retrace:
                    direction = 'short'
                    score = min(current_retrace * 2, 5.0)
    
    if direction is None:
        return None
    
    return {
        'ticker': ticker,
        'direction': direction,
        'entry_price': prc,
        'reason': f'impulse_ret_{current_retrace:.2f}',
        'score': round(score, 4),
        'strategy': 'impulse_return',
    }


def get_default_params():
    return {
        'impulse_bars': 4,
        'impulse_pct': 0.5,
        'retrace': 0.618,
        'cooldown': 24,
        'min_vol_pct': 0.8,
    }


def reset_state():
    _cooldown_state.clear()
