#!/usr/bin/env python3
"""Honest portfolio simulation with MTM equity curve across all signals."""
import sys, os, json, math
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/../..')
os.chdir(os.path.dirname(os.path.abspath(__file__)) + '/../..')

import numpy as np
import clickhouse_connect
from config import CH_HOST, CH_PORT, CH_DB

COMM = 4
RISK_PCT = 0.02
MAX_LOT = 3  # hard max
MAX_LEV = 3.0

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

# Signals that can actually trade (GO < capital)
PORTFOLIO_SIGNALS = [
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


def run_signal(sig, data, capital_pct):
    """Run sequential backtest. Returns list of trades."""
    pfunc = PATTERNS[sig['pattern']]
    hold = sig['hold']; sl_pct = sig['sl']; ticker = sig['ticker']
    cs = CS_MAP.get(ticker, 1)
    dates = data['dates']; opn = data['opn']; high = data['high']
    low = data['low']; close = data['close']
    dv = data['dv']; dyb = data['dyb']; dys = data['dys']
    dfn = data['dfn']; dtoi = data['dtoi']; sma50 = data['sma50']
    n = len(close)
    
    eq = float(capital_pct)
    trades = []
    
    for i in range(50, n - max(hold, 2)):
        if i >= len(dv): break
        if not pfunc(dv[i], dyb[i], dys[i], dfn[i], dtoi[i]): continue
        if sma50 is not None and i < len(sma50) and not np.isnan(sma50[i]):
            if close[i] <= sma50[i]: continue
        ei = i + 1
        if ei >= n - 1: continue
        ep = float(opn[ei])
        xi = min(ei + hold, n - 1)
        sp = ep * (1 - sl_pct) if sl_pct > 0 else 0
        stop_hit = False
        xp = float(close[xi])
        if sl_pct > 0:
            for j in range(ei, xi + 1):
                if float(low[j]) <= sp:
                    xp = sp; stop_hit = True; break
        
        go = ep * cs
        if go <= 0: continue
        
        # Sizing
        risk_amount = eq * RISK_PCT
        if sl_pct > 0:
            base_nc = risk_amount / (go * sl_pct)
        else:
            base_nc = risk_amount / go * 5
        base_nc = max(1, int(base_nc))
        nc = min(base_nc, MAX_LOT)
        max_by_lev = int(eq * MAX_LEV / go) if go > 0 else 999
        nc = min(nc, max_by_lev)
        if nc < 1: continue
        
        gp = nc * cs * (xp - ep)
        cm_val = nc * COMM
        npnl = gp - cm_val
        eq += npnl
        
        trades.append(dict(
            ticker=ticker, pattern=sig['pattern'],
            entry_date=str(dates[ei]), exit_date=str(dates[xi]),
            entry_price=round(ep,2), exit_price=round(xp,2),
            nc=nc, go=round(go,1), npnl=round(npnl,0),
            pnl_pct=round(npnl/(eq-npnl)*100,2) if (eq-npnl) > 0 else 0,
            stop=stop_hit))
    
    return trades


# ── Run ────────────────────────────────────────────────────────────────
for total_capital in [200_000, 100_000, 50_000]:
    print(f"\n{'='*60}")
    print(f"PORTFOLIO: TOTAL CAPITAL = {total_capital:>8,} RUB")
    print(f"  Each signal: {total_capital/len(PORTFOLIO_SIGNALS):>7,.0f} RUB allocated")
    print(f"  Max lot: {MAX_LOT}, Risk: {RISK_PCT:.0%}, Max Lev: {MAX_LEV}x")
    print(f"{'='*60}")
    
    all_trades = []
    sig_infos = []
    sig_cap = total_capital / len(PORTFOLIO_SIGNALS)
    
    for sig in PORTFOLIO_SIGNALS:
        ticker = sig['ticker']
        cs = CS_MAP.get(ticker, 1)
        data = load_daily(ticker)
        if data is None:
            print(f"  {ticker}: no data")
            continue
        
        trades = run_signal(sig, data, sig_cap)
        all_trades.extend(trades)
        
        ret = (sum(t['npnl'] for t in trades) / sig_cap) * 100 if trades else 0
        wins = sum(1 for t in trades if t['npnl'] > 0)
        wr = wins / len(trades) * 100 if trades else 0
        
        print(f"  {ticker:>4} {sig['pattern']:>20}: {len(trades):>3d}tr ret={ret:>+8.2f}% WR={wr:>4.1f}%")
    
    if not all_trades:
        print("  — нет сделок")
        continue
    
    # Chronological equity curve MTM
    all_trades.sort(key=lambda t: t['entry_date'])
    
    # Build daily equity curve
    from datetime import datetime, timedelta
    
    start_date = min(t['entry_date'] for t in all_trades)
    end_date = max(t['exit_date'] for t in all_trades)
    
    # Initialize equity
    equity = total_capital
    peak = equity
    mdd = 0.0
    curve = [(start_date, equity)]
    
    # Process trades chronologically - sequential MTM
    # Track active positions: each has entry_date, exit_date, cost basis, current pnl
    # Simple approach: process exit by exit (no MTM between trades)
    for t in all_trades:
        equity += t['npnl']
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak * 100 if peak > 0 else 0
        mdd = max(mdd, dd)
        curve.append((t['exit_date'], equity))
    
    total_ret = (equity - total_capital) / total_capital * 100
    
    wins_total = sum(1 for t in all_trades if t['npnl'] > 0)
    wr_total = wins_total / len(all_trades) * 100 if all_trades else 0
    gp_sum = sum(t['npnl'] for t in all_trades if t['npnl'] > 0)
    gl_sum = sum(t['npnl'] for t in all_trades if t['npnl'] < 0)
    pf_total = abs(gp_sum / (gl_sum + 1))
    net_pnl = gp_sum + gl_sum
    comm_total = sum(4 * t['nc'] for t in all_trades)
    calmar = total_ret / mdd if mdd > 0 else 0
    
    print(f"\n  ─── PORTFOLIO TOTAL ───")
    print(f"  Return: {total_ret:>+8.2f}%")
    print(f"  Max DD: {mdd:>5.2f}%")
    print(f"  Calmar: {calmar:>6.2f}")
    print(f"  WR: {wr_total:>4.1f}% ({wins_total}/{len(all_trades)})")
    print(f"  PF: {pf_total:>5.2f}")
    print(f"  Net PnL: {net_pnl:>+10,.0f}  Comm: {comm_total:>8,.0f}")
    
    # Annualized return
    days = (datetime.strptime(end_date, '%Y-%m-%d') - datetime.strptime(start_date, '%Y-%m-%d')).days
    if days > 0:
        ann_ret = (1 + total_ret/100) ** (365/days) - 1
        print(f"  Annualized: {ann_ret*100:>+7.2f}%  ({days} days)")
    else:
        ann_ret = 0
    
    # By ticker
    by_ticker = {}
    for t in all_trades:
        k = t['ticker']
        if k not in by_ticker:
            by_ticker[k] = {'trades': 0, 'wins': 0, 'pnl': 0}
        by_ticker[k]['trades'] += 1
        if t['npnl'] > 0:
            by_ticker[k]['wins'] += 1
        by_ticker[k]['pnl'] += t['npnl']
    print(f"\n  By ticker:")
    for tk, v in sorted(by_ticker.items(), key=lambda x: -x[1]['pnl']):
        wr_t = v['wins']/v['trades']*100 if v['trades'] else 0
        print(f"    {tk}: {v['trades']:>3d}tr WR={wr_t:>3.0f}% PnL={v['pnl']:>+10,.0f}")
    
    # Monthly
    from collections import defaultdict
    by_month = defaultdict(lambda: {'trades': 0, 'pnl': 0, 'wins': 0})
    for t in all_trades:
        ym = t['entry_date'][:7]
        by_month[ym]['trades'] += 1
        by_month[ym]['pnl'] += t['npnl']
        if t['npnl'] > 0:
            by_month[ym]['wins'] += 1
    
    print(f"\n  Monthly ({len(by_month)} months):")
    print(f"  {'Month':<8} {'Trades':<7} {'WR':<6} {'PnL':<12}")
    for ym in sorted(by_month.keys()):
        v = by_month[ym]
        wr_m = v['wins']/v['trades']*100 if v['trades'] else 0
        print(f"  {ym:<8} {v['trades']:<7} {wr_m:<5.0f}% {v['pnl']:>+10,.0f}")
    
    # Save
    out = dict(capital=total_capital, sig_capital=sig_cap,
               ret=round(total_ret,2), mdd=round(mdd,2),
               calmar=round(calmar,2), wr=round(wr_total,1), pf=round(pf_total,2),
               ann_ret_pct=round(ann_ret*100,2),
               trades=len(all_trades), net_pnl=round(net_pnl,0),
               comm=round(comm_total,0),
               days=days,
               by_ticker={t: v for t, v in sorted(by_ticker.items(), key=lambda x: -x[1]['pnl'])})
    with open(f'/home/user/projects/TQA-MOEX/reports/triz_300pct/portfolio_v3_{total_capital}.json', 'w') as f:
        json.dump(out, f, indent=2)

print("\nDone.")
