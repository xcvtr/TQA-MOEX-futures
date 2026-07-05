"""Portfolio Engine — универсальный loop: Broker → Executor → стратегии."""

import numpy as np
from strategies.common.executor import Executor
from strategies.common.broker import BrokerSim


class PortfolioEngine:
    """Loop по барам, вызывает все стратегии на каждом баре.

    strategies: [(name, check_signal_fn, tickers, params), ...]
    """

    def __init__(self, strategies: list, broker=None, capital=100_000):
        self.strategies = strategies
        self.executor = Executor(broker=broker or BrokerSim(), initial_capital=capital)
        self._pending = {}  # {ticker: [signal_dict, ...]} — сигналы, ждущие исполнения на open следующего бара

    def _build_bar(self, df, bar_idx, price_10_arr=None):
        """Собрать bar_data с контекстом для всех стратегий."""
        row = df.iloc[bar_idx]
        bar = {
            'prc': float(row.get('prc', row.get('close', 0))),
            'hi': float(row.get('hi', row.get('high', 0))),
            'lo': float(row.get('lo', row.get('low', 0))),
            'opn': float(row.get('opn', row.get('open', 0))),
            'vol': float(row.get('vol', 0)),
            'vb': float(row.get('vb', 0)),
            'vs': float(row.get('vs', 0)),
            'oi': float(row.get('oi', 0)),
            'dcvd_z': float(row.get('dcvd_z', 0)) if 'dcvd_z' in row else 0,
            'vol_ma20': float(row.get('vol_ma20', 1)) if 'vol_ma20' in row else 1,
            'sma20': float(row.get('sma20', float(row.get('prc', 0)))) if 'sma20' in row else float(row.get('prc', 0)),
        }

        # Stop Hunt histories (last 20 bars)
        n = bar_idx + 1
        lo_col = 'lo' if 'lo' in df else 'low'
        hi_col = 'hi' if 'hi' in df else 'high'
        if n >= 20:
            bar['lo_hist'] = list(df[lo_col].iloc[bar_idx-20:bar_idx].values.astype(float))
            bar['hi_hist'] = list(df[hi_col].iloc[bar_idx-20:bar_idx].values.astype(float))
        else:
            bar['lo_hist'] = []
            bar['hi_hist'] = []

        # Churn: OI 5 bars ago
        if n >= 5 and 'oi' in df:
            bar['oi_5ago'] = float(df['oi'].iloc[bar_idx-5])

        # Lunch Reversal: pre-computed price_10
        if price_10_arr and bar_idx < len(price_10_arr):
            bar['price_10'] = price_10_arr[bar_idx]
        else:
            bar['price_10'] = 0
        # Hour/minute from timestamp
        bt = row.get('bt') if hasattr(row, 'bt') else row.name
        if hasattr(bt, 'hour'):
            bar['hour'] = bt.hour
            bar['minute'] = bt.minute
        else:
            bar['hour'] = 0
            bar['minute'] = 0

        return bar

    def run(self, bars_dict: dict, ticker_specs: dict = None):
        """bars_dict: {ticker: DataFrame}. Запускает все стратегии на всём периоде."""
        max_len = max(len(df) for df in bars_dict.values())

        # Pre-compute price_10 for each ticker (цена на 10:00 MSK)
        price_10_cache = {}
        for ticker, df in bars_dict.items():
            if 'bt' in df:
                hours = df['bt'].dt.hour
                minutes = df['bt'].dt.minute
                at_10 = (hours == 10) & (minutes == 0)
                prc_col = df['prc'].values.astype(float) if 'prc' in df else df['close'].values.astype(float)
                p10 = np.where(at_10, prc_col, 0.0)
                # Forward-fill
                for i in range(1, len(p10)):
                    if p10[i] == 0.0:
                        p10[i] = p10[i-1]
                price_10_cache[ticker] = p10.tolist()
            else:
                price_10_cache[ticker] = [0.0] * len(df)

        for bar_idx in range(50, max_len):
            # Pre-build bar_data once per ticker
            bars_for_ticker = {}
            for ticker in bars_dict:
                df = bars_dict[ticker]
                if bar_idx >= len(df):
                    continue
                p10_arr = price_10_cache.get(ticker, [])
                bars_for_ticker[ticker] = self._build_bar(df, bar_idx, p10_arr)

            # Исполнить pending сигналы на open этого бара
            for ticker, pending_list in list(self._pending.items()):
                bar = bars_for_ticker.get(ticker)
                if bar is None:
                    continue
                specs = (ticker_specs or {}).get(ticker, {})
                min_step = float(specs.get('min_step', 0.01))
                # Пробуем сигналы по очереди, пока один не откроется
                for pending in pending_list:
                    if ticker in [p.ticker for p in self.executor.positions if not p.closed]:
                        break  # уже есть позиция по этому тикеру
                    direction = pending['direction']
                    if direction == 'long':
                        pending['entry_price'] = float(bar['opn']) + 1.0 * min_step
                    else:
                        pending['entry_price'] = float(bar['opn']) - 1.0 * min_step
                    if self.executor.process_signal(pending, bar_idx, specs, bar):
                        break  # первая успешная стратегия заняла тикер
                del self._pending[ticker]

            # Сигналы — становятся pending для исполнения на следующем баре
            for name, check_fn, tickers, params in self.strategies:
                for ticker in tickers:
                    bar = bars_for_ticker.get(ticker)
                    if bar is None:
                        continue
                    signal = check_fn(bar, ticker, params)
                    if signal:
                        # Проверка: нет ли уже открытой позиции по этому тикеру
                        has_pos = any(not p.closed and p.ticker == ticker for p in self.executor.positions)
                        if not has_pos:
                            if ticker not in self._pending:
                                self._pending[ticker] = []
                            self._pending[ticker].append(signal)

            # Управление позициями — передаём бары ТОЛЬКО своего тикера
            for p in list(self.executor.positions):
                if p.closed:
                    continue
                df = bars_dict.get(p.ticker)
                if df is None or bar_idx >= len(df):
                    continue
                bar = df.iloc[bar_idx]
                hi = float(bar.get('hi', bar.get('high', 0)))
                lo = float(bar.get('lo', bar.get('low', 0)))
                prc = float(bar.get('prc', bar.get('close', 0)))
                vol = float(bar.get('vol', 0))
                pnl = self.executor.broker.update(p, bar_idx, hi, lo, prc, vol)
                if p.closed:
                    if np.isfinite(pnl):
                        self.executor.equity += float(pnl)
                    else:
                        p.closed = False
                        continue
                    self.executor.trades.append(p)

            # Cleanup closed positions
            self.executor.positions = [p for p in self.executor.positions if not p.closed]

            # Equity curve
            self.executor.eq_curve.append(self.executor.equity)
            if self.executor.equity > self.executor.peak:
                self.executor.peak = self.executor.equity
            self.executor.rm.update(self.executor.equity)

        # Cleanup pending signals at end of data
        for ticker, pending_list in list(self._pending.items()):
            df = bars_dict.get(ticker)
            if df is not None:
                bar = df.iloc[-1]
                specs = (ticker_specs or {}).get(ticker, {})
                min_step = float(specs.get('min_step', 0.01))
                for pending in pending_list:
                    if ticker in [p.ticker for p in self.executor.positions if not p.closed]:
                        break
                    direction = pending['direction']
                    pending['entry_price'] = float(bar.get('prc', bar.get('close', 0)))
                    if self.executor.process_signal(pending, max_len - 1, specs, {}):
                        break

        return self.executor
