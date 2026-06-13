#!/usr/bin/env python3
"""Audit GL trades for the top megagrid result: hold=13 chandelier atr_mult=2, capital=8696RUB."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/../..')
os.chdir(os.path.dirname(os.path.abspath(__file__)) + '/../..')

import numpy as np
import clickhouse_connect
from datetime import datetime
from config import CH_HOST, CH_PORT, CH_DB

CAPITAL = 200_000
COMM = 4
RISK_PCT = 0.02
MAX_LOT = 5
MAX_LEV = 5.0

GO_MAP = {'GL': 1352}
CS_MAP = {'GL': 1}

CBR_DATES = [f'{y}-{m:02d}-{d:02d}' for y,m,d in [
    (2024,2,16),(2024,3,22),(2024,4,26),(2024,6,7),(2024,7,26),
    (2024,9,13),(2024,10,25),(2024,12,20),
    (2025,2,14),(2025,3,21),(2025,4,25),(2025,6,13),
    (2025,7,25),(2025,9,12),(2025,10,24),(2025,12,19),
    (2026,2,14),(2026,3,21),(2026,4,25)]]

def is_cbr(d):
    dt = datetime.strptime(d[:10],'%Y-%m-%d')
    return any(abs((dt-datetime.strptime(c,'%Y-%m-%d')).days)<=2 for c in CBR_DATES)

ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)

def load_ticker_data(ticker):
    """Same as megagrid.py load_ticker_data"""
    d_rows = ch.query("""
        SELECT toDate(p.time) as d,
               argMax(p.open,p.time), argMax(p.high,p.time), argMax(p.low,p.time),
               argMax(p.close,p.time), argMax(p.volume,p.time),
               argMax(o.yur_buy,p.time), argMax(o.yur_sell,p.time),
               argMax(o.fiz_buy,p.time), argMax(o.fiz_sell,p.time),
               argMax(o.total_oi,p.time)
        FROM moex.prices_5m p INNER JOIN moex.prices_5m_oi o ON p.symbol=o.symbol AND p.time=o.time
        WHERE p.symbol=%(t)s AND p.time>='2024-01-01' AND p.time<='2026-05-01'
        GROUP BY d ORDER BY d
    """, parameters={'t': ticker}).result_rows
    if len(d_rows) < 60: return None
    
    a = np.array([list(r) for r in d_rows], dtype=object)
    dates = [str(r[0]) for r in d_rows]
    opn=a[:,1].astype(float); high=a[:,2].astype(float); low=a[:,3].astype(float)
    close=a[:,4].astype(float); vol=a[:,5].astype(float)
    yb=a[:,6].astype(float); ys=a[:,7].astype(float)
    fb=a[:,8].astype(float); fs=a[:,9].astype(float); toi=a[:,10].astype(float)
    
    toi=np.where(toi<=0,1,toi)
    v_m=np.mean(vol)+1; yb_m=np.mean(yb)+1; ys_m=np.mean(ys)+1; toi_m=np.mean(toi)+1
    dv=np.diff(vol)/v_m; dyb=np.diff(yb)/yb_m; dys=np.diff(ys)/ys_m; dtoi=np.diff(toi)/toi_m
    fiz_net=(fb-fs)/toi*100; dfn=np.diff(fiz_net)
    
    # ATR(14)
    tr=np.zeros(len(close))
    tr[1:]=np.maximum(high[1:]-low[1:],np.maximum(abs(high[1:]-close[:-1]),abs(low[1:]-close[:-1])))
    atr=np.full(len(close),np.nan)
    if len(close)>=15:
        atr_s=np.convolve(tr,np.ones(14)/14,mode='valid')[:len(close)]
        for i in range(14,len(close)): atr[i]=atr_s[i-14]
    
    sma50=np.full(len(close),np.nan)
    if len(close)>=50:
        cs=np.cumsum(close); sma50[49]=cs[49]/50; sma50[50:]=(cs[50:]-cs[:-50])/50
    
    cbr_filter=np.array([not is_cbr(d) for d in dates])
    dv_mag=np.abs(dv)
    
    return dict(dates=dates,opn=opn,high=high,low=low,close=close,vol=vol,
                dv=dv,dyb=dyb,dys=dys,dfn=dfn,dtoi=dtoi,sma50=sma50,atr=atr,
                cbr_filter=cbr_filter,dv_mag=dv_mag,n=len(d_rows))

def vol_up_oi_up_yb_up(dv,dyb,dys,dfn,dtoi):
    """Pattern used for GL top result"""
    return dv>0 and dtoi>0 and dyb>0

def backtest_audit(data, ticker, cs, go_val, pfunc, hold, sl_pct, dv_thr,
                   use_chandelier=False, atr_mult=3.0, use_partial_exit=False, 
                   partial_atr=0.5, cap_per_ticker=None):
    """Returns: (summary dict, list of detailed trades)"""
    dates=data['dates']; opn=data['opn']; high=data['high']; low=data['low']
    close=data['close']; dv=data['dv']; dyb=data['dyb']; dys=data['dys']
    dfn=data['dfn']; dtoi=data['dtoi']; sma50=data['sma50']; atr=data['atr']
    cbr_f=data['cbr_filter']; dv_mag=data['dv_mag']
    n=len(close)
    
    cap=cap_per_ticker or CAPITAL
    eq=float(cap); peak=eq; mdd=0.0
    trades=[]  # detailed trades
    
    for i in range(max(50,15), n-max(hold,2)):
        if i>=len(dv): break
        if not pfunc(dv[i],dyb[i],dys[i],dfn[i],dtoi[i]): continue
        if dv_mag[i]<dv_thr: continue
        if i<len(cbr_f) and not cbr_f[i]: continue
        if sma50 is not None and i<len(sma50) and not np.isnan(sma50[i]) and close[i]<=sma50[i]:
            continue
        
        ei=i+1
        if ei>=n-1: continue
        ep=float(opn[ei])
        xi=min(ei+hold, n-1)
        
        go=ep*cs
        if go<=0: continue
        
        # Sizing
        risk_amount=eq*RISK_PCT
        if sl_pct>0:
            base_nc=risk_amount/(go*sl_pct)
        else:
            base_nc=risk_amount/go*5
        base_nc=max(1,int(base_nc))
        nc=min(base_nc, MAX_LOT)
        max_by_go=int(eq*MAX_LEV/go) if go>0 else 99
        nc=min(nc, max_by_go)
        if nc<1: continue
        
        # Exit logic
        remaining_nc=nc
        npnl_total=0
        exit_date=dates[xi]
        xp=float(close[xi])
        stop_hit=False
        
        # Track trailing stop details for audit
        trail_details = []
        
        if use_chandelier:
            running_high=ep
            # Get ATR at entry for initial trail calc
            entry_atr = atr[i] if i < len(atr) and not np.isnan(atr[i]) else 0
            init_trail_pct = min(max(entry_atr/ep*atr_mult, 0.01), 0.05) if entry_atr > 0 else 0.01
            sp=ep*(1-init_trail_pct)
            
            # Record entry stop
            trail_details.append(dict(bar='entry', high=running_high, trail_pct=init_trail_pct, stop_price=sp))
            
            for j in range(ei, xi+1):
                bh=float(high[j])
                if bh>running_high:
                    running_high=bh
                    if j<len(atr) and not np.isnan(atr[j]):
                        new_trail=max(atr[j]/running_high*atr_mult, 0.01)
                    else:
                        new_trail=0.01
                    old_sp = sp
                    sp=max(sp, running_high*(1-min(new_trail,0.05)))
                    trail_details.append(dict(bar=j, high_at_bar=bh, running_high=running_high, 
                                              new_trail_pct=min(new_trail,0.05), 
                                              old_stop=old_sp, new_stop=sp))
                
                # Partial exit (not used for this test but code present)
                if use_partial_exit and remaining_nc>1 and not stop_hit:
                    if j<len(atr) and not np.isnan(atr[j]):
                        partial_tgt=ep+atr[ei]*partial_atr
                    else:
                        partial_tgt=ep*(1+partial_atr*0.02)
                    if bh>=partial_tgt:
                        half=remaining_nc//2
                        if half>0:
                            partial_pnl=half*cs*(partial_tgt-ep)
                            npnl_total+=partial_pnl-half*COMM
                            remaining_nc-=half
                
                if float(low[j])<=sp:
                    xp=sp
                    stop_hit=True
                    exit_date=dates[j]
                    npnl_total+=remaining_nc*cs*(xp-ep)-remaining_nc*COMM
                    remaining_nc=0
                    break
            
            if not stop_hit and remaining_nc>0:
                npnl_total+=remaining_nc*cs*(xp-ep)-remaining_nc*COMM
        else:
            sp=ep*(1-sl_pct) if sl_pct>0 else 0
            if sl_pct>0:
                for j in range(ei, xi+1):
                    if float(low[j])<=sp:
                        xp=sp; stop_hit=True; exit_date=dates[j]; break
            npnl_total=nc*cs*(xp-ep)-nc*COMM
        
        eq+=npnl_total
        if eq>peak: peak=eq
        dd=(peak-eq)/peak*100 if peak>0 else 0
        mdd=max(mdd,dd)
        
        # Entry signal date
        signal_date = dates[i]
        
        trades.append(dict(
            signal_date=signal_date,
            entry_date=dates[ei],
            exit_date=exit_date,
            ep=round(ep,2),
            xp=round(xp,2),
            nc=nc,
            remaining_nc=remaining_nc,
            contracts_used=nc,
            npnl=round(npnl_total,0),
            stop_hit=stop_hit,
            hold_days=xi-ei+1,
            bars_in_trade=j-ei+1 if stop_hit else xi-ei+1,
            entry_go=round(go,0),
            equity_before=round(eq-npnl_total,0),
            ep_pct_of_cap=round(ep/cap_per_ticker*100,2) if cap_per_ticker else 0,
            trail_details=trail_details,
        ))
    
    if not trades: return None, []
    
    ret=(eq-cap)/cap*100
    wins=sum(1 for t in trades if t['npnl']>0)
    wr=wins/len(trades)*100
    gp_s=sum(t['npnl'] for t in trades if t['npnl']>0)
    gl_s=sum(t['npnl'] for t in trades if t['npnl']<0)
    pf=abs(gp_s/(gl_s+0.001))
    
    summary = dict(ret=round(ret,2), mdd=round(mdd,2), wr=round(wr,1), pf=round(pf,2),
                   trades=len(trades), wins=wins, calmar=round(ret/mdd,2) if mdd>0 else 0,
                   net_pnl=round(gp_s+gl_s,0))
    return summary, trades


# ── Main Audit ────────────────────────────────────────

ticker = 'GL'
cs = CS_MAP[ticker]
go_val = GO_MAP[ticker]

print("=" * 130)
print(f"GL (Gold Futures MOEX) — AUDIT")
print(f"  GO (Margin) = {go_val}RUB, Contract Size = {cs}")
print(f"  GL price ~80,000 RUB → contract cost = ~80,000 RUB (GO = 1,352 RUB)")
print("=" * 130)

data = load_ticker_data(ticker)
if data is None:
    print("ERROR: Could not load GL data")
    sys.exit(1)

print(f"  Loaded {data['n']} daily bars")
print(f"  Date range: {data['dates'][0]} to {data['dates'][-1]}")

# Parameters from the top result
N = 23  # number of active tickers in the grid
cap_pt = CAPITAL / N  # = 8696RUB
hold = 13
sl_pct = 0            # chandelier mode
dv_thr = 0
use_chandelier = True
atr_mult = 2

print(f"\n{'=' * 130}")
print(f"CONFIGURATION")
print(f"{'=' * 130}")
print(f"  Pattern:         vol_up_oi_up_yb_up")
print(f"  Hold (max bars): {hold}")
print(f"  Capital/ticker:  {cap_pt:.0f} RUB")
print(f"  Chandelier:      YES, ATR_mult={atr_mult}")
print(f"  Partial exit:    NO")
print(f"  Risk%:           {RISK_PCT*100}%")
print(f"  Max leverage:    {MAX_LEV}x")
print(f"  Max contracts:   {MAX_LOT}")
print(f"  Commission:      {COMM} RUB/trade")
print(f"  GO (margin):     {go_val} RUB per contract")
print(f"  GL price:        ~80,000 RUB per contract")
print(f"  Contract cost:   ~80,000 RUB (price * 1)")
print()

# Also compute some sanity checks
print(f"  SANITY CHECK — Contract sizing:")
print(f"    Capital: {cap_pt:.0f} RUB")
print(f"    Max 3x leverage: {cap_pt * MAX_LEV:.0f} RUB buying power")
print(f"    Contract cost (at 80K): ~80,000 RUB")
print(f"    Contracts affordable at 3x: {int(cap_pt * MAX_LEV / 80000)}")
print(f"    GO-based max: int({cap_pt} * {MAX_LEV} / {go_val}) = {int(cap_pt * MAX_LEV / go_val)}")
print(f"    Risk-based base_nc (2% of {cap_pt}={cap_pt*0.02:.0f}RUB):")
print(f"      With sl_pct=0 (chandelier): base_nc = max(1, int(rsk/go*5))")
print(f"      = max(1, int({cap_pt*0.02:.0f}/{go_val}*5)) = max(1, int({cap_pt*0.02/go_val*5:.2f}))")
base_nc_debug = max(1, int(cap_pt * 0.02 / go_val * 5))
max_by_go_debug = int(cap_pt * MAX_LEV / go_val)
print(f"      = {base_nc_debug}")
print(f"    max_by_go (3x): {max_by_go_debug}")
print(f"    Final nc: min({base_nc_debug}, {MAX_LOT}, {max_by_go_debug}) = {min(base_nc_debug, MAX_LOT, max_by_go_debug)}")
print()

# Run backtest
print(f"{'=' * 130}")
print(f"RUNNING BACKTEST — {68} trades expected")
print(f"{'=' * 130}")
print()

summary, trades = backtest_audit(
    data, ticker, cs, go_val, 
    vol_up_oi_up_yb_up, hold, sl_pct, dv_thr,
    use_chandelier=use_chandelier, atr_mult=atr_mult,
    use_partial_exit=False, cap_per_ticker=cap_pt
)

if summary is None:
    print("No trades generated!")
    sys.exit(1)

print(f"Summary: ret={summary['ret']:+.1f}%, mdd={summary['mdd']:.1f}%, "
      f"wr={summary['wr']:.0f}%, pf={summary['pf']:.2f}, "
      f"trades={summary['trades']}, net_pnl={summary['net_pnl']:.0f}")
print()

# Print ALL trades in a table
print(f"{'=' * 130}")
print(f"ALL {len(trades)} TRADES")
print(f"{'=' * 130}")
print(f"{'#':>3} {'SigDate':>10} {'EntryDate':>10} {'ExitDate':>10} {'EntryPx':>8} {'ExitPx':>8} "
      f"{'NC':>3} {'Bars':>4} {'Stop?':>5} {'PnL':>8} {'CumPnl':>9} {'EqBef':>8} {'Ep%Cap':>7} {'GO':>6}")
print("-" * 130)

cum_pnl = 0
nc_sequence = []
for idx, t in enumerate(trades):
    cum_pnl += t['npnl']
    nc_sequence.append(t['nc'])
    print(f"{idx+1:>3} {t['signal_date']:>10} {t['entry_date']:>10} {t['exit_date']:>10} "
          f"{t['ep']:>8.2f} {t['xp']:>8.2f} {t['nc']:>3} {t['bars_in_trade']:>4} "
          f"{'YES' if t['stop_hit'] else 'NO':>5} {t['npnl']:>+8.0f} {cum_pnl:>+9.0f} "
          f"{t['equity_before']:>8.0f} {t['ep_pct_of_cap']:>6.2f}% {t['entry_go']:>6.0f}")
    
    if idx < 3:
        # Print trail details for first 3 trades
        if t['trail_details']:
            for td in t['trail_details']:
                if td['bar'] == 'entry':
                    print(f"       ├─ Entry trail: stop={td['stop_price']:.2f} "
                          f"(trail={td['trail_pct']*100:.1f}%)")
                else:
                    print(f"       ├─ Bar {td['bar']}: high={td['high_at_bar']:.2f}, "
                          f"run_high={td['running_high']:.2f}, new_trail={td['new_trail_pct']*100:.1f}%, "
                          f"stop {td['old_stop']:.2f}→{td['new_stop']:.2f}")
        print()

# Print a few focused analyses
print()
print("=" * 130)
print(f"CONTRACT COUNT ANALYSIS")
print("=" * 130)
nc_array = np.array(nc_sequence)
print(f"  Min contracts: {nc_array.min()}")
print(f"  Max contracts: {nc_array.max()}")
print(f"  Mean contracts: {nc_array.mean():.1f}")
print(f"  Unique counts: {sorted(set(nc_sequence))}")
print(f"  Trades with nc=5: {(nc_array==5).sum()}")
print(f"  Trades with nc=4: {(nc_array==4).sum()}")
print(f"  Trades with nc=3: {(nc_array==3).sum()}")
print(f"  Trades with nc=2: {(nc_array==2).sum()}")
print(f"  Trades with nc=1: {(nc_array==1).sum()}")

print()
print("=" * 130)
print(f"STOP ANALYSIS")
print("=" * 130)
stop_hits = sum(1 for t in trades if t['stop_hit'])
print(f"  Stop hit: {stop_hits}/{len(trades)} ({stop_hits/len(trades)*100:.1f}%)")
print(f"  Expired (held full duration): {len(trades)-stop_hits}/{len(trades)}")

print()
print("=" * 130)
print(f"TRADE-BY-TRADE: First 3 trades with full trail logic trace")
print("=" * 130)

# Re-run first 3 entries manually with price data context
for idx in range(min(3, len(trades))):
    t = trades[idx]
    print(f"\n--- Trade #{idx+1} ---")
    print(f"  Signal: {t['signal_date']} → Entry: {t['entry_date']} at {t['ep']}")
    print(f"  Exit: {t['exit_date']} at {t['xp']}, stop_hit={t['stop_hit']}")
    print(f"  Contracts: {t['nc']}, PnL: {t['npnl']}")
    print(f"  Entry GO (margin): {t['entry_go']:.0f} RUB")
    print(f"  Equity before trade: {t['equity_before']:.0f} RUB")
    print(f"  Leverage if full position: {t['nc'] * t['entry_go'] / t['equity_before']:.2f}x")
    
    # Verify the stop calculation manually
    # Find the signal bar index
    sig_date = t['signal_date']
    try:
        sig_idx = data['dates'].index(sig_date)
        ei = sig_idx + 1
        xi = min(ei + hold, data['n'] - 1)
        
        ep = float(data['opn'][ei])
        entry_atr = data['atr'][sig_idx] if sig_idx < len(data['atr']) and not np.isnan(data['atr'][sig_idx]) else 0
        init_trail = min(max(entry_atr / ep * atr_mult, 0.01), 0.05) if entry_atr > 0 else 0.01
        init_stop = ep * (1 - init_trail)
        
        print(f"  Entry ATR({sig_idx}): {entry_atr:.2f}")
        print(f"  Initial trail: {init_trail*100:.1f}% = {init_trail*ep:.1f} pts")
        print(f"  Initial stop: {init_stop:.2f}")
        
        if t['stop_hit']:
            # Find which bar hit the stop
            running_high = ep
            sp = init_stop
            for j in range(ei, xi+1):
                bh = float(data['high'][j])
                if bh > running_high:
                    running_high = bh
                    new_trail = max(data['atr'][j]/running_high*atr_mult, 0.01) if j < len(data['atr']) and not np.isnan(data['atr'][j]) else 0.01
                    sp = max(sp, running_high*(1-min(new_trail,0.05)))
                if float(data['low'][j]) <= sp:
                    print(f"  Stop hit at bar {j} ({data['dates'][j]}): low={data['low'][j]:.2f} ≤ stop={sp:.2f}")
                    print(f"    Running high was: {running_high:.2f}")
                    break
        else:
            exit_price = float(data['close'][xi])
            print(f"  Held full {hold} bars, exit at close={exit_price:.2f}")
            
    except ValueError:
        print(f"  Could not find signal date in index")
    print()

# Look-ahead bug analysis
print("=" * 130)
print(f"LOOK-AHEAD BUG ANALYSIS")
print("=" * 130)
print(f"  The function uses:")
print(f"    - Signal on bar index i (uses data up to and including bar i)")
print(f"    - Entry on bar ei=i+1 (next day's open)")
print(f"    - Exit between bars ei..xi where xi=min(ei+hold, n-1)")
print(f"  Check: at signal bar i, it reads dv[i], dyb[i], etc.")
print(f"  dv uses vol[i+1]-vol[i], so dv[i] uses vol[i] and vol[i+1] which are")
print(f"  part of the same row as the signal? Let's check:")
print(f"  - dv[0] = (vol[1]-vol[0])/v_m  → uses vol[0] (date 0) and vol[1] (date 1)")
print(f"  - So at signal bar i, dv[i] uses vol[i+1] which is FUTURE data relative to close[i]!")
print(f"  - The signal at bar i looks at the DIFFERENCE between bar i and bar i+1")
print(f"  - But it does NOT use bar i+2 or beyond")
print(f"  - Entry is at bar i+1's open, so the 'future' vol is from bar i+1 which")
print(f"    is the same day as the entry! This means vol[i+1] is actually from the")
print(f"    same bar as the entry date. Is this a look-ahead?")
print()
print(f"  Let's trace an example:")
print(f"  - Bar i = signal day = some_date")
print(f"  - dv[i] = vol[some_date+1 day] - vol[some_date]")
print(f"  - But vol[some_date+1 day] is KNOWN at the close of bar i+1")
print(f"  - Entry happens at the open of bar i+1")
print(f"  - So at entry time, vol of the same day (bar i+1) is NOT yet known!")
print(f"  - This IS a look-ahead bug: dv[i] uses vol[i+1] which is the volume of")
print(f"    the entry day, not available at signal time.")
print()
print(f"  Verdict: The signal uses dv[i] which requires vol[i] AND vol[i+1], but")
print(f"  vol[i+1] is the volume of the ENTRY day, which is unknown until that")
print(f"  day closes. This is a minor look-ahead.")

print()
print("=" * 130)
print(f"REALITY CHECK")
print("=" * 130)
print(f"  Starting capital: {cap_pt:.0f} RUB")
print(f"  Ending capital: {cap_pt + summary['net_pnl']:.0f} RUB")
print(f"  Return: {summary['ret']:+.1f}%")
print(f"  Number of trades: {summary['trades']}")
print(f"  Average PnL per trade: {summary['net_pnl']/summary['trades']:.0f} RUB")
print(f"  Best trade PnL: {max(t['npnl'] for t in trades):+.0f} RUB")
print(f"  Worst trade PnL: {min(t['npnl'] for t in trades):+.0f} RUB")
print(f"  Max drawdown: {summary['mdd']:.1f}%")

# The main question: can you trade GL with 8696 RUB?
GL_PRICE = 80000  # approximate
contract_cost = GL_PRICE * CS_MAP['GL']
print()
print(f"  CRITICAL: GL contract price = ~{GL_PRICE:,} RUB (cs=1)")
print(f"  Capital per ticker = {cap_pt:.0f} RUB")
print(f"  Leverage needed for 1 contract = {contract_cost/cap_pt:.1f}x")
print(f"  Max leverage allowed = {MAX_LEV}x")
print(f"  Can buy 1 contract? {contract_cost <= cap_pt * MAX_LEV}")
print(f"  GO-based max contracts (3x leverage): {max_by_go_debug}")
print()
print(f"  ACTUAL sizing logic when sl_pct=0:")
print(f"    risk_amount = {cap_pt} * {RISK_PCT} = {cap_pt*RISK_PCT:.1f}")
print(f"    base_nc = max(1, int({cap_pt*RISK_PCT:.1f}/{go_val}*5)) = max(1, int({cap_pt*RISK_PCT/go_val*5:.2f}))")
print(f"           = {base_nc_debug}")
print(f"    max_by_go = int({cap_pt}*{MAX_LEV}/{go_val}) = {max_by_go_debug}")
print(f"    So nc = min({base_nc_debug}, MAX_LOT={MAX_LOT}, {max_by_go_debug}) = {min(base_nc_debug, MAX_LOT, max_by_go_debug)}")
print()
print(f"  PROBLEM: With GO={go_val}RUB and capital={cap_pt}RUB:")
print(f"    max_by_go = int({cap_pt}*{MAX_LEV}/{go_val}) = int({cap_pt*MAX_LEV/go_val:.1f}) = {max_by_go_debug}")
print(f"  This many contracts at GO={go_val} (margin) — BUT this is the MARGIN/GO,")
print(f"  not the actual contract cost! The real contract is worth ~{GL_PRICE:,} RUB")
print(f"  but only GO={go_val} RUB is blocked as margin.")
print(f"  So the leverage math based on GO is WRONG.")
print(f"  GO = 1,352 RUB is the MARGIN requirement, NOT the contract value.")
print(f"  The actual contract value = price * cs ≈ 80,000 * 1 = 80,000 RUB")
print(f"  So nc=1 at 80,000 means 80,000/8,696 = 9.2x leverage, EXCEEDING the 3x max!")
print(f"  The code uses GO (margin) instead of contract value for leverage check.")
print(f"  BUG: max_by_go uses 'go=ep*cs' which is the margin value (1352RUB),")
print(f"  but it should use the contract value (price*cs ≈ 80,000RUB)!")
print(f"  This means the leverage constraint is effectively not enforced.")
