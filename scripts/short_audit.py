#!/usr/bin/env python3
"""
Аудит SHORT-сигналов: проверка что SHORT не баг.
1. Корреляция LONG vs SHORT входов
2. Отдельно LONG PnL vs SHORT PnL по годам
3. Перекрёстная проверка: только SHORT (без LONG)
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
SYMBOLS = ['GL', 'HS', 'RN', 'NM']  # 4 тикера для скорости
START = '2024-01-01'
END = '2026-05-01'


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
    d['fz']=rz(d['fiz_net'],20); d['yz']=rz(d['yur_net'],20)
    d['oi_r']=(d['yur_buy']+d['yur_sell']).fillna(0)/(d['fiz_buy']+d['fiz_sell']+1).fillna(0)
    d['oima']=d['oi_r'].rolling(20).mean()
    d['atr14']=calc_atr(d); d['atr_pct']=d['atr14']/d['close'].clip(lower=1)*100
    vs_raw=(d['vr']-1.5)/3.0; vs=np.clip(vs_raw,-1,1)
    os_raw=(d['oima']-d['oi_r'])/d['oima'].clip(lower=0.1); os_=np.clip(os_raw,-1,1)
    score_raw=vs*0.6+os_*0.4
    af=np.clip(1-(d['atr_pct']-0.3)/3.0,0,1)
    vzf=np.clip(1+d['vz']/5,0.5,1.5)
    d['score_sym']=np.clip(score_raw*af*vzf,-1,1)
    if acc_df is not None and len(acc_df)>0:
        d=d.join(acc_df,how='left').fillna(0)
        d['fiz_vol_pa']=d['fiz_net'].abs()/(d['fiz_buy_a']+d['fiz_sell_a']+1)
        d['yur_a_change']=d['yur_buy_a']-d['yur_sell_a']; d['yur_a_z']=rz(d['yur_a_change'],20)
        d['conc']=np.clip(d['fiz_vol_pa']/1000.0,0,1); d['yur_conf']=np.clip(d['yur_a_z']/2.0,0,1)
        d['score_sym']=np.clip(d['score_sym']*(1+d['conc']*0.5+d['yur_conf']*0.3),-1,1)
    return d

def simulate_with_log(d, sym, mode='both'):
    """mode='long': только LONG, 'short': только SHORT, 'both': оба"""
    mask=(d.index>=pd.Timestamp(START))&(d.index<pd.Timestamp(END))
    dd=d[mask].copy()
    if len(dd)<100: return None

    cash=float(INITIAL); peak=float(INITIAL); max_dd=0.0; trades=0; wins=0
    go=TICKER_CONFIGS.get(sym,{}).get('go',5000); pos=None
    long_pnl=0.0; short_pnl=0.0

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
                if pr>0: wins+=1
                if pos['dir']=='L': long_pnl+=pr
                else: short_pnl+=pr
                pos=None
        if pos is not None:
            dm=1 if pos['dir']=='L' else -1
            mtm=dm*(float(bar['close'])-pos['entry'])/pos['entry']*pos['go']*pos['contracts']
            teq=cash+mtm
        else: teq=cash
        if teq>peak: peak=teq
        ddv=(peak-teq)/peak if peak>0 else 0
        if ddv>max_dd: max_dd=ddv
        if pos is not None: continue

        s=float(bar['score_sym'])
        if np.isnan(s) or abs(s)<0.10: continue
        direction='L' if s>0 else 'S'

        if mode=='long' and direction!='L': continue
        if mode=='short' and direction!='S': continue

        contracts=max(1,int(cash*1.00/go))
        atrv=float(bar.get('atr14',1)); ep=float(bar['close'])
        stop_p=ep-atrv*1.0 if direction=='L' else ep+atrv*1.0
        pos={'dir':direction,'entry':ep,'stop':stop_p,'bars_left':8,'go':go,'contracts':contracts}

    if pos is not None:
        lb=dd.iloc[-1]; dm=1 if pos['dir']=='L' else -1
        pp=dm*(float(lb['close'])-pos['entry'])/pos['entry']; pr=pp*pos['go']*pos['contracts']
        cash+=pr; trades+=1
        if pr>0: wins+=1
        if pos['dir']=='L': long_pnl+=pr
        else: short_pnl+=pr

    tr=(cash-INITIAL)/INITIAL*100
    days=max((pd.Timestamp(END)-pd.Timestamp(START)).days,30); yrs_=days/365.25
    cagr=((cash/INITIAL)**(1/max(yrs_,0.1))-1)*100 if cash>0 else -100
    calmar=tr/100/max(max_dd,0.001) if max_dd>0 else tr*10
    return {'ret':round(tr,2),'cagr':round(cagr,2),'dd':round(max_dd*100,2),'calmar':round(calmar,2),'wr':round(wins/trades*100,2) if trades>0 else 0,'trades':trades,'long_pnl':round(long_pnl,0),'short_pnl':round(short_pnl,0)}


def main():
    print("="*100)
    print("АУДИТ SHORT-СИГНАЛОВ")
    print("="*100, flush=True)

    loaded={}
    for sym in SYMBOLS:
        t0=time.time(); df=load_data(sym); acc=load_accounts(sym); loaded[sym]=precompute(df,acc)
        print(f"  {sym}: {len(loaded[sym])} баров за {time.time()-t0:.1f}s", flush=True)

    # Для каждого тикера: 3 режима
    print(f"\n{'='*100}")
    print(f"LONG vs SHORT vs BOTH: абсолютные PnL")
    print(f"{'='*100}")

    for sym in SYMBOLS:
        d=loaded[sym]
        r_both=simulate_with_log(d,sym,'both')
        r_long=simulate_with_log(d,sym,'long')
        r_short=simulate_with_log(d,sym,'short')

        print(f"\n{sym}:")
        print(f"{'Режим':<10}{'Ret%':>10}{'DD%':>7}{'Calmar':>10}{'Trades':>7}{'LongPnL':>12}{'ShortPnL':>12}")
        print('-'*68)
        if r_both: print(f"{'BOTH':<10}{r_both['ret']:>9.1f}%{r_both['dd']:>6.1f}%{r_both['calmar']:>10.1f}{r_both['trades']:>7}{r_both['long_pnl']:>12.0f}{r_both['short_pnl']:>12.0f}")
        if r_long: print(f"{'LONG only':<10}{r_long['ret']:>9.1f}%{r_long['dd']:>6.1f}%{r_long['calmar']:>10.1f}{r_long['trades']:>7}{r_long['long_pnl']:>12.0f}{0:>12}")
        if r_short: print(f"{'SHORT only':<10}{r_short['ret']:>9.1f}%{r_short['dd']:>6.1f}%{r_short['calmar']:>10.1f}{r_short['trades']:>7}{0:>12}{r_short['short_pnl']:>12.0f}")

        if r_long and r_short:
            print(f"  LONG сделок: {r_long['trades']}, SHORT сделок: {r_short['trades']}, "
                  f"пересечение: {r_long['trades']+r_short['trades']-r_both['trades']}")
            # Корреляция сигналов: баров где оба активны
            dd=d.loc[pd.Timestamp(START):pd.Timestamp(END)]
            scores=dd['score_sym'].dropna()
            long_pct=(scores>0.10).sum()/len(scores)*100
            short_pct=(scores<-0.10).sum()/len(scores)*100
            neutral_pct=100-long_pct-short_pct
            print(f"  Распределение сигналов: LONG={long_pct:.0f}%, SHORT={short_pct:.0f}%, нейтрально={neutral_pct:.0f}%")
        print(f"  done", flush=True)

    # Сводный вердикт
    print(f"\n{'='*100}")
    print(f"ВЕРДИКТ АУДИТА")
    print(f"{'='*100}")

    for sym in SYMBOLS:
        d=loaded[sym]
        r_both=simulate_with_log(d,sym,'both')
        r_long=simulate_with_log(d,sym,'long')
        r_short=simulate_with_log(d,sym,'short')
        if r_long and r_short and r_both:
            lpnl=r_long['long_pnl']
            spnl=r_short['short_pnl']
            combined_contrib=lpnl+spnl
            print(f"\n{sym}:")
            print(f"  LONG contribution: {lpnl:>12,.0f} ₽")
            print(f"  SHORT contribution: {spnl:>12,.0f} ₽")
            print(f"  Combined (L+S): {combined_contrib:>12,.0f} ₽")
            print(f"  Both PnL: {r_both['long_pnl']+r_both['short_pnl']:>12,.0f} ₽")
            print(f"  DD: LONG-only={r_long['dd']:.1f}%, SHORT-only={r_short['dd']:.1f}%, BOTH={r_both['dd']:.1f}%")
            print(f"  Ret: LONG-only={r_long['ret']:.0f}%, SHORT-only={r_short['ret']:.0f}%, BOTH={r_both['ret']:.0f}%")
            print(f"  Синергия: L+S ret={r_both['ret']:.0f}% vs L={r_long['ret']:.0f}%+S={r_short['ret']:.0f}%")
            synergy=r_both['ret']-(r_long['ret']+r_short['ret'])
            print(f"  Эффект реинвеста (сложение капитала): {synergy:+.0f}%")


if __name__=='__main__':
    main()
