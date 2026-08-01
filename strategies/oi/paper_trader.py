#!/usr/bin/env python3 -u
"""OI Paper Trader — contrarian: физ продают за день → long на 120 мин.

Сигнал: после 19:00 IRK, day_net_fiz (накопление продаж физ за день) < порога.
Вход: long по рыночной цене + slippage, выход через 120 мин.
Тикеры: BR (thr -7%), NG (thr -5%), SV (thr -5%).

State: PG futures.paper_state_oi, trades: futures.paper_trades_oi.
"""
import sys, os, json, time, logging
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import clickhouse_connect as cc
import psycopg2

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger('oi_pt')

CH_HOST = os.getenv('CH_HOST', '10.0.0.60')
PG_HOST = os.getenv('PG_HOST', '10.0.0.60')
PG_DB = 'moex'
PG_USER = 'postgres'
PG_PASS = os.getenv('PG_PASS', '')

CAPITAL = 200000.0
RISK_PCT = 0.05
HOLD_MIN = 120
SLIP_TICKS = 2

SPECS = {
    'SV': {'ms': 0.01, 'sp': 7.9357, 'go': 9867, 'fee': 4.0, 'thr': -5.0},
    'BR': {'ms': 0.01, 'sp': 7.70611, 'go': 8620, 'fee': 4.0, 'thr': -7.0},
    'NG': {'ms': 0.001, 'sp': 7.70611, 'go': 11974, 'fee': 4.0, 'thr': -5.0},
}

def pg_conn():
    return psycopg2.connect(host=PG_HOST, dbname=PG_DB, user=PG_USER, password=PG_PASS, connect_timeout=5)

def ensure_tables():
    conn = pg_conn(); cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS futures.paper_state_oi (
            equity numeric, peak numeric, positions_json text, updated_at timestamptz default now()
        )""")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS futures.paper_trades_oi (
            id serial primary key, ticker text, direction text, entry_price numeric,
            exit_price numeric, pnl numeric, ts_open timestamptz, ts_close timestamptz,
            reason text, saved boolean default false
        )""")
    conn.commit(); cur.close(); conn.close()

def load_state():
    conn = pg_conn(); cur = conn.cursor()
    cur.execute("SELECT equity, peak, positions_json FROM futures.paper_state_oi ORDER BY updated_at DESC LIMIT 1")
    row = cur.fetchone(); cur.close(); conn.close()
    if row:
        positions = json.loads(row[2]) if row[2] else []
        return float(row[0]), float(row[1]), positions
    return CAPITAL, CAPITAL, []

def save_state(equity, peak, positions):
    conn = pg_conn(); cur = conn.cursor()
    cur.execute("DELETE FROM futures.paper_state_oi")
    cur.execute("INSERT INTO futures.paper_state_oi (equity, peak, positions_json) VALUES (%s,%s,%s)",
                (equity, peak, json.dumps(positions)))
    conn.commit(); cur.close(); conn.close()

def insert_trade(t):
    conn = pg_conn(); cur = conn.cursor()
    cur.execute("""INSERT INTO futures.paper_trades_oi
        (ticker, direction, entry_price, exit_price, pnl, ts_open, ts_close, reason)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
        (t['ticker'], t['dir'], t['entry'], t['exit'], t['pnl'], t['ts_open'], t['ts_close'], t['reason']))
    conn.commit(); cur.close(); conn.close()

def get_price(ticker):
    """Последняя цена M1."""
    ch = cc.get_client(host=CH_HOST, port=8123, database='moex')
    r = ch.query(f"SELECT prc FROM moex.mt5_continuous WHERE ticker='{ticker}' ORDER BY bt DESC LIMIT 1").result_rows
    ch.close()
    return float(r[0][0]) if r else None

def get_day_net(ticker):
    """Текущее накопление net_fiz за день (в % от OI). Возвращает (day_net, last_ts) или None."""
    ch = cc.get_client(host=CH_HOST, port=8123, database='moex')
    now = datetime.now(timezone.utc)
    # OI за сегодня
    rows = ch.query(f"""
        SELECT bt,buy_fiz,sell_fiz,buy_yur,sell_yur FROM moex.futoi
        WHERE ticker='{ticker}' AND bt >= today() ORDER BY bt
    """).result_rows
    ch.close()
    if not rows:
        return None, None
    # Дневной старт (первая запись)
    first = rows[0]
    day_start_net = int(first[1]) - int(first[2])
    # Последняя запись
    last = rows[-1]
    last_net = int(last[1]) - int(last[2])
    total = int(last[1]) + int(last[2]) + int(last[3]) + int(last[4])
    if total <= 0:
        return None, None
    day_net = (last_net - day_start_net) / total * 100
    return day_net, last[0]

def run_tick():
    now = datetime.now(timezone.utc)
    irk_hour = (now.hour + 8) % 24
    
    equity, peak, positions = load_state()
    
    # 1. Закрыть истёкшие (hold 120 мин)
    remaining = []
    closed = []
    for p in positions:
        age = (now - datetime.fromisoformat(p['ts_open'])).total_seconds() / 60
        if age >= HOLD_MIN:
            price = get_price(p['ticker'])
            if price is None:
                remaining.append(p); continue
            ms = SPECS[p['ticker']]['ms']; sp = SPECS[p['ticker']]['sp']; fee = SPECS[p['ticker']]['fee']
            exit_p = price - ms * SLIP_TICKS
            pnl = (exit_p - p['entry']) / ms * sp * p['shares'] - fee * p['shares']
            equity += pnl
            closed.append({'ticker': p['ticker'], 'dir': 'long', 'entry': p['entry'],
                           'exit': exit_p, 'pnl': pnl,
                           'ts_open': p['ts_open'], 'ts_close': now.isoformat(), 'reason': 'timeout'})
            log.info("CLOSE %s pnl=%+.0f", p['ticker'], pnl)
        else:
            remaining.append(p)
    
    # 2. Открыть новые (19:00-21:45 IRK — hold 120 мин укладывается в сессию до 23:45)
    opened = 0
    if 19 <= irk_hour < 22:
        # Проверяем каждый тикер
        active = {p['ticker'] for p in remaining}
        for ticker in SPECS:
            if ticker in active: continue
            day_net, oi_ts = get_day_net(ticker)
            if day_net is None: continue
            if day_net > SPECS[ticker]['thr']: continue
            # Сигнал: физ продают сверх порога
            price = get_price(ticker)
            if price is None: continue
            ms = SPECS[ticker]['ms']; sp = SPECS[ticker]['sp']; go = SPECS[ticker]['go']
            entry = price + ms * SLIP_TICKS
            shares = max(1, int(equity * RISK_PCT / go))
            remaining.append({'ticker': ticker, 'entry': entry, 'shares': shares,
                              'ts_open': now.isoformat()})
            opened += 1
            log.info("OPEN %s @ %.4f (day_net=%+.2f%%)", ticker, entry, day_net)
    
    # MTM + peak
    mtm = equity
    for p in remaining:
        price = get_price(p['ticker'])
        if price:
            ms = SPECS[p['ticker']]['ms']; sp = SPECS[p['ticker']]['sp']
            mtm += (price - p['entry']) / ms * sp * p['shares']
    if mtm > peak: peak = mtm
    
    for t in closed:
        insert_trade(t)
    save_state(equity, peak, remaining)
    
    log.info("OI tick: equity=%.0f peak=%.0f open=%d opened=%d closed=%d", equity, peak, len(remaining), opened, len(closed))

if __name__ == '__main__':
    ensure_tables()
    run_tick()
