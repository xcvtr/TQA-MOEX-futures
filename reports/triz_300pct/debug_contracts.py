#!/usr/bin/env python3
"""Debug: show contract counts per trade for PT."""
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
    v_m = np.mean(vol) + 1; yb_m = np.mean(yb) + 1; ys_m = np.mean(ys) + 1; toi_m = np.mean(toi) + 1
    dv = np.diff(vol) / v_m; dyb = np.diff(yb) / yb_m; dys = np.diff(ys) / ys_m; dtoi = np.diff(toi) / toi_m
    fiz_net = (fb - fs) / toi * 100; dfn = np.diff(fiz_net)
    sma50 = np.full(len(close), np.nan)
    if len(close) >= 50:
        cs = np.cumsum(close); sma50[49] = cs[49] / 50; sma50[50:] = (cs[50:] - cs[:-50]) / 50
    return dict(dates=dates, opn=opn, high=high, low=low, close=close, vol=vol,
                dv=dv, dyb=dyb, dys=dys, dfn=dfn, dtoi=dtoi, sma50=sma50, n=len(rows))

PT_CS = 10  # contract size

data = load_daily('PT')
a = np.array(data['close'][50:])
print(f"PT close range: {np.min(a):.2f} - {np.max(a):.2f}, mean: {np.mean(a):.2f}")

# Single contract GO: price * 10 (cs)
avg_go = np.mean(data['close'][50:]) * PT_CS
print(f"Avg GO per contract: {avg_go:,.0f}")

# At 100K capital:
capital = 100_000
max_contracts_capital = capital * MAX_PORTFOLIO_LEVERAGE / avg_go
print(f"Max contracts at 100K (3x lev): {max_contracts_capital:.1f}")

# Risk-based: 2% of 100K = 2000RUR risk per trade
# With 1% SL: 2000 / (price*10*0.01) = 2000 / (avg_price*10*0.01)
sl_risk_per_contract = avg_go * 0.01
print(f"SL risk per contract (1% of GO): {sl_risk_per_contract:,.0f}")
base = capital * 0.02 / sl_risk_per_contract
print(f"Base contracts at 100K (risk-based): {base:.1f}")

# What does the code actually compute?
# base_nc = eq * RISK_PCT / (go * sl_pct)
# = 100_000 * 0.02 / (avg_price*10 * 0.01)
# = 2000 / (avg_price*0.1)
# avg_price of PT ~ 1300
# = 2000 / 130 = ~15 contracts
print(f"\nCode computation: 100_000 * 0.02 / (avg_price*10*0.01)")
print(f"  = 2000 / ({np.mean(data['close'][50:]):.0f}*10*0.01)")
print(f"  = 2000 / ({np.mean(data['close'][50:])*10*0.01:.0f})")
print(f"  = {2000 / (np.mean(data['close'][50:])*10*0.01):.1f} contracts")

# Max leverage check: 3x * 100K / (price*10)
max_by_lev = 100_000 * 3 / (np.mean(data['close'][50:]) * 10)
print(f"\nMax by leverage (3x): {max_by_lev:.1f}")

# With MAX_CONTRACTS_MULT=5 from initial:
initial_max = max(1, int(capital * RISK_PCT / (avg_go * max(0.01, 0.005))))
print(f"\nInitial max contracts (raw): {initial_max}")
print(f"After *5 mult: {initial_max * 5}")

# Actually let's trace the full formula for first trade
print(f"\n{'='*60}")
print(f"TRACE: First trade @ 100K")
print(f"{'='*60}")

# Use the exact prices
close = data['close']
dates = data['dates']
opn = data['opn']
high = data['high']
low = data['low']
dv = data['dv']; dyb = data['dyb']; dys = data['dys']
dfn = data['dfn']; dtoi = data['dtoi']; sma50 = data['sma50']

n = len(close)
first_trade = None
for i in range(50, n - 5):
    if i >= len(dv) or i >= len(dfn) or i >= len(dtoi):
        break
    if not (dv[i] > 0 and abs(dfn[i]) > 5):
        continue
    if sma50 is not None and i < len(sma50) and not np.isnan(sma50[i]):
        if close[i] <= sma50[i]:
            continue
    ei = i + 1
    if ei >= n - 1:
        continue
    ep = float(opn[ei])
    go = ep * PT_CS
    sl_pct = 0.01
    
    base_nc = capital * RISK_PCT / (go * sl_pct)
    print(f"  Date: {dates[ei]}, Entry: {ep:.2f}, GO: {go:,.0f}")
    print(f"  base_nc = {capital} * {RISK_PCT} / ({go:.0f} * {sl_pct})")
    print(f"          = {capital * RISK_PCT:.0f} / {go * sl_pct:.0f}")
    print(f"          = {capital * RISK_PCT / (go * sl_pct):.1f}")
    
    init_max = max(1, int(capital * RISK_PCT / (go * max(sl_pct, 0.005))))
    print(f"  init_max (max(1, int())): {init_max}")
    actual_max = init_max * 5
    print(f"  actual_max (init*5): {actual_max}")
    
    max_by_lev_actual = int(capital * 3 / go)
    print(f"  max_by_leverage: {max_by_lev_actual}")
    
    nc = min(int(base_nc), init_max * 5)
    nc = min(nc, max_by_lev_actual)
    nc = max(1, nc) if nc >= 1 else 0
    print(f"  Final nc: {nc}")
    print(f"  Notional: {nc * go:,.0f} vs capital {capital:,.0f}")
    print(f"  Leverage: {nc * go / capital:.1f}x")
    
    first_trade = dict(i=i, ep=ep, nc=nc)
    break
