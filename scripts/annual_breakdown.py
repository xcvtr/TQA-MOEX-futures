#!/usr/bin/env python3
"""
BASE v2 по годам — каждый год отдельно, 100K.
Движок тот же что в portfolio_sweep_enhancements.py (проверенный).
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
import clickhouse_connect
from bar_level_sim import TICKER_CONFIGS
from config import CH_HOST, CH_PORT, CH_DB

INITIAL_CAPITAL = 100_000
SYMBOLS = ['GL', 'HS', 'HY', 'RN', 'NM', 'AF']


def rz(s, w=20):
    m = s.rolling(w, min_periods=w).mean()
    std = s.rolling(w, min_periods=w).std()
    return (s - m) / std.clip(lower=1e-10)


def calc_atr(df, p=14):
    prev = df['close'].shift(1)
    tr = pd.concat([df['high']-df['low'],(df['high']-prev).abs(),(df['low']-prev).abs()],axis=1).max(axis=1)
    return tr.rolling(p, min_periods=p).mean().bfill().fillna(0)


def load_data(sym):
    ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)
    q=f"""
        SELECT p.time,p.open,p.high,p.low,p.close,p.volume,
               o.fiz_buy,o.fiz_sell,o.yur_buy,o.yur_sell,o.total_oi
        FROM moex.prices_5m p
        LEFT JOIN moex.prices_5m_oi o ON p.time=o.time AND p.symbol=o.symbol
        WHERE p.symbol='{sym}' AND p.time>='2022-01-01' AND p.time<='2026-05-01'
        ORDER BY p.time
    """
    r = ch.query(q)
    cols=['time','open','high','low','close','volume','fiz_buy','fiz_sell','yur_buy','yur_sell','total_oi']
    df=pd.DataFrame(r.result_rows,columns=cols)
    df['time']=pd.to_datetime(df['time']).dt.tz_localize(None)
    df.set_index('time',inplace=True)
    return df


def load_accounts(sym):
    ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)
    q=f"""
        SELECT time,clgroup,buy_accounts,sell_accounts
        FROM moex.openinterest
        WHERE symbol='{sym}' AND time>='2022-01-01' AND time<='2026-05-01'
        ORDER BY time,clgroup
    """
    r=ch.query(q)
    rows=r.result_rows
    if not rows: return pd.DataFrame()
    recs=[{'time':r[0],'clg':r[1],'buy_a':r[2],'sell_a':r[3]} for r in rows]
    df=pd.DataFrame(recs)
    df['time']=pd.to_datetime(df['time']).dt.tz_localize(None)
    fiz=df[df['clg']==0][['time','buy_a','sell_a']].rename(columns={'buy_a':'fiz_buy_a','sell_a':'fiz_sell_a'})
    yur=df[df['clg']==1][['time','buy_a','sell_a']].rename(columns={'buy_a':'yur_buy_a','sell_a':'yur_sell_a'})
    merged=pd.merge(fiz,yur,on='time',how='outer').fillna(0)
    merged.set_index('time',inplace=True)
    return merged


def precompute(df, acc_df=None):
    d=df.copy()
    d['volume']=d['volume'].astype(float)
    d['vma20']=d['volume'].rolling(20).mean().fillna(d['volume'])
    d['vr']=d['volume']/d['vma20'].clip(lower=1)
    d['vz']=rz(d['volume'],20)
    d['fiz_net']=d['fiz_buy'].fillna(0)-d['fiz_sell'].fillna(0)
    d['yur_net']=d['yur_buy'].fillna(0)-d['yur_sell'].fillna(0)
    d['oi_r']=(d['yur_buy']+d['yur_sell']).fillna(0)/(d['fiz_buy']+d['fiz_sell']+1).fillna(0)
    d['oima']=d['oi_r'].rolling(20).mean()
    d['atr14']=calc_atr(d)
    d['atr_pct']=d['atr14']/d['close'].clip(lower=1)*100
    vs=np.clip((d['vr']-1.5)/3.0,0,1)
    os_=np.clip((d['oima']-d['oi_r'])/d['oima'].clip(lower=0.1),0,1)
    raw=vs*0.6+os_*0.4
    af=np.clip(1-(d['atr_pct']-0.3)/3.0,0,1)
    score=np.clip(raw*af*np.clip(1+d['vz']/5,0.5,1.5),0,1)
    d['score']=score
    if acc_df is not None and len(acc_df)>0:
        d=d.join(acc_df,how='left').fillna(0)
        d['fiz_vol_pa']=d['fiz_net'].abs()/(d['fiz_buy_a']+d['fiz_sell_a']+1)
        d['yur_a_change']=d['yur_buy_a']-d['yur_sell_a']
        d['yur_a_z']=rz(d['yur_a_change'],20)
        d['conc']=np.clip(d['fiz_vol_pa']/1000.0,0,1)
        d['yur_conf']=np.clip(d['yur_a_z']/2.0,0,1)
        d['score_conf']=np.clip(d['score']*(1+d['conc']*0.5+d['yur_conf']*0.3),0,1)
    else:
        d['score_conf']=d['score']
    return d


def simulate(d, start, end, sym, initial=INITIAL_CAPITAL):
    """Точная копия движка из portfolio_sweep_enhancements.py"""
    mask = (d.index >= pd.Timestamp(start)) & (d.index < pd.Timestamp(end))
    dd = d[mask].copy()
    if len(dd) < 100:
        return None

    cash = float(initial)
    peak = float(initial)
    max_dd = 0.0
    trades = 0
    wins = 0
    go = TICKER_CONFIGS.get(sym, {}).get('go', 5000)
    pos = None

    for i in range(1, len(dd)):
        bar = dd.iloc[i]
        h = bar.name.hour
        if h < 7 or h >= 23:
            continue

        # Exit
        if pos is not None:
            pos['bars_left'] -= 1
            hit = False
            ep = float(bar['close'])
            if pos['dir'] == 'L' and float(bar['low']) <= pos['stop']:
                hit = True; ep = pos['stop']
            elif pos['dir'] == 'S' and float(bar['high']) >= pos['stop']:
                hit = True; ep = pos['stop']
            elif pos['bars_left'] <= 0:
                hit = True
            if hit:
                dm = 1 if pos['dir'] == 'L' else -1
                pp = dm * (ep - pos['entry']) / pos['entry']
                pr = pp * pos['go'] * pos['contracts']
                cash += pr; trades += 1
                if pr > 0: wins += 1
                pos = None

        # MTM
        if pos is not None:
            dm = 1 if pos['dir'] == 'L' else -1
            mtm = dm * (float(bar['close']) - pos['entry']) / pos['entry'] * pos['go'] * pos['contracts']
            teq = cash + mtm
        else:
            teq = cash

        if teq > peak: peak = teq
        ddv = (peak - teq) / peak if peak > 0 else 0
        if ddv > max_dd: max_dd = ddv

        if pos is not None:
            continue

        # Entry
        score = float(bar['score_conf'])
        if np.isnan(score) or score < 0.10:
            continue

        max_rub = cash * 0.50
        contracts = max(1, int(max_rub / go))
        atrv = float(bar.get('atr14', 1))
        ep = float(bar['close'])
        stop_p = ep - atrv * 1.0
        pos = {'dir': 'L', 'entry': ep, 'stop': stop_p,
               'bars_left': 8, 'go': go, 'contracts': contracts}

    if pos is not None:
        lb = dd.iloc[-1]
        dm = 1 if pos['dir'] == 'L' else -1
        pp = dm * (float(lb['close']) - pos['entry']) / pos['entry']
        pr = pp * pos['go'] * pos['contracts']
        cash += pr; trades += 1
        if pr > 0: wins += 1

    tr = (cash - initial) / initial * 100
    days = max((pd.Timestamp(end) - pd.Timestamp(start)).days, 30)
    yrs_ = days / 365.25
    cagr = ((cash / initial) ** (1 / max(yrs_, 0.1)) - 1) * 100 if cash > 0 else -100
    calmar = tr / 100 / max(max_dd, 0.001) if max_dd > 0 else tr * 10
    wr = wins / trades * 100 if trades > 0 else 0
    return {
        'ret': round(tr, 2), 'cagr': round(cagr, 2),
        'dd': round(max_dd * 100, 2), 'calmar': round(calmar, 2),
        'wr': round(wr, 2), 'trades': trades,
    }


def main():
    print("="*90)
    print("BASE v2 ПО ГОДАМ")
    print("score>0.10, bars=8, stop=1.0A, lev=0.50")
    print("Каждый год — отдельный счёт 100K ₽")
    print("="*90, flush=True)

    loaded = {}
    for sym in SYMBOLS:
        t0 = time.time()
        df = load_data(sym)
        acc = load_accounts(sym)
        loaded[sym] = precompute(df, acc)
        print(f"  {sym}: {len(loaded[sym])} баров за {time.time()-t0:.1f}s", flush=True)

    periods = [
        ('2023', '2023-01-01', '2024-01-01'),
        ('2024', '2024-01-01', '2025-01-01'),
        ('2025', '2025-01-01', '2026-01-01'),
        ('2026', '2026-01-01', '2026-05-01'),
    ]

    # По каждому тикеру
    for sym in SYMBOLS:
        d = loaded[sym]
        print(f"\n{sym}:")
        print(f"{'Год':<6}{'Ret%':>9}{'DD%':>7}{'Calmar':>8}{'WR%':>6}{'CAGR%':>8}{'Trades':>8}")
        print('-'*52)
        for pname, pstart, pend in periods:
            r = simulate(d, pstart, pend, sym)
            if r:
                print(f"{pname:<6}{r['ret']:>8.1f}%{r['dd']:>6.1f}%{r['calmar']:>8.1f}{r['wr']:>5.1f}%{r['cagr']:>7.1f}%{r['trades']:>8}")
            else:
                print(f"{pname:<6}{'N/A':>8}")
        r = simulate(d, '2024-01-01', '2026-05-01', sym)
        if r:
            print(f"{'ALL':<6}{r['ret']:>8.1f}%{r['dd']:>6.1f}%{r['calmar']:>8.1f}{r['wr']:>5.1f}%{r['cagr']:>7.1f}%{r['trades']:>8}")
        print(f"  done", flush=True)

    # Сводная
    print(f"\n{'='*90}")
    print(f"СВОДНАЯ: среднее по {len(SYMBOLS)} тикерам")
    print(f"{'='*90}")

    for metric_name, key in [('Доходность Ret%', 'ret'), ('Просадка DD%', 'dd'),
                              ('Calmar', 'calmar'), ('CAGR%', 'cagr'), ('WR%', 'wr'), ('Trades', 'trades')]:
        print(f"\n{metric_name}:")
        print(f"{'Тикер':<6}", end='')
        for pname, _, _ in periods:
            print(f"  {pname:>9}", end='')
        print(f"  {'ALL':>9}")
        print('-' * (6 + 11 * (len(periods)+1)))

        cols = {p[0]: [] for p in periods}
        cols['ALL'] = []

        for sym in SYMBOLS:
            d = loaded[sym]
            print(f"{sym:<6}", end='')
            for pname, pstart, pend in periods:
                r = simulate(d, pstart, pend, sym)
                if r:
                    v = r[key]
                    if key == 'trades':
                        print(f"  {v:>9}", end='')
                    elif key == 'calmar':
                        print(f"  {v:>9.1f}", end='')
                    else:
                        print(f"  {v:>8.1f}%", end='')
                    cols[pname].append(v)
                else:
                    print(f"  {'N/A':>9}", end='')
            r = simulate(d, '2024-01-01', '2026-05-01', sym)
            if r:
                v = r[key]
                if key == 'trades':
                    print(f"  {v:>9}", end='')
                elif key == 'calmar':
                    print(f"  {v:>9.1f}", end='')
                else:
                    print(f"  {v:>8.1f}%", end='')
                cols['ALL'].append(v)
            else:
                print(f"  {'N/A':>9}", end='')
            print()

        # Среднее
        print(f"{'СР':<6}", end='')
        for pname, _, _ in periods:
            vals = cols[pname]
            if vals:
                avg = sum(vals) / len(vals)
                if key == 'trades':
                    print(f"  {avg:>9.0f}", end='')
                elif key == 'calmar':
                    print(f"  {avg:>9.1f}", end='')
                else:
                    print(f"  {avg:>8.1f}%", end='')
            else:
                print(f"  {'N/A':>9}", end='')
        vals = cols['ALL']
        if vals:
            avg = sum(vals)/len(vals)
            if key == 'trades':
                print(f"  {avg:>9.0f}", end='')
            elif key == 'calmar':
                print(f"  {avg:>9.1f}", end='')
            else:
                print(f"  {avg:>8.1f}%", end='')
        print()


if __name__ == '__main__':
    main()
