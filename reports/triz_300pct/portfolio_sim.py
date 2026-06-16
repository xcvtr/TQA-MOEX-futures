#!/usr/bin/env python3
"""Full portfolio simulation: all signals share equity, sequential combined timeline."""
import sys, os, json, math
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/../..')
os.chdir(os.path.dirname(os.path.abspath(__file__)) + '/../..')

import numpy as np
import clickhouse_connect
from config import CH_HOST, CH_PORT, CH_DB

COMM = 4
RISK_PCT = 0.02
MAX_CONTRACTS_MULT = 5
MAX_PORTFOLIO_LEVERAGE = 3.0
MAX_LOT = 5  # hard max contracts per signal per trade

CS_MAP = {
    'AF': 100, 'CC': 10, 'SP': 100, 'PT': 10, 'SF': 1000,
    'GD': 10, 'GK': 100, 'NA': 1, 'RN': 100, 'VB': 1000,
    'NM': 100, 'W4': 1, 'IMOEXF': 10, 'RI': 10, 'RL': 1,
}

PATTERNS = {
    'vol_up_oi_up_yb_up':    lambda dv, dyb, dys, dfn, dtoi: dv > 0 and dtoi > 0 and dyb > 0,
    'smart_money':            lambda dv, dyb, dys, dfn, dtoi: dv > 0 and dyb > 0 and dfn < 0,
    'vol_up_oi_down':        lambda dv, dyb, dys, dfn, dtoi: dv > 0 and dtoi < 0,
    'vol_up_yb_down_fiz_up': lambda dv, dyb, dys, dfn, dtoi: dv > 0 and dyb < 0 and dfn > 0,
    'fiz_extreme_vol_up':    lambda dv, dyb, dys, dfn, dtoi: dv > 0 and abs(dfn) > 5,
}

PORTFOLIO_SIGNALS = [
    {"ticker": "SF", "pattern": "vol_up_oi_down", "hold": 2, "sl": 0.01},
    {"ticker": "CC", "pattern": "smart_money", "hold": 5, "sl": 0.01},
    {"ticker": "SP", "pattern": "fiz_extreme_vol_up", "hold": 5, "sl": 0.01},
    {"ticker": "PT", "pattern": "fiz_extreme_vol_up", "hold": 5, "sl": 0.01},
    {"ticker": "AF", "pattern": "fiz_extreme_vol_up", "hold": 5, "sl": 0.01},
]

ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)

def load_daily(ticker):
    rows = ch.query("""
        SELECT toDate(p.time) as d,
               argMax(p.open, p.time) as open,
               argMax(p.high, p.time) as high,
               argMax(p.low, p.time) as low,
               argMax(p.close, p.time) as close,
               argMax(p.volume, p.time) as volume,
               argMax(o.yur_buy, p.time) as yur_buy,
               argMax(o.yur_sell, p.time) as yur_sell,
               argMax(o.fiz_buy, p.time) as fiz_buy,
               argMax(o.fiz_sell, p.time) as fiz_sell,
               argMax(o.total_oi, p.time) as total_oi
        FROM moex.prices_5m p
        INNER JOIN moex.prices_5m_oi o ON p.symbol = o.symbol AND p.time = o.time
        WHERE p.symbol = %(t)s AND p.time >= '2024-01-01' AND p.time <= '2026-05-01'
        GROUP BY d ORDER BY d
    """, parameters={'t': ticker}).result_rows
    if len(rows) < 60:
        return None
    a = np.array([list(r) for r in rows], dtype=object)
    dates = np.array([str(r[0]) for r in rows])
    opn = a[:, 1].astype(float); high = a[:, 2].astype(float); low = a[:, 3].astype(float)
    close = a[:, 4].astype(float); vol = a[:, 5].astype(float)
    yb = a[:, 6].astype(float); ys = a[:, 7].astype(float)
    fb = a[:, 8].astype(float); fs = a[:, 9].astype(float); toi = a[:, 10].astype(float)
    toi = np.where(toi <= 0, 1, toi)
    v_m = np.mean(vol) + 1; yb_m = np.mean(yb) + 1; ys_m = np.mean(ys) + 1; toi_m = np.mean(toi) + 1
    dv = np.diff(vol) / v_m; dyb = np.diff(yb) / yb_m; dys = np.diff(ys) / ys_m; dtoi = np.diff(toi) / toi_m
    fiz_net = (fb - fs) / toi * 100; dfn = np.diff(fiz_net)
    sma50 = np.full(len(close), np.nan)
    if len(close) >= 50:
        cs = np.cumsum(close); sma50[49] = cs[49] / 50; sma50[50:] = (cs[50:] - cs[:-50]) / 50
    return dict(dates=dates, opn=opn, high=high, low=low, close=close, vol=vol,
                dv=dv, dyb=dyb, dys=dys, dfn=dfn, dtoi=dtoi, sma50=sma50, n=len(rows))


def generate_signals(data, pattern_name, hold, sl_pct):
    """Generate all signal entries for a strategy."""
    pfunc = PATTERNS[pattern_name]
    dates = data['dates']; opn = data['opn']; high = data['high']
    low = data['low']; close = data['close']
    dv = data['dv']; dyb = data['dyb']; dys = data['dys']
    dfn = data['dfn']; dtoi = data['dtoi']; sma50 = data['sma50']
    n = len(close)
    
    signals = []
    for i in range(50, n - max(hold, 2)):
        if i >= len(dv) or i >= len(dfn) or i >= len(dtoi):
            break
        if not pfunc(dv[i], dyb[i], dys[i], dfn[i], dtoi[i]):
            continue
        if sma50 is not None and i < len(sma50) and not np.isnan(sma50[i]):
            if close[i] <= sma50[i]:
                continue
        ei = i + 1
        if ei >= n - 1:
            continue
        ep = float(opn[ei])
        xi = min(ei + hold, n - 1)
        sp = ep * (1 - sl_pct) if sl_pct > 0 else 0
        stop_hit = False
        xp = float(close[xi])
        if sl_pct > 0:
            for j in range(ei, xi + 1):
                if float(low[j]) <= sp:
                    xp = sp
                    stop_hit = True
                    break
        signals.append(dict(signal_date=dates[ei], entry_date=dates[ei], exit_date=dates[xi],
                            entry_idx=ei, exit_idx=xi,
                            entry_price=ep, exit_price=xp, stop_hit=stop_hit))
    return signals


def simulate_portfolio(portfolio_list, capital=200_000):
    """Run all signals concurrently sharing equity."""
    # Load data for all
    tickers_data = {}
    for sig in portfolio_list:
        t = sig['ticker']
        if t not in tickers_data:
            data = load_daily(t)
            if data:
                tickers_data[t] = data
    
    # Generate signal timelines for each
    all_signals = []
    for sig in portfolio_list:
        t = sig['ticker']
        if t not in tickers_data:
            continue
        data = tickers_data[t]
        cs = CS_MAP.get(t, 1)
        sigs = generate_signals(data, sig['pattern'], sig['hold'], sig['sl'])
        all_signals.append(dict(signal=sig, cs=cs, data=data, signals=sigs))
    
    # Merge all signal dates into one timeline
    eq = float(capital)
    peak = eq
    mdd = 0.0
    trades = []
    
    # Process signals chronologically
    # Each signal: entry_date, exit_date, entry_price, exit_price
    # We process in order of entry_date
    all_events = []
    for s in all_signals:
        for sg in s['signals']:
            sig_info = s['signal']
            go = sg['entry_price'] * s['cs']
            all_events.append(dict(
                ticker=sig_info['ticker'],
                pattern=sig_info['pattern'],
                hold=sig_info['hold'],
                sl=sig_info['sl'],
                cs=s['cs'],
                go=go,
                entry_date=sg['entry_date'],
                exit_date=sg['exit_date'],
                entry_price=sg['entry_price'],
                exit_price=sg['exit_price'],
                stop_hit=sg['stop_hit'],
            ))
    
    all_events.sort(key=lambda e: e['entry_date'])
    
    # Track active positions to prevent concurrent on same ticker
    active_by_ticker = {}
    
    for evt in all_events:
        ticker = evt['ticker']
        # Skip if already have an active position on this ticker
        if ticker in active_by_ticker:
            continue
        
        ep = evt['entry_price']
        go = evt['go']
        sl_pct = evt['sl']
        cs = evt['cs']
        
        if go <= 0:
            continue
        
        # Contract sizing
        risk_amount = eq * RISK_PCT
        if sl_pct > 0:
            base_nc = risk_amount / (go * sl_pct)
        else:
            base_nc = risk_amount / go * 5
        
        base_nc = max(1, int(base_nc))
        
        # Hard cap: max 5 contracts
        nc = min(base_nc, MAX_LOT)
        
        # Leverage cap
        max_by_lev = int(eq * MAX_PORTFOLIO_LEVERAGE / go) if go > 0 else 0
        nc = min(nc, max_by_lev)
        
        if nc < 1:
            continue
        
        # Mark position active
        active_by_ticker[ticker] = evt['exit_date']
        
        eq_before = eq
        gp = nc * cs * (evt['exit_price'] - ep)
        cm_val = nc * COMM
        npnl = gp - cm_val
        eq += npnl
        
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak * 100 if peak > 0 else 0
        mdd = max(mdd, dd)
        
        pnl_pct = npnl / eq_before * 100 if eq_before > 0 else 0
        trades.append(dict(ticker=ticker, pattern=evt['pattern'],
                           entry=evt['entry_date'], exit=evt['exit_date'],
                           ep=round(ep,2), xp=round(evt['exit_price'],2),
                           nc=nc, go=round(go,0),
                           gp=round(gp,0), cm=round(cm_val,0),
                           npnl=round(npnl,0), pnl_pct=round(pnl_pct,2),
                           stop=evt['stop_hit']))
    
    # Clean up active_by_ticker on exit dates (not needed for sequential)
    
    ret = (eq - capital) / capital * 100
    wins = sum(1 for t in trades if t['npnl'] > 0)
    wr = wins / len(trades) * 100 if trades else 0
    gp_sum = sum(t['npnl'] for t in trades if t['npnl'] > 0)
    gl_sum = sum(t['npnl'] for t in trades if t['npnl'] < 0)
    pf = abs(gp_sum / (gl_sum + 1))
    tr_comm = sum(t['cm'] for t in trades)
    net_pnl = gp_sum + gl_sum
    
    return dict(ret=round(ret,2), mdd=round(mdd,2), wr=round(wr,1), pf=round(pf,2),
                trades=len(trades), wins=wins, net_pnl=round(net_pnl,0),
                comm=round(tr_comm,0)), trades


# ── Run ────────────────────────────────────────────────────────────────
for capital in [200_000, 100_000, 50_000, 25_000]:
    print(f"\n{'='*60}")
    print(f"PORTFOLIO: CAPITAL = {capital:>8,} RUB (max {MAX_LOT} contracts/signal)")
    print(f"{'='*60}")
    result, trades = simulate_portfolio(PORTFOLIO_SIGNALS, capital)
    print(f"  Ret: {result['ret']:>+8.2f}%  DD: {result['mdd']:>5.2f}%  WR: {result['wr']:>4.1f}%")
    print(f"  PF: {result['pf']:>5.2f}  Trades: {result['trades']:>3d}  Net: {result['net_pnl']:>+10,.0f}  Comm: {result['comm']:>8,.0f}")
    
    # By ticker
    by_ticker = {}
    for t in trades:
        k = t['ticker']
        if k not in by_ticker:
            by_ticker[k] = {'trades': 0, 'wins': 0, 'pnl': 0}
        by_ticker[k]['trades'] += 1
        if t['npnl'] > 0:
            by_ticker[k]['wins'] += 1
        by_ticker[k]['pnl'] += t['npnl']
    for t, v in sorted(by_ticker.items(), key=lambda x: -x[1]['pnl']):
        wr_t = v['wins']/v['trades']*100 if v['trades'] else 0
        print(f"    {t}: {v['trades']:>3d}tr WR={wr_t:>.0f}% PnL={v['pnl']:>+10,.0f}")
    
    # Save
    with open(f'/home/user/projects/TQA-MOEX/reports/triz_300pct/portfolio_{capital}.json', 'w') as f:
        json.dump({'capital': capital, 'params': {'max_lot': MAX_LOT, 'risk_pct': RISK_PCT, 'max_leverage': MAX_PORTFOLIO_LEVERAGE},
                   'result': result, 'trades': trades}, f, indent=2)

print(f"\n{'='*60}")
print(f"DETAILED: 200,000 RUB - all trades")
print(f"{'='*60}")
result, trades = simulate_portfolio(PORTFOLIO_SIGNALS, 200_000)
# Monthly breakdown
from collections import defaultdict
by_month = defaultdict(lambda: {'trades': 0, 'pnl': 0, 'wins': 0})
for t in trades:
    ym = t['entry'][:7]
    by_month[ym]['trades'] += 1
    by_month[ym]['pnl'] += t['npnl']
    if t['npnl'] > 0:
        by_month[ym]['wins'] += 1
print("\nMonthly breakdown:")
print(f"{'Month':<8} {'Trades':<7} {'WR':<6} {'PnL':<12}")
for ym in sorted(by_month.keys()):
    v = by_month[ym]
    wr_m = v['wins']/v['trades']*100 if v['trades'] else 0
    print(f"{ym:<8} {v['trades']:<7} {wr_m:<5.0f}% {v['pnl']:>+10,.0f}")

print("\nDone.")
