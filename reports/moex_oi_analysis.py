#!/usr/bin/env python3
import pandas as pd
import numpy as np
from sqlalchemy import create_engine
from datetime import datetime
from itertools import combinations

DB_URL = 'postgresql://postgres@10.0.0.60:5432/moex'
engine = create_engine(DB_URL)

def load_data(symbols=None, start='2021-01-01', end='2026-12-31'):
    where_sym = ""
    if symbols:
        sym_list = "','".join(symbols)
        where_sym = f" AND symbol IN ('{sym_list}')"
    query = f"""
    SELECT symbol, time, clgroup, buy_orders, sell_orders, buy_accounts, sell_accounts
    FROM openinterest_moex
    WHERE time >= '{start}' AND time <= '{end}'{where_sym}
    ORDER BY symbol, time, clgroup
    """
    df = pd.read_sql(query, engine)
    if df.empty:
        return df
    df['fiz_net'] = df['buy_orders'] - df['sell_orders']
    return df

def task1_si():
    print('='*70)
    print('TASK 1: Si FIZ ratio monthly analysis & structural breaks')
    print('='*70)
    df = load_data(symbols=['Si'], start='2021-01-01')
    if df.empty: return None
    fiz = df[df['clgroup']==0][['time','fiz_net']].rename(columns={'fiz_net':'fiz_net'})
    yur = df[df['clgroup']==1][['time','fiz_net']].rename(columns={'fiz_net':'yur_net'})
    m = pd.merge(fiz, yur, on='time', how='inner')
    m['time'] = pd.to_datetime(m['time'])
    m['month'] = m['time'].dt.to_period('M')
    monthly = m.groupby('month').agg({'fiz_net':'sum','yur_net':'sum'}).reset_index()
    monthly['net_diff'] = monthly['fiz_net'] - monthly['yur_net']
    monthly['fiz_ratio'] = np.where(monthly['net_diff']!=0, monthly['fiz_net']/monthly['net_diff'], np.nan)
    monthly['chg'] = monthly['fiz_ratio'].diff()
    std_ch = monthly['chg'].std()
    mean_ch = monthly['chg'].mean()
    monthly['break'] = np.abs(monthly['chg']-mean_ch) > 2*std_ch
    print(f"{'Month':<10} {'FIZ_net':>10} {'YUR_net':>10} {'Net':>12} {'FIZ_Ratio':>10} {'Break?':>12}")
    print('-'*64)
    breaks = []
    for _, r in monthly.iterrows():
        flag = ' *** BREAK ***' if r['break'] else ''
        print(f"{str(r['month']):<10} {r['fiz_net']:>10,.0f} {r['yur_net']:>10,.0f} {r['net_diff']:>12,.0f} {r['fiz_ratio']:>10.4f} {flag}")
        if r['break']: breaks.append(str(r['month']))
    print(f'\nStructural breaks (>2*sigma):')
    if breaks:
        for b in breaks: print(f'  - {b}')
    else: print('  None')
    monthly.to_csv('/home/user/task1_si_monthly_fiz_ratio.csv', index=False)
    print(f'\nSigma={std_ch:.4f}, Mean={mean_ch:.4f}')
    print(f'Saved to /home/user/task1_si_monthly_fiz_ratio.csv')
    return monthly

def task2_bias():
    print('\n'+'='*70)
    print('TASK 2: FIZ acc bias before vs after 24.02.2022')
    print('='*70)
    tickers = pd.read_sql("SELECT DISTINCT symbol FROM openinterest_moex ORDER BY symbol", engine)['symbol'].tolist()
    results = []
    for sym in tickers:
        df = load_data(symbols=[sym], start='2021-01-01')
        if df.empty: continue
        fiz = df[df['clgroup']==0].copy()
        if fiz.empty: continue
        fiz['period'] = np.where(fiz['time']<'2022-02-24','before','after')
        fiz['bias'] = ((fiz['buy_accounts']-fiz['sell_accounts'])/(fiz['buy_accounts']+fiz['sell_accounts']).replace(0,np.nan)*100)
        avg = fiz.groupby('period')['bias'].mean()
        bf, af = avg.get('before',np.nan), avg.get('after',np.nan)
        if pd.isna(bf) or pd.isna(af): continue
        chg = af - bf
        results.append({'symbol':sym,'before':round(bf,2),'after':round(af,2),'change':round(chg,2),'big':abs(chg)>10})
    res = pd.DataFrame(results).sort_values('change', ascending=False)
    print('\nTop 5 increase:')
    print(res.head(5).to_string(index=False))
    print('\nBottom 5 decrease:')
    print(res.tail(5).to_string(index=False))
    big = res[res['big']]
    print(f'\n|change| > 10 p.p.: {len(big)} tickers')
    for _, r in big.iterrows():
        arr = '^' if r['change']>0 else 'v'
        print(f"  {r['symbol']:>8}: {r['before']:>7.1f}% -> {r['after']:>7.1f}% ({arr}{abs(r['change']):.1f}p.p.)")
    res.to_csv('/home/user/task2_fiz_acc_bias_change.csv', index=False)
    print(f'\nSaved to /home/user/task2_fiz_acc_bias_change.csv')
    return res

def task3_session():
    print('\n'+'='*70)
    print('TASK 3: Session analysis')
    print('='*70)
    df = load_data(symbols=['Si'], start='2021-01-01')
    if df.empty: return None
    fiz = df[df['clgroup']==0][['time','fiz_net']].rename(columns={'fiz_net':'fiz_net'})
    yur = df[df['clgroup']==1][['time','fiz_net']].rename(columns={'fiz_net':'yur_net'})
    m = pd.merge(fiz, yur, on='time', how='inner')
    m['time'] = pd.to_datetime(m['time'])
    m['hm'] = m['time'].dt.hour*100 + m['time'].dt.minute
    m['date'] = m['time'].dt.date
    def sess(hm):
        if 555<=hm<=1000: return 'morning'
        elif 1000<hm<=1710: return 'day'
        elif 1800<=hm<=2045: return 'evening'
        else: return 'x'
    m['session'] = m['hm'].apply(sess)
    m = m[m['session']!='x']
    sd = m.groupby(['date','session']).agg({'fiz_net':'sum','yur_net':'sum'}).reset_index()
    sd['net'] = sd['fiz_net']-sd['yur_net']
    sd['ratio'] = np.where(sd['net']!=0, sd['fiz_net']/sd['net'], np.nan)
    st = sd.groupby('session')['ratio'].agg(['mean','std','count'])
    print('\nSi fiz_ratio by session:')
    print(st.to_string())
    print('\nPairwise differences:')
    for s1,s2 in combinations(['morning','day','evening'],2):
        a = sd[sd['session']==s1]['ratio'].dropna()
        b = sd[sd['session']==s2]['ratio'].dropna()
        if len(a)>0 and len(b)>0:
            print(f'  {s1:<10} vs {s2:<10}: {a.mean()-b.mean():.4f}')
    st.to_csv('/home/user/task3_session_stats.csv')
    sd.to_csv('/home/user/task3_session_daily.csv', index=False)
    print('\nSaved session stats and daily data')
    return sd

def task4_inversion():
    print('\n'+'='*70)
    print('TASK 4: July-November 2023 inversion')
    print('='*70)
    tickers = pd.read_sql("SELECT DISTINCT symbol FROM openinterest_moex ORDER BY symbol", engine)['symbol'].tolist()
    results = []
    for sym in tickers:
        df = load_data(symbols=[sym], start='2023-01-01', end='2023-11-30')
        if df.empty: continue
        fiz = df[df['clgroup']==0].copy()
        yur = df[df['clgroup']==1].copy()
        if fiz.empty or yur.empty: continue
        m = pd.merge(fiz[['time','fiz_net']].rename(columns={'fiz_net':'fiz_net'}),
                     yur[['time','fiz_net']].rename(columns={'fiz_net':'yur_net'}),
                     on='time', how='inner')
        m['time'] = pd.to_datetime(m['time'])
        m['net'] = m['fiz_net']-m['yur_net']
        m['ratio'] = np.where(m['net']!=0, m['fiz_net']/m['net'], np.nan)
        pre = m[m['time']<'2023-07-01']
        inv = m[(m['time']>='2023-07-01')&(m['time']<'2023-11-30')]
        if pre.empty or inv.empty: continue
        pr, iv = pre['ratio'].mean(), inv['ratio'].mean()
        ps = pre['ratio'].std()
        chg = iv-pr
        z = chg/ps if (not pd.isna(ps) and ps!=0 and not pd.isna(chg)) else None
        results.append({'symbol':sym,'pre_ratio':round(pr,4) if not pd.isna(pr) else None,
                        'inv_ratio':round(iv,4) if not pd.isna(iv) else None,
                        'change':round(chg,4) if not pd.isna(chg) else None,
                        'z_score':round(z,2) if z is not None else None})
    res = pd.DataFrame(results).dropna(subset=['change'])
    res['abs_z'] = res['z_score'].abs()
    res = res.sort_values('abs_z', ascending=False)
    print('\nTop 15 by |z|:')
    for _, r in res.head(15).iterrows():
        arr = '^' if r['change']>0 else 'v'
        print(f"  {r['symbol']:>8}: pre={r['pre_ratio']:.3f} -> inv={r['inv_ratio']:.3f} ({arr}{abs(r['change']):.3f}, z={r['z_score']:+.1f})")
    print('\nCrossing 0.5:')
    cross = res[((res['pre_ratio']>0.5)&(res['inv_ratio']<0.5))|((res['pre_ratio']<0.5)&(res['inv_ratio']>0.5))]
    if not cross.empty:
        for _, r in cross.iterrows():
            d = 'FIZ->YUR' if r['pre_ratio']>0.5 and r['inv_ratio']<0.5 else 'YUR->FIZ'
            print(f"  {r['symbol']:>8}: {r['pre_ratio']:.3f} -> {r['inv_ratio']:.3f} ({d})")
    else: print('  None')
    strong = res[res['abs_z']>2.0]
    print(f'\nStrong signal (|z|>2.0, {len(strong)} tickers):')
    for _, r in strong.iterrows():
        print(f"  {r['symbol']:>8}: z={r['z_score']:+.1f}")
    res.to_csv('/home/user/task4_inversion_tickers.csv', index=False)
    print('\nSaved')
    return res

if __name__=='__main__':
    print('='*70)
    print('MOEX FIZ/YUR OI STRUCTURAL BREAK ANALYSIS')
    print('='*70)
    print(f'Time: {datetime.now()}')
    t1 = task1_si()
    t2 = task2_bias()
    t3 = task3_session()
    t4 = task4_inversion()
    print('\n'+'='*70)
    print('DONE')
    print('='*70)
