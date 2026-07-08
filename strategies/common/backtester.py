"""Backtester — загрузка данных, запуск Engine, сбор метрик."""

import os
import numpy as np
import clickhouse_connect as cc
import psycopg2
from collections import Counter

from strategies.common.engine import PortfolioEngine
from strategies.common.broker import BrokerSim

# Стратегии
from strategies.stop_hunt.prod.engine import check_signal as sh_check
from strategies.cvd.prod.engine import check_signal as cvd_check
from strategies.churn.prod.engine import check_signal as churn_check
from strategies.lunch_rev.prod.engine import check_signal as lunch_check
from strategies.impulse_return.prod.engine import check_signal as impulse_check

CH_CONFIG = dict(host=os.getenv('MOEX_CH_HOST', '10.0.0.60'), port=8123, database='moex')
PG_CONFIG = dict(
    host=os.getenv('MOEX_PG_HOST', '10.0.0.60'),
    port=int(os.getenv('MOEX_PG_PORT', '5432')),
    dbname=os.getenv('MOEX_PG_DB', 'moex'),
    user=os.getenv('MOEX_PG_USER', 'user'),
)

STRATEGY_MAP = {
    'stop_hunt': sh_check,
    'cvd': cvd_check,
    'churn': churn_check,
    'lunch_rev': lunch_check,
    'impulse_return': impulse_check,
}

# ── Asset → ticker mapping (from PG ticker_specs, и fallback) ────────
ASSET_TO_TICKER = {
    'GAZR': 'GZ', 'SBRF': 'SR', 'VTBR': 'VB', 'WHEAT': 'W4',
    'Si': 'Si', 'CR': 'CR', 'NG': 'NG',
    'BR': 'BR', 'GL': 'GL', 'GD': 'GD', 'Eu': 'Eu',
}


class Backtester:
    """Загружает данные, запускает портфельный тест, возвращает метрики."""

    def __init__(self, capital=100_000, commission=4):
        self.capital = capital
        self.commission = commission
        self.ch = cc.get_client(**CH_CONFIG)

    # ── Портфель из PG ──────────────────────────────────────────────

    def load_portfolio(self, pg_config=None) -> list:
        """Загрузить портфель из PG. Вернуть [(asset_code, ticker, [strategies]), ...]."""
        cfg = pg_config or PG_CONFIG
        conn = psycopg2.connect(**cfg, connect_timeout=5)
        cur = conn.cursor()

        # Группируем стратегии по тикеру
        cur.execute("""
            SELECT p.ticker, p.strategy, COALESCE(s.asset_code, p.ticker)
            FROM futures.portfolio p
            LEFT JOIN futures.ticker_specs s ON p.ticker = s.ticker
            WHERE p.enabled = true
            ORDER BY p.ticker, p.strategy
        """)
        rows = cur.fetchall()
        cur.close()
        conn.close()

        # Группировка: ticker → [strategies]
        from collections import defaultdict
        groups = defaultdict(list)
        asset_map = {}
        for ticker, strategy, asset in rows:
            groups[ticker].append(strategy)
            asset_map[ticker] = asset or ticker

        return [(asset_map[t], t, ss) for t, ss in groups.items()]

    # ── Загрузка данных ─────────────────────────────────────────────

    def load_data(self, portfolio, start='2024-10-01') -> dict:
        """Загрузить 5м OHLCV+vol из tradestats_fo, предвычислить индикаторы.

        Возвращает {ticker: DataFrame} с колонками: bt, opn, hi, lo, prc, vol,
        vb, vs, oi, dcvd_z, vol_ma20, sma20, hour, minute.
        """
        data = {}
        for asset, ticker, strategies in portfolio:
            df = self.ch.query_df(f"""
                SELECT toStartOfInterval(SYSTIME,INTERVAL 5 MINUTE) as bt,
                       argMax(pr_open,SYSTIME) as opn,
                       argMax(pr_high,SYSTIME) as hi,
                       argMax(pr_low,SYSTIME) as lo,
                       argMax(pr_close,SYSTIME) as prc,
                       sum(vol_b) as vb, sum(vol_s) as vs,
                       argMax(oi_close,SYSTIME) as oi
                FROM moex.tradestats_fo
                WHERE asset_code='{asset}' AND SYSTIME >= '{start}'
                GROUP BY bt ORDER BY bt
            """)
            if df.empty or len(df) < 1000:
                continue

            n = len(df)
            prc = df['prc'].values.astype(float)
            hi = df['hi'].values.astype(float)
            lo = df['lo'].values.astype(float)
            vb = df['vb'].values.astype(float).clip(0)
            vs = df['vs'].values.astype(float).clip(0)
            vol = vb + vs
            vol = np.where(vol <= 0, 1, vol)

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

            # Вспомогательные колонки
            df['vol'] = vol
            df['dcvd_z'] = dcvd_z
            df['sma20'] = sma20
            df['vol_ma20'] = vol_ma20
            # Timezone: CH в Asia/Irkutsk (+08). MOEX торгует 10:00-18:45 MSK = 15:00-23:45 IRK
            df['hour'] = df['bt'].dt.hour
            df['minute'] = df['bt'].dt.minute
            # Фильтр: только MOEX основная + вечерняя сессия (убираем овернайт 05:00-14:00 IRK)
            trading_mask = (df['hour'] >= 15) | (df['hour'] <= 4)
            before = len(df)
            df = df[trading_mask].copy()
            print(f"  {ticker}: {before} → {len(df)} bars (filtered off-hours)", flush=True)

            data[ticker] = df

        return data

    # ── Спецификации из PG ──────────────────────────────────────────

    def load_specs(self, tickers, pg_config=None) -> dict:
        """Загрузить ticker_specs для списка тикеров."""
        cfg = pg_config or PG_CONFIG
        conn = psycopg2.connect(**cfg, connect_timeout=5)
        cur = conn.cursor()

        placeholders = ','.join(['%s'] * len(tickers))
        cur.execute(f"""
            SELECT ticker, go, min_step, step_price, lot_volume
            FROM futures.ticker_specs
            WHERE ticker IN ({placeholders})
        """, list(tickers))
        rows = cur.fetchall()
        cur.close()
        conn.close()

        specs = {}
        for r in rows:
            specs[str(r[0])] = {
                'go': float(r[1]) if r[1] else 0,
                'min_step': float(r[2]) if r[2] else 0.01,
                'step_price': float(r[3]) if r[3] else 1.0,
                'lot_volume': int(r[4]) if r[4] else 1,
            }
        return specs

    # ── Запуск ──────────────────────────────────────────────────────

    def run(self, portfolio=None, start='2024-10-01', capital=None):
        """Полный цикл: portfolio → data → engine → metrics."""
        capital = capital or self.capital

        if portfolio is None:
            portfolio = self.load_portfolio()

        # Загружаем данные
        data = self.load_data(portfolio, start)
        if not data:
            return {'error': 'no data loaded'}

        tickers = list(data.keys())

        # Загружаем specs
        specs = self.load_specs(tickers)

        # Собираем strategies для Engine
        strategies = []
        for asset, ticker, strats in portfolio:
            if ticker not in data:
                continue
            for sname in strats:
                fn = STRATEGY_MAP.get(sname)
                if fn:
                    strategies.append((sname, fn, [ticker], None))

        if not strategies:
            return {'error': 'no strategies loaded'}

        # Запускаем Engine
        broker = BrokerSim(commission=self.commission)
        engine = PortfolioEngine(strategies, broker=broker, capital=capital)
        engine.executor.load_portfolio()
        result = engine.run(data, specs)

        # Метрики
        executor = result
        trades = executor.trades
        eq = executor.eq_curve

        metrics = {
            'capital': capital,
            'equity': round(executor.equity, 2),
            'return_pct': round(executor.total_return_pct, 2),
            'mdd_pct': round(executor.max_dd_pct, 2),
            'n_trades': len(trades),
            'calmar': round(executor.total_return_pct / executor.max_dd_pct, 3) if executor.max_dd_pct > 0 else 0,
        }

        # Доп. метрики
        if trades:
            pnls = np.array([t.pnl for t in trades])
            wins = pnls[pnls > 0]
            losses = pnls[pnls <= 0]
            metrics['win_rate'] = round(len(wins) / len(pnls) * 100, 1)
            metrics['profit_factor'] = round(abs(sum(wins) / sum(losses)), 3) if len(losses) > 0 and sum(losses) != 0 else float('inf')
            metrics['avg_win'] = round(float(np.mean(wins)), 2) if len(wins) > 0 else 0
            metrics['avg_loss'] = round(float(np.mean(losses)), 2) if len(losses) > 0 else 0

            # Sharpe (по дневным equity изменениям)
            if len(eq) > 1:
                eq_arr = np.array(eq)
                daily_ret = np.diff(eq_arr) / eq_arr[:-1]
                sharpe = np.mean(daily_ret) / np.std(daily_ret) * np.sqrt(252) if np.std(daily_ret) > 0 else 0
                metrics['sharpe'] = round(float(sharpe), 3)

            # По стратегиям и тикерам
            strat_pnl = Counter()
            strat_trades = Counter()
            strat_wins = Counter()
            ticker_pnl = Counter()
            ticker_trades = Counter()
            ticker_wins = Counter()
            for t in trades:
                strat_pnl[t.strategy] += t.pnl
                strat_trades[t.strategy] += 1
                if t.pnl > 0:
                    strat_wins[t.strategy] += 1
                ticker_pnl[t.ticker] += t.pnl
                ticker_trades[t.ticker] += 1
                if t.pnl > 0:
                    ticker_wins[t.ticker] += 1
            metrics['by_strategy'] = {
                s: {
                    'trades': strat_trades[s],
                    'wins': strat_wins[s],
                    'wr': round(strat_wins[s] / strat_trades[s] * 100, 1) if strat_trades[s] > 0 else 0,
                    'pnl': round(strat_pnl[s], 2),
                }
                for s in sorted(strat_pnl)
            }
            metrics['by_ticker'] = {
                t: {
                    'trades': ticker_trades[t],
                    'wins': ticker_wins[t],
                    'wr': round(ticker_wins[t] / ticker_trades[t] * 100, 1) if ticker_trades[t] > 0 else 0,
                    'pnl': round(ticker_pnl[t], 2),
                    'avg_pnl': round(ticker_pnl[t] / ticker_trades[t], 2) if ticker_trades[t] > 0 else 0,
                }
                for t in sorted(ticker_pnl)
            }

        return metrics
