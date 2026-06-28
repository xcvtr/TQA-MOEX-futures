"""CVD engine — check_signal() only."""
import numpy as np

def check_signal(bar_data: dict, ticker: str, params: dict = None) -> dict:
    """
    CVD: Cumulative Volume Delta z-score.
    LONG:  dcvd_z > 0.6
    SHORT: dcvd_z < -0.6
    """
    if params is None:
        params = {'period': 20, 'z_threshold': 0.6}
    
    dcvd_z = bar_data.get('dcvd_z', 0)
    prc = bar_data.get('prc', 0)
    
    if np.isnan(dcvd_z):
        return None
    
    if dcvd_z > params['z_threshold']:
        return {'ticker': ticker, 'direction': 'long', 'entry_price': prc,
                'reason': f'cvd_long_z={dcvd_z:.2f}', 'score': round(float(dcvd_z), 4),
                'strategy': 'cvd'}
    elif dcvd_z < -params['z_threshold']:
        return {'ticker': ticker, 'direction': 'short', 'entry_price': prc,
                'reason': f'cvd_short_z={dcvd_z:.2f}', 'score': round(float(-dcvd_z), 4),
                'strategy': 'cvd'}
    
    return None
