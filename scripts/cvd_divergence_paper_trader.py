#!/usr/bin/env python3
"""
CVD divergence paper trader — live on M5 MOEX futures via AlgoPack API.

Логика (как в честном бэктесте v4):
  1. Данные через AlgoPack REST API (fo/tradestats), не через CH
  2. Ресемпл 1м → 5м: open='first', close='last', vol_b='sum', vol_s='sum'
  3. CVD = vol_b - vol_s, CVD_cum = cumsum
  4. Walk-forward: train 180d / test 60d для порогов p_thr, c_thr (quantile q=0.6)
  5. Сигнал: (close.diff(lk) > p_thr AND cvd_cum.diff(lk) > c_thr)
  6. Entry: лимитка по close сигнального бара, hold=1 (выход на след. 5м баре по close)
  7. Комиссия 0 (мейкер), slippage 0.5 тика

Запуск: каждые 5 мин в будни 09:00-23:50 IRKT (MSK+5)
"""

import os, sys, json, time, requests
from datetime import datetime, timedelta, date
import pandas as pd
import numpy as np
import clickhouse_connect

# ── Пути ──────────────────────────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR) if os.path.basename(SCRIPT_DIR) == 'scripts' else SCRIPT_DIR
DATA_DIR = os.path.join(os.path.expanduser("~"), ".hermes", "data", "cvd_paper")
os.makedirs(DATA_DIR, exist_ok=True)
LOG_FILE = os.path.join(DATA_DIR, "trades.log")

# ── Параметры стратегии ────────────────────────────────────────────────────
INITIAL_CAPITAL = 100_000.0
LK = 20
HOLD_BARS = 1
Q = 0.6
SLIPPAGE_TICKS = 0.5
COMMISSION = 0.0  # мейкерская
N_SYMS = 4
SYMBOLS = ['NG', 'BR', 'Si', 'MXI']

TICK = {'NG': 0.0005, 'BR': 0.001, 'Si': 0.0025, 'MXI': 0.01}
TICK_COST = {'NG': 3.715, 'BR': 0.743, 'Si': 0.0025, 'MXI': 0.10}
GO = {'NG': 4800, 'BR': 3500, 'Si': 2500, 'MXI': 2000}
TRAIN_DAYS = 180

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


def get_ch():
    global CH
    if CH is None:
        CH = clickhouse_connect.get_client(host=CH_HOST, database='moex')
    return CH


# ── Логирование ────────────────────────────────────────────────────────────
def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")
    print(line, flush=True)


# ── AlgoPack API — получение 1м данных ─────────────────────────────────────
ALGOPACK_COLUMNS_CACHE = None


def fetch_algopack_day(ds):
    """Загрузить все строки за один день из fo/tradestats."""
    global ALGOPACK_COLUMNS_CACHE
    all_rows = []
    cols = None
    start = 0
    while True:
        params = {"date": ds, "limit": 1000, "start": start}
        try:
            r = requests.get(ALGOPACK_URL, params=params, headers=ALGOPACK_HEADERS, timeout=30)
        except Exception as e:
            log(f"API error for {ds} start={start}: {e}")
            break
        if r.status_code != 200:
            log(f"API status {r.status_code} for {ds} start={start}")
            break
        j = r.json()
        data = j.get("data", {}).get("data", [])
        if not data:
            break
        if cols is None:
            cols = j.get("data", {}).get("columns", [])
            if ALGOPACK_COLUMNS_CACHE is None:
                ALGOPACK_COLUMNS_CACHE = cols
        all_rows.extend(data)
        if len(data) < 1000:
            break
        start += 1000
    return all_rows, cols


def get_1m_bars(symbol, days_back=200):
    """Загрузить 1м бары для символа за последние days_back дней через AlgoPack API."""
    today = date.today()
    all_rows_raw = []
    cols = None

    for i in range(days_back, 0, -1):
        ds = (today - timedelta(days=i)).isoformat()
        raw, c = fetch_algopack_day(ds)
        if not raw:
            continue
        if cols is None and c:
            cols = c
        elif cols is None and ALGOPACK_COLUMNS_CACHE:
            cols = ALGOPACK_COLUMNS_CACHE
        all_rows_raw.extend(raw)

    if not all_rows_raw or cols is None:
        return pd.DataFrame()

    # Фильтруем по asset_code
    records = []
    for row in all_rows_raw:
        d = dict(zip(cols, row))
        if d.get('asset_code') != symbol:
            continue
        records.append({
            'tradedate': str(d['tradedate']) if d.get('tradedate') else '',
            'tradetime': str(d['tradetime']) if d.get('tradetime') else '',
            'open': float(d['pr_open']) if d.get('pr_open') is not None else None,
            'close': float(d['pr_close']) if d.get('pr_close') is not None else None,
            'vol_b': int(d['vol_b']) if d.get('vol_b') is not None else 0,
            'vol_s': int(d['vol_s']) if d.get('vol_s') is not None else 0,
        })

    df = pd.DataFrame(records)
    if df.empty:
        return df

    df['time'] = pd.to_datetime(df['tradedate'] + ' ' + df['tradetime'])
    df.sort_values('time', inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df


def resample_to_5m(df):
    """Ресемпл 1м → 5м: open='first', close='last', vol_b='sum', vol_s='sum'."""
    if df.empty:
        return df

    df = df.set_index('time')
    resampled = df.resample('5T', closed='right', label='right').agg({
        'open': 'first',
        'close': 'last',
        'vol_b': 'sum',
        'vol_s': 'sum',
        'tradedate': 'last',
    })
    resampled = resampled.dropna(subset=['open', 'close'])
    resampled = resampled.reset_index()
    # date колонка
    resampled['date'] = pd.to_datetime(resampled['time']).dt.date
    return resampled


# ── Walk-forward пороги ────────────────────────────────────────────────────
def calc_thresholds(train_df, lk=LK, q=Q):
    """Рассчитать p_thr и c_thr на train-данных."""
    if train_df.empty or len(train_df) < lk + 10:
        return None, None

    train = train_df.copy()
    train['cvd'] = train['vol_b'].fillna(0) - train['vol_s'].fillna(0)
    train['cvd_cum'] = train['cvd'].cumsum()
    train['pchg'] = train['close'].diff(lk)
    train['cchg'] = train['cvd_cum'].diff(lk)
    train_v = train.dropna(subset=['pchg', 'cchg'])

    if len(train_v) < 50:
        return None, None

    p_thr = train_v['pchg'].abs().quantile(q)
    c_thr = train_v['cchg'].abs().quantile(q)

    if p_thr == 0 or c_thr == 0:
        return None, None

    return float(p_thr), float(c_thr)


def get_latest_thresholds(symbol):
    """Walk-forward: train на последних 180 днях, считаем пороги."""
    df_1m = get_1m_bars(symbol, days_back=TRAIN_DAYS + 5)
    if df_1m.empty:
        log(f"{symbol}: no data for threshold calculation")
        return None, None

    df_5m = resample_to_5m(df_1m)
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


# ── Сигналы ────────────────────────────────────────────────────────────────
def detect_signals(df_5m, p_thr, c_thr, lk=LK):
    """Детектить CVD divergence сигналы на M5 барах.
    signal: 1 (bullish), -1 (bearish), 0 (none).
    """
    if df_5m.empty or p_thr is None or c_thr is None:
        return df_5m

    df = df_5m.copy()
    df['cvd'] = df['vol_b'].fillna(0) - df['vol_s'].fillna(0)
    df['cvd_cum'] = df['cvd'].cumsum()
    df['pchg'] = df['close'].diff(lk)
    df['cchg'] = df['cvd_cum'].diff(lk)

    # Bullish: цена падает, CVD растёт
    bullish = (df['pchg'] < -p_thr) & (df['cchg'] > c_thr)
    # Bearish: цена растёт, CVD падает
    bearish = (df['pchg'] > p_thr) & (df['cchg'] < -c_thr)

    df['signal'] = 0
    df.loc[bullish, 'signal'] = 1
    df.loc[bearish, 'signal'] = -1

    return df


# ── CH операции ─────────────────────────────────────────────────────────────
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

    cnt = ch.query("SELECT count() FROM moex.strategy_portfolio_state WHERE strategy = 'cvd_divergence'").result_rows[0][0]
    if cnt == 0:
        ch.command("INSERT INTO moex.strategy_portfolio_state (strategy, capital, peak_capital, lots) VALUES ('cvd_divergence', 100000.0, 100000.0, 1)")
        log("Initial portfolio state created")


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


def get_portfolio_state():
    ch = get_ch()
    rows = ch.query("""
        SELECT capital, peak_capital, lots
        FROM moex.strategy_portfolio_state
        WHERE strategy = 'cvd_divergence'
        ORDER BY updated_at DESC
        LIMIT 1
    """).result_rows
    if rows:
        return {'capital': float(rows[0][0]), 'peak_capital': float(rows[0][1]), 'lots': int(rows[0][2])}
    return {'capital': INITIAL_CAPITAL, 'peak_capital': INITIAL_CAPITAL, 'lots': 1}


def update_portfolio_state(capital, peak_capital, lots):
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


# ── Основная логика ────────────────────────────────────────────────────────
def main():
    """Основной цикл paper trader."""
    dry_run = '--dry-run' in sys.argv
    log(f"{'='*60}")
    log(f"CVD Divergence Paper Trader — run at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    if not dry_run:
        ensure_tables()

    state = get_portfolio_state() if not dry_run else {'capital': INITIAL_CAPITAL, 'peak_capital': INITIAL_CAPITAL, 'lots': 1}
    open_trades = get_open_trades() if not dry_run else {}
    capital = state['capital']
    peak_capital = state['peak_capital']

    log(f"Capital: {capital:,.0f} | Peak: {peak_capital:,.0f}")
    log(f"Open positions: {list(open_trades.keys()) if open_trades else 'none'}")

    total_pnl_rub = 0.0
    new_trades = 0
    changes = []

    for symbol in SYMBOLS:
        # ── 1. Загружаем данные ─────────────────────────────────────────
        df_1m = get_1m_bars(symbol, days_back=TRAIN_DAYS + 5)
        if df_1m.empty:
            log(f"{symbol}: NO DATA")
            continue

        df_5m = resample_to_5m(df_1m)
        if df_5m.empty or len(df_5m) < 50:
            log(f"{symbol}: insufficient 5m bars ({len(df_5m)})")
            continue

        # ── 2. Walk-forward пороги ─────────────────────────────────────
        p_thr, c_thr = get_latest_thresholds(symbol)
        if p_thr is None:
            continue

        # ── 3. Детектим сигналы ─────────────────────────────────────────
        df_signals = detect_signals(df_5m, p_thr, c_thr)
        if df_signals.empty:
            continue

        recent = df_signals.iloc[-10:].copy().reset_index(drop=True)
        if len(recent) < 3:
            continue

        # ── 4. Закрытие позиций ────────────────────────────────────────
        if symbol in open_trades:
            trade = open_trades[symbol]
            entry_price = trade['entry_price']
            direction = 1 if trade['direction'] == 'long' else -1

            last_bar = recent.iloc[-1]
            exit_price = float(last_bar['close'])
            exit_time = last_bar['time']

            tick = TICK.get(symbol, 0.001)
            tick_cost = TICK_COST.get(symbol, 1.0)
            pnl_ticks = (exit_price - entry_price) * direction / tick
            slippage_rub = SLIPPAGE_TICKS * tick_cost
            pnl_rub = pnl_ticks * tick_cost - slippage_rub  # комиссия 0

            if not dry_run:
                close_trade(symbol, exit_price, exit_time, pnl_rub)

            go = GO.get(symbol, 3000)
            max_lots = max(1, int(capital / N_SYMS / go))
            pnl_total = pnl_rub * max_lots
            capital += pnl_total
            peak_capital = max(peak_capital, capital)
            total_pnl_rub += pnl_total

            changes.append(f"  {symbol}: 🔴 CLOSE {trade['direction']} @ {exit_price:.4f} pnl={pnl_rub:+.2f}")
            log(f"{symbol}: CLOSE {trade['direction']} entry={entry_price} exit={exit_price} pnl={pnl_rub:+.2f}")
            del open_trades[symbol]

        # ── 5. Новые сигналы ───────────────────────────────────────────
        if symbol in open_trades:
            continue

        # Сигнал на предпоследнем завершённом баре
        if len(recent) >= 2:
            signal_bar = recent.iloc[-2]
            signal_val = int(signal_bar['signal'])

            if signal_val == 0:
                continue

            entry_price = float(signal_bar['close'])
            entry_time = signal_bar['time']

            # Проверяем уникальность
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
                insert_trade(symbol, signal_val, entry_price, entry_time)

            new_trades += 1
            changes.append(f"  {symbol}: 🟢 OPEN {direction_name} @ {entry_price:.4f}")

    # ── 6. Обновляем portfolio_state ────────────────────────────────────
    if not dry_run:
        lots = max(1, min(4, int(capital / max(GO.values()))))
        update_portfolio_state(capital, peak_capital, lots)

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


# ── Проверка пропущенных сигналов ──────────────────────────────────────────
def catchup_missed_signals():
    """В начале дня проверяем пропущенные сигналы."""
    log("Catchup: checking for missed signals...")
    ensure_tables()
    state = get_portfolio_state()
    capital = state['capital']

    for symbol in SYMBOLS:
        df_1m = get_1m_bars(symbol, days_back=TRAIN_DAYS + 5)
        if df_1m.empty:
            continue
        df_5m = resample_to_5m(df_1m)
        if df_5m.empty or len(df_5m) < 100:
            continue

        p_thr, c_thr = get_latest_thresholds(symbol)
        if p_thr is None:
            continue

        df_signals = detect_signals(df_5m, p_thr, c_thr)
        if df_signals.empty:
            continue

        signals = df_signals[df_signals['signal'] != 0]
        caught = 0
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
            entry_price = float(row['close'])
            insert_trade(symbol, signal_val, entry_price, entry_time)
            caught += 1

        if caught > 0:
            log(f"CATCHUP: {symbol} — {caught} missed signals recorded")


if __name__ == '__main__':
    if '--catchup' in sys.argv:
        catchup_missed_signals()
    else:
        main()
