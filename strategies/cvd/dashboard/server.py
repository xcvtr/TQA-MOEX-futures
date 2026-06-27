"""
CVD Divergence Paper Trader Dashboard — FastAPI сервер.

Данные из ClickHouse moex.strategy_paper_trades, moex.strategy_portfolio_state.

Запуск: python -m uvicorn dashboard.server:app --host 0.0.0.0 --port 8101
"""

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

import clickhouse_connect

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(title="CVD Divergence Dashboard", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent
static_dir = BASE_DIR / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

CH_HOST = os.environ.get('MOEX_CH_HOST', '10.0.0.64')

# ---------------------------------------------------------------------------
# Root
# ---------------------------------------------------------------------------
@app.get("/")
async def root():
    index_path = static_dir / "index.html"
    if index_path.exists():
        return HTMLResponse(index_path.read_text())
    return HTMLResponse("<h1>CVD Divergence Dashboard</h1><p>index.html not found</p>")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def get_ch():
    return clickhouse_connect.get_client(host=CH_HOST, database='moex')


# ---------------------------------------------------------------------------
# API — состояние портфеля
# ---------------------------------------------------------------------------
@app.get("/api/state")
def api_state():
    """Текущее состояние портфеля."""
    ch = get_ch()
    try:
        rows = ch.query("""
            SELECT capital, peak_capital, lots
            FROM moex.strategy_portfolio_state
            WHERE strategy = 'cvd_divergence'
            ORDER BY updated_at DESC
            LIMIT 1
        """).result_rows
        if rows:
            capital = float(rows[0][0])
            peak = float(rows[0][1])
            lots = int(rows[0][2])
            dd = (capital - peak) / peak * 100 if peak > 0 else 0
        else:
            capital = 100000.0
            peak = 100000.0
            lots = 1
            dd = 0.0
    finally:
        ch.close()

    return {
        "capital": round(capital, 2),
        "peak": round(peak, 2),
        "dd_pct": round(dd, 2),
        "lots": lots,
        "initial_capital": 100000.0,
        "total_return_pct": round((capital / 100000.0 - 1) * 100, 2),
    }


# ---------------------------------------------------------------------------
# API — открытые позиции
# ---------------------------------------------------------------------------
@app.get("/api/positions")
def api_positions():
    """Открытые позиции."""
    ch = get_ch()
    try:
        rows = ch.query("""
            SELECT id, ticker, direction, entry_price, entry_time
            FROM moex.strategy_paper_trades
            WHERE status = 'open' AND strategy = 'cvd_divergence'
            ORDER BY entry_time
        """).result_rows
    finally:
        ch.close()

    positions = []
    for r in rows:
        positions.append({
            "id": r[0],
            "ticker": r[1],
            "direction": r[2],
            "entry_price": round(float(r[3]), 4),
            "entry_time": r[4].strftime('%Y-%m-%d %H:%M') if hasattr(r[4], 'strftime') else str(r[4]),
        })
    return {"positions": positions, "count": len(positions)}


# ---------------------------------------------------------------------------
# API — закрытые сделки
# ---------------------------------------------------------------------------
@app.get("/api/trades")
def api_trades(limit: int = Query(50, ge=1, le=500)):
    """Последние закрытые сделки."""
    ch = get_ch()
    try:
        rows = ch.query(f"""
            SELECT ticker, direction, entry_price, exit_price, entry_time, exit_time, pnl_rub
            FROM moex.strategy_paper_trades
            WHERE status = 'closed' AND strategy = 'cvd_divergence'
            ORDER BY exit_time DESC
            LIMIT {limit}
        """).result_rows
    finally:
        ch.close()

    trades = []
    for r in rows:
        trades.append({
            "ticker": r[0],
            "direction": r[1],
            "entry_price": round(float(r[2]), 4) if r[2] else None,
            "exit_price": round(float(r[3]), 4) if r[3] else None,
            "entry_time": r[4].strftime('%Y-%m-%d %H:%M') if hasattr(r[4], 'strftime') else str(r[4]),
            "exit_time": r[5].strftime('%Y-%m-%d %H:%M') if r[5] and hasattr(r[5], 'strftime') else str(r[5]) if r[5] else None,
            "pnl_rub": round(float(r[6]), 2) if r[6] else 0,
        })
    return {"trades": trades, "count": len(trades)}


# ---------------------------------------------------------------------------
# API — статистика per-symbol
# ---------------------------------------------------------------------------
@app.get("/api/stats")
def api_stats():
    """Статистика по каждому символу."""
    ch = get_ch()
    try:
        rows = ch.query("""
            SELECT ticker,
                   count() as total,
                   countIf(status = 'closed') as closed,
                   countIf(status = 'open') as open_pos,
                   countIf(pnl_rub > 0) as wins,
                   countIf(pnl_rub < 0) as losses,
                   sum(pnl_rub) as total_pnl,
                   avg(pnl_rub) as avg_pnl
            FROM moex.strategy_paper_trades
            WHERE strategy = 'cvd_divergence'
            GROUP BY ticker
            ORDER BY ticker
        """).result_rows
    finally:
        ch.close()

    stats = []
    for r in rows:
        closed = int(r[2])
        wins = int(r[4])
        wr = round(wins / max(closed, 1) * 100, 1) if closed > 0 else 0
        stats.append({
            "ticker": r[0],
            "total": int(r[1]),
            "closed": closed,
            "open": int(r[3]),
            "wins": wins,
            "losses": int(r[5]),
            "wr_pct": wr,
            "total_pnl": round(float(r[6]), 2) if r[6] else 0,
            "avg_pnl": round(float(r[7]), 2) if r[7] else 0,
        })
    return {"stats": stats}


# ---------------------------------------------------------------------------
# API — equity curve
# ---------------------------------------------------------------------------
@app.get("/api/equity")
def api_equity():
    """Equity curve из portfolio_state."""
    ch = get_ch()
    try:
        rows = ch.query("""
            SELECT capital, peak_capital, updated_at
            FROM moex.strategy_portfolio_state
            WHERE strategy = 'cvd_divergence'
            ORDER BY updated_at ASC
        """).result_rows
    finally:
        ch.close()

    equity = []
    first_cap = None
    for r in rows:
        cap = float(r[0])
        peak = float(r[1])
        if first_cap is None:
            first_cap = cap
        dd = (cap - peak) / peak * 100 if peak > 0 else 0
        ts = r[2]
        equity.append({
            "capital": round(cap, 2),
            "peak": round(peak, 2),
            "dd_pct": round(dd, 2),
            "time": ts.strftime('%Y-%m-%d %H:%M') if hasattr(ts, 'strftime') else str(ts),
        })
    
    # Добавляем стартовую точку 100K, если её нет
    if not equity or equity[0]["capital"] != 100000.0:
        equity.insert(0, {
            "capital": 100000.0,
            "peak": 100000.0,
            "dd_pct": 0.0,
            "time": "2026-06-26 10:00"
        })
    
    return {"equity": equity}


# ---------------------------------------------------------------------------
# API — health check (последние данные в БД, последний запуск крона)
# ---------------------------------------------------------------------------
LOG_FILE = os.path.join(os.path.expanduser("~"), ".hermes", "data", "cvd_paper", "trades.log")


@app.get("/api/health")
def api_health():
    """Когда последний раз были данные в БД и запускался крон."""
    ch = get_ch()
    try:
        # Последняя сделка
        last_trade = ch.query("""
            SELECT max(entry_time) FROM moex.strategy_paper_trades
            WHERE strategy = 'cvd_divergence'
        """).result_rows[0][0]

        # Последнее обновление portfolio_state
        last_portfolio = ch.query("""
            SELECT max(updated_at) FROM moex.strategy_portfolio_state
            WHERE strategy = 'cvd_divergence'
        """).result_rows[0][0]
    finally:
        ch.close()

    # Последний запуск крона из лога
    last_cron = None
    try:
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE) as f:
                for line in f:
                    if 'CVD Divergence Paper Trader — run at' in line:
                        # Парсим дату из строки
                        last_cron = line.strip()
        # Если не нашли в логе трейдера — проверяем cron log
        cron_log = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'logs', 'cvd_paper_trader.log')
        if os.path.exists(cron_log):
            with open(cron_log) as f:
                for line in f:
                    if 'CVD Divergence Paper Trader — run at' in line:
                        last_cron = line.strip()
    except Exception:
        pass

    def fmt(ts):
        if ts is None:
            return None
        return ts.strftime('%Y-%m-%d %H:%M:%S') if hasattr(ts, 'strftime') else str(ts)

    return {
        "last_trade_time": fmt(last_trade),
        "last_portfolio_update": fmt(last_portfolio),
        "last_cron_run": last_cron,
    }
