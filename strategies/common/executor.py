"""Executor — финальная версия."""
from strategies.common.broker import Position, BrokerSim

ACTIVATION_PCT = 0.5
TRAIL_PCT = 0.3
TIMEOUT_BARS = 12
COMMISSION = 4
RISK_PCT = 0.1
MAX_LEVERAGE = 10

class Executor:
    def __init__(self, initial_capital=100000):
        self.equity = float(initial_capital)
        self.initial = float(initial_capital)
        self.peak = float(initial_capital)
        self.positions = []
        self.trades = []
        self.broker = BrokerSim()
        self.eq_curve = []
    
    def process_signal(self, signal, bar_idx, specs):
        ticker = signal['ticker']
        direction = signal['direction']
        price = signal['entry_price']
        strategy = signal['strategy']
        
        go = float(specs.get('go', 0))
        step_price = float(specs.get('step_price', 1.0))
        min_step = float(specs.get('min_step', 0.01))
        lot = int(specs.get('lot_volume', 1))
        
        if go <= 0: return None
        
        max_sh = int(self.equity * RISK_PCT / go)
        cv = price * lot
        max_lev = max(1, int(self.equity * MAX_LEVERAGE / cv)) if cv > 0 else 1
        shares = max(1, min(max_sh, max_lev))
        
        needed = go * shares * 1.2
        if self.equity < needed:
            return None
        
        pos = Position(ticker, direction, price, bar_idx, shares, strategy,
                       go, step_price, min_step)
        self.positions.append(pos)
        return pos
    
    def update_positions(self, bar_idx, hi, lo, prc):
        for p in list(self.positions):
            if p.closed:
                self.positions.remove(p)
                continue
            pnl = self.broker.update(p, bar_idx, hi, lo, prc)
            if p.closed:
                self.equity += pnl
                self.trades.append(p)
        
        if self.equity > self.peak:
            self.peak = self.equity
        self.eq_curve.append(self.equity)
    
    @property
    def max_dd_pct(self):
        peak = self.initial
        max_dd = 0.0
        for eq in self.eq_curve:
            if eq > peak: peak = eq
            dd = (peak - eq) / peak * 100
            if dd > max_dd: max_dd = dd
        return max_dd
    
    @property
    def total_return_pct(self):
        return (self.equity / self.initial - 1) * 100
