"""Lunch Reversal engine — check_signal() only."""

def check_signal(bar_data: dict, ticker: str, params: dict = None) -> dict:
    """
    Lunch Reversal: разворот перед дневным клирингом (13:00-14:00 MSK).
    SHORT если цена выросла с 10:00 до 13:00.
    LONG если цена упала с 10:00 до 13:00.
    """
    if params is None:
        params = {'min_move_pct': 0.1}
    
    hour = bar_data.get('hour', -1)
    minute = bar_data.get('minute', -1)
    prc = bar_data.get('prc', 0)
    price_10 = bar_data.get('price_10', 0)
    
    # Сигнал только в 13:00 MSK
    if hour != 13 or minute != 0 or price_10 <= 0:
        return None
    
    change_pct = (prc - price_10) / price_10 * 100
    
    if change_pct > params['min_move_pct']:  # выросла → SHORT
        return {'ticker': ticker, 'direction': 'short', 'entry_price': prc,
                'reason': f'lunch_rev_short_{change_pct:.1f}%',
                'score': round(float(abs(change_pct)), 4), 'strategy': 'lunch_rev'}
    elif change_pct < -params['min_move_pct']:  # упала → LONG
        return {'ticker': ticker, 'direction': 'long', 'entry_price': prc,
                'reason': f'lunch_rev_long_{change_pct:.1f}%',
                'score': round(float(abs(change_pct)), 4), 'strategy': 'lunch_rev'}
    
    return None
