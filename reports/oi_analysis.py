#!/usr/bin/env python3
import pandas as pd
import numpy as np
from sqlalchemy import create_engine
from datetime import datetime
from itertools import combinations

engine = create_engine('postgresql://postgres@10.0.0.60:5432/moex')

def ld(sym=None, st='2021-01-01', en='2026-12-31'):
    w = ''
    if sym:
        w = " AND symbol IN ('" + "','".join(sym) + "')"
    q = "SELECT symbol,time,clgroup,buy_orders,sell_orders,buy_accounts,sell_accounts FROM openinterest_moex WHERE time>='" + st + "' AND time<='" + en + "'" + w + " ORDER BY symbol,time,clgroup"
    df = pd.read_sql(q, engine)
    if not df.empty:
        df['fiz_net'] = df['buy_orders'] - df['sell_orders']
    return df

def t1():
    print('='*70)
    print('TASK 1: Si FIZ ratio monthly & structural breaks')
    print('='*70)
    df = ld(sym=['Si'])
    if df.empty: return
    fiz = df[df['clgroup']==0][['time','fiz_net']].rename(columns={'fiz_net':'fn'})
    yur = df[df['clgroup']==1][['time','fiz_net']].rename(columns={'fiz_net':'yn'})
    m = pd.merge(fiz, yur, on='time', how='inner')
    m['time'] = pd.to_datetime(m['time'])
    m['mo'] = m['time'].dt.to_period('M')
    mo = m.groupby('mo').agg({'fn':'sum','yn':'sum'}).reset_index()
    mo['nd'] = mo['fn'] - mo['yn']
    mo['r'] = np.where(mo['nd']!=0, mo['fn']/mo['nd'], np.nan)
    mo['ch'] = mo['r'].diff()
    sc = mo['ch'].std()
    mc = mo['ch'].mean()
    mo['br'] = np.abs(mo['ch']-mc) > 2*sc
    print(f"{'Month':<7} {'FIZ_net':>10} {'YUR_net':>10} {'Net_Diff':>10} {'Ratio':>8} {'Break':>8}")
    print('-'*53)
    brk = []
    for _, r in mo.iterrows():
        f = ' ***' if r['br'] else ''
        print(f"{str(r['mo']):<7} {r['fn']:>10,.0f} {r['yn']:>10,.0f} {r['nd']:>10,.0f} {r['r']:>8.4f} {f}")
        if r['br']: brk.append(str(r['mo']))
    print(f'\nBreaks (>2sigma, sigma={sc:.4f}):')
    if brk:
        for b in brk: print(f'  {b}')
    else: print('  None')
    mo.to_csv('/home/user/t1_si.csv', index=False)
    print('Saved t1_si.csv')

def t2():
    print('\n'+'='*70)
    print('TASK 2: FIZ acc bias before/after 24.02.2022')
    print('='*70)
    tickers = pd.read_sql("SELECT DISTINCT symbol FROM openinterest_moex ORDER BY symbol", engine)['symbol'].tolist()
    res = []
    for sym in tickers:
        df = ld(sym=[sym])
        if df.empty: continue
        fiz = df[df['clgroup']==0].copy()
        if fiz.empty: continue
        fiz['per'] = np.where(fiz['time']<'2022-02-24','b','a')
        fiz['bias'] = ((fiz['buy_accounts']-fiz['sell_accounts'])/(fiz['buy_accounts']+fiz['sell_accounts']).replace(0,np.nan)*100)
        av = fiz.groupby('per')['bias'].mean()
        bf, af = av.get('b',np.nan), av.get('a',np.nan)
        if pd.isna(bf) or pd.isna(af): continue
        ch = af - bf
        res.append({'sym':sym,'b4':round(bf,2),'af':round(af,2),'ch':round(ch,2),'big':abs(ch)>10})
    r = pd.DataFrame(res).sort_values('ch', ascending=False)
    print('Top 5 increase:')
    print(r.head(5).to_string(index=False))
    print('Top 5 decrease:')
    print(r.tail(5).to_string(index=False))
    bg = r[r['big']]
    print(f'|Change|>10 p.p.: {len(bg)}')
    for _, v in bg.iterrows():
        a = '^\u2191' if v['ch']>0 else 'v\u2193'
        print(f"  {v['sym']:>8}: {v['b4']:>7.1f}% -> {v['af']:>7.1f}% ({a}{abs(v['ch']):.1f}p.p.)")
    r.to_csv('/home/user/t2_bias.csv', index=False)
    print('Saved t2_bias.csv')

def t3():
    print('\n'+'='*70)
    print('TASK 3: Session analysis')
    print('='*70)
    df = ld(sym=['Si'])
    if df.empty: return
    fiz = df[df['clgroup']==0][['time','fiz_net']].rename(columns={'fiz_net':'fn'})
    yur = df[df['clgroup']==1][['time','fiz_net']].rename(columns={'fiz_net':'yn'})
    m = pd.merge(fiz, yur, on='time', how='inner')
    m['time'] = pd.to_datetime(m['time'])
    m['hm'] = m['time'].dt.hour*100 + m['time'].dt.minute
    m['dt'] = m['time'].dt.date
    def ss(hm):
        if 555<=hm<=1000: return 'morn'
        if 1000<hm<=1710: return 'day'
        if 1800<=hm<=2045: return 'eve'
        return 'x'
    m['sess'] = m['hm'].apply(ss)
    m = m[m['sess']!='x']
    sd = m.groupby(['dt','sess']).agg({'fn':'sum','yn':'sum'}).reset_index()
    sd['nd'] = sd['fn']-sd['yn']
    sd['r'] = np.where(sd['nd']!=0, sd['fn']/sd['nd'], np.nan)
    st = sd.groupby('sess')['r'].agg(['mean','std','count'])
    print('Si fiz_ratio by session:')
    print(st.to_string())
    print('Pairwise:')
    for a,b in combinations(['morn','day','eve'],2):
        x = sd[sd['sess']==a]['r'].dropna()
        y = sd[sd['sess']==b]['r'].dropna()
        if len(x)>0 and len(y)>0:
            print(f'  {a:<5} vs {b:<5}: {x.mean()-y.mean():.4f}')
    st.to_csv('/home/user/t3_session.csv')

def t4():
    print('\n'+'='*70)
    print('TASK 4: Jul-Nov 2023 inversion')
    print('='*70)
    tickers = pd.read_sql("SELECT DISTINCT symbol FROM openinterest_moex ORDER BY symbol", engine)['symbol'].tolist()
    res = []
    for sym in tickers:
        df = ld(sym=[sym], st='2023-01-01', en='2023-11-30')
        if df.empty: continue
        fiz = df[df['clgroup']==0].copy()
        yur = df[df['clgroup']==1].copy()
        if fiz.empty or yur.empty: continue
        m = pd.merge(fiz[['time','fiz_net']].rename(columns={'fiz_net':'fn'}),
                     yur[['time','fiz_net']].rename(columns={'fiz_net':'yn'}),
                     on='time', how='inner')
        m['time'] = pd.to_datetime(m['time'])
        m['nd'] = m['fn']-m['yn']
        m['r'] = np.where(m['nd']!=0, m['fn']/m['nd'], np.nan)
        pre = m[m['time']<'2023-07-01']
        inv = m[(m['time']>='2023-07-01')&(m['time']<'2023-11-30')]
        if pre.empty or inv.empty: continue
        pr, iv = pre['r'].mean(), inv['r'].mean()
        ps = pre['r'].std()
        ch = iv-pr
        z = ch/ps if (not pd.isna(ps) and ps!=0 and not pd.isna(ch)) else None
        res.append({'sym':sym,'pre':round(pr,4) if not pd.isna(pr) else None,
                    'inv':round(iv,4) if not pd.isna(iv) else None,
                    'ch':round(ch,4) if not pd.isna(ch) else None,
                    'z':round(z,2) if z is not None else None})
    r = pd.DataFrame(res).dropna(subset=['ch'])
    r['az'] = r['z'].abs()
    r = r.sort_values('az', ascending=False)
    print('Top 15 by |z|:')
    for _, v in r.head(15).iterrows():
        a = '^\u2191' if v['ch']>0 else 'v\u2193'
        print(f"  {v['sym']:>8}: pre={v['pre']:.3f} -> inv={v['inv']:.3f} ({a}{abs(v['ch']):.3f}, z={v['z']:+.1f})")
    cr = r[((r['pre']>0.5)&(r['inv']<0.5))|((r['pre']<0.5)&(r['inv']>0.5))]
    print(f'Crossed 0.5 ({len(cr)} tickers):')
    if not cr.empty:
        for _, v in cr.iterrows():
            d = 'FIZ->YUR' if v['pre']>0.5 and v['inv']<0.5 else 'YUR->FIZ'
            print(f"  {v['sym']:>8}: {v['pre']:.3f} -> {v['inv']:.3f} ({d})")
    st = r[r['az']>2.0]
    print(f'|z|>2.0 ({len(st)} tickers):')
    for _, v in st.iterrows():
        print(f"  {v['sym']:>8}: z={v['z']:+.1f}, pre={v['pre']:.3f}, inv={v['inv']:.3f}")
    r.to_csv('/home/user/t4_inversion.csv', index=False)
    print('Saved t4_inversion.csv')

if __name__=='__main__':
    print('MOEX OI ANALYSIS')
    print(f'Started: {datetime.now()}')
    t1(); t2(); t3(); t4()
    print('DONE')
