#!/usr/bin/env python3 -u
"""
Unified Portfolio Manager — запускает независимые стратегии.
Каждая стратегия получает свою долю капитала, свои тикеры, свой риск.
"""
import sys, os, json, importlib, logging
from datetime import datetime, timezone
from collections import defaultdict

log = logging.getLogger('portfolio')

# Стратегии регистрируются здесь
# Каждая: {"name", "module", "class", "alloc": 0-1, "enabled": bool}
STRATEGIES = [
    {"name": "dragon", "alloc": 0.6, "risk_pct": 7, "enabled": True},
    {"name": "stop_hunt", "alloc": 0.2, "risk_pct": 5, "enabled": False},
    {"name": "impulse_return", "alloc": 0.2, "risk_pct": 5, "enabled": False},
]


class StrategyEngine:
    """Базовый класс для стратегии."""
    def __init__(self, name, alloc, risk_pct):
        self.name = name
        self.alloc = alloc
        self.risk_pct = risk_pct
        self.equity = 0
        self.positions = {}
        self.trades = []
    
    def on_tick(self, bar_data, i):
        """Вызывается на каждом баре. Возвращает список ордеров."""
        raise NotImplementedError
    
    def on_detect(self, bar_data, i):
        """Вызывается на каждом detect-баре. Возвращает сигналы."""
        raise NotImplementedError


class DragonStrategy(StrategyEngine):
    """Dragon strategy — динамический выбор тикеров по GO."""
    
    ALL_TICKERS = {
        'MM': {'ms': 0.05, 'sp': 0.5, 'go': 2165.21},
        'GZ': {'ms': 1.0, 'sp': 1.0, 'go': 2898.11},
        'SV': {'ms': 0.01, 'sp': 7.70611, 'go': 15353.35},
        'BR': {'ms': 0.01, 'sp': 7.70611, 'go': 17164.0},
        'NG': {'ms': 0.001, 'sp': 7.70611, 'go': 10259.52},
        'RN': {'ms': 1.0, 'sp': 1.0, 'go': 3847.51},
        'CR': {'ms': 0.001, 'sp': 1.0, 'go': 1821.72},
    }
    PRIORITY = ['MM', 'GZ', 'SV', 'BR', 'NG', 'RN', 'CR']
    
    def __init__(self, name, alloc, risk_pct):
        super().__init__(name, alloc, risk_pct)
        self.m5_cache = {}
        self.ticker_eq = {}
        from dragon.prod.engine import check_signal
        self.check_signal = check_signal
        self.dp = {'impulse_pct': 0.3, 'retrace_max_pct': 70, 'hump_extension': 0.1, 'lookback': 100}
    
    def select_tickers(self, equity):
        """Динамический выбор тикеров под капитал."""
        go_limit = equity * 0.5  # KNUR 0.5
        sel = []
        for t in self.PRIORITY:
            n = max(len(sel), 1)
            if self.ALL_TICKERS[t]['go'] * 2 <= go_limit / n:
                sel.append(t)
        return sel if sel else ['MM', 'GZ']
    
    def on_tick(self, bar_data, ticker, i, sl=0.01, ta=0.015, tt=0.005):
        """Проверка SL/TP для открытых позиций."""
        pos = self.positions.get(ticker)
        if pos is None:
            return None
        bar = bar_data
        ep = pos['ep']
        ms, sp = self.ALL_TICKERS[ticker]['ms'], self.ALL_TICKERS[ticker]['sp']
        ex = None
        
        slev = ep*(1-sl) if pos['dir']=='long' else ep*(1+sl)
        if (pos['dir']=='long' and bar['lo']<=slev) or (pos['dir']=='short' and bar['hi']>=slev):
            ex = slev
        if not ex and i%5==4:
            if not pos.get('tr'):
                if (pos['dir']=='long' and bar['hi']>=ep*(1+ta)) or (pos['dir']=='short' and bar['lo']<=ep*(1-ta)):
                    pos['tr']=True
                    pos['tl']=bar['hi']*(1-tt) if pos['dir']=='long' else bar['lo']*(1+tt)
            if pos.get('tr'):
                if (pos['dir']=='long' and bar['lo']<=pos['tl']) or (pos['dir']=='short' and bar['hi']>=pos['tl']):
                    ex = pos['tl']
        if not ex and i-pos['bi']>=60:
            ex = bar['prc']
        
        if ex is not None:
            raw = ((ex-ep)/ms*sp - 4) * pos['contracts']
            pnl = raw if pos['dir']=='long' else -raw
            self.trades.append(pnl)
            del self.positions[ticker]
            return {'ticker': ticker, 'pnl': pnl, 'exit': ex}
        return None
    
    def on_detect(self, bar_data, ticker, i, go_used, go_limit, equity):
        """Проверка сигнала на M5."""
        if ticker in self.positions:
            return None
        bar = bar_data
        ms, sp, go = self.ALL_TICKERS[ticker]['ms'], self.ALL_TICKERS[ticker]['sp'], self.ALL_TICKERS[ticker]['go']
        sig = self.check_signal({'prc': bar['prc'], 'bars_list': bar.get('bars_list', [])}, ticker, self.dp)
        if not sig:
            return None
        
        risk_a = (equity * self.alloc) * self.risk_pct / 100
        sc = sig['entry_price'] * 0.01 / ms * sp + 4
        c = max(1, int(risk_a / sc)) if sc > 0 else 1
        if go_used + go * c > go_limit:
            return None
        
        self.positions[ticker] = {'bi': i, 'ep': sig['entry_price'], 'dir': sig['direction'],
                                   'tr': False, 'tl': None, 'contracts': c}
        return {'ticker': ticker, 'contracts': c, 'dir': sig['direction'], 'entry': sig['entry_price']}


class PortfolioManager:
    """Управляет несколькими стратегиями, распределяет капитал."""
    
    def __init__(self, total_equity=200000):
        self.total_equity = total_equity
        self.strategies = {}
        self.load_strategies()
    
    def load_strategies(self):
        for cfg in STRATEGIES:
            if cfg['name'] == 'dragon':
                self.strategies['dragon'] = DragonStrategy(
                    cfg['name'], cfg['alloc'], cfg['risk_pct']
                )
                self.strategies['dragon'].equity = self.total_equity * cfg['alloc']
    
    def get_state(self):
        return {
            'equity': self.total_equity,
            'strategies': {
                name: {
                    'equity': s.equity,
                    'positions': len(s.positions),
                    'trades': len(s.trades),
                }
                for name, s in self.strategies.items()
            }
        }


if __name__ == '__main__':
    pm = PortfolioManager(200000)
    print(json.dumps(pm.get_state(), indent=2))
