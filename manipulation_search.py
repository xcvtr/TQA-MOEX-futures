#!/usr/bin/env python3
"""
Manipulation Search — поиск манипуляций против толпы на MOEX фьючерсах.

Анализирует 5-минутные свечи + OI (FIZ/YUR) и находит паттерны,
где крупный игрок (smart money) охотится за ликвидностью розничных трейдеров.

Типы сигналов (pattern['type']):
  OI_EXTREME    — z-score FIZ_net > порог + разворот цены против толпы
  OI_TRAP       — цена и FIZ движутся в одном направлении, затем разворот
  OI_DIVERGENCE — расхождение цены и OI (умные деньги выходят)
  FALSE_BREAK   — ложный пробой свингового уровня
  STOP_HUNT     — длинный фитиль через уровень + разворот
  VOL_CLIMAX    — аномальный объём + откат

Usage:
    python3 services/MOEX_LOADER/manipulation_search.py --symbol Si --days 60

Импорт:
    from services.MOEX_LOADER.manipulation_search import (
        load_price_data, load_oi_data, prepare_data,
        find_swing_points, detect_all, calc_atr
    )
"""

import sys, os
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME

import numpy as np
import pandas as pd
import psycopg2
import warnings
warnings.filterwarnings('ignore')

# ══════════════════════════════════════════════════════════════════════
#  ПАРАМЕТРЫ ДЕТЕКЦИИ
# ══════════════════════════════════════════════════════════════════════

SWING_WINDOW = 10          # свечей влево/вправо для свинга
BREAK_LOOKAHEAD = 8        # свечей для подтверждения пробоя
VOLUME_WINDOW = 50         # окно средней волатильности
VOLUME_THRESHOLD = 2.0     # порог объёмного климакса
WICK_BODY_RATIO = 2.0      # фитиль / тело для стоп-ханта
BREAK_EPSILON = 0.0005     # мин. пробой (0.05%)
OI_WINDOW = 12             # окно OI скользящего среднего
OI_CHANGE_PCT = 0.001      # мин. изменение OI (0.1%)
ZSCORE_THRESHOLD = 2.0     # порог z-score
OI_ROLLING_WINDOW = 576    # 2 дня по 288 свечей


# ══════════════════════════════════════════════════════════════════════
#  1. ЗАГРУЗКА ДАННЫХ
# ══════════════════════════════════════════════════════════════════════

def load_price_data(symbol: str, days: int) -> pd.DataFrame:
    """Загрузить 5m свечи из moex_prices_5m."""
    conn = psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
        user=DB_USER, password=DB_PASSWORD
    )
    since = datetime.now(timezone.utc) - timedelta(days=days)
    df = pd.read_sql("""
        SELECT time, open, high, low, close, volume
        FROM moex_prices_5m
        WHERE symbol = %s AND time >= %s
        ORDER BY time ASC
    """, conn, params=(symbol, since))
    conn.close()
    return df


def load_oi_data(symbol: str, days: int) -> pd.DataFrame:
    """
    Загрузить OI (FIZ и YUR) из openinterest_moex.
    Возвращает таблицу с time, fiz_buy, fiz_sell, yur_buy, yur_sell.
    """
    conn = psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
        user=DB_USER, password=DB_PASSWORD
    )
    since = datetime.now(timezone.utc) - timedelta(days=days)
    df = pd.read_sql("""
        SELECT time, clgroup, buy_orders, sell_orders
        FROM openinterest_moex
        WHERE symbol = %s AND time >= %s
        ORDER BY time ASC
    """, conn, params=(symbol, since))
    conn.close()

    if df.empty:
        return pd.DataFrame()

    # Развернуть clgroup в колонки fiz/yur
    df['side'] = df['clgroup'].map({0: 'fiz', 1: 'yur'})
    oi = df.pivot_table(
        index='time', columns='side',
        values=['buy_orders', 'sell_orders'],
        aggfunc='first'
    )
    short = {'buy_orders': 'buy', 'sell_orders': 'sell'}
    oi.columns = [f'{side}_{short[s]}' for s, side in oi.columns]
    oi = oi.reset_index().sort_values('time')
    oi['time'] = oi['time'].dt.floor('5min')
    oi = oi.drop_duplicates('time').sort_values('time')
    return oi


def prepare_data(df_prices: pd.DataFrame, df_oi: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """
    Объединить цены и OI по (symbol, time), добавить метрики.
    OI forward-fill (последнее известное значение).
    """
    if df_oi.empty:
        df = df_prices.copy()
        df['has_oi'] = False
        return df

    df_prices['symbol'] = symbol
    df_oi['symbol'] = symbol

    df = pd.merge_asof(
        df_prices.sort_values(['symbol', 'time']),
        df_oi.sort_values(['symbol', 'time']),
        on='time',
        by='symbol',
        direction='backward',
    )
    oi_max_time = df_oi['time'].max()
    df['has_oi'] = df['time'] <= oi_max_time

    for col in ['fiz_buy', 'fiz_sell', 'yur_buy', 'yur_sell']:
        df[col] = df[col].fillna(0.0)

    # OI метрики
    for prefix in ['fiz', 'yur']:
        df[f'{prefix}_net'] = df[f'{prefix}_buy'] - df[f'{prefix}_sell']
        df[f'{prefix}_net_sma'] = (
            df[f'{prefix}_net'].rolling(OI_WINDOW, min_periods=3).mean()
        )
        df[f'{prefix}_net_delta'] = df[f'{prefix}_net'].diff(periods=OI_WINDOW // 2)
        df[f'{prefix}_flow'] = df[f'{prefix}_net'].diff()  # 5-min поток

        # Rolling z-score потока (24h = 288 свечей)
        flow_mean = df[f'{prefix}_flow'].rolling(288, min_periods=50).mean()
        flow_std = df[f'{prefix}_flow'].rolling(288, min_periods=50).std()
        df[f'{prefix}_flow_zscore'] = ((df[f'{prefix}_flow'] - flow_mean) / flow_std.replace(0, np.nan)).fillna(0)

        # Rolling z-score (2 дня)
        series = df[f'{prefix}_net']
        mean = series.rolling(OI_ROLLING_WINDOW, min_periods=50).mean()
        std = series.rolling(OI_ROLLING_WINDOW, min_periods=50).std()
        df[f'{prefix}_zscore'] = ((series - mean) / std.replace(0, np.nan)).fillna(0)

    df['fiz_bias'] = np.sign(df['fiz_net'])
    return df


# ══════════════════════════════════════════════════════════════════════
#  2. СВИНГОВЫЕ УРОВНИ
# ══════════════════════════════════════════════════════════════════════

def find_swing_points(df: pd.DataFrame, window: int = SWING_WINDOW) -> pd.DataFrame:
    """Найти свинг-хаи и свинг-лои."""
    highs = df['high'].values
    lows = df['low'].values
    n = len(highs)
    swing_highs = np.full(n, np.nan)
    swing_lows = np.full(n, np.nan)

    for i in range(window, n - window):
        if highs[i] == max(highs[i - window:i + window + 1]):
            left_avg = np.mean(highs[i - window:i])
            right_avg = np.mean(highs[i + 1:i + window + 1])
            if highs[i] > max(left_avg, right_avg) * 1.001:
                swing_highs[i] = highs[i]

        if lows[i] == min(lows[i - window:i + window + 1]):
            left_avg = np.mean(lows[i - window:i])
            right_avg = np.mean(lows[i + 1:i + window + 1])
            if lows[i] < min(left_avg, right_avg) * 0.999:
                swing_lows[i] = lows[i]

    df = df.copy()
    df['swing_high'] = swing_highs
    df['swing_low'] = swing_lows
    return df


# ══════════════════════════════════════════════════════════════════════
#  3. ДЕТЕКЦИЯ ПАТТЕРНОВ
# ══════════════════════════════════════════════════════════════════════

def detect_false_breakouts(df: pd.DataFrame) -> list:
    """Ложные пробои свинговых уровней."""
    patterns = []
    highs = df['high'].values
    lows = df['low'].values
    closes = df['close'].values
    opens = df['open'].values
    times = df['time'].values
    n = len(highs)
    epsilon = BREAK_EPSILON
    if df['close'].max() < 10:
        epsilon = 0.002

    # — Бычьи капканы —
    swing_high_idxs = np.where(~np.isnan(df['swing_high'].values))[0]
    for idx in swing_high_idxs:
        sh = highs[idx]
        look_until = min(idx + BREAK_LOOKAHEAD * 2, n)
        peaked = False; peak = sh; j_break = None
        for j in range(idx + 1, look_until):
            peak = max(peak, highs[j])
            if not peaked:
                if highs[j] > sh * (1 + epsilon):
                    peaked = True; j_break = j
            else:
                if closes[j] < sh and closes[j] < opens[j]:
                    patterns.append({
                        'type': 'FALSE_BREAK', 'direction': 'BEAR',
                        'time': pd.Timestamp(times[j_break]),
                        'swing_idx': int(idx), 'swing_level': float(sh),
                        'rejection_pct': round((peak - sh) / sh * 100, 2),
                        'has_oi': bool(df.iloc[j_break]['has_oi']),
                    })
                    break

    # — Медвежьи капканы —
    swing_low_idxs = np.where(~np.isnan(df['swing_low'].values))[0]
    for idx in swing_low_idxs:
        sl = lows[idx]
        look_until = min(idx + BREAK_LOOKAHEAD * 2, n)
        peaked = False; trough = sl; j_break = None
        for j in range(idx + 1, look_until):
            trough = min(trough, lows[j])
            if not peaked:
                if lows[j] < sl * (1 - epsilon):
                    peaked = True; j_break = j
            else:
                if closes[j] > sl and closes[j] > opens[j]:
                    patterns.append({
                        'type': 'FALSE_BREAK', 'direction': 'BULL',
                        'time': pd.Timestamp(times[j_break]),
                        'swing_idx': int(idx), 'swing_level': float(sl),
                        'rejection_pct': round((sl - trough) / sl * 100, 2),
                        'has_oi': bool(df.iloc[j_break]['has_oi']),
                    })
                    break
    return patterns


def detect_stop_hunts(df: pd.DataFrame) -> list:
    """Охота за стопами."""
    patterns = []
    n = len(df)
    highs = df['high'].values; lows = df['low'].values
    opens = df['open'].values; closes = df['close'].values
    times = df['time'].values
    swing_highs = df['swing_high'].values; swing_lows = df['swing_low'].values

    for i in range(1, n - 3):
        body = abs(closes[i] - opens[i])
        if body == 0: continue
        upper_wick = highs[i] - max(opens[i], closes[i])
        lower_wick = min(opens[i], closes[i]) - lows[i]

        if upper_wick > body * WICK_BODY_RATIO and upper_wick > lower_wick:
            wick_r = upper_wick / (upper_wick + lower_wick) if (upper_wick + lower_wick) > 0 else 0
            if wick_r > 0.65 and _near_swing_high(i, highs, swing_highs) and _down_conf(closes, i):
                patterns.append({
                    'type': 'STOP_HUNT', 'direction': 'BEAR',
                    'time': pd.Timestamp(times[i]),
                    'swing_idx': int(i), 'wick_size': float(upper_wick),
                    'has_oi': bool(df.iloc[i]['has_oi']),
                })

        if lower_wick > body * WICK_BODY_RATIO and lower_wick > upper_wick:
            wick_r = lower_wick / (upper_wick + lower_wick) if (upper_wick + lower_wick) > 0 else 0
            if wick_r > 0.65 and _near_swing_low(i, lows, swing_lows) and _up_conf(closes, i):
                patterns.append({
                    'type': 'STOP_HUNT', 'direction': 'BULL',
                    'time': pd.Timestamp(times[i]),
                    'swing_idx': int(i), 'wick_size': float(lower_wick),
                    'has_oi': bool(df.iloc[i]['has_oi']),
                })
    return patterns


def _near_swing_high(i, highs, swing_highs, window=SWING_WINDOW):
    for o in range(-window, window + 1):
        si = i + o
        if 0 <= si < len(highs) and not np.isnan(swing_highs[si]):
            if abs(highs[i] - swing_highs[si]) / swing_highs[si] < 0.002:
                return True
    return False


def _near_swing_low(i, lows, swing_lows, window=SWING_WINDOW):
    for o in range(-window, window + 1):
        si = i + o
        if 0 <= si < len(lows) and not np.isnan(swing_lows[si]):
            if abs(lows[i] - swing_lows[si]) / swing_lows[si] < 0.002:
                return True
    return False


def _down_conf(closes, i):
    return i + 3 < len(closes) and closes[i + 1] < closes[i] and closes[i + 2] < closes[i + 1]


def _up_conf(closes, i):
    return i + 3 < len(closes) and closes[i + 1] > closes[i] and closes[i + 2] > closes[i + 1]


def detect_volume_climax(df: pd.DataFrame) -> list:
    """Объёмный климакс + откат."""
    patterns = []
    n = len(df)
    volumes = df['volume'].values.astype(float)
    closes = df['close'].values; opens = df['open'].values
    highs = df['high'].values; lows = df['low'].values
    times = df['time'].values
    vol_ma = pd.Series(volumes).rolling(VOLUME_WINDOW, min_periods=VOLUME_WINDOW // 2).mean().values

    for i in range(1, n - 3):
        if np.isnan(vol_ma[i]) or vol_ma[i] == 0: continue
        vol_r = volumes[i] / vol_ma[i]
        if vol_r < VOLUME_THRESHOLD: continue
        tr = highs[i] - lows[i]
        if tr == 0: continue
        rpct = tr / ((highs[i] + lows[i]) / 2)
        if rpct < 0.001: continue

        if closes[i] > opens[i]:
            if i + 3 < n and closes[i + 1] < closes[i] and closes[i + 2] < closes[i + 1]:
                patterns.append({
                    'type': 'VOL_CLIMAX', 'direction': 'BEAR',
                    'time': pd.Timestamp(times[i]), 'swing_idx': int(i),
                    'volume_ratio': round(float(vol_r), 1),
                    'range_pct': round(float(rpct * 100), 3),
                    'has_oi': bool(df.iloc[i]['has_oi']),
                })
        else:
            if i + 3 < n and closes[i + 1] > closes[i] and closes[i + 2] > closes[i + 1]:
                patterns.append({
                    'type': 'VOL_CLIMAX', 'direction': 'BULL',
                    'time': pd.Timestamp(times[i]), 'swing_idx': int(i),
                    'volume_ratio': round(float(vol_r), 1),
                    'range_pct': round(float(rpct * 100), 3),
                    'has_oi': bool(df.iloc[i]['has_oi']),
                })
    return patterns


def detect_oi_traps(df: pd.DataFrame) -> list:
    """
    OI-ловушки: цена и FIZ в одном направлении, затем разворот.
    """
    patterns = []
    if 'fiz_net' not in df.columns or 'fiz_net_delta' not in df.columns:
        return patterns

    n = len(df)
    closes = df['close'].values; times = df['time'].values
    fiz_net = df['fiz_net'].values; fiz_delta = df['fiz_net_delta'].values
    has_oi = df['has_oi'].values
    price_sma = pd.Series(closes).rolling(OI_WINDOW, min_periods=5).mean().values

    for i in range(OI_WINDOW, n - OI_WINDOW):
        if not has_oi[i] or np.isnan(fiz_delta[i]) or np.isnan(price_sma[i]):
            continue
        oi_pct = abs(fiz_delta[i] / (abs(fiz_net[i]) + 1))
        if oi_pct < OI_CHANGE_PCT:
            continue

        price_up = closes[i] > price_sma[i]
        price_down = closes[i] < price_sma[i]
        fiz_up = fiz_delta[i] > 0
        fiz_down = fiz_delta[i] < 0

        if price_up and fiz_up:
            for k in range(i + 1, min(i + OI_WINDOW, n)):
                if closes[k] < closes[i] * 0.998:
                    patterns.append({
                        'type': 'OI_TRAP', 'direction': 'BEAR',
                        'time': pd.Timestamp(times[i]), 'swing_idx': int(i),
                        'fiz_net': float(fiz_net[i]),
                        'fiz_delta': float(fiz_delta[i]),
                        'has_oi': True,
                    })
                    break
        elif price_down and fiz_down:
            for k in range(i + 1, min(i + OI_WINDOW, n)):
                if closes[k] > closes[i] * 1.002:
                    patterns.append({
                        'type': 'OI_TRAP', 'direction': 'BULL',
                        'time': pd.Timestamp(times[i]), 'swing_idx': int(i),
                        'fiz_net': float(fiz_net[i]),
                        'fiz_delta': float(fiz_delta[i]),
                        'has_oi': True,
                    })
                    break

        # OI-дивергенция
        diverged = False
        if price_up and fiz_down and abs(fiz_delta[i]) > abs(fiz_net[i]) * 0.02:
            diverged = True
            desc = 'Цена растёт, а толпа сокращает лонг — умные деньги выходят'
        elif price_down and fiz_up and abs(fiz_delta[i]) > abs(fiz_net[i]) * 0.02:
            diverged = True
            desc = 'Цена падает, а толпа набирает лонг — ловушка'
        if diverged:
            patterns.append({
                'type': 'OI_DIVERGENCE', 'direction': 'NEUTRAL',
                'time': pd.Timestamp(times[i]), 'swing_idx': int(i),
                'fiz_net': float(fiz_net[i]),
                'fiz_delta': float(fiz_delta[i]),
                'has_oi': True,
            })
    return patterns


def detect_oi_extreme(df: pd.DataFrame, zscore_threshold: float = ZSCORE_THRESHOLD) -> list:
    """
    OI-экстремумы: |z-score FIZ_net| > порог + разворот цены.
    """
    patterns = []
    if 'fiz_net' not in df.columns or 'fiz_zscore' not in df.columns:
        return patterns

    n = len(df)
    closes = df['close'].values; times = df['time'].values
    fiz_net = df['fiz_net'].values; fiz_zscore = df['fiz_zscore'].values
    has_oi = df['has_oi'].values
    last_time = None

    for i in range(OI_WINDOW, n - OI_WINDOW):
        if not has_oi[i] or np.isnan(fiz_zscore[i]) or np.isnan(fiz_net[i]):
            continue
        if abs(fiz_zscore[i]) < zscore_threshold:
            continue
        # Группировка соседних экстремумов
        if last_time is not None:
            diff = pd.Timestamp(times[i]) - last_time
            if diff.total_seconds() < 300 * OI_WINDOW:
                continue
        last_time = pd.Timestamp(times[i])

        if fiz_zscore[i] > zscore_threshold:
            for k in range(i + 1, min(i + OI_WINDOW, n)):
                if closes[k] < closes[i] * 0.998:
                    patterns.append({
                        'type': 'OI_EXTREME', 'direction': 'BEAR',
                        'time': pd.Timestamp(times[i]), 'swing_idx': int(i),
                        'fiz_net': float(fiz_net[i]),
                        'fiz_zscore': float(fiz_zscore[i]),
                        'has_oi': True,
                    })
                    break
        elif fiz_zscore[i] < -zscore_threshold:
            for k in range(i + 1, min(i + OI_WINDOW, n)):
                if closes[k] > closes[i] * 1.002:
                    patterns.append({
                        'type': 'OI_EXTREME', 'direction': 'BULL',
                        'time': pd.Timestamp(times[i]), 'swing_idx': int(i),
                        'fiz_net': float(fiz_net[i]),
                        'fiz_zscore': float(fiz_zscore[i]),
                        'has_oi': True,
                    })
                    break
    return patterns


def detect_flow_extreme(df: pd.DataFrame, zscore_threshold: float = ZSCORE_THRESHOLD) -> list:
    """
    Экстремальный поток толпы: |fiz_flow_zscore| > порог + разворот цены.

    fiz_flow_zscore > +2  → толпа аномально много купила за 5 мин → разворот вниз
    fiz_flow_zscore < -2  → толпа аномально много продала за 5 мин → разворот вверх
    """
    patterns = []
    if 'fiz_flow_zscore' not in df.columns or 'fiz_flow' not in df.columns:
        return patterns

    n = len(df)
    closes = df['close'].values; times = df['time'].values
    fiz_net = df['fiz_net'].values; fiz_flow = df['fiz_flow'].values
    fiz_flow_z = df['fiz_flow_zscore'].values
    has_oi = df['has_oi'].values
    last_time = None

    for i in range(OI_WINDOW, n - OI_WINDOW):
        if not has_oi[i] or np.isnan(fiz_flow_z[i]) or np.isnan(fiz_flow[i]):
            continue
        if abs(fiz_flow_z[i]) < zscore_threshold:
            continue
        # Группировка соседних экстремумов
        if last_time is not None:
            diff = pd.Timestamp(times[i]) - last_time
            if diff.total_seconds() < 300 * OI_WINDOW:
                continue
        last_time = pd.Timestamp(times[i])

        if fiz_flow_z[i] > zscore_threshold:
            # Толпа аномально много купила → ждём разворота вниз
            for k in range(i + 1, min(i + OI_WINDOW, n)):
                if closes[k] < closes[i] * 0.998:
                    patterns.append({
                        'type': 'FLOW_EXTREME', 'direction': 'BEAR',
                        'time': pd.Timestamp(times[i]), 'swing_idx': int(i),
                        'fiz_net': float(fiz_net[i]),
                        'fiz_flow': float(fiz_flow[i]),
                        'fiz_flow_zscore': round(float(fiz_flow_z[i]), 2),
                        'has_oi': True,
                    })
                    break
        elif fiz_flow_z[i] < -zscore_threshold:
            # Толпа аномально много продала → ждём разворота вверх
            for k in range(i + 1, min(i + OI_WINDOW, n)):
                if closes[k] > closes[i] * 1.002:
                    patterns.append({
                        'type': 'FLOW_EXTREME', 'direction': 'BULL',
                        'time': pd.Timestamp(times[i]), 'swing_idx': int(i),
                        'fiz_net': float(fiz_net[i]),
                        'fiz_flow': float(fiz_flow[i]),
                        'fiz_flow_zscore': round(float(fiz_flow_z[i]), 2),
                        'has_oi': True,
                    })
                    break
    return patterns


def detect_flow_divergence(df: pd.DataFrame, zscore_threshold: float = 1.5) -> list:
    """
    Дивергенция потоков физлиц и юрлиц.

    Когда физлица резко наращивают buy, а юрлица sell (или наоборот).
    Это сигнал, что толпа и умные деньги движутся в разные стороны.

    Условия:
    - fiz_flow > 0 и yur_flow < 0 (или наоборот)
    - |fiz_flow_zscore| > 1.5 или |yur_flow_zscore| > 1.5
    """
    patterns = []
    if not all(c in df.columns for c in
               ['fiz_flow', 'yur_flow', 'fiz_flow_zscore', 'yur_flow_zscore',
                'fiz_buy', 'fiz_sell', 'yur_buy', 'yur_sell']):
        return patterns

    n = len(df)
    closes = df['close'].values; times = df['time'].values
    fiz_flow = df['fiz_flow'].values; yur_flow = df['yur_flow'].values
    fiz_flow_z = df['fiz_flow_zscore'].values; yur_flow_z = df['yur_flow_zscore'].values
    fiz_buy = df['fiz_buy'].values; fiz_sell = df['fiz_sell'].values
    yur_buy = df['yur_buy'].values; yur_sell = df['yur_sell'].values
    has_oi = df['has_oi'].values
    last_time = None

    for i in range(OI_WINDOW, n - OI_WINDOW):
        if not has_oi[i]:
            continue
        if np.isnan(fiz_flow[i]) or np.isnan(yur_flow[i]):
            continue
        if np.isnan(fiz_flow_z[i]) or np.isnan(yur_flow_z[i]):
            continue

        # Физлица покупают, юрлица продают
        fiz_bull = fiz_flow[i] > 0 and fiz_flow_z[i] > zscore_threshold
        yur_bear = yur_flow[i] < 0 and abs(yur_flow_z[i]) > zscore_threshold
        # Физлица продают, юрлица покупают
        fiz_bear = fiz_flow[i] < 0 and abs(fiz_flow_z[i]) > zscore_threshold
        yur_bull = yur_flow[i] > 0 and yur_flow_z[i] > zscore_threshold

        diverged = (fiz_bull and yur_bear) or (fiz_bear and yur_bull)
        if not diverged:
            continue

        # Группировка соседних (не чаще чем OI_WINDOW * 5 мин)
        if last_time is not None:
            diff = pd.Timestamp(times[i]) - last_time
            if diff.total_seconds() < 300 * OI_WINDOW:
                continue
        last_time = pd.Timestamp(times[i])

        direction = 'BEAR' if (fiz_bull and yur_bear) else 'BULL'
        patterns.append({
            'type': 'FLOW_DIVERGENCE',
            'direction': direction,
            'time': pd.Timestamp(times[i]),
            'swing_idx': int(i),
            'fiz_flow': float(fiz_flow[i]),
            'yur_flow': float(yur_flow[i]),
            'fiz_flow_zscore': round(float(fiz_flow_z[i]), 2),
            'yur_flow_zscore': round(float(yur_flow_z[i]), 2),
            'has_oi': True,
        })
    return patterns



def add_forward_returns(patterns: list, df: pd.DataFrame) -> list:
    """
    Для каждого паттерна рассчитать forward return на горизонтах 1-6 часов.

    Добавляет поля fwd_ret_1h, fwd_ret_2h, ..., fwd_ret_6h (%).
    """
    closes = df['close'].values
    n = len(closes)
    # 5-min бары → часы: 1h=12, 2h=24, 3h=36, 4h=48, 5h=60, 6h=72
    horizons = {'1h': 12, '2h': 24, '3h': 36, '4h': 48, '5h': 60, '6h': 72}

    for p in patterns:
        idx = p['swing_idx']
        entry_price = float(closes[idx])
        p['entry_price'] = entry_price

        for label, bars in horizons.items():
            fwd_idx = idx + bars
            if fwd_idx < n:
                ret = (closes[fwd_idx] - entry_price) / entry_price * 100
                p[f'fwd_ret_{label}'] = round(float(ret), 2)
            else:
                p[f'fwd_ret_{label}'] = None

        # Успех: цена пошла против толпы
        direction = p['direction']
        if direction == 'BEAR':  # толпа купила → ждём падения
            p['success'] = any(
                p.get(f'fwd_ret_{label}', 0) is not None and p[f'fwd_ret_{label}'] < -0.3
                for label in horizons
            )
        elif direction == 'BULL':  # толпа продала → ждём роста
            p['success'] = any(
                p.get(f'fwd_ret_{label}', 0) is not None and p[f'fwd_ret_{label}'] > 0.3
                for label in horizons
            )
        else:
            p['success'] = None

    return patterns


# ══════════════════════════════════════════════════════════════════════
#  4. ATR + ОБЩАЯ ДЕТЕКЦИЯ
# ══════════════════════════════════════════════════════════════════════

def calc_atr(df: pd.DataFrame, period: int = 14) -> np.ndarray:
    """Average True Range."""
    high, low, close = df['high'].values, df['low'].values, df['close'].values
    tr = np.full(len(df), np.nan)
    for i in range(1, len(df)):
        tr[i] = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
    return pd.Series(tr).rolling(period, min_periods=period).mean().values


def detect_all(df: pd.DataFrame, zscore_threshold: float = ZSCORE_THRESHOLD,
               use_oi: bool = True) -> list:
    """
    Запустить все детекторы. Вернуть список паттернов.

    Args:
        df: DataFrame с ценами, OI, свинговыми уровнями
        zscore_threshold: порог z-score для OI_EXTREME
        use_oi: если False — только ценовые паттерны

    Returns:
        list[dict] — паттерны, отсортированные по времени
    """
    df = find_swing_points(df)
    patterns = []
    patterns.extend(detect_false_breakouts(df))
    patterns.extend(detect_stop_hunts(df))
    patterns.extend(detect_volume_climax(df))

    if use_oi and 'fiz_net' in df.columns and df['has_oi'].any():
        patterns.extend(detect_oi_traps(df))
        patterns.extend(detect_oi_extreme(df, zscore_threshold))
        patterns.extend(detect_flow_extreme(df, zscore_threshold))
        patterns.extend(detect_flow_divergence(df, zscore_threshold))

    # Forward return verification (для OI-паттернов)
    patterns = add_forward_returns(patterns, df)

    # ATR-фильтр (только ценовые паттерны)
    atr = calc_atr(df)
    filtered = []
    for p in patterns:
        if p['type'] in ('OI_TRAP', 'OI_DIVERGENCE', 'OI_EXTREME', 'FLOW_EXTREME', 'FLOW_DIVERGENCE'):
            filtered.append(p)
            continue
        idx = p['swing_idx']
        if idx >= len(atr) or np.isnan(atr[idx]):
            continue
        price = float(df.iloc[idx]['close'])
        atr_pct = atr[idx] / price * 100
        if p['type'] == 'FALSE_BREAK':
            r = abs(p.get('rejection_pct', 0))
            if r >= max(0.05, atr_pct * 0.3):
                filtered.append(p)
        elif p['type'] == 'STOP_HUNT':
            w = p.get('wick_size', 0)
            if w / price * 100 >= max(0.05, atr_pct * 0.5):
                filtered.append(p)
        elif p['type'] == 'VOL_CLIMAX':
            r = p.get('range_pct', 0)
            if r >= max(0.05, atr_pct * 0.4):
                filtered.append(p)

    # Дедупликация
    seen = set()
    result = []
    for p in sorted(filtered, key=lambda x: x['time']):
        key = (p['type'], str(p['time']))
        if key not in seen:
            seen.add(key)
            result.append(p)

    return result


# ══════════════════════════════════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════════════════════════════════

def resolve_symbol(sym: str) -> str:
    """Найти правильный регистр тикера в БД."""
    try:
        conn = psycopg2.connect(
            host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
            user=DB_USER, password=DB_PASSWORD
        )
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT symbol FROM moex_prices_5m")
        symbols = [r[0] for r in cur.fetchall()]
        conn.close()
        for s in symbols:
            if s.upper() == sym.upper():
                return s
        return sym
    except Exception:
        return sym


def oi_summary(df: pd.DataFrame) -> dict:
    """Сводка по OI позициям на последнюю дату."""
    if 'fiz_net' not in df.columns or not df['has_oi'].any():
        return {}
    last = df[df['has_oi']].iloc[-1]
    fiz_net = last['fiz_net']
    total_oi = last['fiz_buy'] + last['fiz_sell']
    return {
        'time': str(last['time']),
        'fiz_buy': int(last['fiz_buy']),
        'fiz_sell': int(last['fiz_sell']),
        'fiz_net': int(fiz_net),
        'fiz_bias': 'LONG' if fiz_net > 0 else 'SHORT',
        'fiz_long_pct': round(last['fiz_buy'] / total_oi * 100, 1) if total_oi else 0,
        'yur_net': int(last['yur_buy'] - last['yur_sell']),
    }


def print_report(symbol: str, df: pd.DataFrame, patterns: list, oi_info: dict):
    """Вывести отчёт в консоль."""
    print(f"\n{'=' * 70}")
    print(f"  MOEX Manipulation Scan — {symbol}")
    print(f"  {len(df)} свечей ({df['time'].min():%Y-%m-%d} — {df['time'].max():%Y-%m-%d})")
    if oi_info:
        print(f"  OI FIZ: {oi_info['fiz_long_pct']}% long  "
              f"net={oi_info['fiz_net']:+,d} ({oi_info['fiz_bias']})")
        print(f"  OI YUR: net={oi_info['yur_net']:+,d}")
    print(f"{'=' * 70}")

    if not patterns:
        print("  Паттернов не обнаружено.")
        return

    print(f"\n  НАЙДЕНО {len(patterns)} ПАТТЕРНОВ:")
    for p_type in ['FLOW_DIVERGENCE', 'FLOW_EXTREME', 'OI_EXTREME', 'OI_TRAP', 'OI_DIVERGENCE', 'FALSE_BREAK', 'STOP_HUNT', 'VOL_CLIMAX']:
        subset = [p for p in patterns if p['type'] == p_type]
        if not subset:
            continue
        labels = {
            'FLOW_DIVERGENCE': 'Дивергенция потоков', 'FLOW_EXTREME': 'Экстр. поток',
            'OI_EXTREME': 'OI-экстремумы', 'OI_TRAP': 'OI-ловушки',
            'OI_DIVERGENCE': 'OI-дивергенция', 'FALSE_BREAK': 'Ложные пробои',
            'STOP_HUNT': 'Стоп-ханты', 'VOL_CLIMAX': 'Объёмные климаксы',
        }
        print(f"    {labels[p_type]}: {len(subset)}")
        for p in subset[:5]:  # первые 5
            t = pd.Timestamp(p['time']).strftime('%m-%d %H:%M')
            extra = ''
            if 'fiz_zscore' in p:
                extra = f" z={p['fiz_zscore']:.1f}"
            elif 'fiz_flow_zscore' in p:
                extra = f" flow_z={p['fiz_flow_zscore']:+.1f}"
            if 'fiz_delta' in p and p.get('fiz_delta', 0) != 0:
                extra = f" Δ={p['fiz_delta']:+.0f}"
            if 'rejection_pct' in p:
                extra = f" {p['rejection_pct']:+.2f}%"
            print(f"      [{t}] {p['direction']:>6}{extra}")
        if len(subset) > 5:
            print(f"      ... и ещё {len(subset) - 5}")

    # Forward return verification (FLOW_EXTREME + OI_EXTREME)
    verif = [p for p in patterns if p['type'] in ('FLOW_EXTREME', 'OI_EXTREME') and 'fwd_ret_1h' in p]
    if verif:
        successes = sum(1 for p in verif if p.get('success'))
        avg_ret_1h = sum(p.get('fwd_ret_1h', 0) or 0 for p in verif) / len(verif)
        avg_ret_3h = sum(p.get('fwd_ret_3h', 0) or 0 for p in verif if p.get('fwd_ret_3h') is not None) / max(1, sum(1 for p in verif if p.get('fwd_ret_3h') is not None))
        avg_ret_6h = sum(p.get('fwd_ret_6h', 0) or 0 for p in verif if p.get('fwd_ret_6h') is not None) / max(1, sum(1 for p in verif if p.get('fwd_ret_6h') is not None))
        print(f"\n  ── Верификация (n={len(verif)}) ──")
        print(f"    Успех: {successes}/{len(verif)} ({successes/len(verif)*100:.0f}%)")
        print(f"    Avg fwd return:  1h={avg_ret_1h:+.2f}%  3h={avg_ret_3h:+.2f}%  6h={avg_ret_6h:+.2f}%")

    bulls = sum(1 for p in patterns if p['direction'] == 'BULL')
    bears = sum(1 for p in patterns if p['direction'] == 'BEAR')
    print(f"  BULL: {bulls}  BEAR: {bears}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description='MOEX Manipulation Scanner')
    parser.add_argument('--symbol', default='Si', help='Тикер фьючерса')
    parser.add_argument('--days', type=int, default=30, help='Глубина в днях')
    parser.add_argument('--zscore', type=float, default=ZSCORE_THRESHOLD,
                        help=f'Порог z-score (по умолч. {ZSCORE_THRESHOLD})')
    parser.add_argument('--no-oi', action='store_true', help='Без OI данных')
    args = parser.parse_args()

    symbol = resolve_symbol(args.symbol.strip())

    df_prices = load_price_data(symbol, args.days)
    if df_prices.empty:
        print(f"Нет данных для {symbol}")
        sys.exit(1)

    df_oi = pd.DataFrame()
    oi_info = {}
    if not args.no_oi:
        df_oi = load_oi_data(symbol, args.days)
    df = prepare_data(df_prices, df_oi, symbol)
    oi_info = oi_summary(df)

    patterns = detect_all(df, args.zscore, use_oi=not args.no_oi and not df_oi.empty)
    print_report(symbol, df, patterns, oi_info)


if __name__ == '__main__':
    main()
