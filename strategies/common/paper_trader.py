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
    STRATEGY_MAP['stop_hunt'] = sh_check
    STRATEGY_MAP['cvd'] = cvd_check

# ── Config ────────────────────────────────────────────────────────────────
CH_HOST = os.getenv('MOEX_CH_HOST', '10.0.0.64')
CH_PORT = 8123
CH_DB = 'moex'

PG_HOST = os.getenv('MOEX_PG_HOST', '10.0.0.64')
PG_PORT = int(os.getenv('MOEX_PG_PORT', '5432'))
PG_DB = os.getenv('MOEX_PG_DB', 'moex')
PG_USER = os.getenv('MOEX_PG_USER', 'postgres')
PG_PASS = os.getenv('MOEX_PG_PASSWORD', '')

TRADE_COST = 4  # руб за сделку
TIMEOUT_BARS = 12  # дефолт, берётся из PG если есть

# PnL formula: (exit-entry)/ms*sp*lot*contracts - TC*contracts
# LOT must always be in the formula. Spec in ticker_specs corrected.
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
    try:
        conn = pg_conn()
        cur = conn.cursor()
        cur.execute("SELECT capital, equity, peak, positions_json, bar_idx, next_id FROM futures.paper_state ORDER BY updated_at DESC LIMIT 1")
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
    try:
        conn = pg_conn()
        cur = conn.cursor()
        max_pos = max((p['id'] for p in state.get('positions', [])), default=0)
        for t in state.get('trades', []):
            if t.get('saved', False):
                continue
            cur.execute("""
                INSERT INTO futures.paper_trades
                (ticker, strategy, direction, entry_price, exit_price, entry_time, exit_time,
                 pnl_rub, signal_type, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'closed')
            """, (t['ticker'], t.get('strategy', 'stop_hunt'), t['direction'], t['entry_price'], t.get('exit_price'),
                  t.get('entry_time', datetime.now(timezone.utc)), t.get('exit_time'),
                  t.get('pnl'), t.get('exit_reason', '')))
            t['saved'] = True
        conn.commit()
        # Delete old state, insert new
        cur.execute("DELETE FROM futures.paper_state")
        cur.execute("""
            INSERT INTO futures.paper_state (capital, equity, peak, positions_json, bar_idx, next_id, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, NOW())
        """, (round(state['capital'], 2), round(state['equity'], 2), round(state.get('peak', state['equity']), 2),
              json.dumps(state['positions']), state['bar_idx'], state.get('next_id', 1)))
        conn.commit()
        cur.close(); conn.close()
    except Exception as e:
        log.warning("Save state failed: %s", e)


# ── CH helpers ────────────────────────────────────────────────────────────

def get_latest_bars(ticker, asset, n_bars=50):
    """Get last N 5-min bars for a ticker from CH."""
    ch = cc.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)
    try:
        df = ch.query_df(f"""
            SELECT toStartOfInterval(SYSTIME, INTERVAL 5 MINUTE) as bt,
                   argMax(pr_open, SYSTIME) as opn,
                   argMax(pr_high, SYSTIME) as hi,
                   argMax(pr_low, SYSTIME) as lo,
                   argMax(pr_close, SYSTIME) as prc,
                   sum(vol_b) as vb, sum(vol_s) as vs
            FROM moex.tradestats_fo
            WHERE asset_code = '{asset}'
              AND SYSTIME >= now() - INTERVAL {n_bars * 5 + 5} MINUTE
            GROUP BY bt ORDER BY bt
        """)
        ch.close()
        return df
    except Exception as e:
        log.error("CH error for %s: %s", ticker, e)
        ch.close()
        return None


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
            pnl = (close - p['entry_price']) / ms * sp * lot * p.get('pct', 1.0) * max(0.001, p.get('rem', 1)) - TRADE_COST * p.get('contracts', 1)
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
                pnl = (exit_price - p['entry_price']) / ms * sp * lot * p.get('pct', 1.0) * rem - TRADE_COST * p.get('contracts', 1)
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
                pnl = (p['entry_price'] - exit_price) / ms * sp * lot * p.get('pct', 1.0) * rem - TRADE_COST * p.get('contracts', 1)
                pnl += p.get('part_pnl', 0)
                p['pnl'] = pnl
                p['exit_price'] = exit_price
                p['closed'] = True
                p['exit_bar'] = bar_idx
                closed.append(p)

    return closed


# ── Main tick ─────────────────────────────────────────────────────────────

def run_tick():
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

    tickers = list(portfolio.keys())
    specs = load_specs(tickers)
    if not specs:
        log.warning("No specs loaded")
        return

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
        bar_data[ticker] = {
            'bt': last['bt'],
            'opn': float(last['opn']),
            'hi': float(last['hi']),
            'lo': float(last['lo']),
            'prc': float(last['prc']),
            'prc_prev': float(second_last['prc']),
            'vol': 100,
            'dcvd_z': 0,
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

    # Check for new signals
    for ticker in tickers:
        bd = bar_data.get(ticker)
        if not bd:
            continue
        # Already have open position for this ticker?
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
            entry_price = float(bd.get('prc_prev', bd['prc'])) + ms_val
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
    state['trades'] = trades
    state['next_id'] = next_id
    save_state(state)

    log.info("Tick complete: equity=%.0f, open=%d, total_trades=%d",
             equity, len(state['positions']), len(trades))


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
    run_tick()
