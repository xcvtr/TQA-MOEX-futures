from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Signal:
    time: str
    direction: str
    entry: float
    exit: float
    return_pct: float
    strategy: str = ''
    ticker: str = ''
    idx: int = 0


@dataclass
class Position:
    id: str
    symbol: str
    direction: str
    entry_price: float
    contracts: int
    entry_time: str
    horizon: int
    status: str = 'open'
    pnl: Optional[float] = None
    exit_price: Optional[float] = None
    exit_time: Optional[str] = None


@dataclass
class Trade:
    time: str
    symbol: str
    direction: str
    entry: float
    exit: float
    contracts: int
    pnl_pct: float
    pnl_rub: float
    status: str
