#!/usr/bin/env python3
"""Crowd Bias Strategy Backtest — v3 с исправленными SQL запросами"""
import psycopg2
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import sys, time, warnings
warnings.filterwarnings('ignore')

DB = dict(host='10.0.0.60', dbname='moex', user='postgres')
TOP = ['Si','BR','GD','SR','NG','ED','MM','RI','SV','MX']
ALL = ['AF','AL','AU','BM','BR','CC','CE','CH','CNYRUBF','CR','DX','ED','Eu',
       'EURRUBF','FF','GAZPF','GD','GK','GL','GLDRUBF','GZ','HS','HY','IB',
       'IMOEXF','KC','LK','MC','ME','MG','MM','MN','MX','MY','NA','NG','NM',
       'NR','OJ','PD','PT','RB','RI','RL','RM','RN','SBERF','SE','SF','Si',
       'SN','SP','SR','SS','SV','TN','TT','UC','USDRUBF','VB','VI','W4','X5','YD']

WINDOW = 20  # bars (D1 = 20 days)

def load_one(sym):
    """Загрузка OI и цен для одного символа — daily aggregation в pandas"""
    conn = psycopg2.connect(**DB)
    
    # OI data
    oi = pd.read_sql("""
        SELECT date_trunc('day', time) as day,
               SUM(CASE WHEN clgroup=0 THEN buy_orders ELSE 0 END) as fiz_buy,
               SUM(CASE WHEN clgroup=0 THEN sell_orders ELSE 0 END) as fiz_sell,
               SUM(CASE WHEN clgroup=1 THEN buy_orders ELSE 0 END) as yur_buy,
               SUM(CASE WHEN clgroup=1 THEN sell_orders ELSE 0 END) as yur_sell,
               MAX(CASE WHEN clgroup=0 THEN buy_accounts ELSE 0 END) as fiz_buy_accts,
               MAX(CASE WHEN clgroup=0 THEN sell_accounts ELSE 0 END) as fiz_sell_accts
        FROM openinterest_moex
        WHERE symbol = %s AND time >= '2023-01-01'
        GROUP BY day ORDER BY day
    """, conn, params=(sym,))
    
    # Price data
    px = pd.read_sql("""
        SELECT day, first_open, high, low, last_close, volume
        FROM (
            SELECT date_trunc('day', time) as day,
                   (array_agg(open ORDER BY time))[1] as first_open,
                   MAX(high) as high,
                   MIN(low) as low,
                   (array_agg(close ORDER BY time DESC))[1] as last_close,
                   SUM(volume) as volume
            FROM moex_prices_5m
            WHERE symbol = %s AND time >= '2023-01-01'
            GROUP BY day
        ) subq ORDER BY day
    """, conn, params=(sym,))
    
    conn.close()
    
    if oi.empty or px.empty:
        return None, None
    
    oi = oi.set_index('day').fillna(0)
    oi = oi[oi['fiz_buy'] > 0]
    
    px = px.set_index('day')
    px.columns = ['open', 'high', 'low', 'close', 'volume']
    px = px.dropna(subset=['open'])
    
    return oi, px

def run_backtest(sym, tf='D1'):
    oi, px = load_one(sym)
    if oi is None or px is None or oi.empty or px.empty:
        return None
    
    df = oi.join(px[['open','high','low','close','volume']], how='inner')
    if df.empty or len(df) < 30:
        return None
    
    n = len(df)
    W = WINDOW
    hold_map = {'D1': 2, 'H4': 3, '15m': 5}
    hold = hold_map.get(tf, 2)
    
    trades = []
    signals = {}
    
    for i in range(n):
        row = df.iloc[i]
        t = df.index[i]
        
        fiz_b = row['fiz_buy']; fiz_s = row['fiz_sell']
        yur_b = row['yur_buy']; yur_s = row['yur_sell']
        fiz_ba = row['fiz_buy_accts']; fiz_sa = row['fiz_sell_accts']
        
        fiz_tot = fiz_b + fiz_s
        fac_tot = fiz_ba + fiz_sa
        yur_tot = yur_b + yur_s
        all_tot = fiz_tot + yur_tot
        
        nb = (fiz_b - fiz_s) / fiz_tot if fiz_tot > 0 else 0.0
        ab = (fiz_ba - fiz_sa) / fac_tot if fac_tot > 0 else 0.0
        fr = fiz_tot / all_tot if all_tot > 0 else 0.5
        conv = abs(fr - 0.5)
        
        d1 = d3 = d5 = 0.0
        if i >= 1:
            p1 = df.iloc[i-1]
            p1a = p1['fiz_buy_accts'] + p1['fiz_sell_accts']
            d1 = (fac_tot - p1a)/p1a if p1a > 0 else 0.0
        if i >= 3:
            p3 = df.iloc[i-3]
            p3a = p3['fiz_buy_accts'] + p3['fiz_sell_accts']
            d3 = (fac_tot - p3a)/p3a if p3a > 0 else 0.0
        if i >= 5:
            p5 = df.iloc[i-5]
            p5a = p5['fiz_buy_accts'] + p5['fiz_sell_accts']
            d5 = (fac_tot - p5a)/p5a if p5a > 0 else 0.0
        
        # Z-scores from history only
        nb_z = ab_z = 0.0
        nb_e2 = ab_e2 = 0.0
        nb_e95 = ab_e95 = 0.0
        
        if i >= W:
            nb_h, ab_h, conv_h = [], [], []
            for j in range(i-W, i):
                r = df.iloc[j]
                ft = r['fiz_buy'] + r['fiz_sell']
                if ft > 0: nb_h.append((r['fiz_buy'] - r['fiz_sell'])/ft)
                fac = r['fiz_buy_accts'] + r['fiz_sell_accts']
                if fac > 0: ab_h.append((r['fiz_buy_accts'] - r['fiz_sell_accts'])/fac)
                yt = r['yur_buy'] + r['yur_sell']
                tot = ft + yt
                if tot > 0: conv_h.append(abs(ft/tot - 0.5))
            
            nb_a = np.array(nb_h)
            if len(nb_a) > 5:
                mu, sig = np.mean(nb_a), np.std(nb_a)
                nb_z = (nb - mu)/sig if sig > 1e-10 else 0.0
                nb_e2 = 1.0 if abs(nb_z) > 2.0 else 0.0
                nb_e95 = 1.0 if abs(nb) > np.percentile(nb_a, 95) else 0.0
            
            ab_a = np.array(ab_h)
            if len(ab_a) > 5:
                mu, sig = np.mean(ab_a), np.std(ab_a)
                ab_z = (ab - mu)/sig if sig > 1e-10 else 0.0
                ab_e2 = 1.0 if abs(ab_z) > 2.0 else 0.0
                ab_e95 = 1.0 if abs(ab) > np.percentile(ab_a, 95) else 0.0
        
        # Signal
        thr = 0.5
        sig = 0
        if (nb_z < -thr or ab_z < -thr) and conv > 0.2:
            sig = 1
        elif (nb_z > thr or ab_z > thr) and conv > 0.2:
            sig = -1
        signals[t] = sig
        
        # Execute (entry on next bar's open)
        if i > 0:
            prev_t = df.index[i-1]
            psig = signals.get(prev_t, 0)
            if psig != 0:
                # Hold for 'hold' bars
                ex = min(i + hold - 1, n - 1)
                entry = row['open']
                exit_c = df.iloc[ex]['close']
                
                if entry > 0:
                    ret = (exit_c - entry) / entry
                    pnl = ret if psig == 1 else -ret
                    pnl -= 0.0002
                    trades.append({
                        'entry': t, 'exit': df.index[ex],
                        'sig': psig, 'pnl': pnl
                    })
    
    return calc_metrics(trades, signals)

def calc_metrics(trades, signals):
    if not trades:
        return None
    
    pnls = np.array([t['pnl'] for t in trades])
    wins = pnls[pnls > 0]
    losses = pnls[pnls <= 0]
    
    n = len(pnls)
    wr = len(wins)/n
    gp = sum(wins) if len(wins) > 0 else 0.0
    gl = abs(sum(losses)) if len(losses) > 0 else 1.0
    pf = gp/gl
    sh = np.mean(pnls)/np.std(pnls) if np.std(pnls) > 1e-10 else 0.0
    
    cum = np.cumsum(pnls)
    dd = cum - np.maximum.accumulate(cum)
    mdd = abs(min(dd))
    
    if n > 1:
        days = (trades[-1]['exit'] - trades[0]['entry']).days
        spm = n / max(days/30.44, 1)
    else:
        spm = 0.0
    
    ns = sum(1 for s in signals.values() if s != 0)
    
    return {
        'n': n, 'wr': wr, 'pf': pf, 'sh': sh, 'mdd': mdd,
        'spm': spm, 'ns': ns, 'avg_pnl': float(np.mean(pnls)),
        'best': float(max(pnls)), 'worst': float(min(pnls))
    }

def fmt(r):
    if r is None:
        return "NO DATA"
    return (f"WR={r['wr']:.1%} PF={r['pf']:.2f} "
            f"Sh={r['sh']:.3f} DD={r['mdd']:.4f} "
            f"N={r['n']} SPM={r['spm']:.1f}")

def test_top(tf='D1'):
    print(f"\n=== ТФ: {tf} ===")
    res = []
    for sym in TOP:
        t0 = time.time()
        r = run_backtest(sym, tf)
        dt = time.time()-t0
        r_str = fmt(r) if r else "NO SIGNALS"
        print(f"  {sym:6s} ({dt:.1f}s) → {r_str}")
        if r: res.append((sym, r))
    return res

def test_all():
    print(f"\n=== ВСЕ 64 ТИКЕРА (D1) ===")
    all_r = []
    for i, sym in enumerate(ALL):
        sys.stdout.write(f"\r  [{i+1}/{len(ALL)}] {sym:8s}...")
        sys.stdout.flush()
        r = run_backtest(sym, 'D1')
        if r: all_r.append((sym, r))
    print()
    
    print(f"\n  Сигналы на {len(all_r)}/{len(ALL)} тикерах")
    
    by_sh = sorted(all_r, key=lambda x: x[1]['sh'], reverse=True)
    print(f"\n  ⋆ ТОП-10 по Sharpe:")
    for sym, r in by_sh[:10]:
        print(f"    {sym:8s}: {fmt(r)}")
    print(f"\n  ⋆ Bottom-10 по Sharpe:")
    for sym, r in by_sh[-10:]:
        print(f"    {sym:8s}: {fmt(r)}")
    
    by_wr = sorted(all_r, key=lambda x: x[1]['wr'], reverse=True)
    print(f"\n  ⋆ ТОП-10 по Win Rate:")
    for sym, r in by_wr[:10]:
        print(f"    {sym:8s}: {fmt(r)}")
    
    by_pf = sorted(all_r, key=lambda x: x[1]['pf'], reverse=True)
    print(f"\n  ⋆ ТОП-10 по Profit Factor:")
    for sym, r in by_pf[:10]:
        print(f"    {sym:8s}: {fmt(r)}")
    
    # Average metrics
    avg_wr = np.mean([r['wr'] for _,r in all_r])
    avg_pf = np.mean([r['pf'] for _,r in all_r])
    avg_sh = np.mean([r['sh'] for _,r in all_r])
    avg_n = np.mean([r['n'] for _,r in all_r])
    tot_n = sum(r['n'] for _,r in all_r)
    print(f"\n  СРЕДНЕЕ по {len(all_r)} тикерам:")
    print(f"    WR={avg_wr:.1%} PF={avg_pf:.2f} Sharpe={avg_sh:.3f} Trades={avg_n:.0f} Всего сделок={tot_n}")

def thresh_scan():
    print(f"\n=== СКАНИРОВАНИЕ ПОРОГОВ (D1) ===")
    # Quick scan using modified signal condition
    for thr in [0.3, 0.5, 0.7, 1.0, 1.3, 1.5, 2.0]:
        # Re-run for a subset
        syms = TOP[:5]
        rs = []
        for sym in syms:
            # Modify function locally
            oi, px = load_one(sym)
            if oi is None or px is None: continue
            df = oi.join(px[['open','high','low','close','volume']], how='inner')
            if df.empty or len(df) < 30: continue
            
            n = len(df); W = 20
            trades = []; signals = {}
            
            for i in range(n):
                row = df.iloc[i]; t = df.index[i]
                fiz_b=row['fiz_buy']; fiz_s=row['fiz_sell']
                fiz_ba=row['fiz_buy_accts']; fiz_sa=row['fiz_sell_accts']
                fiz_tot=fiz_b+fiz_s; fac_tot=fiz_ba+fiz_sa
                yur_tot=row['yur_buy']+row['yur_sell']
                all_tot=fiz_tot+yur_tot
                
                nb = (fiz_b-fiz_s)/fiz_tot if fiz_tot>0 else 0.0
                ab = (fiz_ba-fiz_sa)/fac_tot if fac_tot>0 else 0.0
                conv = abs(fiz_tot/all_tot-0.5) if all_tot>0 else 0.5
                
                nb_z=ab_z=0.0
                if i>=W:
                    nb_h, ab_h = [], []
                    for j in range(i-W,i):
                        r=df.iloc[j]
                        ft=r['fiz_buy']+r['fiz_sell']
                        if ft>0: nb_h.append((r['fiz_buy']-r['fiz_sell'])/ft)
                        fac=r['fiz_buy_accts']+r['fiz_sell_accts']
                        if fac>0: ab_h.append((r['fiz_buy_accts']-r['fiz_sell_accts'])/fac)
                    nb_a=np.array(nb_h)
                    if len(nb_a)>5:
                        mu,sig=np.mean(nb_a),np.std(nb_a)
                        nb_z=(nb-mu)/sig if sig>1e-10 else 0.0
                    ab_a=np.array(ab_h)
                    if len(ab_a)>5:
                        mu,sig=np.mean(ab_a),np.std(ab_a)
                        ab_z=(ab-mu)/sig if sig>1e-10 else 0.0
                
                sig=0
                if (nb_z<-thr or ab_z<-thr) and conv>0.2: sig=1
                elif (nb_z>thr or ab_z>thr) and conv>0.2: sig=-1
                signals[t]=sig
                
                if i>0:
                    psig=signals.get(df.index[i-1],0)
                    if psig!=0:
                        ex_i=min(i+1,n-1)
                        entry=row['open']
                        exit_c=df.iloc[ex_i]['close']
                        if entry>0:
                            ret=(exit_c-entry)/entry
                            pnl=ret if psig==1 else -ret
                            pnl-=0.0002
                            trades.append({'pnl':pnl})
            
            if trades:
                pnls=[t['pnl'] for t in trades]
                rs.append(np.mean(pnls)/np.std(pnls) if np.std(pnls)>1e-10 else 0)
        
        if rs:
            avg_sh = np.mean(rs)
            print(f"  thr={thr:.1f}: mean Sharpe={avg_sh:.3f}")

def feature_importance():
    print(f"\n=== АНАЛИЗ ФИЧЕЙ (какие сигналы работают) ===")
    for sym in TOP[:5]:
        oi, px = load_one(sym)
        if oi is None or px is None: continue
        df = oi.join(px[['open','high','low','close','volume']], how='inner')
        if df.empty or len(df)<30: continue
        
        n=len(df); W=20
        stats = {'nb_bear_long':0,'nb_bull_short':0,'ab_bear_long':0,'ab_bull_short':0,
                 'conv_high':0,'conv_low':0,'extreme_nb':0,'extreme_ab':0,
                 'nb_bear_long_w':0,'nb_bull_short_w':0,'ab_bear_long_w':0,'ab_bull_short_w':0}
        
        for i in range(W, n-2):
            row=df.iloc[i]
            nxt=df.iloc[i+2]['close']
            opn=row['open']
            
            fiz_b=row['fiz_buy']; fiz_s=row['fiz_sell']
            fiz_ba=row['fiz_buy_accts']; fiz_sa=row['fiz_sell_accts']
            fiz_tot=fiz_b+fiz_s; fac_tot=fiz_ba+fiz_sa
            yur_tot=row['yur_buy']+row['yur_sell']
            all_tot=fiz_tot+yur_tot
            
            nb=(fiz_b-fiz_s)/fiz_tot if fiz_tot>0 else 0.0
            ab=(fiz_ba-fiz_sa)/fac_tot if fac_tot>0 else 0.0
            conv=abs(fiz_tot/all_tot-0.5) if all_tot>0 else 0.5
            
            nb_h, ab_h = [], []
            for j in range(i-W,i):
                r=df.iloc[j]
                ft=r['fiz_buy']+r['fiz_sell']
                if ft>0: nb_h.append((r['fiz_buy']-r['fiz_sell'])/ft)
                fac=r['fiz_buy_accts']+r['fiz_sell_accts']
                if fac>0: ab_h.append((r['fiz_buy_accts']-r['fiz_sell_accts'])/fac)
            
            ret = (nxt-opn)/opn if opn>0 else 0
            direction = 1 if ret>0 else (-1 if ret<0 else 0)
            
            nb_a=np.array(nb_h)
            ab_a=np.array(ab_h)
            
            deltas = {1:0,3:0,5:0}
            for db in [1,3,5]:
                if i>=db:
                    p=df.iloc[i-db]
                    p_fac=p['fiz_buy_accts']+p['fiz_sell_accts']
                    deltas[db]=(fac_tot-p_fac)/p_fac if p_fac>0 else 0.0
            
            # NB bearish (crowd short) → should go LONG
            if len(nb_a)>5:
                mu, sig = np.mean(nb_a), np.std(nb_a)
                nb_z = (nb-mu)/sig if sig>1e-10 else 0.0
                
                if nb_z<-0.5 and conv>0.2:
                    stats['nb_bear_long']+=1
                    if direction==1: stats['nb_bear_long_w']+=1
                if nb_z>0.5 and conv>0.2:
                    stats['nb_bull_short']+=1
                    if direction==-1: stats['nb_bull_short_w']+=1
                if abs(nb_z)>2:
                    stats['extreme_nb']+=1
            
            if len(ab_a)>5:
                mu, sig = np.mean(ab_a), np.std(ab_a)
                ab_z = (ab-mu)/sig if sig>1e-10 else 0.0
                
                if ab_z<-0.5 and conv>0.2:
                    stats['ab_bear_long']+=1
                    if direction==1: stats['ab_bear_long_w']+=1
                if ab_z>0.5 and conv>0.2:
                    stats['ab_bull_short']+=1
                    if direction==-1: stats['ab_bull_short_w']+=1
                if abs(ab_z)>2:
                    stats['extreme_ab']+=1
        
        print(f"  {sym}:")
        for k, v in stats.items():
            if 'w' in k:  # win count
                base = k.replace('_w','')
                total = stats.get(base, 0)
                if total>0:
                    print(f"    {base:20s}: win {v}/{total} = {v/total:.1%}")

if __name__ == '__main__':
    t0=time.time()
    print("="*65)
    print("CROWD BIAS STRATEGY — MOEX BACKTEST")
    print("No look-ahead, entry on next bar open")
    print("="*65)
    
    # 1. D1
    r1 = test_top('D1')
    if r1:
        avg = {k: np.mean([r[1][k] for r in r1]) for k in ['wr','pf','sh','n','spm']}
        print(f"  {'СРЕДНЕЕ':6s}: WR={avg['wr']:.1%} PF={avg['pf']:.2f} "
              f"Sharpe={avg['sh']:.3f} N={avg['n']:.0f} SPM={avg['spm']:.1f}")
    
    # 2. H4
    r2 = test_top('H4')
    if r2:
        avg = {k: np.mean([r[1][k] for r in r2]) for k in ['wr','pf','sh','n','spm']}
        print(f"  {'СРЕДНЕЕ':6s}: WR={avg['wr']:.1%} PF={avg['pf']:.2f} "
              f"Sharpe={avg['sh']:.3f} N={avg['n']:.0f} SPM={avg['spm']:.1f}")
    
    # 3. Feature importance
    feature_importance()
    
    # 4. All 64
    test_all()
    
    # 5. Threshold scan
    thresh_scan()
    
    print(f"\n{'='*65}")
    print(f"ВРЕМЯ: {time.time()-t0:.0f}s")
    print(f"{'='*65}")
