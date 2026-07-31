"""Stop Hunt engine — check_signal() only."""
from dataclasses import dataclass
from typing import Optional
import numpy as np

def check_signal(bar_data: dict, ticker: str, params: dict = None) -> Optional[dict]:
    """
    Stop Hunt: ложный пробой N-барового диапазона.
    M1: lookback=40, retrace=0.1 (M1 бары волатильнее M5)
    """
    if params is None:
        params = {'lookback': 40, 'retrace': 0.1}
    
    lo = bar_data.get('lo', 0)
    hi = bar_data.get('hi', 0)
    prc = bar_data.get('prc', 0)
    lo_hist = bar_data.get('lo_hist', [])
    hi_hist = bar_data.get('hi_hist', [])
    
    if len(lo_hist) < params['lookback'] or len(hi_hist) < params['lookback']:
        return None
    
    min_lo = min(lo_hist[-params['lookback']:])
    max_hi = max(hi_hist[-params['lookback']:])
    retrace = params['retrace']
    
    if prc <= 0 or lo <= 0 or hi <= 0:
        return None
    
    if lo < min_lo and prc > lo + retrace * (hi - lo):
        score = (min_lo - lo) / (hi - lo + 0.001)
        return {'ticker': ticker, 'direction': 'long', 'entry_price': prc,
                'reason': f'stop_hunt_long', 'score': round(float(score), 4),
                'strategy': 'stop_hunt'}
    
    if hi > max_hi and prc < hi - retrace * (hi - lo):
        score = (hi - max_hi) / (hi - lo + 0.001)
        return {'ticker': ticker, 'direction': 'short', 'entry_price': prc,
                'reason': f'stop_hunt_short', 'score': round(float(score), 4),
                'strategy': 'stop_hunt'}
    
    return None
