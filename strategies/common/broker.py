"""Broker interface — abstract + implementations."""
from dataclasses import dataclass
from typing import Optional, Protocol

COMMISSION = 4  # RUB per contract round-trip
SLIPPAGE_TICKS = 1

@dataclass
class Position:
    ticker: str
    direction: str  # 'long' | 'short'
    entry_price: float
    entry_bar: int
    shares: int
    strategy: str
    best_price: float = 0.0
    trail_activated: bool = False
    closed: bool = False
    pnl: float = 0.0
    exit_reason: str = 'open'
    go: float = 0
    step_price: float = 1.0
    min_step: float = 0.01
    lot: int = 1

    def dir_sign(self) -> int:
        return 1 if self.direction == 'long' else -1

class BrokerSim:
    """Эмулятор брокера для backtesting."""
    
    def __init__(self, commission=COMMISSION, slippage_ticks=SLIPPAGE_TICKS):
        self.commission = commission
        self.slippage_ticks = slippage_ticks
        self.trades = []
    
    def open_with_slippage(self, pos: Position, open_price: float) -> float:
        """Entry с проскальзыванием. Возвращает цену входа."""
        slip = pos.min_step * self.slippage_ticks * pos.dir_sign()
        return open_price + slip
    
    def close_position(self, pos: Position, exit_price: float, reason: str):
        """Закрыть позицию: PnL = (exit-entry)/step * step_price * shares - comm."""
        ticks = (exit_price - pos.entry_price) / pos.min_step * pos.dir_sign()
        pnl = ticks * pos.step_price * pos.shares - self.commission * pos.shares
        pos.pnl = round(pnl, 2)
        pos.exit_reason = reason
        pos.closed = True
        self.trades.append(pos)
        return pnl
    
    def update(self, pos: Position, bar_idx: int, hi: float, lo: float, prc: float):
        """Проверить trailing TP и timeout."""
        entry = pos.entry_price
        
        # Favourable movement
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
        
        # Trailing TP hit
        if pos.trail_activated:
            trail_stop = pos.best_price - 0.3
            if cur_dn >= trail_stop:
                if pos.direction == 'long':
                    exit_px = entry * (1 + trail_stop / 100)
                else:
                    exit_px = entry * (1 - trail_stop / 100)
                return self.close_position(pos, exit_px, 'trailing_tp')
        
        # Timeout at 12 bars
        if bar_idx - pos.entry_bar >= 12:
            return self.close_position(pos, prc, 'timeout')
        
        return 0.0

class BrokerLive:
    """MOEX API через Alor — заглушка."""
    def __init__(self, token=None):
        self.token = token
    
    def open_with_slippage(self, pos, price):
        # TODO: send limit order via Alor API
        return price
    
    def close_position(self, pos, exit_price, reason):
        # TODO: send close order
        pass
    
    def update(self, pos, bar_idx, hi, lo, prc):
        pass
