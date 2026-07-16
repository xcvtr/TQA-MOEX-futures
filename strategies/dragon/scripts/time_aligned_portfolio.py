#!/usr/bin/env python3 -u
"""Time-aligned portfolio backtest — ВСЕ тикеры одновременно, единый капитал."""
import sys, os, json, subprocess
from datetime import datetime
sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures/strategies/dragon')
sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures/strategies')
from dragon.prod.engine import check_signal

TC = 4
CAPITAL = 200000
MT5_PATH = "C:/Program Files/MetaTrader 5 FINAM/terminal64.exe"

SPECS = {
    'BR': {'ms': 0.01, 'sp': 7.66647}, 'NG': {'ms': 0.001, 'sp': 7.66647},
    'RN': {'ms': 1.0, 'sp': 1.0}, 'SV': {'ms': 0.01, 'sp': 7.66647},
    'MM': {'ms': 0.01, 'sp': 1.0}, 'Si': {'ms': 1.0, 'sp': 1.0},
    'CR': {'ms': 0.01, 'sp': 1.0}, 'GZ': {'ms': 1.0, 'sp': 1.0},
}


def load_bars(ticker):
    import clickhouse_connect as cc
    rows = cc.get_client(host='10.0.0.60', port=8123, database='moex').query(
        f"SELECT bt, opn, hi, lo, prc FROM moex.mt5_continuous WHERE ticker='{ticker}' AND bt >= '2025-07-16' ORDER BY bt"
    ).result_rows
    cc.get_client(host='10.0.0.60', port=8123, database='moex').close()
    bars = []
    for r in rows:
        ts = r[0]
        if ts.weekday() >= 5: continue
        h, m = ts.hour, ts.minute
        if h < 15 or h > 23 or (h == 23 and m > 45): continue
        bars.append({'ts': ts, 'opn': float(r[1]), 'hi': float(r[2]), 'lo': float(r[3]), 'prc': float(r[4])})
    return bars


def portfolio(tickers_contracts, days=365, reinvest=False):
    """Time-aligned portfolio backtest."""
    # Load all bars
    all_bars = {}
    for ticker, base_cont in tickers_contracts:
        bars = load_bars(ticker)
        if len(bars) > 100:
            all_bars[ticker] = bars
            print(f'  {ticker}: {len(bars)} bars', flush=True)

    tickers = list(all_bars.keys())
    if not tickers:
        return

    # Build aligned time index (use timestamps from first ticker)
    ref = all_bars[tickers[0]]
    min_len = min(len(all_bars[t]) for t in tickers)
    
    dp = {'impulse_pct': 0.3, 'retrace_max_pct': 70, 'hump_extension': 0.1, 'lookback': 100}
    ta, tt, sl = 0.015, 0.005, 0.01
    
    # State per ticker
    positions = {}  # ticker -> {entry, bar_idx, direction, contracts}
    m5_cache = {t: [] for t in tickers}
    trade_log = []  # (timestamp, ticker, pnl)
    
    equity = CAPITAL
    peak_eq = CAPITAL
    mdd = 0
    total_trades = 0

    for i in range(30, min_len):
        bar_ts = ref[i]['ts']

        # Update M5 cache for each ticker
        for t in tickers:
            if i % 5 == 0:
                g = all_bars[t][i-5:i]
                if len(g) >= 3:
                    m5_cache[t].append({
                        'opn': g[0]['opn'], 'hi': max(b['hi'] for b in g),
                        'lo': min(b['lo'] for b in g), 'prc': g[-1]['prc']
                    })

        # --- TICK: check SL/TP for all open positions ---
        for t in tickers:
            pos = positions.get(t)
            if pos is None:
                continue
            bar = all_bars[t][i]
            ep = pos['ep']
            ms, sp = SPECS[t]['ms'], SPECS[t]['sp']
            ex = None
            
            # SL
            slev = ep*(1-sl) if pos['dir']=='long' else ep*(1+sl)
            if (pos['dir']=='long' and bar['lo']<=slev) or (pos['dir']=='short' and bar['hi']>=slev):
                ex = slev
            # Trailing on M5 close
            if not ex and i%5==4:
                if not pos.get('tr'):
                    if (pos['dir']=='long' and bar['hi']>=ep*(1+ta)) or (pos['dir']=='short' and bar['lo']<=ep*(1-ta)):
                        pos['tr'] = True
                        pos['tl'] = bar['hi']*(1-tt) if pos['dir']=='long' else bar['lo']*(1+tt)
                if pos.get('tr'):
                    if (pos['dir']=='long' and bar['lo']<=pos['tl']) or (pos['dir']=='short' and bar['hi']>=pos['tl']):
                        ex = pos['tl']
            # Timeout
            if not ex and i-pos['bi']>=60:
                ex = bar['prc']
            
            if ex is not None:
                raw = ((ex - ep) / ms * sp - TC) * pos['contracts']
                pnl = raw if pos['dir']=='long' else -raw
                equity += pnl
                peak_eq = max(peak_eq, equity)
                mdd = max(mdd, (peak_eq - equity) / peak_eq * 100)
                trade_log.append((bar_ts, t, pnl, 'exit', pos['contracts']))
                total_trades += 1
                del positions[t]

        # --- DETECT: check signals for all tickers ---
        if i % 5 == 0:
            for t in tickers:
                if t in positions:
                    continue
                if len(m5_cache[t]) < 6:
                    continue
                slc = m5_cache[t][-110:]
                bar = all_bars[t][i]
                ms, sp = SPECS[t]['ms'], SPECS[t]['sp']
                sig = check_signal({'prc': slc[-1]['prc'], 'bars_list': slc}, t, dp)
                if sig:
                    entry = sig['entry_price']
                    # Calculate contracts
                    base_cont = dict(tickers_contracts)[t]
                    if reinvest:
                        risk_rub = equity * 0.01
                        sl_cost = entry * sl / ms * sp + TC
                        contracts_cnt = max(1, int(risk_rub / sl_cost)) if sl_cost > 0 else base_cont
                    else:
                        contracts_cnt = base_cont
                    positions[t] = {'bi': i, 'ep': entry, 'dir': sig['direction'],
                                    'tr': False, 'tl': None, 'contracts': contracts_cnt}
                    trade_log.append((bar_ts, t, 0, 'enter', contracts_cnt))

    # Close remaining positions at end
    for t, pos in list(positions.items()):
        bar = all_bars[t][-1]
        ms, sp = SPECS[t]['ms'], SPECS[t]['sp']
        raw = ((bar['prc'] - pos['ep']) / ms * sp - TC) * pos['contracts']
        pnl = raw if pos['dir']=='long' else -raw
        equity += pnl
        trade_log.append((bar['ts'], t, pnl, 'eof', pos['contracts']))

    # Report
    pnls = [t[2] for t in trade_log if t[3] != 'enter']
    n = len(pnls)
    if n == 0:
        print('  No trades!')
        return
    wins = [p for p in pnls if p > 0]
    total = sum(pnls)
    pf = sum(wins) / sum(abs(p) for p in pnls if p <= 0) if any(p <= 0 for p in pnls) else float('inf')
    ret = (equity - CAPITAL)/CAPITAL*100
    
    # Per-ticker report
    for t in tickers:
        tp = [x[2] for x in trade_log if x[1]==t and x[3]!='enter']
        nt = len(tp)
        if nt == 0: continue
        tw = sum(p for p in tp if p>0)
        tl = sum(abs(p) for p in tp if p<=0)
        print(f'  {t:4s}: {nt:4d} tr  pnl={sum(tp):+9.0f}  pf={tw/tl if tl else 0:.2f}', flush=True)
    
    print(f'  {"="*55}')
    print(f'  PORTFOLIO ({n} tr) — TIME-ALIGNED')
    print(f'  Capital: {CAPITAL:,} -> {equity:,.0f} ({ret:+.1f}%)')
    print(f'  PF: {pf:.2f} | MDD: {mdd:.2f}% | Calmar: {ret/mdd if mdd>0 else 0:.1f}')


if __name__ == '__main__':
    tickers = [('BR', 2), ('NG', 2), ('RN', 2), ('SV', 2), ('MM', 2),
               ('Si', 10), ('CR', 100), ('GZ', 10)]
    
    print('=== Fixed contracts (time-aligned) ===', flush=True)
    portfolio(tickers, 365, reinvest=False)
    
    print('\n=== Reinvest 1% (time-aligned) ===', flush=True)
    portfolio(tickers, 365, reinvest=True)
