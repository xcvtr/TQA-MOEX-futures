"""Portfolio Engine — универсальный loop для всех стратегий."""
from strategies.common.executor import Executor
from strategies.common.broker import BrokerSim

class PortfolioEngine:
    """Один loop по барам, вызывает все стратегии на каждом баре."""
    
    def __init__(self, strategies: list, config: dict, capital=100000):
        """
        strategies: [(name, check_signal_fn, tickers, params), ...]
        config: PG config per ticker (GO, lot, etc.)
        """
        self.strategies = strategies
        self.config = config
        self.executor = Executor(initial_capital=capital)
        self.broker = BrokerSim()
    
    def run(self, bars_dict: dict):
        """
        bars_dict: {ticker: DataFrame with OHLCV}
        Запускает все стратегии на всём периоде.
        """
        # Get max length across all tickers
        max_len = max(len(df) for df in bars_dict.values())
        
        for bar_idx in range(50, max_len):
            for name, check_fn, tickers, params in self.strategies:
                for ticker in tickers:
                    df = bars_dict.get(ticker)
                    if df is None or bar_idx >= len(df): continue
                    bar = df.iloc[bar_idx]
                    
                    signal = check_fn(bar, ticker, params)
                    if signal:
                        specs = self.config.get(ticker, {})
                        self.executor.process_signal(signal, bar_idx, specs, self.broker)
            
            # Manage all open positions
            for ticker, df in bars_dict.items():
                if bar_idx < len(df):
                    bar = df.iloc[bar_idx]
                    specs = self.config.get(ticker, {})
                    self.executor.manage_positions(bar, bar_idx, specs, self.broker)
        
        return self.executor
