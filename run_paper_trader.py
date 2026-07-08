#!/usr/bin/env python3
"""Paper trader runner for MOEX futures — silent-till-event.

Запускается по cron каждые 5 мин. Использует стратегии из PG portfolio.
Выводит только события: открытие/закрытие сделок, просадку >20%.
Тишина = всё ОК, сигналов нет.

Usage:
    python3 run_paper_trader.py                          # обычный run
    python3 run_paper_trader.py --stdout                 # принудительный вывод статуса
"""
import sys, os, json
from datetime import datetime, timezone

# Project root
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import psycopg2

from strategies.common.paper_trader import run_tick

# ── PG config (same as paper_trader.py) ──────────────────────────────────
PG_HOST = os.getenv('MOEX_PG_HOST', '10.0.0.60')
PG_PORT = int(os.getenv('MOEX_PG_PORT', '5432'))
PG_DB = os.getenv('MOEX_PG_DB', 'moex')
PG_USER = os.getenv('MOEX_PG_USER', 'postgres')
PG_PASS = os.getenv('MOEX_PG_PASS', '')

INITIAL_CAPITAL = 200000.0


def pg_conn():
    return psycopg2.connect(host=PG_HOST, port=PG_PORT, dbname=PG_DB,
                            user=PG_USER, password=PG_PASS, connect_timeout=5)


def get_trades_count():
    """Количество закрытых сделок в PG."""
    conn = pg_conn()
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM futures.paper_trades")
    r = cur.fetchone()[0] or 0
    cur.close(); conn.close()
    return r


def get_last_trades(n=5):
    """Последние n закрытых сделок."""
    conn = pg_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT ticker, strategy, direction, pnl_rub, exit_reason, exit_time
        FROM futures.paper_trades
        ORDER BY exit_time DESC NULLS LAST
        LIMIT %s
    """, (n,))
    rows = cur.fetchall()
    cur.close(); conn.close()
    return rows


def get_state():
    """Текущее состояние бумажного трейдера."""
    conn = pg_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT capital, equity, peak, updated_at
        FROM futures.paper_state
        ORDER BY updated_at DESC LIMIT 1
    """)
    r = cur.fetchone()
    cur.close(); conn.close()
    if r:
        return {'capital': float(r[0]), 'equity': float(r[1]),
                'peak': float(r[2]), 'updated_at': r[3]}
    return {'capital': INITIAL_CAPITAL, 'equity': INITIAL_CAPITAL,
            'peak': INITIAL_CAPITAL, 'updated_at': None}


def get_position_count():
    """Количество открытых позиций из JSON-поля."""
    conn = pg_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT positions_json
        FROM futures.paper_state
        ORDER BY updated_at DESC LIMIT 1
    """)
    r = cur.fetchone()
    cur.close(); conn.close()
    if r and r[0]:
        try:
            return len(json.loads(r[0]))
        except (json.JSONDecodeError, TypeError):
            return 0
    return 0


def main():
    force_stdout = '--stdout' in sys.argv

    # ── Before ────────────────────────────────────────────────────────────
    old_trades = get_trades_count()
    old_state = get_state()
    old_positions = get_position_count()

    # ── Run tick ──────────────────────────────────────────────────────────
    try:
        run_tick()
    except Exception as e:
        import traceback
        print(f"❌ PaperTrader ошибка: {e}")
        traceback.print_exc()
        sys.exit(1)

    # ── After ─────────────────────────────────────────────────────────────
    new_trades = get_trades_count()
    new_state = get_state()
    new_positions = get_position_count()

    lines = []

    # Новые закрытые сделки
    if new_trades > old_trades:
        last_trades = get_last_trades(new_trades - old_trades)
        for t in reversed(last_trades):
            ticker, strategy, direction, pnl, reason, ts = t
            if pnl is None:
                continue
            sign = '✅' if pnl > 0 else '❌'
            ts_str = str(ts)[:19] if ts else ''
            lines.append(f"{sign} {ticker} {direction} {strategy} pnl={pnl:+.0f}₽ ({reason}) [{ts_str}]")

    # Новые открытые позиции
    if new_positions > old_positions:
        lines.append(f"📌 Открыто позиций: {new_positions}")

    # Закрылись позиции
    if new_positions < old_positions and new_trades == old_trades:
        lines.append(f"🔒 Позиции закрыты: {old_positions} → {new_positions}")

    # Equity & DD
    eq = new_state.get('equity', INITIAL_CAPITAL)
    pk = new_state.get('peak', INITIAL_CAPITAL)
    dd = (pk - eq) / pk * 100 if pk > 0 else 0
    cap = new_state.get('capital', INITIAL_CAPITAL)
    total_pnl = eq - INITIAL_CAPITAL
    ret = total_pnl / INITIAL_CAPITAL * 100

    # Просадка >20%
    if dd >= 20:
        lines.append(f"⚠️ Просадка {dd:.1f}% — капитал {eq:>.0f}₽ из {pk:>.0f}₽ пик")

    # Если флаг --stdout — показать статус в любом случае
    if force_stdout:
        lines.append(f"📊 Eq={eq:>.0f}₽ (+{ret:.1f}%) DD={dd:.1f}% | Открыто={new_positions} | Сделок={new_trades}")

    # Вывод
    if lines:
        print("\n".join(lines))


if __name__ == '__main__':
    main()
