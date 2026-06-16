#!/usr/bin/env python3
"""
Phase 3g — Полный sweep: все тикеры × все TF × ATR*1.0.
Быстрый: только 1 конфиг на тикер (лучший из Phase 3: OI Div V1, W=40, T=2.0, hold=10, ATR*1.0).
"""
import sys, os
sys.path.insert(0, '/home/user/projects/TQA-MOEX')
os.chdir('/home/user/projects/TQA-MOEX')
import clickhouse_connect
import numpy as np
import pandas as pd
from config import CH_HOST, CH_PORT, CH_DB

ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)

COMM = 2.0
CAP = 200000.0
OUT = "reports/phase3"

# ── Конфигурация ──
rec_map = {
    "AF": "2025-08-01", "AL": "2021-11-01", "BR": "2026-04-01", "ED": "2022-05-01",
    "Eu": "2022-10-01", "GZ": "2023-05-01", "IMOEXF": "2024-07-01", "LK": "2022-11-01",
    "MX": "2024-02-01", "NG": "2023-01-01", "NM": "2023-05-01", "PD": "2022-10-01",
    "RI": "2023-04-01", "RN": "2021-11-01", "SN": "2021-10-01", "SR": "2025-07-01",
    "SV": "2024-02-01", "VB": "2024-06-01",
}
always = {"Si", "CR", "CNYRUBF", "USDRUBF", "GLDRUBF"}
exclude = {"GD", "PT"}

# Готовим конфиги для каждого тикера
# V1 = fiz - yur (standard), V2 = yur*2 - fiz (weighted)
# Для каждого тикера тестируем V1 и V2
TICKER_CONFIGS = {
    # (ticker, variant, recovery_start) — recovery_start=None для always
}

def get_go(ticker):
    r = ch.query("SELECT go_rub, lot, stepprice, minstep FROM moex.securities WHERE ticker = {t:String}", parameters={'t': ticker}).result_rows
    if r:
        return {'go': float(r[0][0]), 'lot': int(r[0][1]), 'stepprice': float(r[0][2]), 'minstep': float(r[0][3])}
    return None

tickers_to_test = ['AL', 'ED', 'LK', 'MX', 'NG', 'NM', 'PD', 'RI', 'RN', 'SV', 'VB'] + \
                  ['Si', 'CNYRUBF', 'USDRUBF', 'GLDRUBF'] + \
                  ['BR', 'AF', 'SR', 'IMOEXF', 'Eu', 'CR']  # уже проверенные — для перепроверки на других TF

# ── TF map ──
TF_CONFIGS = {
    '5m':  {'table': 'moex.prices_5m_oi', 'resample': None, 'n_bars_year': 57000},
    '15m': {'table': 'moex.prices_5m', 'resample': '15min', 'n_bars_year': 19000},
    'H1':  {'table': 'moex.prices_5m', 'resample': '1h', 'n_bars_year': 4750},
    'D1':  {'table': 'moex.prices_5m', 'resample': 'D', 'n_bars_year': 250},
}

def load_resampled(ticker, start_date, tf_config):
    """Load and resample data for a timeframe."""
    if tf_config['resample'] is None:
        # Direct 5m OI table
        conditions = ["o.symbol = {t:String}"]
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
        if not rows: return None
        df = pd.DataFrame(rows, columns=[
            "time","open","high","low","close","volume",
            "fiz_buy","fiz_sell","yur_buy","yur_sell","total_oi"
        ])
        return df
    else:
        # Resample from 5m
        conditions = ["p.symbol = {t:String}"]
        params = {"t": ticker}
        if start_date:
            conditions.append("p.time >= {start:String}")
            params["start"] = start_date
        where = " AND ".join(conditions)
        query = f"""
        SELECT p.time, p.open, p.high, p.low, p.close, p.volume
        FROM moex.prices_5m AS p
        WHERE {where} ORDER BY p.time
        """
        rows = ch.query(query, parameters=params).result_rows
        if not rows: return None
        df = pd.DataFrame(rows, columns=["time","open","high","low","close","volume"])
        df['time'] = pd.to_datetime(df['time'])
        df = df.set_index('time').resample(tf_config['resample']).agg({
            'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'
        }).dropna().reset_index()
        return df

def run_test(df, go_info, variant=1, W=40, T=2.0, hold=10):
    """Быстрый тест OI Div с ATR*1.0 стопом."""
    if df is None or len(df) < 100:
        return None
    
    opens = df['open'].values.astype(np.float64)
    highs = df['high'].values.astype(np.float64)
    lows = df['low'].values.astype(np.float64)
    closes = df['close'].values.astype(np.float64)
    n = len(df)
    
    # ATR
    tr = np.zeros(n)
    for i in range(1, n):
        tr[i] = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
    tr[0] = highs[0]-lows[0]
    atr = pd.Series(tr).ewm(span=14).mean().values
    
    # Signal
    if 'fiz_buy' in df.columns:
        fiz_net = (df['fiz_buy'].values - df['fiz_sell'].values).astype(np.float64)
        yur_net = (df['yur_buy'].values - df['yur_sell'].values).astype(np.float64)
    else:
        return None
    
    s_fiz = pd.Series(fiz_net)
    s_yur = pd.Series(yur_net)
    fiz_z = ((s_fiz - s_fiz.rolling(W).mean()) / s_fiz.rolling(W).std()).fillna(0).values
    yur_z = ((s_yur - s_yur.rolling(W).mean()) / s_yur.rolling(W).std()).fillna(0).values
    
    if variant == 1:
        div = fiz_z - yur_z
    else:
        div = yur_z * 2.0 - fiz_z
    
    sig = np.zeros(n)
    sig[div > T] = -1
    sig[div < -T] = 1
    sig[:W] = 0
    
    # GO
    go = go_info['go']
    lot = go_info['lot']
    stepprice = go_info['stepprice']
    minstep = go_info['minstep']
    price_to_rub = lot * stepprice / minstep if minstep > 0 else 1.0
    
    if go <= 0 or go > CAP:
        return None
    
    cur = float(CAP)
    pos = None
    trades = []
    eq = []
    
    for i in range(n):
        if i >= len(opens): break
        op, hi, lo, cl = opens[i], highs[i], lows[i], closes[i]
        
        if pos is not None:
            pos['bars'] += 1
            exit_reason = None
            exit_px = cl
            
            stop_pct = max(atr[i] / cl * 100 * 1.0, 1.0) / 100  # ATR*1.0 min 1%
            if pos['dir'] == 'LONG':
                if lo <= pos['entry'] * (1 - stop_pct):
                    exit_px = pos['entry'] * (1 - stop_pct)
                    exit_reason = 'stop'
            else:
                if hi >= pos['entry'] * (1 + stop_pct):
                    exit_px = pos['entry'] * (1 + stop_pct)
                    exit_reason = 'stop'
            
            if not exit_reason and pos['bars'] >= hold:
                exit_px = cl; exit_reason = 'time'
            
            if exit_reason:
                if pos['dir'] == 'LONG': ret = (exit_px - pos['entry']) * price_to_rub
                else: ret = (pos['entry'] - exit_px) * price_to_rub
                pnl = ret * 1 - COMM
                cur += pos['locked'] + pnl
                trades.append({'pnl': pnl})
                pos = None
        
        if pos is None and i > W and sig[i] != 0 and cur >= go:
            if sig[i] == 1: dir = 'LONG'
            else: dir = 'SHORT'
            pos = {'dir': dir, 'entry': op, 'bars': 0, 'locked': go}
            cur -= go
        
        eq_val = cur
        if pos is not None:
            if pos['dir'] == 'LONG': mtm = (cl - pos['entry']) * price_to_rub
            else: mtm = (pos['entry'] - cl) * price_to_rub
            eq_val += pos['locked'] + mtm
        eq.append(eq_val)
    
    if pos is not None:
        cl = closes[-1]
        if pos['dir'] == 'LONG': ret = (cl - pos['entry']) * price_to_rub
        else: ret = (pos['entry'] - cl) * price_to_rub
        pnl = ret * 1 - COMM
        cur += pos['locked'] + pnl
        trades.append({'pnl': pnl})
    
    rets = np.array([t['pnl'] for t in trades])
    wins = rets[rets > 0]
    losses = rets[rets <= 0]
    wr = len(wins) / len(rets) * 100 if len(rets) > 0 else 0
    total_ret = (cur / CAP - 1) * 100
    eq_arr = np.array(eq)
    peak = np.maximum.accumulate(eq_arr)
    dd_arr = (peak - eq_arr) / peak * 100
    max_dd = float(np.max(dd_arr)) if len(dd_arr) > 0 else 0.0
    calmar = total_ret / max_dd if max_dd > 1e-6 else 0.0
    
    return {
        'trades': len(trades), 'wr': round(wr, 1),
        'return_pct': round(total_ret, 1), 'dd_pct': round(max_dd, 1),
        'calmar': round(calmar, 3),
        'final_capital': round(cur, 0),
    }


def main():
    os.makedirs(OUT, exist_ok=True)
    
    results = []
    
    for ticker in tickers_to_test:
        go_info = get_go(ticker)
        if go_info is None:
            continue
        
        if ticker in always:
            start = None
            rec_label = "always"
        elif ticker in rec_map:
            start = rec_map[ticker]
            rec_label = start
        else:
            continue
        
        for tf_name, tf_cfg in TF_CONFIGS.items():
            print(f"\n{ticker:8s} {tf_name:4s} recovery={rec_label}...", end=" ")
            sys.stdout.flush()
            
            df = load_resampled(ticker, start, tf_cfg)
            if df is None or len(df) < 50:
                print(f"no data")
                continue
            
            # V1
            r1 = run_test(df, go_info, variant=1)
            if r1 and r1['trades'] >= 10 and r1['calmar'] > 0.5:
                results.append({'ticker': ticker, 'tf': tf_name, 'variant': 'V1', **r1})
                print(f"V1: ret={r1['return_pct']:.1f}% DD={r1['dd_pct']:.1f}% Cal={r1['calmar']:.2f} trades={r1['trades']}", end="")
            
            # V2
            r2 = run_test(df, go_info, variant=2)
            if r2 and r2['trades'] >= 10 and r2['calmar'] > 0.5:
                results.append({'ticker': ticker, 'tf': tf_name, 'variant': 'V2', **r2})
                print(f" | V2: ret={r2['return_pct']:.1f}% DD={r2['dd_pct']:.1f}% Cal={r2['calmar']:.2f} trades={r2['trades']}", end="")
            
            if (r1 is None or r1['calmar'] <= 0.5) and (r2 is None or r2['calmar'] <= 0.5):
                print(f"no edge", end="")
            
        print()
    
    # ── Results ──
    print(f"\n\n{'='*70}")
    print("  SWEEP RESULTS: all tickers × TF")
    print(f"{'='*70}")
    print(f"{'Ticker':<8} {'TF':<5} {'V':<4} {'Return%':>8} {'DD%':>6} {'Calmar':>8} {'Trades':>7}")
    print("-" * 55)
    
    for r in sorted(results, key=lambda x: x['calmar'], reverse=True):
        print(f"{r['ticker']:<8} {r['tf']:<5} {r['variant']:<4} {r['return_pct']:>7.1f}% {r['dd_pct']:>5.1f}% "
              f"{r['calmar']:>7.3f} {r['trades']:>6d}")
    
    if results:
        pd.DataFrame(results).to_csv(os.path.join(OUT, "phase3g_sweep_all.csv"), index=False)
        print(f"\n✅ Saved {len(results)} rows")
    
    print(f"\nDone")


if __name__ == "__main__":
    main()
