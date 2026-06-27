# Default params for CVD strategy
# Period=20 bars (100 min), Lookahead=12 bars (60 min), Z=0.6
import numpy as np

DEFAULT_PARAMS = {
    'period': 20,
    'lookahead': 12,
    'z_threshold': 0.6,
    'data_source': 'tradestats_fo',
    'min_signals': 10,
}

def compute_cvd_z(cvd, period=20):
    n = len(cvd)
    z = np.zeros(n)
    for i in range(period, n):
        s = cvd[i-period:i]
        if s.std() > 0:
            z[i] = (cvd[i] - s.mean()) / s.std()
    return z

def run(ticker, bars, params=None):
    """Compute CVD signal for a ticker.
    
    Args:
        ticker: str
        bars: DataFrame with columns [bt, prc, vb, vs, ...]
        params: dict with period, lookahead, z_threshold
    
    Returns:
        dict with ticker, direction, entry_price, reason, score
        or None if no signal
    """
    if params is None:
        params = DEFAULT_PARAMS
    
    period = params.get('period', 20)
    lookahead = params.get('lookahead', 12)
    z_thresh = params.get('z_threshold', 0.6)
    
    n = len(bars)
    if n < period + lookahead:
        return None
    
    cvd = bars['vb'].values.astype(float) - bars['vs'].values.astype(float)
    dcvd = np.diff(cvd, prepend=cvd[0])
    dcvd_z = compute_cvd_z(dcvd, period)
    
    price = float(bars['prc'].iloc[-1])
    last_z = dcvd_z[-1]
    
    if np.isnan(last_z):
        return None
    
    if last_z > z_thresh:
        return {
            'ticker': ticker,
            'direction': 'long',
            'entry_price': price,
            'reason': f'CVD_z={last_z:.2f} > {z_thresh}',
            'score': float(last_z),
        }
    elif last_z < -z_thresh:
        return {
            'ticker': ticker,
            'direction': 'short',
            'entry_price': price,
            'reason': f'CVD_z={last_z:.2f} < -{z_thresh}',
            'score': float(-last_z),
        }
    
    return None
