#!/usr/bin/env python3
"""
BASE v2 LONG+SHORT — доходность по годам.
Сравнение с LONG-only по каждому году.
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
import clickhouse_connect
from bar_level_sim import TICKER_CONFIGS
from config import CH_HOST, CH_PORT, CH_DB

INITIAL = 100_000
SYMBOLS = ['GL', 'HS', 'HY', 'RN', 'NM', 'AF']

PERIODS = [
    ('2023', '2023-01-01', '2024-01-01'),
    ('2024', '2024-01-01', '2025-01-01'),
    ('2025', '2025-01-01', '2026-01-01'),
    ('2026', '2026-01-01', '2026-05-01'),
]


def rz(s,w=20): m=s.rolling(w,min_periods=w).mean();std=s.rolling(w,min_periods=w).std();return (s-m)/std.clip(lower=1e-10)

def calc_atr(df,p=14):
    prev=df['close'].shift(1); tr=pd.concat([df['high']-df['low'],(df['high']-prev).abs(),(df['low']-prev).abs()],axis=1).max(axis=1)
    return tr.rolling(p,min_periods=p).mean().bfill().fillna(0)

def load_data(sym):
    ch=clickhouse_connect.get_client(host=CH_HOST,port=CH_PORT,database=CH_DB)
    q=f"SELECT p.time,p.open,p.high,p.low,p.close,p.volume,o.fiz_buy,o.fiz_sell,o.yur_buy,o.yur_sell,o.total_oi FROM moex.prices_5m p LEFT JOIN moex.prices_5m_oi o ON p.time=o.time AND p.symbol=o.symbol WHERE p.symbol='{sym}' AND p.time>='2023-01-01' AND p.time<='2026-05-01' ORDER BY p.time"
    r=ch.query(q)
    cols=['time','open','high','low','close','volume','fiz_buy','fiz_sell','yur_buy','yur_sell','total_oi']
    df=pd.DataFrame(r.result_rows,columns=cols); df['time']=pd.to_datetime(df['time']).dt.tz_localize(None); df.set_index('time',inplace=True)
    return df

def load_accounts(sym):
    ch=clickhouse_connect.get_client(host=CH_HOST,port=CH_PORT,database=CH_DB)
    q=f"SELECT time,clgroup,buy_accounts,sell_accounts FROM moex.openinterest WHERE symbol='{sym}' AND time>='2023-01-01' AND time<='2026-05-01' ORDER BY time,clgroup"
    r=ch.query(q); rows=r.result_rows
    if not rows: return pd.DataFrame()
    recs=[{'time':r[0],'clg':r[1],'buy_a':r[2],'sell_a':r[3]} for r in rows]
    df=pd.DataFrame(recs); df['time']=pd.to_datetime(df['time']).dt.tz_localize(None)
    fiz=df[df['clg']==0][['time','buy_a','sell_a']].rename(columns={'buy_a':'fiz_buy_a','sell_a':'fiz_sell_a'})
    yur=df[df['clg']==1][['time','buy_a','sell_a']].rename(columns={'buy_a':'yur_buy_a','sell_a':'yur_sell_a'})
    merged=pd.merge(fiz,yur,on='time',how='outer').fillna(0); merged.set_index('time',inplace=True)
    return merged

def precompute(df, acc_df=None):
    d=df.copy()
    d['volume']=d['volume'].astype(float)
    d['vma20']=d['volume'].rolling(20).mean().fillna(d['volume'])
    d['vr']=d['volume']/d['vma20'].clip(lower=1); d['vz']=rz(d['volume'],20)
    d['fiz_net']=d['fiz_buy'].fillna(0)-d['fiz_sell'].fillna(0); d['yur_net']=d['yur_buy'].fillna(0)-d['yur_sell'].fillna(0)
    d['oi_r']=(d['yur_buy']+d['yur_sell']).fillna(0)/(d['fiz_buy']+d['fiz_sell']+1).fillna(0)
    d['oima']=d['oi_r'].rolling(20).mean()
    d['atr14']=calc_atr(d); d['atr_pct']=d['atr14']/d['close'].clip(lower=1)*100

    # === SYMMETRICAL SCORE ===
    vs=np.tanh(d['vz']/3)
    os_raw=(d['oima']-d['oi_r'])/d['oima'].clip(lower=0.1); os_=np.clip(os_raw,-1,1)
    score_sym=vs*0.3+os_*0.7
    af=np.clip(1-(d['atr_pct']-0.3)/3.0,0,1)
    d['score_sym']=np.clip(score_sym*af,-1,1)

    if acc_df is not None and len(acc_df)>0:
        d=d.join(acc_df,how='left').fillna(0)
        d['fiz_vol_pa']=d['fiz_net'].abs()/(d['fiz_buy_a']+d['fiz_sell_a']+1)
        d['yur_a_change']=d['yur_buy_a']-d['yur_sell_a']; d['yur_a_z']=rz(d['yur_a_change'],20)
        d['conc']=np.clip(d['fiz_vol_pa']/1000.0,0,1); d['yur_conf']=np.clip(d['yur_a_z']/2.0,0,1)
        d['score_sym']=np.clip(d['score_sym']*(1+d['conc']*0.5+d['yur_conf']*0.3),-1,1)

    # === OLD LONG score for comparison ===
    vs_old=np.clip((d['vr']-1.5)/3.0,0,1); os_old=np.clip((d['oima']-d['oi_r'])/d['oima'].clip(lower=0.1),0,1)
    d['score_old']=np.clip((vs_old*0.6+os_old*0.4)*af,0,1)
    return d

def simulate_year(d, start, end, sym, mode='ls'):
    """mode='ls': LONG+SHORT, 'long': только LONG"""
    mask=(d.index>=pd.Timestamp(start))&(d.index<pd.Timestamp(end))
    dd=d[mask].copy()
    if len(dd)<100: return None

    cash=float(INITIAL); peak=float(INITIAL); max_dd=0.0; trades=0; wins=0
    go=TICKER_CONFIGS.get(sym,{}).get('go',5000); pos=None

    for i in range(1,len(dd)):
        bar=dd.iloc[i]; h=bar.name.hour
        if h<7 or h>=23: continue
        if pos is not None:
            pos['bars_left']-=1; hit=False; ep=float(bar['close'])
            if pos['dir']=='L' and float(bar['low'])<=pos['stop']: hit=True; ep=pos['stop']
            elif pos['dir']=='S' and float(bar['high'])>=pos['stop']: hit=True; ep=pos['stop']
            elif pos['bars_left']<=0: hit=True
            if hit:
                dm=1 if pos['dir']=='L' else -1
                pp=dm*(ep-pos['entry'])/pos['entry']; pr=pp*pos['go']*pos['contracts']
                cash+=pr; trades+=1
                if pr>0: wins+=1; pos=None
        if pos is not None:
            dm=1 if pos['dir']=='L' else -1
            mtm=dm*(float(bar['close'])-pos['entry'])/pos['entry']*pos['go']*pos['contracts']
            teq=cash+mtm
        else: teq=cash
        if teq>peak: peak=teq
        ddv=(peak-teq)/peak if peak>0 else 0
        if ddv>max_dd: max_dd=ddv
        if pos is not None: continue

        if mode=='ls':
            s=float(bar['score_sym'])
            if np.isnan(s) or abs(s)<0.10: continue
            direction='L' if s>0 else 'S'
        else:
            s=float(bar['score_old'])
            if np.isnan(s) or s<0.10: continue
            direction='L'

        lp=1.00
        if sym in ['HY','AF']: lp=0.75
        contracts=int(cash*lp/go)
        if contracts<1 or cash<go: continue  # минимум 1 GO
        atrv=float(bar.get('atr14',1)); ep=float(bar['close'])
        stop_p=ep-atrv*1.0 if direction=='L' else ep+atrv*1.0
        pos={'dir':direction,'entry':ep,'stop':stop_p,'bars_left':8,'go':go,'contracts':contracts}

    if pos is not None:
        lb=dd.iloc[-1]; dm=1 if pos['dir']=='L' else -1
        pp=dm*(float(lb['close'])-pos['entry'])/pos['entry']; pr=pp*pos['go']*pos['contracts']
        cash+=pr; trades+=1
        if pr>0: wins+=1

    tr=(cash-INITIAL)/INITIAL*100
    days=max((pd.Timestamp(end)-pd.Timestamp(start)).days,30); yrs_=days/365.25
    cagr=((cash/INITIAL)**(1/max(yrs_,0.1))-1)*100 if cash>0 else -100
    calmar=tr/100/max(max_dd,0.001) if max_dd>0 else tr*10
    return {'ret':round(tr,2),'cagr':round(cagr,2),'dd':round(max_dd*100,2),'calmar':round(calmar,2),'wr':round(wins/trades*100,2) if trades>0 else 0,'trades':trades}


def main():
    print("="*100)
    print("LONG+SHORT ПО ГОДАМ")
    print("="*100, flush=True)

    loaded={}
    for sym in SYMBOLS:
        t0=time.time(); df=load_data(sym); acc=load_accounts(sym); loaded[sym]=precompute(df,acc)
        print(f"  {sym}: {len(loaded[sym])} баров за {time.time()-t0:.1f}s", flush=True)

    # По каждому тикеру — оба режима по годам
    for sym in SYMBOLS:
        d=loaded[sym]
        print(f"\n{sym}:")
        print(f"{'Год':<6}{'Режим':<8}{'Ret%':>10}{'DD%':>7}{'Calmar':>9}{'CAGR%':>9}{'WR%':>6}{'Tr':>6}")
        print('-'*61)
        for pname,pstart,pend in PERIODS:
            r_ls=simulate_year(d,pstart,pend,sym,'ls')
            r_l=simulate_year(d,pstart,pend,sym,'long')
            if r_ls: print(f"{pname:<6}{'L+S':<8}{r_ls['ret']:>9.1f}%{r_ls['dd']:>6.1f}%{r_ls['calmar']:>9.1f}{r_ls['cagr']:>8.1f}%{r_ls['wr']:>5.1f}%{r_ls['trades']:>6}")
            if r_l:  print(f"{'':<6}{'LONG':<8}{r_l['ret']:>9.1f}%{r_l['dd']:>6.1f}%{r_l['calmar']:>9.1f}{r_l['cagr']:>8.1f}%{r_l['wr']:>5.1f}%{r_l['trades']:>6}")
            if r_ls and r_l:
                ratio=r_ls['ret']/r_l['ret'] if r_l['ret']!=0 else float('inf')
                print(f"{'':<6}{'  ×':<8}{ratio:>9.1f}x{'':>14}{'':>23}")
        # ALL
        r_ls=simulate_year(d,'2024-01-01','2026-05-01',sym,'ls')
        r_l=simulate_year(d,'2024-01-01','2026-05-01',sym,'long')
        if r_ls: print(f"{'ALL':<6}{'L+S':<8}{r_ls['ret']:>9.1f}%{r_ls['dd']:>6.1f}%{r_ls['calmar']:>9.1f}{r_ls['cagr']:>8.1f}%{r_ls['wr']:>5.1f}%{r_ls['trades']:>6}")
        if r_l:  print(f"{'':<6}{'LONG':<8}{r_l['ret']:>9.1f}%{r_l['dd']:>6.1f}%{r_l['calmar']:>9.1f}{r_l['cagr']:>8.1f}%{r_l['wr']:>5.1f}%{r_l['trades']:>6}")
        print(f"  done", flush=True)

    # Сводная
    print(f"\n{'='*100}")
    print(f"СВОДНАЯ: среднее по {len(SYMBOLS)} тикерам")
    print(f"{'='*100}")

    for mode_name, mode in [('LONG+SHORT','ls'),('LONG only','long')]:
        print(f"\n{mode_name}:")
        print(f"{'Год':<6}",end='')
        for pname,_,_ in PERIODS:
            print(f"  {pname:>13}",end='')
        print(f"  {'ALL':>13}")
        print('-'*(6+15*(len(PERIODS)+1)))

        for metric_name,key in [('CAGR%','cagr'),('Ret%','ret'),('DD%','dd'),('Calmar','calmar'),('WR%','wr')]:
            print(f"{metric_name:<6}",end='')
            for pname,pstart,pend in PERIODS:
                vals=[simulate_year(loaded[s],pstart,pend,s,mode) for s in SYMBOLS]
                vals=[v for v in vals if v]
                if vals:
                    avg=sum(v[key] for v in vals)/len(vals)
                    if key in ('cagr','ret','dd','wr'):
                        print(f"  {avg:>12.1f}%",end='')
                    else:
                        print(f"  {avg:>12.1f}",end='')
                else: print(f"  {'N/A':>13}",end='')
            vals=[simulate_year(loaded[s],'2024-01-01','2026-05-01',s,mode) for s in SYMBOLS]
            vals=[v for v in vals if v]
            if vals:
                avg=sum(v[key] for v in vals)/len(vals)
                if key in ('cagr','ret','dd','wr'):
                    print(f"  {avg:>12.1f}%",end='')
                else:
                    print(f"  {avg:>12.1f}",end='')
            print()

    # Сравнение CAGR L+S vs LONG по годам
    print(f"\n{'='*100}")
    print(f"СРАВНЕНИЕ CAGR: LONG+SHORT vs LONG-only")
    print(f"{'='*100}")
    print(f"{'Год':<6}{'L+S CAGR':>10}{'LONG CAGR':>10}{'Δ':>10}{'×':>8}")
    print('-'*44)
    for pname,pstart,pend in PERIODS:
        ls_cagrs=[simulate_year(loaded[s],pstart,pend,s,'ls') for s in SYMBOLS]
        l_cagrs=[simulate_year(loaded[s],pstart,pend,s,'long') for s in SYMBOLS]
        ls_cagrs=[v['cagr'] for v in ls_cagrs if v]
        l_cagrs=[v['cagr'] for v in l_cagrs if v]
        if ls_cagrs and l_cagrs:
            ls_avg=sum(ls_cagrs)/len(ls_cagrs); l_avg=sum(l_cagrs)/len(l_cagrs)
            mult=ls_avg/l_avg if l_avg!=0 else float('inf')
            print(f"{pname:<6}{ls_avg:>9.1f}%{l_avg:>9.1f}%{ls_avg-l_avg:>9.1f}%{mult:>7.1f}x")


if __name__=='__main__':
    main()
