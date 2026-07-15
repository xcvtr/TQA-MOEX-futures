#!/usr/bin/env python3
"""Universal paper trader — runs every tick, reads portfolio from PG.

Loads portfolio → loads latest bar → checks signals → manages positions.
Works with any strategy that has check_signal(bar_data, ticker) -> dict|None.
"""
import os, sys, json, time, logging
from datetime import datetime, timezone
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
    STRATEGY_MAP['stop_hunt'] = sh_check
    STRATEGY_MAP['cvd'] = cvd_check
    STRATEGY_MAP['impulse_return'] = imp_check
    STRATEGY_MAP['dragon'] = dragon_check

# ── Config ────────────────────────────────────────────────────────────────
CH_HOST = os.getenv('MOEX_CH_HOST', '10.0.0.60')
CH_PORT = 8123
CH_DB = 'moex'

PG_HOST = os.getenv('MOEX_PG_HOST', '10.0.0.60')
PG_PORT = int(os.getenv('MOEX_PG_PORT', '5432'))
PG_DB = os.getenv('MOEX_PG_DB', 'moex')
PG_USER = os.getenv('MOEX_PG_USER', 'postgres')
PG_PASS = os.getenv('MOEX_PG_PASSWORD', '')

TRADE_COST = 4  # руб за сделку
TIMEOUT_BARS = 12  # дефолт, берётся из PG если есть
STATE_KEY = ''  # модульный уровень — задаётся в __main__ или run_paper_trader.py

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
               COALESCE(timeout_bars, 12)
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
        portfolio[ticker].append({
            'strategy': strategy,
            'contracts': r[2],  # None = use fixed contract count from ticker_specs
            'weight': float(r[3]) if r[3] else 1.0,
            'trailing_activation': float(r[4]) if r[4] else 0.5,
            'trailing_trail': float(r[5]) if r[5] else 0.3,
            'timeout_bars': int(r[6]) if r[6] else 12,
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
               COALESCE(asset_code, ticker)
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
                 pnl_rub, signal_type, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'closed')
            """, (t['ticker'], t.get('strategy', 'stop_hunt'), t['direction'], t['entry_price'], t.get('exit_price'),
                  t.get('entry_time', datetime.now(timezone.utc)), t.get('exit_time'),
                  t.get('pnl'), t.get('exit_reason', '')))
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
        trade_cost = TRADE_COST * contracts
        
        if p['direction'] == 'long':
            pnl = (prc - entry) / ms * sp * pct * rem - trade_cost
        else:  # short
            pnl = (entry - prc) / ms * sp * pct * rem - trade_cost
        mtm_pnl += pnl
    return capital + mtm_pnl


# ── CH helpers ────────────────────────────────────────────────────────────

def get_latest_bars(ticker, asset, n_bars=50):
    """Get last N 5-min OHLC bars.
    
    Priority:
    1. PG futures.bars_1m (live, autopurge 2mo, для paper trader)
    2. CH moex.mt5_bars (полная история, для backtest)
    3. CH moex.tradestats_fo (AlgoPack real OHLC)
    4. CH moex.prices_5min (ISS snapshots, fallback)
    Returns DataFrame or None.
    """
    now = datetime.now(timezone.utc)
    
    # ── 1. PG (primary для paper trader) ────────────────────────────────────
    try:
        import psycopg2
        conn = psycopg2.connect(host=PG_HOST, port=PG_PORT, dbname=PG_DB, user=PG_USER, password=PG_PASS, connect_timeout=3)
        cur = conn.cursor()
        cur.execute(f"""
            SELECT to_timestamp(floor(extract(epoch from bt) / 300) * 300) as bt5,
                   (array_agg(opn ORDER BY bt))[1] as opn,
                   max(hi) as hi, min(lo) as lo,
                   (array_agg(prc ORDER BY bt DESC))[1] as prc
            FROM futures.bars_1m
            WHERE ticker = %s
            GROUP BY bt5 ORDER BY bt5 DESC LIMIT %s
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
            SELECT toStartOfInterval(bt, INTERVAL 5 MINUTE) as bt5,
                   argMin(opn, bt) as opn,
                   max(hi) as hi, min(lo) as lo,
                   argMax(prc, bt) as prc_close
            FROM moex.mt5_bars WHERE ticker = '{ticker}'
            GROUP BY bt5 ORDER BY bt5 DESC LIMIT {n_bars + 5}
        """)
        if not df.empty:
            df = df.sort_values('bt5').reset_index(drop=True)
            df.rename(columns={'bt5': 'bt', 'prc_close': 'prc'}, inplace=True)
            age = (now - df.iloc[-1]['bt'].replace(tzinfo=timezone.utc)).total_seconds() / 60
            if age < 5:
                ch.close(); return df
        ch.close()
    except Exception as e:
        log.warning("mt5_bars error for %s: %s", ticker, e)
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


# ── Position management ──────────────────────────────────────────────────

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
        if p['entry_bar'] >= bar_idx:
            continue

        # Timeout
        if bar_idx - p['entry_bar'] >= p.get('timeout_bars', 12):
            pnl = (close - p['entry_price']) / ms * sp * p.get('pct', 1.0) * max(0.001, p.get('rem', 1)) - TRADE_COST * p.get('contracts', 1)
            pnl += p.get('part_pnl', 0)
            p['pnl'] = pnl
            p['exit_price'] = close
            p['exit_reason'] = 'timeout'
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
            elif lo <= p['entry_price'] * (1 - p.get('stop_loss', 0.007)):
                exit_price = lo
                p['exit_reason'] = 'stop_loss'

            if exit_price:
                rem = max(0.001, p.get('rem', 1))
                pnl = (exit_price - p['entry_price']) / ms * sp * p.get('pct', 1.0) * rem - TRADE_COST * p.get('contracts', 1)
                pnl += p.get('part_pnl', 0)
                p['pnl'] = pnl
                p['exit_price'] = exit_price
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
            elif hi >= p['entry_price'] * (1 + p.get('stop_loss', 0.007)):
                exit_price = hi
                p['exit_reason'] = 'stop_loss'

            if exit_price:
                rem = max(0.001, p.get('rem', 1))
                pnl = (p['entry_price'] - exit_price) / ms * sp * p.get('pct', 1.0) * rem - TRADE_COST * p.get('contracts', 1)
                pnl += p.get('part_pnl', 0)
                p['pnl'] = pnl
                p['exit_price'] = exit_price
                p['closed'] = True
                p['exit_bar'] = bar_idx
                closed.append(p)

    return closed


# ── Main tick ─────────────────────────────────────────────────────────────

def run_tick(strategy_filter=None):
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
    MARKET_OPEN_IRK = 15  # MOEX открывается в 10:00 MSK = 15:00 IRK
    MARKET_CLOSE_IRK = 0  # 23:45 MSK следующий день = 00:00 IRK следующего дня
    
    # Проверка: рынок открыт? (MOEX: 15:00-23:45 IRK = 10:00-18:45 MSK)
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
        
        bar_data[ticker] = {
            'bt': last['bt'],
            'opn': float(last['opn']),
            'hi': float(last['hi']),
            'lo': float(last['lo']),
            'prc': float(last['prc']),
            'prc_prev': float(second_last['prc']),
            'vol': current_vol,
            'dcvd_z': dcvd_z,
            'close_hist': close_hist,
            'vol_hist': vol_hist,
            'bars_list': [
                {'opn': float(r['opn']), 'hi': float(r['hi']),
                 'lo': float(r['lo']), 'prc': float(r['prc'])}
                for _, r in df.iterrows()
            ],
        }
        # Build hi/lo history for signal check
        hi_hist = [float(v) for v in df['hi'].iloc[-21:-1].values]
        lo_hist = [float(v) for v in df['lo'].iloc[-21:-1].values]
        bar_data[ticker]['hi_hist'] = hi_hist
        bar_data[ticker]['lo_hist'] = lo_hist
        max_bar_idx = max(max_bar_idx, bar_idx)

    state['bar_idx'] = max_bar_idx

    # Manage existing positions
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
    if market_open:
        for ticker in tickers:
            bd = bar_data.get(ticker)
            if not bd:
                continue
            if any(not p.get('closed', False) and p.get('ticker') == ticker for p in positions):
                continue
            s = specs.get(ticker, {})
            ms = s.get('ms', 0.01)
            sp = s.get('sp', 1)
            lot = s.get('lot', 1)

            for entry in portfolio[ticker]:
                strategy_name = entry['strategy']
                fn = STRATEGY_MAP.get(strategy_name)
                if not fn:
                    continue

                try:
                    signal = fn(bd, ticker)
                except Exception as e:
                    log.warning("Signal error %s/%s: %s", ticker, strategy_name, e)
                    continue

                if not signal:
                    continue

                # Entry on next bar's open + 1 tick
                ms_val = ms
                entry_price = float(bd['prc']) + ms_val  # latest close + 1 tick slippage
                entry_price = round(entry_price / ms_val) * ms_val

                # Contract sizing
                contracts = entry.get('contracts') or 1

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
                'activation': entry.get('trailing_activation', 0.005),
                'trail': entry.get('trailing_trail', 0.003),
                'stop_loss': 0.007,
                'timeout_bars': entry.get('timeout_bars', 12),
                'pct': specs.get(ticker, {}).get('pct', 1.0),
                }
                next_id += 1
                positions.append(pos)
                log.info("New %s %s %s @ %.1f", ticker, strategy_name, signal['direction'], entry_price)

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
    args = parser.parse_args()
    
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
    
    if args.state_key:
        STATE_KEY = args.state_key
    
    run_tick(strategy_filter=args.strategy)
