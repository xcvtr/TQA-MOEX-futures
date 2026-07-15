#!/usr/bin/env python3
"""Универсальный backtest для всех стратегий — M1 бары, detect M5, tick M1."""
import sys, os, argparse
from datetime import datetime, timezone, timedelta
from collections import defaultdict
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import clickhouse_connect as cc
import psycopg2

TRADE_COST = 4
TRAIL_ACT, TRAIL_TRAIL = 0.005, 0.003

CH = dict(host='10.0.0.60', port=8123, database='moex')
PG = dict(host='10.0.0.60', port=5432, dbname='moex', user='postgres')

# Загружаем все стратегии из PG portfolio
def load_portfolio():
    conn = psycopg2.connect(**PG)
    cur = conn.cursor()
    cur.execute("SELECT ticker, strategy, contracts FROM futures.portfolio WHERE enabled=true ORDER BY ticker, strategy")
    rows = cur.fetchall()
    cur.close(); conn.close()
    
    portfolio = defaultdict(list)
    for r in rows:
        portfolio[r[0]].append({'strategy': r[1], 'contracts': r[2] or 1})
    return dict(portfolio)


def load_specs():
    conn = psycopg2.connect(**PG)
    cur = conn.cursor()
    cur.execute("SELECT ticker, min_step, step_price FROM futures.ticker_specs")
    specs = {}
    for r in cur.fetchall():
        specs[r[0]] = {'ms': float(r[1]) if r[1] else 0.01, 'sp': float(r[2]) if r[2] else 1.0}
    cur.close(); conn.close()
    return specs


def load_bars(ticker, days=365):
    """Load M1 bars from CH."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    ch = cc.get_client(**CH)
    rows = ch.query(f"""
        SELECT bt, opn, hi, lo, prc
        FROM moex.mt5_bars
        WHERE ticker = '{ticker}'
          AND bt >= %(cutoff)s
        ORDER BY bt
    """, parameters={'cutoff': cutoff}).result_rows
    ch.close()
    
    bars = []
    for r in rows:
        ts = r[0]
        if ts.weekday() >= 5: continue
        h, m = ts.hour, ts.minute
        if h < 7 or (h == 15 and m > 45) or h > 15: continue
        bars.append({'ts': ts, 'opn': float(r[1]), 'hi': float(r[2]), 'lo': float(r[3]), 'prc': float(r[4])})
    return bars


def load_strategies():
    """Load strategy check_signal functions."""
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
    from common.paper_trader import _load_strategies, STRATEGY_MAP
    _load_strategies()
    return STRATEGY_MAP


def calc_pnl(entry, exit_, direction, ms, sp, contracts=1):
    raw = (exit_ - entry) / ms * sp - TRADE_COST
    return (raw if direction == 'long' else -raw) * contracts


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--tickers', type=str, default=None, help='Ticker filter (comma)')
    parser.add_argument('--days', type=int, default=365)
    parser.add_argument('--capital', type=int, default=200000)
    args = parser.parse_args()
    
    strategies = load_strategies()
    portfolio = load_portfolio()
    specs = load_specs()
    
    if args.tickers:
        allowed = set(args.tickers.split(','))
        portfolio = {t: s for t, s in portfolio.items() if t in allowed}
    
    strategies_list = set()
    for s_list in portfolio.values():
        for s in s_list:
            strategies_list.add(s['strategy'])
    print(f'Стратегии: {strategies_list}')
    print(f'Тикеры: {list(portfolio.keys())}')
    print()
    
    all_trades = []
    
    for ticker, entries in sorted(portfolio.items()):
        if ticker not in specs:
            continue
        spec = specs[ticker]
        ms, sp = spec['ms'], spec['sp']
        
        bars = load_bars(ticker, args.days)
        if len(bars) < 30:
            print(f'{ticker}: только {len(bars)} баров, пропускаем')
            continue
        
        trades = []
        open_pos = None
        DETECT_INTERVAL = 5
        
        for i in range(30, len(bars)):
            bar = bars[i]
            
            # ── Tick (M1) ──
            if open_pos:
                if i - open_pos['bi'] >= 60:  # timeout 60 min
                    pnl = calc_pnl(open_pos['ep'], bar['prc'], open_pos['dir'], ms, sp, open_pos['ct'])
                    trades.append({'ts': bar['ts'], 'pnl': pnl, 'reason': 'timeout'})
                    open_pos = None
                else:
                    ep = open_pos['ep']
                    if not open_pos.get('tr'):
                        if (open_pos['dir'] == 'long' and bar['hi'] >= ep * 1.005) or \
                           (open_pos['dir'] == 'short' and bar['lo'] <= ep * 0.995):
                            open_pos['tr'] = True
                            open_pos['tl'] = bar['hi'] * 0.997 if open_pos['dir'] == 'long' else bar['lo'] * 1.003
                    exit_p = None
                    if open_pos.get('tr'):
                        if (open_pos['dir'] == 'long' and bar['lo'] <= open_pos['tl']) or \
                           (open_pos['dir'] == 'short' and bar['hi'] >= open_pos['tl']):
                            exit_p = open_pos['tl']
                    if not exit_p:
                        sl = ep * 0.993 if open_pos['dir'] == 'long' else ep * 1.007
                        if (open_pos['dir'] == 'long' and bar['lo'] <= sl) or \
                           (open_pos['dir'] == 'short' and bar['hi'] >= sl):
                            exit_p = sl
                    if exit_p:
                        pnl = calc_pnl(ep, exit_p, open_pos['dir'], ms, sp, open_pos['ct'])
                        trades.append({'ts': bar['ts'], 'pnl': pnl, 'reason': 'exit'})
                        open_pos = None
            
            # ── Detect (M5) ──
            if i % DETECT_INTERVAL == 0 and not open_pos:
                for entry in entries:
                    name = entry['strategy']
                    fn = strategies.get(name)
                    if not fn:
                        continue
                    bars_list = [{'opn': b['opn'], 'hi': b['hi'], 'lo': b['lo'], 'prc': b['prc']} for b in bars[:i+1]]
                    bd = {'prc': bar['prc'], 'bars_list': bars_list}
                    try:
                        sig = fn(bd, ticker)
                    except Exception:
                        continue
                    if sig:
                        contracts = entry.get('contracts', 1)
                        open_pos = {'bi': i, 'ep': sig['entry_price'], 'dir': sig['direction'],
                                    'tr': False, 'tl': None, 'ct': contracts}
                        break
        
        if open_pos and bars:
            pnl = calc_pnl(open_pos['ep'], bars[-1]['prc'], open_pos['dir'], ms, sp, open_pos['ct'])
            trades.append({'ts': bars[-1]['ts'], 'pnl': pnl, 'reason': 'eof'})
        
        # Report per ticker
        total = sum(t['pnl'] for t in trades)
        wins = [t for t in trades if t['pnl'] > 0]
        wr = len(wins) / len(trades) * 100 if trades else 0
        cap = args.capital; peak = cap; mdd = 0
        for t in trades:
            cap += t['pnl']; peak = max(peak, cap); mdd = max(mdd, (peak - cap) / peak * 100)
        tp = sum(t['pnl'] for t in wins)
        tn = sum(abs(t['pnl']) for t in trades if t['pnl'] <= 0)
        pf = tp / tn if tn > 0 else float('inf')
        
        print(f'{ticker}: {len(trades):4d} сделок WR={wr:5.1f}% PnL={total:+9.0f} MDD={mdd:5.2f}% PF={pf:.2f}')
        
        for t in trades:
            t['ticker'] = ticker
        all_trades.extend(trades)
    
    # Portfolio
    if all_trades:
        all_trades.sort(key=lambda x: x['ts'])
        cap = args.capital; peak = cap; mdd = 0
        for t in all_trades:
            cap += t['pnl']; peak = max(peak, cap); mdd = max(mdd, (peak - cap) / peak * 100)
        wins = [t for t in all_trades if t['pnl'] > 0]
        wr = len(wins) / len(all_trades) * 100
        tp = sum(t['pnl'] for t in wins)
        tn = sum(abs(t['pnl']) for t in all_trades if t['pnl'] <= 0)
        pf = tp / tn if tn > 0 else float('inf')
        
        print(f'\n=== ПОРТФЕЛЬ ({len(portfolio)} тикеров, {len(all_trades)} сделок) ===')
        print(f'Capital: {args.capital:,} -> {cap:,.0f} ({(cap-args.capital)/args.capital*100:+.1f}%)')
        print(f'WR: {wr:.1f}% | PF: {pf:.2f} | MDD: {mdd:.2f}%')
