#!/usr/bin/env python3
"""
CVD divergence paper trader — live on M5 MOEX futures via AlgoPack API.

Логика (как в честном бэктесте v4):
  1. Данные через AlgoPack REST API (fo/tradestats), с SQLite-кешем
  2. Ресемпл 1м → 5м через lib_cvd_divergence.resample_to_5m()
  3. Сигналы через lib_cvd_divergence.detect_signals()
  4. Вход: лимитный ордер с touch-check (проверка high/low сигнального бара)
  5. Выход: по close следующего 5м бара (рыночный)
  6. Slippage: 0.5 тика на вход + 1.0 тик на выход = 1.5 тика round-trip
  7. Адаптивный сдвиг лимитки: 30% от ATR(14), мин 5 тиков, макс 20 тиков

Запуск: каждые 5 мин в будни 09:00-23:50 IRKT (MSK+5)
"""

import os, sys, json, time, requests, sqlite3, concurrent.futures
from datetime import datetime, timedelta, date
from pathlib import Path
import pandas as pd
import numpy as np
import clickhouse_connect

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib_cvd_divergence import (
    TICK, TICK_COST, GO, SYMBOLS, N_SYMS,
    LK, HOLD_BARS, Q, INITIAL_CAPITAL,
    SLIPPAGE_IN_TICKS, SLIPPAGE_OUT_TICKS, ROUND_TRIP_TICKS,
    MIN_SLIPPAGE_TICKS, MAX_SLIPPAGE_TICKS, FIXED_SLIPPAGE_TICKS,
    resample_to_5m, calc_thresholds, detect_signals,
    calc_entry_price, check_touch, calc_pnl_rub,
    calc_slippage_ticks, simulate_trade,
)

# ── Пути ──────────────────────────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR) if os.path.basename(SCRIPT_DIR) == 'scripts' else SCRIPT_DIR
DATA_DIR = os.path.join(os.path.expanduser("~"), ".hermes", "data", "cvd_paper")
os.makedirs(DATA_DIR, exist_ok=True)
LOG_FILE = os.path.join(DATA_DIR, "trades.log")

# ── Параметры ────────────────────────────────────────────────────────────────
TRAIN_DAYS = 120

# Для восстановления из лога (когда ATR неизвестен) используем 10 тиков
AGGRESSIVE_TICKS = 3  # запасной min сдвиг (используется когда нет ATR)

# ── AlgoPack API ────────────────────────────────────────────────────────────
ALGOPACK_TOKEN = os.environ.get('ALGOPACK_APIKEY', '')
if not ALGOPACK_TOKEN:
    env_path = os.path.join(PROJECT_DIR, '.env')
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                if line.startswith('ALGOPACK_APIKEY='):
                    ALGOPACK_TOKEN = line.strip().split('=', 1)[1]
                    break

ALGOPACK_URL = "https://apim.moex.com/iss/datashop/algopack/fo/tradestats.json"
ALGOPACK_HEADERS = {"Authorization": f"Bearer {ALGOPACK_TOKEN}"}

# ── ClickHouse ──────────────────────────────────────────────────────────────
CH_HOST = os.environ.get('MOEX_CH_HOST', '10.0.0.64')
CH = None  # lazy init

# ── SQLite cache ────────────────────────────────────────────────────────────
CACHE_DB = os.path.join(DATA_DIR, "algopack_cache.db")
MAX_CATCHUP_DAYS = 5
INITIAL_DAYS = 185

# Конфиг YURz-фильтра per ticker (OI из prices_5m_oi)
OI_FILTER_CONFIG = {
    'NG': {'tf_min': 240, 'yur_z': 2.0},   # 4h
    'BR': {'tf_min': 15,  'yur_z': 2.0},   # 15m
    'Si': {'tf_min': 60,  'yur_z': 1.0},   # 1h
}
TICKER_TO_OI = {'NG': 'NG', 'BR': 'BR', 'Si': 'Si'}


def init_cache_db():
    """Создать таблицы кеша если их нет."""
    conn = sqlite3.connect(CACHE_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bars (
            symbol TEXT,
            time TEXT,
            open REAL,
            close REAL,
            vol_b INTEGER,
            vol_s INTEGER,
            PRIMARY KEY (symbol, time)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cache_meta (
            symbol TEXT PRIMARY KEY,
            last_date TEXT,
            bar_count INTEGER
        )
    """)
    conn.commit()
    conn.close()


def get_cached_bars(symbol):
    """Загрузить все бары символа из кеша."""
    conn = sqlite3.connect(CACHE_DB)
    df = pd.read_sql_query(
        "SELECT time, open, close, vol_b, vol_s FROM bars WHERE symbol = ? ORDER BY time",
        conn, params=[symbol]
    )
    conn.close()
    if not df.empty:
        df['time'] = pd.to_datetime(df['time'])
    return df


def save_bars_to_cache(symbol, df_new):
    """Сохранить/обновить бары символа в кеше (UPSERT)."""
    if df_new.empty:
        return
    conn = sqlite3.connect(CACHE_DB)
    c = conn.cursor()

    rows = []
    for _, row in df_new.iterrows():
        ts = row['time']
        if isinstance(ts, pd.Timestamp):
            ts = ts.strftime('%Y-%m-%d %H:%M:%S')
        rows.append((
            symbol, ts,
            float(row['open']) if pd.notna(row.get('open')) else None,
            float(row['close']) if pd.notna(row.get('close')) else None,
            int(row.get('vol_b', 0) or 0),
            int(row.get('vol_s', 0) or 0),
        ))

    c.executemany("""
        INSERT OR REPLACE INTO bars (symbol, time, open, close, vol_b, vol_s)
        VALUES (?, ?, ?, ?, ?, ?)
    """, rows)

    last_time = df_new['time'].max()
    if isinstance(last_time, pd.Timestamp):
        last_date = last_time.strftime('%Y-%m-%d')
    else:
        last_date = str(last_time)[:10]
    cnt = c.execute("SELECT count() FROM bars WHERE symbol = ?", (symbol,)).fetchone()[0]
    c.execute("""
        INSERT OR REPLACE INTO cache_meta (symbol, last_date, bar_count)
        VALUES (?, ?, ?)
    """, (symbol, last_date, cnt))

    conn.commit()
    conn.close()


def get_cache_last_date(symbol):
    """Получить последнюю дату в кеше для символа."""
    conn = sqlite3.connect(CACHE_DB)
    row = conn.execute(
        "SELECT last_date FROM cache_meta WHERE symbol = ?", (symbol,)
    ).fetchone()
    conn.close()
    if row and row[0]:
        return datetime.strptime(row[0], '%Y-%m-%d').date()
    return None


# ── Логирование ────────────────────────────────────────────────────────────
def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")
    print(line, flush=True)


def get_ch():
    global CH
    if CH is None:
        CH = clickhouse_connect.get_client(host=CH_HOST, database='moex')
    return CH


def fetch_algopack_day(ds):
    """Загрузить все страницы за один день. Возвращает (rows, cols)."""
    all_rows = []
    cols = None
    start = 0
    max_retries = 3
    r = None
    while True:
        params = {"date": ds, "limit": 1000, "start": start}
        for attempt in range(max_retries):
            try:
                r = requests.get(ALGOPACK_URL, params=params, headers=ALGOPACK_HEADERS, timeout=60)
                break
            except Exception as e:
                if attempt == max_retries - 1:
                    log(f"API error for {ds} start={start} after {max_retries} attempts: {e}")
                    return all_rows, cols
                time.sleep(2)
        if r is None or r.status_code != 200:
            log(f"API status {r.status_code} for {ds} start={start}")
            break
        j = r.json()
        data = j.get("data", {}).get("data", [])
        if not data:
            break
        if cols is None:
            cols = j.get("data", {}).get("columns", [])
        all_rows.extend(data)
        if len(data) < 1000:
            break
        start += 1000
    return all_rows, cols


def fetch_algopack_day_for_symbol(ds, symbol):
    """Загрузить один день и отфильтровать по символу. Возвращает DataFrame."""
    raw, cols = fetch_algopack_day(ds)
    if not raw or cols is None:
        return pd.DataFrame()

    records = []
    for row in raw:
        d = dict(zip(cols, row))
        if d.get('asset_code') != symbol:
            continue
        records.append({
            'time': f"{d['tradedate']} {d['tradetime']}",
            'open': float(d['pr_open']) if d.get('pr_open') is not None else None,
            'close': float(d['pr_close']) if d.get('pr_close') is not None else None,
            'vol_b': int(d['vol_b']) if d.get('vol_b') is not None else 0,
            'vol_s': int(d['vol_s']) if d.get('vol_s') is not None else 0,
        })

    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records)
    df['time'] = pd.to_datetime(df['time'])
    df.sort_values('time', inplace=True)
    return df


def ensure_cache_for_symbol(symbol, days_back=183, max_workers=6):
    """Убедиться что кеш для символа загружен с параллельными запросами."""
    last_date = get_cache_last_date(symbol)
    today = date.today()

    if last_date is not None:
        needed = (today - last_date).days
        if needed < 0:
            days_to_fetch = INITIAL_DAYS
            fetch_dates = [(today - timedelta(days=i)).isoformat() for i in range(days_to_fetch, 0, -1)]
        elif needed == 0:
            conn = sqlite3.connect(CACHE_DB)
            last_time = conn.execute(
                "SELECT max(time) FROM bars WHERE symbol = ?", (symbol,)
            ).fetchone()[0]
            cnt = conn.execute("SELECT count() FROM bars WHERE symbol = ?", (symbol,)).fetchone()[0]
            conn.close()

            if last_time:
                last_dt = datetime.strptime(last_time, '%Y-%m-%d %H:%M:%S')
                minutes_old = (datetime.now() - last_dt).total_seconds() / 60
                if minutes_old < 15 and cnt >= days_back * 100:
                    return get_cached_bars(symbol)
                fetch_dates = [today.isoformat()]
            else:
                days_to_fetch = INITIAL_DAYS
                fetch_dates = [(today - timedelta(days=i)).isoformat() for i in range(days_to_fetch, 0, -1)]
        else:
            days_to_fetch = min(needed + 1, MAX_CATCHUP_DAYS)
            fetch_dates = [(last_date + timedelta(days=i)).isoformat() for i in range(1, days_to_fetch + 1)]
    else:
        days_to_fetch = INITIAL_DAYS
        fetch_dates = [(today - timedelta(days=i)).isoformat() for i in range(days_to_fetch, 0, -1)]

    log(f"{symbol}: loading {len(fetch_dates)} days (cache last: {last_date})...")

    total_bars = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        fut_to_ds = {executor.submit(fetch_algopack_day_for_symbol, ds, symbol): ds for ds in fetch_dates}
        for fut in concurrent.futures.as_completed(fut_to_ds):
            ds = fut_to_ds[fut]
            try:
                df = fut.result()
                if not df.empty:
                    save_bars_to_cache(symbol, df)
                    total_bars += len(df)
                    log(f"{symbol}: {ds} — {len(df)} bars (cached)")
            except Exception as e:
                log(f"{symbol}: {ds} error: {e}")

    if total_bars == 0:
        cached = get_cached_bars(symbol)
        if not cached.empty:
            log(f"{symbol}: using cached ({len(cached)} bars)")
            return cached
        return pd.DataFrame()

    result = get_cached_bars(symbol)
    log(f"{symbol}: cache ready — {len(result)} total bars")
    return result


def get_latest_thresholds(symbol):
    """Walk-forward: train на последних 180 днях, считаем пороги."""
    df_1m = ensure_cache_for_symbol(symbol, days_back=TRAIN_DAYS + 5)
    if df_1m.empty:
        log(f"{symbol}: no data for threshold calculation")
        return None, None

    df_5m = resample_to_5m(df_1m, deduplicate=True)
    if df_5m.empty or len(df_5m) < 100:
        log(f"{symbol}: too few 5m bars ({len(df_5m)}) for thresholds")
        return None, None

    unique_dates = sorted(df_5m['date'].unique())
    if len(unique_dates) < TRAIN_DAYS:
        log(f"{symbol}: only {len(unique_dates)} days, need {TRAIN_DAYS} for train")
        return None, None

    train_dates = set(unique_dates[-TRAIN_DAYS:])
    train_df = df_5m[df_5m['date'].isin(train_dates)].copy()
    if len(train_df) < LK + 10:
        log(f"{symbol}: train too small ({len(train_df)} bars)")
        return None, None

    p_thr, c_thr = calc_thresholds(train_df)
    if p_thr is None:
        log(f"{symbol}: could not calculate thresholds")
        return None, None

    log(f"{symbol}: p_thr={p_thr:.6f} c_thr={c_thr:.6f} (train={len(train_df)} bars, {len(train_dates)} days)")
    return p_thr, c_thr


def ensure_tables():
    """Создать таблицы если не существуют (CH)."""
    ch = get_ch()
    ch.command("""
        CREATE TABLE IF NOT EXISTS moex.strategy_paper_trades (
            id Int32,
            ticker String,
            direction String,
            entry_price Float64,
            exit_price Nullable(Float64),
            entry_time DateTime,
            exit_time Nullable(DateTime),
            pnl_rub Nullable(Float64),
            signal_type String DEFAULT 'cvd_divergence',
            status String DEFAULT 'open',
            strategy String DEFAULT 'cvd_divergence',
            created_at DateTime DEFAULT now(),
            updated_at DateTime DEFAULT now()
        ) ENGINE = MergeTree()
        ORDER BY (ticker, entry_time)
    """)
    ch.command("""
        CREATE TABLE IF NOT EXISTS moex.strategy_portfolio_state (
            strategy String,
            capital Float64 DEFAULT 100000.0,
            peak_capital Float64 DEFAULT 100000.0,
            lots Int32 DEFAULT 1,
            updated_at DateTime DEFAULT now()
        ) ENGINE = MergeTree()
        ORDER BY (strategy, updated_at)
    """)


def get_next_id():
    ch = get_ch()
    r = ch.query("SELECT max(id) FROM moex.strategy_paper_trades")
    max_id = r.result_rows[0][0]
    return 1 if max_id is None else int(max_id) + 1


def get_open_trades():
    ch = get_ch()
    rows = ch.query("""
        SELECT id, ticker, direction, entry_price, entry_time
        FROM moex.strategy_paper_trades
        WHERE status = 'open' AND strategy = 'cvd_divergence'
        ORDER BY entry_time
    """).result_rows
    trades = {}
    for row in rows:
        trades[str(row[1])] = {
            'id': row[0],
            'ticker': str(row[1]),
            'direction': str(row[2]),
            'entry_price': float(row[3]),
            'entry_time': row[4],
        }
    return trades


def get_capital_from_closed_trades():
    """Рассчитать капитал с начала: 100K + сумма PnL всех закрытых сделок."""
    ch = get_ch()
    rows = ch.query("""
        SELECT coalesce(sum(pnl_rub), 0)
        FROM moex.strategy_paper_trades
        WHERE strategy = 'cvd_divergence' AND status = 'closed'
    """).result_rows
    closed_pnl = float(rows[0][0])
    capital = INITIAL_CAPITAL + closed_pnl
    peak = max(INITIAL_CAPITAL, capital)
    return capital, peak


def save_portfolio_snapshot(capital, peak_capital, lots=1):
    """Дописать текущее состояние в portfolio_state (одна запись за запуск)."""
    ch = get_ch()
    ch.command(f"""
        INSERT INTO moex.strategy_portfolio_state (strategy, capital, peak_capital, lots, updated_at)
        VALUES ('cvd_divergence', {capital:.2f}, {peak_capital:.2f}, {lots}, now())
    """)


def insert_trade(ticker, direction, entry_price, entry_time, signal_type='cvd_divergence'):
    ch = get_ch()
    tid = get_next_id()
    direction_str = 'long' if direction == 1 else 'short'
    ts = entry_time.strftime('%Y-%m-%d %H:%M:%S') if hasattr(entry_time, 'strftime') else str(entry_time)
    ch.command(f"""
        INSERT INTO moex.strategy_paper_trades (id, ticker, direction, entry_price, entry_time, signal_type, status, strategy)
        VALUES ({tid}, '{ticker}', '{direction_str}', {entry_price:.6f}, '{ts}', '{signal_type}', 'open', 'cvd_divergence')
    """)
    log(f"TRADE ENTRY: {ticker} {direction_str} @ {entry_price:.4f} ({ts})")
    return tid


def close_trade(ticker, exit_price, exit_time, pnl_rub):
    ch = get_ch()
    ts = exit_time.strftime('%Y-%m-%d %H:%M:%S') if hasattr(exit_time, 'strftime') else str(exit_time)
    ch.command(f"""
        ALTER TABLE moex.strategy_paper_trades
        UPDATE exit_price = {exit_price:.6f}, exit_time = '{ts}',
               pnl_rub = {pnl_rub:.2f}, status = 'closed', updated_at = now()
        WHERE ticker = '{ticker}' AND status = 'open' AND strategy = 'cvd_divergence'
    """)
    log(f"TRADE EXIT: {ticker} @ {exit_price:.4f} pnl={pnl_rub:+.2f}")


def load_oi_data(ticker, ch):
    """Загрузить OI из prices_5m_oi и посчитать yur_net_z на нужном tf."""
    oi_sym = TICKER_TO_OI.get(ticker)
    if oi_sym is None:
        return None
    cfg = OI_FILTER_CONFIG.get(ticker)
    if cfg is None:
        return None
    tf_min = cfg['tf_min']
    q = f"""
        SELECT time, yur_buy, yur_sell FROM moex.prices_5m_oi
        WHERE symbol='{oi_sym}' AND time >= '2025-01-01'
        ORDER BY time
    """
    try:
        rows = ch.query(q).result_rows
    except Exception as e:
        log(f"{ticker}: OI load error: {e}")
        return None
    if not rows or len(rows) < 20:
        return None
    df = pd.DataFrame(rows, columns=['time','yur_buy','yur_sell'])
    df['time'] = pd.to_datetime(df['time']).dt.tz_localize(None)
    for c in ['yur_buy','yur_sell']: df[c] = df[c].astype(float)
    df['yur_net'] = df['yur_buy'] - df['yur_sell']

    # Resample на tf_min
    df = df.set_index('time')
    r = df['yur_net'].resample(f'{tf_min}min', closed='right', label='right').last().dropna()
    r = r.to_frame('yur_net')
    r['yur_net_chg'] = r['yur_net'].diff()

    # Rolling z-score
    window = max(10, 20 * 5 // max(tf_min, 5))
    m = r['yur_net_chg'].rolling(window, min_periods=5).mean()
    s = r['yur_net_chg'].rolling(window, min_periods=5).std().clip(lower=1e-10)
    r['yur_net_z'] = (r['yur_net_chg'] - m) / s
    return r.reset_index()[['time','yur_net_z','yur_net_chg']]


def check_yur_filter(ticker, direction, signal_time, oi_cache):
    """Проверить YURz-фильтр: конфлюэнс direction * yur_net_chg + |z| >= threshold.
    oi_cache — DataFrame с колонками time, yur_net_z, yur_net_chg.
    """
    cfg = OI_FILTER_CONFIG.get(ticker)
    if cfg is None or oi_cache is None or oi_cache.empty:
        return True  # нет фильтра — пропускаем
    # Ищем ближайший OI-бар до signal_time
    mask = oi_cache['time'] <= signal_time
    if not mask.any():
        return True
    nearest = oi_cache[mask].iloc[-1]
    if pd.isna(nearest['yur_net_z']) or pd.isna(nearest['yur_net_chg']):
        return True
    # Проверка конфлюэнса: направление сигнала совпадает с направлением ЮР-движения
    if direction * nearest['yur_net_chg'] <= 0:
        return False
    # Проверка порога
    if abs(nearest['yur_net_z']) < cfg['yur_z']:
        return False
    return True


def main():
    """Основной цикл paper trader."""
    dry_run = '--dry-run' in sys.argv
    log(f"{'='*60}")
    log(f"CVD Divergence Paper Trader — run at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    init_cache_db()

    if not dry_run:
        ensure_tables()

    open_trades = get_open_trades() if not dry_run else {}
    capital, peak_capital = get_capital_from_closed_trades() if not dry_run else (INITIAL_CAPITAL, INITIAL_CAPITAL)

    if not dry_run:
        save_portfolio_snapshot(capital, peak_capital)

    log(f"Capital: {capital:,.0f} | Peak: {peak_capital:,.0f}")
    log(f"Open positions: {list(open_trades.keys()) if open_trades else 'none'}")

    total_pnl_rub = 0.0
    new_trades = 0
    changes = []

    # ── 1a. Прогрев + train + self-check ──────────────────────────────
    thresholds = {}
    df_5m_by_symbol = {}
    abort = False
    just_exited = set()
    for symbol in SYMBOLS:
        df_1m = ensure_cache_for_symbol(symbol, days_back=TRAIN_DAYS + 5)
        if df_1m.empty:
            log(f"{symbol}: NO DATA — aborting")
            abort = True
            continue
        # Paper trader использует данные из AlgoPack API (мульти-потоковые)
        df_5m = resample_to_5m(df_1m, deduplicate=True)
        if df_5m.empty or len(df_5m) < 50:
            log(f"{symbol}: insufficient 5m bars ({len(df_5m)}) — aborting")
            abort = True
            continue
        df_5m_by_symbol[symbol] = df_5m

        p_thr, c_thr = get_latest_thresholds(symbol)
        if p_thr is None or c_thr is None or p_thr == 0.0 or c_thr == 0.0:
            log(f"{symbol}: bad thresholds p={p_thr} c={c_thr} — aborting")
            abort = True
            continue
        thresholds[symbol] = (p_thr, c_thr)
        log(f"  ✓ {symbol}: p_thr={p_thr:.6f} c_thr={c_thr:.6f} ({len(df_5m)} bars)")

    if abort:
        log(f"SELF-CHECK FAILED — aborting paper trader")
        print(f"❌ CVD Paper Trader: self-check failed — see log for details", flush=True)
        return

    log("✅ Self-check passed — all symbols OK")
    if not dry_run:
        log("Starting trading loop")

    for symbol in SYMBOLS:
        df_5m = df_5m_by_symbol.get(symbol, pd.DataFrame())
        if df_5m.empty or len(df_5m) < 50:
            log(f"{symbol}: insufficient 5m bars ({len(df_5m)})")
            continue

        p_thr, c_thr = thresholds.get(symbol, (None, None))
        if p_thr is None:
            continue

        # ── 3. Детектим сигналы ─────────────────────────────────────────
        df_signals = detect_signals(df_5m, p_thr, c_thr)
        if df_signals.empty:
            continue

        recent = df_signals.iloc[-10:].copy().reset_index(drop=True)
        if len(recent) < 3:
            continue

        today_ds = date.today().isoformat()

        # ── 4. Закрытие позиций ────────────────────────────────────────
        if symbol in open_trades:
            trade = open_trades[symbol]
            entry_price = trade['entry_price']
            direction = 1 if trade['direction'] == 'long' else -1

            last_bar = recent.iloc[-1]
            exit_price = float(last_bar['close'])
            exit_time = last_bar['time']

            # PnL с slippage: 0.5 тика на вход + 1.0 тик на выход
            pnl_rub, slippage_total = calc_pnl_rub(
                symbol, entry_price, exit_price, direction,
                slippage_in_ticks=SLIPPAGE_IN_TICKS,
                slippage_out_ticks=SLIPPAGE_OUT_TICKS,
            )

            if not dry_run:
                close_trade(symbol, exit_price, exit_time, pnl_rub)

            go = GO.get(symbol, 3000)
            max_lots = 1  # 1 контракт
            pnl_total = pnl_rub * max_lots
            capital += pnl_total
            peak_capital = max(peak_capital, capital)
            total_pnl_rub += pnl_total

            changes.append(f"  {symbol}: CLOSE {trade['direction']} @ {exit_price:.4f} pnl={pnl_rub:+.2f}")
            log(f"{symbol}: CLOSE {trade['direction']} entry={entry_price} exit={exit_price} pnl={pnl_rub:+.2f} slippage={slippage_total:.2f}")
            just_exited.add(symbol)
            del open_trades[symbol]

        # ── 5. Новые сигналы ───────────────────────────────────────────
        if symbol in open_trades:
            continue

        if symbol in just_exited:
            continue

        if len(recent) >= 2:
            signal_bar = recent.iloc[-2]  # предпоследний бар (сигнальный)
            signal_val = int(signal_bar['signal'])

            if signal_val == 0:
                continue

            # Цена закрытия сигнального бара — базовая цена
            close_price = float(signal_bar['close'])

            # Адаптивный сдвиг лимитки от ATR(14)
            slippage_ticks = calc_slippage_ticks(symbol, df_5m)
            tick = TICK.get(symbol, 0.001)

            # Цена лимитного ордера: сдвиг в сторону сигнала
            limit_price = calc_entry_price(close_price, signal_val, slippage_ticks, tick)
            entry_time = signal_bar['time']

            # ── ПРОВЕРКА КАСАНИЯ ═══════════════════════════════════════
            bar_high = float(signal_bar.get('high', close_price))
            bar_low = float(signal_bar.get('low', close_price))

            touches = check_touch(bar_high, bar_low, limit_price, signal_val)

            if not touches:
                log(f"NO TOUCH: {symbol} {'LONG' if signal_val==1 else 'SHORT'} "
                    f"limit={limit_price:.4f} bar(H={bar_high:.4f} L={bar_low:.4f}) "
                    f"time={entry_time} — skipping")
                continue

            # Проверяем уникальность (только сегодняшние сигналы)
            if not dry_run:
                ts = entry_time.strftime('%Y-%m-%d %H:%M:%S') if hasattr(entry_time, 'strftime') else str(entry_time)
                existing = get_ch().query(f"""
                    SELECT count() FROM moex.strategy_paper_trades
                    WHERE ticker = '{symbol}' AND entry_time = '{ts}' AND status = 'open'
                """).result_rows[0][0]
                if existing > 0:
                    continue

            direction_name = 'long' if signal_val == 1 else 'short'

            if not dry_run:
                insert_trade(symbol, signal_val, limit_price, entry_time)

            new_trades += 1
            changes.append(f"  {symbol}: 🟢 OPEN {direction_name} @ {limit_price:.4f}")

    # ── 6. Обновляем portfolio_state ────────────────────────────────────
    if not dry_run:
        lots = 1
        save_portfolio_snapshot(capital, peak_capital)

    # ── 7. Вывод ────────────────────────────────────────────────────────
    total_return = (capital / INITIAL_CAPITAL - 1) * 100
    if changes:
        print(f"\n📊 CVD Divergence Paper — {datetime.now().strftime('%d.%m %H:%M')}")
        print(f"💵 {capital:,.0f} ₽ ({total_return:+.2f}%) | Peak: {peak_capital:,.0f}")
        for line in changes:
            print(line)
        open_cnt = len(get_open_trades()) if not dry_run else 0
        print(f"  📌 Open: {open_cnt}")
    else:
        print(f"💤 CVD Paper — Flat — {capital:,.0f} ₽ ({total_return:+.2f}%)")

    log(f"Run complete: capital={capital:,.0f} peak={peak_capital:,.0f} new_trades={new_trades} pnl={total_pnl_rub:+.2f}")
    log(f"{'='*60}")


def catchup_missed_signals():
    """В начале дня проверяем пропущенные сигналы.
    
    Ограничено только сегодняшним днём — сигналы старше сегодня не записываем
    (предотвращает look-ahead из исторических данных).
    """
    log("Catchup: checking for missed signals...")
    init_cache_db()
    ensure_tables()
    capital, _ = get_capital_from_closed_trades()
    today = date.today()

    for symbol in SYMBOLS:
        df_1m = ensure_cache_for_symbol(symbol, days_back=TRAIN_DAYS + 5)
        if df_1m.empty:
            continue
        # Для catchup используем deduplicate=True — это данные из API/файла
        df_5m = resample_to_5m(df_1m, deduplicate=True)
        if df_5m.empty or len(df_5m) < 100:
            continue

        p_thr, c_thr = get_latest_thresholds(symbol)
        if p_thr is None:
            continue

        df_signals = detect_signals(df_5m, p_thr, c_thr)
        if df_signals.empty:
            continue

        signals = df_signals[df_signals['signal'] != 0]
        # Только сегодняшние сигналы!
        signals = signals[signals['date'] == today]

        # OI (YURz) фильтр — загружаем один раз для всех сигналов символа
        oi_data = load_oi_data(symbol, ch)
        if oi_data is not None and not oi_data.empty:
            log(f"{symbol}: OI filter loaded ({len(oi_data)} bars, tf={OI_FILTER_CONFIG[symbol]['tf_min']}min)")
            oi_signals_before = len(signals)
            keep = []
            for sidx, srow in signals.iterrows():
                direction_n = 1 if srow['signal'] == 1 else -1
                if check_yur_filter(symbol, direction_n, srow['time'], oi_data):
                    keep.append(sidx)
            signals = signals.loc[keep]
            filtered_cnt = oi_signals_before - len(signals)
            if filtered_cnt > 0:
                log(f"{symbol}: OI filter removed {filtered_cnt}/{oi_signals_before} signals")
        else:
            log(f"{symbol}: OI filter not available (proceeding without)")
            oi_data = None

        log(f"{symbol}: {len(signals)} signal(s) today")
        for _, row in signals.iterrows():
            entry_time = row['time']
            ts = pd.Timestamp(entry_time).strftime('%Y-%m-%d %H:%M:%S')
            existing = get_ch().query(f"""
                SELECT count() FROM moex.strategy_paper_trades
                WHERE ticker = '{symbol}' AND entry_time = '{ts}'
            """).result_rows[0][0]
            if existing > 0:
                continue

            signal_val = int(row['signal'])
            close_price = float(row['close'])
            tick = TICK.get(symbol, 0.001)
            limit_price = calc_entry_price(close_price, signal_val, FIXED_SLIPPAGE_TICKS, tick)

            # Проверка касания
            bar_high = float(row.get('high', close_price))
            bar_low = float(row.get('low', close_price))
            touches = check_touch(bar_high, bar_low, limit_price, signal_val)

            if not touches:
                log(f"CATCHUP NO TOUCH: {symbol} limit={limit_price:.4f} bar(H={bar_high:.4f} L={bar_low:.4f}) — skipping")
                continue

            insert_trade(symbol, signal_val, limit_price, entry_time)
            caught += 1

        if caught > 0:
            log(f"CATCHUP: {symbol} — {caught} missed signals recorded (today only)")


if __name__ == '__main__':
    if '--catchup' in sys.argv:
        catchup_missed_signals()
    else:
        main()
