#!/usr/bin/env python3
"""Final audit: re-run with correct params (capital=11765, sl_pct=0.005)"""
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
    """Same as megagrid.py"""
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
    return dv>0 and dtoi>0 and dyb>0

def backtest_one(data, ticker, cs, go_val, pfunc, hold, sl_pct, dv_thr,
                 use_chandelier=False, atr_mult=3.0, use_partial_exit=False, partial_atr=0.5,
                 cap_per_ticker=None):
    """EXACT copy of megagrid.py backtest_one"""
    dates=data['dates']; opn=data['opn']; high=data['high']; low=data['low']
    close=data['close']; dv=data['dv']; dyb=data['dyb']; dys=data['dys']
    dfn=data['dfn']; dtoi=data['dtoi']; sma50=data['sma50']; atr=data['atr']
    cbr_f=data['cbr_filter']; dv_mag=data['dv_mag']
    n=len(close)
    
    cap=cap_per_ticker or CAPITAL
    eq=float(cap); peak=eq; mdd=0.0; trades=[]
    
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
        exit_date=dates[xi]; xp=float(close[xi]); stop_hit=False
        
        if use_chandelier:
            running_high=ep
            sp=ep*(1-min(max(atr[i]/ep*atr_mult,0.01),0.05))
            for j in range(ei, xi+1):
                bh=float(high[j])
                if bh>running_high:
                    running_high=bh
                    if j<len(atr) and not np.isnan(atr[j]):
                        new_trail=max(atr[j]/running_high*atr_mult,0.01)
                    else:
                        new_trail=0.01
                    sp=max(sp, running_high*(1-min(new_trail,0.05)))
                
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
                    xp=sp; stop_hit=True; exit_date=dates[j]
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
        
        # SAVE EQUITY BEFORE TRADE for audit
        trades.append(dict(entry=dates[ei], exit=exit_date, ep=round(ep,2), xp=round(xp,2),
                           nc=nc, npnl=round(npnl_total,0), stop=stop_hit))
    
    if not trades: return None
    ret=(eq-cap)/cap*100
    wins=sum(1 for t in trades if t['npnl']>0)
    wr=wins/len(trades)*100
    gp_s=sum(t['npnl'] for t in trades if t['npnl']>0)
    gl_s=sum(t['npnl'] for t in trades if t['npnl']<0)
    pf=abs(gp_s/(gl_s+0.001))
    return dict(ret=round(ret,2), mdd=round(mdd,2), wr=round(wr,1), pf=round(pf,2),
                trades=len(trades), wins=wins, calmar=round(ret/mdd,2) if mdd>0 else 0,
                net_pnl=round(gp_s+gl_s,0))


# ── MAIN ────────────────────────────────────────────
ticker = 'GL'
cs = CS_MAP[ticker]
go_val = GO_MAP[ticker]  # This is never used in the function but passed anyway

# In the grid loop:
# for sl_pct in [0.005, 0.01, 0.02]:
#     for dv_thr in [0, 1.0, 2.0]:
#         # Chandelier variants for am in [2,3,5]:
#         r2=backtest_one(..., sl_pct, dv_thr, use_chandelier=True, atr_mult=am, ...)
# The top result is hold=13, chandelier=True, atr_mult=2
# From loop: first hit is sl_pct=0.005, dv_thr=0, hold=13
# Wait - hold=13, atr_mult=2, dv_thr=0... this is the first sl_pct iteration
# But the grid iterates sl_pct=[0.005,0.01,0.02] and dv_thr=[0,1.0,2.0]
# hold=13 is in [1,2,3,5,8,13,21] — 7th iteration
# sl_pct=0.005 — 1st sl_pct
# dv_thr=0 — 1st dv_thr
# So it's hold=13, sl_pct=0.005, dv_thr=0
# Wait but holding period is 13, and hold=13 is used as the MAX holding period
# The entry happens at bar ei=i+1, and exit at xi=min(ei+hold, n-1)

print("=" * 130)
print("FINAL AUDIT WITH CORRECT PARAMETERS")
print("=" * 130)

data = load_ticker_data(ticker)
N_actual = 17  # from the audit
cap_pt = CAPITAL / N_actual

print(f"Actual N (tickers with data): {N_actual}")
print(f"Cap per ticker: {cap_pt:.0f} RUB")
print(f"GL data: {data['n']} bars")
print()

# Run: match the exact loop iteration that produced the top result
# The top result has: hold=13, chandelier, atr_mult=2, sl=0 (stored), dv_thr=0
# This was run with: sl_pct=0.005 (first in loop), dv_thr=0 (first in loop)

print(f"Reproducing: hold=13, sl_pct=0.005, dv_thr=0, chandelier=True, atr_mult=2")
print()

result = backtest_one(data, ticker, cs, go_val, 
                      vol_up_oi_up_yb_up, 13, 0.005, 0,
                      use_chandelier=True, atr_mult=2,
                      cap_per_ticker=cap_pt)

if result:
    print(f"RESULT: ret={result['ret']:+.1f}%, mdd={result['mdd']:.1f}%, "
          f"wr={result['wr']:.0f}%, pf={result['pf']:.2f}, "
          f"trades={result['trades']}, net_pnl={result['net_pnl']:.0f}")
    print(f"EXPECTED: ret=+755.5%, mdd=11.8%, wr=60%, pf=5.99, "
          f"trades=68, net_pnl=65691")
    print()

# Now check with sl_pct=0 (what I used earlier)
print("-" * 80)
print("With sl_pct=0 (wrong — my first audit):")
result0 = backtest_one(data, ticker, cs, go_val, 
                       vol_up_oi_up_yb_up, 13, 0, 0,
                       use_chandelier=True, atr_mult=2,
                       cap_per_ticker=cap_pt)
if result0:
    print(f"  ret={result0['ret']:+.1f}%, net_pnl={result0['net_pnl']:.0f}, trades={result0['trades']}")

# Check with sl_pct=0.005 (correct)
print(f"\nWith sl_pct=0.005 (correct — matches grid):")
result5 = backtest_one(data, ticker, cs, go_val, 
                       vol_up_oi_up_yb_up, 13, 0.005, 0,
                       use_chandelier=True, atr_mult=2,
                       cap_per_ticker=cap_pt)
if result5:
    print(f"  ret={result5['ret']:+.1f}%, net_pnl={result5['net_pnl']:.0f}, trades={result5['trades']}")

# Check with sl_pct=0.01
print(f"\nWith sl_pct=0.01:")
result10 = backtest_one(data, ticker, cs, go_val, 
                        vol_up_oi_up_yb_up, 13, 0.01, 0,
                        use_chandelier=True, atr_mult=2,
                        cap_per_ticker=cap_pt)
if result10:
    print(f"  ret={result10['ret']:+.1f}%, net_pnl={result10['net_pnl']:.0f}, trades={result10['trades']}")

print()
print("=" * 80)
print("KEY FINDINGS ABOUT SIZING")
print("=" * 80)
print()
print("1. sl_pct controls position sizing EVEN IN CHANDELIER MODE:")
print("   base_nc = max(1, int(risk_amount / (go * sl_pct)))")
print(f"   With cap={cap_pt:.0f}, go≈6973, sl_pct=0.005:")
print(f"     base_nc = max(1, int({cap_pt*0.02:.0f} / (6973 * 0.005)))")
print(f"     = max(1, int({cap_pt*0.02:.0f} / {6973*0.005:.1f}))")
print(f"     ≈ 5 contracts")
print()
print("2. max_by_go = int(eq * MAX_LEV / go) where go = ep * cs = contract VALUE")
print(f"   = int({cap_pt} * {MAX_LEV} / ~7000)")
print(f"   ≈ {int(cap_pt*5/7000)} contracts")
print(f"   So final nc = min(base_nc=5, MAX_LOT=5, max_by_go) = min(5,5,{int(cap_pt*5/7000)})")
print()
print("3. The contract value ~7000 RUB (GL mini-gold on MOEX ≈ gram gold)")
print("   Leverage = nc * 7000 / 11765")
print("   At nc=5: leverage = 35000/11765 = 3.0x")
print("   At nc=1: leverage = 7000/11765 = 0.6x")
print()
print("4. KEY BUG: Stored result shows sl=0 but the actual run used sl_pct=0.005")
print("   This HIDES the position sizing parameter from analysis!")
print("   Anyone re-running with the stored params (sl=0) will get different results.")
