"""Portfolio Engine — универсальный loop: Broker → Executor → стратегии."""

from strategies.common.executor import Executor
from strategies.common.broker import BrokerSim


class PortfolioEngine:
    """Loop по барам, вызывает все стратегии на каждом баре.

    strategies: [(name, check_signal_fn, tickers, params), ...]
    """

    def __init__(self, strategies: list, broker=None, capital=100_000):
        self.strategies = strategies
        self.executor = Executor(broker=broker or BrokerSim(), initial_capital=capital)

    def run(self, bars_dict: dict, ticker_specs: dict = None):
        """bars_dict: {ticker: DataFrame}. Запускает все стратегии на всём периоде."""
        max_len = max(len(df) for df in bars_dict.values())

        for bar_idx in range(50, max_len):
            # Сигналы
            for name, check_fn, tickers, params in self.strategies:
                for ticker in tickers:
                    df = bars_dict.get(ticker)
                    if df is None or bar_idx >= len(df):
                        continue
                    bar = df.iloc[bar_idx]
                    signal = check_fn(bar, ticker, params)
                    if signal:
                        specs = (ticker_specs or {}).get(ticker, {})
                        self.executor.process_signal(signal, bar_idx, specs)

            # Управление позициями
            for ticker, df in bars_dict.items():
                if bar_idx < len(df):
                    bar = df.iloc[bar_idx]
                    hi = bar.get('hi', bar.get('high', 0))
                    lo = bar.get('lo', bar.get('low', 0))
                    prc = bar.get('prc', bar.get('close', 0))
                    self.executor.manage_positions(bar_idx, hi, lo, prc)

        return self.executor
