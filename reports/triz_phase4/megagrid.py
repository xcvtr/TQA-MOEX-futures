#!/usr/bin/env python3
"""TRIZ Phase 4 — Full per-ticker grid search with chandelier + stacked + partial exit.
OpenCode-совместимая версия (компактная, без дублирования загрузки).
"""
import sys, os, json, time
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/../..')
os.chdir(os.path.dirname(os.path.abspath(__file__)) + '/../..')

import numpy as np
import clickhouse_connect
from datetime import datetime
from config import CH_HOST, CH_PORT, CH_DB

OUTPUT_DIR = 'reports/triz_phase4'
CAPITAL = 200_000
COMM = 4
RISK_PCT = 0.02
MAX_LOT = 5
MAX_LEV = 5.0

TICKERS = ['CC', 'NM', 'PD', 'SV', 'VB', 'GD', 'SR', 'LK', 'PT', 'Si', 'Eu', 'CNYRUBF', 'CR', 'NG', 'MX', 'AL', 'RN']

GO_MAP = {'RI':27034,'GL':1352,'USDRUBF':11186,'AF':673,'BR':17228,'IMOEXF':2596,'CC':506,
          'NM':256,'PD':24487,'SV':12960,'VB':1556,'GD':32003,'SR':6620,'LK':11606,'PT':31749,
          'Si':12330,'Eu':14478,'CNYRUBF':875,'CR':17200,'NG':8027,'MX':4133,'AL':728,'RN':3152}
CS_MAP = {'RI':1,'GL':1,'USDRUBF':1000,'AF':1,'BR':10,'IMOEXF':10,'CC':10,'NM':10,'PD':1,
          'SV':10,'VB':100,'GD':1,'SR':100,'LK':10,'PT':1,'Si':1000,'Eu':1000,'CNYRUBF':1000,
          'CR':10,'NG':100,'MX':1,'AL':100,'RN':100}

PATTERNS = {
    'vol_up_oi_up_yb_up': lambda dv,dyb,dys,dfn,dtoi: dv>0 and dtoi>0 and dyb>0,
    'smart_money': lambda dv,dyb,dys,dfn,dtoi: dv>0 and dyb>0 and dfn<0,
    'vol_up_oi_down': lambda dv,dyb,dys,dfn,dtoi: dv>0 and dtoi<0,
    'vol_up_yb_down_fiz_up': lambda dv,dyb,dys,dfn,dtoi: dv>0 and dyb<0 and dfn>0,
    'fiz_extreme_vol_up': lambda dv,dyb,dys,dfn,dtoi: dv>0 and abs(dfn)>5,
}

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
    """Load daily + 5m stacked fiz_z in one go."""
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
    
    # 5m fiz_z
    m5=ch.query("""
        SELECT p.time, o.fiz_buy, o.fiz_sell, o.total_oi, p.volume
        FROM moex.prices_5m_oi o INNER JOIN moex.prices_5m p ON p.symbol=o.symbol AND p.time=o.time
        WHERE p.symbol=%(t)s AND p.time>='2024-01-01' AND p.time<='2026-05-01' ORDER BY p.time
    """, parameters={'t': ticker}).result_rows
    if len(m5)>=200:
        m5_toi=np.array([float(r[3]) for r in m5]); m5_fb=np.array([float(r[1]) for r in m5])
        m5_fs=np.array([float(r[2]) for r in m5]); m5_vol=np.array([float(r[4]) for r in m5])
        m5_toi=np.where(m5_toi<=0,1,m5_toi); m5_fn=(m5_fb-m5_fs)/m5_toi*100
        m5_fiz_z=np.zeros(len(m5_fn))
        for i in range(20,len(m5_fn)):
            s=m5_fn[i-20:i]; mu=np.mean(s); sd=np.std(s)+0.001
            m5_fiz_z[i]=(m5_fn[i]-mu)/sd
        m5_vol_z=np.zeros(len(m5_vol))
        for i in range(40,len(m5_vol)):
            s=m5_vol[i-40:i]; mu=np.mean(s); sd=np.std(s)+0.001
            m5_vol_z[i]=(m5_vol[i]-mu)/sd
        daily_5m=defaultdict(list)
        for i in range(len(m5)):
            daily_5m[m5[i][0].strftime('%Y-%m-%d')].append((m5_fiz_z[i],m5_vol_z[i]))
        daily_stacked={}
        for ds,bars in daily_5m.items():
            last3=bars[-3:] if len(bars)>=3 else bars
            daily_stacked[ds]={'fiz_z':float(np.mean([b[0] for b in last3])),
                               'vol_z':float(np.mean([b[1] for b in last3]))}
    else:
        daily_stacked={}
    
    cbr_filter=np.array([not is_cbr(d) for d in dates])
    dv_mag=np.abs(dv)
    
    return dict(dates=dates,opn=opn,high=high,low=low,close=close,vol=vol,
                dv=dv,dyb=dyb,dys=dys,dfn=dfn,dtoi=dtoi,sma50=sma50,atr=atr,
                cbr_filter=cbr_filter,dv_mag=dv_mag,daily_stacked=daily_stacked,n=len(d_rows))


def backtest_one(data, ticker, cs, go_val, pfunc, hold, sl_pct, dv_thr,
                 use_chandelier=False, atr_mult=3.0, use_partial_exit=False, partial_atr=0.5,
                 use_stacked=False, fiz_thr=1.0, vol_thr=1.5, cap_per_ticker=None):
    """Single backtest run."""
    dates=data['dates']; opn=data['opn']; high=data['high']; low=data['low']
    close=data['close']; dv=data['dv']; dyb=data['dyb']; dys=data['dys']
    dfn=data['dfn']; dtoi=data['dtoi']; sma50=data['sma50']; atr=data['atr']
    cbr_f=data['cbr_filter']; dv_mag=data['dv_mag']; st=data['daily_stacked']
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
        
        # Stacked filter
        if use_stacked:
            ds=st.get(dates[i],None)
            if ds is None: continue
            if ds['fiz_z']<fiz_thr and ds['vol_z']<vol_thr: continue
        
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
        nc=min(base_nc,MAX_LOT)
        max_by_go=int(eq*MAX_LEV/go) if go>0 else 99
        nc=min(nc,max_by_go)
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
                
                # Partial exit
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


# ── Main ──────────────────────────────────────────────
os.makedirs(OUTPUT_DIR, exist_ok=True)
t0=time.time()
all_results=[]

# Load ALL data first
ticker_data={}
for ticker in TICKERS:
    data=load_ticker_data(ticker)
    if data: ticker_data[ticker]=data
    print(f"  {ticker}: {'OK' if data else 'NO DATA'} ({len(data['dates']) if data else 0} bars)")

N=len(ticker_data)
cap_pt=CAPITAL/N
active=list(ticker_data.keys())
print(f"\nActive: {N} tickers, cap/ticker={cap_pt:,.0f}")
print(f"Tickers: {', '.join(active)}\n")

for ticker in active:
    data=ticker_data[ticker]; cs=CS_MAP.get(ticker,1); go=GO_MAP.get(ticker,1000)
    tr=[]
    for pname,pfunc in PATTERNS.items():
        for hold in [1,2,3,5,8,13,21]:
            for sl_pct in [0.005,0.01,0.02]:
                for dv_thr in [0,1.0,2.0]:
                    r=backtest_one(data,ticker,cs,go,pfunc,hold,sl_pct,dv_thr,
                                   cap_per_ticker=cap_pt)
                    if r and r['trades']>=5 and r['ret']>0:
                        r.update(dict(ticker=ticker,pattern=pname,hold=hold,sl=sl_pct,dv_thr=dv_thr,
                                      use_chandelier=False,atr_mult=0,use_partial_exit=False))
                        tr.append(r)
                    
                    # Chandelier variants
                    for am in [2,3,5]:
                        r2=backtest_one(data,ticker,cs,go,pfunc,hold,sl_pct,dv_thr,
                                         use_chandelier=True,atr_mult=am,
                                         cap_per_ticker=cap_pt)
                        if r2 and r2['trades']>=5 and r2['ret']>0:
                            r2.update(dict(ticker=ticker,pattern=pname,hold=hold,sl=sl_pct,dv_thr=dv_thr,
                                           use_chandelier=True,atr_mult=am,use_partial_exit=False))
                            tr.append(r2)
                        
                        # Chandelier+Partial
                        for pa in [0.5,1.0]:
                            r3=backtest_one(data,ticker,cs,go,pfunc,hold,sl_pct,dv_thr,
                                             use_chandelier=True,atr_mult=am,
                                             use_partial_exit=True,partial_atr=pa,
                                             cap_per_ticker=cap_pt)
                            if r3 and r3['trades']>=5 and r3['ret']>0:
                                r3.update(dict(ticker=ticker,pattern=pname,hold=hold,sl=0,dv_thr=dv_thr,
                                               use_chandelier=True,atr_mult=am,
                                               use_partial_exit=True,partial_atr=pa))
                                tr.append(r3)
                    
                    # Stacked variants
                    for ft in [0.5,1.0]:
                        for vt in [1.0,1.5]:
                            r4=backtest_one(data,ticker,cs,go,pfunc,hold,sl_pct,dv_thr,
                                             use_stacked=True,fiz_thr=ft,vol_thr=vt,
                                             cap_per_ticker=cap_pt)
                            if r4 and r4['trades']>=3 and r4['ret']>0:
                                r4.update(dict(ticker=ticker,pattern=pname,hold=hold,sl=sl_pct,dv_thr=dv_thr,
                                               use_stacked=True,fiz_thr=ft,vol_thr=vt))
                                tr.append(r4)
        
        # Chandelier+Stacked
        for hold in [5,8,13,21]:
            for am in [3,5]:
                for ft in [0.5,1.0]:
                    r5=backtest_one(data,ticker,cs,go,pfunc,hold,0.01,0,
                                     use_chandelier=True,atr_mult=am,
                                     use_stacked=True,fiz_thr=ft,
                                     cap_per_ticker=cap_pt)
                    if r5 and r5['trades']>=3 and r5['ret']>0:
                        r5.update(dict(ticker=ticker,pattern=pname,hold=hold,sl=0,dv_thr=0,
                                       use_chandelier=True,atr_mult=am,
                                       use_stacked=True,fiz_thr=ft,vol_thr=1.0))
                        tr.append(r5)
    
    tr.sort(key=lambda x: -x['calmar'])
    print(f"  {ticker}: {len(tr)} profitable combos, top calmar={tr[0]['calmar']:.1f} ret={tr[0]['ret']:+.1f}%" if tr else f"  {ticker}: 0 profitable")
    
    if tr:
        with open(f'{OUTPUT_DIR}/grid_{ticker}.json','w') as f:
            json.dump(tr[:50], f, indent=2)
        all_results.extend(tr)

all_results.sort(key=lambda x: -x['calmar'])
top50=all_results[:50]

with open(f'{OUTPUT_DIR}/all_best.json','w') as f:
    json.dump(top50, f, indent=2)

print(f"\n{'='*60}")
print(f"TOP 50 COMBOS (by Calmar)")
print(f"{'='*60}")
print(f"{'#':>3} {'Ticker':>8} {'Pattern':>22} {'H':>3} {'SL':>5} {'Ch':>3} {'AM':>4} {'Prt':>4} {'Stk':>4} {'Ret':>8} {'DD':>6} {'Calmar':>7} {'WR':>5} {'Tr':>4}")
print("-"*110)
for i,r in enumerate(top50):
    ch='Y' if r.get('use_chandelier') else 'N'
    am=r.get('atr_mult',0)
    prt='Y' if r.get('use_partial_exit') else 'N'
    stk='Y' if r.get('use_stacked') else 'N'
    print(f"{i+1:>3} {r['ticker']:>8} {r['pattern']:>22} {r['hold']:>3} {r.get('sl',0):.1%} "
          f"{ch:>3} {am:>4} {prt:>4} {stk:>4} "
          f"{r['ret']:>+7.1f}% {r['mdd']:>5.1f}% {r['calmar']:>6.1f} {r['wr']:>4.0f}% {r['trades']:>4d}")

# Portfolio sim from top non-overlapping
print(f"\n{'='*60}")
print("PORTFOLIO: Top non-overlapping combos")
print(f"{'='*60}")

used_tickers=set()
pf_signals=[]
for r in top50:
    if r['ticker'] not in used_tickers and len(pf_signals)<6:
        pf_signals.append(r)
        used_tickers.add(r['ticker'])

# Re-run with same capital (200K split evenly)
sig_cap=CAPITAL/len(pf_signals)
all_trades=[]
for sig in pf_signals:
    t=sig['ticker']
    data=ticker_data.get(t)
    if not data: continue
    cs=CS_MAP.get(t,1); go=GO_MAP.get(t,1000)
    pfunc=PATTERNS[sig['pattern']]
    
    r=backtest_one(data,t,cs,go,pfunc,sig['hold'],sig.get('sl',0.01),sig.get('dv_thr',0),
                   use_chandelier=sig.get('use_chandelier',False),
                   atr_mult=sig.get('atr_mult',3.0),
                   use_partial_exit=sig.get('use_partial_exit',False),
                   partial_atr=sig.get('partial_atr',0.5),
                   use_stacked=sig.get('use_stacked',False),
                   fiz_thr=sig.get('fiz_thr',1.0),
                   vol_thr=sig.get('vol_thr',1.5),
                   cap_per_ticker=sig_cap)
    if r:
        ret_pct=(sig_cap*(1+r['ret']/100)-sig_cap)/sig_cap*100
        print(f"  {t}: ret={r['ret']:+.1f}% calmar={r['calmar']:.1f} tr={r['trades']} wr={r['wr']:.0f}%")
        all_trades.append(r)

if all_trades:
    total_ret=sum(r['ret']*sig_cap/100 for r in all_trades)/CAPITAL*100
    avg_dd=np.mean([r['mdd'] for r in all_trades])
    avg_wr=np.mean([r['wr'] for r in all_trades])
    print(f"\n  PORTFOLIO TOTAL (estimated): ret={total_ret:+.1f}% DD={avg_dd:.1f}% WR={avg_wr:.0f}%")

print(f"\nTotal: {len(all_results)} combos, {time.time()-t0:.0f}s")
print(f"Saved to {OUTPUT_DIR}/")
