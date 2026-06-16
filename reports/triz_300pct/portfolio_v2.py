#!/usr/bin/env python3
"""Portfolio simulation: each signal runs independent sequential backtest, equity pooled."""
import sys, os, json, math
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/../..')
os.chdir(os.path.dirname(os.path.abspath(__file__)) + '/../..')

import numpy as np
import clickhouse_connect
from config import CH_HOST, CH_PORT, CH_DB

COMM = 4
RISK_PCT = 0.02
MAX_LOT = 5
MAX_PORTFOLIO_LEVERAGE = 5.0  # per signal

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


def sequential_curve(signal, data, capital, cs):
    """Run sequential backtest, return list of (date, equity, trade) tuples."""
    pfunc = PATTERNS[signal['pattern']]
    hold = signal['hold']; sl_pct = signal['sl']
    dates = data['dates']; opn = data['opn']; high = data['high']
    low = data['low']; close = data['close']
    dv = data['dv']; dyb = data['dyb']; dys = data['dys']
    dfn = data['dfn']; dtoi = data['dtoi']; sma50 = data['sma50']
    n = len(close)
    
    eq = float(capital)
    peak = eq
    points = [(dates[0], eq, None)]  # (date, equity, trade_info)
    
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
        
        risk_amount = eq * RISK_PCT
        if sl_pct > 0:
            base_nc = risk_amount / (go * sl_pct)
        else:
            base_nc = risk_amount / go * 5
        base_nc = max(1, int(base_nc))
        nc = min(base_nc, MAX_LOT)
        max_by_lev = int(eq * MAX_PORTFOLIO_LEVERAGE / go) if go > 0 else 999
        nc = min(nc, max_by_lev)
        if nc < 1: continue
        
        eq_before = eq
        gp = nc * cs * (xp - ep)
        cm_val = nc * COMM
        npnl = gp - cm_val
        eq += npnl
        if eq > peak: peak = eq
        dd = (peak - eq) / peak * 100 if peak > 0 else 0
        
        points.append((dates[xi], eq, dict(
            ticker=signal['ticker'], pattern=signal['pattern'],
            date=dates[ei], exit_date=dates[xi],
            ep=round(ep,2), xp=round(xp,2), nc=nc,
            go=round(go,0), npnl=round(npnl,0), stop=stop_hit)))
    
    if len(points) <= 1:
        return []
    
    return points


def merge_curves(curves_list):
    """Merge multiple equity curves into one, sorted by date."""
    all_pts = []
    for pts in curves_list:
        all_pts.extend(pts)
    all_pts.sort(key=lambda p: p[0])
    return all_pts


# ── Run ────────────────────────────────────────────────────────────────
for capital in [200_000, 100_000, 50_000]:
    print(f"\n{'='*60}")
    print(f"PORTFOLIO: CAPITAL = {capital:>8,} RUB (max {MAX_LOT} contracts/signal)")
    print(f"{'='*60}")
    
    curves = []
    total_trades = []
    
    for sig in PORTFOLIO_SIGNALS:
        ticker = sig['ticker']
        cs = CS_MAP.get(ticker, 1)
        data = load_daily(ticker)
        if data is None:
            print(f"  {ticker}: no data")
            continue
        
        # Calculate individual allocation for this signal
        sig_capital = capital / len(PORTFOLIO_SIGNALS)
        
        pts = sequential_curve(sig, data, sig_capital, cs)
        if not pts:
            print(f"  {ticker}: 0 trades")
            continue
        
        trades_in = [p[2] for p in pts if p[2] is not None]
        ret = (pts[-1][1] - sig_capital) / sig_capital * 100
        
        # Max DD from this curve
        peak_curve = sig_capital
        mdd = 0
        for p in pts:
            eq_v = p[1]
            if eq_v > peak_curve: peak_curve = eq_v
            dd = (peak_curve - eq_v) / peak_curve * 100 if peak_curve > 0 else 0
            mdd = max(mdd, dd)
        
        wins = sum(1 for t in trades_in if t['npnl'] > 0)
        wr = wins / len(trades_in) * 100 if trades_in else 0
        
        print(f"  {ticker} {sig['pattern']:>20}: ret={ret:>+8.2f}%  DD={mdd:>5.2f}%  WR={wr:>4.1f}%  tr={len(trades_in):>3d}")
        total_trades.extend(trades_in)
        curves.append(pts)
    
    # Merge all curves by date
    all_pts = merge_curves(curves)
    
    if not all_pts:
        print("  — нет сделок")
        continue
    
    # Build combined equity from merged timeline
    # We track sum of individual equities at each point
    eq_sum = {}
    latest = {}
    for pts in curves:
        for p in pts:
            dt, eq_v, tr = p
            eq_sum[dt] = eq_sum.get(dt, 0) + eq_v
            latest[dt] = True
    
    sorted_dates = sorted(eq_sum.keys())
    if len(sorted_dates) < 2:
        print("  — не хватает точек для equity кривой")
        continue
    
    # Get first capital contribution
    total_start = sum(pts[0][1] for pts in curves if pts)
    
    # Final equity = sum of last points
    total_end = eq_sum[sorted_dates[-1]]
    
    total_ret = (total_end - total_start) / total_start * 100
    
    # Max DD on total equity
    peak_total = total_start
    total_mdd = 0
    for dt in sorted_dates:
        e = eq_sum[dt]
        if e > peak_total: peak_total = e
        dd = (peak_total - e) / peak_total * 100 if peak_total > 0 else 0
        total_mdd = max(total_mdd, dd)
    
    # Portfolio metrics
    wins_total = sum(1 for t in total_trades if t['npnl'] > 0)
    wr_total = wins_total / len(total_trades) * 100 if total_trades else 0
    gp_sum = sum(t['npnl'] for t in total_trades if t['npnl'] > 0)
    gl_sum = sum(t['npnl'] for t in total_trades if t['npnl'] < 0)
    pf_total = abs(gp_sum / (gl_sum + 1))
    net_pnl = gp_sum + gl_sum
    total_comm = sum(t['npnl'] for t in total_trades) - sum(t['npnl'] for t in total_trades)  # placeholder
    comm_total = sum(4 * t['nc'] for t in total_trades)
    
    total_capital_used = len(PORTFOLIO_SIGNALS) * capital / len(PORTFOLIO_SIGNALS)  # = capital
    calmar = total_ret / total_mdd if total_mdd > 0 else 0
    
    print(f"\n  ─── PORTFOLIO TOTAL ───")
    print(f"  Return: {total_ret:>+8.2f}%")
    print(f"  Max DD: {total_mdd:>5.2f}%")
    print(f"  Calmar: {calmar:>6.2f}")
    print(f"  WR: {wr_total:>4.1f}% ({wins_total}/{len(total_trades)})")
    print(f"  PF: {pf_total:>5.2f}")
    print(f"  Net PnL: {net_pnl:>+10,.0f}  Comm: {comm_total:>8,.0f}")
    
    # By ticker
    by_ticker = {}
    for t in total_trades:
        k = t['ticker']
        if k not in by_ticker:
            by_ticker[k] = {'trades': 0, 'wins': 0, 'pnl': 0}
        by_ticker[k]['trades'] += 1
        if t['npnl'] > 0:
            by_ticker[k]['wins'] += 1
        by_ticker[k]['pnl'] += t['npnl']
    print(f"\n  By ticker:")
    for t, v in sorted(by_ticker.items(), key=lambda x: -x[1]['pnl']):
        wr_t = v['wins']/v['trades']*100 if v['trades'] else 0
        print(f"    {t}: {v['trades']:>3d}tr WR={wr_t:>3.0f}% PnL={v['pnl']:>+10,.0f}")
    
    # Save
    out = dict(capital=capital, ret=round(total_ret,2), mdd=round(total_mdd,2),
               calmar=round(calmar,2), wr=round(wr_total,1), pf=round(pf_total,2),
               trades=len(total_trades), net_pnl=round(net_pnl,0),
               comm=round(comm_total,0),
               by_ticker={t: v for t, v in sorted(by_ticker.items(), key=lambda x: -x[1]['pnl'])})
    with open(f'/home/user/projects/TQA-MOEX/reports/triz_300pct/portfolio_v2_{capital}.json', 'w') as f:
        json.dump(out, f, indent=2)

print("\nDone.")
