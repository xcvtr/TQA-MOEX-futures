#!/usr/bin/env python3
"""Crowd Bias Strategy Backtest — оптимизированная версия"""
import psycopg2
import psycopg2.extras
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

WINDOW = {'15m': 1520, 'H4': 120, 'D1': 20}
HOLD = {'15m': 5, 'H4': 3, 'D1': 2}

def load_one(sym):
    """Загрузка OI и цен для одного символа"""
    conn = psycopg2.connect(**DB)
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    # OI data - aggregate to daily immediately
    cur.execute("""
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
    """, (sym,))
    oi_rows = cur.fetchall()
    if not oi_rows:
        cur.close(); conn.close()
        return None, None
    
    oi = pd.DataFrame(oi_rows, columns=['day','fiz_buy','fiz_sell','yur_buy','yur_sell','fiz_buy_accts','fiz_sell_accts'])
    oi = oi.set_index('day')
    oi = oi[oi['fiz_buy'] > 0]
    
    # Price data - daily from moex_prices_5m
    cur.execute("""
        SELECT date_trunc('day', time) as day,
               FIRST(open ORDER BY time) as open,
               MAX(high) as high,
               MIN(low) as low,
               LAST(close ORDER BY time) as close,
               SUM(volume) as volume
        FROM moex_prices_5m
        WHERE symbol = %s AND time >= '2023-01-01'
        GROUP BY day ORDER BY day
    """, (sym,))
    px_rows = cur.fetchall()
    cur.close(); conn.close()
    
    if not px_rows:
        return None, None
    
    px = pd.DataFrame(px_rows, columns=['day','open','high','low','close','volume'])
    px = px.set_index('day')
    px = px.dropna(subset=['open'])
    
    return oi, px

def compute(oi, px, tf='D1'):
    """Вычисление фич и бэктест"""
    if oi is None or px is None or oi.empty or px.empty:
        return None
    
    df = oi.join(px[['open','high','low','close','volume']], how='inner')
    if df.empty or len(df) < 30:
        return None
    
    n = len(df)
    W = WINDOW[tf]
    hold = HOLD[tf]
    
    trades = []
    signals = {}
    
    for i in range(n):
        row = df.iloc[i]
        t = df.index[i]
        
        fiz_tot = row['fiz_buy'] + row['fiz_sell']
        fac_tot = row['fiz_buy_accts'] + row['fiz_sell_accts']
        yur_tot = row['yur_buy'] + row['yur_sell']
        all_tot = fiz_tot + yur_tot
        
        nb = (row['fiz_buy'] - row['fiz_sell']) / fiz_tot if fiz_tot > 0 else 0.0
        ab = (row['fiz_buy_accts'] - row['fiz_sell_accts']) / fac_tot if fac_tot > 0 else 0.0
        fr = fiz_tot / all_tot if all_tot > 0 else 0.5
        conv = abs(fr - 0.5)
        
        # Deltas
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
        nb_z = ab_z = conv_z = 0.0
        nb_e2 = ab_e2 = conv_e2 = 0.0
        nb_e95 = ab_e95 = conv_e95 = 0.0
        
        if i >= W:
            hist = df.iloc[i-W:i]
            nb_hist = []
            ab_hist = []
            conv_hist = []
            
            for j in range(i-W, i):
                r = df.iloc[j]
                ft = r['fiz_buy'] + r['fiz_sell']
                if ft > 0:
                    nb_hist.append((r['fiz_buy'] - r['fiz_sell'])/ft)
                fac = r['fiz_buy_accts'] + r['fiz_sell_accts']
                if fac > 0:
                    ab_hist.append((r['fiz_buy_accts'] - r['fiz_sell_accts'])/fac)
                yt = r['yur_buy'] + r['yur_sell']
                tot = ft + yt
                if tot > 0:
                    conv_hist.append(abs(ft/tot - 0.5))
            
            for arr, key, val in [(nb_hist, 'nb', nb), (ab_hist, 'ab', ab), (conv_hist, 'conv', conv)]:
                arr_np = np.array(arr)
                if len(arr_np) > 5:
                    mu = np.mean(arr_np)
                    sig = np.std(arr_np)
                    z = (val - mu)/sig if sig > 1e-10 else 0.0
                    if key == 'nb': nb_z = z
                    elif key == 'ab': ab_z = z
                    else: conv_z = z
                    
                    e2 = 1.0 if abs(z) > 2.0 else 0.0
                    p95 = np.percentile(arr_np, 95)
                    e95 = 1.0 if abs(val) > p95 else 0.0
                    if key == 'nb': nb_e2, nb_e95 = e2, e95
                    elif key == 'ab': ab_e2, ab_e95 = e2, e95
                    else: conv_e2, conv_e95 = e2, e95
        
        # Signal generation
        sig = 0
        thr = 0.5
        if (nb_z < -thr or ab_z < -thr) and conv > 0.2:
            sig = 1  # LONG (FIZ oversold)
        elif (nb_z > thr or ab_z > thr) and conv > 0.2:
            sig = -1  # SHORT (FIZ overbought)
        
        signals[t] = sig
        
        # Trade execution: entry at next bar's open
        if i > 0:
            prev_t = df.index[i-1]
            prev_sig = signals.get(prev_t, 0)
            if prev_sig != 0 and i + hold - 1 < n:
                entry_open = row['open']
                exit_idx = min(i + hold - 1, n - 1)
                exit_close = df.iloc[exit_idx]['close']
                
                if entry_open > 0:
                    ret = (exit_close - entry_open) / entry_open
                    pnl = ret if prev_sig == 1 else -ret
                    pnl -= 0.0002  # комиссия
                    
                    trades.append({
                        'entry_time': t,
                        'exit_time': df.index[exit_idx],
                        'signal': prev_sig,
                        'entry': entry_open,
                        'exit': exit_close,
                        'pnl': pnl
                    })
    
    if not trades:
        return None
    
    pnls = [t['pnl'] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    
    n_t = len(trades)
    wr = len(wins)/n_t if n_t > 0 else 0
    gp = sum(wins) if wins else 0
    gl = abs(sum(losses)) if losses else 1
    pf = gp/gl if gl > 0 else 0
    sh = np.mean(pnls)/np.std(pnls) if np.std(pnls) > 0 else 0
    
    cum = np.cumsum(pnls)
    dd = cum - np.maximum.accumulate(cum)
    mdd = abs(min(dd)) if len(dd) > 0 else 0
    
    if n_t > 1:
        days = (trades[-1]['exit_time'] - trades[0]['entry_time']).days
        spm = n_t / max(days/30.44, 1)
    else:
        spm = 0
    
    n_sigs = sum(1 for s in signals.values() if s != 0)
    
    return {
        'n_trades': n_t,
        'win_rate': wr,
        'profit_factor': pf,
        'sharpe': sh,
        'max_dd': mdd,
        'sig_per_month': spm,
        'tot_sigs': n_sigs,
        'avg_pnl': np.mean(pnls),
        'max_win': max(pnls) if pnls else 0,
        'max_loss': min(pnls) if pnls else 0
    }

def test_top(tf='D1'):
    print(f"\n=== ТФ: {tf} ===")
    results = []
    for sym in TOP:
        t0 = time.time()
        oi, px = load_one(sym)
        r = compute(oi, px, tf)
        dt = time.time() - t0
        if r:
            results.append((sym, r))
            print(f"  {sym:6s} dt={dt:.1f}s | WR={r['win_rate']:.1%} PF={r['profit_factor']:.2f} "
                  f"Sharpe={r['sharpe']:.3f} DD={r['max_dd']:.4f} "
                  f"Trades={r['n_trades']} SPM={r['sig_per_month']:.1f}")
        else:
            print(f"  {sym:6s} dt={dt:.1f}s | NO SIGNALS / NO DATA")
    return results

def test_all():
    print(f"\n=== ВСЕ 64 ТИКЕРА (D1) ===")
    all_r = []
    for sym in ALL:
        oi, px = load_one(sym)
        r = compute(oi, px, 'D1')
        if r and r['n_trades'] > 0:
            all_r.append((sym, r))
    
    all_r.sort(key=lambda x: x[1]['sharpe'], reverse=True)
    print(f"  Сигналы на {len(all_r)}/{len(ALL)} тикерах")
    
    print(f"\n  ТОП-10 по Sharpe:")
    for sym, r in all_r[:10]:
        print(f"    {sym:8s}: WR={r['win_rate']:.1%} PF={r['profit_factor']:.2f} "
              f"Sharpe={r['sharpe']:.3f} Trades={r['n_trades']}")
    
    print(f"\n  Bottom-10 по Sharpe:")
    for sym, r in all_r[-10:]:
        print(f"    {sym:8s}: WR={r['win_rate']:.1%} PF={r['profit_factor']:.2f} "
              f"Sharpe={r['sharpe']:.3f} Trades={r['n_trades']}")
    
    print(f"\n  ТОП-10 по Win Rate:")
    for sym, r in sorted(all_r, key=lambda x: x[1]['win_rate'], reverse=True)[:10]:
        print(f"    {sym:8s}: WR={r['win_rate']:.1%} PF={r['profit_factor']:.2f} "
              f"Sharpe={r['sharpe']:.3f} Trades={r['n_trades']}")
    
    print(f"\n  ТОП-10 по Profit Factor:")
    for sym, r in sorted(all_r, key=lambda x: x[1]['profit_factor'], reverse=True)[:10]:
        print(f"    {sym:8s}: WR={r['win_rate']:.1%} PF={r['profit_factor']:.2f} "
              f"Sharpe={r['sharpe']:.3f} Trades={r['n_trades']}")

def threshold_scan():
    print(f"\n=== СКАНИРОВАНИЕ ПОРОГОВ ===")
    for tf in ['D1', 'H4']:
        for th in [0.3, 0.5, 0.7, 1.0, 1.3, 1.5, 2.0]:
            # Need to modify compute for threshold... let me just use the first few
            pass  # will do inline

def feature_analysis():
    print(f"\n=== АНАЛИЗ ФИЧЕЙ ===")
    print("  Оценка предсказательной силы каждого сигнала на D1")
    
    for sym in TOP[:5]:
        oi, px = load_one(sym)
        if oi is None or px is None: continue
        df = oi.join(px[['open','high','low','close','volume']], how='inner')
        if df.empty or len(df) < 30: continue
        
        n = len(df)
        W = 20
        
        results = {
            'nb_low_high': {'total': 0, 'win': 0},
            'ab_low_high': {'total': 0, 'win': 0},
            'conv_high': {'total': 0, 'win': 0},
            'delta1_up': {'total': 0, 'win': 0},
            'extreme_nb': {'total': 0, 'win': 0},
            'extreme_ab': {'total': 0, 'win': 0},
        }
        
        for i in range(W, n-2):
            row = df.iloc[i]
            t = df.index[i]
            next_close = df.iloc[i+2]['close']
            curr_open = row['open']
            
            fiz_tot = row['fiz_buy'] + row['fiz_sell']
            fac_tot = row['fiz_buy_accts'] + row['fiz_sell_accts']
            yur_tot = row['yur_buy'] + row['yur_sell']
            all_tot = fiz_tot + yur_tot
            
            nb = (row['fiz_buy'] - row['fiz_sell']) / fiz_tot if fiz_tot > 0 else 0.0
            ab = (row['fiz_buy_accts'] - row['fiz_sell_accts']) / fac_tot if fac_tot > 0 else 0.0
            fr = fiz_tot / all_tot if all_tot > 0 else 0.5
            conv = abs(fr - 0.5)
            
            hist = df.iloc[i-W:i]
            
            def hist_vals(field):
                vals = []
                for j in range(i-W, i):
                    r = df.iloc[j]
                    ft = r['fiz_buy'] + r['fiz_sell']
                    if ft > 0:
                        vals.append((r['fiz_buy'] - r['fiz_sell'])/ft)
                return np.array(vals)
            
            nb_h = hist_vals('nb')
            if len(nb_h) > 5:
                nb_mu, nb_sig = np.mean(nb_h), np.std(nb_h)
                nb_z = (nb - nb_mu)/nb_sig if nb_sig > 1e-10 else 0
                
                ret = (next_close - curr_open)/curr_open if curr_open > 0 else 0
                direction = 1 if ret > 0 else (-1 if ret < 0 else 0)
                
                # NB low z-score (FIZ bearish) -> LONG
                if nb_z < -0.5 and conv > 0.2:
                    results['nb_low_high']['total'] += 1
                    if direction == 1:
                        results['nb_low_high']['win'] += 1
                
                # NB high z-score (FIZ bullish) -> SHORT
                if nb_z > 0.5 and conv > 0.2:
                    results['nb_low_high']['total'] += 1
                    if direction == -1:
                        results['nb_low_high']['win'] += 1
                
                # Extreme
                if abs(nb_z) > 2:
                    results['extreme_nb']['total'] += 1
                    if (nb_z < -2 and direction == 1) or (nb_z > 2 and direction == -1):
                        results['extreme_nb']['win'] += 1
            
            # AB
            ab_h = []
            for j in range(i-W, i):
                r = df.iloc[j]
                fac = r['fiz_buy_accts'] + r['fiz_sell_accts']
                if fac > 0:
                    ab_h.append((r['fiz_buy_accts'] - r['fiz_sell_accts'])/fac)
            ab_h = np.array(ab_h)
            if len(ab_h) > 5:
                ab_mu, ab_sig = np.mean(ab_h), np.std(ab_h)
                ab_z = (ab - ab_mu)/ab_sig if ab_sig > 1e-10 else 0
                
                ret = (next_close - curr_open)/curr_open if curr_open > 0 else 0
                direction = 1 if ret > 0 else (-1 if ret < 0 else 0)
                
                if ab_z < -0.5 and conv > 0.2:
                    results['ab_low_high']['total'] += 1
                    if direction == 1:
                        results['ab_low_high']['win'] += 1
                
                if ab_z > 0.5 and conv > 0.2:
                    results['ab_low_high']['total'] += 1
                    if direction == -1:
                        results['ab_low_high']['win'] += 1
                
                if abs(ab_z) > 2:
                    results['extreme_ab']['total'] += 1
                    if (ab_z < -2 and direction == 1) or (ab_z > 2 and direction == -1):
                        results['extreme_ab']['win'] += 1
        
        print(f"  {sym}:")
        for k, v in results.items():
            if v['total'] > 0:
                wr = v['win']/v['total']
                print(f"    {k:20s}: {v['win']:3d}/{v['total']:3d} WR={wr:.1%}")
            else:
                print(f"    {k:20s}: 0 signals")

if __name__ == '__main__':
    t_start = time.time()
    print("="*65)
    print("CROWD BIAS STRATEGY BACKTEST — MOEX (без look-ahead)")
    print(f"Данные: 2023-01 до ~2026-06, 64 тикера")
    print("="*65)
    
    # 1. Top-10 на D1
    r_d1 = test_top('D1')
    if r_d1:
        avg = {k: np.mean([r[1][k] for r in r_d1]) for k in ['win_rate','profit_factor','sharpe','n_trades','sig_per_month']}
        print(f"  {'СРЕДНЕЕ':6s}: WR={avg['win_rate']:.1%} PF={avg['profit_factor']:.2f} "
              f"Sharpe={avg['sharpe']:.3f} Trades={avg['n_trades']:.0f} SPM={avg['sig_per_month']:.1f}")
    
    # 2. H4
    r_h4 = test_top('H4')
    if r_h4:
        avg = {k: np.mean([r[1][k] for r in r_h4]) for k in ['win_rate','profit_factor','sharpe','n_trades','sig_per_month']}
        print(f"  {'СРЕДНЕЕ':6s}: WR={avg['win_rate']:.1%} PF={avg['profit_factor']:.2f} "
              f"Sharpe={avg['sharpe']:.3f} Trades={avg['n_trades']:.0f} SPM={avg['sig_per_month']:.1f}")
    
    # 3. 15m (выборочно, 2-3 тикера из-за времени)
    print(f"\n=== ТФ: 15m ===")
    for sym in TOP[:3]:
        t0 = time.time()
        oi, px = load_one(sym)
        r = compute(oi, px, '15m')
        dt = time.time() - t0
        if r:
            print(f"  {sym:6s} dt={dt:.1f}s | WR={r['win_rate']:.1%} PF={r['profit_factor']:.2f} "
                  f"Sharpe={r['sharpe']:.3f} Trades={r['n_trades']} SPM={r['sig_per_month']:.1f}")
        else:
            print(f"  {sym:6s} dt={dt:.1f}s | NO SIGNALS")
    
    # 4. Feature analysis
    feature_analysis()
    
    # 5. All 64 tickers
    test_all()
    
    elapsed = time.time() - t_start
    print(f"\n{'='*65}")
    print(f"ВСЕГО ВРЕМЕНИ: {elapsed:.0f}s ({elapsed/60:.1f} мин)")
    print(f"{'='*65}")
