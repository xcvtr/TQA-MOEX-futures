#!/usr/bin/env python3
"""Divergence strategy: orderstats (intent) vs tradestats (execution) backtest.

Logic:
- o_imb = (put_b - put_s) / (put_b + put_s) * 100  (imbalance заявок)
- t_imb = (trades_b - trades_s) / (trades_b + trades_s) * 100  (imbalance сделок)
- Если o_imb и t_imb расходятся (разного знака), и divergence > threshold:
  → LONG если t_imb > abs(o_imb) * div_mult (сделки идут вверх вопреки заявкам)
  → SHORT если t_imb < -abs(o_imb) * div_mult

Entry: на следующем 1m баре по open
Exit: через hold минут, или по схождению divergence < min_div

Usage:
  python3 scripts/divergence_backtest.py              # SBER
  python3 scripts/divergence_backtest.py --secid GAZP  # Any ticker
  python3 scripts/divergence_backtest.py --scan       # All top-50 by volume
"""
import subprocess, sys, time
from datetime import datetime, timedelta
import numpy as np

CH = "10.0.0.63"
DB = "moex_algopack_v2"

def ch(sql):
    r = subprocess.run(['clickhouse-client', '--host', CH, '-d', DB, '--query', sql],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise Exception(r.stderr.strip())
    lines = r.stdout.strip().split('\n')
    return [l.split('\t') for l in lines if l.strip() and not l.startswith('┌') and not l.startswith('│') and not l.startswith('└') and not l.startswith('┌')]

def load_ticker(secid, start='2024-01-01', end='2026-06-18'):
    """Load aligned orderstats + tradestats data."""
    sql = f"""
        SELECT o.tradedate, o.tradetime,
               o.put_orders_b, o.put_orders_s, o.cancel_orders_b, o.cancel_orders_s,
               t.pr_open, t.pr_close, t.pr_high, t.pr_low,
               t.trades_b, t.trades_s, t.val_b, t.val_s
        FROM orderstats_local o
        JOIN tradestats_local t
          ON o.tradedate = t.tradedate AND o.secid = t.ticker AND o.tradetime = t.tradetime
        WHERE o.secid = '{secid}'
          AND o.tradedate >= '{start}' AND o.tradedate <= '{end}'
        ORDER BY o.tradedate, o.tradetime
        FORMAT TabSeparated
    """
    raw = ch(sql)
    if not raw or len(raw) < 100:
        return None

    arr = {}
    arr['date'] = [r[0] for r in raw]
    arr['time'] = [r[1] for r in raw]
    raw_data = raw  # list of tab-separated rows
    
    def col_int(i):
        return np.array([int(r[i]) if r[i] and r[i] != '\\N' else 0 for r in raw_data])
    def col_float(i):
        return np.array([float(r[i]) if r[i] and r[i] != '\\N' else 0.0 for r in raw_data])
    
    arr['put_b'] = col_int(2)
    arr['put_s'] = col_int(3)
    arr['cancel_b'] = col_int(4)
    arr['cancel_s'] = col_int(5)
    arr['open'] = col_float(6)
    arr['close'] = col_float(7)
    arr['high'] = col_float(8)
    arr['low'] = col_float(9)
    arr['trades_b'] = col_float(10)
    arr['trades_s'] = col_float(11)
    arr['val_b'] = col_float(12)
    arr['val_s'] = col_float(13)

    # Imbalances
    tot_put = arr['put_b'] + arr['put_s']
    tot_cancel = arr['cancel_b'] + arr['cancel_s']
    tot_trades = arr['trades_b'] + arr['trades_s']

    arr['o_imb'] = np.where(tot_put > 0, (arr['put_b'] - arr['put_s']) / tot_put * 100, 0)
    arr['t_imb'] = np.where(tot_trades > 0, (arr['trades_b'] - arr['trades_s']) / tot_trades * 100, 0)
    arr['pc_ratio'] = np.where(tot_cancel > 0, tot_put / tot_cancel, tot_put)

    # Smooth o_imb (rolling 5 bars to reduce noise)
    w = 5
    o_imb_sm = np.copy(arr['o_imb'])
    for i in range(w, len(o_imb_sm)):
        o_imb_sm[i] = np.mean(arr['o_imb'][i-w:i])
    arr['o_imb_sm'] = o_imb_sm

    return arr

def backtest(arr, params):
    """Run divergence strategy."""
    div_thr = params.get('div_thr', 15)     # min |divergence| %
    hold = params.get('hold', 5)            # bars to hold (minutes)
    stop_pct = params.get('stop_pct', 0.02)  # 2% stop
    
    n = arr['o_imb'].shape[0]
    equity = 100000.0
    pos = 0  # 0 flat, 1 long, -1 short
    entry_price = 0.0
    entry_bar = 0
    trades = []
    eq_curve = [equity]
    
    for i in range(10, n - 1):
        # Close existing position
        if pos != 0:
            bars_held = i - entry_bar
            if bars_held >= hold:
                # Time exit
                ret = (arr['close'][i] / entry_price - 1) * pos
                equity *= (1 + ret)
                trades.append({'bar': i, 'type': 'exit_time', 'ret': ret, 'eq': equity})
                pos = 0
            elif stop_pct > 0:
                # Stop check
                if pos == 1 and arr['low'][i] < entry_price * (1 - stop_pct):
                    ret = -stop_pct
                    equity *= (1 + ret)
                    trades.append({'bar': i, 'type': 'stop_long', 'ret': ret, 'eq': equity})
                    pos = 0
                elif pos == -1 and arr['high'][i] > entry_price * (1 + stop_pct):
                    ret = -stop_pct
                    equity *= (1 + ret)
                    trades.append({'bar': i, 'type': 'stop_short', 'ret': ret, 'eq': equity})
                    pos = 0

        # Generate new signal
        if pos == 0 and i > 10:
            o = arr['o_imb_sm'][i]
            t = arr['t_imb'][i]
            
            # Divergence: o_imb and t_imb have opposite signs
            div = abs(o - t)
            
            if div > div_thr:
                if t > abs(o) * 0.5 and t > 5:
                    # LONG: сделки покупают, заявки против
                    pos = 1
                    entry_price = arr['open'][i + 1]
                    entry_bar = i + 1
                    trades.append({'bar': i, 'type': 'enter_long', 'price': entry_price, 'eq': equity,
                                   'o_imb': o, 't_imb': t})
                elif t < -abs(o) * 0.5 and t < -5:
                    # SHORT
                    pos = -1
                    entry_price = arr['open'][i + 1]
                    entry_bar = i + 1
                    trades.append({'bar': i, 'type': 'enter_short', 'price': entry_price, 'eq': equity,
                                   'o_imb': o, 't_imb': t})
        
        eq_curve.append(equity)
    
    return trades, eq_curve

def run_backtest(secid, start='2024-01-01', end='2026-06-18'):
    print(f"\n=== {secid}: Divergence backtest ({start} → {end}) ===")
    data = load_ticker(secid, start, end)
    if data is None or len(data['open']) < 1000:
        print(f"  SKIP: insufficient data (<1000 bars)")
        return None
    
    n = len(data['open'])
    print(f"  Bars: {n:,}")
    
    best = {'calmar': 0}
    
    # Grid search
    for div_thr in [10, 15, 20, 30]:
        for hold in [3, 5, 10]:
            for stop in [0.01, 0.02, 0.03, 0.05]:
                params = {'div_thr': div_thr, 'hold': hold, 'stop_pct': stop}
                trades, eq = backtest(data, params)
                
                if len(trades) < 2:
                    continue
                    
                total_ret = (eq[-1] / 100000.0 - 1) * 100
                peak = max(eq)
                dd = max((peak - v) / peak * 100 for v in eq)
                calmar = total_ret / max(dd, 0.01)
                trades_count = sum(1 for t in trades if 'enter' in t['type'])
                wr = sum(1 for t in trades if t.get('ret', 0) > 0) / max(sum(1 for t in trades if 'exit' in t['type'] or 'stop' in t['type']), 1) * 100
                
                if calmar > best['calmar'] and total_ret > 0:
                    best = {
                        'secid': secid, 'div_thr': div_thr, 'hold': hold, 'stop_pct': stop,
                        'ret_pct': total_ret, 'dd_pct': dd, 'calmar': calmar,
                        'trades': trades_count, 'wr': wr
                    }
    
    if best['calmar'] > 0:
        print(f"  Best: div={best['div_thr']} hold={best['hold']} stop={best['stop_pct']:.0%}")
        print(f"  Ret: {best['ret_pct']:+.1f}%  DD: {best['dd_pct']:.1f}%  Calmar: {best['calmar']:.1f}")
        print(f"  Trades: {best['trades']}  WR: {best['wr']:.0f}%")
    else:
        print(f"  No profitable config found")
    
    return best

if __name__ == "__main__":
    args = sys.argv[1:]
    if '--scan' in args:
        # Top 30 liquid equities
        top = ch("""
            SELECT secid, count() as bars 
            FROM orderstats_local 
            WHERE tradedate >= '2026-01-01' 
            GROUP BY secid 
            ORDER BY bars DESC 
            LIMIT 30 
            FORMAT TabSeparated
        """)
        results = []
        for r in top:
            secid = r[0]
            res = run_backtest(secid)
            if res:
                results.append(res)
        
        print("\n\n=== SCAN RESULTS ===")
        print(f"{'Ticker':>6} {'Calmar':>7} {'Ret%':>7} {'DD%':>6} {'Trades':>6} {'WR%':>5} {'Config':>25}")
        print("-" * 65)
        for r in sorted(results, key=lambda x: x['calmar'], reverse=True):
            cfg = f"div={r['div_thr']} h={r['hold']} s={r['stop_pct']:.0%}"
            print(f"{r['secid']:>6} {r['calmar']:>7.1f} {r['ret_pct']:>+6.1f}% {r['dd_pct']:>5.1f}% {r['trades']:>6} {r['wr']:>4.0f}% {cfg:>25}")
    else:
        secid = args[args.index('--secid') + 1] if '--secid' in args else 'SBER'
        run_backtest(secid)
