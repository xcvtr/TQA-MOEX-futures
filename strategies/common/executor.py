"""Executor — управление позициями и капиталом."""
from dataclasses import dataclass
from typing import Optional

@dataclass
class Signal:
    ticker: str
    direction: str  # 'long' | 'short'
    entry_price: float
    reason: str
    score: float
    strategy: str  # 'stop_hunt' | 'cvd' | 'churn' | 'lunch_rev'

@dataclass
class Position:
    ticker: str
    direction: str
    entry_price: float
    entry_bar: int
    shares: int
    strategy: str
    best_price: float = 0.0
    trail_activated: bool = False
    closed: bool = False
    pnl: float = 0.0
    exit_reason: str = 'open'

class Executor:
    """Управляет позициями, капиталом, ГО."""
    def __init__(self, initial_capital=100000, risk_pct=0.1, max_leverage=10):
        self.equity = float(initial_capital)
        self.peak = float(initial_capital)
        self.positions = []
        self.trades = []
        self.risk_pct = risk_pct
        self.max_leverage = max_leverage
    
    def process_signal(self, signal: Signal, bar_idx: int, specs: dict, broker):
        """Открыть позицию по сигналу, если есть капитал."""
        go = specs.get('go', 0)
        lot = specs.get('lot', 1)
        if go <= 0: return
        
        max_sh = int(self.equity * self.risk_pct / go)
        cv = signal.entry_price * lot
        max_lev = max(1, int(self.equity * self.max_leverage / cv)) if cv > 0 else 1
        shares = max(1, min(max_sh, max_lev))
        
        if self.equity > go * shares * 1.2:
            pos = Position(
                ticker=signal.ticker, direction=signal.direction,
                entry_price=signal.entry_price, entry_bar=bar_idx,
                shares=shares, strategy=signal.strategy
            )
            self.positions.append(pos)
            broker.open_position(pos)
    
    def manage_positions(self, bar, bar_idx, specs, broker):
        """Trailing TP и timeout для всех открытых позиций."""
        for p in list(self.positions):
            if p.closed: continue
            broker.update_price(p, bar)
    
    @property
    def total_go(self):
        return sum(p.shares * specs_map.get(p.ticker, {}).get('go', 0) for p in self.positions)
