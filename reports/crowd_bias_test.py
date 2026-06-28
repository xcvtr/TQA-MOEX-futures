#!/usr/bin/env python3
"""
Crowd Bias Strategy Backtest — MOEX данные, без look-ahead
Фичи (все считаются без look-ahead):
  - FIZ net bias: (fiz_buy - fiz_sell) / (fiz_buy + fiz_sell)
  - FIZ accounts bias: (fiz_buy_accts - fiz_sell_accts) / (fiz_buy_accts + fiz_sell_accts)
  - FIZ/YUR convergence: abs(fiz_ratio - 0.5)
  - Delta FIZ accounts (1d, 3d, 5d change)
  - FIZ z-score (20d window)
  - FIZ extreme: >2 sigma OR >95 percentile

Таргет: direction(close[t+n] - close[t]) для n=1..5 баров
ТФ: D1, H4, 15m
"""

import psycopg2
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings('ignore')

DB_HOST = '10.0.0.60'
DB_NAME = 'moex'
DB_USER = 'postgres'

TOP_SYMBOLS = ['Si', 'BR', 'GD', 'SR', 'NG', 'ED', 'MM', 'RI', 'SV', 'MX']

ALL_SYMBOLS = [
    'AF','AL','AU','BM','BR','CC','CE','CH','CNYRUBF','CR','DX','ED','Eu',
    'EURRUBF','FF','GAZPF','GD','GK','GL','GLDRUBF','GZ','HS','HY','IB',
    'IMOEXF','KC','LK','MC','ME','MG','MM','MN','MX','MY','NA','NG','NM',
    'NR','OJ','PD','PT','RB','RI','RL','RM','RN','SBERF','SE','SF','Si',
    'SN','SP','SR','SS','SV','TN','TT','UC','USDRUBF','VB','VI','W4','X5','YD'
]

SVO_DATE = datetime(2022, 2, 24)

def load_data(symbol):
    conn = psycopg2.connect(host=DB_HOST, dbname=DB_NAME, user=DB_USER)
    
    oi = pd.read_sql("""
        SELECT time, buy_orders, sell_orders, buy_accounts, sell_accounts, clgroup
        FROM openinterest_moex WHERE symbol = %s ORDER BY time
    """, conn, params=(symbol,))
    
    prices = pd.read_sql("""
        SELECT time, open, high, low, close, volume
        FROM moex_prices_5m WHERE symbol = %s ORDER BY time
    """, conn, params=(symbol,))
    
    conn.close()
    return oi, prices

def resample(oi_df, price_df, tf):
    rule_map = {'15m': '15T', 'H4': '4H', 'D1': 'D'}
    rule = rule_map[tf]
    
    if oi_df is not None and not oi_df.empty:
        fiz = oi_df[oi_df['clgroup']==0].copy().set_index('time')
        yur = oi_df[oi_df['clgroup']==1].copy().set_index('time')
        
        oir = pd.DataFrame()
        oir['fiz_buy'] = fiz['buy_orders'].resample(rule).sum()
        oir['fiz_sell'] = fiz['sell_orders'].resample(rule).sum()
        oir['fiz_buy_accts'] = fiz['buy_accounts'].resample(rule).last()
        oir['fiz_sell_accts'] = fiz['sell_accounts'].resample(rule).last()
        oir['yur_buy'] = yur['buy_orders'].resample(rule).sum()
        oir['yur_sell'] = yur['sell_orders'].resample(rule).sum()
        oir['yur_buy_accts'] = yur['buy_accounts'].resample(rule).last()
        oir['yur_sell_accts'] = yur['sell_accounts'].resample(rule).last()
        oir = oir.fillna(0)
        oir = oir[oir['fiz_buy'] > 0]
    else:
        oir = None
    
    if price_df is not None and not price_df.empty:
        p = price_df.set_index('time')
        pr = p.resample(rule).agg({
            'open': 'first', 'high': 'max', 'low': 'min',
            'close': 'last', 'volume': 'sum'
        }).dropna(subset=['open'])
    else:
        pr = None
    
    return oir, pr

def compute_features(oi, px, tf):
    if oi is None or px is None or oi.empty or px.empty:
        return None
    
    df = oi.join(px[['open','high','low','close','volume']], how='inner')
    if df.empty:
        return None
    
    # Window for z-score
    W = {'15m': 20*76, 'H4': 20*6, 'D1': 20}.get(tf, 20)
    
    feat_list = []
    n = len(df)
    
    for i in range(n):
        row = df.iloc[i]
        f = {}
        t = df.index[i]
        f['time'] = t
        f['open'] = row['open']
        f['close'] = row['close']
        f['high'] = row['high']
        f['low'] = row['low']
        f['volume'] = row['volume']
        
        fiz_total = row['fiz_buy'] + row['fiz_sell']
        fiz_accts_total = row['fiz_buy_accts'] + row['fiz_sell_accts']
        yur_total = row['yur_buy'] + row['yur_sell']
        total_oi = fiz_total + yur_total
        
        # 1. FIZ net bias
        f['fiz_net_bias'] = (row['fiz_buy'] - row['fiz_sell']) / fiz_total if fiz_total > 0 else 0.0
        
        # 2. FIZ accounts bias
        f['fiz_accts_bias'] = (row['fiz_buy_accts'] - row['fiz_sell_accts']) / fiz_accts_total if fiz_accts_total > 0 else 0.0
        
        # 3. FIZ/YUR convergence
        fiz_ratio = fiz_total / total_oi if total_oi > 0 else 0.5
        f['fiz_yur_convergence'] = abs(fiz_ratio - 0.5)
        
        # 5. Delta FIZ accounts
        for db in [1, 3, 5]:
            if i >= db:
                prev = df.iloc[i - db]
                p_accts = prev['fiz_buy_accts'] + prev['fiz_sell_accts']
                c_accts = row['fiz_buy_accts'] + row['fiz_sell_accts']
                f[f'delta_fiz_accts_{db}d'] = (c_accts - p_accts) / p_accts if p_accts > 0 else 0.0
            else:
                f[f'delta_fiz_accts_{db}d'] = 0.0
        
        # 4. Z-score and extreme (ONLY from history window, not including current)
        if i >= W:
            hist_means = {'fiz_net_bias': [], 'fiz_accts_bias': [], 'fiz_yur_convergence': []}
            for j in range(i-W, i):
                r = df.iloc[j]
                ft = r['fiz_buy'] + r['fiz_sell']
                if ft > 0:
                    hist_means['fiz_net_bias'].append((r['fiz_buy'] - r['fiz_sell']) / ft)
                fac = r['fiz_buy_accts'] + r['fiz_sell_accts']
                if fac > 0:
                    hist_means['fiz_accts_bias'].append((r['fiz_buy_accts'] - r['fiz_sell_accts']) / fac)
                yt = r['yur_buy'] + r['yur_sell']
                tot = ft + yt
                if tot > 0:
                    fr = ft / tot
                    hist_means['fiz_yur_convergence'].append(abs(fr - 0.5))
            
            for key, arr in hist_means.items():
                arr = np.array(arr)
                if len(arr) > 5:
                    mu = np.mean(arr)
                    sigma = np.std(arr)
                    z = (f[key] - mu) / sigma if sigma > 1e-10 else 0.0
                    f[f'{key}_zscore'] = z
                    f[f'{key}_extreme_2sig'] = 1.0 if abs(z) > 2.0 else 0.0
                    p95 = np.percentile(arr, 95)
                    f[f'{key}_extreme_p95'] = 1.0 if abs(f[key]) > p95 else 0.0
                else:
                    f[f'{key}_zscore'] = 0.0
                    f[f'{key}_extreme_2sig'] = 0.0
                    f[f'{key}_extreme_p95'] = 0.0
        else:
            for k in ['fiz_net_bias_zscore','fiz_accts_bias_zscore','fiz_yur_convergence_zscore',
                      'fiz_net_bias_extreme_2sig','fiz_accts_bias_extreme_2sig','fiz_yur_convergence_extreme_2sig',
                      'fiz_net_bias_extreme_p95','fiz_accts_bias_extreme_p95','fiz_yur_convergence_extreme_p95']:
                f[k] = 0.0
        
        feat_list.append(f)
    
    return feat_list

def compute_targets(features, horizons=[1,2,3,4,5]):
    """direction: sign(close[t+n]-close[t])"""
    for h in horizons:
        for idx, f in enumerate(features):
            if idx + h < len(features):
                ret = (features[idx+h]['close'] - f['close']) / f['close']
                if ret > 0.0001:
                    f[f'target_{h}'] = 1
                elif ret < -0.0001:
                    f[f'target_{h}'] = -1
                else:
                    f[f'target_{h}'] = 0
            else:
                f[f'target_{h}'] = 0
    return features

def generate_signals(features, threshold=0.5):
    """Crowd Bias сигналы"""
    signals = {}
    for f in features:
        t = f['time']
        s = 0
        
        nb_z = f.get('fiz_net_bias_zscore', 0)
        ab_z = f.get('fiz_accts_bias_zscore', 0)
        conv = f.get('fiz_yur_convergence', 0)
        
        long_cond = (nb_z < -threshold or ab_z < -threshold) and conv > 0.2
        short_cond = (nb_z > threshold or ab_z > threshold) and conv > 0.2
        
        if long_cond:
            s = 1
        elif short_cond:
            s = -1
        
        signals[t] = s
    return signals

def backtest_one(symbol, tf, threshold=0.5):
    oi, prices = load_data(symbol)
    if oi.empty or prices.empty:
        return None
    
    oir, pr = resample(oi, prices, tf)
    if oir is None or pr is None or oir.empty or pr.empty:
        return None
    
    feats = compute_features(oir, pr, tf)
    if feats is None or len(feats) < 20:
        return None
    
    feats = compute_targets(feats)
    signals = generate_signals(feats, threshold)
    
    # Train/test split: 60/40 (2023-2026)
    times_all = [f['time'] for f in feats]
    if len(times_all) < 10:
        return None
    split_idx = int(len(times_all) * 0.6)
    
    test_feats = [f for f in feats if f['time'] >= times_all[split_idx]]
    
    # Backtest on test set
    trades = []
    test_times = [f['time'] for f in test_feats]
    time_to_feat = {f['time']: f for f in test_feats}
    
    for i, t in enumerate(test_times):
        if t not in signals or signals[t] == 0:
            continue
        signal = signals[t]
        
        if i + 1 >= len(test_times):
            continue
        
        next_t = test_times[i+1]
        if next_t not in time_to_feat:
            continue
        
        entry_price = time_to_feat[next_t]['open']
        
        # Exit after 1 bar
        if i + 2 < len(test_times):
            exit_t = test_times[i+2]
            if exit_t in time_to_feat:
                exit_price = time_to_feat[exit_t]['close']
            else:
                continue
        else:
            continue
        
        if entry_price <= 0:
            continue
        
        ret = (exit_price - entry_price) / entry_price
        pnl = ret if signal == 1 else -ret
        pnl -= 0.0002  # commission
        
        trades.append({
            'entry_time': next_t, 'exit_time': exit_t,
            'signal': signal, 'entry_price': entry_price,
            'exit_price': exit_price, 'pnl': pnl
        })
    
    n_sigs = sum(1 for s in signals.values() if s != 0)
    return compute_metrics(trades, n_sigs, trades)

def compute_metrics(trades, n_sigs, trade_list):
    if not trades:
        return {'win_rate': 0, 'profit_factor': 0, 'sharpe': 0, 'max_dd': 0, 'n_trades': 0, 'signals_per_month': 0, 'n_sigs': n_sigs}
    
    pnls = [t['pnl'] for t in trade_list]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    
    n = len(pnls)
    wr = len(wins)/n if n > 0 else 0
    gp = sum(wins) if wins else 0
    gl = abs(sum(losses)) if losses else 1
    pf = gp/gl if gl > 0 else 0
    sh = np.mean(pnls)/np.std(pnls) if np.std(pnls) > 0 else 0
    
    cum = np.cumsum(pnls)
    dd = cum - np.maximum.accumulate(cum)
    mdd = abs(min(dd)) if len(dd) > 0 else 0
    
    spm = 0
    if len(trade_list) > 1:
        days = (trade_list[-1]['exit_time'] - trade_list[0]['entry_time']).days
        months = max(days/30.44, 1)
        spm = n/months
    
    return {'win_rate': wr, 'profit_factor': pf, 'sharpe': sh, 'max_dd': mdd, 'n_trades': n, 'signals_per_month': spm, 'n_sigs': n_sigs}

def run_all():
    print("="*60)
    print("CROWD BIAS STRATEGY — MOEX BACKTEST (без look-ahead)")
    print("="*60)
    
    # 1. Top-10 full test
    for tf in ['D1', 'H4', '15m']:
        print(f"\n--- ТФ: {tf} ---")
        tres = []
        for sym in TOP_SYMBOLS:
            r = backtest_one(sym, tf)
            if r:
                tres.append((sym, r))
                print(f"  {sym:6s}: WR={r['win_rate']:.1%} PF={r['profit_factor']:.2f} Sh={r['sharpe']:.2f} DD={r['max_dd']:.4f} Trades={r['n_trades']} SPM={r['signals_per_month']:.1f}")
        
        if tres:
            avg = {k: np.mean([r[1][k] for r in tres]) for k in ['win_rate','profit_factor','sharpe','n_trades','signals_per_month']}
            print(f"  {'СРЕДНЕЕ':6s}: WR={avg['win_rate']:.1%} PF={avg['profit_factor']:.2f} Sh={avg['sharpe']:.2f} Trades={avg['n_trades']:.0f} SPM={avg['signals_per_month']:.1f}")
    
    # 2. All 64 symbols
    print(f"\n--- ВСЕ 64 ТИКЕРА (D1) ---")
    all_r = []
    for sym in ALL_SYMBOLS:
        r = backtest_one(sym, 'D1')
        if r and r['n_trades'] > 0:
            all_r.append((sym, r))
    
    all_r.sort(key=lambda x: x[1]['sharpe'], reverse=True)
    print(f"  Всего с сигналами: {len(all_r)} из {len(ALL_SYMBOLS)}")
    print(f"  ТОП-10 по Sharpe:")
    for sym, r in all_r[:10]:
        print(f"    {sym:8s}: WR={r['win_rate']:.1%} PF={r['profit_factor']:.2f} Sh={r['sharpe']:.2f} Trades={r['n_trades']}")
    print(f"  Bottom-5 по Sharpe:")
    for sym, r in all_r[-5:]:
        print(f"    {sym:8s}: WR={r['win_rate']:.1%} PF={r['profit_factor']:.2f} Sh={r['sharpe']:.2f} Trades={r['n_trades']}")
    
    # 3. Threshold sweep
    print(f"\n--- СКАНИРОВАНИЕ ПОРОГОВ ---")
    for tf in ['D1', 'H4']:
        print(f"  {tf}:")
        for th in [0.3, 0.5, 0.7, 1.0, 1.3, 1.5, 2.0]:
            rs = [backtest_one(s, tf, th) for s in TOP_SYMBOLS[:5]]
            rs = [r for r in rs if r and r['n_trades'] > 0]
            if rs:
                avg_sh = np.mean([r['sharpe'] for r in rs])
                avg_wr = np.mean([r['win_rate'] for r in rs])
                tot = sum(r['n_trades'] for r in rs)
                print(f"    th={th:.1f}: WR={avg_wr:.1%} Sh={avg_sh:.2f} Trades={tot}")
    
    # 4. Feature importance
    print(f"\n--- АНАЛИЗ ФИЧ ---")
    analyze_features()
    
    # 5. SVO analysis
    print(f"\n--- АНАЛИЗ СВО (пред-/пост-) ---")
    for tf in ['D1']:
        for sym in TOP_SYMBOLS[:3]:
            r_pre = backtest_one(sym, tf)
            print(f"  {sym}: всего {r_pre['n_trades'] if r_pre else 0} сделок (2023-2026)")

def analyze_features():
    """Анализ, какие фичи лучше всего предсказывают движение"""
    from collections import Counter
    
    win_counts = Counter()
    total_counts = Counter()
    
    for sym in TOP_SYMBOLS:
        oi, prices = load_data(sym)
        if oi.empty or prices.empty:
            continue
        oir, pr = resample(oi, prices, 'D1')
        if oir is None or pr is None:
            continue
        feats = compute_features(oir, pr, 'D1')
        if feats is None:
            continue
        feats = compute_targets(feats)
        
        for f in feats:
            for key, val in f.items():
                if key.startswith(('fiz_net_bias','fiz_accts_bias','fiz_yur_convergence','delta_fiz')):
                    continue
            # Check extremes vs target
            nb_ext = f.get('fiz_net_bias_extreme_2sig', 0)
            ab_ext = f.get('fiz_accts_bias_extreme_2sig', 0)
            target = f.get('target_1', 0)
            
            if nb_ext == 1:
                total_counts['nb_extreme'] += 1
                z = f.get('fiz_net_bias_zscore', 0)
                if (z < -2 and target == 1) or (z > 2 and target == -1):
                    win_counts['nb_extreme'] += 1
            
            if ab_ext == 1:
                total_counts['ab_extreme'] += 1
                z = f.get('fiz_accts_bias_zscore', 0)
                if (z < -2 and target == 1) or (z > 2 and target == -1):
                    win_counts['ab_extreme'] += 1
    
    for k in total_counts:
        wr = win_counts[k]/total_counts[k] if total_counts[k] > 0 else 0
        print(f"  {k}: {win_counts[k]}/{total_counts[k]} correct = {wr:.1%}")

if __name__ == '__main__':
    run_all()
