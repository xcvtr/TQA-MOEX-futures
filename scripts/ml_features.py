#!/usr/bin/env python3
"""ML feature engineering: 100+ features + target for futures prediction."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
import clickhouse_connect
from config import CH_HOST, CH_PORT, CH_DB
from scripts.stress_test_full import rz, calc_atr, calc_adx


def load_data(sym):
    ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)
    q = f"""
        SELECT p.time, p.open, p.high, p.low, p.close, p.volume,
               o.fiz_buy, o.fiz_sell, o.yur_buy, o.yur_sell, o.total_oi
        FROM moex.prices_5m p
        LEFT JOIN moex.prices_5m_oi o ON p.time = o.time AND p.symbol = o.symbol
        WHERE p.symbol='{sym}' AND p.time>='2023-01-01' AND p.time<='2026-05-31'
        ORDER BY p.time
    """
    r = ch.query(q)
    cols = ['time','open','high','low','close','volume',
            'fiz_buy','fiz_sell','yur_buy','yur_sell','total_oi']
    df = pd.DataFrame(r.result_rows, columns=cols)
    df['time'] = pd.to_datetime(df['time']).dt.tz_localize(None)
    df.set_index('time', inplace=True)
    for c in cols[1:]:
        df[c] = df[c].astype(float)
    return df


def calc_plus_minus_di(df, p=14):
    high, low, close = df['high'], df['low'], df['close']
    up_move = high - high.shift(1)
    down_move = low.shift(1) - low
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    tr = pd.concat([high - low, (high - close.shift(1)).abs(),
                    (low - close.shift(1)).abs()], axis=1).max(axis=1)
    atr = tr.rolling(p, min_periods=p).mean().clip(lower=1e-10)
    plus_di = 100 * pd.Series(plus_dm, index=df.index).rolling(p, min_periods=p).mean() / atr
    minus_di = 100 * pd.Series(minus_dm, index=df.index).rolling(p, min_periods=p).mean() / atr
    return plus_di.bfill().fillna(0), minus_di.bfill().fillna(0)


def resample_h1(df):
    rez = pd.DataFrame(index=df.index)
    rez['open'] = df['open'].resample('60min').first()
    rez['high'] = df['high'].resample('60min').max()
    rez['low'] = df['low'].resample('60min').min()
    rez['close'] = df['close'].resample('60min').last()
    rez['volume'] = df['volume'].resample('60min').sum().astype(float)
    for col in ['fiz_buy', 'fiz_sell', 'yur_buy', 'yur_sell', 'total_oi']:
        rez[col] = df[col].resample('60min').last().fillna(0).astype(float)
    return rez.dropna()


def add_features(d):
    df = d.copy()
    eps = 1e-10

    v = df['volume'].astype(float)
    df['vma20'] = v.rolling(20, min_periods=1).mean()
    df['vma50'] = v.rolling(50, min_periods=1).mean()
    df['vr'] = v / df['vma20'].clip(lower=eps)
    df['vz'] = rz(v, 20).fillna(0)

    for c in ['fiz_buy', 'fiz_sell', 'yur_buy', 'yur_sell', 'total_oi']:
        df[c] = df[c].fillna(0).astype(float)

    df['fiz_net'] = df['fiz_buy'] - df['fiz_sell']
    df['yur_net'] = df['yur_buy'] - df['yur_sell']
    df['fiz_total'] = df['fiz_buy'] + df['fiz_sell']
    df['yur_total'] = df['yur_buy'] + df['yur_sell']
    df['oi_r'] = df['yur_total'] / (df['fiz_total'] + 1)
    df['oi_r_z'] = rz(df['oi_r'], 20).fillna(0)
    df['oi_r_ma5'] = df['oi_r'].rolling(5).mean()
    df['oi_r_ma20'] = df['oi_r'].rolling(20).mean()
    df['oi_accel'] = df['oi_r'].diff().rolling(5).mean()
    df['fiz_yur_delta'] = (df['fiz_net'] - df['yur_net']).abs() / (df['fiz_net'].abs() + df['yur_net'].abs() + 1)
    df['oi_change'] = df['total_oi'].pct_change().fillna(0)

    df['atr14'] = calc_atr(df)
    df['atr_pct'] = df['atr14'] / df['close'].clip(lower=eps) * 100
    df['adx14'] = calc_adx(df)
    df['plus_di14'], df['minus_di14'] = calc_plus_minus_di(df)
    df['di_diff'] = df['plus_di14'] - df['minus_di14']
    df['di_sum'] = df['plus_di14'] + df['minus_di14'] + eps

    body = (df['close'] - df['open']).abs()
    candle_range = df['high'] - df['low']
    df['body_ratio'] = body / candle_range.clip(lower=eps)
    df['upper_shadow'] = (df['high'] - df[['open', 'close']].max(axis=1)) / candle_range.clip(lower=eps)
    df['lower_shadow'] = (df[['open', 'close']].min(axis=1) - df['low']) / candle_range.clip(lower=eps)
    df['candle_dir'] = np.sign(df['close'] - df['open'])
    df['candle_range_pct'] = candle_range / df['close'].clip(lower=eps) * 100

    df['close_pct_change_1'] = df['close'].pct_change(1).fillna(0)
    df['close_pct_change_5'] = df['close'].pct_change(5).fillna(0)
    df['close_pct_change_10'] = df['close'].pct_change(10).fillna(0)
    df['close_pct_change_20'] = df['close'].pct_change(20).fillna(0)
    df['high_pct_change_1'] = df['high'].pct_change(1).fillna(0)
    df['low_pct_change_1'] = df['low'].pct_change(1).fillna(0)
    df['open_pct_change_1'] = df['open'].pct_change(1).fillna(0)

    df['hh10'] = df['high'].rolling(10).max()
    df['ll10'] = df['low'].rolling(10).min()
    df['hh20'] = df['high'].rolling(20).max()
    df['ll20'] = df['low'].rolling(20).min()
    df['hh50'] = df['high'].rolling(50).max()
    df['ll50'] = df['low'].rolling(50).min()
    df['pos_in_10'] = (df['close'] - df['ll10']) / (df['hh10'] - df['ll10'] + eps)
    df['pos_in_20'] = (df['close'] - df['ll20']) / (df['hh20'] - df['ll20'] + eps)
    df['pos_in_50'] = (df['close'] - df['ll50']) / (df['hh50'] - df['ll50'] + eps)
    df['high_break_20'] = (df['high'] > df['hh20'].shift(1)).astype(int)
    df['low_break_20'] = (df['low'] < df['ll20'].shift(1)).astype(int)

    for w in [5, 10, 20, 50, 100]:
        df[f'ma{w}'] = df['close'].rolling(w).mean()
        df[f'close_ma{w}_ratio'] = df['close'] / df[f'ma{w}'].clip(lower=eps)

    ret = df['close'].pct_change().fillna(0)
    df['returns'] = ret
    df['volatility_5'] = ret.rolling(5).std().fillna(0)
    df['volatility_10'] = ret.rolling(10).std().fillna(0)
    df['volatility_20'] = ret.rolling(20).std().fillna(0)
    df['volatility_50'] = ret.rolling(50).std().fillna(0)
    df['vol_ratio_5_20'] = df['volatility_5'] / df['volatility_20'].clip(lower=eps)

    for w in [7, 21]:
        df[f'atr{w}'] = calc_atr(df, p=w)
        df[f'atr{w}_pct'] = df[f'atr{w}'] / df['close'].clip(lower=eps) * 100
    df['atr14_ma20'] = df['atr14'] / df['atr14'].rolling(20).mean().clip(lower=eps)
    df['atr_ratio_7_14'] = df['atr7'] / df['atr14'].clip(lower=eps)
    df['atr_ratio_14_21'] = df['atr14'] / df['atr21'].clip(lower=eps)

    mid = (df['high'] + df['low']) / 2
    std20 = df['close'].rolling(20).std()
    ma20 = df['close'].rolling(20).mean()
    df['bb_upper'] = ma20 + 2 * std20
    df['bb_lower'] = ma20 - 2 * std20
    df['bb_width'] = (df['bb_upper'] - df['bb_lower']) / ma20.clip(lower=eps) * 100
    df['bb_pos'] = (df['close'] - df['bb_lower']) / (df['bb_upper'] - df['bb_lower'] + eps)
    df['bb_upper_dist'] = (df['bb_upper'] - df['close']) / df['close'].clip(lower=eps) * 100
    df['bb_lower_dist'] = (df['close'] - df['bb_lower']) / df['close'].clip(lower=eps) * 100

    df['resid'] = df['close'] - ma20
    df['zscore_20'] = rz(df['close'], 20).fillna(0)
    df['zscore_volume_50'] = rz(v, 50).fillna(0)

    df['hour'] = df.index.hour
    df['weekday'] = df.index.dayofweek
    df['month'] = df.index.month
    df['dayofyear'] = df.index.dayofyear
    df['is_month_start'] = (df.index.day <= 5).astype(int)
    df['is_month_end'] = (df.index.day >= 25).astype(int)
    df['quarter'] = df.index.quarter

    df['cum_ret_5'] = (1 + ret).rolling(5).apply(lambda x: x.prod(), raw=True).fillna(1)
    df['cum_ret_10'] = (1 + ret).rolling(10).apply(lambda x: x.prod(), raw=True).fillna(1)
    df['max_ret_5'] = ret.rolling(5).max().fillna(0)
    df['min_ret_5'] = ret.rolling(5).min().fillna(0)

    return df


def add_lags(df, cols, shifts=(1, 2, 3)):
    lagged = pd.concat({f'{c}_lag{s}': df[c].shift(s) for c in cols for s in shifts}, axis=1)
    return pd.concat([df, lagged], axis=1)


def add_target(df, N=4):
    thresh = 1 + df['atr_pct'] * N / 100
    df['target'] = (df['close'].shift(-N) / df['close'].clip(lower=1e-10) > thresh).astype(int)
    return df


def build_dataset(sym, df_5m, df_oi):
    df = df_5m.copy()
    for c in ['fiz_buy', 'fiz_sell', 'yur_buy', 'yur_sell', 'total_oi']:
        df[c] = df_oi[c].astype(float)

    df_h1 = resample_h1(df)
    df_h1 = add_features(df_h1)

    exclude = {'open', 'high', 'low', 'close', 'volume',
               'fiz_buy', 'fiz_sell', 'yur_buy', 'yur_sell',
               'total_oi', 'returns', 'target'}
    base_cols = [c for c in df_h1.columns if c not in exclude]
    time_cols = {'hour', 'weekday', 'month', 'dayofyear',
                 'is_month_start', 'is_month_end', 'quarter'}
    lag_cols = [c for c in base_cols if c not in time_cols]

    df_h1 = add_lags(df_h1, lag_cols)
    df_h1 = add_target(df_h1, N=4)
    df_h1 = df_h1.dropna()

    # Reorder: features first, target last
    feature_names = [c for c in df_h1.columns if c != 'target']
    df_h1 = df_h1[feature_names + ['target']]
    df_h1 = df_h1.astype({c: 'float32' for c in feature_names if c not in time_cols})
    return df_h1


if __name__ == '__main__':
    sym = 'GL'
    print(f"Loading {sym}...")
    raw = load_data(sym)
    print(f"5m rows: {len(raw)}  ({raw.index.min().date()} -> {raw.index.max().date()})")

    df_5m = raw[['open', 'high', 'low', 'close', 'volume']].copy()
    df_oi = raw[['fiz_buy', 'fiz_sell', 'yur_buy', 'yur_sell', 'total_oi']].copy()

    print("Building dataset...")
    ds = build_dataset(sym, df_5m, df_oi)
    print(f"Dataset: {ds.shape[0]} rows, {ds.shape[1]} cols ({ds.columns.tolist()[0]} .. {ds.columns.tolist()[-1]})")
    print(f"Target balance: {ds['target'].value_counts().to_dict()}")
    print(f"Feature count: {ds.shape[1] - 1}")

    os.makedirs('reports', exist_ok=True)
    path = f'reports/ml_features_{sym}.parquet'
    ds.to_parquet(path, index=True)
    print(f"Saved: {path}")
