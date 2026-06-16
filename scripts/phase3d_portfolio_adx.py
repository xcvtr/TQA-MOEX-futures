#!/usr/bin/env python3
"""
Phase 3d — Per-ticker ADX-фильтр, портфель минимальным лотом, реинвест.

Гипотеза из анализа (phase3c_divergence_behavior.py):
- BR OI Div V1: работает в тренде (ADX>25), WR=54%
- AF OI Div V2: работает в боковике (ADX<20-25), WR=49%
- IMOEXF OI Div V1: mid-session эффект
- SR OI Div V1: стабильно WR~51%

Портфельный подход:
1. Каждый тикер — 1 контракт (минимальный лот через ГО)
2. Per-ticker ADX-фильтр
3. Реинвест всей прибыли в новые лоты
4. Капитал 200K
5. Оценка: годовая доходность, DD, Calmar
"""
import sys, os
from datetime import datetime
from itertools import product

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import clickhouse_connect
import numpy as np
import pandas as pd
from config import CH_HOST, CH_PORT, CH_DB

SL_PCT = 0.05
COMMISSION = 2.0
CAPITAL = 200000.0
OUT_DIR = "reports/phase3"


def get_ch():
    return clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)


def get_go_map(ch, tickers):
    if not tickers:
        return {}
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
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=[
        "time","open","high","low","close","volume",
        "fiz_buy","fiz_sell","yur_buy","yur_sell","total_oi"
    ])
    return df


def add_adx(df, period=14):
    """Add ADX as a column."""
    high = df['high'].values.astype(np.float64)
    low = df['low'].values.astype(np.float64)
    close = df['close'].values.astype(np.float64)
    n = len(df)
    
    tr = np.zeros(n)
    for i in range(1, n):
        tr[i] = max(high[i] - low[i], abs(high[i] - close[i-1]), abs(low[i] - close[i-1]))
    tr[0] = high[0] - low[0]
    
    up_move = high[1:] - high[:-1]
    down_move = low[:-1] - low[1:]
    pos_dm = np.zeros(n)
    neg_dm = np.zeros(n)
    for i in range(1, n):
        up = high[i] - high[i-1]
        down = low[i-1] - low[i]
        if up > down and up > 0:
            pos_dm[i] = up
        if down > up and down > 0:
            neg_dm[i] = down
    
    tr_s = pd.Series(tr).ewm(span=period).mean().values
    pos_s = pd.Series(pos_dm).ewm(span=period).mean().values
    neg_s = pd.Series(neg_dm).ewm(span=period).mean().values
    
    di_plus = pos_s / (tr_s + 1e-10) * 100
    di_minus = neg_s / (tr_s + 1e-10) * 100
    dx = np.abs(di_plus - di_minus) / (di_plus + di_minus + 1e-10) * 100
    df['adx'] = pd.Series(dx).ewm(span=period).mean().fillna(0).values
    return df


def compute_signal(df, W, variant, adx_min=None, adx_max=None, session_filter=None):
    """
    Compute OI divergence with ADX filter.
    Returns array of signal values: 0 = no signal, +1 = LONG, -1 = SHORT
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
    
    # ADX filter
    if adx_min is not None:
        div[(df['adx'].values < adx_min)] = 0.0
    if adx_max is not None:
        div[(df['adx'].values > adx_max)] = 0.0
    
    # Session filter
    if session_filter:
        times = df['time'].values
        hours = np.array([pd.Timestamp(t).hour + pd.Timestamp(t).minute/60.0 for t in times])
        if session_filter == 'mid':
            div[(hours < 12) | (hours >= 17)] = 0.0
        elif session_filter == 'close':
            div[hours < 17] = 0.0
        elif session_filter == 'open':
            div[hours >= 12] = 0.0
    
    return div


def run_portfolio_test(df_map, ticker_configs, capital=CAPITAL, sl_pct=SL_PCT, commission=COMMISSION):
    """
    Портфельный тест с минимальным лотом (1 контракт) и реинвестом.
    
    df_map: dict ticker -> df (с колонкой adx)
    ticker_configs: dict {
        ticker: {W, T, hold, variant, adx_min, adx_max, session_filter, go_info}
    }
    
    Алгоритм:
    - На каждом баре смотрим все тикеры
    - Если есть свободный капитал ≥ ГО тикера, открываем 1 контракт
    - Каждая позиция управляется отдельно (стоп, time stop, MTM)
    - Прибыль/убыток сразу влияет на капитал (реинвест)
    """
    # Precompute signals for all tickers
    signals = {}
    for ticker, df in df_map.items():
        cfg = ticker_configs[ticker]
        div = compute_signal(df, cfg['W'], cfg['variant'],
                              cfg.get('adx_min'), cfg.get('adx_max'),
                              cfg.get('session_filter'))
        signals[ticker] = div
    
    closes = {t: df['close'].values.astype(np.float64) for t, df in df_map.items()}
    highs = {t: df['high'].values.astype(np.float64) for t, df in df_map.items()}
    lows = {t: df['low'].values.astype(np.float64) for t, df in df_map.items()}
    n = max(len(df) for df in df_map.values())
    
    cur_cap = float(capital)
    positions = {}  # ticker -> {dir, entry_px, entry_idx, bars_held, contracts}
    equity_curve = []
    trades = []
    
    for i in range(n):
        # Process exits first
        closed_tickers = []
        for ticker, pos in list(positions.items()):
            if i >= len(closes[ticker]):
                closed_tickers.append(ticker)
                continue
            
            cl, hi, lo = closes[ticker][i], highs[ticker][i], lows[ticker][i]
            pos['bars_held'] += 1
            should_exit = False
            exit_px = cl
            reason = None
            
            cfg = ticker_configs[ticker]
            go_info = cfg['go_info']
            
            if pos['dir'] == 'LONG':
                stop = pos['entry_px'] * (1 - sl_pct)
                if lo <= stop:
                    exit_px = stop
                    should_exit = True
                    reason = 'stop_loss'
            else:
                stop = pos['entry_px'] * (1 + sl_pct)
                if hi >= stop:
                    exit_px = stop
                    should_exit = True
                    reason = 'stop_loss'
            
            if not should_exit and pos['bars_held'] >= cfg['hold']:
                exit_px = cl
                should_exit = True
                reason = 'time_stop'
            
            if should_exit:
                price_to_rub = go_info['lot'] * go_info['stepprice'] / go_info['minstep'] if go_info['minstep'] > 0 else 1.0
                if pos['dir'] == 'LONG':
                    ret = (exit_px - pos['entry_px']) * price_to_rub
                else:
                    ret = (pos['entry_px'] - exit_px) * price_to_rub
                pnl = ret * pos['contracts']
                net_pnl = pnl - commission
                cur_cap += pos['locked'] + net_pnl
                
                trades.append({
                    'ticker': ticker,
                    'dir': pos['dir'],
                    'entry_px': float(pos['entry_px']),
                    'exit_px': float(exit_px),
                    'contracts': pos['contracts'],
                    'pnl_net': float(net_pnl),
                    'ret_pct_capital': float(net_pnl / capital * 100),
                    'bars_held': pos['bars_held'],
                    'reason': reason,
                    'entry_bar': pos['entry_idx'],
                    'exit_bar': i,
                })
                closed_tickers.append(ticker)
        
        for t in closed_tickers:
            del positions[t]
        
        # Process entries for all tickers
        for ticker, df in df_map.items():
            if i >= len(df) or i < 20:  # skip first bars for indicator warmup
                continue
            if ticker in positions:
                continue  # already have position
            
            sig = signals[ticker][i]
            direction = None
            if sig > 0:
                direction = 'SHORT'
            elif sig < 0:
                direction = 'LONG'
            
            if direction:
                cfg = ticker_configs[ticker]
                go_info = cfg['go_info']
                go = go_info['go']
                
                if go > 0 and cur_cap >= go:
                    entry_px = closes[ticker][i]
                    contracts = 1  # exactly 1 contract
                    locked = contracts * go
                    price_to_rub = go_info['lot'] * go_info['stepprice'] / go_info['minstep'] if go_info['minstep'] > 0 else 1.0
                    
                    # Calculate notional exposure
                    notional_per_contract = entry_px * price_to_rub
                    
                    positions[ticker] = {
                        'dir': direction,
                        'entry_px': entry_px,
                        'entry_idx': i,
                        'bars_held': 0,
                        'contracts': contracts,
                        'locked': locked,
                        'notional': notional_per_contract,
                    }
                    cur_cap -= locked
        
        # MTM equity
        eq = cur_cap
        for ticker, pos in positions.items():
            if i < len(closes[ticker]):
                cl = closes[ticker][i]
                cfg = ticker_configs[ticker]
                go_info = cfg['go_info']
                price_to_rub = go_info['lot'] * go_info['stepprice'] / go_info['minstep'] if go_info['minstep'] > 0 else 1.0
                
                if pos['dir'] == 'LONG':
                    mtm = (cl - pos['entry_px']) * price_to_rub * pos['contracts']
                else:
                    mtm = (pos['entry_px'] - cl) * price_to_rub * pos['contracts']
                eq += pos['locked'] + mtm
        equity_curve.append(float(eq))
    
    # Close any remaining positions at end of data
    for ticker, pos in list(positions.items()):
        last_idx = len(closes[ticker]) - 1
        cl = closes[ticker][last_idx]
        cfg = ticker_configs[ticker]
        go_info = cfg['go_info']
        price_to_rub = go_info['lot'] * go_info['stepprice'] / go_info['minstep'] if go_info['minstep'] > 0 else 1.0
        
        if pos['dir'] == 'LONG':
            ret = (cl - pos['entry_px']) * price_to_rub
        else:
            ret = (pos['entry_px'] - cl) * price_to_rub
        pnl = ret * pos['contracts']
        net_pnl = pnl - commission
        cur_cap += pos['locked'] + net_pnl
        trades.append({
            'ticker': ticker, 'dir': pos['dir'],
            'entry_px': float(pos['entry_px']), 'exit_px': float(cl),
            'contracts': pos['contracts'], 'pnl_net': float(net_pnl),
            'ret_pct_capital': float(net_pnl / capital * 100),
            'bars_held': pos['bars_held'], 'reason': 'end',
        })
    
    return trades, equity_curve


def compute_stats(trades, equity_curve, cap=CAPITAL):
    if not trades:
        return {"trades": 0}
    returns = np.array([t['pnl_net'] for t in trades])
    
    # Per-ticker stats
    tickers = set(t['ticker'] for t in trades)
    
    wins = returns[returns > 0]
    losses = returns[returns <= 0]
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
    if final > 0:
        annual = ((final / cap) ** (1.0 / n_years) - 1) * 100
    else:
        annual = total_ret
    
    return {
        "trades": len(trades),
        "wr": round(wr, 2),
        "total_ret_pct": round(total_ret, 2),
        "max_dd_pct": round(max_dd, 2),
        "calmar": round(calmar, 4),
        "annual_pct": round(annual, 2),
        "final_capital": round(final, 0),
    }


def main():
    ch = get_ch()
    os.makedirs(OUT_DIR, exist_ok=True)
    
    print(f"PHASE 3d — Per-ticker ADX-фильтр + портфель + реинвест | {datetime.now()}")
    print(f"Капитал: {CAPITAL:,} RUB, стоп={SL_PCT*100:.0f}%")
    
    all_tickers = ['BR', 'AF', 'IMOEXF', 'SR', 'Eu', 'CR']
    go_map = get_go_map(ch, all_tickers)
    
    print("\nГО:")
    for t in all_tickers:
        if t in go_map:
            g = go_map[t]
            print(f"  {t}: ГО={g['go']:.0f} RUB, lot={g['lot']}, stepprice={g['stepprice']}, minstep={g['minstep']}, плечо={g['leverage']:.1f}x")
    
    # ── Load data ──
    ticker_starts = {
        'BR': '2026-04-01', 'IMOEXF': '2024-07-01',
        'AF': '2025-08-01', 'SR': '2025-07-01',
        'Eu': '2022-10-01', 'CR': None,
    }
    
    df_map = {}
    for ticker, start in ticker_starts.items():
        print(f"\nЗагрузка {ticker}...")
        df = load_data(ch, ticker, start_date=start)
        if df is None or len(df) < 100:
            print(f"  ⚠ нет данных")
            continue
        df = add_adx(df)
        df_map[ticker] = df
        print(f"  → {len(df)} баров, ADX range: {df['adx'].min():.1f}-{df['adx'].max():.1f}")
    
    # ── Per-ticker best parameters (из phase 3) ──
    # Base configs (no filter)
    base_configs = {
        'BR': {'W': 40, 'T': 2.0, 'hold': 10, 'variant': 1, 'go_info': go_map.get('BR', {'go': 17228, 'lot': 10, 'stepprice': 7.43, 'minstep': 0.01})},
        'AF': {'W': 40, 'T': 1.5, 'hold': 5, 'variant': 2, 'go_info': go_map.get('AF', {'go': 673, 'lot': 1, 'stepprice': 0.74, 'minstep': 0.01})},
        'IMOEXF': {'W': 20, 'T': 2.0, 'hold': 10, 'variant': 1, 'go_info': go_map.get('IMOEXF', {'go': 2596, 'lot': 10, 'stepprice': 5, 'minstep': 0.5})},
        'SR': {'W': 40, 'T': 1.5, 'hold': 10, 'variant': 1, 'go_info': go_map.get('SR', {'go': 6620, 'lot': 100, 'stepprice': 1, 'minstep': 1})},
        'Eu': {'W': 20, 'T': 1.5, 'hold': 10, 'variant': 1, 'go_info': go_map.get('Eu', {'go': 14478, 'lot': 1000, 'stepprice': 1, 'minstep': 1})},
        'CR': {'W': 40, 'T': 1.5, 'hold': 10, 'variant': 1, 'go_info': go_map.get('CR', {'go': 5000, 'lot': 1, 'stepprice': 1, 'minstep': 0.01})},
    }
    
    # ── Test scenarios ──
    scenarios = [
        # (name, {ticker: {override_params}})
        ("Base (no filter)", {}),
        
        # BR только ADX>25, AF ADX<25 (комплементарные)
        ("BR:ADX>25 + AF:ADX<25", {
            'BR': {'adx_min': 25},
            'AF': {'adx_max': 25},
        }),
        
        # Все с ADX>25
        ("All ADX>25", {t: {'adx_min': 25} for t in all_tickers}),
        
        # Все с ADX>20
        ("All ADX>20", {t: {'adx_min': 20} for t in all_tickers}),
        
        # BR ADX>25, AF ADX<25, IMOEXF mid-session
        ("BR:ADX>25 + AF:ADX<25 + IMOEXF:mid", {
            'BR': {'adx_min': 25},
            'AF': {'adx_max': 25},
            'IMOEXF': {'session_filter': 'mid', 'W': 20, 'T': 2.0},
        }),
        
        # BR + AF + SR — только те, что дают положительный PnL
        ("BR+AF+SR (no Eu/CR)", {
            'BR': {},
            'AF': {},
            'SR': {},
        }),
        
        # BR+AF+SR с ADX фильтрацией
        ("BR+AF+SR + ADX", {
            'BR': {'adx_min': 25},
            'AF': {'adx_max': 25},  # AF в боковике
            'SR': {},
        }),
        
        # BR+AF+SR все с ADX>20
        ("BR+AF+SR all ADX>20", {
            'BR': {'adx_min': 20},
            'AF': {'adx_min': 20},
            'SR': {'adx_min': 20},
        }),
    ]
    
    results = []
    
    for scenario_name, overrides in scenarios:
        print(f"\n\n{'='*60}")
        print(f"  СЦЕНАРИЙ: {scenario_name}")
        print(f"{'='*60}")
        
        # Build config
        configs = {}
        
        # Determine which tickers to include
        if 'only' in scenario_name.lower():
            # Only specified tickers (no Eu, no CR, no IMOEXF unless specified)
            included = {'BR', 'AF', 'SR'}
            for ticker in included:
                if ticker in df_map:
                    cfg = dict(base_configs.get(ticker, {}))
                    if ticker in overrides:
                        cfg.update(overrides[ticker])
                    configs[ticker] = cfg
        else:
            for ticker in list(df_map.keys()):
                cfg = dict(base_configs.get(ticker, {}))
                if ticker in overrides:
                    cfg.update(overrides[ticker])
                configs[ticker] = cfg
        
        # Apply ADX filters and log
        tickers_in_portfolio = []
        for ticker, df in df_map.items():
            cfg = configs[ticker]
            adx_min = cfg.get('adx_min')
            adx_max = cfg.get('adx_max')
            session = cfg.get('session_filter')
            
            can_trade = df is not None
            info = f"{ticker}: W={cfg['W']} T={cfg['T']} V{cfg['variant']}"
            if adx_min: info += f" ADX>{adx_min}"
            if adx_max: info += f" ADX<{adx_max}"
            if session: info += f" sess={session}"
            if can_trade:
                tickers_in_portfolio.append(ticker)
            print(f"  {info}")
        
        if len(tickers_in_portfolio) == 0:
            print("  ⚠ Нет тикеров для портфеля")
            continue
        
        trades, equity = run_portfolio_test(
            {t: df_map[t] for t in tickers_in_portfolio},
            configs, capital=CAPITAL
        )
        
        stats = compute_stats(trades, equity, cap=CAPITAL)
        stats['scenario'] = scenario_name
        results.append(stats)
        
        print(f"\n  Результаты:")
        print(f"    Итого сделок: {stats['trades']}")
        print(f"    Финальный капитал: {stats['final_capital']:,.0f} RUB")
        print(f"    Total return: {stats['total_ret_pct']:.1f}%")
        print(f"    Max DD: {stats['max_dd_pct']:.1f}%")
        print(f"    Calmar: {stats['calmar']:.3f}")
        print(f"    Годовая: {stats['annual_pct']:.1f}%")
        print(f"    WR: {stats['wr']:.1f}%")
        
        # Per-ticker
        if trades:
            df_t = pd.DataFrame(trades)
            print(f"\n  По тикерам:")
            for ticker in tickers_in_portfolio:
                sub = df_t[df_t['ticker'] == ticker]
                if len(sub):
                    wr = (sub['pnl_net'] > 0).mean() * 100
                    total = sub['pnl_net'].sum()
                    print(f"    {ticker}: {len(sub)} сделок, WR={wr:.1f}%, PnL={total:+.0f}, "
                          f"avg={sub['pnl_net'].mean():+.0f}")
    
    # ── Summary ──
    print(f"\n\n{'#'*70}")
    print("# ИТОГОВАЯ ТАБЛИЦА — Портфельные сценарии")
    print(f"{'#'*70}")
    print(f"{'Scenario':<35} {'Trades':>7} {'WR':>6} {'Return%':>9} {'DD%':>6} {'Calmar':>8} {'Annual%':>9} {'Final':>10}")
    print("-" * 100)
    for r in sorted(results, key=lambda x: x['calmar'], reverse=True):
        print(f"{r['scenario']:<35} {r['trades']:>7d} {r['wr']:>5.1f}% {r['total_ret_pct']:>8.1f}% "
              f"{r['max_dd_pct']:>5.1f}% {r['calmar']:>7.3f} {r['annual_pct']:>8.1f}% {r['final_capital']:>9.0f}")
    
    if results:
        df_out = pd.DataFrame(results)
        csv_path = os.path.join(OUT_DIR, "phase3d_portfolio_results.csv")
        df_out.to_csv(csv_path, index=False)
        print(f"\n✅ {csv_path}")
    
    print(f"\nDone: {datetime.now()}")


if __name__ == "__main__":
    main()
