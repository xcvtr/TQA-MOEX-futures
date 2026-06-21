#!/usr/bin/env python3
"""
Честный пересчёт BASE v2 + LONG+SHORT.
Фикс: contracts считаются строго, cash не уходит в минус.
lot_pct=0.50 (50% — как в оригинальном корректном тесте).
LONG+SHORT по годам.
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
LOT_PCT = 0.50  # 50% капитала на контракт (честно)

PERIODS = [
    ('2023', '2023-01-01', '2024-01-01'),
    ('2024', '2024-01-01', '2025-01-01'),
    ('2025', '2025-01-01', '2026-01-01'),
    ('2026', '2026-01-01', '2026-05-01'),
]


def rz(s,w=20): m=s.rolling(w).mean();std=s.rolling(w).std();return (s-m)/std.clip(lower=1e-10)

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
    d['vz']=rz(d['volume'],20)
    d['fiz_net']=d['fiz_buy'].fillna(0)-d['fiz_sell'].fillna(0); d['yur_net']=d['yur_buy'].fillna(0)-d['yur_sell'].fillna(0)
    d['oi_r']=(d['yur_buy']+d['yur_sell']).fillna(0)/(d['fiz_buy']+d['fiz_sell']+1).fillna(0)
    d['oima']=d['oi_r'].rolling(20).mean()
    d['atr14']=calc_atr(d); d['atr_pct']=d['atr14']/d['close'].clip(lower=1)*100

    # Symmetrical score
    vs=np.tanh(d['vz']/3); os_raw=(d['oima']-d['oi_r'])/d['oima'].clip(lower=0.1); os_=np.clip(os_raw,-1,1)
    score_sym=vs*0.3+os_*0.7
    af=np.clip(1-(d['atr_pct']-0.3)/3.0,0,1)
    d['score_sym']=np.clip(score_sym*af,-1,1)

    if acc_df is not None and len(acc_df)>0:
        d=d.join(acc_df,how='left').fillna(0)
        d['fiz_vol_pa']=d['fiz_net'].abs()/(d['fiz_buy_a']+d['fiz_sell_a']+1)
        d['yur_a_change']=d['yur_buy_a']-d['yur_sell_a']; d['yur_a_z']=rz(d['yur_a_change'],20)
        d['conc']=np.clip(d['fiz_vol_pa']/1000.0,0,1); d['yur_conf']=np.clip(d['yur_a_z']/2.0,0,1)
        d['score_sym']=np.clip(d['score_sym']*(1+d['conc']*0.5+d['yur_conf']*0.3),-1,1)

    # Old LONG-only score — как в portfolio_sweep_enhancements.py
    d['vr']=d['volume']/d['vma20'].clip(lower=1)  # volume ratio
    vs_old=np.clip((d['vr']-1.5)/3.0,0,1)
    os_old=np.clip((d['oima']-d['oi_r'])/d['oima'].clip(lower=0.1),0,1)
    d['score_old']=np.clip((vs_old*0.6+os_old*0.4)*af,0,1)
    return d

def simulate(d, start, end, sym, use_short):
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

        if use_short:
            s=float(bar['score_sym'])
            if np.isnan(s) or abs(s)<0.10: continue
            direction='L' if s>0 else 'S'
        else:
            s=float(bar['score_old'])
            if np.isnan(s) or s<0.10: continue
            direction='L'

        # Честный размер позиции
        lp=LOT_PCT
        if sym in ['HY','AF'] and lp>0.40: lp=0.40
        potential=cash*lp
        if potential<go: continue  # не хватает на 1 контракт
        contracts=int(potential/go)
        if contracts<1: continue

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
    wr=wins/trades*100 if trades>0 else 0
    return {'ret':round(tr,2),'cagr':round(cagr,2),'dd':round(max_dd*100,2),'calmar':round(calmar,2),'wr':round(wr,2),'trades':trades,'final_cash':round(cash,2)}


def main():
    print("="*100)
    print(f"ЧЕСТНЫЙ ПЕРЕСЧЁТ: LOT={LOT_PCT*100:.0f}%")
    print("="*100, flush=True)

    loaded={}
    for sym in SYMBOLS:
        t0=time.time(); df=load_data(sym); acc=load_accounts(sym); loaded[sym]=precompute(df,acc)
        print(f"  {sym}: {len(loaded[sym])} баров за {time.time()-t0:.1f}s", flush=True)

    # По тикерам
    for sym in SYMBOLS:
        d=loaded[sym]
        print(f"\n{sym}:")
        print(f"{'Год':<6}{'Реж':<5}{'Ret%':>9}{'DD%':>7}{'Calm':>8}{'WR%':>6}{'CAGR%':>8}{'Tr':>6}")
        print('-'*55)
        for pname,pstart,pend in PERIODS:
            r_ls=simulate(d,pstart,pend,sym,True)
            r_l=simulate(d,pstart,pend,sym,False)
            if r_ls: print(f"{pname:<6}{'L+S':<5}{r_ls['ret']:>8.1f}%{r_ls['dd']:>6.1f}%{r_ls['calmar']:>8.1f}{r_ls['wr']:>5.1f}%{r_ls['cagr']:>7.1f}%{r_ls['trades']:>6}")
            if r_l:  print(f"{'':<6}{'L':<5}{r_l['ret']:>8.1f}%{r_l['dd']:>6.1f}%{r_l['calmar']:>8.1f}{r_l['wr']:>5.1f}%{r_l['cagr']:>7.1f}%{r_l['trades']:>6}")
        print(f"  done", flush=True)

    # Сводная
    print(f"\n{'='*100}")
    print(f"СВОДНАЯ: среднее по {len(SYMBOLS)} тикерам, LOT={LOT_PCT*100:.0f}%")
    print(f"{'='*100}")

    for mode_name, mode in [('LONG+SHORT',True),('LONG only',False)]:
        print(f"\n{mode_name}:")
        for metric_name,key in [('CAGR%','cagr'),('Ret%','ret'),('DD%','dd'),('Calmar','calmar'),('WR%','wr')]:
            print(f"  {metric_name:<8}",end='')
            for pname,pstart,pend in PERIODS:
                vals=[simulate(loaded[s],pstart,pend,s,mode) for s in SYMBOLS]
                vals=[v for v in vals if v]
                if vals:
                    avg=sum(v[key] for v in vals)/len(vals)
                    suf='%' if key in ('cagr','ret','dd','wr') else ''
                    print(f"  {pname}={avg:.1f}{suf}",end='')
            print()

    # Сравнение
    print(f"\n{'='*100}")
    print(f"СРАВНЕНИЕ: CAGR по годам")
    print(f"{'='*100}")
    print(f"{'Год':<6}{'L+S CAGR':>10}{'LONG CAGR':>10}{'×':>8}")
    print('-'*34)
    for pname,pstart,pend in PERIODS:
        ls_c=[simulate(loaded[s],pstart,pend,s,True) for s in SYMBOLS]
        l_c=[simulate(loaded[s],pstart,pend,s,False) for s in SYMBOLS]
        ls_v=[v['cagr'] for v in ls_c if v]; l_v=[v['cagr'] for v in l_c if v]
        if ls_v and l_v:
            la=sum(ls_v)/len(ls_v); lo=sum(l_v)/len(l_v)
            mult=la/lo if lo!=0 else float('inf')
            print(f"{pname:<6}{la:>9.1f}%{lo:>9.1f}%{mult:>7.1f}x")


if __name__=='__main__':
    main()
