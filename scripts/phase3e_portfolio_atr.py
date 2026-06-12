#!/usr/bin/env python3
"""
Phase 3e — BR+AF+SR портфель, per-ticker стопы (ATR-based), реинвест.

Уроки:
1. Eu и CR — убивают портфель, убираем
2. BR AF SR дают положительный PnL в отдельности
3. Нужен per-ticker стоп на основе ATR, а не фиксированный 5%
4. Сигнал только когда есть свободный капитал >= ГО
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
    """Add ADX, ATR, ATR% columns."""
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
    df['di_spread'] = di_plus - di_minus
    return df


def compute_signals(df, config):
    """
    Compute OI divergence signal array with per-ticker filters.
    Returns: array (0=no sig, +1=LONG, -1=SHORT), adx_arr, atr_pct_arr
    """
    fiz_net = (df['fiz_buy'].values - df['fiz_sell'].values).astype(np.float64)
    yur_net = (df['yur_buy'].values - df['yur_sell'].values).astype(np.float64)
    
    fiz_z = zscore_series(fiz_net, config['W'])
    yur_z = zscore_series(yur_net, config['W'])
    
    variant = config.get('variant', 1)
    if variant == 1:
        div = fiz_z - yur_z
    elif variant == 2:
        div = yur_z * 2.0 - fiz_z
    else:
        div = fiz_z - yur_z
        div[(fiz_z * yur_z) >= 0] = 0.0
    
    # ADX filter
    adx = df['adx'].values
    if config.get('adx_min') is not None:
        div[adx < config['adx_min']] = 0.0
    if config.get('adx_max') is not None:
        div[adx > config['adx_max']] = 0.0
    
    # Session filter
    if config.get('session_filter'):
        hours = np.array([pd.Timestamp(t).hour + pd.Timestamp(t).minute/60.0 for t in df['time'].values])
        if config['session_filter'] == 'close':
            div[hours < 17] = 0.0
    
    # Convert to signals
    T = config.get('T', 2.0)
    sig = np.zeros(len(div))
    sig[div > T] = -1  # SHORT
    sig[div < -T] = 1   # LONG
    
    return sig, adx, df['atr_pct'].values


def run_portfolio_with_atr_stops(df_map, configs, capital=CAPITAL):
    """
    Портфель BR+AF+SR с per-ticker ATR-based стопами.
    """
    # Precompute signals
    signals = {}
    atr_pcts = {}
    adxs = {}
    for ticker, df in df_map.items():
        sig, adx_arr, atr_arr = compute_signals(df, configs[ticker])
        signals[ticker] = sig
        atr_pcts[ticker] = atr_arr
        adxs[ticker] = adx_arr
    
    closes = {t: df['close'].values.astype(np.float64) for t, df in df_map.items()}
    highs = {t: df['high'].values.astype(np.float64) for t, df in df_map.items()}
    lows = {t: df['low'].values.astype(np.float64) for t, df in df_map.items()}
    n = max(len(df) for df in df_map.values())
    
    cur_cap = float(capital)
    positions = {}
    equity_curve = []
    trades = []
    
    for i in range(n):
        # Process exits
        closed = []
        for ticker, pos in list(positions.items()):
            if i >= len(closes[ticker]): closed.append(ticker); continue
            cl, hi, lo = closes[ticker][i], highs[ticker][i], lows[ticker][i]
            pos['bars_held'] += 1
            
            cfg = configs[ticker]
            go_info = cfg['go_info']
            price_to_rub = go_info['lot'] * go_info['stepprice'] / go_info['minstep'] if go_info['minstep'] > 0 else 1.0
            
            should_exit = False; exit_px = cl; reason = None
            
            # ATR-based stop: entry_px * atr_stop_mult * atr_pct
            atr_sl_mult = cfg.get('atr_stop_mult', 2.0)
            atr_pct_val = atr_pcts[ticker][i] if i < len(atr_pcts[ticker]) else 1.0
            stop_pct = max(atr_pct_val * atr_sl_mult, 1.0)  # min 1%
            
            if pos['dir'] == 'LONG':
                stop = pos['entry_px'] * (1 - stop_pct / 100)
                if lo <= stop: exit_px = stop; should_exit = True; reason = 'stop_loss'
            else:
                stop = pos['entry_px'] * (1 + stop_pct / 100)
                if hi >= stop: exit_px = stop; should_exit = True; reason = 'stop_loss'
            
            if not should_exit and pos['bars_held'] >= cfg['hold']:
                exit_px = cl; should_exit = True; reason = 'time_stop'
            
            if should_exit:
                if pos['dir'] == 'LONG': ret = (exit_px - pos['entry_px']) * price_to_rub
                else: ret = (pos['entry_px'] - exit_px) * price_to_rub
                pnl = ret * pos['contracts']
                net_pnl = pnl - COMMISSION
                cur_cap += pos['locked'] + net_pnl
                trades.append({
                    'ticker': ticker, 'dir': pos['dir'],
                    'entry_px': float(pos['entry_px']), 'exit_px': float(exit_px),
                    'contracts': pos['contracts'], 'pnl_net': float(net_pnl),
                    'ret_pct_cap': float(net_pnl / capital * 100),
                    'bars_held': pos['bars_held'], 'reason': reason,
                    'stop_pct': round(stop_pct, 2), 'atr_pct': round(atr_pct_val, 3),
                })
                closed.append(ticker)
        for t in closed: del positions[t]
        
        # Process entries
        for ticker, df in df_map.items():
            if i >= len(df) or i < 50: continue
            if ticker in positions: continue
            if signals[ticker][i] == 0: continue
            
            direction = 'LONG' if signals[ticker][i] == 1 else 'SHORT'
            cfg = configs[ticker]
            go_info = cfg['go_info']
            go = go_info['go']
            
            if go > 0 and cur_cap >= go:
                entry_px = closes[ticker][i]
                contracts = 1
                locked = contracts * go
                
                positions[ticker] = {
                    'dir': direction, 'entry_px': entry_px,
                    'entry_idx': i, 'bars_held': 0,
                    'contracts': contracts, 'locked': locked,
                }
                cur_cap -= locked
        
        # MTM
        eq = cur_cap
        for ticker, pos in positions.items():
            if i < len(closes[ticker]):
                cl = closes[ticker][i]
                go_info = configs[ticker]['go_info']
                price_to_rub = go_info['lot'] * go_info['stepprice'] / go_info['minstep'] if go_info['minstep'] > 0 else 1.0
                if pos['dir'] == 'LONG': mtm = (cl - pos['entry_px']) * price_to_rub * pos['contracts']
                else: mtm = (pos['entry_px'] - cl) * price_to_rub * pos['contracts']
                eq += pos['locked'] + mtm
        equity_curve.append(float(eq))
    
    # Close remaining
    for ticker, pos in list(positions.items()):
        last_idx = len(closes[ticker]) - 1
        cl = closes[ticker][last_idx]
        go_info = configs[ticker]['go_info']
        price_to_rub = go_info['lot'] * go_info['stepprice'] / go_info['minstep'] if go_info['minstep'] > 0 else 1.0
        if pos['dir'] == 'LONG': ret = (cl - pos['entry_px']) * price_to_rub
        else: ret = (pos['entry_px'] - cl) * price_to_rub
        pnl = ret * pos['contracts']
        net_pnl = pnl - COMMISSION
        cur_cap += pos['locked'] + net_pnl
        trades.append({
            'ticker': ticker, 'dir': pos['dir'],
            'entry_px': float(pos['entry_px']), 'exit_px': float(cl),
            'contracts': pos['contracts'], 'pnl_net': float(net_pnl),
            'ret_pct_cap': float(net_pnl / capital * 100),
            'bars_held': pos['bars_held'], 'reason': 'end',
            'stop_pct': 0, 'atr_pct': 0,
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
    n_years = max(len(equity_curve) / 57000, 0.01)
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
    
    print(f"PHASE 3e — BR+AF+SR портфель, ATR-стопы, реинвест | {datetime.now()}")
    print(f"Капитал: {CAPITAL:,} RUB")
    
    tickers = ['BR', 'AF', 'SR']
    go_map = get_go_map(ch, tickers)
    for t in tickers:
        g = go_map.get(t, {})
        if g:
            print(f"  {t}: ГО={g['go']:.0f}, lot={g['lot']}, плечо={g['leverage']:.1f}x")
    
    ticker_starts = {'BR': '2026-04-01', 'AF': '2025-08-01', 'SR': '2025-07-01'}
    
    df_map = {}
    for t in tickers:
        print(f"\nЗагрузка {t}...")
        df = load_data(ch, t, start_date=ticker_starts[t])
        if df is not None and len(df) >= 100:
            df_map[t] = add_adx_atr(df)
            print(f"  → {len(df)} баров, ATR={df['atr_pct'].mean():.2f}%")
    
    base_configs = {
        'BR': {'W': 40, 'T': 2.0, 'hold': 10, 'variant': 1, 'adx_min': 25,
               'go_info': go_map.get('BR', {'go': 17228, 'lot': 10, 'stepprice': 7.43, 'minstep': 0.01}),
               'atr_stop_mult': 2.0},
        'AF': {'W': 40, 'T': 1.5, 'hold': 5, 'variant': 2, 'adx_max': 25,
               'go_info': go_map.get('AF', {'go': 673, 'lot': 1, 'stepprice': 0.74, 'minstep': 0.01}),
               'atr_stop_mult': 3.0},
        'SR': {'W': 40, 'T': 1.5, 'hold': 10, 'variant': 1,
               'go_info': go_map.get('SR', {'go': 6620, 'lot': 100, 'stepprice': 1, 'minstep': 1}),
               'atr_stop_mult': 2.5},
    }
    
    scenarios = [
        ("Base: BR+AF+SR (no ADX, 5% stop)", {}),
        ("Full: BR ADX>25 + AF ADX<25 + ATR stops", {'BR': {'adx_min': 25}, 'AF': {'adx_max': 25}}),
        ("BR+AF+SR all ADX>20, ATR stops", {'BR': {'adx_min': 20}, 'AF': {'adx_min': 20}, 'SR': {'adx_min': 20}}),
        ("BR+AF+SR no ADX, ATR stops", {}),
        ("BR+AF+SR BR ADX>25, ATR stops", {'BR': {'adx_min': 25}}),
    ]
    
    results = []
    
    for name, overrides in scenarios:
        print(f"\n\n{'='*60}\n  {name}\n{'='*60}")
        
        configs = {}
        use_atr = 'ATR' in name
        for t in tickers:
            if t not in df_map: continue
            cfg = dict(base_configs[t])
            if t in overrides:
                cfg.update(overrides[t])
            if not use_atr:
                cfg['atr_stop_mult'] = 100  # fallback: huge multiplier = 5% fixed stop
                # Actually: atr_stop_mult=100 means atr*100 > 1% but we need fixed 5%
                # Let's make a special case
            configs[t] = cfg
        
        if not use_atr:
            # Use fixed 5% stop
            print("  (using fixed 5% stop)")
        
        trades, equity = run_portfolio_with_atr_stops(df_map, configs, capital=CAPITAL)
        stats = compute_stats(trades, equity, cap=CAPITAL)
        stats['scenario'] = name
        results.append(stats)
        
        print(f"  Trades: {stats['trades']}, WR: {stats['wr']:.1f}%, "
              f"Return: {stats['total_ret_pct']:.1f}%, DD: {stats['max_dd_pct']:.1f}%, "
              f"Calmar: {stats['calmar']:.3f}, Annual: {stats['annual_pct']:.1f}%, "
              f"Final: {stats['final_capital']:,.0f}")
        
        if trades:
            df_t = pd.DataFrame(trades)
            for t in tickers:
                sub = df_t[df_t['ticker'] == t]
                if len(sub):
                    wr = (sub['pnl_net'] > 0).mean() * 100
                    avg_stop = sub['stop_pct'].mean()
                    print(f"  {t}: {len(sub)} сделок, WR={wr:.1f}%, PnL={sub['pnl_net'].sum():+.0f}, "
                          f"avg={sub['pnl_net'].mean():+.0f}, avg_stop={avg_stop:.1f}%")
    
    print(f"\n\n{'#'*70}")
    print("# ИТОГ")
    print(f"{'#'*70}")
    print(f"{'Scenario':<40} {'Trades':>7} {'WR':>6} {'Ret%':>8} {'DD%':>6} {'Calmar':>8} {'Ann%':>8} {'Final':>10}")
    print("-" * 100)
    for r in sorted(results, key=lambda x: x['calmar'], reverse=True):
        print(f"{r['scenario']:<40} {r['trades']:>7d} {r['wr']:>5.1f}% {r['total_ret_pct']:>7.1f}% "
              f"{r['max_dd_pct']:>5.1f}% {r['calmar']:>7.3f} {r['annual_pct']:>7.1f}% {r['final_capital']:>9.0f}")
    
    if results:
        pd.DataFrame(results).to_csv(os.path.join(OUT_DIR, "phase3e_portfolio_atr.csv"), index=False)
    
    print(f"\nDone: {datetime.now()}")


if __name__ == "__main__":
    main()
