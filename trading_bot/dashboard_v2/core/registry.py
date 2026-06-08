"""Strategy and Market registries — the open architecture."""


_strategies = {}
_strategy_market = {}
_markets = {}


class StrategyInfo:
    def __init__(self, name, display_name, detect_fn, default_config, tickers,
                 needs_oi=False, description=""):
        self.name = name
        self.display_name = display_name
        self.detect_fn = detect_fn
        self.default_config = default_config
        self.tickers = tickers
        self.needs_oi = needs_oi
        self.description = description


class MarketInfo:
    def __init__(self, name, display_name, db_config, load_bars_fn,
                 load_oi_fn=None, symbols=None):
        self.name = name
        self.display_name = display_name
        self.db_config = db_config
        self.load_bars_fn = load_bars_fn
        self.load_oi_fn = load_oi_fn
        self.symbols = symbols or []


def register_strategy(info):
    _strategies[info.name] = info


def get_strategy(name):
    return _strategies.get(name)


def list_strategies():
    return list(_strategies.values())


def register_market(info):
    _markets[info.name] = info


def list_markets():
    return list(_markets.values())


def map_strategy_to_market(strategy_name, market_name):
    _strategy_market[strategy_name] = market_name


def get_strategies_for_market(market_name):
    return [s for s_name, s in _strategies.items()
            if _strategy_market.get(s_name) == market_name]
