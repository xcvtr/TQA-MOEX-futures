#!/usr/bin/env python3 -u
"""Dragon portfolio — масштабирование лота + реинвест."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import clickhouse_connect as cc
from dragon.prod.engine import check_signal

CH = dict(host='10.0.0.60', port=8123, database='moex')
TC = 4
CAPITAL = 200000

SPECS = {
    'Si': {'ms': 1.0, 'sp': 1.0},
    'CR': {'ms': 0.01, 'sp': 1.0},
    'GZ': {'ms': 1.0, 'sp': 1.0},
    'RN': {'ms': 1.0, 'sp': 1.0},
    'GD': {'ms': 0.05, 'sp': 1.0},
    'NG': {'ms': 0.001, 'sp': 7.66647},
    'BR': {'ms': 0.01, 'sp': 7.66647},
    'MM': {'ms': 0.01, 'sp': 1.0},
    'SV': {'ms': 0.01, 'sp': 7.66647},
}


def load_bars(ticker, days=365):
    cutoff = '2025-07-16'
    ch = cc.get_client(**CH)
    rows = ch.query(f"""
        SELECT bt, opn, hi, lo, prc
        FROM moex.mt5_continuous
        WHERE ticker = '{ticker}' AND bt >= '{cutoff}'
        ORDER BY bt
    """).result_rows
    ch.close()
    bars = []
    for r in rows:
        ts = r[0]
        if ts.weekday() >= 5: continue
        h, m = ts.hour, ts.minute
        if h < 15 or h > 23 or (h == 23 and m > 45): continue
        bars.append({'ts': ts, 'opn': float(r[1]), 'hi': float(r[2]), 'lo': float(r[3]), 'prc': float(r[4])})
    return bars


def portfolio(tickers_contracts, days=365, reinvest=False):
    """Backtest с разным лотом на тикер и реинвестом."""
    all_trades = []
    eq = float(CAPITAL)

    for ticker, base_contracts in tickers_contracts:
        bars = load_bars(ticker, days)
        if ticker not in SPECS:
            continue
        s = SPECS[ticker]
        ms, sp = s['ms'], s['sp']
        dp = {'impulse_pct': 0.3, 'retrace_max_pct': 70, 'hump_extension': 0.1, 'lookback': 100}
        trail_act, trail_trail, sl_pct = 0.015, 0.005, 0.01

        trades, open_pos = [], None
        m5 = []
        ticker_pnl = 0
        cur_contracts = base_contracts

        for i in range(30, len(bars)):
            if i % 5 == 0:
                g = bars[i-5:i]
                if len(g) >= 3:
                    m5.append({'opn': g[0]['opn'], 'hi': max(b['hi'] for b in g),
                               'lo': min(b['lo'] for b in g), 'prc': g[-1]['prc']})
            bar = bars[i]
            if open_pos:
                ep = open_pos['ep']
                exit_p = None
                sl = ep * (1 - sl_pct) if open_pos['dir'] == 'long' else ep * (1 + sl_pct)
                if (open_pos['dir'] == 'long' and bar['lo'] <= sl) or \
                   (open_pos['dir'] == 'short' and bar['hi'] >= sl):
                    exit_p = sl
                if not exit_p and i % 5 == 4:
                    if not open_pos.get('tr'):
                        if (open_pos['dir'] == 'long' and bar['hi'] >= ep*(1+trail_act)) or \
                           (open_pos['dir'] == 'short' and bar['lo'] <= ep*(1-trail_act)):
                            open_pos['tr'] = True
                            open_pos['tl'] = bar['hi']*(1-trail_trail) if open_pos['dir']=='long' else bar['lo']*(1+trail_trail)
                    if open_pos.get('tr'):
                        if (open_pos['dir'] == 'long' and bar['lo'] <= open_pos['tl']) or \
                           (open_pos['dir'] == 'short' and bar['hi'] >= open_pos['tl']):
                            exit_p = open_pos['tl']
                if not exit_p and i - open_pos['bi'] >= 60:
                    exit_p = bar['prc']
                if exit_p is not None:
                    raw = (exit_p - ep) / ms * sp - TC
                    pnl = (raw if open_pos['dir'] == 'long' else -raw) * cur_contracts
                    trades.append(pnl)
                    ticker_pnl += pnl
                    eq += pnl
                    open_pos = None
            if i % 5 == 0 and not open_pos:
                if len(m5) < 6: continue
                slc = m5[-110:]
                sig = check_signal({'prc': slc[-1]['prc'], 'bars_list': slc}, ticker, dp)
                if sig:
                    entry = sig['entry_price']
                    if reinvest:
                        risk_rub = eq * 0.01
                        sl_rub = entry * sl_pct / ms * sp + TC
                        cur_contracts = max(1, int(risk_rub / sl_rub)) if sl_rub > 0 else base_contracts
                    else:
                        cur_contracts = base_contracts
                    open_pos = {'bi': i, 'ep': entry, 'dir': sig['direction'], 'tr': False, 'tl': None}

        wins = [p for p in trades if p > 0]
        losses = [p for p in trades if p <= 0]
        n = len(trades)
        wr = len(wins)/n*100 if n else 0
        tp = sum(wins) if wins else 0
        tn = sum(abs(p) for p in losses) if losses else 0
        pf = tp/tn if tn else 0
        cap = CAPITAL
        peak = cap
        mdd = 0
        for p in trades:
            cap += p
            peak = max(peak, cap)
            mdd = max(mdd, (peak - cap)/peak*100)
        print(f'  {ticker:4s} ×{cur_contracts:2d} | tr={n:4d} wr={wr:5.1f}% pnl={ticker_pnl:+9.0f} pf={pf:.2f} mdd={mdd:.2f}%', flush=True)
        all_trades.extend(trades)

    if all_trades:
        wins = [p for p in all_trades if p > 0]
        losses = [p for p in all_trades if p <= 0]
        total = sum(all_trades)
        n = len(all_trades)
        wr = len(wins)/n*100
        tp = sum(wins)
        tn = sum(abs(p) for p in losses)
        pf = tp/tn if tn else 0
        cap = CAPITAL
        peak = cap
        mdd = 0
        for p in all_trades:
            cap += p
            peak = max(peak, cap)
            mdd = max(mdd, (peak - cap)/peak*100)
        ret = (cap - CAPITAL)/CAPITAL*100
        print(f'  {"="*50}')
        print(f'  PORTFOLIO ({len(tickers_contracts)} tk, {n} tr)')
        print(f'  Capital: {CAPITAL:,} -> {cap:,.0f} ({ret:+.1f}%)')
        print(f'  WR: {wr:.1f}% | PF: {pf:.2f} | MDD: {mdd:.2f}% | Calmar: {ret/mdd if mdd>0 else 0:.1f}')


if __name__ == '__main__':
    print('1) Fixed contracts — масштабирование лота для низко-MDD тикеров', flush=True)
    print(f'{"="*60}', flush=True)
    portfolio([('BR',2), ('NG',2), ('RN',2), ('SV',2), ('MM',2), ('CR',100), ('Si',10), ('GZ',10)])

    print(f'\n2) Reinvest 1% — без GO лимита', flush=True)
    print(f'{"="*60}', flush=True)
    portfolio([('BR',2), ('NG',2), ('RN',2), ('SV',2), ('MM',2), ('CR',100), ('Si',10), ('GZ',10)], reinvest=True)
