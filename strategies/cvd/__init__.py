# CVD strategy
# prod/ — стабильная версия (paper/live)
# dev/ — эксперименты

from .prod.engine import run, DEFAULT_PARAMS
from .prod.lib import load_ticker_specs, get_tick, get_tick_cost, get_go
