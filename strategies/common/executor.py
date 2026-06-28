"""Executor — управляет капиталом, позициями, портфелем. Broker снаружи."""

import os
import psycopg2
from strategies.common.broker import Position, BrokerSim

RISK_PCT = 0.1          # доля капитала на 1 сделку
MAX_LEVERAGE = 10
COMMISSION = 4

PG_CONFIG = dict(
    host=os.getenv('MOEX_PG_HOST', '10.0.0.60'),
    port=int(os.getenv('MOEX_PG_PORT', '5432')),
    dbname=os.getenv('MOEX_PG_DB', 'moex'),
    user=os.getenv('MOEX_PG_USER', 'user'),
)


class Executor:
    """Управление портфелем, капиталом, позициями.

    Параметры портфеля — из PG futures.portfolio.
    Брокер — снаружи (BrokerSim сейчас, BrokerLive потом).
    """

    def __init__(self, broker=None, initial_capital=100_000):
        self.broker = broker or BrokerSim()
        self.equity = float(initial_capital)
        self.initial = float(initial_capital)
        self.peak = float(initial_capital)
        self.positions = []
        self.trades = []
        self.eq_curve = []
        self._portfolio = {}  # {(ticker, strategy): row}

    # ── Портфель из PG ──────────────────────────────────────────────

    def load_portfolio(self, pg_config=None):
        """Загрузить futures.portfolio в self._portfolio."""
        cfg = pg_config or PG_CONFIG
        conn = psycopg2.connect(**cfg, connect_timeout=5)
        cur = conn.cursor()
        cur.execute("""
            SELECT ticker, strategy, enabled, contracts, weight,
                   params::text,
                   trailing_activation, trailing_trail, timeout_bars
            FROM futures.portfolio
            WHERE enabled = true
            ORDER BY ticker, strategy
        """)
        rows = cur.fetchall()
        cur.close()
        conn.close()

        for r in rows:
            ticker, strategy = r[0], r[1]
            key = (ticker, strategy)
            self._portfolio[key] = {
                'enabled': r[2],
                'contracts': r[3],
                'weight': float(r[4]) if r[4] else 1.0,
                'params': r[5],  # JSON string or None
                'trailing': {
                    'activation': float(r[6]) if r[6] else 0.5,
                    'trail': float(r[7]) if r[7] else 0.3,
                    'timeout': int(r[8]) if r[8] else 12,
                },
            }
        return self._portfolio

    def get_trailing(self, ticker: str, strategy: str) -> dict:
        """Вернуть trailing params для пары тикер+стратегия."""
        row = self._portfolio.get((ticker, strategy), {})
        return row.get('trailing', {'activation': 0.5, 'trail': 0.3, 'timeout': 12})

    # ── Сигналы ─────────────────────────────────────────────────────

    def process_signal(self, signal, bar_idx, specs):
        """Создать позицию по сигналу. Вернуть Position или None."""
        ticker = signal['ticker']
        direction = signal['direction']
        price = signal['entry_price']
        strategy = signal['strategy']

        go = float(specs.get('go', 0))
        step_price = float(specs.get('step_price', 1.0))
        min_step = float(specs.get('min_step', 0.01))
        lot = int(specs.get('lot_volume', 1))

        if go <= 0:
            return None

        # Sizing
        max_by_go = int(self.equity * RISK_PCT / go)
        cv = price * lot
        max_by_lev = max(1, int(self.equity * MAX_LEVERAGE / cv)) if cv > 0 else 1
        shares = max(1, min(max_by_go, max_by_lev))

        # Проверка ГО
        needed = go * shares * 1.2
        if self.equity < needed:
            return None

        # Trailing params из portfolio
        trailing_params = self.get_trailing(ticker, strategy)

        pos = Position(ticker, direction, price, bar_idx, shares, strategy,
                       go, step_price, min_step, trailing_params)
        self.positions.append(pos)
        return pos

    def manage_positions(self, bar_idx, hi, lo, prc):
        """Обновить все открытые позиции через брокера."""
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

    # ── Метрики ─────────────────────────────────────────────────────

    @property
    def max_dd_pct(self):
        peak = self.initial
        max_dd = 0.0
        for eq in self.eq_curve:
            if eq > peak:
                peak = eq
            dd = (peak - eq) / peak * 100
            if dd > max_dd:
                max_dd = dd
        return max_dd

    @property
    def total_return_pct(self):
        return (self.equity / self.initial - 1) * 100

    @property
    def n_trades(self):
        return len(self.trades)

    def summary(self):
        """Краткий отчёт."""
        return {
            'initial': self.initial,
            'equity': round(self.equity, 2),
            'return_pct': round(self.total_return_pct, 2),
            'mdd_pct': round(self.max_dd_pct, 2),
            'n_trades': self.n_trades,
        }
