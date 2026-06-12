#!/usr/bin/env python3
"""
Анализ: когда OI divergence работает, когда нет.
Разбиваем BR post-recovery данные по режимам и смотрим WR/return в каждом.

Режимы:
1. По сессиям: open (<12), mid (12-17), close (>17)
2. По дням недели: Mon-Fri
3. По ADX: тренд (ADX>25) vs боковик (ADX<25)
4. По ATR: низкая/средняя/высокая волатильность
5. По месяцам: сезонность
6. По времени суток (часам)
"""
import sys, os, csv
from datetime import datetime
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import clickhouse_connect
import numpy as np
import pandas as pd
from config import CH_HOST, CH_PORT, CH_DB

CAPITAL = 100000.0
SL_PCT = 0.05
COMMISSION = 2.0
MU = 0.50
OUT_DIR = "reports/phase3"


def get_ch():
    return clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)


def zscore_series(series, window):
    s = pd.Series(series.astype(np.float64))
    mu = s.rolling(window, min_periods=window).mean()
    sd = s.rolling(window, min_periods=window).std()
    result = (s - mu) / sd
    result = result.fillna(0.0).replace([np.inf, -np.inf], 0.0)
    return result.values.astype(np.float64)


def load_data(ch, ticker, start_date=None):
    conditions = ["o.symbol = {t:String}", "p.symbol = {t:String}"]
    params = {"t": ticker}
    if start_date:
        conditions.append("p.time >= {start:String}")
        params["start"] = start_date
    where = " AND ".join(conditions)
    query = f"""
    SELECT p.time, p.open, p.high, p.low, p.close, p.volume,
           o.fiz_buy, o.fiz_sell, o.yur_buy, o.yur_sell, o.total_oi
    FROM moex.prices_5m_oi AS o
    INNER JOIN moex.prices_5m AS p ON p.symbol = o.symbol AND p.time = o.time
    WHERE {where} ORDER BY p.time
    """
    rows = ch.query(query, parameters=params).result_rows
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=[
        "time","open","high","low","close","volume",
        "fiz_buy","fiz_sell","yur_buy","yur_sell","total_oi"
    ])
    return df


def add_features(df):
    """Add session, day of week, hour, ATR, ADX features."""
    df = df.copy()
    df['dt'] = pd.to_datetime(df['time'])
    df['hour'] = df['dt'].dt.hour + df['dt'].dt.minute / 60.0
    df['dow'] = df['dt'].dt.dayofweek  # 0=Mon
    df['month'] = df['dt'].dt.month
    
    # Session
    df['session'] = 'mid'
    df.loc[df['hour'] < 12, 'session'] = 'open'
    df.loc[df['hour'] >= 17, 'session'] = 'close'
    
    # ATR(14)
    hi = df['high'].values.astype(np.float64)
    lo = df['low'].values.astype(np.float64)
    cl = df['close'].values.astype(np.float64)
    tr = np.maximum(hi[1:] - lo[1:], 
                    np.maximum(np.abs(hi[1:] - cl[:-1]), 
                               np.abs(lo[1:] - cl[:-1])))
    tr = np.concatenate([[tr[0]], tr])  # pad first
    atr = pd.Series(tr).rolling(14).mean().values
    df['atr'] = atr
    df['atr_pct'] = atr / cl * 100
    
    # ATR percentile (over last 100 bars)
    atr_pct_series = pd.Series(df['atr_pct'].values)
    df['atr_percentile'] = atr_pct_series.rolling(100).apply(
        lambda x: (x[-1] >= x).mean() * 100, raw=True
    ).fillna(50).values
    
    # ADX(14)
    high, low, close = hi, lo, cl
    
    # +DM and -DM
    up_move = high[1:] - high[:-1]
    down_move = low[:-1] - low[1:]
    pos_dm = np.zeros_like(up_move)
    neg_dm = np.zeros_like(down_move)
    
    pos_mask = (up_move > down_move) & (up_move > 0)
    neg_mask = (down_move > up_move) & (down_move > 0)
    pos_dm[pos_mask] = up_move[pos_mask]
    neg_dm[neg_mask] = down_move[neg_mask]
    
    pos_dm = np.concatenate([[0], pos_dm])
    neg_dm = np.concatenate([[0], neg_dm])
    
    # Smoothed
    tr_smooth = pd.Series(tr).ewm(span=14).mean().values
    pos_dm_smooth = pd.Series(pos_dm).ewm(span=14).mean().values
    neg_dm_smooth = pd.Series(neg_dm).ewm(span=14).mean().values
    
    # DI+ and DI-
    di_plus = pos_dm_smooth / tr_smooth * 100
    di_minus = neg_dm_smooth / tr_smooth * 100
    
    # DX and ADX
    dx = np.abs(di_plus - di_minus) / (di_plus + di_minus + 1e-10) * 100
    df['adx'] = pd.Series(dx).ewm(span=14).mean().values
    df['adx'] = df['adx'].fillna(20).values
    
    # DI spread
    df['di_spread'] = di_plus - di_minus
    
    return df


def analyze_by_regime(df, W, T, hold, variant=1):
    """
    Разбиваем все сделки OI divergence по режимам и считаем WR/return в каждом.
    """
    fiz_net = (df['fiz_buy'].values - df['fiz_sell'].values).astype(np.float64)
    yur_net = (df['yur_buy'].values - df['yur_sell'].values).astype(np.float64)
    
    fiz_z = zscore_series(fiz_net, W)
    yur_z = zscore_series(yur_net, W)
    
    if variant == 1:
        div = fiz_z - yur_z
    elif variant == 2:
        div = yur_z * 2.0 - fiz_z
    else:
        div = fiz_z - yur_z
        div[(fiz_z * yur_z) >= 0] = 0.0
    
    closes = df['close'].values.astype(np.float64)
    lows = df['low'].values.astype(np.float64)
    highs = df['high'].values.astype(np.float64)
    n = len(df)
    
    # Run backtest, record each trade with features at entry time
    trades = []
    position = None
    cur_cap = float(CAPITAL)
    
    for i in range(n):
        cl = closes[i]
        hi, lo = highs[i], lows[i]
        
        if position is not None:
            pos = position
            pos['bars_held'] += 1
            should_exit = False
            exit_px = cl
            reason = None
            
            if pos['dir'] == 'LONG':
                stop = pos['entry_px'] * (1 - SL_PCT)
                if lo <= stop:
                    exit_px = stop
                    should_exit = True
                    reason = 'stop_loss'
            else:
                stop = pos['entry_px'] * (1 + SL_PCT)
                if hi >= stop:
                    exit_px = stop
                    should_exit = True
                    reason = 'stop_loss'
            
            if not should_exit and pos['bars_held'] >= hold:
                exit_px = cl
                should_exit = True
                reason = 'time_stop'
            
            if should_exit:
                if pos['dir'] == 'LONG':
                    ret = (exit_px - pos['entry_px']) / pos['entry_px']
                else:
                    ret = (pos['entry_px'] - exit_px) / pos['entry_px']
                pnl = ret * pos['cap_used']
                net_pnl = pnl - COMMISSION
                cur_cap += pos['cap_used'] + net_pnl
                
                # Store trade with entry features
                trades.append({
                    **pos['features'],
                    'dir': pos['dir'],
                    'ret_pct': float(ret * 100),
                    'pnl_net': float(net_pnl),
                    'bars_held': pos['bars_held'],
                    'reason': reason,
                    'entry_px': float(pos['entry_px']),
                    'exit_px': float(exit_px),
                })
                position = None
        
        if position is None and i > 0:
            div_val = div[i - 1]
            direction = None
            if div_val > T:
                direction = 'SHORT'
            elif div_val < -T:
                direction = 'LONG'
            
            if direction:
                entry_px = cl
                cap_used = cur_cap * MU
                if cap_used > 0 and entry_px > 0:
                    # Capture features at ENTRY time
                    features = {
                        'entry_time': str(df['dt'].iloc[i]),
                        'session': df['session'].iloc[i],
                        'dow': int(df['dow'].iloc[i]),
                        'hour': round(float(df['hour'].iloc[i]), 2),
                        'month': int(df['month'].iloc[i]),
                        'adx': round(float(df['adx'].iloc[i]), 2),
                        'atr_pct': round(float(df['atr_pct'].iloc[i]), 4),
                        'atr_percentile': round(float(df['atr_percentile'].iloc[i]), 1),
                        'di_spread': round(float(df['di_spread'].iloc[i]), 2),
                        'close': float(entry_px),
                        'divergence': round(float(div_val), 4),
                    }
                    position = {
                        'dir': direction, 'entry_px': entry_px,
                        'bars_held': 0, 'cap_used': cap_used,
                        'features': features,
                    }
                    cur_cap -= cap_used
    
    return trades


def print_regime_analysis(trades, title="BR OI Divergence"):
    """Печатает WR по каждому режиму."""
    if not trades:
        print(f"\n{title}: нет сделок")
        return
    
    df = pd.DataFrame(trades)
    total_trades = len(df)
    total_win = (df['pnl_net'] > 0).mean() * 100
    total_pnl = df['pnl_net'].sum()
    
    print(f"\n{'='*60}")
    print(f"  {title} — {total_trades} сделок")
    print(f"  Общий WR: {total_win:.1f}% | PnL: {total_pnl:+.0f} RUB")
    print(f"{'='*60}")
    
    # 1. By session
    print(f"\n── По сессиям ──")
    print(f"{'Session':<10} {'Trades':>8} {'WR':>8} {'Avg PnL':>10} {'Total':>10}")
    print("-" * 50)
    for sess in ['open', 'mid', 'close']:
        sub = df[df['session'] == sess]
        if len(sub) == 0:
            continue
        wr = (sub['pnl_net'] > 0).mean() * 100
        print(f"{sess:<10} {len(sub):>8} {wr:>7.1f}% {sub['pnl_net'].mean():>+9.0f} {sub['pnl_net'].sum():>+9.0f}")
    
    # 2. By day of week
    days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri']
    print(f"\n── По дням недели ──")
    print(f"{'Day':<10} {'Trades':>8} {'WR':>8} {'Avg PnL':>10} {'Total':>10}")
    print("-" * 50)
    for d, name in enumerate(days):
        sub = df[df['dow'] == d]
        if len(sub) == 0:
            continue
        wr = (sub['pnl_net'] > 0).mean() * 100
        print(f"{name:<10} {len(sub):>8} {wr:>7.1f}% {sub['pnl_net'].mean():>+9.0f} {sub['pnl_net'].sum():>+9.0f}")
    
    # 3. By ADX regime
    print(f"\n── По ADX (тренд vs боковик) ──")
    print(f"{'Regime':<15} {'Trades':>8} {'WR':>8} {'Avg PnL':>10} {'Total':>10}")
    print("-" * 50)
    for label, cond in [('ADX<20 (боковик)', df['adx'] < 20),
                         ('ADX 20-25', (df['adx'] >= 20) & (df['adx'] < 25)),
                         ('ADX 25-30', (df['adx'] >= 25) & (df['adx'] < 30)),
                         ('ADX>30 (тренд)', df['adx'] >= 30)]:
        sub = df[cond]
        if len(sub) == 0:
            continue
        wr = (sub['pnl_net'] > 0).mean() * 100
        print(f"{label:<15} {len(sub):>8} {wr:>7.1f}% {sub['pnl_net'].mean():>+9.0f} {sub['pnl_net'].sum():>+9.0f}")
    
    # 4. By ATR percentile
    print(f"\n── По ATR процентилю (волатильность) ──")
    print(f"{'ATR pctl':<15} {'Trades':>8} {'WR':>8} {'Avg PnL':>10} {'Total':>10}")
    print("-" * 50)
    for label, lo, hi in [('ATR<25 (low)', 0, 25), ('ATR 25-50', 25, 50),
                           ('ATR 50-75', 50, 75), ('ATR>75 (high)', 75, 101)]:
        sub = df[(df['atr_percentile'] >= lo) & (df['atr_percentile'] < hi)]
        if len(sub) == 0:
            continue
        wr = (sub['pnl_net'] > 0).mean() * 100
        print(f"{label:<15} {len(sub):>8} {wr:>7.1f}% {sub['pnl_net'].mean():>+9.0f} {sub['pnl_net'].sum():>+9.0f}")
    
    # 5. By hour of day
    print(f"\n── По часу ──")
    print(f"{'Hour':<10} {'Trades':>8} {'WR':>8} {'Avg PnL':>10} {'Total':>10}")
    print("-" * 50)
    for h in range(10, 24):
        sub = df[(df['hour'] >= h) & (df['hour'] < h + 1)]
        if len(sub) < 3:
            continue
        wr = (sub['pnl_net'] > 0).mean() * 100
        print(f"{h:02d}:00  {len(sub):>8} {wr:>7.1f}% {sub['pnl_net'].mean():>+9.0f} {sub['pnl_net'].sum():>+9.0f}")
    
    # 6. By month
    months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
    print(f"\n── По месяцам ──")
    print(f"{'Month':<10} {'Trades':>8} {'WR':>8} {'Avg PnL':>10} {'Total':>10}")
    print("-" * 50)
    for m in range(1, 13):
        sub = df[df['month'] == m]
        if len(sub) < 3:
            continue
        wr = (sub['pnl_net'] > 0).mean() * 100
        print(f"{months[m-1]:<10} {len(sub):>8} {wr:>7.1f}% {sub['pnl_net'].mean():>+9.0f} {sub['pnl_net'].sum():>+9.0f}")
    
    # 7. By divergence strength
    div_abs = df['divergence'].abs()
    print(f"\n── По силе сигнала (|div|) ──")
    print(f"{'|Div| range':<15} {'Trades':>8} {'WR':>8} {'Avg PnL':>10} {'Total':>10}")
    print("-" * 50)
    thresholds = [1.5, 2.0, 2.5, 3.0, 5.0]
    prev = 1.5
    for th in thresholds:
        sub = df[(div_abs >= prev) & (div_abs < th)]
        if len(sub) >= 3:
            wr = (sub['pnl_net'] > 0).mean() * 100
            print(f"{prev:.1f}-{th:.1f}     {len(sub):>8} {wr:>7.1f}% {sub['pnl_net'].mean():>+9.0f} {sub['pnl_net'].sum():>+9.0f}")
        prev = th
    sub = df[div_abs >= 5.0]
    if len(sub) >= 3:
        wr = (sub['pnl_net'] > 0).mean() * 100
        print(f">=5.0      {len(sub):>8} {wr:>7.1f}% {sub['pnl_net'].mean():>+9.0f} {sub['pnl_net'].sum():>+9.0f}")


def main():
    ch = get_ch()
    os.makedirs(OUT_DIR, exist_ok=True)
    
    print(f"АНАЛИЗ: когда OI divergence работает | {datetime.now()}")
    print(f"Параметры: W=40 T=2.0 hold=10, стоп=5%, mu={MU}")
    
    # BR post-recovery
    print("\nЗагрузка BR (2026-04+)...")
    df = load_data(ch, 'BR', start_date='2026-04-01')
    if df is not None:
        df = add_features(df)
        print(f"  {len(df)} баров")
        
        trades = analyze_by_regime(df, W=40, T=2.0, hold=10, variant=1)
        print_regime_analysis(trades, "BR OI Div V1 (W=40 T=2.0 hold=10)")
        
        # Save trades for deeper analysis
        if trades:
            df_trades = pd.DataFrame(trades)
            csv_path = os.path.join(OUT_DIR, "br_divergence_trades.csv")
            df_trades.to_csv(csv_path, index=False)
            print(f"\n✅ Сохранено {csv_path}")
    else:
        print("  ⚠ Нет данных")
    
    # AF post-recovery  
    print("\n\nЗагрузка AF (2025-08+)...")
    df2 = load_data(ch, 'AF', start_date='2025-08-01')
    if df2 is not None:
        df2 = add_features(df2)
        print(f"  {len(df2)} баров")
        trades2 = analyze_by_regime(df2, W=40, T=1.5, hold=5, variant=2)
        print_regime_analysis(trades2, "AF OI Div V2 (W=40 T=1.5 hold=5)")
    
    print(f"\nDone: {datetime.now()}")


if __name__ == "__main__":
    main()
