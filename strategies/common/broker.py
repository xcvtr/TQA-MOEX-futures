"""Broker — финальная версия."""
COMMISSION = 4

class Position:
    def __init__(self, ticker, direction, entry_price, entry_bar, shares, strategy,
                 go=0, step_price=1.0, min_step=0.01):
        self.ticker = ticker
        self.direction = direction
        self.entry_price = entry_price
        self.entry_bar = entry_bar
        self.shares = shares
        self.strategy = strategy
        self.go = go
        self.step_price = step_price
        self.min_step = min_step
        self.best_price = 0.0
        self.trail_activated = False
        self.pnl = 0.0
        self.exit_reason = 'open'
        self.closed = False

class BrokerSim:
    def __init__(self, commission=COMMISSION):
        self.commission = commission
    
    def update(self, pos, bar_idx, hi, lo, prc):
        if pos.closed:
            return 0.0
        
        entry = pos.entry_price
        if pos.direction == 'long':
            fav = (hi - entry) / entry * 100
            cur_dn = (entry - lo) / entry * 100
        else:
            fav = (entry - lo) / entry * 100
            cur_dn = (hi - entry) / entry * 100
        
        if fav > pos.best_price:
            pos.best_price = fav
        if not pos.trail_activated and fav >= 0.5:
            pos.trail_activated = True
        
        if pos.trail_activated:
            trail_stop = pos.best_price - 0.3
            if cur_dn >= trail_stop:
                exit_px = entry * (1 + trail_stop/100) if pos.direction == 'long' else entry * (1 - trail_stop/100)
                return self._close(pos, exit_px, 'trailing_tp')
        
        if bar_idx - pos.entry_bar >= 12:
            return self._close(pos, prc, 'timeout')
        
        return 0.0
    
    def _close(self, pos, exit_px, reason):
        ticks = (exit_px - pos.entry_price) / pos.min_step
        if pos.direction == 'short':
            ticks = -ticks
        pnl = ticks * pos.step_price * pos.shares - self.commission * pos.shares
        pos.pnl = round(pnl, 2)
        pos.exit_reason = reason
        pos.closed = True
        return pos.pnl
