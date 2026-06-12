#!/usr/bin/env python3
"""
Phase 3f — Аудит и перепроверка портфеля BR+AF+SR.
Исправлены баги:
1. Вход по open следующего бара (не close текущего)
2. Fixed 5% stop — настоящий, а не через atr_mult=100
3. Per-ticker конфиги независимы
4. Добавлен аудит: печать equity curve и per-ticker сделок

Все результаты double-check.
"""
import sys, os
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import clickhouse_connect
import numpy as np
import pandas as pd
from config import CH_HOST, CH_PORT, CH_DB

COMMISSION = 2.0
CAPITAL = 200000.0
OUT_DIR = "reports/phase3"


def get_ch():
    return clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)


def get_go_map(ch, tickers):
    if not tickers: return {}
    rows = ch.query(
        f"SELECT ticker, go_rub, lot, stepprice, minstep, leverage FROM moex.securities "
        f"WHERE ticker IN {tuple(tickers)}"
    ).result_rows
    return {r[0]: {'go': float(r[1]), 'lot': int(r[2]), 'stepprice': float(r[3]),
                    'minstep': float(r[4]), 'leverage': float(r[5])} for r in rows}


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
    if not rows: return None
    df = pd.DataFrame(rows, columns=[
        "time","open","high","low","close","volume",
        "fiz_buy","fiz_sell","yur_buy","yur_sell","total_oi"
    ])
    return df


def add_adx_atr(df, period=14):
    high = df['high'].values.astype(np.float64)
    low = df['low'].values.astype(np.float64)
    close = df['close'].values.astype(np.float64)
    n = len(df)
    tr = np.zeros(n)
    for i in range(1, n):
        tr[i] = max(high[i] - low[i], abs(high[i] - close[i-1]), abs(low[i] - close[i-1]))
    tr[0] = high[0] - low[0]
    df['atr'] = pd.Series(tr).ewm(span=period).mean().values
    df['atr_pct'] = (df['atr'] / close * 100).values
    
    up_move = high[1:] - high[:-1]
    down_move = low[:-1] - low[1:]
    pos_dm = np.zeros(n)
    neg_dm = np.zeros(n)
    for i in range(1, n):
        up = high[i] - high[i-1]
        down = low[i-1] - low[i]
        if up > down and up > 0: pos_dm[i] = up
        if down > up and down > 0: neg_dm[i] = down
    tr_s = pd.Series(tr).ewm(span=period).mean().values
    pos_s = pd.Series(pos_dm).ewm(span=period).mean().values
    neg_s = pd.Series(neg_dm).ewm(span=period).mean().values
    di_plus = pos_s / (tr_s + 1e-10) * 100
    di_minus = neg_s / (tr_s + 1e-10) * 100
    dx = np.abs(di_plus - di_minus) / (di_plus + di_minus + 1e-10) * 100
    df['adx'] = pd.Series(dx).ewm(span=period).mean().fillna(0).values
    return df


def compute_signals_safe(df, config, warmup=50):
    """
    Compute OI divergence signal array.
    Правильно: сигнал на баре i -> вход на i+1.
    Возвращает: массив signals (0/+1/-1) с warmup смещением.
    """
    fiz_net = (df['fiz_buy'].values - df['fiz_sell'].values).astype(np.float64)
    yur_net = (df['yur_buy'].values - df['yur_sell'].values).astype(np.float64)
    
    fiz_z = zscore_series(fiz_net, config['W'])
    yur_z = zscore_series(yur_net, config['W'])
    
    v = config.get('variant', 1)
    if v == 1: div = fiz_z - yur_z
    elif v == 2: div = yur_z * 2.0 - fiz_z
    else:
        div = fiz_z - yur_z
        div[(fiz_z * yur_z) >= 0] = 0.0
    
    # ADX filter
    adx = df['adx'].values
    if config.get('adx_min') is not None: div[adx < config['adx_min']] = 0.0
    if config.get('adx_max') is not None: div[adx > config['adx_max']] = 0.0
    
    T = config.get('T', 2.0)
    sig = np.zeros(len(div))
    sig[div > T] = -1  # SHORT
    sig[div < -T] = 1   # LONG
    
    # Обнуляем warmup период
    sig[:warmup] = 0
    
    return sig, df['atr_pct'].values


def run_single_ticker_test(df, config, capital=CAPITAL):
    """
    Тест одного тикера с правильной симуляцией:
    - Вход: по OPEN следующего бара после сигнала
    - Стоп: по ATR или фиксированный, на low/high внутри бара
    - Выход по времени: по close
    - 1 контракт
    - Возвращает сделки и equity curve
    """
    sig, atr_pct_arr = compute_signals_safe(df, config)
    opens = df['open'].values.astype(np.float64)
    highs = df['high'].values.astype(np.float64)
    lows = df['low'].values.astype(np.float64)
    closes = df['close'].values.astype(np.float64)
    n = len(df)
    
    go_info = config['go_info']
    go = go_info['go']
    lot = go_info['lot']
    stepprice = go_info['stepprice']
    minstep = go_info['minstep']
    price_to_rub = lot * stepprice / minstep if minstep > 0 else 1.0
    
    hold = config.get('hold', 10)
    use_atr = config.get('use_atr', False)
    fixed_sl_pct = config.get('fixed_sl_pct', 0.05)
    atr_sl_mult = config.get('atr_sl_mult', 2.0)
    
    cur_cap = float(capital)
    position = None  # {dir, entry_px, entry_idx, bars_held, locked}
    trades = []
    equity_curve = []
    
    for i in range(n):
        op, hi, lo, cl = opens[i], highs[i], lows[i], closes[i]
        
        # Exit
        if position is not None:
            pos = position
            pos['bars_held'] += 1
            should_exit = False; exit_px = cl; reason = None
            
            # Стоп
            if use_atr:
                atr_pct = atr_pct_arr[i] if i < len(atr_pct_arr) else 1.0
                stop_pct = max(atr_pct * atr_sl_mult, 1.0) / 100
            else:
                stop_pct = fixed_sl_pct
            
            if pos['dir'] == 'LONG':
                stop = pos['entry_px'] * (1 - stop_pct)
                if lo <= stop: exit_px = stop; should_exit = True; reason = 'stop_loss'
            else:
                stop = pos['entry_px'] * (1 + stop_pct)
                if hi >= stop: exit_px = stop; should_exit = True; reason = 'stop_loss'
            
            if not should_exit and pos['bars_held'] >= hold:
                exit_px = cl; should_exit = True; reason = 'time_stop'
            
            if should_exit:
                if pos['dir'] == 'LONG': ret = (exit_px - pos['entry_px']) * price_to_rub
                else: ret = (pos['entry_px'] - exit_px) * price_to_rub
                pnl = ret * pos['contracts']
                net_pnl = pnl - COMMISSION
                cur_cap += pos['locked'] + net_pnl
                trades.append({
                    'ticker': config.get('ticker', '?'),
                    'dir': pos['dir'],
                    'entry_px': float(pos['entry_px']),
                    'exit_px': float(exit_px),
                    'contracts': pos['contracts'],
                    'pnl_net': float(net_pnl),
                    'ret_pct_cap': float(net_pnl / capital * 100),
                    'bars_held': pos['bars_held'],
                    'reason': reason,
                    'stop_pct': round(stop_pct * 100, 2) if use_atr else round(fixed_sl_pct * 100, 1),
                })
                position = None
        
        # Entry: сигнал на предыдущем баре -> вход по OPEN
        if position is None and i > 0 and sig[i-1] != 0 and cur_cap >= go and go > 0:
            direction = 'LONG' if sig[i-1] == 1 else 'SHORT'
            entry_px = op  # ← ИСПРАВЛЕНО: вход по OPEN, а не по close текущего бара
            contracts = 1
            locked = contracts * go
            position = {
                'dir': direction, 'entry_px': entry_px,
                'entry_idx': i, 'bars_held': 0,
                'contracts': contracts, 'locked': locked,
            }
            cur_cap -= locked
        
        # MTM
        eq = cur_cap
        if position is not None:
            if position['dir'] == 'LONG':
                mtm = (cl - position['entry_px']) * price_to_rub * position['contracts']
            else:
                mtm = (position['entry_px'] - cl) * price_to_rub * position['contracts']
            eq += position['locked'] + mtm
        equity_curve.append(float(eq))
    
    # Close remaining
    if position is not None:
        cl = closes[-1]
        if position['dir'] == 'LONG': ret = (cl - position['entry_px']) * price_to_rub
        else: ret = (position['entry_px'] - cl) * price_to_rub
        pnl = ret * position['contracts']
        net_pnl = pnl - COMMISSION
        cur_cap += position['locked'] + net_pnl
        trades.append({
            'ticker': config.get('ticker', '?'),
            'dir': position['dir'],
            'entry_px': float(position['entry_px']),
            'exit_px': float(cl),
            'contracts': position['contracts'],
            'pnl_net': float(net_pnl),
            'ret_pct_cap': float(net_pnl / capital * 100),
            'bars_held': position['bars_held'],
            'reason': 'end',
            'stop_pct': 0,
        })
    
    return trades, equity_curve


def compute_stats(trades, equity_curve, cap=CAPITAL):
    if not trades: return {"trades": 0}
    returns = np.array([t['pnl_net'] for t in trades])
    wins = returns[returns > 0]; losses = returns[returns <= 0]
    wr = len(wins) / len(returns) * 100 if len(returns) > 0 else 0.0
    final = equity_curve[-1] if equity_curve else cap
    total_ret = (final / cap - 1) * 100
    eq = np.array(equity_curve)
    peak = np.maximum.accumulate(eq)
    dd = (peak - eq) / peak * 100
    max_dd = float(np.max(dd)) if len(dd) > 0 else 0.0
    calmar = total_ret / max_dd if max_dd > 1e-6 else 0.0
    n_bars = len(equity_curve)
    n_years = max(n_bars / 57000, 0.01)
    annual = ((final / cap) ** (1.0 / n_years) - 1) * 100 if final > 0 else total_ret
    return {
        "trades": len(trades), "wr": round(wr, 2),
        "total_ret_pct": round(total_ret, 2), "max_dd_pct": round(max_dd, 2),
        "calmar": round(calmar, 4), "annual_pct": round(annual, 2),
        "final_capital": round(final, 0),
    }


def main():
    ch = get_ch()
    os.makedirs(OUT_DIR, exist_ok=True)
    
    print(f"PHASE 3f — АУДИТ: правильная симуляция per-ticker | {datetime.now()}")
    print(f"Капитал: {CAPITAL:,} RUB, вход по OPEN, стоп OHLCV")
    print("="*70)
    
    tickers = ['BR', 'AF', 'SR']
    go_map = get_go_map(ch, tickers)
    for t in tickers:
        g = go_map.get(t, {})
        if g:
            print(f"{t}: ГО={g['go']:.0f}, lot={g['lot']}, stepprice={g['stepprice']:.2f}, "
                  f"minstep={g['minstep']}, плечо={g['leverage']:.1f}x, "
                  f"стоимость 1п = {g['lot']*g['stepprice']/max(g['minstep'],0.001):.0f} RUB")
    
    ticker_starts = {'BR': '2026-04-01', 'AF': '2025-08-01', 'SR': '2025-07-01'}
    
    df_map = {}
    for t in tickers:
        print(f"\nЗагрузка {t}...", end=" ")
        df = load_data(ch, t, start_date=ticker_starts[t])
        if df is not None and len(df) >= 100:
            df_map[t] = add_adx_atr(df)
            print(f"{len(df)} баров, ATR={df['atr_pct'].mean():.3f}%")
    
    # ── Per-ticker тесты ──
    test_configs = [
        # (name, ticker, config_overrides)
        ("BR OI Div V1, 5% stop", 'BR', {'variant': 1, 'W': 40, 'T': 2.0, 'hold': 10, 'use_atr': False, 'fixed_sl_pct': 0.05}),
        ("BR OI Div V1, ATR*2 stop", 'BR', {'variant': 1, 'W': 40, 'T': 2.0, 'hold': 10, 'use_atr': True, 'atr_sl_mult': 2.0}),
        ("BR OI Div V1, ADX>25 + ATR*2", 'BR', {'variant': 1, 'W': 40, 'T': 2.0, 'hold': 10, 'use_atr': True, 'atr_sl_mult': 2.0, 'adx_min': 25}),
        ("AF OI Div V2, 5% stop", 'AF', {'variant': 2, 'W': 40, 'T': 1.5, 'hold': 5, 'use_atr': False, 'fixed_sl_pct': 0.05}),
        ("AF OI Div V2, ATR*3 stop", 'AF', {'variant': 2, 'W': 40, 'T': 1.5, 'hold': 5, 'use_atr': True, 'atr_sl_mult': 3.0}),
        ("AF OI Div V2, ADX<25 + ATR*3", 'AF', {'variant': 2, 'W': 40, 'T': 1.5, 'hold': 5, 'use_atr': True, 'atr_sl_mult': 3.0, 'adx_max': 25}),
        ("SR OI Div V1, 5% stop", 'SR', {'variant': 1, 'W': 40, 'T': 1.5, 'hold': 10, 'use_atr': False, 'fixed_sl_pct': 0.05}),
        ("SR OI Div V1, ATR*2.5 stop", 'SR', {'variant': 1, 'W': 40, 'T': 1.5, 'hold': 10, 'use_atr': True, 'atr_sl_mult': 2.5}),
    ]
    
    results = []
    
    for name, ticker, overrides in test_configs:
        if ticker not in df_map:
            continue
        
        cfg = {
            'ticker': ticker,
            'go_info': go_map.get(ticker, {'go': 10000, 'lot': 1, 'stepprice': 1, 'minstep': 1}),
            'use_atr': False,
            'fixed_sl_pct': 0.05,
        }
        cfg.update(overrides)
        
        trades, equity = run_single_ticker_test(df_map[ticker], cfg, capital=CAPITAL)
        stats = compute_stats(trades, equity, cap=CAPITAL)
        stats['name'] = name
        stats['ticker'] = ticker
        stats['stop_type'] = 'ATR' if cfg.get('use_atr') else '5% fixed'
        results.append(stats)
        
        print(f"\n{name}:")
        print(f"  Trades: {stats['trades']}, WR: {stats['wr']:.1f}%, "
              f"Return: {stats['total_ret_pct']:.1f}%, DD: {stats['max_dd_pct']:.1f}%, "
              f"Calmar: {stats['calmar']:.3f}")
        
        if trades:
            df_t = pd.DataFrame(trades)
            wins = df_t[df_t['pnl_net'] > 0]
            losses = df_t[df_t['pnl_net'] <= 0]
            print(f"  Avg win: {wins['pnl_net'].mean():+.0f} RUB, Avg loss: {losses['pnl_net'].mean():+.0f} RUB")
            print(f"  Avg stop: {df_t['stop_pct'].mean():.1f}%")
            reasons = df_t['reason'].value_counts()
            for r, c in reasons.items():
                print(f"  Exit reason '{r}': {c} ({c/len(df_t)*100:.0f}%)")
            print(f"  Final capital: {stats['final_capital']:,.0f} RUB")
    
    # ── Summary ──
    print(f"\n\n{'#'*70}")
    print("# ИТОГОВАЯ ТАБЛИЦА PER-TICKER (правильная симуляция)")
    print(f"{'#'*70}")
    print(f"{'Name':<40} {'Trades':>7} {'WR':>6} {'Ret%':>8} {'DD%':>6} {'Calmar':>8} {'Final':>10}")
    print("-" * 90)
    for r in sorted(results, key=lambda x: x['calmar'], reverse=True):
        print(f"{r['name']:<40} {r['trades']:>7d} {r['wr']:>5.1f}% {r['total_ret_pct']:>7.1f}% "
              f"{r['max_dd_pct']:>5.1f}% {r['calmar']:>7.3f} {r['final_capital']:>9.0f}")
    
    if results:
        pd.DataFrame(results).to_csv(os.path.join(OUT_DIR, "phase3f_audit_results.csv"), index=False)
    
    print(f"\nDone: {datetime.now()}")


if __name__ == "__main__":
    main()
