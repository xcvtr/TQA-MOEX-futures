#!/usr/bin/env python3
"""Универсальный backtest — мульти-стратегия, приоритет по score, раздельный учёт."""
import sys, os, argparse, json
from datetime import datetime, timezone, timedelta
from collections import defaultdict
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import clickhouse_connect as cc
import psycopg2
import numpy as np
import pandas as pd
CH = dict(host='10.0.0.60', port=8123, database='moex')
PG = dict(host='10.0.0.60', port=5432, dbname='moex', user='postgres')


def load_portfolio():
    conn = psycopg2.connect(**PG)
    cur = conn.cursor()
    cur.execute("""
        SELECT p.ticker, p.strategy, p.contracts, p.trailing_activation, p.trailing_trail,
               p.timeout_bars, p.params, p.weight, COALESCE(s.go,0), COALESCE(s.min_step,0.01), COALESCE(s.step_price,1.0),
               COALESCE(s.fee_entry, 4.0)
        FROM futures.portfolio p
        LEFT JOIN futures.ticker_specs s ON s.ticker = p.ticker
        WHERE p.enabled=true ORDER BY p.ticker, p.strategy
    """)
    rows = cur.fetchall()
    cur.close(); conn.close()
    return rows  # list of tuples


def load_strategies():
    from common.paper_trader import _load_strategies, STRATEGY_MAP
    _load_strategies()
    return STRATEGY_MAP


def load_bars(ticker, days=365):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    ch = cc.get_client(**CH)
    rows = ch.query(f"""
        SELECT bt, opn, hi, lo, prc, vol
        FROM moex.mt5_continuous
        WHERE ticker = '{ticker}' AND bt >= toDateTime('{cutoff}')
        ORDER BY bt
    """).result_rows
    ch.close()
    bars = []
    for x in rows:
        ts = x[0]
        if ts.weekday() >= 5: continue
        h, m = ts.hour, ts.minute
        if h < 15 or h > 23 or (h == 23 and m > 45): continue
        bars.append({'ts': ts, 'opn': float(x[1]), 'hi': float(x[2]),
                     'lo': float(x[3]), 'prc': float(x[4]), 'vol': float(x[5])})
    return bars


def resample_bars(m1_bars, minutes=5):
    """Resample M1 bars to N-minute OHLC."""
    g = {}
    for b in m1_bars:
        total_m = b['ts'].hour * 60 + b['ts'].minute
        k_min = (total_m // minutes) * minutes
        k = b['ts'].replace(minute=k_min % 60, hour=k_min // 60, second=0)
        if k not in g:
            g[k] = {'ts': k, 'opn': b['opn'], 'hi': b['hi'], 'lo': b['lo'], 'prc': b['prc']}
        else:
            gg = g[k]
            gg['hi'] = max(gg['hi'], b['hi'])
            gg['lo'] = min(gg['lo'], b['lo'])
            gg['prc'] = b['prc']
    return sorted(g.values(), key=lambda x: x['ts'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--tickers', type=str, default=None)
    parser.add_argument('--days', type=int, default=365)
    parser.add_argument('--capital', type=int, default=200000)
    parser.add_argument('--risk-pct', type=float, default=1.0)
    parser.add_argument('--tf', type=int, default=5, help='Detect timeframe in minutes (resample M1→N min)')
    parser.add_argument('--close-entry', action='store_true', help='Entry at close of signal bar (else open of next bar)')
    parser.add_argument('--strategy', type=str, default=None, help='Strategy name (overrides PG portfolio, uses --tickers)')
    parser.add_argument('--params', type=str, default=None, help='JSON strategy params, e.g. \'{"impulse_bars":3}\'')
    parser.add_argument('--tf-map', type=str, default=None, help='Per-ticker TF override JSON, e.g. \'{"Si":1,"MM":10,"GZ":5,"RN":1}\'')
    args = parser.parse_args()

    # Parse strategy params
    strategy_params = None
    if args.params:
        strategy_params = json.loads(args.params)

    # Parse per-ticker TF map
    tf_map = {}
    if args.tf_map:
        tf_map = json.loads(args.tf_map)

    strategies = load_strategies()
    portfolio = load_portfolio()

    # Override: use --strategy + --tickers directly (no PG config needed)
    if args.strategy and args.tickers:
        portfolio = []
        SPECS = {}
        try:
            pg2 = psycopg2.connect(**PG)
            cur2 = pg2.cursor()
            cur2.execute("SELECT ticker, go, min_step, step_price, fee_entry FROM futures.ticker_specs")
            for r in cur2.fetchall():
                SPECS[r[0]] = {'go': float(r[1]), 'ms': float(r[2]), 'sp': float(r[3]), 'fee': float(r[4] or 4)}
            cur2.close(); pg2.close()
        except: pass
        for t in args.tickers.split(','):
            s = SPECS.get(t, {'go': 10000, 'ms': 1, 'sp': 1, 'fee': 4})
            portfolio.append((t, args.strategy, 1, 0.005, 0.003, 12, {}, 1.0, s['go'], s['ms'], s['sp'], s['fee']))

    # Filter tickers
    if args.tickers:
        allowed = set(args.tickers.split(','))
        portfolio = [r for r in portfolio if r[0] in allowed]

    # Group by ticker
    by_ticker = defaultdict(list)
    for r in portfolio:
        ticker = r[0]
        by_ticker[ticker].append(r)

    print(f'Тикеры: {sorted(by_ticker.keys())}')
    print(f'Капитал: {args.capital}, риск: {args.risk_pct}%')
    print()

    import strategies.common.executor as exec_module
    
    # ── Build strategy list for PortfolioEngine ──
    from strategies.common.engine import PortfolioEngine
    from strategies.common.broker import BrokerSim
    
    # Group strategies by name → tickers
    strat_map = defaultdict(list)
    for r in portfolio:
        sname = r[1]
        ticker = r[0]
        strat_map[sname].append(ticker)
    
    strat_list = []
    for sname, tickers in strat_map.items():
        fn = strategies.get(sname)
        if fn:
            strat_list.append((sname, fn, tickers, strategy_params))
    
    # ── Load data ──
    import pandas as pd
    all_data = {}
    ticker_specs = {}
    for ticker in sorted(by_ticker.keys()):
        m1 = load_bars(ticker, args.days)
        if len(m1) < 100:
            continue
        ticker_tf = tf_map.get(ticker, args.tf)
        df = pd.DataFrame(m1)
        df.index = pd.to_datetime([b['ts'] for b in m1])
        if df.index.tz is None:
            df.index = df.index.tz_localize('Asia/Irkutsk')
        else:
            df.index = df.index.tz_convert('Asia/Irkutsk')
        all_data[ticker] = df
        
        # Get specs from first entry
        e = by_ticker[ticker][0]
        ticker_specs[ticker] = {
            'go': float(e[8]), 'ms': float(e[9]), 'sp': float(e[10]),
            'fee': float(e[11]) if len(e) > 11 else 4.0,
        }
        print(f'{ticker}: {len(m1)} баров')
    
    if not all_data:
        print('НЕТ ДАННЫХ')
        exit()
    
    # ── Run PortfolioEngine ──
    exec_module.RISK_PCT = args.risk_pct / 100.0
    print(f'RISK_PCT set to {exec_module.RISK_PCT}', flush=True)
    engine = PortfolioEngine(strat_list, broker=BrokerSim(), capital=args.capital)
    engine.executor.load_portfolio()
    print(f'Portfolio loaded: {list(engine.executor._portfolio.keys())}', flush=True)
    for key in list(engine.executor._portfolio.keys()):
        engine.executor._portfolio[key]['contracts'] = None
    print(f'Ticker specs: {ticker_specs}', flush=True)
    result = engine.run(all_data, ticker_specs=ticker_specs)
    
    trades = result.trades if hasattr(result, 'trades') else []
    if not trades:
        print('НЕТ СДЕЛОК')
        exit()
    
    # ── Report ──
    # By strategy
    by_st = defaultdict(list)
    for t in trades:
        by_st[t.strategy].append(t)
    
    print()
    print('=== ПО СТРАТЕГИЯМ ===')
    for sname, ts in sorted(by_st.items()):
        pnls = [t.pnl for t in ts]
        n = len(pnls)
        wins = [p for p in pnls if p > 0]
        wr = len(wins)/n*100
        tp = sum(wins)
        tn = sum(abs(p) for p in pnls if p <= 0)
        pf = tp/tn if tn else float('inf')
        total = sum(pnls)
        # By ticker
        by_tk = defaultdict(list)
        for t in ts:
            by_tk[t.ticker].append(t.pnl)
        details = ' '.join(f'{tk}={sum(v):+.0f}' for tk, v in sorted(by_tk.items()))
        print(f'{sname:16s}: n={n:3d} WR={wr:5.1f}% PnL={total:+9.0f} PF={pf:.2f}  [{details}]')
    
    # By ticker
    by_tk = defaultdict(list)
    for t in trades:
        by_tk[t.ticker].append(t)
    print()
    print('=== ПО ТИКЕРАМ ===')
    for tk in sorted(by_tk):
        ts = by_tk[tk]
        pnls = [t.pnl for t in ts]
        total = sum(pnls)
        wins = [p for p in pnls if p > 0]
        wr = len(wins)/len(pnls)*100
        by_s = defaultdict(list)
        for t in ts:
            by_s[t.strategy].append(t.pnl)
        sw = ' '.join(f'{s}={sum(v):+.0f}' for s, v in sorted(by_s.items()))
        print(f'{tk:4s}: n={len(ts):3d} WR={wr:5.1f}% PnL={total:+9.0f}  [{sw}]')
    
    # Portfolio total
    all_pnls = [t.pnl for t in trades]
    total_pnl = sum(all_pnls)
    cap = args.capital + total_pnl
    wins = [p for p in all_pnls if p > 0]
    wr = len(wins)/len(all_pnls)*100
    tp = sum(wins)
    tn = sum(abs(p) for p in all_pnls if p <= 0)
    pf = tp/tn if tn else float('inf')
    
    # MDD (from cumulative PnL)
    cum = args.capital + np.cumsum(all_pnls)
    peak = np.maximum.accumulate(cum)
    mdd = np.max((peak - cum) / peak * 100)
    
    ret = (cap - args.capital) / args.capital * 100
    mtm_mdd = getattr(result, 'mtm_max_dd', mdd)
    
    print()
    print('=== ПОРТФЕЛЬ ===')
    print(f'Capital: {args.capital:,} → {cap:,.0f} ({ret:+.1f}%)')
    print(f'Trades: {len(all_pnls)} | WR: {wr:.1f}% | PF: {pf:.2f} | MDD: {mdd:.2f}%')
