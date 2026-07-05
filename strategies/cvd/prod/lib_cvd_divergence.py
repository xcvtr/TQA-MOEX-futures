#!/usr/bin/env python3
"""
lib_cvd_divergence.py — Единая библиотека для CVD divergence стратегии.

Содержит общие функции, используемые paper trader'ом, бэктестом v4 и restore_trades.
Все спецификации и параметры синхронизированы в одном месте.

Реалистичная модель исполнения:
1. Вход — лимитный ордер, сдвинутый в сторону сигнала на slippage_ticks
2. Проверка касания (touch-check) — цена должна коснуться лимитного уровня в течение сигнального бара
3. Выход — на следующем 5м баре по close (рыночный)
4. Slippage: 0.5 тика на вход (мейкер) + 1.0 тик на выход (тейкер) = 1.5 тика round-trip
"""

import os
import pandas as pd
import numpy as np
from datetime import datetime, date
import psycopg2

# ── PG config for ticker specs ──────────────────────────────────────────
DB_HOST = os.environ.get('MOEX_DB_HOST', '10.0.0.60')
DB_PORT = int(os.environ.get('MOEX_DB_PORT', 5432))
DB_NAME = os.environ.get('MOEX_DB_NAME', 'moex')
DB_USER = os.environ.get('MOEX_DB_USER', 'user')
DB_PASS = os.environ.get('MOEX_DB_PASSWORD', '')

# ── Ticker specs (lazy-loaded from PG ticker_specs) ────────────────────
_TICKER_SPECS_CACHE = None
TICK = {}          # {ticker: min_step} — lazy-loaded
TICK_COST = {}     # {ticker: step_price} — lazy-loaded
TICK_LOT = {}      # {ticker: lot_volume} — lazy-loaded
TICK_PCT = {}      # {ticker: pct} — lazy-loaded
GO = {}            # {ticker: go} — lazy-loaded
SYMBOLS = ['NG', 'BR', 'Si', 'MXI']
N_SYMS = len(SYMBOLS)


def load_ticker_specs(tickers=None):
    """Загрузить спецификации тикеров из PG ticker_specs.

    Параметры:
        tickers: список тикеров (или None — загрузить все)

    Возвращает dict: {ticker: {'min_step': float, 'step_price': float,
                                'lot': int, 'go': float}}
    Если тикер не найден в БД — возвращает дефолтные значения.
    """
    global _TICKER_SPECS_CACHE, TICK, TICK_COST, GO
    try:
        conn = psycopg2.connect(
            host=DB_HOST, port=DB_PORT,
            dbname=DB_NAME, user=DB_USER, password=DB_PASS,
            connect_timeout=5,
        )
        cur = conn.cursor()
        if tickers is not None and len(tickers) > 0:
            placeholders = ','.join(['%s'] * len(tickers))
            cur.execute(f'''
                SELECT ticker, min_step, step_price, lot_volume, go
                FROM futures.ticker_specs
                WHERE ticker IN ({placeholders})
            ''', list(tickers))
        else:
            cur.execute(
                'SELECT ticker, min_step, step_price, lot_volume, go FROM futures.ticker_specs'
            )
        rows = cur.fetchall()
        cur.close()
        conn.close()

        specs = {}
        for r in rows:
            pct = float(r[5]) if len(r) > 5 else 1.0
            specs[str(r[0])] = {
                'min_step': float(r[1]),
                'step_price': float(r[2]),
                'lot': int(r[3]),
                'go': float(r[4]),
                'pct': pct,
            }

        # If specific tickers were requested — fill missing with defaults
        if tickers is not None:
            for t in tickers:
                if t not in specs:
                    specs[t] = {
                        'min_step': 0.01,
                        'step_price': 1.0,
                        'lot': 1,
                        'go': 0.0,
                    }

        # Update module-level dicts for backward compatibility (mutate in-place)
        TICK.clear()
        TICK.update({t: s['min_step'] for t, s in specs.items()})
        TICK_COST.clear()
        TICK_COST.update({t: s['step_price'] for t, s in specs.items()})
        TICK_LOT.clear()
        TICK_LOT.update({t: s['lot'] for t, s in specs.items()})
        TICK_PCT.clear()
        TICK_PCT.update({t: s['pct'] for t, s in specs.items()})
        GO.clear()
        GO.update({t: s['go'] for t, s in specs.items()})

        _TICKER_SPECS_CACHE = specs
        return specs
    except Exception as e:
        print(f'  ⚠ PG load_ticker_specs failed ({e}), using defaults')
        # Fallback: return defaults for requested tickers
        specs = {}
        target = tickers if tickers is not None else SYMBOLS
        for t in target:
            specs[t] = {
                'min_step': 0.01,
                'step_price': 1.0,
                'lot': 1,
                'go': 0.0,
            }
        TICK.clear()
        TICK_COST.clear()
        GO.clear()
        TICK.update({t: s['min_step'] for t, s in specs.items()})
        TICK_COST.update({t: s['step_price'] for t, s in specs.items()})
        GO.update({t: s['go'] for t, s in specs.items()})
        _TICKER_SPECS_CACHE = specs
        return specs


def _ensure_specs():
    """Загрузить specs при первом обращении (если ещё не загружены)."""
    global _TICKER_SPECS_CACHE
    if _TICKER_SPECS_CACHE is None:
        load_ticker_specs()


def get_tick(ticker):
    """Вернуть min_step (TICK) для тикера. Кеширует specs при первом вызове.

    Если тикер не найден — возвращает 0.01.
    """
    _ensure_specs()
    return TICK.get(ticker, 0.01)


def get_tick_cost(ticker):
    """Вернуть step_price (TICK_COST) для тикера. Кеширует specs при первом вызове.

    Если тикер не найден — возвращает 1.0.
    """
    _ensure_specs()
    return TICK_COST.get(ticker, 1.0)


def get_tick_lot(ticker):
    """Вернуть lot_volume (TICK_LOT) для тикера. Кеширует specs при первом вызове.

    Если тикер не найден — возвращает 1.
    """
    _ensure_specs()
    return TICK_LOT.get(ticker, 1)


def get_tick_pct(ticker):
    """Вернуть pct (TICK_PCT) для тикера. Кеширует specs при первом вызове.

    Если тикер не найден — возвращает 1.0.
    """
    _ensure_specs()
    return TICK_PCT.get(ticker, 1.0)


def get_go(ticker):
    """Вернуть GO (гарантийное обеспечение) для тикера.

    Если тикер не найден — возвращает 0.0.
    """
    _ensure_specs()
    return GO.get(ticker, 0.0)

# ── Параметры стратегии ───────────────────────────────────────────────────
LK = 20
HOLD_BARS = 1
Q = 0.6
INITIAL_CAPITAL = 100_000.0

# ── Slippage (тики) ──────────────────────────────────────────────────────
SLIPPAGE_IN_TICKS = 0.5     # лимитка (мейкер)
SLIPPAGE_OUT_TICKS = 1.0    # выход по close (тейкер)
ROUND_TRIP_TICKS = SLIPPAGE_IN_TICKS + SLIPPAGE_OUT_TICKS  # 1.5 тика

# ── Адаптивный сдвиг лимитки ──────────────────────────────────────────────
MIN_SLIPPAGE_TICKS = 5      # минимум 5 тиков
MAX_SLIPPAGE_TICKS = 20     # максимум 20 тиков
FIXED_SLIPPAGE_TICKS = 10   # запасной (когда нет ATR)


# ═══════════════════════════════════════════════════════════════════════════
#  1. ДЕДУПЛИКАЦИЯ МУЛЬТИ-ПОТОКОВЫХ 1М ДАННЫХ
# ═══════════════════════════════════════════════════════════════════════════

def deduplicate_1m(df):
    """Для каждой минуты оставляем запись с max(vol_b+vol_s).
    
    MOEX AlgoPack возвращает несколько потоков (TOD/TOM/разные серии)
    за одну минуту. Выбираем основной — с максимальным суммарным объёмом.
    Если vol_b+vol_s=0 у всех записей минуты — отбрасываем минуту целиком.
    
    Работает только если в данных есть колонки high/low (т.е. сырые данные
    из файла или API). Для выгрузки из CH tradestats_fo не вызывать —
    там уже один ряд на минуту.
    
    Возвращает DataFrame с уникальными минутами.
    """
    df = df.copy()
    df['tot_vol'] = df['vol_b'].fillna(0) + df['vol_s'].fillna(0)
    # Группируем по минутам (округляем time до минуты)
    df['minute_key'] = df['time'].dt.floor('1min')
    idx = df.groupby('minute_key')['tot_vol'].idxmax()
    result = df.loc[idx].drop(columns=['minute_key', 'tot_vol'])
    # Если max тоже 0 — удаляем совсем
    result = result[result['vol_b'].fillna(0) + result['vol_s'].fillna(0) > 0]
    return result


# ═══════════════════════════════════════════════════════════════════════════
#  2. РЕСЕМПЛ 1М → 5М
# ═══════════════════════════════════════════════════════════════════════════

def resample_to_5m(df, deduplicate=False):
    """Ресемпл 1м → 5м с OHLC: open='first', high='max'(close), low='min'(close),
       close='last', vol_b='sum', vol_s='sum'.
    
    Фильтр: только торговые часы MSK 09:55-23:50.
    Если в данных нет колонок high/low — аппроксимируем через close 1м баров.
    
    Параметр deduplicate=True — вызывает deduplicate_1m() перед ресемплом
    для фильтрации мульти-потоковых данных.
    
    Возвращает DataFrame с колонками: time, open, close, high, low, vol_b, vol_s, cvd, date.
    """
    if df.empty:
        return df

    # Дедупликация мульти-потоковых данных (если есть колонки high/low)
    if deduplicate and 'high' in df.columns and 'low' in df.columns:
        df = deduplicate_1m(df)
        if df.empty:
            return pd.DataFrame()

    df = df.set_index('time')
    df = df.between_time('09:55', '23:50')
    if df.empty:
        return pd.DataFrame()

    # Ресемпл основных колонок
    resampled = df.resample('5min', closed='right', label='right').agg({
        'open': 'first',
        'close': 'last',
        'vol_b': 'sum',
        'vol_s': 'sum',
    })

    # High/low — аппроксимируем через close, если нет настоящих
    if 'high' in df.columns and 'low' in df.columns:
        resampled['high'] = df['high'].resample('5min', closed='right', label='right').max()
        resampled['low'] = df['low'].resample('5min', closed='right', label='right').min()
    else:
        resampled['high'] = df['close'].resample('5min', closed='right', label='right').max()
        resampled['low'] = df['close'].resample('5min', closed='right', label='right').min()

    resampled = resampled.dropna(subset=['open', 'close'])
    if resampled.empty:
        return pd.DataFrame()

    resampled = resampled.reset_index()
    resampled['cvd'] = resampled['vol_b'].fillna(0) - resampled['vol_s'].fillna(0)
    resampled['date'] = pd.to_datetime(resampled['time']).dt.date
    return resampled


# ═══════════════════════════════════════════════════════════════════════════
#  2. РАСЧЁТ ПОРОГОВ (WALK-FORWARD)
# ═══════════════════════════════════════════════════════════════════════════

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


# ═══════════════════════════════════════════════════════════════════════════
#  3. ДЕТЕКЦИЯ СИГНАЛОВ
# ═══════════════════════════════════════════════════════════════════════════

def detect_signals(df_5m, p_thr, c_thr, lk=LK):
    """Детектить CVD divergence сигналы на M5 барах.
    
    Сигнал: (close.diff(lk) > p_thr AND cvd_cum.diff(lk) > c_thr) — медвежий
            (close.diff(lk) < -p_thr AND cvd_cum.diff(lk) < -c_thr) — бычий
    
    Возвращает DataFrame с колонкой 'signal': 1 (bullish), -1 (bearish), 0 (none).
    """
    if df_5m.empty or p_thr is None or c_thr is None:
        return df_5m

    df = df_5m.copy()
    df['cvd'] = df['vol_b'].fillna(0) - df['vol_s'].fillna(0)
    df['cvd_cum'] = df['cvd'].cumsum()
    df['pchg'] = df['close'].diff(lk)
    df['cchg'] = df['cvd_cum'].diff(lk)

    bullish = (df['pchg'] < -p_thr) & (df['cchg'] > c_thr)
    bearish = (df['pchg'] > p_thr) & (df['cchg'] < -c_thr)

    df['signal'] = 0
    df.loc[bullish, 'signal'] = 1
    df.loc[bearish, 'signal'] = -1

    return df


# ═══════════════════════════════════════════════════════════════════════════
#  4. РАСЧЁТ ЦЕНЫ ВХОДА (ЛИМИТНЫЙ ОРДЕР)
# ═══════════════════════════════════════════════════════════════════════════

def calc_entry_price(close_price, direction, slippage_ticks, tick):
    """Рассчитать цену лимитного ордера.
    
    Для LONG: цена ниже рынка (close - slippage_ticks * tick)
    Для SHORT: цена выше рынка (close + slippage_ticks * tick)
    
    direction: 1 (long), -1 (short)
    """
    # Для LONG (1) опускаем цену ниже рынка, для SHORT (-1) поднимаем выше
    return close_price - direction * slippage_ticks * tick


# ═══════════════════════════════════════════════════════════════════════════
#  5. ПРОВЕРКА КАСАНИЯ ЛИМИТНОГО УРОВНЯ
# ═══════════════════════════════════════════════════════════════════════════

def check_touch(bar_high, bar_low, limit_price, direction):
    """Проверить, коснулась ли цена лимитного уровня в течение бара.
    
    Для LONG (1): low бара <= limit_price (цена опустилась до нашего buy limit)
    Для SHORT (-1): high бара >= limit_price (цена поднялась до нашего sell limit)
    
    Возвращает True если коснулось.
    """
    if direction == 1:  # long
        return bar_low <= limit_price
    else:  # short
        return bar_high >= limit_price


# ═══════════════════════════════════════════════════════════════════════════
#  6. РАСЧЁТ PnL С SLIPPAGE
# ═══════════════════════════════════════════════════════════════════════════

def calc_pnl_rub(symbol, entry_price, exit_price, direction,
                 slippage_in_ticks=SLIPPAGE_IN_TICKS,
                 slippage_out_ticks=SLIPPAGE_OUT_TICKS):
    """Рассчитать PnL в рублях с учётом slippage round-trip.
    
    pnl_ticks = (exit_price - entry_price) * direction / TICK[symbol]
    slippage_total = (slippage_in_ticks + slippage_out_ticks) * TICK_COST[symbol]
    pnl_rub = pnl_ticks * TICK_COST[symbol] * TICK_LOT[symbol] * TICK_PCT[symbol] - slippage_total
    
    Возвращает (pnl_rub, slippage_total).
    """
    tick = get_tick(symbol)
    tick_cost = get_tick_cost(symbol)
    lot = get_tick_lot(symbol)
    pct = get_tick_pct(symbol)
    pnl_ticks = (exit_price - entry_price) * direction / tick
    slippage_total = (slippage_in_ticks + slippage_out_ticks) * tick_cost
    pnl_rub = pnl_ticks * tick_cost * lot * pct - slippage_total
    return pnl_rub, slippage_total


# ═══════════════════════════════════════════════════════════════════════════
#  7. АДАПТИВНЫЙ СДВИГ ЛИМИТКИ (30% от ATR(14))
# ═══════════════════════════════════════════════════════════════════════════

def calc_slippage_ticks(symbol, df_5m=None):
    """Рассчитать адаптивный сдвиг лимитки на основе ATR(14) на 5м барах.
    
    ATR = mean(|close.diff|) за 14 баров.
    slippage = max(MIN, min(ATR_ticks * 0.3, MAX))
    
    Если df_5m не передан или ATR недоступен — использует FIXED_SLIPPAGE_TICKS.
    """
    if df_5m is not None and not df_5m.empty and len(df_5m) >= 14:
        close_diff = df_5m['close'].diff().abs()
        atr_5m = close_diff.rolling(14).mean().iloc[-1]
        tick = get_tick(symbol)
        atr_ticks = atr_5m / tick if tick > 0 else 0
        slippage = max(MIN_SLIPPAGE_TICKS, min(int(atr_ticks * 0.3), MAX_SLIPPAGE_TICKS))
        return slippage
    return FIXED_SLIPPAGE_TICKS


# ═══════════════════════════════════════════════════════════════════════════
#  8. ПОИСК 5М БАРА ПО ВРЕМЕНИ ВХОДА
# ═══════════════════════════════════════════════════════════════════════════

def find_5m_bar(bars_5m, entry_time_dt):
    """Найти индекс 5м бара, содержащего entry_time.
    
    Бар считается содержащим entry_time, если:
      bar_time - 5min <= entry_time < bar_time
    (label='right' в ресемпле)
    """
    et = pd.Timestamp(entry_time_dt)
    for i in range(len(bars_5m)):
        bar_time = bars_5m.iloc[i]['time']
        if isinstance(bar_time, pd.Timestamp):
            bar_start = bar_time - pd.Timedelta(minutes=5)
        else:
            bar_time = pd.Timestamp(bar_time)
            bar_start = bar_time - pd.Timedelta(minutes=5)
        if bar_start <= et < bar_time:
            return i
    return None


# ═══════════════════════════════════════════════════════════════════════════
#  9. ПОЛНАЯ СИМУЛЯЦИЯ СДЕЛКИ (touch-check + exit + pnl)
# ═══════════════════════════════════════════════════════════════════════════

def simulate_trade(signal_bar, next_bar, entry_price, direction, symbol,
                   slippage_in_ticks=SLIPPAGE_IN_TICKS,
                   slippage_out_ticks=SLIPPAGE_OUT_TICKS):
    """Полная симуляция сделки: проверка касания, выход по close, PnL.
    
    Параметры:
        signal_bar — сигнальный 5м бар (содержит high, low, close)
        next_bar — следующий 5м бар (для выхода)
        entry_price — цена лимитного ордера
        direction — 1 (long) или -1 (short)
    
    Возвращает dict:
        executed: True/False (коснулось ли)
        pnl_rub: PnL в рублях (0 если не исполнилось)
        exit_price: цена выхода
        slippage_total: slippage в рублях
        reason: строка с описанием
    """
    # Проверка касания
    bar_high = float(signal_bar.get('high', float(signal_bar['close'])))
    bar_low = float(signal_bar.get('low', float(signal_bar['close'])))
    
    touches = check_touch(bar_high, bar_low, entry_price, direction)
    
    if not touches:
        return {
            'executed': False,
            'pnl_rub': 0.0,
            'exit_price': None,
            'slippage_total': 0.0,
            'reason': (
                f"NO TOUCH: {symbol} {'LONG' if direction==1 else 'SHORT'} "
                f"entry={entry_price:.4f} bar(H={bar_high:.4f} L={bar_low:.4f})"
            ),
        }
    
    # Выход по close следующего бара
    if next_bar is not None:
        exit_price = float(next_bar['close'])
    else:
        exit_price = float(signal_bar['close'])
    
    pnl_rub, slippage_total = calc_pnl_rub(
        symbol, entry_price, exit_price, direction,
        slippage_in_ticks=slippage_in_ticks,
        slippage_out_ticks=slippage_out_ticks,
    )
    
    return {
        'executed': True,
        'pnl_rub': round(pnl_rub, 2),
        'exit_price': exit_price,
        'slippage_total': round(slippage_total, 2),
        'reason': (
            f"EXECUTED: {symbol} {'LONG' if direction==1 else 'SHORT'} "
            f"entry={entry_price:.4f} exit={exit_price:.4f} "
            f"pnl={pnl_rub:+.2f} touch=✅"
        ),
    }


# ═══════════════════════════════════════════════════════════════════════════
#  10. WALK-FORWARD РАЗБИВКА ДАТ
# ═══════════════════════════════════════════════════════════════════════════

def walk_forward_split(dates, ws_train=180, ws_test=60):
    """Генератор walk-forward окон. Возвращает (train_dates, test_dates) на каждой итерации."""
    i = ws_train
    while i < len(dates):
        te = min(i + ws_test, len(dates))
        train_dates = set(dates[i - ws_train:i])
        test_dates = set(dates[i:te])
        if len(test_dates) < 20:
            i = te
            continue
        yield train_dates, test_dates
        i = te
