"""Clean portfolio with capped leverage, patterns only, both directions confirmed."""
import sys, os
sys.path.insert(0, '/home/user/projects/TQA-MOEX')
os.chdir('/home/user/projects/TQA-MOEX')
import numpy as np
import clickhouse_connect
from config import CH_HOST, CH_PORT, CH_DB

ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)

TICKERS = ['NM', 'VB', 'SR', 'Eu']
CAPITAL = 200_000
COMM = 4
MAX_LOT = 5
RISK_PCT = 0.02
MAX_LEV = 3.0  # hard cap on leverage

CS_MAP = {'NM':10, 'VB':100, 'SR':100, 'Eu':1000}

# Configs that worked well in both directions
CONFIGS = [
    # (ticker, direction, pattern, hold, atr_mult)
    ('NM', 'short', 'vol_up_yb_down_fiz_up', 21, 3),
    ('NM', 'long',  'vol_up_oi_down', 13, 3),
    ('VB', 'long',  'vol_up_oi_down', 5, 2),
    ('SR', 'long',  'vol_up_yb_down_fiz_up', 21, 2),
    ('SR', 'short', 'vol_up_oi_down', 8, 2),
    ('Eu', 'long',  'vol_up_oi_down', 8, 3),
]

PATTERNS = {
    'vol_up_oi_up_yb_up': lambda dv,dyb,dys,dfn,dtoi: dv>0 and dtoi>0 and dyb>0,
    'smart_money': lambda dv,dyb,dys,dfn,dtoi: dv>0 and dyb>0 and dfn<0,
    'vol_up_oi_down': lambda dv,dyb,dys,dfn,dtoi: dv>0 and dtoi<0,
    'vol_up_yb_down_fiz_up': lambda dv,dyb,dys,dfn,dtoi: dv>0 and dyb<0 and dfn>0,
    'fiz_extreme_vol_up': lambda dv,dyb,dys,dfn,dtoi: dv>0 and abs(dfn)>5,
}

# Load data
ticker_data = {}
for ticker in TICKERS:
    rows = ch.query("""
        SELECT toDate(p.time) as d, argMax(p.open,p.time), argMax(p.high,p.time),
               argMax(p.low,p.time), argMax(p.close,p.time), argMax(p.volume,p.time),
               argMax(o.yur_buy,p.time), argMax(o.yur_sell,p.time),
               argMax(o.fiz_buy,p.time), argMax(o.fiz_sell,p.time),
               argMax(o.total_oi,p.time)
        FROM moex.prices_5m p INNER JOIN moex.prices_5m_oi o ON p.symbol=o.symbol AND p.time=o.time
        WHERE p.symbol=%(t)s AND p.time>='2024-01-01' AND p.time<='2026-05-01'
        GROUP BY d ORDER BY d
    """, parameters={'t': ticker}).result_rows
    
    if len(rows) < 60: continue
    a = np.array([list(r) for r in rows], dtype=object)
    dates = [str(r[0]) for r in rows]
    opn=a[:,1].astype(float); high=a[:,2].astype(float); low=a[:,3].astype(float)
    close=a[:,4].astype(float); vol=a[:,5].astype(float)
    yb=a[:,6].astype(float); ys=a[:,7].astype(float)
    fb=a[:,8].astype(float); fs=a[:,9].astype(float); toi=a[:,10].astype(float)
    toi=np.where(toi<=0,1,toi)
    
    tr=np.zeros(len(close))
    tr[1:]=np.maximum(high[1:]-low[1:],np.maximum(abs(high[1:]-close[:-1]),abs(low[1:]-close[:-1])))
    atr=np.full(len(close),np.nan)
    for i in range(14,len(close)): atr[i]=np.mean(tr[i-13:i+1])
    
    v_m=np.mean(vol)+1; yb_m=np.mean(yb)+1; ys_m=np.mean(ys)+1; toi_m=np.mean(toi)+1
    dv=np.diff(vol)/v_m; dyb=np.diff(yb)/yb_m; dys=np.diff(ys)/ys_m; dtoi=np.diff(toi)/toi_m
    fiz_net=(fb-fs)/toi*100; dfn=np.diff(fiz_net)
    sma50=np.full(len(close),np.nan)
    if len(close)>=50:
        cs=np.cumsum(close); sma50[49]=cs[49]/50; sma50[50:]=(cs[50:]-cs[:-50])/50
    
    ticker_data[ticker] = dict(dates=dates, opn=opn, high=high, low=low, close=close, vol=vol,
                                dv=dv, dyb=dyb, dys=dys, dfn=dfn, dtoi=dtoi,
                                atr=atr, sma50=sma50, n=len(dates))

# Run portfolio simulation
def run_portfolio(configs, reinvest=True):
    """Run portfolio with exact trade simulation."""
    # Pre-compute all signals
    all_entries = []  # [(date_idx, ticker, direction, pnl_fn), ...]
    
    for ticker, direction, pname, hold, atr_mult in configs:
        data = ticker_data.get(ticker)
        if not data: continue
        d = data
        cs = CS_MAP.get(ticker, 1)
        pfunc = PATTERNS.get(pname)
        
        for i in range(50, d['n'] - hold - 1):
            if i >= len(d['dv']): break
            ep = float(d['opn'][i+1])
            
            if pfunc and not pfunc(d['dv'][i], d['dyb'][i], d['dys'][i], d['dfn'][i], d['dtoi'][i]):
                continue
            if d['vol'][i] < np.mean(d['vol'][:i]) * 1.2:
                continue
            
            go_val = ep * cs
            if go_val <= 0: continue
            
            # Store entry for later simulation (sizing depends on current equity)
            if direction == 'long':
                sp = ep * (1 - min(max(d['atr'][i]/ep*atr_mult, 0.005), 0.05)) if not np.isnan(d['atr'][i]) else ep*0.95
                r_h = ep
                for j in range(i+1, min(i+hold+1, d['n'])):
                    bh = float(d['high'][j])
                    if bh > r_h:
                        r_h = bh
                        if not np.isnan(d['atr'][j]):
                            sp = max(sp, r_h*(1-min(max(d['atr'][j]/r_h*atr_mult,0.005),0.05)))
                    if float(d['low'][j]) <= sp:
                        xp = sp
                        all_entries.append((i+1, j, ticker, 'L', ep, xp, cs, hold))
                        break
                else:
                    xp = float(d['close'][min(i+hold, d['n']-1)])
                    all_entries.append((i+1, min(i+hold, d['n']-1), ticker, 'L', ep, xp, cs, hold))
            else:
                sp = ep * (1 + min(max(d['atr'][i]/ep*atr_mult, 0.005), 0.05)) if not np.isnan(d['atr'][i]) else ep*1.05
                r_l = ep
                for j in range(i+1, min(i+hold+1, d['n'])):
                    bl = float(d['low'][j])
                    if bl < r_l:
                        r_l = bl
                        if not np.isnan(d['atr'][j]):
                            sp = min(sp, r_l*(1+min(max(d['atr'][j]/r_l*atr_mult,0.005),0.05)))
                    if float(d['high'][j]) >= sp:
                        xp = sp
                        all_entries.append((i+1, j, ticker, 'S', ep, xp, cs, hold))
                        break
                else:
                    xp = float(d['close'][min(i+hold, d['n']-1)])
                    all_entries.append((i+1, min(i+hold, d['n']-1), ticker, 'S', ep, xp, cs, hold))
    
    # Sort by entry date
    all_entries.sort(key=lambda x: x[0])
    
    # Simulate with reinvestment
    eq = float(CAPITAL)
    peak = eq
    mdd = 0.0
    trades_log = []
    n_signals = len(configs)
    
    for entry_idx, exit_idx, ticker, direction, ep, xp, cs, hold in all_entries:
        go_val = ep * cs
        if go_val <= 0: continue
        
        # Sizing from current equity
        sig_eq = eq / n_signals  # equal allocation per signal
        risk_amount = sig_eq * RISK_PCT
        base_nc = risk_amount / (go_val * 0.005)
        base_nc = max(1, int(base_nc))
        nc = min(base_nc, MAX_LOT)
        
        # Leverage cap
        max_by_go = int(eq * MAX_LEV / go_val) if go_val > 0 else 99
        nc = min(nc, max_by_go)
        if nc < 1: continue
        
        if direction == 'L':
            pnl = nc * cs * (xp - ep) - nc * COMM
        else:
            pnl = nc * cs * (ep - xp) - nc * COMM
        
        eq += pnl
        if eq > peak: peak = eq
        dd = (peak - eq) / peak * 100 if peak > 0 else 0
        mdd = max(mdd, dd)
        
        trades_log.append({'ticker': ticker, 'dir': direction, 'entry_idx': entry_idx,
                           'pnl': round(pnl,0), 'nc': nc})
    
    if not trades_log: return None
    ret = (eq - CAPITAL) / CAPITAL * 100
    wins = sum(1 for t in trades_log if t['pnl'] > 0)
    wr = wins / len(trades_log) * 100
    gp = sum(t['pnl'] for t in trades_log if t['pnl'] > 0)
    gl = sum(t['pnl'] for t in trades_log if t['pnl'] < 0)
    pf = abs(gp / (gl + 0.001))
    
    return dict(ret=round(ret,2), mdd=round(mdd,2), wr=round(wr,1), pf=round(pf,2),
                trades=len(trades_log), wins=wins, calmar=round(ret/mdd,2) if mdd>0 else 0,
                net_pnl=round(gp+gl,0))

# Run
print(f"{'Config':>50} {'Ret':>8} {'DD':>6} {'Calmar':>7} {'WR':>5} {'Tr':>4} {'Net':>10}")
print("-"*95)

# Individual
for ticker, direction, pname, hold, atr_mult in CONFIGS:
    r = run_portfolio([(ticker, direction, pname, hold, atr_mult)])
    if r:
        label = f"{ticker} {direction[:1]} {pname[:18]} h={hold} am={atr_mult}"
        print(f"{label:>50} {r['ret']:>+7.1f}% {r['mdd']:>5.1f}% {r['calmar']:>6.1f} {r['wr']:>4.0f}% {r['trades']:>4d} {r['net_pnl']:>10,.0f}₽")

# Portfolio combinations
print(f"\n{'─'*95}")
print(f"{'PORTFOLIO':>50}")
print(f"{'─'*95}")

# All 6 signals together
r_all = run_portfolio(CONFIGS)
if r_all:
    print(f"{'All 6 signals':>50} {r_all['ret']:>+7.1f}% {r_all['mdd']:>5.1f}% {r_all['calmar']:>6.1f} {r_all['wr']:>4.0f}% {r_all['trades']:>4d} {r_all['net_pnl']:>10,.0f}₽")

# 4 best (no Eu)
configs_4 = [c for c in CONFIGS if c[0] != 'Eu']
r_4 = run_portfolio(configs_4)
if r_4:
    print(f"{'4 signals (no Eu)':>50} {r_4['ret']:>+7.1f}% {r_4['mdd']:>5.1f}% {r_4['calmar']:>6.1f} {r_4['wr']:>4.0f}% {r_4['trades']:>4d} {r_4['net_pnl']:>10,.0f}₽")

# 3 signals (NM + VB + SR)
configs_3 = [c for c in CONFIGS if c[0] in ('NM', 'VB', 'SR')]
r_3 = run_portfolio(configs_3)
if r_3:
    print(f"{'3 signals (NM+VB+SR)':>50} {r_3['ret']:>+7.1f}% {r_3['mdd']:>5.1f}% {r_3['calmar']:>6.1f} {r_3['wr']:>4.0f}% {r_3['trades']:>4d} {r_3['net_pnl']:>10,.0f}₽")

# per-ticker pairs (long+short)
for ticker in ['NM', 'VB', 'SR', 'Eu']:
    tc = [c for c in CONFIGS if c[0] == ticker]
    if tc:
        r_t = run_portfolio(tc)
        if r_t:
            dirs = "+".join([c[1][0] for c in tc])
            print(f"{f'{ticker} ({dirs})':>50} {r_t['ret']:>+7.1f}% {r_t['mdd']:>5.1f}% {r_t['calmar']:>6.1f} {r_t['wr']:>4.0f}% {r_t['trades']:>4d} {r_t['net_pnl']:>10,.0f}₽")
