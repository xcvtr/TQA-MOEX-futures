"""Trailing TP — общий модуль для всех стратегий."""

ACTIVATION_PCT = 0.5
TRAIL_PCT = 0.3
TIMEOUT_BARS = 12

def check_trailing(position, bar, bar_idx):
    """
    Проверяет, нужно ли закрыть позицию по Trailing TP.
    Возвращает (close: bool, exit_price: float, reason: str).
    """
    entry = position.entry_price
    direction = position.direction
    
    if direction == 'long':
        fav = (bar['hi'] - entry) / entry * 100
        cur_dn = (entry - bar['lo']) / entry * 100
    else:
        fav = (entry - bar['lo']) / entry * 100
        cur_dn = (bar['hi'] - entry) / entry * 100
    
    if fav > position.best_price:
        position.best_price = fav
    
    if not position.trail_activated and fav >= ACTIVATION_PCT:
        position.trail_activated = True
    
    if position.trail_activated:
        trail_stop = position.best_price - TRAIL_PCT
        if cur_dn >= trail_stop:
            if direction == 'long':
                exit_px = entry * (1 + trail_stop / 100)
            else:
                exit_px = entry * (1 - trail_stop / 100)
            return True, exit_px, 'trailing_tp'
    
    # Timeout
    if bar_idx - position.entry_bar >= TIMEOUT_BARS:
        return True, bar['prc'], 'timeout'
    
    return False, 0, ''
