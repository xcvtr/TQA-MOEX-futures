#!/usr/bin/env python3
"""Dragon portfolio backtest — M1 bars, M5 detect + M1 tick, MTM DD, reinvest."""
import sys, os, argparse
from datetime import datetime, timezone, timedelta
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import clickhouse_connect as cc
import numpy as np

CH = dict(host='10.0.0.60', port=8123, database='moex')
TC = 4
TO_M1 = 60
TRAIL_ACT, TRAIL_TRAIL = 0.005, 0.003
SL_PCT = 0.007

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from dragon.prod.engine import check_signal

SPECS = {
    'Si': {'ms': 1.0, 'sp': 1.0},
    'GZ': {'ms': 1.0, 'sp': 1.0},
    'CR': {'ms': 0.01, 'sp': 1.0},
    'RN': {'ms': 1.0, 'sp': 1.0},
    'GD': {'ms': 0.05, 'sp': 1.0},
    'NG': {'ms': 0.001, 'sp': 7.66647},
    'BR': {'ms': 0.01, 'sp': 7.66647},
    'MM': {'ms': 0.01, 'sp': 1.0},
}


def load_bars(ticker, days=365):
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
        if h < 15 or h > 23 or (h == 23 and m > 45): continue
        bars.append({'ts': ts, 'opn': float(r[1]), 'hi': float(r[2]), 'lo': float(r[3]), 'prc': float(r[4])})
    return bars


def calc_pnl(entry, exit_, direction, ms, sp, contracts=1):
    raw = (exit_ - entry) / ms * sp - TC
    return (raw if direction == 'long' else -raw) * contracts


def get_go(ticker):
    import psycopg2
    conn = psycopg2.connect(host='10.0.0.60', port=5432, dbname='moex', user='postgres')
    cur = conn.cursor()
    cur.execute("SELECT go FROM futures.ticker_specs WHERE ticker=%s", (ticker,))
    r = cur.fetchone()
    cur.close(); conn.close()
    return float(r[0]) if r and r[0] else 10000


def backtest_portfolio(tickers_contracts, days=365, capital=200000, reinvest=False, knur=0.5):
    all_bars = {}
    go_map = {}
    specs_map = {}
    for ticker, contracts in tickers_contracts:
        bars = load_bars(ticker, days)
        if len(bars) < 50:
            print(f"  {ticker}: only {len(bars)} bars, skip")
            continue
        all_bars[ticker] = bars
        specs_map[ticker] = SPECS[ticker]
        go_map[ticker] = get_go(ticker)
        print(f"  {ticker}: {len(bars)} bars, GO={go_map[ticker]:.0f}₽")

    if not all_bars:
        print("Нет данных!")
        return

    tickers = [t for t, _ in tickers_contracts if t in all_bars]
    all_closed_trades = []

    # Track equity across all tickers
    eq = float(capital)

    for ticker, base_contracts in tickers_contracts:
        if ticker not in all_bars:
            continue
        bars = all_bars[ticker]
        s = specs_map[ticker]
        ms, sp = s['ms'], s['sp']
        go_per_contract = go_map[ticker]

        trades, open_pos = [], None
        ticker_pnl = 0.0
        cur_contracts = base_contracts

        for i in range(30, len(bars)):
            bar = bars[i]

            # ── Tick ──
            if open_pos:
                if i - open_pos['bi'] >= TO_M1:
                    pnl = calc_pnl(open_pos['ep'], bar['prc'], open_pos['dir'], ms, sp, cur_contracts)
                    trades.append({'ts': bar['ts'], 'pnl': pnl, 'reason': 'timeout', 'ticker': ticker})
                    ticker_pnl += pnl
                    eq += pnl
                    open_pos = None
                else:
                    ep = open_pos['ep']
                    if not open_pos.get('tr'):
                        if (open_pos['dir'] == 'long' and bar['hi'] >= ep*(1+TRAIL_ACT)) or \
                           (open_pos['dir'] == 'short' and bar['lo'] <= ep*(1-TRAIL_ACT)):
                            open_pos['tr'] = True
                            open_pos['tl'] = bar['hi']*(1-TRAIL_TRAIL) if open_pos['dir'] == 'long' else bar['lo']*(1+TRAIL_TRAIL)
                    exit_p = None
                    if open_pos.get('tr'):
                        if (open_pos['dir'] == 'long' and bar['lo'] <= open_pos['tl']) or \
                           (open_pos['dir'] == 'short' and bar['hi'] >= open_pos['tl']):
                            exit_p = open_pos['tl']
                    if not exit_p:
                        sl = ep*(1-SL_PCT) if open_pos['dir'] == 'long' else ep*(1+SL_PCT)
                        if (open_pos['dir'] == 'long' and bar['lo'] <= sl) or \
                           (open_pos['dir'] == 'short' and bar['hi'] >= sl):
                            exit_p = sl
                    if exit_p:
                        pnl = calc_pnl(ep, exit_p, open_pos['dir'], ms, sp, cur_contracts)
                        trades.append({'ts': bar['ts'], 'pnl': pnl, 'reason': 'exit', 'ticker': ticker})
                        ticker_pnl += pnl
                        eq += pnl
                        open_pos = None

            # ── Detect ──
            if i % 5 == 0 and not open_pos:
                bars_list = [{'opn': b['opn'], 'hi': b['hi'], 'lo': b['lo'], 'prc': b['prc']} for b in bars[:i+1]]
                bd = {'prc': bar['prc'], 'bars_list': bars_list}
                sig = check_signal(bd, ticker)
                if sig:
                    entry = sig['entry_price']

                    # Calculate contracts with reinvest
                    if reinvest:
                        risk_rub = eq * 0.01  # 1% risk per trade
                        sl_rub_per_contract = entry * SL_PCT / ms * sp + TC
                        if sl_rub_per_contract <= 0:
                            continue
                        cur_contracts = max(1, int(risk_rub / sl_rub_per_contract))
                    else:
                        cur_contracts = base_contracts

                    # GO check
                    go_needed = go_per_contract * cur_contracts
                    if go_needed > eq * knur:
                        continue

                    open_pos = {'bi': i, 'ep': entry, 'dir': sig['direction'],
                                'tr': False, 'tl': None}

        if open_pos:
            pnl = calc_pnl(open_pos['ep'], bars[-1]['prc'], open_pos['dir'], ms, sp, cur_contracts)
            trades.append({'ts': bars[-1]['ts'], 'pnl': pnl, 'reason': 'eof', 'ticker': ticker})
            ticker_pnl += pnl
            eq += pnl
        else:
            eq += ticker_pnl  # Already counted above

        # Report per ticker
        wins = [t for t in trades if t['pnl'] > 0]
        losses = [t for t in trades if t['pnl'] <= 0]
        n = len(trades)
        wr = len(wins)/n*100 if n else 0
        tp = sum(t['pnl'] for t in wins)
        tn = sum(abs(t['pnl']) for t in losses)
        pf = tp/tn if tn else float('inf')
        aw = tp/len(wins) if wins else 0
        al = tn/len(losses) if losses else 0

        print(f"  {ticker:4s} ×{cur_contracts} | n={n:4d} wr={wr:5.1f}% pnl={ticker_pnl:+8.0f} pf={pf:.2f} aw={aw:+6.0f} al={al:+6.0f}")
        all_closed_trades.extend(trades)

    # Sort all trades by time
    all_closed_trades.sort(key=lambda x: x['ts'] if isinstance(x['ts'], datetime) else datetime.fromisoformat(str(x['ts'])))

    # Portfolio metrics
    cap = capital
    peak = cap
    mdd = 0
    for t in all_closed_trades:
        cap += t['pnl']
        peak = max(peak, cap)
        dd = (peak - cap) / peak * 100
        mdd = max(mdd, dd)

    wins = [t for t in all_closed_trades if t['pnl'] > 0]
    losses = [t for t in all_closed_trades if t['pnl'] <= 0]
    n = len(all_closed_trades)
    wr = len(wins)/n*100 if n else 0
    tp = sum(t['pnl'] for t in wins)
    tn = sum(abs(t['pnl']) for t in losses)
    pf = tp/tn if tn else float('inf')
    aw = tp/len(wins) if wins else 0
    al = tn/len(losses) if losses else 0
    ret = (cap - capital) / capital * 100

    print(f"\n{'='*60}")
    print(f"ПОРТФЕЛЬ ({len(tickers)} тикеров, {n} сделок)")
    print(f"{'='*60}")
    print(f"  Капитал: {capital:,.0f}₽ → {cap:,.0f}₽ ({ret:+.2f}%)")
    print(f"  WR: {wr:.1f}% | PF: {pf:.2f} | MDD: {mdd:.2f}%")
    print(f"  AvgWin: {aw:+.0f}₽ | AvgLoss: {al:+.0f}₽")
    print(f"  Calmar: {ret/mdd if mdd > 0 else float('inf'):.1f}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--tickers', type=str, default='MM,GD')
    parser.add_argument('--contracts', type=int, default=2)
    parser.add_argument('--days', type=int, default=365)
    parser.add_argument('--capital', type=int, default=200000)
    parser.add_argument('--reinvest', action='store_true')
    args = parser.parse_args()

    tickers = [s.strip() for s in args.tickers.split(',')]
    tickers_contracts = [(t, args.contracts) for t in tickers]

    print(f"\n🐉 Dragon Portfolio — {args.days}д, {args.capital:,}₽, ×{args.contracts}{' REINVEST' if args.reinvest else ''}")
    print(f"{'='*60}")
    backtest_portfolio(tickers_contracts, args.days, args.capital, args.reinvest)
