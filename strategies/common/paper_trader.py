#!/usr/bin/env python3
"""Universal paper trader — runs every tick, reads portfolio from PG.

Loads portfolio → loads latest bar → checks signals → manages positions.
Works with any strategy that has check_signal(bar_data, ticker) -> dict|None.
"""
import os, sys, json, time, logging
from datetime import datetime, timezone, date
from decimal import Decimal
from collections import defaultdict

# ── Project root ──────────────────────────────────────────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))

import clickhouse_connect as cc
import psycopg2
from psycopg2.extras import execute_values

# ── Strategy imports ──────────────────────────────────────────────────────
STRATEGY_MAP = {}

def _load_strategies():
    """Lazy-import strategies — only when needed."""
    from strategies.stop_hunt.prod.engine import check_signal as sh_check
    from strategies.cvd.prod.engine import check_signal as cvd_check
    from strategies.impulse_return.prod.engine import check_signal as imp_check
    from strategies.dragon.prod.engine import check_signal as dragon_check
    from strategies.oi.prod.engine import check_signal as oi_check
    from strategies.oi_dom.prod.engine import check_signal as oi_dom_check
    STRATEGY_MAP['stop_hunt'] = sh_check
    STRATEGY_MAP['cvd'] = cvd_check
    STRATEGY_MAP['impulse_return'] = imp_check
    STRATEGY_MAP['dragon'] = dragon_check
    STRATEGY_MAP['oi'] = oi_check
    STRATEGY_MAP['oi_dom'] = oi_dom_check

# ── Config ────────────────────────────────────────────────────────────────
CH_HOST = os.getenv('MOEX_CH_HOST', '10.0.0.60')
CH_PORT = 8123
CH_DB = 'moex'

PG_HOST = os.getenv('MOEX_PG_HOST', '10.0.0.60')
PG_PORT = int(os.getenv('MOEX_PG_PORT', '5432'))
PG_DB = os.getenv('MOEX_PG_DB', 'moex')
PG_USER = os.getenv('MOEX_PG_USER', 'postgres')
PG_PASS = os.getenv('MOEX_PG_PASSWORD', '')

TRADE_COST = 4  # fallback, per-ticker из PG
MAX_CONTRACTS = 1000
# Per-ticker лимит контрактов — ОБНОВЛЯЕТСЯ из реальных дневных объёмов AlgoPack при старте.
# Старые значения (100/80) душили компаунд: volume cap 0.2×tick_volume давал макс 11 лотов на BR.
TICKER_LIMITS = {'BR': 100, 'NG': 100, 'SV': 80, 'RN': 80, 'RI': 50, 'TT': 30}
VOLUME_CAP = 0.2  # 20% of M1 volume (0.5 for Si)
LIQ_FRAC = 0.05   # доля реального ДНЕВНОГО объёма (ISS VOLTODAY, активный контракт) как лимит лотов.
                  # Реальная ёмкость ОГРОМНАЯ: BR 813K, NG 629K, SV 415K лотов/день (ISS API!).
                  # tradestats_fo (AlgoPack) стух 13.07 и занижал в 180 раз — больше не источник.
                  # 5% дн.объёма: BR 40K, NG 31K, SV 21K лотов — не ограничивает до eq ~100M.
                  # Бэктест (реальная ёмкость): CAGR +1415-2128%, MTM 18.5%.
SIZING_EQ_CAP = 10_000_000  # кап eq для sizing лотов: при eq>10M лоты НЕ растут (slippage убивает edge).
                            # Оптимум бэктеста: CAGR +382%, MTM DD 15% (риски 10/7/4, pyr3, cap 10M).
TIMEOUT_BARS = 12  # дефолт, берётся из PG если есть
STATE_KEY = ''  # модульный уровень — задаётся в __main__ или run_paper_trader.py
BROKER = os.environ.get('MOEX_BROKER', 'sim')  # 'sim' (close+1тик) или 'dom' (исполнение по стакану из PG futures.dom)

# ── DOM-брокер (исполнение по стакану) ──
DOM_BROKER = None
def get_dom_broker():
    """Ленивая инициализация BrokerDOM (только для BROKER='dom')."""
    global DOM_BROKER
    if DOM_BROKER is None:
        from strategies.common.broker_dom import BrokerDOM
        DOM_BROKER = BrokerDOM(commission=4)
    return DOM_BROKER

# ── Реальные дневные объёмы (AlgoPack контракты) — для лимита лотов ──
DAILY_VOL = {}

def load_daily_volumes():
    """Загрузить реальный дневной объём (контракты/день) активного контракта из ISS API.
    ВАЖНО: tradestats_fo (AlgoPack) стух 13.07.2026 и содержит лишь часть сделок —
    его объёмы занижены в 15-180 раз. Правильный источник: ISS VOLTODAY (объём сегодня).
    mt5 vol = tick_volume (число сделок), НЕ контракты — использовать его нельзя.
    Перпетуалы (USDRUBF и др.) не в ISS futures — оценка mt5 tick_vol × 1440 × 20."""
    global DAILY_VOL
    try:
        # Только АКТИВНЫЙ контракт (из PG active_symbols)
        try:
            import psycopg2
            conn_pg = psycopg2.connect(host=PG_HOST, port=PG_PORT, dbname=PG_DB, user=PG_USER, password=PG_PASS, connect_timeout=3)
            cur = conn_pg.cursor()
            cur.execute("SELECT prefix, symbol FROM futures.active_symbols")
            active_map = dict(cur.fetchall())
            cur.close(); conn_pg.close()
        except Exception:
            active_map = {}
        # Реальный объём из ISS API: СРЕДНИЙ дневной объём за 5 дней (candles D1).
        # ВАЖНО: VOLTODAY (объём сегодня) утром мал (рынок не открыт) — лимиты прыгали ×17.
        # Дневные свечи дают полный объём за завершённые дни.
        import urllib.request, json as _json
        new_vol = {}
        for prefix, sym in active_map.items():
            vol = 0.0
            try:
                req = urllib.request.Request(
                    f'https://iss.moex.com/iss/engines/futures/markets/forts/securities/{sym}/candles.json?iss.meta=off&interval=24',
                    headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    d = _json.loads(resp.read().decode())
                cols = d['candles']['columns']
                vi = cols.index('volume')
                vols = [float(r[vi]) for r in d['candles']['data'][-5:] if r[vi] and r[vi] > 0]
                if vols:
                    vol = sum(vols) / len(vols)
            except Exception:
                pass
            if vol <= 0:
                # Fallback: VOLTODAY (объём сегодня) — но может быть мал утром
                try:
                    req = urllib.request.Request(
                        f'https://iss.moex.com/iss/engines/futures/markets/forts/securities/{sym}.json?iss.meta=off&iss.only=marketdata',
                        headers={'User-Agent': 'Mozilla/5.0'})
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        d = _json.loads(resp.read().decode())
                    info = dict(zip(d['marketdata']['columns'], d['marketdata']['data'][0]))
                    vol = float(info.get('VOLTODAY') or 0)
                except Exception:
                    vol = 0
            if vol > 0:
                new_vol[prefix] = vol
        # Фолбэк: tradestats_fo (если ISS не дал)
        if not new_vol:
            ch = cc.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)
            rows = ch.query("""
                SELECT secid, round(sum(vol) / NULLIF(count(DISTINCT tradedate), 0))
                FROM moex.tradestats_fo
                WHERE tradedate >= toDate(now()) - INTERVAL 400 DAY AND vol > 0
                GROUP BY secid
            """).result_rows
            sec_vol = {r[0]: float(r[1]) for r in rows}
            for prefix, sym in active_map.items():
                if sym in sec_vol:
                    new_vol[prefix] = sec_vol[sym]
        # перпетуалы: оценка
        ch = cc.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)
        rows2 = ch.query("""
            SELECT ticker, round(avg(vol)) FROM moex.mt5_continuous
            WHERE ticker IN ('USDRUBF','EURRUBF','CNYRUBF','GLDRUBF','Si','Eu','CNY')
              AND bt >= now() - INTERVAL 400 DAY GROUP BY ticker
        """).result_rows
        for tk, avg_vol in rows2:
            new_vol.setdefault(tk, float(avg_vol) * 1440 * 20)
        ch.close()
        # мутируем существующий dict (imported references остаются валидными)
        DAILY_VOL.clear()
        DAILY_VOL.update(new_vol)
        # маппинг AlgoPack SILV → наш SV, GOLD → GD
        alias = {'SILV': 'SV', 'GOLD': 'GD'}
        for src, dst in alias.items():
            if src in DAILY_VOL:
                DAILY_VOL[dst] = DAILY_VOL[src]
        # Применить к TICKER_LIMITS: лимит = 10% дневного объёма (ёмкость рынка)
        for tk in list(TICKER_LIMITS.keys()):
            if tk in DAILY_VOL:
                TICKER_LIMITS[tk] = max(10, int(DAILY_VOL[tk] * LIQ_FRAC))
    except Exception as e:
        log.warning("load_daily_volumes failed (keep old limits): %s", e)

# PnL formula: (exit-entry)/ms*sp*contracts - TC*contracts
# MOEX STEPPRICE = RUB per tick per contract. NO *lot.
# sp is per-contract. See CH moex.securities for reference.
log = logging.getLogger('paper_trader')


# ── PG helpers ────────────────────────────────────────────────────────────

def pg_conn():
    return psycopg2.connect(host=PG_HOST, port=PG_PORT, dbname=PG_DB,
                            user=PG_USER, password=PG_PASS, connect_timeout=5)

def load_portfolio():
    """Load enabled portfolio entries from PG.
    Returns {ticker: [(strategy_name, weight, contracts, trailing_params), ...]}
    """
    conn = pg_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT ticker, strategy, contracts, weight,
               COALESCE(trailing_activation, 0.5), COALESCE(trailing_trail, 0.3),
               COALESCE(timeout_bars, 12), params
        FROM futures.portfolio
        WHERE enabled = true
        ORDER BY ticker, strategy
    """)
    rows = cur.fetchall()
    cur.close(); conn.close()

    portfolio = defaultdict(list)
    asset_map = {}
    for r in rows:
        ticker, strategy = r[0], r[1]
        # Parse params JSONB for stop_loss and other extras
        params = r[7] if isinstance(r[7], dict) else {}
        if isinstance(r[7], str):
            try:
                params = json.loads(r[7])
            except (json.JSONDecodeError, TypeError):
                params = {}
        portfolio[ticker].append({
            'strategy': strategy,
            'contracts': r[2],  # None = use fixed contract count from ticker_specs
            'weight': float(r[3]) if r[3] else 1.0,
            'trailing_activation': float(r[4]) if r[4] else 0.5,
            'trailing_trail': float(r[5]) if r[5] else 0.3,
            'timeout_bars': int(r[6]) if r[6] else 12,
            'stop_loss': float(params.get('stop_loss_pct', 0.7)) / 100.0,
            'tf': int(params.get('tf', 5)),  # detect timeframe (minutes)
            'risk': float(params.get('risk', 0.2)),  # risk fraction of equity (как бэктест)
            'params': params,  # ВЕСЬ JSONB — direction/thr/max_positions для check_signal (live = бэктест)
        })
    return dict(portfolio)


def load_specs(tickers):
    """Load ticker specs from PG."""
    if not tickers:
        return {}
    conn = pg_conn()
    cur = conn.cursor()
    placeholders = ','.join(['%s'] * len(tickers))
    cur.execute(f"""
        SELECT ticker, go, step_price, min_step, lot_volume,
               COALESCE(pct, 1.0),
               COALESCE(asset_code, ticker),
               COALESCE(fee_entry, 4.0)
        FROM futures.ticker_specs
        WHERE ticker IN ({placeholders})
    """, list(tickers))
    rows = cur.fetchall()
    cur.close(); conn.close()
    return {
        str(r[0]): {
            'go': float(r[1]) if r[1] else 0,
            'sp': float(r[2]) if r[2] else 1.0,
            'ms': float(r[3]) if r[3] else 0.01,
            'lot': int(r[4]) if r[4] else 1,
            'pct': float(r[5]) if r[5] else 1.0,
            'asset': str(r[6]),
            'fee': float(r[7]) if r[7] else 4.0,
        }
        for r in rows
    }


def load_state():
    """Load current paper trader state from PG."""
    tbl = 'futures.paper_state' + ('' if not STATE_KEY else '_' + STATE_KEY)
    try:
        conn = pg_conn()
        cur = conn.cursor()
        cur.execute(f"SELECT capital, equity, peak, positions_json, bar_idx, next_id FROM {tbl} ORDER BY updated_at DESC LIMIT 1")
        r = cur.fetchone()
        cur.close(); conn.close()
        if r:
            cap, eq, pk, pos_json, bi, nid = r
            return {'capital': float(cap), 'equity': float(eq), 'peak': float(pk),
                    'positions': json.loads(pos_json) if pos_json else [],
                    'bar_idx': bi or 0, 'next_id': nid or 1}
    except Exception:
        pass
    return {'capital': 200000.0, 'equity': 200000.0, 'positions': [], 'peak': 200000.0,
            'trades': [], 'bar_idx': 0, 'next_id': 1}


def save_state(state):
    """Save paper trader state to PG."""
    tbl_state = 'futures.paper_state' + ('' if not STATE_KEY else '_' + STATE_KEY)
    tbl_trades = 'futures.paper_trades' + ('' if not STATE_KEY else '_' + STATE_KEY)
    try:
        conn = pg_conn()
        cur = conn.cursor()
        max_pos = max((p['id'] for p in state.get('positions', [])), default=0)
        for t in state.get('trades', []):
            if t.get('saved', False):
                continue
            cur.execute(f""" 
                INSERT INTO {tbl_trades}
                (ticker, strategy, direction, entry_price, exit_price, entry_time, exit_time,
                 pnl_rub, signal_type, status, exit_reason)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'closed', %s)
            """, (t['ticker'], t.get('strategy', 'stop_hunt'), t['direction'], t['entry_price'], t.get('exit_price'),
                  t.get('entry_time', datetime.now(timezone.utc)), t.get('exit_time'),
                  t.get('pnl'), t.get('exit_reason', ''), t.get('exit_reason', '')))
            t['saved'] = True
        conn.commit()
        # Delete old state, insert new
        cur.execute(f"DELETE FROM {tbl_state}")
        cur.execute(f"""
            INSERT INTO {tbl_state} (capital, equity, peak, mtm_equity, mtm_peak, positions_json, bar_idx, next_id, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
        """, (round(state['capital'], 2), round(state['equity'], 2), round(state.get('peak', state['equity']), 2),
              round(state.get('mtm_equity', state['equity']), 2), round(state.get('mtm_peak', state['equity']), 2),
              json.dumps(_json_safe(state['positions'])), state['bar_idx'], state.get('next_id', 1)))
        conn.commit()
        cur.close(); conn.close()
    except Exception as e:
        log.warning("Save state failed: %s", e)


def _json_safe(obj):
    """Convert non-serializable objects for JSON dump."""
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_json_safe(v) for v in obj]
    elif isinstance(obj, datetime):
        return obj.isoformat()
    return obj


def calc_mtm_equity(capital, positions, bar_data, specs):
    """Calculate MTM equity = capital + unrealized PnL of open positions."""
    mtm_pnl = 0.0
    for p in positions:
        if p.get('closed', False):
            continue
        ticker = p['ticker']
        bd = bar_data.get(ticker)
        s = specs.get(ticker, {})
        if not bd:
            continue
        sp = s.get('sp', 1)
        ms = s.get('ms', 0.01)
        entry = p['entry_price']
        prc = bd['prc']
        contracts = p.get('contracts', 1)
        pct = p.get('pct', 1.0)
        rem = max(0.001, p.get('rem', 1))
        trade_cost = specs.get(ticker, {}).get("fee", TRADE_COST) * 2 * contracts
        
        if p['direction'] == 'long':
            pnl = (prc - entry) / ms * sp * pct * rem * contracts - trade_cost
        else:  # short
            pnl = (entry - prc) / ms * sp * pct * rem * contracts - trade_cost
        mtm_pnl += pnl
    return capital + mtm_pnl


# ── CH helpers ────────────────────────────────────────────────────────────

def get_latest_bars(ticker, asset, n_bars=1500):
    """Get last N 1-min OHLC bars.
    
    Priority:
    0. CH moex.mt5_continuous (FINAM, live, M1→M5 OHLC)
    1. PG futures.bars_1m (live, autopurge 2mo, для paper trader)
    2. CH moex.mt5_bars (полная история, для backtest)
    3. CH moex.tradestats_fo (AlgoPack real OHLC)
    4. CH moex.prices_5min (ISS snapshots, fallback)
    Returns DataFrame or None.
    """
    now = datetime.now(timezone.utc)
    
    # ── 0. PG bars_1m (primary) ──────────────────────────────────────────
    try:
        import psycopg2
        conn = psycopg2.connect(host=PG_HOST, port=PG_PORT, dbname=PG_DB, user=PG_USER, password=PG_PASS, connect_timeout=3)
        cur = conn.cursor()
        cur.execute(f"""
            SELECT bt, opn, hi, lo, prc
            FROM futures.bars_1m
            WHERE ticker = %s
            ORDER BY bt DESC LIMIT %s
        """, (ticker, n_bars + 5))
        rows = cur.fetchall()
        cur.close(); conn.close()
        
        if rows:
            import pandas as pd
            df = pd.DataFrame(rows, columns=['bt', 'opn', 'hi', 'lo', 'prc'])
            df = df.sort_values('bt').reset_index(drop=True)
            age = (now - df.iloc[-1]['bt'].replace(tzinfo=timezone.utc)).total_seconds() / 60
            if age < 10:  # < 10 min — свежие данные
                return df
            log.info("PG bars_1m age=%.0fm, trying next source", age)
    except Exception as e:
        log.warning("PG bars_1m error for %s: %s", ticker, e)
    
    # ── 1. PG (для paper trader) ────────────────────────────────────────
    try:
        import psycopg2
        conn = psycopg2.connect(host=PG_HOST, port=PG_PORT, dbname=PG_DB, user=PG_USER, password=PG_PASS, connect_timeout=3)
        cur = conn.cursor()
        cur.execute(f"""
            SELECT bt, opn, hi, lo, prc
            FROM futures.bars_1m
            WHERE ticker = %s
            ORDER BY bt DESC LIMIT %s
        """, (ticker, n_bars + 5))
        rows = cur.fetchall()
        cur.close(); conn.close()
        
        if rows:
            import pandas as pd
            df = pd.DataFrame(rows, columns=['bt', 'opn', 'hi', 'lo', 'prc'])
            df = df.sort_values('bt').reset_index(drop=True)
            age = (now - df.iloc[-1]['bt'].replace(tzinfo=timezone.utc)).total_seconds() / 60
            if age < 3:  # < 3 min — свежие данные
                return df
            log.info("PG bars_1m age=%.0fm, trying next source", age)
    except Exception as e:
        log.warning("PG bars_1m error for %s: %s", ticker, e)
    
    # ── 2. CH mt5_bars ──────────────────────────────────────────────────────
    ch = cc.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)
    try:
        df = ch.query_df(f"""
            SELECT bt, opn, hi, lo, prc
            FROM moex.mt5_continuous WHERE ticker = '{ticker}'
            ORDER BY bt DESC LIMIT {n_bars + 5}
        """)
        if not df.empty:
            df = df.sort_values('bt').reset_index(drop=True)
            age = (now - df.iloc[-1]['bt'].replace(tzinfo=timezone.utc)).total_seconds() / 60
            if age < 10:
                ch.close(); return df
            log.info("mt5_continuous age=%.0fm, trying next", age)
        ch.close()
    except Exception as e:
        log.warning("mt5_continuous error for %s: %s", ticker, e)
        ch.close()
    
    # ── 3. tradestats_fo ────────────────────────────────────────────────────
    try:
        ch = cc.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)
        df = ch.query_df(f"""
            SELECT toStartOfInterval(SYSTIME, INTERVAL 5 MINUTE) as bt5,
                   argMin(pr_open, SYSTIME) as opn,
                   max(pr_high) as hi, min(pr_low) as lo,
                   argMax(pr_close, SYSTIME) as prc_close
            FROM moex.tradestats_fo WHERE asset_code = '{asset}'
            GROUP BY bt5 ORDER BY bt5 DESC LIMIT {n_bars + 5}
        """)
        if not df.empty:
            df = df.sort_values('bt5').reset_index(drop=True)
            df.rename(columns={'bt5': 'bt', 'prc_close': 'prc'}, inplace=True)
            age = (now - df.iloc[-1]['bt'].replace(tzinfo=timezone.utc)).total_seconds() / 60
            if age < 60:
                ch.close(); return df
        ch.close()
    except Exception as e:
        log.warning("tradestats_fo error for %s/%s: %s", ticker, asset, e)
        ch.close()
    
    # ── 4. prices_5min (fallback) ────────────────────────────────────────────
    try:
        ch = cc.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)
        df = ch.query_df(f"""
            SELECT toStartOfInterval(bt, INTERVAL 5 MINUTE) as bt5,
                   argMin(prc, bt) as opn, max(prc) as hi, min(prc) as lo,
                   argMax(prc, bt) as prc_close
            FROM moex.prices_5min WHERE ticker = '{ticker}'
            GROUP BY bt5 ORDER BY bt5 DESC LIMIT {n_bars + 5}
        """)
        if not df.empty:
            df = df.sort_values('bt5').reset_index(drop=True)
            df.rename(columns={'bt5': 'bt', 'prc_close': 'prc'}, inplace=True)
        ch.close()
        return df
    except Exception as e:
        log.error("CH error for %s (all sources): %s", ticker, e)
        ch.close()
        return None


def get_volume_data(ticker, n_bars=55):
    """Get volume data (vol_b, vol_s) from tradestats_fo for CVD calculation.
    Returns (vol_hist, vol_b_hist, vol_s_hist) or ([], [], []) if no data.
    """
    try:
        ch = cc.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)
        rows = ch.query(f"""
            SELECT SYSTIME, vol, vol_b, vol_s
            FROM moex.tradestats_fo
            WHERE asset_code = '{ticker}'
            ORDER BY SYSTIME DESC
            LIMIT {n_bars + 10}
        """).result_rows
        ch.close()
        if not rows:
            return [], [], []
        # Sort chronologically
        rows = list(reversed(rows))
        vol = [float(r[1]) for r in rows if r[1] is not None]
        vol_b = [float(r[2]) for r in rows if r[2] is not None]
        vol_s = [float(r[3]) for r in rows if r[3] is not None]
        return vol, vol_b, vol_s
    except Exception as e:
        log.warning("Volume data error for %s: %s", ticker, e)
        return [], [], []


def fetch_day_net(ticker):
    """Накопленный дневной дисбаланс физлиц — КАК В БЭКТЕСТЕ.

    day_net = (cur_b - day_start_b) / total_oi * 100
    где day_start_b = (buy_fiz - sell_fiz) первой записи дня,
    total_oi = buy_fiz + sell_fiz + buy_yur + sell_yur последней записи.

    Граница дня — как в бэктесте: день начинается в 07:00 UTC (=15:00 IRK),
    irk_day(ts) = int((ts - 7*3600) // 86400). НЕ today() (00:00 IRK)!

    Отрицательное = физлица за день НАКОПИЛИ продажи (паника → long).
    Из moex.futoi.
    """
    try:
        ch = cc.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)
        rows = ch.query(f"""
            SELECT toUnixTimestamp(toDateTime(bt)), buy_fiz, sell_fiz, buy_yur, sell_yur
            FROM moex.futoi
            WHERE ticker = '{ticker}'
              AND toUnixTimestamp(toDateTime(bt)) >= %(start_ts)s
            ORDER BY bt
        """, {'start_ts': int(datetime.now(timezone.utc).timestamp()) - 72 * 3600}).result_rows
        ch.close()
        if not rows:
            return None
        # Дневной старт: первая запись текущего IRK-дня (граница 07:00 UTC = 15:00 IRK)
        DAY_SEC = 86400
        cur_day = int((rows[-1][0] - 7 * 3600) // DAY_SEC)
        day_rows = [r for r in rows if int((r[0] - 7 * 3600) // DAY_SEC) == cur_day]
        if len(day_rows) < 2:
            return None
        first = day_rows[0]
        day_start_net = int(first[1]) - int(first[2])
        # Текущее состояние: последняя запись дня
        last = day_rows[-1]
        cur_net = int(last[1]) - int(last[2])
        total = int(last[1]) + int(last[2]) + int(last[3]) + int(last[4])
        if total <= 0:
            return None
        return (cur_net - day_start_net) / total * 100.0
    except Exception as e:
        log.warning("fetch_day_net error for %s: %s", ticker, e)
        return None


def fetch_dom_imbalance(ticker):
    """Imbalance стакана (ask-bid)/(ask+bid) за последние ~10 минут из PG futures.dom.

    Положительное = ask-heavy (покупки агрессивны) → подтверждает long.
    Отрицательное = bid-heavy (продажи) → подтверждает short.
    Возвращает float или None при ошибке/нет данных.
    """
    try:
        conn = psycopg2.connect(host=PG_HOST, port=PG_PORT, dbname=PG_DB,
                                user=PG_USER, password=PG_PASS, connect_timeout=3)
        cur = conn.cursor()
        cur.execute("""
            SELECT side, sum(volume) FROM futures.dom
            WHERE ticker = %s AND ts >= now() - interval '10 minutes'
            GROUP BY side
        """, (ticker,))
        rows = cur.fetchall()
        conn.close()
        bid = ask = 0.0
        for side, vol in rows:
            if side == 1:
                bid = float(vol)
            elif side == 2:
                ask = float(vol)
        total = bid + ask
        if total <= 0:
            return None
        return (ask - bid) / total
    except Exception as e:
        log.warning("fetch_dom_imbalance error for %s: %s", ticker, e)
        return None


def is_roll_day(ticker):
    """День экспирации/ролла контракта.

    Ролл continuous ALLFUT происходит в LASTTRADEDATE (последний день торговли
    контрактом): вечером этого дня склейка переключается на новый контракт с
    гэпом. Сигналы в этот день ненадёжны, а открытые позиции надо закрыть
    заранее (см. manage_positions: roll_close).

    ВАЖНО: признак ТОЛЬКО по expiration_date из PG (ISS LASTTRADEDATE).
    Скачки цены >2% НЕ использовать — SV/NG/BR волатильны, ложные срабатывания
    в 20-25% дней (проверено 2026-08-07: SV 51/197 дней).
    """
    try:
        conn = psycopg2.connect(host=PG_HOST, port=PG_PORT, dbname=PG_DB,
                                user=PG_USER, password=PG_PASS, connect_timeout=3)
        cur = conn.cursor()
        cur.execute("SELECT expiration_date FROM futures.ticker_specs WHERE ticker = %s", (ticker,))
        row = cur.fetchone()
        conn.close()
        if row and row[0]:
            return row[0] == date.today()
        return False
    except Exception as e:
        log.warning("is_roll_day error for %s: %s", ticker, e)
        return False


def calc_dcvd_z(vol_b_hist, vol_s_hist, period=20):
    """Calculate CVD z-score from vol_b/vol_s history.
    Returns z-score (float) or 0 if insufficient data.
    """
    if len(vol_b_hist) < period + 1 or len(vol_s_hist) < period + 1:
        return 0.0
    cvd = [vol_b_hist[i] - vol_s_hist[i] for i in range(len(vol_b_hist))]
    # z-score of last value relative to recent history
    recent = cvd[-(period+1):-1]
    if not recent:
        return 0.0
    mean = sum(recent) / len(recent)
    var = sum((x - mean) ** 2 for x in recent) / len(recent)
    std = var ** 0.5
    if std < 0.001:
        return 0.0
    return (cvd[-1] - mean) / std


def resample_m1_to_tf(df, tf_min):
    """Resample M1 DataFrame (bt,opn,hi,lo,prc) to tf_min bars. Returns list of dicts."""
    bars = []
    g = {}
    for _, r in df.iterrows():
        ts = r['bt']
        tm = ts.hour * 60 + ts.minute
        km = (tm // tf_min) * tf_min
        k = ts.replace(minute=km % 60, hour=km // 60, second=0)
        if k not in g:
            g[k] = {'ts': k, 'opn': float(r['opn']), 'hi': float(r['hi']),
                    'lo': float(r['lo']), 'prc': float(r['prc'])}
        else:
            gg = g[k]
            gg['hi'] = max(gg['hi'], float(r['hi']))
            gg['lo'] = min(gg['lo'], float(r['lo']))
            gg['prc'] = float(r['prc'])
    for k in sorted(g.keys()):
        bars.append(g[k])
    return bars


# ── Position management ──────────────────────────────────────────────────

def _close_pos(p, close, ms, sp, specs, ticker, reason):
    """Единое закрытие позиции: по стакану (dom) или close±ms (sim).
    Возвращает (exit_price, pnl)."""
    fee = specs.get(ticker, {}).get('fee', TRADE_COST)
    if BROKER == 'dom':
        bk = get_dom_broker()
        try:
            if p['direction'] == 'long':
                exit_px, pnl, _ = bk.exit_long(ticker, p['entry_price'], p.get('contracts', 1), ms, sp, p.get('pct', 1.0), fee)
            else:
                exit_px, pnl, _ = bk.exit_short(ticker, p['entry_price'], p.get('contracts', 1), ms, sp, p.get('pct', 1.0), fee)
            # Если стакан пуст — fallback на close±ms
            if pnl == 0.0 and exit_px == p['entry_price']:
                raise ValueError('empty book')
            return exit_px, pnl + p.get('part_pnl', 0)
        except Exception:
            pass
    # sim fallback
    exit_px = close - ms if p['direction'] == 'long' else close + ms
    pnl = (exit_px - p['entry_price']) / ms * sp * p.get('pct', 1.0) * max(0.001, p.get('rem', 1)) * p.get('contracts', 1) - fee * 2 * p.get('contracts', 1)
    return exit_px, pnl + p.get('part_pnl', 0)


def manage_positions(positions, bar_data, specs, bar_idx):
    """Update all open positions. Return closed trades."""
    closed = []
    for p in list(positions):
        if p.get('closed', False):
            continue
        ticker = p['ticker']
        bd = bar_data.get(ticker)
        if not bd:
            continue
        s = specs.get(ticker, {})
        sp, ms = s.get('sp', 1), s.get('ms', 0.01)
        lot = s.get('lot', 1)
        hi, lo, close = bd['hi'], bd['lo'], bd['prc']
        try:
            et = p['entry_time']
            if isinstance(et, str):
                et = datetime.fromisoformat(et)
            age_sec = (datetime.now(timezone.utc) - et.replace(tzinfo=timezone.utc)).total_seconds()
        except Exception:
            age_sec = 0
        if p['entry_bar'] >= bar_idx and age_sec < 60:
            continue

        # Внешний аудит (moex_trade_audit.py): force_close → принудительное закрытие
        if p.get('force_close'):
            exit_px, pnl = _close_pos(p, close, ms, sp, specs, ticker, 'audit_close')
            p['pnl'] = pnl
            p['exit_price'] = exit_px
            p['exit_reason'] = 'audit_close'
            p['closed'] = True
            p['exit_bar'] = bar_idx
            closed.append(p)
            log.warning("AUDIT force_close %s %s", ticker, p.get('direction'))
            continue

        # Ролл/экспирация: закрыть позицию заранее (склейка контракта вечером)
        if is_roll_day(p.get('ticker', '')):
            exit_px, pnl = _close_pos(p, close, ms, sp, specs, ticker, 'roll_close')
            p['pnl'] = pnl
            p['exit_price'] = exit_px
            p['exit_reason'] = 'roll_close'
            p['closed'] = True
            p['exit_bar'] = bar_idx
            closed.append(p)
            continue
        
        # Timeout (by bars or by real time)
        age_bars = bar_idx - p['entry_bar']
        timeout_triggered = age_bars >= p.get('timeout_bars', 12) or age_sec > p.get('timeout_bars', 12) * 300
        # Для OI: max_hold_h (часы) — как бэктест (age_sec > max_hold_h*3600)
        if p.get('strategy') == 'oi' and not timeout_triggered:
            max_hold_h = p.get('max_hold_h', 120)
            timeout_triggered = age_sec > max_hold_h * 3600
        if timeout_triggered:
            exit_px, pnl = _close_pos(p, close, ms, sp, specs, ticker, 'timeout')
            p['pnl'] = pnl
            p['exit_price'] = exit_px
            p['exit_reason'] = 'timeout'
            p['closed'] = True
            p['exit_bar'] = bar_idx
            closed.append(p)
            continue

        # Пирамидинг для OI: добавка лота при движении +pyra_pct от входа (как бэктест pyr/pyra_pct)
        if p.get('strategy') == 'oi' and not p.get('closed'):
            pyra_max = int(p.get('pyra_max', 0))
            pyra_pct = float(p.get('pyra_pct', 0.5))
            pyra_added = int(p.get('pyra_added', 0))
            # Базовая цена для порога пирамиды = ПЕРВОНАЧАЛЬНЫЙ вход (как бэктест entry_p).
            # НЕ пересчитанная средняя! Иначе пороги сдвигаются и live≠бэктест.
            pyra_base = p.get('pyra_base_price') or p['entry_price']
            if pyra_added < pyra_max:
                gain_pct = 0.0
                if p['direction'] == 'long':
                    if hi and pyra_base > 0:
                        gain_pct = (hi - pyra_base) / pyra_base * 100
                else:
                    if lo and pyra_base > 0:
                        gain_pct = (pyra_base - lo) / pyra_base * 100
                if gain_pct >= (pyra_added + 1) * pyra_pct:
                    add_lots = int(p.get('base_contracts', p.get('contracts', 1)))
                    max_lots = TICKER_LIMITS.get(ticker, MAX_CONTRACTS)
                    new_contracts = min(p.get('contracts', 1) + add_lots, max_lots)
                    if new_contracts > p.get('contracts', 1):
                        # Цена добавки: по стакану (ask для long, bid для short) или hi/lo как раньше
                        if BROKER == 'dom':
                            bk = get_dom_broker()
                            bids, asks = bk._get_book(ticker)
                            if p['direction'] == 'long' and asks:
                                pyra_px = asks[0][0]  # лучший ask
                            elif p['direction'] == 'short' and bids:
                                pyra_px = bids[0][0]  # лучший bid
                            else:
                                pyra_px = hi if p['direction'] == 'long' else lo
                        else:
                            pyra_px = hi if p['direction'] == 'long' else lo
                        # Пересчёт средней цены входа (как бэктест pyra_prices):
                        # avg = (old_ct * old_entry + add_lots * pyra_px) / new_ct
                        old_ct = p.get('contracts', 1)
                        old_entry = p['entry_price']
                        new_entry = (old_ct * old_entry + add_lots * pyra_px) / new_contracts
                        p['entry_price'] = round(new_entry, 6)
                        p['contracts'] = new_contracts
                        p['pyra_added'] = pyra_added + 1
                        # Сохраняем базовую цену для следующих порогов (если ещё не задана)
                        if not p.get('pyra_base_price'):
                            p['pyra_base_price'] = pyra_base
                        log.info("PYRAMID %s %s: %d→%d @%.4f (avg %.4f, base %.4f, gain=%.2f%%)",
                                 ticker, p['direction'], old_ct, new_contracts, pyra_px, p['entry_price'], pyra_base, gain_pct)

        # Выход по ОИ (обратное условие входа) — для стратегии oi
        # long закрывается, когда day_net ≥ exit_thr (физ начали покупать — паника кончилась)
        # short закрывается, когда day_net ≤ -exit_thr (физ начали продавать)
        if p.get('strategy') == 'oi':
            dn = bd.get('day_net')
            exit_thr = p.get('exit_thr', 3)
            if dn is not None:
                oi_exit = (p['direction'] == 'long' and dn >= exit_thr) or \
                          (p['direction'] == 'short' and dn <= -exit_thr)
                if oi_exit:
                    # slippage на выходе: 1 тик (как бэктест: exit = close - ms для long)
                    # или исполнение по стакану (BROKER='dom')
                    if BROKER == 'dom':
                        bk = get_dom_broker()
                        if p['direction'] == 'long':
                            exit_px, pnl_dom, slip = bk.exit_long(ticker, p['entry_price'], p.get('contracts', 1), ms, sp, p.get('pct', 1.0), specs.get(p.get('ticker',''), {}).get('fee', TRADE_COST))
                        else:
                            exit_px, pnl_dom, slip = bk.exit_short(ticker, p['entry_price'], p.get('contracts', 1), ms, sp, p.get('pct', 1.0), specs.get(p.get('ticker',''), {}).get('fee', TRADE_COST))
                        pnl = pnl_dom + p.get('part_pnl', 0)
                    else:
                        exit_px = close - ms if p['direction'] == 'long' else close + ms
                        pnl = (exit_px - p['entry_price']) / ms * sp * p.get('pct', 1.0) * max(0.001, p.get('rem', 1)) * p.get('contracts', 1) - specs.get(p.get('ticker',''), {}).get('fee', TRADE_COST) * 2 * p.get('contracts', 1)
                        pnl += p.get('part_pnl', 0)
                    p['pnl'] = pnl
                    p['exit_price'] = exit_px
                    p['exit_reason'] = 'oi_exit'
                    p['closed'] = True
                    p['exit_bar'] = bar_idx
                    closed.append(p)
                    continue

        # Trailing TP
        if p['direction'] == 'long':
            if not p.get('trailing_activated'):
                if hi >= p['entry_price'] * (1 + p.get('activation', 0.005)):
                    p['trailing_activated'] = True
                    p['trailing_level'] = hi * (1 - p.get('trail', 0.003))
            elif p['trailing_level'] and hi >= p['trailing_level'] / (1 - p.get('trail', 0.003)):
                p['trailing_level'] = hi * (1 - p.get('trail', 0.003))

            exit_price = None
            if p.get('trailing_activated') and lo <= p.get('trailing_level', 0):
                exit_price = p['trailing_level']
                p['exit_reason'] = 'trailing_tp'
            elif lo <= (p.get('pyra_base_price') or p['entry_price']) * (1 - p.get('stop_loss', 0.007)):
                exit_price = lo
                p['exit_reason'] = 'stop_loss'

            if exit_price:
                # Исполнение по стакану (dom) или по цене срабатывания (sim)
                if BROKER == 'dom':
                    exit_px2, pnl = _close_pos(p, close, ms, sp, specs, ticker, p['exit_reason'])
                    p['exit_price'] = exit_px2
                else:
                    pnl = (exit_price - p['entry_price']) / ms * sp * p.get('pct', 1.0) * max(0.001, p.get('rem', 1)) * p.get('contracts', 1) - specs.get(p.get('ticker',''), {}).get('fee', TRADE_COST) * 2 * p.get('contracts', 1)
                    pnl += p.get('part_pnl', 0)
                    p['exit_price'] = exit_price
                p['pnl'] = pnl
                p['closed'] = True
                p['exit_bar'] = bar_idx
                closed.append(p)

        elif p['direction'] == 'short':
            if not p.get('trailing_activated'):
                if lo <= p['entry_price'] * (1 - p.get('activation', 0.005)):
                    p['trailing_activated'] = True
                    p['trailing_level'] = lo * (1 + p.get('trail', 0.003))
            elif p['trailing_level'] and lo <= p['trailing_level'] / (1 + p.get('trail', 0.003)):
                p['trailing_level'] = lo * (1 + p.get('trail', 0.003))

            exit_price = None
            if p.get('trailing_activated') and hi >= p.get('trailing_level', 0):
                exit_price = p['trailing_level']
                p['exit_reason'] = 'trailing_tp'
            elif hi >= (p.get('pyra_base_price') or p['entry_price']) * (1 + p.get('stop_loss', 0.007)):
                exit_price = hi
                p['exit_reason'] = 'stop_loss'

            if exit_price:
                # Исполнение по стакану (dom) или по цене срабатывания (sim)
                if BROKER == 'dom':
                    exit_px2, pnl = _close_pos(p, close, ms, sp, specs, ticker, p['exit_reason'])
                    p['exit_price'] = exit_px2
                else:
                    pnl = (p['entry_price'] - exit_price) / ms * sp * p.get('pct', 1.0) * max(0.001, p.get('rem', 1)) * p.get('contracts', 1) - specs.get(p.get('ticker',''), {}).get('fee', TRADE_COST) * 2 * p.get('contracts', 1)
                    pnl += p.get('part_pnl', 0)
                    p['exit_price'] = exit_price
                p['pnl'] = pnl
                p['closed'] = True
                p['exit_bar'] = bar_idx
                closed.append(p)

    return closed


# ── Main tick ─────────────────────────────────────────────────────────────

def run_tick(strategy_filter=None, mode=None):
    """Run paper trader tick.
    
    Args:
        strategy_filter: filter by strategy name
        mode: None (full: manage + detect), 'tick' (only manage positions),
              'detect' (only check signals)
    """
    _load_strategies()

    # Load state
    state = load_state()
    positions = state.get('positions', [])
    equity = state.get('equity', 200000.0)
    capital = state.get('capital', 200000.0)
    peak = state.get('peak', 200000.0)
    trades = state.get('trades', [])
    next_id = state.get('next_id', 1)

    # Load portfolio
    portfolio = load_portfolio()
    if not portfolio:
        log.warning("Empty portfolio")
        return
    
    # Filter by strategy if specified
    if strategy_filter:
        portfolio = {t: [s for s in strats if s.get('strategy') == strategy_filter]
                     for t, strats in portfolio.items()}
        portfolio = {t: s for t, s in portfolio.items() if s}
        if not portfolio:
            log.warning(f"No portfolio entries for strategy '{strategy_filter}'")
            return

    tickers = list(portfolio.keys())
    specs = load_specs(tickers)
    if not specs:
        log.warning("No specs loaded")
        return

    # ── Freshness check ────────────────────────────────────────────────────
    now = datetime.now(timezone.utc)
    MARKET_OPEN_IRK = 15   # MOEX дневная сессия 10:00 MSK = 15:00 IRK
    MARKET_CLOSE_IRK = 5   # вечерняя сессия до 23:50 MSK = 04:50 IRK (след. сутки)

    # Проверка: рынок открыт? (MOEX: дневная 15:00-23:45 IRK + вечерняя 00:00-04:50 IRK)
    irk_hour = now.hour + 8  # UTC → IRK
    if irk_hour >= 24:
        irk_hour -= 24
    market_open = (irk_hour >= MARKET_OPEN_IRK or irk_hour < MARKET_CLOSE_IRK)
    if not market_open:
        log.info("MOEX market closed (IRK hour=%d). Skipping new signals.", irk_hour)
    
    # Load latest bars for all tickers
    bar_data = {}
    max_bar_idx = 0
    for ticker in tickers:
        s = specs.get(ticker)
        if not s:
            continue
        df = get_latest_bars(ticker, s['asset'])
        if df is None or df.empty:
            continue
        bar_idx = len(df)  # последний бар
        last = df.iloc[-1]
        second_last = df.iloc[-2] if len(df) >= 2 else last
        
        # Close history (for impulse_return)
        close_hist = [float(v) for v in df['prc'].iloc[:-1].values]
        
        # Volume data (for CVD + impulse_return)
        vol, vol_b, vol_s = get_volume_data(s.get('asset', ticker))
        
        # CVD z-score — отключить если tradestats_fo stale (>30ч)
        vol_age_hours = 0
        if vol:
            try:
                ch_tmp = cc.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)
                r = ch_tmp.query(f"SELECT max(SYSTIME) FROM moex.tradestats_fo WHERE asset_code='{s.get('asset', ticker)}'").result_rows
                ch_tmp.close()
                if r and r[0][0]:
                    vol_age_hours = (datetime.now(timezone.utc) - r[0][0].replace(tzinfo=timezone.utc)).total_seconds() / 3600
            except Exception:
                pass
        
        if vol_age_hours > 30:
            dcvd_z = 0.0  # stale volume — отключаем CVD
        else:
            dcvd_z = calc_dcvd_z(vol_b, vol_s) if vol_b else 0.0
        
        # Volume history for impulse_return
        vol_hist = vol[:-1] if len(vol) > 1 else []
        current_vol = vol[-1] if vol else 100
        
        # Resample M1 -> detect tf (согласовано с бэктестом portfolio_run.py)
        tf = s.get('tf', 5)
        detect_bars = resample_m1_to_tf(df, tf)
        if not detect_bars:
            continue
        last_d = detect_bars[-1]  # последний ПОЛНЫЙ detect бар
        # Для bars_list НЕ включаем незакрытый текущий бар:
        # последний detect бар считается закрытым только если его период завершён.
        # Здесь last_d — последний полный бар (df уже содержит только закрытые M1).
        
        bar_data[ticker] = {
            'bt': last['bt'],
            'opn': float(last['opn']),
            'hi': float(last['hi']),
            'lo': float(last['lo']),
            'prc': float(last['prc']),  # последний M1 close — тики для SL/TP (как бэктест)
            'prc_prev': float(second_last['prc']),
            'vol': current_vol,
            'dcvd_z': dcvd_z,
            'close_hist': [float(b['prc']) for b in detect_bars[:-1]],
            'vol_hist': vol_hist,
            'bars_list': detect_bars,  # detect бары для check_signal (как бэктест: dh + [db])
        }
        # Build hi/lo history for signal check (need 60+ for lookback=40 + buffer)
        hi_hist = [float(b['hi']) for b in detect_bars[:-1]][-61:]
        lo_hist = [float(b['lo']) for b in detect_bars[:-1]][-61:]
        bar_data[ticker]['hi_hist'] = hi_hist
        bar_data[ticker]['lo_hist'] = lo_hist
        # OI day_net для oi/oi_dom стратегий: накопление нетто-физ за день из futoi
        oi_strategies = [s for s in portfolio.get(ticker, []) if s.get('strategy') in ('oi', 'oi_dom')]
        if oi_strategies:
            dn = fetch_day_net(ticker)
            if dn is not None:
                bar_data[ticker]['day_net'] = dn
            # dom подтверждение для oi_dom
            if any(s.get('strategy') == 'oi_dom' for s in oi_strategies):
                imb = fetch_dom_imbalance(ticker)
                if imb is not None:
                    bar_data[ticker]['dom_imb'] = imb
        max_bar_idx = max(max_bar_idx, bar_idx)

    state['bar_idx'] = max_bar_idx

    # Manage existing positions
    if mode != 'detect':
        closed = manage_positions(positions, bar_data, specs, max_bar_idx)
        for c in closed:
            c['exit_time'] = datetime.now(timezone.utc)
            c['saved'] = False
            c['id'] = next_id
            next_id += 1
            equity += c['pnl']
            trades.append(c)
            log.info("Closed %s %s PnL=%.0f (%s)", c['ticker'], c['direction'], c['pnl'], c.get('exit_reason', ''))

    # Check for new signals (only when market is open)
    if mode != 'tick' and market_open:
        # Аудит-пауза (moex_trade_audit.py): DD>20% или проблемы → флаг-файл → не открывать
        pause_file = os.path.expanduser('~/.hermes/scripts/.moex_pause_flag')
        audited_paused = os.path.exists(pause_file)
        if audited_paused:
            log.warning("AUDIT pause: .moex_pause_flag существует — новые сигналы заблокированы")
        for ticker in tickers:
            bd = bar_data.get(ticker)
            if not bd:
                continue
            # Пирамидинг: максимум позиций на тикер (из params, default 1)
            active = [p for p in positions if not p.get('closed', False) and p.get('ticker') == ticker]
            s = specs.get(ticker, {})
            ms = s.get('ms', 0.01)
            sp = s.get('sp', 1)
            lot = s.get('lot', 1)

            for entry in portfolio[ticker]:
                strategy_name = entry['strategy']
                fn = STRATEGY_MAP.get(strategy_name)
                if not fn:
                    continue

                # Пирамидинг: лимит позиций на (тикер, стратегию)
                params = entry.get('params', {})
                max_pos = int(params.get('max_positions', 1))
                active_same = [p for p in active if p.get('strategy') == strategy_name]
                if len(active_same) >= max_pos:
                    continue

                # Ролл-фильтр (экспирация): не открывать в день скачка цены >5% (ролл контракта)
                if strategy_name == 'oi' and is_roll_day(ticker):
                    continue

                try:
                    signal = fn(bd, ticker, entry.get('params', {}))
                except Exception as e:
                    log.warning("Signal error %s/%s: %s", ticker, strategy_name, e)
                    continue

                if not signal:
                    continue

                # Аудит-пауза: не открывать новые позиции (DD>20% или проблемы)
                if audited_paused:
                    log.info("AUDIT pause active — skip new %s %s signal", ticker, strategy_name)
                    continue

                # Trend filter (SMA50) from params
                params = entry.get('params', {})
                if params.get('trend') and 'close_hist' in bd and len(bd.get('close_hist', [])) >= 50:
                    closes = bd['close_hist'][-50:]
                    sma50 = sum(closes) / 50
                    if signal['direction'] == 'long' and float(bd['prc']) < sma50:
                        continue
                    if signal['direction'] == 'short' and float(bd['prc']) > sma50:
                        continue

                # Realistic slippage: 2-5 tick based on position size — moved after contracts
                
                # Contract sizing: фиксированные contracts из PG ИЛИ risk-based (как бэктест)
                if entry.get('contracts'):
                    contracts = entry['contracts']
                else:
                    risk = entry.get('risk', 0.2)
                    go = s.get('go', 1)
                    # Кап eq для sizing: при eq > капа лоты НЕ растут (slippage убивает edge).
                    # Кап из PG (sizing_eq_cap, оптимум бэктеста 600K для OI) или константа.
                    cap_eq = params.get('sizing_eq_cap', SIZING_EQ_CAP)
                    sizing_eq = min(equity, cap_eq)
                    contracts = max(1, int(sizing_eq * risk / go)) if go > 0 else 1

                # Volume cap: реальный дневной объём × LIQ_FRAC (ёмкость рынка).
                # ВАЖНО: b_vol×0.2 (tick_volume M1) ДУШИТ до 1 лота при малом объёме утром —
                # отключено. Только DAILY_VOL (средний дневной объём) — стабильный лимит.
                if DAILY_VOL.get(ticker):
                    contracts = min(contracts, max(1, int(DAILY_VOL[ticker] * LIQ_FRAC)))
                # Per-ticker лимит контрактов (обновлён из реальных объёмов при старте)
                max_lots = TICKER_LIMITS.get(ticker, MAX_CONTRACTS)
                contracts = min(contracts, max_lots)
                if s.get('go', 0) * contracts > equity:
                    contracts = max(1, int(equity * 0.1 / s.get('go', 1)))
                    contracts = min(contracts, max_lots)

                # Realistic slippage: 1 тик (лимитка по текущей цене, как бэктест LONG+h120)
                # НЕ 2-5 тиков: на NG (ms=0.001, цена ~2.7) 3 тика = 0.11% — убивает edge
                ms_val = ms
                dom_slip = 1
                if BROKER == 'dom':
                    # Исполнение по стакану: сколько тиков нужно для contracts лотов
                    dom_slip = get_dom_broker().entry_slippage(ticker, signal['direction'], contracts, ms_val)
                    slip_total = ms_val * dom_slip
                else:
                    slip_total = ms_val * 1
                entry_price = float(bd['prc']) + (slip_total if signal['direction'] == 'long' else -slip_total)
                entry_price = round(entry_price / ms_val) * ms_val

                # Timeout для OI: max_hold_h (часы) → минуты (M1-бары), как бэктест
                if strategy_name == 'oi':
                    max_hold_h = params.get('max_hold_h', 120)
                    timeout_bars = max_hold_h * 60
                else:
                    timeout_bars = entry.get('timeout_bars', 12)

                pos = {
                    'id': next_id,
                    'ticker': ticker,
                    'strategy': strategy_name,
                    'direction': signal['direction'],
                    'entry_price': entry_price,
                'entry_time': datetime.now(timezone.utc),
                'entry_bar': max_bar_idx,
                'contracts': contracts,
                'pnl': 0,
                'closed': False,
                'trailing_activated': False,
                'trailing_level': None,
                'rem': 1,
                'part_pnl': 0,
                'exit_thr': params.get('exit_thr', 3),
                'max_hold_h': params.get('max_hold_h', 120),
                'base_contracts': contracts,
                'pyra_base_price': entry_price,  # первоначальный вход — порог пирамиды (как бэктест)
                'pyra_max': max(0, int(params.get('pyra_max', 0)) or (int(params.get('max_positions', 1)) - 1)),
                'pyra_pct': float(params.get('pyra_pct', 0.5)),
                'pyra_added': 0,
                'activation': entry.get('trailing_activation', 0.005),
                'trail': entry.get('trailing_trail', 0.003),
                'stop_loss': entry.get('stop_loss', 0.007),
                'timeout_bars': timeout_bars,
                'pct': specs.get(ticker, {}).get('pct', 1.0),
                }
                next_id += 1
                positions.append(pos)
                log.info("New %s %s %s @ %.1f (%d ct, thr=%.1f exit=%.1f slip=%s)", ticker, strategy_name, signal['direction'], entry_price, contracts, params.get('thr', 0), params.get('exit_thr', 0), f"{dom_slip}t" if BROKER == 'dom' else "1t")

    # Save
    state['positions'] = [p for p in positions if not p.get('closed', False)]
    state['equity'] = equity
    state['peak'] = max(peak, equity)
    
    # MTM equity с учётом открытых позиций
    mtm_eq = calc_mtm_equity(equity, state['positions'], bar_data, specs)
    state['mtm_equity'] = mtm_eq
    state['mtm_peak'] = max(state.get('mtm_peak', mtm_eq), mtm_eq)
    
    state['trades'] = trades
    state['next_id'] = next_id
    save_state(state)

    log.info("Tick complete: equity=%.0f, open=%d, trades=%d, mtm_eq=%.0f",
             equity, len(state['positions']), len(trades), mtm_eq)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--strategy', type=str, default=None, help='Strategy name filter (e.g. impulse_return)')
    parser.add_argument('--state-key', type=str, default=None, help='State key suffix for separate instance')
    parser.add_argument('--mode', type=str, default=None, choices=['tick', 'detect'], help='tick=only manage, detect=only signals')
    parser.add_argument('--broker', type=str, default='sim', choices=['sim', 'dom'], help='sim=close+1тик, dom=исполнение по стакану (PG futures.dom)')
    args = parser.parse_args()
    
    if args.broker == 'dom':
        os.environ['MOEX_BROKER'] = 'dom'
        log.info("BrokerDOM: исполнение по стакану (PG futures.dom)")
    
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
    
    if args.state_key:
        import __main__
        __main__.STATE_KEY = args.state_key
        STATE_KEY = args.state_key
    
    load_daily_volumes()  # реальные дневные объёмы AlgoPack → лимиты лотов (компаунд)
    log.info("TICKER_LIMITS после загрузки объёмов: %s", {k: v for k, v in TICKER_LIMITS.items() if k in ('BR','NG','SV','RN','RI','TT')})
    
    run_tick(strategy_filter=args.strategy, mode=args.mode)
