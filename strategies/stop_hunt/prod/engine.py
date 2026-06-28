"""Stop Hunt engine — check_signal() only."""
from dataclasses import dataclass
from typing import Optional
import numpy as np

def check_signal(bar_data: dict, ticker: str, params: dict = None) -> Optional[dict]:
    """
    Stop Hunt: ложный пробой 20-барового диапазона.
    
    LONG:  low[i] < min(low[i-20:i]) AND close[i] > low[i] + 0.3*(high[i]-low[i])
    SHORT: high[i] > max(high[i-20:i]) AND close[i] < high[i] - 0.3*(high[i]-low[i])
    """
    if params is None:
        params = {'lookback': 20, 'retrace': 0.3}
    
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
