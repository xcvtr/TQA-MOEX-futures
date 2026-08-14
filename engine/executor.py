#!/usr/bin/env python3 -u
"""Executor TQA-MOEX-futures — исполняет сигналы из PG futures.signals.

Читает signals (status='new', valid_until > NOW()) → sizing → открывает позицию
в futures.paper_state → помечает signal status='processed'.

Позиции открываются ТОЛЬКО через executor (папер-детектор больше не открывает).
Запуск: cron каждые 5 мин в торговые часы.
"""
import os, sys, json, logging
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

import clickhouse_connect as cc
import psycopg2

CH_HOST = os.getenv('MOEX_CH_HOST', '10.0.0.60')
PG = dict(host=os.getenv('MOEX_PG_HOST', '10.0.0.60'), port=5432, dbname='moex',
          user='postgres', password=os.getenv('MOEX_PG_PASSWORD', ''), connect_timeout=5)

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s', datefmt='%H:%M:%S')
log = logging.getLogger('executor')

SIZING_EQ_CAP = 2_000_000
LIQ_FRAC = 0.05
TICKER_LIMITS = {}  # заполняется из PG ticker_specs

# ── Спецификации (ms, sp, go, fee) из PG — не текстовые конфиги ──
def load_specs(tickers):
    conn = pg_conn()
    cur = conn.cursor()
    specs = {}
    for tk in tickers:
        cur.execute("""
            SELECT COALESCE(min_step,0.01), COALESCE(step_price,1.0),
                   COALESCE(go,1), COALESCE(fee_entry,4.0), COALESCE(daily_vol,0)
            FROM futures.ticker_specs WHERE ticker=%s
        """, (tk,))
        r = cur.fetchone()
        if r:
            specs[tk] = {'ms': float(r[0]), 'sp': float(r[1]), 'go': float(r[2]),
                         'fee': float(r[3]), 'daily_vol': float(r[4])}
    cur.close(); conn.close()
    return specs

def pg_conn():
    return psycopg2.connect(**PG)

def load_state():
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
    return {'capital': 200000.0, 'equity': 200000.0, 'peak': 200000.0,
            'positions': [], 'bar_idx': 0, 'next_id': 1}

def save_state(state):
    conn = pg_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM futures.paper_state")
    cur.execute("""
        INSERT INTO futures.paper_state (capital, equity, peak, mtm_equity, mtm_peak, positions_json, bar_idx, next_id, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
    """, (state['capital'], state['equity'], state['peak'], state.get('mtm_equity', state['equity']),
          state.get('mtm_peak', state['peak']), json.dumps(state['positions'], ensure_ascii=False),
          state['bar_idx'], state['next_id']))
    conn.commit()
    cur.close(); conn.close()

def get_last_price(ticker):
    """Последняя цена из PG bars_1m (live-источник). CH не используется."""
    try:
        conn = pg_conn()
        cur = conn.cursor()
        cur.execute("SELECT prc FROM futures.bars_1m WHERE ticker=%s ORDER BY bt DESC LIMIT 1", (ticker,))
        r = cur.fetchone()
        cur.close(); conn.close()
        return float(r[0]) if r else None
    except Exception:
        return None

def mark_signal(conn, sid, status, error=None):
    cur = conn.cursor()
    cur.execute("UPDATE futures.signals SET status=%s, processed_at=NOW(), error=%s WHERE id=%s",
                (status, error, sid))
    conn.commit()
    cur.close()

def main():
    conn = pg_conn()
    cur = conn.cursor()
    # Свежие необработанные сигналы
    cur.execute("""
        SELECT id, strategy, ticker, direction, entry_price, params
        FROM futures.signals
        WHERE status='new' AND valid_until > NOW()
        ORDER BY signal_ts
        LIMIT 20
    """)
    sigs = cur.fetchall()
    cur.close()
    if not sigs:
        conn.close()
        return

    state = load_state()
    specs = load_specs({s[2] for s in sigs})
    equity = state['equity']

    for sid, strategy, ticker, direction, entry_price, params in sigs:
        params = params if isinstance(params, dict) else {}
        s = specs.get(ticker)
        if not s:
            mark_signal(conn, sid, 'rejected', f'нет спецификации {ticker}')
            continue
        # Активная позиция на тикер? max_positions из params или 1
        max_pos = int(params.get('max_positions', 1))
        active = [p for p in state['positions'] if not p.get('closed') and p.get('ticker') == ticker]
        if len(active) >= max_pos:
            mark_signal(conn, sid, 'rejected', f'уже есть позиция {ticker}')
            continue
        # Sizing: risk × min(eq, cap) / GO
        risk = float(params.get('risk', 0.1))
        go = s['go']
        sizing_eq = min(equity, SIZING_EQ_CAP)
        contracts = max(1, int(sizing_eq * risk / go)) if go > 0 else 1
        # Volume cap
        if s['daily_vol'] > 0:
            contracts = min(contracts, max(1, int(s['daily_vol'] * LIQ_FRAC)))
        # Текущая цена
        px = get_last_price(ticker)
        if not px:
            mark_signal(conn, sid, 'rejected', f'нет цены {ticker}')
            continue
        # Создание позиции (как папер: entry по close + 1 тик slippage)
        ms = s['ms']
        entry_px = round((px + ms if direction == 'long' else px - ms) / ms) * ms
        hold_h = float(params.get('max_hold_h', 72))
        pos = {
            'id': state['next_id'], 'ticker': ticker, 'strategy': strategy,
            'direction': direction, 'entry_price': entry_px,
            'entry_time': datetime.now(timezone.utc).isoformat(),
            'entry_bar': state['bar_idx'], 'contracts': contracts, 'pnl': 0,
            'closed': False, 'trailing_activated': False, 'trailing_level': None,
            'rem': 1, 'part_pnl': 0, 'exit_thr': float(params.get('exit_thr', 1.5)),
            'max_hold_h': hold_h, 'base_contracts': contracts,
            'pyra_base_price': entry_px, 'pyra_max': int(params.get('pyra_max', 2)),
            'pyra_pct': float(params.get('pyra_pct', 0.3)), 'pyra_added': 0,
            'activation': float(params.get('trailing_activation', 0.005)),
            'trail': float(params.get('trailing_trail', 0.003)),
            'stop_loss': float(params.get('stop_loss_pct', 2.5)) / 100.0,
            'timeout_bars': int(hold_h * 60), 'pct': 1.0,
            'force_close': False,
        }
        state['positions'].append(pos)
        state['next_id'] += 1
        mark_signal(conn, sid, 'processed')
        log.info('ОТКРЫТА %s %s %s x%d @ %.4f (сигнал id=%s)', ticker, strategy, direction,
                 contracts, entry_px, sid)

    save_state(state)
    conn.close()
    log.info('Executor: обработано %d сигналов', len(sigs))

if __name__ == '__main__':
    main()
