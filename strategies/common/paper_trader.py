"""PaperTrader — циклический раннер для paper trading.

На каждом тике:
1. Загружает последние бары из PG
2. Вычисляет индикаторы
3. Запускает check_signal() всех стратегий
4. Executor управляет позициями через BrokerSim
5. Сохраняет состояние в PG (на случай рестарта)

use_pg=True  — только PG (препрод/прод)
use_pg=False — только CH (для отладки/тестов)
"""

import os
import json
import numpy as np
import pandas as pd
from datetime import datetime

try:
    import clickhouse_connect as cc
except ImportError:
    cc = None

import psycopg2

from strategies.common.executor import Executor
from strategies.common.broker import BrokerSim

PG_CONFIG = dict(
    host=os.getenv('MOEX_PG_HOST', '10.0.0.60'),
    port=int(os.getenv('MOEX_PG_PORT', '5432')),
    dbname=os.getenv('MOEX_PG_DB', 'moex'),
    user=os.getenv('MOEX_PG_USER', 'user'),
)

N_CONTEXT = 50  # баров контекста для индикаторов


class PaperTrader:
    """Paper trading runner. Синхронный цикл: загрузка → сигналы → управление."""

    def __init__(self, strategies: list, executor: Executor = None, capital=100_000, use_pg=False):
        """
        strategies: [(name, check_signal_fn, tickers, params), ...]
        executor: если None — создаётся с BrokerSim
        use_pg: True = читать данные из PG (препрод), False = из CH
        """
        self.strategies = strategies
        self.executor = executor or Executor(broker=BrokerSim(), initial_capital=capital)
        self.use_pg = use_pg
        if not use_pg and cc is not None:
            self.ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')
        else:
            self.ch = None
        self._context = {}    # {ticker: DataFrame последних N_CONTEXT баров}
        self._specs = {}      # {ticker: specs}

    # ── Инициализация ────────────────────────────────────────────────

    def init(self, pg_config=None):
        """Загрузить портфель, specs, восстановить состояние."""
        self._ensure_state_table()
        self.executor.load_portfolio(pg_config or PG_CONFIG)
        self._load_specs()
        self._restore_state()
        return self

    def _ensure_state_table(self):
        conn = psycopg2.connect(**PG_CONFIG, connect_timeout=5)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS futures.paper_state (
                key   VARCHAR(50) PRIMARY KEY,
                value TEXT
            )
        """)
        conn.commit()
        cur.close()
        conn.close()

    def _load_specs(self):
        """Загрузить ticker_specs для всех тикеров портфеля."""
        tickers = set()
        for _, _, ts, _ in self.strategies:
            tickers.update(ts)
        if not tickers:
            return

        cfg = PG_CONFIG
        conn = psycopg2.connect(**cfg, connect_timeout=5)
        cur = conn.cursor()
        placeholders = ','.join(['%s'] * len(tickers))
        cur.execute(f"""
            SELECT ticker, go, min_step, step_price, lot_volume
            FROM futures.ticker_specs WHERE ticker IN ({placeholders})
        """, list(tickers))
        for r in cur.fetchall():
            self._specs[str(r[0])] = {
                'go': float(r[1]) if r[1] else 0,
                'min_step': float(r[2]) if r[2] else 0.01,
                'step_price': float(r[3]) if r[3] else 1.0,
                'lot_volume': int(r[4]) if r[4] else 1,
            }
        cur.close()
        conn.close()

    # ── Данные из CH ─────────────────────────────────────────────────

    def fetch_bars(self, asset_code: str, n_bars: int = N_CONTEXT):
        """Загрузить последние n_bars 5-минутных баров из CH."""
        return self.ch.query_df(f"""
            SELECT toStartOfInterval(SYSTIME,INTERVAL 5 MINUTE) as bt,
                   argMax(pr_open,SYSTIME) as opn,
                   argMax(pr_high,SYSTIME) as hi,
                   argMax(pr_low,SYSTIME) as lo,
                   argMax(pr_close,SYSTIME) as prc,
                   sum(vol_b) as vb, sum(vol_s) as vs
            FROM moex.tradestats_fo
            WHERE asset_code = '{asset_code}'
              AND SYSTIME > now() - INTERVAL {n_bars * 5 + 60} MINUTE
            GROUP BY bt ORDER BY bt
        """)

    def fetch_bars_from_pg(self, ticker: str, n_bars: int = N_CONTEXT):
        """Загрузить последние n_bars 5-минутных баров из PG futures.prices."""
        conn = psycopg2.connect(**PG_CONFIG, connect_timeout=5)
        cur = conn.cursor()
        cur.execute(
            "SELECT bt, opn, hi, lo, prc, vol FROM futures.prices WHERE ticker=%s ORDER BY bt DESC LIMIT %s",
            (ticker, n_bars),
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows, columns=['bt', 'opn', 'hi', 'lo', 'prc', 'vol'])
        df = df.sort_values('bt').reset_index(drop=True)
        df['vb'] = df['vol'].astype(float) * 0.5
        df['vs'] = df['vol'].astype(float) * 0.5
        df['oi_close'] = 0
        return df

    def compute_indicators(self, ticker: str, df) -> dict:
        """Вычислить индикаторы для сигналов. Вернуть bar_data для check_signal."""
        n = len(df)
        if n < 25:
            return {}

        prc = df['prc'].values.astype(float)
        hi = df['hi'].values.astype(float)
        lo = df['lo'].values.astype(float)
        vb = df['vb'].values.astype(float).clip(0)
        vs = df['vs'].values.astype(float).clip(0)
        vol = np.maximum(vb + vs, 1)

        # CVD z-score (period=20)
        cvd_arr = vb - vs
        dcvd = np.diff(cvd_arr, prepend=cvd_arr[0])
        dcvd_z = np.full(n, np.nan)
        for i in range(20, n):
            s = dcvd[i - 20:i]
            if s.std() > 0:
                dcvd_z[i] = (dcvd[i] - s.mean()) / s.std()

        # SMA(20), Vol MA(20)
        sma20 = np.full(n, np.nan)
        vol_ma20 = np.full(n, np.nan)
        for i in range(20, n):
            sma20[i] = np.mean(prc[i - 20:i])
            vol_ma20[i] = np.mean(vol[i - 20:i])

        last = df.iloc[-1]
        bar_data = {
            'prc': float(last['prc']),
            'hi': float(last['hi']),
            'lo': float(last['lo']),
            'opn': float(last['opn']),
            'vol': float(vol[-1]),
            'vb': float(vb[-1]),
            'vs': float(vs[-1]),
            'dcvd_z': float(dcvd_z[-1]) if np.isfinite(dcvd_z[-1]) else 0,
            'sma20': float(sma20[-1]) if np.isfinite(sma20[-1]) else float(last['prc']),
            'vol_ma20': float(vol_ma20[-1]) if np.isfinite(vol_ma20[-1]) else 1,
            'oi': float(last.get('oi_close', 0)),
        }

        # Histories for Stop Hunt
        if n >= 20:
            bar_data['lo_hist'] = list(lo[-20:])
            bar_data['hi_hist'] = list(hi[-20:])
        bar_data['oi_5ago'] = float(df['oi_close'].iloc[-5]) if 'oi_close' in df.columns and n >= 5 else 0

        # Lunch Reversal
        bt = last.get('bt') or last.name
        if hasattr(bt, 'hour'):
            bar_data['hour'] = bt.hour
            bar_data['minute'] = bt.minute
        # Price at 10:00 MSK (bar 10:00 = index where hour=10, minute=0)
        for j in range(n - 1, -1, -1):
            row = df.iloc[j]
            bt2 = row.get('bt') or row.name
            if hasattr(bt2, 'hour') and bt2.hour == 10 and bt2.minute == 0:
                bar_data['price_10'] = float(row['prc'])
                break
        else:
            bar_data['price_10'] = 0

        return bar_data

    # ── Основной цикл ────────────────────────────────────────────────

    def tick(self, asset_map: dict = None):
        """Один тик: загрузить данные → сигналы → управление позициями.

        asset_map: {ticker: asset_code} для загрузки из CH.
                   Если None — берётся из portfolio + ticker_specs.
        """
        if asset_map is None:
            asset_map = self._build_asset_map()

        # Загружаем данные для всех тикеров
        bar_idx = datetime.now().timestamp()  # уникальный индекс для этого тика
        for ticker, asset in asset_map.items():
            if ticker not in self._specs:
                continue
            if self.use_pg:
                df = self.fetch_bars_from_pg(ticker, N_CONTEXT)
            else:
                df = self.fetch_bars(asset, N_CONTEXT)
            if df.empty or len(df) < 25:
                continue
            self._context[ticker] = df

            # Индикаторы → bar_data
            bar_data = self.compute_indicators(ticker, df)
            if not bar_data:
                continue

            # Сигналы для всех стратегий этого тикера
            specs = self._specs.get(ticker, {})
            for name, check_fn, tickers, params in self.strategies:
                if ticker not in tickers:
                    continue
                signal = check_fn(bar_data, ticker, params)
                if signal:
                    self.executor.process_signal(signal, int(bar_idx), specs, bar_data)

            # Управление позициями — через broker напрямую
            for p in list(self.executor.positions):
                if p.closed:
                    continue
                pnl = self.executor.broker.update(
                    p, int(bar_idx),
                    float(df['hi'].iloc[-1]),
                    float(df['lo'].iloc[-1]),
                    float(df['prc'].iloc[-1]),
                    float(bar_data.get('vol', 0)),
                )
                if p.closed:
                    import numpy as np
                    if np.isfinite(pnl):
                        self.executor.equity += float(pnl)
                    else:
                        p.closed = False
                        continue
                    self.executor.trades.append(p)

            # Cleanup + equity tracking
            self.executor.positions = [p for p in self.executor.positions if not p.closed]
            if self.executor.equity > self.executor.peak:
                self.executor.peak = self.executor.equity
            self.executor.rm.update(self.executor.equity)

    def catch_up(self, asset_map: dict = None):
        """Прогнать всю историю из PG через Backtester (один раз при старте)."""
        # Build a simple backtest on PG data
        import psycopg2
        import clickhouse_connect as cc
        import numpy as np
        import pandas as pd
        from datetime import datetime
        
        pg = psycopg2.connect(host='10.0.0.60', port=5432, dbname='moex', user='user')
        cur = pg.cursor()
        
        bars_dict = {}
        specs = {}
        for ticker in self._specs:
            cur.execute("""
                SELECT bt, opn, hi, lo, prc, vol, vol_b, vol_s, oi
                FROM futures.prices WHERE ticker=%s ORDER BY bt
            """, (ticker,))
            rows = cur.fetchall()
            if len(rows) < 25:
                continue
            df = pd.DataFrame(rows, columns=['bt','opn','hi','lo','prc','vol','vb','vs','oi'])
            df['vol'] = df['vol'].fillna(0).clip(0)
            df['vb'] = df['vb'].fillna(0).clip(0)
            df['vs'] = df['vs'].fillna(0).clip(0)
            
            n = len(df)
            prc = df['prc'].values.astype(float)
            hi = df['hi'].values.astype(float)
            lo = df['lo'].values.astype(float)
            vb = df['vb'].values.astype(float)
            vs = df['vs'].values.astype(float)
            
            # CVD z-score
            cvd_arr = vb - vs
            dcvd = np.diff(cvd_arr, prepend=cvd_arr[0])
            dcvd_z = np.full(n, np.nan)
            for i in range(20, n):
                s = dcvd[i-20:i]
                if s.std() > 0:
                    dcvd_z[i] = (dcvd[i] - s.mean()) / s.std()
            
            # SMA(20), Vol MA(20)
            sma20 = np.full(n, np.nan)
            vol_ma20 = np.full(n, np.nan)
            for i in range(20, n):
                sma20[i] = np.mean(prc[i-20:i])
                vol_ma20[i] = np.mean(vb[i-20:i] + vs[i-20:i]) or 1
            
            df['dcvd_z'] = dcvd_z
            df['sma20'] = sma20
            df['vol_ma20'] = vol_ma20
            df['hour'] = df['bt'].dt.hour
            df['minute'] = df['bt'].dt.minute
            
            bars_dict[ticker] = df
            specs[ticker] = self._specs[ticker]
        
        cur.close()
        pg.close()
        
        if not bars_dict:
            return
        
        # Run Engine
        from strategies.common.engine import PortfolioEngine
        from strategies.common.broker import BrokerSim
        engine = PortfolioEngine(self.strategies, broker=BrokerSim(), capital=int(self.executor.equity))
        engine.executor.rm = self.executor.rm
        engine.run(bars_dict, specs)
        
        # Copy state
        self.executor.equity = engine.executor.equity
        self.executor.peak = engine.executor.peak
        self.executor.positions = engine.executor.positions
        self.executor.trades = engine.executor.trades
        self.executor.eq_curve = engine.executor.eq_curve
        self._save_state()

    def run(self, n_ticks: int = None, asset_map: dict = None):
        """Запустить N тиков (None = бесконечно)."""
        tick_count = 0
        while n_ticks is None or tick_count < n_ticks:
            try:
                self.tick(asset_map)
                tick_count += 1
                self._save_state()
            except Exception as e:
                print(f'[PaperTrader] tick {tick_count} error: {e}')
                import traceback
                traceback.print_exc()
            if n_ticks is not None:
                break

    # ── Asset map ────────────────────────────────────────────────────

    def _build_asset_map(self) -> dict:
        """Build {ticker: asset_code} from PG ticker_specs."""
        tickers = set()
        for _, _, ts, _ in self.strategies:
            tickers.update(ts)
        cfg = PG_CONFIG
        conn = psycopg2.connect(**cfg, connect_timeout=5)
        cur = conn.cursor()
        placeholders = ','.join(['%s'] * len(tickers)) if tickers else ''
        if not placeholders:
            cur.close()
            conn.close()
            return {}
        cur.execute(f"""
            SELECT ticker, asset_code FROM futures.ticker_specs
            WHERE ticker IN ({placeholders})
        """, list(tickers))
        am = {str(r[0]): str(r[1]) for r in cur.fetchall() if r[1]}
        cur.close()
        conn.close()
        return am

    # ── Сохранение/восстановление состояния ──────────────────────────

    def _save_state(self):
        """Сохранить капитал и открытые позиции в PG."""
        conn = psycopg2.connect(**PG_CONFIG, connect_timeout=5)
        cur = conn.cursor()

        # Убедиться что таблица есть
        cur.execute("""
            CREATE TABLE IF NOT EXISTS futures.paper_state (
                key   VARCHAR(50) PRIMARY KEY,
                value TEXT
            )
        """)

        # Капитал
        cur.execute("""
            INSERT INTO futures.paper_state (key, value)
            VALUES ('capital', %s)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        """, (str(round(self.executor.equity, 2)),))
        # Peak (для расчёта DD)
        cur.execute("""
            INSERT INTO futures.paper_state (key, value)
            VALUES ('peak', %s)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        """, (str(round(self.executor.peak, 2)),))

        # Открытые позиции (сериализовать)
        positions = []
        for p in self.executor.positions:
            if not p.closed:
                positions.append({
                    'ticker': p.ticker,
                    'direction': p.direction,
                    'entry_price': p.entry_price,
                    'entry_bar': p.entry_bar,
                    'shares': p.shares,
                    'strategy': p.strategy,
                    'go': p.go,
                    'step_price': p.step_price,
                    'min_step': p.min_step,
                    'best_price': p.best_price,
                    'best_abs': p.best_abs,
                    'trail_activated': p.trail_activated,
                })
        cur.execute("""
            INSERT INTO futures.paper_state (key, value)
            VALUES ('positions', %s)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        """, (json.dumps(positions),))

        conn.commit()
        cur.close()
        conn.close()

    def _restore_state(self):
        """Восстановить капитал и позиции из PG."""
        conn = psycopg2.connect(**PG_CONFIG, connect_timeout=5)
        cur = conn.cursor()
        cur.execute("SELECT key, value FROM futures.paper_state")
        rows = cur.fetchall()
        cur.close()
        conn.close()

        state = {r[0]: r[1] for r in rows}

        # Капитал
        if 'capital' in state:
            self.executor.equity = float(state['capital'])
            self.executor.initial = self.executor.equity
            self.executor.peak = self.executor.equity

        # Позиции
        if 'positions' in state:
            import json
            from strategies.common.broker import Position
            positions = json.loads(state['positions'])
            for pd in positions:
                pos = Position(
                    pd['ticker'], pd['direction'], pd['entry_price'],
                    pd['entry_bar'], pd['shares'], pd['strategy'],
                    pd['go'], pd['step_price'], pd['min_step'],
                )
                pos.best_price = pd.get('best_price', 0.0)
                pos.best_abs = pd.get('best_abs', 0.0)
                pos.trail_activated = pd.get('trail_activated', False)
                self.executor.positions.append(pos)

    # ── Статус ───────────────────────────────────────────────────────

    def status(self) -> dict:
        """Текущее состояние."""
        open_positions = [p for p in self.executor.positions if not p.closed]
        return {
            'equity': round(self.executor.equity, 2),
            'return_pct': round(self.executor.total_return_pct, 2),
            'mdd_pct': round(self.executor.max_dd_pct, 2),
            'open_positions': len(open_positions),
            'total_trades': len(self.executor.trades),
            'positions': [
                {'ticker': p.ticker, 'direction': p.direction,
                 'strategy': p.strategy, 'entry': p.entry_price,
                 'shares': p.shares, 'pnl': round(p.pnl, 2)}
                for p in open_positions
            ],
        }
