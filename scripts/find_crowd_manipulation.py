#!/home/user/venvs/tqa/main/bin/python
"""
Поиск манипуляций против толпы на MOEX фьючерсах.

Использует OI данные MOEX, разделённые на:
  - FIZ (clgroup=0) — физические лица, розница, "толпа"
  - YUR (clgroup=1) — юридические лица, киты, smart money

Паттерны обнаружения:

  1. FALSE_BREAK  — ложный пробой свингового уровня (ценовой)
  2. STOP_HUNT    — охота за стопами: выброс с длинным фитилём + разворот
  3. VOL_CLIMAX   — объёмный климакс + откат
  4. OI_TRAP      — OI-дивергенция: разница между движением цены и
                    изменением позиции толпы

Usage:
  # Базовый запуск по Si (USDRUB)
  python scripts/find_crowd_manipulation.py --symbol Si

  # Полный контроль параметров
  python scripts/find_crowd_manipulation.py --symbol BR --days 90 --swing-window 12

  # Только OI-паттерны (без ценовых)
  python scripts/find_crowd_manipulation.py --symbol Si --filters oi_trap

  # Сохранить данные + без графика
  python scripts/find_crowd_manipulation.py --symbol GD --days 14 --csv ./gd.csv --no-plot
"""

import argparse, sys, os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2
import warnings
warnings.filterwarnings('ignore')

# ──────────────────────────────────────────────────────────────────────
#  Конфигурация
# ──────────────────────────────────────────────────────────────────────
DB_HOST = os.getenv("MOEX_DB_HOST", "10.0.0.64")
DB_PORT = int(os.getenv("MOEX_DB_PORT", "5432"))
DB_NAME = os.getenv("MOEX_DB_NAME", "moex")
DB_USER = os.getenv("MOEX_DB_USER", "postgres")
DB_PASS = os.getenv("MOEX_DB_PASSWORD", "")

OUTPUT_DIR = '/home/user/.hermes/cache/screenshots/tqa/'
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Параметры детекции (умолчания)
DEFAULT_SWING_WINDOW = 10
DEFAULT_BREAK_LOOKAHEAD = 8
DEFAULT_VOLUME_WINDOW = 50
DEFAULT_VOLUME_THRESHOLD = 2.0
DEFAULT_WICK_BODY_RATIO = 2.0
DEFAULT_BREAK_EPSILON = 0.0005
DEFAULT_OI_WINDOW = 12        # окно для OI скользящего среднего
DEFAULT_OI_CHANGE_PCT = 0.001  # мин. изменение OI (доля от позиции, 0.001 = 0.1%)
DEFAULT_ZSCORE = 2.0           # порог z-score для экстремальной позиции толпы


# ══════════════════════════════════════════════════════════════════════
#  1. ЗАГРУЗКА ДАННЫХ
# ══════════════════════════════════════════════════════════════════════

def load_price_data(symbol: str, days: int) -> pd.DataFrame:
    """Загрузить 5m свечи."""
    conn = psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
        user=DB_USER, password=DB_PASS
    )
    since = datetime.now(timezone.utc) - timedelta(days=days)
    df = pd.read_sql("""
        SELECT time, open, high, low, close, volume
        FROM moex_prices_5m
        WHERE symbol = %s AND time >= %s
        ORDER BY time ASC
    """, conn, params=(symbol, since))
    conn.close()

    if df.empty:
        print(f"Нет данных для {symbol} за последние {days} дн.")
        sys.exit(1)

    print(f"  Цены: {len(df)} свечей ({df['time'].min():%Y-%m-%d} — {df['time'].max():%Y-%m-%d})")
    return df


def load_oi_data(symbol: str, days: int) -> pd.DataFrame:
    """
    Загрузить 5m OI из moex_prices_5m_oi (пред-агрегировано).
    Returns: time, fiz_buy, fiz_sell, yur_buy, yur_sell, fiz_net, yur_net, crowd_ratio
    """
    conn = psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
        user=DB_USER, password=DB_PASS
    )
    since = datetime.now(timezone.utc) - timedelta(days=days)
    df = pd.read_sql("""
        SELECT time, fiz_buy, fiz_sell, yur_buy, yur_sell, total_oi
        FROM moex_prices_5m_oi
        WHERE symbol = %s AND time >= %s
        ORDER BY time ASC
    """, conn, params=(symbol, since))
    conn.close()

    if df.empty:
        return pd.DataFrame()

    # Производные поля
    df['fiz_net'] = df['fiz_buy'] - df['fiz_sell']
    df['yur_net'] = df['yur_buy'] - df['yur_sell']
    df['crowd_ratio'] = df['fiz_net'] / df['total_oi'].clip(lower=1)

    print(f"  OI 5m: {len(df)} записей "
          f"({df['time'].min():%Y-%m-%d} — {df['time'].max():%Y-%m-%d})")
    return df


def prepare_data(df_prices: pd.DataFrame, df_oi: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """
    Объединить цены и OI, добавить метрики.
    OI данные forward-fill там, где нет новой записи.
    """
    if df_oi.empty:
        df = df_prices.copy()
        df['has_oi'] = False
        return df

    # Merge: forward-fill OI (последнее известное значение) по (symbol, time)
    df_prices['symbol'] = symbol
    df_oi['symbol'] = symbol
    df = pd.merge_asof(
        df_prices.sort_values(['symbol', 'time']),
        df_oi.sort_values(['symbol', 'time']),
        on='time',
        by='symbol',
        direction='backward',
    )
    # has_oi = True только для дат, когда OI был доступен
    oi_max_time = df_oi['time'].max()
    df['has_oi'] = df['time'] <= oi_max_time
    # Заполнить NaN (периоды без OI) нулями
    for col in ['fiz_buy', 'fiz_sell', 'yur_buy', 'yur_sell']:
        df[col] = df[col].fillna(0.0)

    # OI метрики
    for prefix in ['fiz', 'yur']:
        df[f'{prefix}_net'] = df[f'{prefix}_buy'] - df[f'{prefix}_sell']
        # Сглаженное изменение OI
        df[f'{prefix}_net_sma'] = (
            df[f'{prefix}_net'].rolling(DEFAULT_OI_WINDOW, min_periods=3).mean()
        )
        df[f'{prefix}_net_delta'] = df[f'{prefix}_net'].diff(periods=DEFAULT_OI_WINDOW // 2)
        df[f'{prefix}_flow'] = df[f'{prefix}_net'].diff()  # 5-min поток

        # Rolling z-score потока (24h = 288 свечей)
        flow_mean = df[f'{prefix}_flow'].rolling(288, min_periods=50).mean()
        flow_std = df[f'{prefix}_flow'].rolling(288, min_periods=50).std()
        df[f'{prefix}_flow_zscore'] = ((df[f'{prefix}_flow'] - flow_mean) / flow_std.replace(0, np.nan)).fillna(0)

    # OI z-score: насколько позиция толпы экстремальна относительно недавней истории
    # rolling окно — ~2 дня данных (5min * 288 = 1440 свечей за день)
    OI_ROLLING_WINDOW = 288 * 2  # 2 дня по 288 свечей
    for prefix in ['fiz', 'yur']:
        series = df[f'{prefix}_net']
        mean = series.rolling(OI_ROLLING_WINDOW, min_periods=50).mean()
        std = series.rolling(OI_ROLLING_WINDOW, min_periods=50).std()
        df[f'{prefix}_zscore'] = ((series - mean) / std.replace(0, np.nan)).fillna(0)

    # OI bias: знак позиции толпы
    df['fiz_bias'] = np.sign(df['fiz_net'])

    # OI дивергенция: цена против позиции толпы
    # fwd_close = цена через N свечей
    df['fwd_return'] = df['close'].pct_change(periods=DEFAULT_OI_WINDOW).shift(-DEFAULT_OI_WINDOW)

    return df


# ══════════════════════════════════════════════════════════════════════
#  2. СВИНГОВЫЕ УРОВНИ
# ══════════════════════════════════════════════════════════════════════

def find_swing_points(df: pd.DataFrame, window: int):
    """Найти значимые свинг-хаи и свинг-лои."""
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
    n_highs = int(np.sum(~np.isnan(swing_highs)))
    n_lows = int(np.sum(~np.isnan(swing_lows)))
    print(f"  Свинг-хаев: {n_highs}, свинг-лоёв: {n_lows}")
    return df


# ══════════════════════════════════════════════════════════════════════
#  3. ДЕТЕКЦИЯ ПАТТЕРНОВ
# ══════════════════════════════════════════════════════════════════════

def detect_false_breakouts(df: pd.DataFrame, lookahead: int, epsilon: float):
    """
    Ложные пробои (False Breakout / BOS Failure).
    """
    patterns = []
    highs = df['high'].values
    lows = df['low'].values
    closes = df['close'].values
    opens = df['open'].values
    times = df['time'].values
    n = len(highs)

    # — Бычьи капканы: пробой вверх → закрытие ниже —
    swing_high_idxs = np.where(~np.isnan(df['swing_high'].values))[0]
    for idx in swing_high_idxs:
        sh = highs[idx]
        look_until = min(idx + lookahead * 2, n)
        breakout_found = False
        breakout_j = None
        peak = sh

        for j in range(idx + 1, look_until):
            peak = max(peak, highs[j])
            if not breakout_found:
                if highs[j] > sh * (1 + epsilon):
                    breakout_found = True
                    breakout_j = j
            else:
                if closes[j] < sh and closes[j] < opens[j]:
                    rejection = (peak - sh) / sh
                    patterns.append({
                        'type': 'FALSE_BREAK', 'direction': 'BEAR',
                        'description': 'Ложный пробой хая — бычий капкан',
                        'swing_idx': int(idx), 'break_idx': int(breakout_j),
                        'confirm_idx': int(j),
                        'swing_level': float(sh), 'break_level': float(highs[breakout_j]),
                        'peak_level': float(peak),
                        'rejection_pct': round(rejection * 100, 2),
                        'time': pd.Timestamp(times[breakout_j]),
                        'has_oi': bool(df.iloc[breakout_j]['has_oi']),
                    })
                    break

    # — Медвежьи капканы: пробой вниз → закрытие выше —
    swing_low_idxs = np.where(~np.isnan(df['swing_low'].values))[0]
    for idx in swing_low_idxs:
        sl = lows[idx]
        look_until = min(idx + lookahead * 2, n)
        breakout_found = False
        breakout_j = None
        trough = sl

        for j in range(idx + 1, look_until):
            trough = min(trough, lows[j])
            if not breakout_found:
                if lows[j] < sl * (1 - epsilon):
                    breakout_found = True
                    breakout_j = j
            else:
                if closes[j] > sl and closes[j] > opens[j]:
                    rejection = (sl - trough) / sl
                    patterns.append({
                        'type': 'FALSE_BREAK', 'direction': 'BULL',
                        'description': 'Ложный пробой лоя — медвежий капкан',
                        'swing_idx': int(idx), 'break_idx': int(breakout_j),
                        'confirm_idx': int(j),
                        'swing_level': float(sl), 'break_level': float(lows[breakout_j]),
                        'trough_level': float(trough),
                        'rejection_pct': round(rejection * 100, 2),
                        'time': pd.Timestamp(times[breakout_j]),
                        'has_oi': bool(df.iloc[breakout_j]['has_oi']),
                    })
                    break

    # Обогатить OI-контекстом (если доступен)
    for p in patterns:
        idx = p['swing_idx']
        if idx < len(df) and 'fiz_net' in df.columns:
            p['fiz_net'] = float(df.iloc[max(0, idx - 1)]['fiz_net'])
            p['fiz_delta'] = float(df.iloc[idx]['fiz_net_delta']) if 'fiz_net_delta' in df.columns else 0
        else:
            p['fiz_net'] = 0
            p['fiz_delta'] = 0

    return patterns


def detect_stop_hunts(df: pd.DataFrame, wick_body_ratio: float, swing_window: int):
    """
    Охота за стопами (Stop Hunt / Liquidity Grab).
    """
    patterns = []
    n = len(df)
    highs = df['high'].values
    lows = df['low'].values
    opens = df['open'].values
    closes = df['close'].values
    times = df['time'].values
    swing_highs = df['swing_high'].values
    swing_lows = df['swing_low'].values

    for i in range(1, n - 3):
        body = abs(closes[i] - opens[i])
        if body == 0:
            continue

        upper_wick = highs[i] - max(opens[i], closes[i])
        lower_wick = min(opens[i], closes[i]) - lows[i]

        # — Верхний фитиль (охота стопов лонгов) —
        if upper_wick > body * wick_body_ratio and upper_wick > lower_wick:
            wick_ratio = upper_wick / (upper_wick + lower_wick) if (upper_wick + lower_wick) > 0 else 0
            if wick_ratio > 0.65:
                near_swing = _near_swing_high(i, highs, swing_highs, swing_window)
                if near_swing and _down_confirmation(closes, i):
                    patterns.append({
                        'type': 'STOP_HUNT', 'direction': 'BEAR',
                        'description': 'Охота стопов лонгов',
                        'swing_idx': int(i), 'break_idx': int(i),
                        'confirm_idx': int(i + 2),
                        'swing_level': float(highs[i]),
                        'wick_size': float(upper_wick),
                        'wick_ratio': round(wick_ratio, 2),
                        'time': pd.Timestamp(times[i]),
                        'has_oi': bool(df.iloc[i]['has_oi']),
                    })

        # — Нижний фитиль (охота стопов шортов) —
        if lower_wick > body * wick_body_ratio and lower_wick > upper_wick:
            wick_ratio = lower_wick / (upper_wick + lower_wick) if (upper_wick + lower_wick) > 0 else 0
            if wick_ratio > 0.65:
                near_swing = _near_swing_low(i, lows, swing_lows, swing_window)
                if near_swing and _up_confirmation(closes, i):
                    patterns.append({
                        'type': 'STOP_HUNT', 'direction': 'BULL',
                        'description': 'Охота стопов шортов',
                        'swing_idx': int(i), 'break_idx': int(i),
                        'confirm_idx': int(i + 2),
                        'swing_level': float(lows[i]),
                        'wick_size': float(lower_wick),
                        'wick_ratio': round(wick_ratio, 2),
                        'time': pd.Timestamp(times[i]),
                        'has_oi': bool(df.iloc[i]['has_oi']),
                    })

    # OI-контекст
    for p in patterns:
        idx = p['swing_idx']
        if idx < len(df) and 'fiz_net' in df.columns:
            p['fiz_net'] = float(df.iloc[max(0, idx - 1)]['fiz_net'])
            p['fiz_delta'] = float(df.iloc[idx]['fiz_net_delta']) if 'fiz_net_delta' in df.columns else 0
        else:
            p['fiz_net'] = 0
            p['fiz_delta'] = 0

    return patterns


def _near_swing_high(i, highs, swing_highs, swing_window):
    for offset in range(-swing_window, swing_window + 1):
        si = i + offset
        if 0 <= si < len(highs) and not np.isnan(swing_highs[si]):
            dist = abs(highs[i] - swing_highs[si]) / swing_highs[si]
            if dist < 0.002:
                return True
    return False


def _near_swing_low(i, lows, swing_lows, swing_window):
    for offset in range(-swing_window, swing_window + 1):
        si = i + offset
        if 0 <= si < len(lows) and not np.isnan(swing_lows[si]):
            dist = abs(lows[i] - swing_lows[si]) / swing_lows[si]
            if dist < 0.002:
                return True
    return False


def _down_confirmation(closes, i):
    return i + 3 < len(closes) and closes[i + 1] < closes[i] and closes[i + 2] < closes[i + 1]


def _up_confirmation(closes, i):
    return i + 3 < len(closes) and closes[i + 1] > closes[i] and closes[i + 2] > closes[i + 1]


def detect_volume_climax(df: pd.DataFrame, vol_window: int, vol_threshold: float):
    """
    Объёмный климакс + откат (Volume Climax Reversal).
    """
    patterns = []
    n = len(df)
    volumes = df['volume'].values.astype(float)
    closes = df['close'].values
    opens = df['open'].values
    highs = df['high'].values
    lows = df['low'].values
    times = df['time'].values

    vol_ma = pd.Series(volumes).rolling(window=vol_window, min_periods=vol_window // 2).mean().values

    for i in range(1, n - 3):
        if np.isnan(vol_ma[i]) or vol_ma[i] == 0:
            continue
        vol_ratio = volumes[i] / vol_ma[i]
        if vol_ratio < vol_threshold:
            continue

        body = abs(closes[i] - opens[i])
        total_range = highs[i] - lows[i]
        if total_range == 0:
            continue
        range_pct = total_range / ((highs[i] + lows[i]) / 2)
        if range_pct < 0.001:
            continue

        if closes[i] > opens[i]:
            # Бычья свеча → откат вниз
            if i + 3 < n and closes[i + 1] < closes[i] and closes[i + 2] < closes[i + 1]:
                patterns.append({
                    'type': 'VOL_CLIMAX', 'direction': 'BEAR',
                    'description': 'Климакс вверх + откат (раздача)',
                    'swing_idx': int(i), 'break_idx': int(i),
                    'confirm_idx': int(i + 2),
                    'swing_level': float(highs[i]),
                    'volume_ratio': round(float(vol_ratio), 1),
                    'range_pct': round(float(range_pct * 100), 3),
                    'time': pd.Timestamp(times[i]),
                    'has_oi': bool(df.iloc[i]['has_oi']),
                })
        else:
            # Медвежья свеча → откат вверх
            if i + 3 < n and closes[i + 1] > closes[i] and closes[i + 2] > closes[i + 1]:
                patterns.append({
                    'type': 'VOL_CLIMAX', 'direction': 'BULL',
                    'description': 'Климакс вниз + откат (набор)',
                    'swing_idx': int(i), 'break_idx': int(i),
                    'confirm_idx': int(i + 2),
                    'swing_level': float(lows[i]),
                    'volume_ratio': round(float(vol_ratio), 1),
                    'range_pct': round(float(range_pct * 100), 3),
                    'time': pd.Timestamp(times[i]),
                    'has_oi': bool(df.iloc[i]['has_oi']),
                })

    for p in patterns:
        idx = p['swing_idx']
        if idx < len(df) and 'fiz_net' in df.columns:
            p['fiz_net'] = float(df.iloc[max(0, idx - 1)]['fiz_net'])
            p['fiz_delta'] = float(df.iloc[idx]['fiz_net_delta']) if 'fiz_net_delta' in df.columns else 0
        else:
            p['fiz_net'] = 0
            p['fiz_delta'] = 0

    return patterns


# ══════════════════════════════════════════════════════════════════════
#  4. OI-МАНИПУЛЯЦИИ (новые паттерны на основе OI)
# ══════════════════════════════════════════════════════════════════════

def detect_oi_traps(df: pd.DataFrame, oi_window: int):
    """
    OI-ловушки: расхождение между движением цены и позицией толпы (FIZ).

    Логика:
      FIZ_net_up   = толпа наращивает лонг
      FIZ_net_down = толпа наращивает шорт

      OI_TRAP_LONG:  цена растёт + FIZ_net растёт (толпа докупает на хаях) → разворот
      OI_TRAP_SHORT: цена падает + FIZ_net падает (толпа продаёт на лоях) → разворот
      OI_DIVERGENCE: цена растёт, но FIZ_net падает (умные деньги выходят) → предупреждение
    """
    patterns = []
    if 'fiz_net' not in df.columns or 'fiz_net_delta' not in df.columns:
        return patterns

    n = len(df)
    closes = df['close'].values
    times = df['time'].values
    fiz_net = df['fiz_net'].values
    fiz_delta = df['fiz_net_delta'].values
    has_oi = df['has_oi'].values

    # Скользящее среднее цены для определения направления тренда
    price_sma = pd.Series(closes).rolling(oi_window, min_periods=5).mean().values

    for i in range(oi_window, n - oi_window):
        if not has_oi[i] or np.isnan(fiz_delta[i]) or np.isnan(price_sma[i]):
            continue

        # Достаточное ли изменение OI?
        oi_change_pct = abs(fiz_delta[i] / (abs(fiz_net[i]) + 1))
        if oi_change_pct < DEFAULT_OI_CHANGE_PCT:
            continue

        # Направление цены
        price_up = closes[i] > price_sma[i]
        price_down = closes[i] < price_sma[i]
        fiz_up = fiz_delta[i] > 0
        fiz_down = fiz_delta[i] < 0

        # — OI ловушка: цена и OI в одном направлении (толпа набирает позицию против разворота) —
        if price_up and fiz_up:
            # Цена вверх, толпа набирает лонг → ждём разворота вниз
            for k in range(i + 1, min(i + oi_window, n)):
                if closes[k] < closes[i] * 0.998:  # разворот > 0.2%
                    patterns.append({
                        'type': 'OI_TRAP', 'direction': 'BEAR',
                        'description': 'Толпа набрала лонг на хаях — разворот',
                        'swing_idx': int(i), 'break_idx': int(i),
                        'confirm_idx': int(k),
                        'swing_level': float(closes[i]),
                        'fiz_net': float(fiz_net[i]),
                        'fiz_delta': float(fiz_delta[i]),
                        'oi_change_pct': round(oi_change_pct * 100, 1),
                        'time': pd.Timestamp(times[i]),
                        'has_oi': True,
                    })
                    break

        elif price_down and fiz_down:
            # Цена вниз, толпа набирает шорт → ждём разворота вверх
            for k in range(i + 1, min(i + oi_window, n)):
                if closes[k] > closes[i] * 1.002:
                    patterns.append({
                        'type': 'OI_TRAP', 'direction': 'BULL',
                        'description': 'Толпа набрала шорт на лоях — разворот',
                        'swing_idx': int(i), 'break_idx': int(i),
                        'confirm_idx': int(k),
                        'swing_level': float(closes[i]),
                        'fiz_net': float(fiz_net[i]),
                        'fiz_delta': float(fiz_delta[i]),
                        'oi_change_pct': round(oi_change_pct * 100, 1),
                        'time': pd.Timestamp(times[i]),
                        'has_oi': True,
                    })
                    break

        # — OI дивергенция (предупреждение, без подтверждения) —
        diverged = False
        if price_up and fiz_down and abs(fiz_delta[i]) > abs(fiz_net[i]) * 0.02:
            diverged = True
            div_desc = 'Цена растёт, а толпа сокращает лонг — умные деньги выходят'
        elif price_down and fiz_up and abs(fiz_delta[i]) > abs(fiz_net[i]) * 0.02:
            diverged = True
            div_desc = 'Цена падает, а толпа набирает лонг — ловушка'

        if diverged:
            patterns.append({
                'type': 'OI_DIVERGENCE', 'direction': 'NEUTRAL',
                'description': div_desc,
                'swing_idx': int(i), 'break_idx': int(i),
                'confirm_idx': int(i),
                'swing_level': float(closes[i]),
                'fiz_net': float(fiz_net[i]),
                'fiz_delta': float(fiz_delta[i]),
                'oi_change_pct': round(oi_change_pct * 100, 1),
                'time': pd.Timestamp(times[i]),
                'has_oi': True,
            })

    return patterns


# ══════════════════════════════════════════════════════════════════════
#  5. ATR + ФИЛЬТРАЦИЯ
# ══════════════════════════════════════════════════════════════════════

def calc_atr(df: pd.DataFrame, period: int = 14) -> np.ndarray:
    """Average True Range."""
    high, low, close = df['high'].values, df['low'].values, df['close'].values
    tr = np.full(len(df), np.nan)
    for i in range(1, len(df)):
        tr[i] = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
    return pd.Series(tr).rolling(period, min_periods=period).mean().values


def filter_patterns(patterns: list, df: pd.DataFrame, atr: np.ndarray):
    """Отфильтровать по значимости (ATR)."""
    filtered = []
    for p in patterns:
        idx = p['swing_idx']
        if idx >= len(atr) or np.isnan(atr[idx]):
            continue
        price = float(df.iloc[idx]['close'])
        atr_val = atr[idx]
        atr_pct = atr_val / price * 100

        if p['type'] == 'FALSE_BREAK':
            rpct = abs(p.get('rejection_pct', 0))
            if rpct < max(0.05, atr_pct * 0.3):
                continue
        elif p['type'] == 'STOP_HUNT':
            wick = p.get('wick_size', 0)
            wick_pct = wick / price * 100
            if wick_pct < max(0.05, atr_pct * 0.5):
                continue
        elif p['type'] == 'VOL_CLIMAX':
            rpct = p.get('range_pct', 0)
            if rpct < max(0.05, atr_pct * 0.4):
                continue
        elif p['type'] in ('OI_TRAP', 'OI_DIVERGENCE', 'OI_EXTREME'):
            pass  # OI паттерны не фильтруем по ATR, но должны войти в результат

        filtered.append(p)
    return filtered


def detect_oi_extreme(df: pd.DataFrame, oi_window: int, zscore_threshold: float = DEFAULT_ZSCORE):
    """
    OI-экстремумы: позиция толпы (FIZ) достигла экстремального уровня (|z-score| > threshold),
    после чего цена развернулась против толпы.

    FIZ z-score > +2  → толпа максимально в лонге → разворот вниз (distribute)
    FIZ z-score < -2  → толпа максимально в шорте → разворот вверх (accumulate)
    """
    patterns = []
    if 'fiz_net' not in df.columns or 'fiz_zscore' not in df.columns:
        return patterns

    n = len(df)
    closes = df['close'].values
    times = df['time'].values
    fiz_net = df['fiz_net'].values
    fiz_zscore = df['fiz_zscore'].values
    has_oi = df['has_oi'].values
    last_extreme_time = None  # для группировки соседних экстремумов

    for i in range(oi_window, n - oi_window):
        if not has_oi[i] or np.isnan(fiz_zscore[i]) or np.isnan(fiz_net[i]):
            continue
        if abs(fiz_zscore[i]) < zscore_threshold:
            continue

        # Пропускаем, если уже нашли экстремум в этом же районе
        if last_extreme_time is not None:
            time_diff = pd.Timestamp(times[i]) - last_extreme_time
            if time_diff.total_seconds() < 300 * oi_window:
                continue

        last_extreme_time = pd.Timestamp(times[i])  # запомнили экстремум

        extremely_long = fiz_zscore[i] > zscore_threshold
        extremely_short = fiz_zscore[i] < -zscore_threshold

        if extremely_long:
            # Толпа максимально в лонге — ждём разворота вниз
            for k in range(i + 1, min(i + oi_window, n)):
                if closes[k] < closes[i] * 0.998:
                    patterns.append({
                        'type': 'OI_EXTREME', 'direction': 'BEAR',
                        'description': f'FIZ z={fiz_zscore[i]:.1f} — толпа перекуплена, разворот',
                        'swing_idx': int(i), 'break_idx': int(i),
                        'confirm_idx': int(k),
                        'swing_level': float(closes[i]),
                        'fiz_net': float(fiz_net[i]),
                        'fiz_zscore': round(float(fiz_zscore[i]), 2),
                        'time': pd.Timestamp(times[i]),
                        'has_oi': True,
                    })
                    break

        elif extremely_short:
            # Толпа максимально в шорте — ждём разворота вверх
            for k in range(i + 1, min(i + oi_window, n)):
                if closes[k] > closes[i] * 1.002:
                    patterns.append({
                        'type': 'OI_EXTREME', 'direction': 'BULL',
                        'description': f'FIZ z={fiz_zscore[i]:.1f} — толпа перепродана, разворот',
                        'swing_idx': int(i), 'break_idx': int(i),
                        'confirm_idx': int(k),
                        'swing_level': float(closes[i]),
                        'fiz_net': float(fiz_net[i]),
                        'fiz_zscore': round(float(fiz_zscore[i]), 2),
                        'time': pd.Timestamp(times[i]),
                        'has_oi': True,
                    })
                    break

    return patterns


def deduplicate_patterns(patterns: list) -> list:
    """Убрать дубликаты."""
    seen = set()
    result = []
    for p in patterns:
        key = (p['type'], str(p['time']))
        if key not in seen:
            seen.add(key)
            result.append(p)
    return result


# ══════════════════════════════════════════════════════════════════════
#  6. ВИЗУАЛИЗАЦИЯ
# ══════════════════════════════════════════════════════════════════════

def plot_results(df: pd.DataFrame, patterns: list, symbol: str, output_path: str):
    """Построить Plotly-график с ценой, объёмом, OI и аннотациями."""
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    has_oi = 'fiz_net' in df.columns and df['has_oi'].any()
    n_rows = 3 if has_oi else 2
    row_heights = [0.55, 0.20, 0.25] if has_oi else [0.75, 0.25]

    COLOR_MAP = {
        'FALSE_BREAK': '#ff7f0e',
        'STOP_HUNT': '#d62728',
        'VOL_CLIMAX': '#9467bd',
        'OI_TRAP': '#1f77b4',
        'OI_DIVERGENCE': '#bcbd22',
        'OI_EXTREME': '#e377c2',
    }
    DIR_COLORS = {'BULL': '#2ca02c', 'BEAR': '#d62728', 'NEUTRAL': '#bcbd22'}

    fig = make_subplots(
        rows=n_rows, cols=1,
        shared_xaxes=True, vertical_spacing=0.04,
        row_heights=row_heights,
    )

    # ── Цена (Candlestick) ──
    fig.add_trace(go.Candlestick(
        x=df['time'], open=df['open'], high=df['high'],
        low=df['low'], close=df['close'],
        name='Price', showlegend=False,
    ), row=1, col=1)

    # Свинг-уровни
    if df['swing_high'].notna().any():
        mask = df['swing_high'].notna()
        fig.add_trace(go.Scatter(
            x=df.loc[mask, 'time'], y=df.loc[mask, 'swing_high'],
            mode='markers', marker=dict(symbol='triangle-down', size=8, color='#e63946'),
            name='Swing High', showlegend=True,
        ), row=1, col=1)
    if df['swing_low'].notna().any():
        mask = df['swing_low'].notna()
        fig.add_trace(go.Scatter(
            x=df.loc[mask, 'time'], y=df.loc[mask, 'swing_low'],
            mode='markers', marker=dict(symbol='triangle-up', size=8, color='#2d6a4f'),
            name='Swing Low', showlegend=True,
        ), row=1, col=1)

    # ── Паттерны ──
    for p in patterns:
        idx = p['swing_idx']
        if idx >= len(df):
            continue
        color = COLOR_MAP.get(p['type'], '#888')
        arrow_color = DIR_COLORS.get(p['direction'], '#888')
        label_map = {
            'FALSE_BREAK': '↑Fake' if p['direction'] == 'BEAR' else '↓Fake',
            'STOP_HUNT': '🎯Short' if p['direction'] == 'BEAR' else '🎯Long',
            'VOL_CLIMAX': '📊Distr' if p['direction'] == 'BEAR' else '📊Accum',
            'OI_TRAP': '🪤Trap↓' if p['direction'] == 'BEAR' else '🪤Trap↑',
            'OI_DIVERGENCE': '⚠️Div',
            'OI_EXTREME': '🔥Extr↓' if p['direction'] == 'BEAR' else '🔥Extr↑',
        }
        lbl = label_map.get(p['type'], p['type'])

        fig.add_annotation(
            x=df.iloc[idx]['time'],
            y=df.iloc[idx]['high'] * 1.005,
            text=f"<b>{lbl}</b>",
            showarrow=True,
            arrowhead=2 if p['direction'] == 'BEAR' else 6,
            arrowsize=1.2, arrowwidth=2, arrowcolor=arrow_color,
            ax=0, ay=-50,
            font=dict(size=9, color=color),
            bgcolor='rgba(0,0,0,0.7)', bordercolor=color, borderwidth=1,
        )
        fig.add_vrect(
            x0=df.iloc[max(0, idx - 1)]['time'],
            x1=df.iloc[min(len(df) - 1, idx + 1)]['time'],
            fillcolor=color, opacity=0.08, layer='below', line_width=0,
        )

    # ── Объём ──
    vol_colors = ['#2ca02c' if c >= o else '#d62728' for c, o in zip(df['close'], df['open'])]
    fig.add_trace(go.Bar(
        x=df['time'], y=df['volume'], name='Volume',
        marker=dict(color=vol_colors, opacity=0.6), showlegend=False,
    ), row=2, col=1)

    # ── OI (FIZ net) ──
    if has_oi:
        oi_color = np.where(df['fiz_net'] >= 0, '#2ca02c', '#d62728')
        fig.add_trace(go.Bar(
            x=df['time'], y=df['fiz_net'],
            name='FIZ Net (толпа)',
            marker=dict(color=oi_color, opacity=0.5),
            showlegend=True,
        ), row=3, col=1)
        # SMA линии
        if 'fiz_net_sma' in df.columns:
            fig.add_trace(go.Scatter(
                x=df['time'], y=df['fiz_net_sma'],
                line=dict(color='#58a6ff', width=1.5, dash='dot'),
                name='FIZ SMA', showlegend=True,
            ), row=3, col=1)

        # Нулевая линия
        fig.add_hline(y=0, line=dict(color='white', width=0.5), row=3, col=1)

    # ── Счётчик паттернов ──
    n_fb = sum(1 for p in patterns if p['type'] == 'FALSE_BREAK')
    n_sh = sum(1 for p in patterns if p['type'] == 'STOP_HUNT')
    n_vc = sum(1 for p in patterns if p['type'] == 'VOL_CLIMAX')
    n_oi = sum(1 for p in patterns if p['type'] == 'OI_TRAP')
    n_div = sum(1 for p in patterns if p['type'] == 'OI_DIVERGENCE')
    n_ext = sum(1 for p in patterns if p['type'] == 'OI_EXTREME')
    total = len(patterns)

    legend_parts = []
    if n_fb: legend_parts.append(f'FB={n_fb}')
    if n_sh: legend_parts.append(f'SH={n_sh}')
    if n_vc: legend_parts.append(f'VC={n_vc}')
    if n_oi: legend_parts.append(f'OI🪤={n_oi}')
    if n_div: legend_parts.append(f'OI⚠️={n_div}')
    if n_ext: legend_parts.append(f'OI🔥={n_ext}')
    legend_parts.append(f'total={total}')

    fig.add_annotation(
        xref='paper', yref='paper',
        x=1.0, y=1.0,
        text=f"<b>{'  '.join(legend_parts)}</b>",
        showarrow=False,
        font=dict(size=12, color='white'),
        bgcolor='rgba(0,0,0,0.6)', bordercolor='#555', borderwidth=1,
        xanchor='right', yanchor='top',
    )

    fig.update_layout(
        title=dict(
            text=f"<b>{symbol}</b> — Манипуляции против толпы ({', '.join(legend_parts)})",
            font=dict(size=16),
        ),
        template='plotly_dark', height=900 if has_oi else 800,
        margin=dict(l=40, r=40, t=60, b=40),
        hovermode='x unified', dragmode='pan',
    )
    fig.update_xaxes(rangeslider_visible=False, row=n_rows, col=1)
    fig.update_yaxes(title_text='Price', row=1, col=1)
    fig.update_yaxes(title_text='Volume', row=2, col=1)
    if has_oi:
        fig.update_yaxes(title_text='FIZ Net OI', row=3, col=1)

    fig.write_html(output_path)
    print(f"  График: {output_path}")

    if os.environ.get('DISPLAY'):
        try:
            fig.show()
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════════
#  7. ВЫВОД
# ══════════════════════════════════════════════════════════════════════

def _fmt_time(t):
    try:
        return pd.Timestamp(t).strftime('%Y-%m-%d %H:%M')
    except Exception:
        return str(t)[:16]


def _oi_summary_line(p):
    """Сформировать строку с OI контекстом."""
    fiz = p.get('fiz_net', 0)
    delta = p.get('fiz_delta', 0)
    if fiz is None or (isinstance(fiz, float) and np.isnan(fiz)):
        return ''
    if fiz != 0:
        oi_bias = 'LONG' if fiz > 0 else 'SHORT'
        s = f" | OI:FIZ={fiz:+.0f} ({oi_bias})"
        if delta and not (isinstance(delta, float) and np.isnan(delta)):
            s += f" Δ={delta:+.0f}"
        return s
    return ''


def print_results(patterns: list, symbol: str):
    """Вывод в консоль."""
    if not patterns:
        print("\n  Паттернов не обнаружено.")
        return

    print(f"\n{'=' * 80}")
    print(f"  НАЙДЕНО {len(patterns)} ПАТТЕРНОВ — {symbol}")
    print(f"{'=' * 80}")

    type_order = ['OI_EXTREME', 'OI_TRAP', 'OI_DIVERGENCE', 'FALSE_BREAK', 'STOP_HUNT', 'VOL_CLIMAX']
    type_headers = {
        'OI_EXTREME': '🔥 OI-экстремумы (OI Extreme)',
        'OI_TRAP': '🪤 OI-ловушки (OI Trap)',
        'OI_DIVERGENCE': '⚠️ OI-дивергенция (OI Divergence)',
        'FALSE_BREAK': '🔷 Ложные пробои (False Breakout)',
        'STOP_HUNT': '🔴 Охота за стопами (Stop Hunt)',
        'VOL_CLIMAX': '🟣 Объёмный климакс (Volume Climax)',
    }

    for p_type in type_order:
        subset = [p for p in patterns if p['type'] == p_type]
        if not subset:
            continue
        print(f"\n{'─' * 80}")
        print(f"  {type_headers[p_type]} — {len(subset)} шт.")
        print(f"{'─' * 80}")

        for p in subset:
            t_str = _fmt_time(p['time'])
            oi_str = _oi_summary_line(p)

            if p_type == 'FALSE_BREAK':
                print(f"  [{t_str}] {p['direction']} | "
                      f"уровень={p['swing_level']:.1f} "
                      f"отклонение={p['rejection_pct']:+.2f}% | {p['description']}"
                      f"{oi_str}")

            elif p_type == 'STOP_HUNT':
                print(f"  [{t_str}] {p['direction']} | "
                      f"фитиль={p['wick_size']:.1f} (ratio={p['wick_ratio']:.2f}) | "
                      f"{p['description']}{oi_str}")

            elif p_type == 'VOL_CLIMAX':
                print(f"  [{t_str}] {p['direction']} | "
                      f"объём x{p['volume_ratio']:.1f} (диапазон {p['range_pct']:.3f}%) | "
                      f"{p['description']}{oi_str}")

            elif p_type == 'OI_TRAP':
                print(f"  [{t_str}] {p['direction']} | "
                      f"close={p['swing_level']:.1f} "
                      f"FIZ_net={p['fiz_net']:+.0f} (Δ={p['fiz_delta']:+.0f}) | "
                      f"{p['description']}")

            elif p_type == 'OI_DIVERGENCE':
                print(f"  [{t_str}] | "
                      f"close={p['swing_level']:.1f} "
                      f"FIZ_net={p['fiz_net']:+.0f} (Δ={p['fiz_delta']:+.0f}) | "
                      f"{p['description']}")

            elif p_type == 'OI_EXTREME':
                z = p.get('fiz_zscore', 0)
                print(f"  [{t_str}] {p['direction']} | "
                      f"close={p['swing_level']:.1f} "
                      f"FIZ_net={p['fiz_net']:+.0f} z={z:+.1f} | "
                      f"{p['description']}")

    bulls = sum(1 for p in patterns if p['direction'] == 'BULL')
    bears = sum(1 for p in patterns if p['direction'] == 'BEAR')
    neutral = sum(1 for p in patterns if p['direction'] == 'NEUTRAL')
    print(f"\n{'─' * 80}")
    print(f"  BULL: {bulls}  BEAR: {bears}  NEUTRAL: {neutral}")
    print(f"{'=' * 80}\n")


# ══════════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════════

def resolve_symbol_case(user_symbol: str) -> str:
    """Найти правильный регистр тикера в БД."""
    try:
        conn = psycopg2.connect(
            host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
            user=DB_USER, password=DB_PASS
        )
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT symbol FROM moex_prices_5m")
        symbols = [r[0] for r in cur.fetchall()]
        conn.close()
        for s in symbols:
            if s.upper() == user_symbol.upper():
                return s
        return user_symbol
    except Exception:
        return user_symbol


def print_oi_summary(df: pd.DataFrame):
    """Вывести сводку по OI позициям на последнюю дату."""
    if 'fiz_net' not in df.columns or not df['has_oi'].any():
        return

    last_oi = df[df['has_oi']].iloc[-1]
    fiz_net = last_oi['fiz_net']
    fiz_buy = last_oi['fiz_buy']
    fiz_sell = last_oi['fiz_sell']
    yur_buy = last_oi['yur_buy']
    yur_sell = last_oi['yur_sell']
    total_oi = fiz_buy + fiz_sell  # общий OI

    fiz_long_pct = fiz_buy / total_oi * 100 if total_oi else 0
    fiz_short_pct = fiz_sell / total_oi * 100 if total_oi else 0

    bias = 'LONG' if fiz_net > 0 else 'SHORT'
    print(f"\n  OI на {last_oi['time']:%Y-%m-%d %H:%M}:")
    print(f"    Толпа (FIZ): buy={fiz_buy:,.0f} sell={fiz_sell:,.0f} "
          f"net={fiz_net:+,.0f} ({bias})")
    print(f"    Киты (YUR): buy={yur_buy:,.0f} sell={yur_sell:,.0f} "
          f"net={yur_buy - yur_sell:+,.0f}")
    print(f"    FIZ long: {fiz_long_pct:.1f}% short: {fiz_short_pct:.1f}%")


# ══════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description='Поиск манипуляций против толпы на MOEX фьючерсах (с OI)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument('--symbol', default='Si', help='Тикер (по умолч. Si)')
    parser.add_argument('--days', type=int, default=30, help='Глубина в днях')
    parser.add_argument('--swing-window', type=int, default=DEFAULT_SWING_WINDOW)
    parser.add_argument('--lookahead', type=int, default=DEFAULT_BREAK_LOOKAHEAD)
    parser.add_argument('--vol-threshold', type=float, default=DEFAULT_VOLUME_THRESHOLD)
    parser.add_argument('--wick-ratio', type=float, default=DEFAULT_WICK_BODY_RATIO)
    parser.add_argument('--oi-window', type=int, default=DEFAULT_OI_WINDOW)
    parser.add_argument('--zscore', type=float, default=DEFAULT_ZSCORE,
                        help=f'Порог z-score для OI_EXTREME (по умолч. {DEFAULT_ZSCORE})')
    parser.add_argument('--no-plot', action='store_true')
    parser.add_argument('--csv', default='', help='Сохранить CSV')
    parser.add_argument('--filters', default='',
                        help='Типы: false_break,stop_hunt,vol_climax,oi_trap,oi_divergence,oi_extreme')
    parser.add_argument('--no-oi', action='store_true', help='Не загружать OI данные')

    args = parser.parse_args()

    symbol = resolve_symbol_case(args.symbol.strip())

    print(f"\n{'=' * 70}")
    print(f"  MOEX Manipulation Scanner — {symbol}")
    print(f"  {args.days} дн.  swing={args.swing_window}  vol=x{args.vol_threshold}")
    print(f"{'=' * 70}")

    # ── Загрузка ──
    df_prices = load_price_data(symbol, args.days)

    df_oi = pd.DataFrame()
    if not args.no_oi:
        df_oi = load_oi_data(symbol, args.days)
        if df_oi.empty:
            print("  OI данные недоступны (работаем без OI)")

    df = prepare_data(df_prices, df_oi, symbol)
    print_oi_summary(df)

    # ── Свинговые уровни ──
    df = find_swing_points(df, args.swing_window)

    # ── Детекция ──
    epsilon = DEFAULT_BREAK_EPSILON
    if df['close'].max() < 10:
        epsilon = 0.002

    patterns = []

    # Ценовые паттерны
    patterns.extend(detect_false_breakouts(df, args.lookahead, epsilon))
    patterns.extend(detect_stop_hunts(df, args.wick_ratio, args.swing_window))
    patterns.extend(detect_volume_climax(df, DEFAULT_VOLUME_WINDOW, args.vol_threshold))

    # OI-паттерны (только если есть данные)
    if not df_oi.empty and not args.no_oi:
        patterns.extend(detect_oi_traps(df, args.oi_window))
        patterns.extend(detect_oi_extreme(df, args.oi_window, args.zscore))

    if not patterns:
        print("\n  Паттернов не обнаружено. Попробуйте увеличить --days или уменьшить пороги.")
        return

    # ── Фильтрация ──
    atr = calc_atr(df)
    before = len(patterns)
    patterns = filter_patterns(patterns, df, atr)
    if len(patterns) < before:
        print(f"  ATR-фильтр: отсеяно {before - len(patterns)} из {before}")

    before = len(patterns)
    patterns = deduplicate_patterns(patterns)
    if len(patterns) < before:
        print(f"  Дедупликация: убрано {before - len(patterns)}")

    # ── Фильтр по типам ──
    if args.filters:
        allowed = [t.strip().upper() for t in args.filters.split(',')]
        type_map = {
            'FALSE_BREAK': 'FALSE_BREAK', 'FB': 'FALSE_BREAK',
            'STOP_HUNT': 'STOP_HUNT', 'SH': 'STOP_HUNT',
            'VOL_CLIMAX': 'VOL_CLIMAX', 'VC': 'VOL_CLIMAX',
            'OI_TRAP': 'OI_TRAP', 'OIT': 'OI_TRAP',
            'OI_DIVERGENCE': 'OI_DIVERGENCE', 'OID': 'OI_DIVERGENCE',
            'OI_EXTREME': 'OI_EXTREME', 'OIE': 'OI_EXTREME',
        }
        allowed_types = {type_map.get(a, a) for a in allowed}
        patterns = [p for p in patterns if p['type'] in allowed_types]

    patterns.sort(key=lambda p: p['time'])

    # ── Вывод ──
    print_results(patterns, symbol)

    # ── CSV ──
    if args.csv:
        cols = ['type', 'direction', 'time', 'description', 'swing_level',
                'rejection_pct', 'volume_ratio', 'range_pct',
                'wick_size', 'wick_ratio',
                'fiz_net', 'fiz_delta', 'oi_change_pct',
                'has_oi']
        pdf = pd.DataFrame(patterns)
        pdf = pdf[[c for c in cols if c in pdf.columns]]
        pdf.to_csv(args.csv, index=False)
        print(f"  CSV: {args.csv}")

    # ── График ──
    if not args.no_plot:
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        out = os.path.join(OUTPUT_DIR, f'crowd_manip_{symbol}_{ts}.html')
        plot_results(df, patterns, symbol, out)


if __name__ == '__main__':
    main()
