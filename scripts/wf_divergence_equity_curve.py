#!/usr/bin/env python3
"""Equity curve + drawdown analysis для CVD divergence, 1 cont, без реинвеста"""
import clickhouse_connect, pandas as pd, numpy as np, sys
ch = clickhouse_connect.get_client(host='10.0.0.64', database='moex')
SLIPPAGE=0.5; TICK_COST={'NG':3.715,'BR':0.743,'Si':0.0025,'MXI':0.10}
TICK={'NG':0.0005,'BR':0.001,'Si':0.0025,'MXI':0.01}; SYMBOLS=['NG','BR','Si','MXI']
lk=20; hb=1; q=0.6

def resample(df):
    d=df.copy(); d['time']=pd.to_datetime(d['time'])
    d=d.set_index('time').resample('5min').agg({'open':'first','close':'last','vol_b':'sum','vol_s':'sum'}).dropna(subset=['open']).reset_index()
    d['cvd']=d['vol_b'].fillna(0)-d['vol_s'].fillna(0); d['date']=d['time'].dt.date; return d
data={}
for SYM in SYMBOLS:
    sys.stdout.write(f"Loading {SYM}...\n"); sys.stdout.flush()
    data[SYM]=resample(ch.query_df(f"""SELECT toDateTime(tradedate||' '||tradetime)AS time,pr_open AS open,pr_close AS close,vol_b,vol_s FROM moex.tradestats_fo WHERE asset_code='{SYM}'AND vol>0 ORDER BY time"""))

trades=[]
for SYM in SYMBOLS:
    df=data[SYM].copy(); tc=TICK_COST[SYM]; tick=TICK[SYM]
    dates=sorted(df['date'].unique()); ws_train=min(180,max(60,len(dates)//3)); ws_test=min(60,max(20,len(dates)//6)); i=ws_train
    while i<len(dates):
        te=min(i+ws_test,len(dates)); td=set(dates[i-ws_train:i]); ted=set(dates[i:te])
        if len(ted)<20: i+=ws_test; continue
        train=df[df['date'].isin(td)].copy(); test=df[df['date'].isin(ted)].copy().reset_index(drop=True)
        if len(train)<50 or len(test)<10: i+=ws_test; continue
        train['cvd_cum']=train['cvd'].cumsum(); train['pchg']=train['close'].diff(lk).dropna(); train['cchg']=train['cvd_cum'].diff(lk).dropna(); tv=train.dropna()
        if len(tv)<30: i+=ws_test; continue
        p=tv['pchg'].abs().quantile(q); c=tv['cchg'].abs().quantile(q)
        if p==0 or c==0: i+=ws_test; continue
        lc=train['cvd_cum'].iloc[-1]; test['cvd_cum']=lc+test['cvd'].cumsum(); test['pchg']=test['close'].diff(lk); test['cchg']=test['cvd_cum'].diff(lk); tv=test.dropna()
        bear=(tv['pchg']>p)&(tv['cchg']<-c); bull=(tv['pchg']<-p)&(tv['cchg']>c)
        for si in range(len(tv)):
            s=-1 if bear.iloc[si] else(1 if bull.iloc[si] else 0)
            if s==0 or si>=len(tv)-1: continue
            ep=tv.iloc[si]['close']; ei=min(si+hb,len(tv)-1); xp=tv.iloc[ei]['close']
            pt=(xp-ep)*s/tick; rub=pt*tc-SLIPPAGE*tc
            trades.append({'t':tv.iloc[ei]['time'],'pnl':rub,'s':SYM,'m':str(tv.iloc[ei]['time'].to_period('M'))})
        i+=ws_test

df=pd.DataFrame(trades).sort_values('t')

# Equity curve
eq=[]; cum=0.0; peak=0.0; max_dd_val=0.0; max_dd_peak_val=0.0; max_dd_trough=0.0; max_dd_start=None; max_dd_end=None
cur_peak=cum; cur_dd_start=None
for _,r in df.iterrows():
    cum+=r['pnl']
    if cum>peak:
        peak=cum
        if cur_dd_start is not None:
            # закончилась просадка
            cur_dd_start=None
    else:
        if cur_dd_start is None:
            cur_dd_start=r['t']
    dd=(peak-cum)/peak*100 if peak>0 else 0
    if dd>max_dd_val:
        max_dd_val=dd; max_dd_peak_val=peak; max_dd_trough=cum
        max_dd_start=cur_dd_start; max_dd_end=r['t']
    eq.append({'t':r['t'],'cum':cum,'peak':peak,'dd':dd,'pnl':r['pnl']})

eq_df=pd.DataFrame(eq)

# Find all major drawdowns (>10%)
print(f"{'='*70}")
print(f"  EQUITY CURVE — CVD divergence (1 cont, M5 lk=20 hold=1 q=0.6)")
print(f"{'='*70}")
print(f"  Начало: {df['t'].min()}")
print(f"  Конец:  {df['t'].max()}")
print(f"  Сделок: {len(df):,}")
print(f"  Net PnL: {cum:+,.0f} RUB")
print()

# Max DD details
print(f"  ⚠️  MAX DRAWDOWN: {max_dd_val:.2f}%")
print(f"     Пик:   {max_dd_peak_val:>12,.0f} RUB ({max_dd_start})")
print(f"     Впадина: {max_dd_trough:>12,.0f} RUB ({max_dd_end})")
print(f"     Потеряно: {max_dd_peak_val-max_dd_trough:>12,.0f} RUB")
print()

# Monthly equity
m_eq = eq_df.set_index('t').resample('M')['cum'].last().ffill()
mon_ret = m_eq.pct_change() * 100
print(f"  Месяцев всего: {len(mon_ret)}")
print(f"  Месяцев с отрицательной доходностью: {(mon_ret<0).sum()} ({(mon_ret<0).sum()/len(mon_ret)*100:.1f}%)")
print(f"  Худший месяц: {mon_ret.min():.2f}%")

# All drawdowns >5%
print(f"\n{'='*70}")
print(f"  ALL DRAWDOWNS > 5%")
print(f"{'='*70}")
dds=[]
in_dd=False; dd_start=None; dd_peak_local=0; dd_peak_val=0
for _,r in eq_df.iterrows():
    if not in_dd and r['dd']>0.1:
        in_dd=True; dd_start=r['t']; dd_peak_local=r['peak']
    if in_dd:
        dd_peak_local=max(dd_peak_local,r['peak'])
        if r['cum']>=dd_peak_local*0.999:
            pass
    if in_dd and r['dd']<0.1:
        in_dd=False
        # record
        if r['cum']<dd_peak_local:
            dds.append({'start':dd_start,'end':r['t'],'peak':dd_peak_local,'trough':r['cum'],
                       'dd':(dd_peak_local-r['cum'])/dd_peak_local*100,'recovery':True})

# Если не закончилась
if in_dd:
    dds.append({'start':dd_start,'end':eq_df.iloc[-1]['t'],'peak':dd_peak_local,
               'trough':eq_df.iloc[-1]['cum'],'dd':(dd_peak_local-eq_df.iloc[-1]['cum'])/dd_peak_local*100,'recovery':False})

# Фильтр >5%
major_dds=[d for d in dds if d['dd']>5]
major_dds.sort(key=lambda x:x['dd'],reverse=True)
print(f"  Найдено просадок >5%: {len(major_dds)}")
print(f"{'Старт':<20} {'Конец':<20} {'DD%':>7} {'Потеря(RUB)':>12} {'Восст':>6}")
print(f"{'-'*70}")
for d in major_dds[:15]:
    rec='Да' if d['recovery'] else 'НЕТ'
    loss=d['peak']-d['trough']
    print(f"{str(d['start'])[:19]:<20} {str(d['end'])[:19]:<20} {d['dd']:>6.1f}% {loss:>10,.0f} {rec:>6}")

# Worst drawdowns by duration
print(f"\n{'='*70}")
print(f"  SAMPLES OF NEGATIVE EQUITY (when underwater)")
print(f"{'='*70}")
underwater = eq_df[eq_df['cum'] < 0]
if len(underwater)>0:
    print(f"  Счёт был отрицательным: {len(underwater)} раз")
    print(f"  Первый раз: {underwater['t'].iloc[0]}")
    print(f"  Минимальный баланс: {underwater['cum'].min():,.0f} RUB ({underwater.loc[underwater['cum'].idxmin(),'t']})")
else:
    print(f"  Счёт ни разу не был отрицательным")

# Loss streaks
print(f"\n{'='*70}")
print(f"  LOSS STREAK ANALYSIS")
print(f"{'='*70}")
max_loss_streak=0; cur=0; cur_total=0; streaks=[]
for _,r in df.iterrows():
    if r['pnl']<0:
        cur+=1; cur_total+=r['pnl']
        max_loss_streak=max(max_loss_streak,cur)
    else:
        if cur>3:
            streaks.append({'n':cur,'total':cur_total})
        cur=0; cur_total=0
if cur>3:
    streaks.append({'n':cur,'total':cur_total})
print(f"  Макс серия лоссов: {max_loss_streak}")
if streaks:
    streaks.sort(key=lambda x:x['n'],reverse=True)
    print(f"  Серии >3 лоссов подряд:")
    for s in streaks[:10]:
        print(f"    {s['n']} loss(es), total {s['total']:+,.0f} RUB")
else:
    print(f"  Нет серий >3 лоссов подряд")

# Win streaks
max_win_streak=0; cur=0
for _,r in df.iterrows():
    if r['pnl']>0: cur+=1; max_win_streak=max(max_win_streak,cur)
    else: cur=0
print(f"  Макс серия вин: {max_win_streak}")
print(f"\n{'='*70}")
print(f"  DONE")
print(f"{'='*70}")
