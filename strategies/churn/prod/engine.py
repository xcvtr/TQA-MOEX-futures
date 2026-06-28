"""Churn engine — check_signal() only."""
import numpy as np

def check_signal(bar_data: dict, ticker: str, params: dict = None) -> dict:
    """
    Churn: OI flat + volume explosion → разворот.
    oi_flat: |oi - oi_5bars_ago| / oi_5bars_ago < 0.01
    vol_surge: vol / vol_ma20 > 2.0
    Direction: против SMA(20) тренда
    """
    if params is None:
        params = {'oi_window': 5, 'vol_window': 20, 'vol_threshold': 2.0, 'oi_flat_threshold': 0.01}
    
    oi = bar_data.get('oi', 0)
    oi_ago = bar_data.get('oi_5ago', 0)
    vol = bar_data.get('vol', 0)
    vol_ma = bar_data.get('vol_ma20', 1)
    prc = bar_data.get('prc', 0)
    sma = bar_data.get('sma20', prc)
    
    if oi_ago <= 0 or vol_ma <= 0:
        return None
    
    oi_flat = abs(oi - oi_ago) / oi_ago < params['oi_flat_threshold']
    vol_surge = vol / vol_ma > params['vol_threshold']
    
    if oi_flat and vol_surge:
        if prc > sma:  # тренд вверх → SHORT (разворот)
            return {'ticker': ticker, 'direction': 'short', 'entry_price': prc,
                    'reason': 'churn_short', 'score': round(float(vol/vol_ma), 4),
                    'strategy': 'churn'}
        else:  # тренд вниз → LONG (разворот)
            return {'ticker': ticker, 'direction': 'long', 'entry_price': prc,
                    'reason': 'churn_long', 'score': round(float(vol/vol_ma), 4),
                    'strategy': 'churn'}
    
    return None
