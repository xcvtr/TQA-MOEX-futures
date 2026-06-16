#!/usr/bin/env python3
"""Re-run portfolio with sequential backtest (not walk-forward), reinvest, max lot cap."""
import sys, os, json, math
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/../..')
os.chdir(os.path.dirname(os.path.abspath(__file__)) + '/../..')

import numpy as np
import clickhouse_connect
from config import CH_HOST, CH_PORT, CH_DB

COMM = 4
RISK_PCT = 0.02
MAX_CONTRACTS_MULT = 5  # max multiplier from initial lot
MAX_PORTFOLIO_LEVERAGE = 3.0  # total notional / equity

CS_MAP = {
    'AF': 100, 'AL': 25, 'AU': 1, 'BM': 10, 'BR': 10, 'CC': 10, 'CE': 100, 'CH': 100,
    'CNYRUBF': 1000, 'CR': 10, 'DX': 10000, 'ED': 1000, 'EURRUBF': 1000, 'Eu': 1000,
    'FF': 100, 'GAZPF': 100, 'GD': 10, 'GK': 100, 'GL': 10, 'GLDRUBF': 10,
    'GZ': 100, 'HS': 100, 'HY': 1000, 'IB': 100, 'IMOEXF': 10, 'KC': 100,
    'LK': 100, 'MC': 100, 'ME': 100, 'MG': 100, 'MM': 1, 'MN': 100, 'MX': 1,
    'MY': 1, 'NA': 1, 'NG': 100, 'NM': 100, 'NR': 1, 'OJ': 10, 'PD': 10,
    'PT': 10, 'RB': 1000, 'RI': 10, 'RL': 1, 'RM': 10, 'RN': 100,
    'SBERF': 100, 'SE': 100, 'SF': 1000, 'Si': 1000, 'SN': 100, 'SP': 100,
    'SR': 100, 'SS': 100, 'SV': 1, 'TN': 100, 'TT': 1, 'UC': 1000,
    'USDRUBF': 1000, 'VB': 1000, 'VI': 1, 'W4': 1, 'X5': 100, 'YD': 100,
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
    dates = [str(r[0]) for r in rows]
    opn = a[:, 1].astype(float); high = a[:, 2].astype(float); low = a[:, 3].astype(float)
    close = a[:, 4].astype(float); vol = a[:, 5].astype(float)
    yb = a[:, 6].astype(float); ys = a[:, 7].astype(float)
    fb = a[:, 8].astype(float); fs = a[:, 9].astype(float); toi = a[:, 10].astype(float)
    
    toi = np.where(toi <= 0, 1, toi)
    v_m = np.mean(vol) + 1
    yb_m = np.mean(yb) + 1
    ys_m = np.mean(ys) + 1
    toi_m = np.mean(toi) + 1
    dv = np.diff(vol) / v_m
    dyb = np.diff(yb) / yb_m
    dys = np.diff(ys) / ys_m
    dtoi = np.diff(toi) / toi_m
    fiz_net = (fb - fs) / toi * 100
    dfn = np.diff(fiz_net)
    sma50 = np.full(len(close), np.nan)
    if len(close) >= 50:
        cs = np.cumsum(close)
        sma50[49] = cs[49] / 50
        sma50[50:] = (cs[50:] - cs[:-50]) / 50
    
    return dict(dates=dates, opn=opn, high=high, low=low, close=close, vol=vol,
                dv=dv, dyb=dyb, dys=dys, dfn=dfn, dtoi=dtoi, sma50=sma50, n=len(rows))


def simulate_sequential(signal, data, capital, cs):
    """Sequential backtest (not walk-forward) across entire period with reinvest."""
    pname = signal['pattern']
    pfunc = PATTERNS[pname]
    hold = signal['hold']
    sl_pct = signal['sl']
    tf = signal.get('timeframe', 'daily')
    
    dates = data['dates']; opn = data['opn']; high = data['high']
    low = data['low']; close = data['close']
    dv = data['dv']; dyb = data['dyb']; dys = data['dys']
    dfn = data['dfn']; dtoi = data['dtoi']; sma50 = data['sma50']
    n = len(close)
    
    # Find first valid index: need features at i, and sma50[i] not nan
    first_i = 0
    for i in range(50, n - max(hold, 5)):
        if pfunc(dv[i], dyb[i], dys[i], dfn[i], dtoi[i]):
            if sma50 is not None and i < len(sma50) and not np.isnan(sma50[i]):
                if close[i] > sma50[i]:
                    first_i = i
                    break
            else:
                first_i = i
                break
    
    if first_i == 0:
        return None
    
    eq = float(capital)
    peak = eq
    mdd = 0.0
    trades = []
    
    # Calculate initial notional to determine max contracts
    init_notional = None
    
    for i in range(first_i, n - max(hold, 2)):
        if i >= len(dv) or i >= len(dfn) or i >= len(dtoi):
            break
        
        if not pfunc(dv[i], dyb[i], dys[i], dfn[i], dtoi[i]):
            continue
        
        # Trend filter
        if sma50 is not None and i < len(sma50) and not np.isnan(sma50[i]):
            if close[i] <= sma50[i]:
                continue
        
        ei = i + 1
        if ei >= n - 1:
            continue
        
        ep = float(opn[ei])
        xi = min(ei + hold, n - 1)
        
        # Stop
        sp = ep * (1 - sl_pct) if sl_pct > 0 else 0
        stop_hit = False
        xp = float(close[xi])
        if sl_pct > 0:
            for j in range(ei, xi + 1):
                if float(low[j]) <= sp:
                    xp = sp
                    stop_hit = True
                    break
        
        go = ep * cs
        if go <= 0:
            continue
        
        # Contract sizing: risk-based with cap
        notional_per_contract = go
        
        if init_notional is None:
            init_notional = notional_per_contract
            initial_max_contracts = max(1, int(capital * RISK_PCT / (go * max(sl_pct, 0.005))))
            max_contracts = initial_max_contracts * MAX_CONTRACTS_MULT
        else:
            max_contracts = max(1, int(capital * RISK_PCT / (init_notional * max(sl_pct, 0.005)))) * MAX_CONTRACTS_MULT
        
        # Risk-based sizing
        if sl_pct > 0:
            base_nc = eq * RISK_PCT / (go * sl_pct)
        else:
            base_nc = eq * RISK_PCT / go * 5
        base_nc = max(1, int(base_nc))
        nc = min(base_nc, max_contracts)
        
        # Portfolio leverage cap
        max_by_leverage = int(eq * MAX_PORTFOLIO_LEVERAGE / notional_per_contract) if notional_per_contract > 0 else 999999
        nc = min(nc, max_by_leverage)
        
        if nc < 1:
            continue
        
        eq_before = eq
        gp = nc * cs * (xp - ep)
        cm_val = nc * COMM
        npnl = gp - cm_val
        eq += npnl
        
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak * 100 if peak > 0 else 0
        mdd = max(mdd, dd)
        
        pnl_pct = npnl / eq_before * 100 if eq_before > 0 else 0
        trades.append(dict(entry=dates[ei], exit=dates[xi],
                           ep=round(ep,2), xp=round(xp,2),
                           nc=nc, gp=round(gp,0), cm=round(cm_val,0),
                           npnl=round(npnl,0), pnl_pct=round(pnl_pct,2),
                           stop=stop_hit))
    
    ret = (eq - capital) / capital * 100
    wins = sum(1 for t in trades if t['npnl'] > 0)
    wr = wins / len(trades) * 100 if trades else 0
    gp_sum = sum(t['npnl'] for t in trades if t['npnl'] > 0)
    gl_sum = sum(t['npnl'] for t in trades if t['npnl'] < 0)
    pf = abs(gp_sum / (gl_sum + 1))
    tr_comm = sum(t['cm'] for t in trades)
    
    return dict(ret=round(ret,2), mdd=round(mdd,2), wr=round(wr,1), pf=round(pf,2),
                trades=len(trades), wins=wins, net_pnl=round(gp_sum+gl_sum,0),
                comm=round(tr_comm,0), first_signal=dates[first_i] if trades else None)


def estimate_min_capital(signal):
    """Estimate minimum capital to trade 1 contract of this signal."""
    ticker = signal['ticker']
    cs = CS_MAP.get(ticker, 1)
    data = load_daily(ticker)
    if data is None:
        return None
    # Get average close price
    avg_price = np.mean(data['close'][50:])
    go = avg_price * cs
    # Need at least 2 * GO (exchange margin rule of thumb) + buffer
    min_cap = go * 2
    return min_cap


# ── Main ────────────────────────────────────────────────────────────────
for capital in [200_000, 100_000, 50_000, 25_000]:
    print(f"\n{'='*60}")
    print(f"CAPITAL = {capital:>8,} RUB")
    print(f"{'='*60}")
    
    results = []
    any_ok = False
    
    for sig in PORTFOLIO_SIGNALS:
        ticker = sig['ticker']
        cs = CS_MAP.get(ticker, 1)
        data = load_daily(ticker)
        if data is None:
            print(f"  {ticker}: no data")
            continue
        
        r = simulate_sequential(sig, data, capital, cs)
        if r is None or r['trades'] == 0:
            print(f"  {ticker}: 0 trades")
            continue
        
        print(f"  {ticker} {sig['pattern']:>20} hold={sig['hold']} sl={sig['sl']:.0%}"
              f"  ret={r['ret']:>+8.2f}%  DD={r['mdd']:>5.2f}%  WR={r['wr']:>4.1f}%"
              f"  PF={r['pf']:>5.2f}  tr={r['trades']:>3d}")
        any_ok = True
    
    if not any_ok:
        print("  — ни один сигнал не дал сделок, капитал мал")

# Detailed with 200K
print(f"\n\n{'='*60}")
print(f"DETAILED: 200,000 RUB")
print(f"{'='*60}")

capital = 200_000

for sig in PORTFOLIO_SIGNALS:
    ticker = sig['ticker']
    cs = CS_MAP.get(ticker, 1)
    data = load_daily(ticker)
    if data is None:
        continue
    
    r = simulate_sequential(sig, data, capital, cs)
    if r is None or r['trades'] == 0:
        continue
    
    print(f"\n--- {ticker} {sig['pattern']} hold={sig['hold']} sl={sig['sl']:.0%} ---")
    print(f"  Return: {r['ret']:+.2f}%")
    print(f"  Max DD: {r['mdd']:.2f}%")
    print(f"  Trades: {r['trades']} (W: {r['wins']}, WR: {r['wr']:.1f}%)")
    print(f"  Profit Factor: {r['pf']:.2f}")
    print(f"  Net PnL: {r['net_pnl']:>+,.0f}  Comm: {r['comm']:>+,.0f}")
    print(f"  First signal: {r['first_signal']}")

print("\nDone.")
