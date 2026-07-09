#!/usr/bin/env python3
"""Paper trader runner for MOEX futures — silent-till-event.

Запускается по cron каждые 5 мин. Использует strategies/common/paper_trader.py.
Поддерживает --strategy и --state-key для раздельных инстансов.

Usage:
    python3 run_paper_trader.py [--strategy stop_hunt] [--state-key stop_hunt]
    python3 run_paper_trader.py --stdout
"""
import sys, os, json, argparse
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import psycopg2

# ── PG config ──────────────────────────────────────────────────────────────
PG_HOST = os.getenv('MOEX_PG_HOST', '10.0.0.60')
PG_PORT = int(os.getenv('MOEX_PG_PORT', '5432'))
PG_DB = os.getenv('MOEX_PG_DB', 'moex')
PG_USER = os.getenv('MOEX_PG_USER', 'postgres')
PG_PASS = os.getenv('MOEX_PG_PASS', '')

INITIAL_CAPITAL = 200000.0


def pg_conn():
    return psycopg2.connect(host=PG_HOST, port=PG_PORT, dbname=PG_DB,
                            user=PG_USER, password=PG_PASS, connect_timeout=5)


def get_trades_count(state_key):
    """Количество закрытых сделок в PG для данного state-key."""
    tbl = 'futures.paper_trades' + ('' if not state_key else '_' + state_key)
    conn = pg_conn()
    cur = conn.cursor()
    # Проверить что таблица существует
    cur.execute(f"""
        SELECT count(*) FROM information_schema.tables
        WHERE table_schema='futures' AND table_name='{tbl.split('.')[1]}'
    """)
    exists = cur.fetchone()[0]
    if not exists:
        cur.close(); conn.close()
        return 0
    cur.execute(f"SELECT count(*) FROM {tbl}")
    r = cur.fetchone()
    cur.close(); conn.close()
    return r[0] or 0


def get_last_trades(state_key, n=5):
    """Последние n закрытых сделок."""
    tbl = 'futures.paper_trades' + ('' if not state_key else '_' + state_key)
    conn = pg_conn()
    cur = conn.cursor()
    cur.execute(f"""
        SELECT ticker, strategy, direction, pnl_rub, exit_reason, exit_time
        FROM {tbl}
        ORDER BY exit_time DESC NULLS LAST
        LIMIT %s
    """, (n,))
    rows = cur.fetchall()
    cur.close(); conn.close()
    return rows


def get_state(state_key):
    """Текущее состояние бумажного трейдера."""
    tbl = 'futures.paper_state' + ('' if not state_key else '_' + state_key)
    conn = pg_conn()
    cur = conn.cursor()
    cur.execute(f"""
        SELECT capital, equity, peak, mtm_equity, mtm_peak, updated_at
        FROM {tbl}
        ORDER BY updated_at DESC LIMIT 1
    """)
    r = cur.fetchone()
    cur.close(); conn.close()
    if r:
        return {'capital': float(r[0]), 'equity': float(r[1]),
                'peak': float(r[2]), 'mtm_equity': float(r[3]) if r[3] else float(r[1]),
                'mtm_peak': float(r[4]) if r[4] else float(r[2]),
                'updated_at': r[5]}
    return {'capital': INITIAL_CAPITAL, 'equity': INITIAL_CAPITAL,
            'peak': INITIAL_CAPITAL, 'mtm_equity': INITIAL_CAPITAL,
            'mtm_peak': INITIAL_CAPITAL, 'updated_at': None}


def get_position_count(state_key):
    """Количество открытых позиций из JSON-поля."""
    tbl = 'futures.paper_state' + ('' if not state_key else '_' + state_key)
    conn = pg_conn()
    cur = conn.cursor()
    cur.execute(f"""
        SELECT positions_json FROM {tbl}
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
    parser = argparse.ArgumentParser()
    parser.add_argument('--strategy', type=str, default=None,
                        help='Strategy name filter (e.g. stop_hunt)')
    parser.add_argument('--state-key', type=str, default=None,
                        help='State key suffix for separate instance (e.g. stop_hunt)')
    parser.add_argument('--stdout', action='store_true',
                        help='Принудительный вывод статуса')
    args = parser.parse_args()

    state_key = args.state_key

    # Build CLI args for paper_trader.py
    pt_args = []
    if args.strategy:
        pt_args.extend(['--strategy', args.strategy])
    if args.state_key:
        pt_args.extend(['--state-key', args.state_key])

    # ── Before ──────────────────────────────────────────────────────────
    old_trades = get_trades_count(state_key)
    old_state = get_state(state_key)
    old_positions = get_position_count(state_key)

    # ── Run tick ────────────────────────────────────────────────────────
    import subprocess
    cmd = [sys.executable, 'strategies/common/paper_trader.py'] + pt_args
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        print(f"❌ PaperTrader ошибка (exit={result.returncode}): {result.stderr.strip() or result.stdout.strip()}")
        sys.exit(1)

    # ── After ───────────────────────────────────────────────────────────
    new_trades = get_trades_count(state_key)
    new_state = get_state(state_key)
    new_positions = get_position_count(state_key)

    lines = []

    # Новые закрытые сделки
    if new_trades > old_trades:
        last_trades = get_last_trades(state_key, new_trades - old_trades)
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
    mtm_eq = new_state.get('mtm_equity', eq)
    mtm_pk = new_state.get('mtm_peak', pk)
    dd = (pk - eq) / pk * 100 if pk > 0 else 0
    mtm_dd = (mtm_pk - mtm_eq) / mtm_pk * 100 if mtm_pk > 0 else 0
    total_pnl = eq - INITIAL_CAPITAL
    ret = total_pnl / INITIAL_CAPITAL * 100

    # Просадка >20% (cash или MTM)
    if dd >= 20:
        lines.append(f"⚠️ Просадка {dd:.1f}% — капитал {eq:>.0f}₽ из {pk:>.0f}₽ пик")
    if mtm_dd >= 20:
        lines.append(f"⚠️ MTM просадка {mtm_dd:.1f}% — MTM equity {mtm_eq:>.0f}₽ из {mtm_pk:>.0f}₽ пик")

    # Если флаг --stdout — показать статус
    if args.stdout:
        mtm_info = f" MTM DD={mtm_dd:.1f}%" if mtm_dd != dd else ""
        lines.append(f"📊 Eq={eq:>.0f}₽ (+{ret:.1f}%) DD={dd:.1f}%{mtm_info} | Открыто={new_positions} | Сделок={new_trades}")

    if lines:
        print("\n".join(lines))


if __name__ == '__main__':
    main()
