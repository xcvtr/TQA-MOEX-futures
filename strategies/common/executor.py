"""Executor — управление позициями и капиталом."""
from strategies.common.broker import Position, BrokerSim

class Executor:
    """Управляет позициями, капиталом, ГО."""
    
    def __init__(self, initial_capital=100000, risk_pct=0.1, max_leverage=10):
        self.equity = float(initial_capital)
        self.peak = float(initial_capital)
        self.positions = []
        self.trades = []
        self.risk_pct = risk_pct
        self.max_leverage = max_leverage
        self.broker = BrokerSim()
    
    def process_signal(self, signal: dict, bar_idx: int, specs: dict):
        """Открыть позицию по сигналу."""
        ticker = signal['ticker']
        direction = signal['direction']
        price = signal['entry_price']
        strategy = signal['strategy']
        
        go = float(specs.get('go', 0))
        lot = int(specs.get('lot_volume', 1))
        step_price = float(specs.get('step_price', 1.0))
        min_step = float(specs.get('min_step', 0.01))
        
        if go <= 0:
            return None
        
        # Sizing
        max_sh = int(self.equity * self.risk_pct / go)
        cv = price * lot
        max_lev = max(1, int(self.equity * self.max_leverage / cv)) if cv > 0 else 1
        shares = max(1, min(max_sh, max_lev))
        
        # Check margin
        needed = go * shares * 1.2
        if self.equity < needed:
            return None
        
        pos = Position(
            ticker=ticker, direction=direction, entry_price=price,
            entry_bar=bar_idx, shares=shares, strategy=strategy,
            go=go, step_price=step_price, min_step=min_step, lot=lot
        )
        
        # Apply slippage
        entry = self.broker.open_with_slippage(pos, price)
        pos.entry_price = entry
        
        self.positions.append(pos)
        return pos
    
    def update_positions(self, bar_idx: int, hi: float, lo: float, prc: float):
        """Обновить все открытые позиции (trailing, timeout)."""
        total_pnl = 0.0
        for p in list(self.positions):
            if p.closed:
                self.positions.remove(p)
                continue
            pnl = self.broker.update(p, bar_idx, hi, lo, prc)
            if p.closed:
                self.equity += pnl
                total_pnl += pnl
                self.trades.append(p)
        
        if self.equity > self.peak:
            self.peak = self.equity
        
        return total_pnl
    
    @property
    def max_dd_pct(self) -> float:
        if self.peak <= 0: return 0
        return (self.peak - self.equity) / self.peak * 100
    
    @property
    def total_return_pct(self) -> float:
        return (self.equity / 100000 - 1) * 100
