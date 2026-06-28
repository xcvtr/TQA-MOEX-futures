"""Broker interface — abstract and implementations."""
from dataclasses import dataclass
from typing import Optional, Protocol

@dataclass
class Bar:
    bt: str       # timestamp
    opn: float
    hi: float
    lo: float
    prc: float
    vol: float = 0
    vb: float = 0
    vs: float = 0
    oi: float = 0

class BrokerBase(Protocol):
    def open_position(self, pos) -> None: ...
    def close_position(self, pos, exit_price: float, reason: str) -> None: ...
    def update_price(self, pos, bar: Bar) -> None: ...

class BrokerSim:
    """Simulator for backtesting."""
    def __init__(self, commission=4, slippage_ticks=0.5):
        self.commission = commission
        self.slippage_ticks = slippage_ticks
        self.trades = []
    
    def open_position(self, pos):
        pos.entry_price += pos.direction * self.slippage_ticks * 0.001  # simplified
        self.trades.append(pos)
    
    def close_position(self, pos, exit_price: float, reason: str):
        pnl_ticks = (exit_price - pos.entry_price) * pos.direction
        pnl = pnl_ticks * 1.0 * pos.shares - self.commission * pos.shares
        pos.pnl = pnl
        pos.exit_reason = reason
        pos.closed = True
    
    def update_price(self, pos, bar: Bar):
        # Trailing TP logic
        fav = ((bar.hi - pos.entry_price) / pos.entry_price * 100) if pos.direction == 'long' \
              else ((pos.entry_price - bar.lo) / pos.entry_price * 100)
        if fav > pos.best_price: pos.best_price = fav
        if not pos.trail_activated and fav >= 0.5: pos.trail_activated = True
        
        if pos.trail_activated:
            trail_stop = pos.best_price - 0.3
            hit = ((bar.lo - pos.entry_price) / pos.entry_price * 100 <= -trail_stop) if pos.direction == 'long' \
                  else ((pos.entry_price - bar.hi) / pos.entry_price * 100 <= -trail_stop)
            if hit:
                stop_px = pos.entry_price * (1 + trail_stop/100) if pos.direction == 'long' \
                          else pos.entry_price * (1 - trail_stop/100)
                self.close_position(pos, stop_px, 'trailing_tp')

class BrokerLive:
    """MOEX API connector — stub for now."""
    def __init__(self, token=None):
        self.token = token
    
    def open_position(self, pos):
        # TODO: send order to MOEX via Alor API
        pass
    
    def close_position(self, pos, exit_price, reason):
        # TODO: send close order
        pass
    
    def update_price(self, pos, bar):
        pass
