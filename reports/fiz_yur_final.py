#!/usr/bin/env python3
"""
FIZ/YUR Divergence Test — FINAL SUMMARY
"""
import pandas as pd
import numpy as np
import requests
from datetime import datetime
import warnings
warnings.filterwarnings('ignore')

CLICKHOUSE_HOST = 'http://10.0.0.60:8123'
DATABASE = 'moex'
TICKERS = ['Si', 'GZ', 'BR', 'NG', 'CR', 'SR']
START_DATE = '2024-10-01'
END_DATE = datetime.now().strftime('%Y-%m-%d')

def query_ch(query):
    url = f"{CLICKHOUSE_HOST}/?database={DATABASE}"
    r = requests.post(url, data=query, timeout=60)
    r.raise_for_status()
    return r.text

def load_data(ticker):
    oi_query = f"""
    SELECT time, fiz_buy, fiz_sell, yur_buy, yur_sell, total_oi
    FROM moex.prices_5m_oi WHERE symbol='{ticker}' AND time>='{START_DATE}'
    ORDER BY time FORMAT TabSeparatedWithNames
    """
    oi_raw = query_ch(oi_query)
    lines = oi_raw.strip().split('\n')
    if len(lines) <= 1: return None
    rows = []
    for line in lines[1:]:
        if not line.strip(): continue
        p = line.split('\t')
        if len(p) >= 6:
            rows.append({'time':p[0], 'fiz_buy':int(p[1]),'fiz_sell':int(p[2]),
                         'yur_buy':int(p[3]),'yur_sell':int(p[4]),'total_oi':int(p[5])})
    df = pd.DataFrame(rows); df['time'] = pd.to_datetime(df['time']); df.set_index('time',inplace=True)
    
    pq = f"SELECT time,close FROM moex.prices_5m WHERE symbol='{ticker}' AND time>='{START_DATE}' ORDER BY time FORMAT TabSeparatedWithNames"
    pr = query_ch(pq)
    lines = pr.strip().split('\n')
    if len(lines) <= 1: return None
    prows = []
    for line in lines[1:]:
        if not line.strip(): continue
        p = line.split('\t')
        if len(p)>=2 and p[1]!='\\N' and p[1]!='':
            prows.append({'time':p[0],'close':float(p[1])})
    pdf = pd.DataFrame(prows); pdf['time']=pd.to_datetime(pdf['time']); pdf.set_index('time',inplace=True)
    pdf = pdf[~pdf.index.duplicated(keep='first')]
    return df.join(pdf, how='inner')

def run_test(ticker):
    df = load_data(ticker)
    if df is None or len(df)<100: return None
    
    df['fiz_net'] = (df['fiz_buy']-df['fiz_sell'])/df['total_oi']
    df['yur_net'] = (df['yur_buy']-df['yur_sell'])/df['total_oi']
    df['dfiz'] = df['fiz_net'].diff()
    df['dyur'] = df['yur_net'].diff()
    df = df.dropna(subset=['dfiz','dyur'])
    
    # z-scores
    df['dfiz_z'] = (df['dfiz']-df['dfiz'].mean())/df['dfiz'].std()
    df['dyur_z'] = (df['dyur']-df['dyur'].mean())/df['dyur'].std()
    
    # Thresholds
    thresholds = {
        'raw': (lambda r: r['dfiz']*r['dyur']<0, lambda r: True),
        'z05': (lambda r: r['dfiz']*r['dyur']<0, lambda r: abs(r['dfiz_z'])>=0.5 and abs(r['dyur_z'])>=0.5),
        'z10': (lambda r: r['dfiz']*r['dyur']<0, lambda r: abs(r['dfiz_z'])>=1.0 and abs(r['dyur_z'])>=1.0),
        'z15': (lambda r: r['dfiz']*r['dyur']<0, lambda r: abs(r['dfiz_z'])>=1.5 and abs(r['dyur_z'])>=1.5),
    }
    
    print(f"\n  {ticker} ({len(df)} rows)")
    print(f"  {'Method':<10} {'Fwd':>4} {'Signals':>8} {'Wins':>6} {'WR%':>7} {'AvgRet%':>10} {'AvgFwdRet%':>12} {'Signal':>8}")
    print(f"  {'-'*65}")
    
    for method, (div_fn, thresh_fn) in thresholds.items():
        mask = pd.Series(False, index=df.index)
        for idx in df.index:
            r = df.loc[idx]
            mask[idx] = div_fn(r) and thresh_fn(r)
        
        trade_dir = np.sign(df['dyur'])
        n_sig = int(mask.sum())
        
        for fwd in [3, 6, 12]:
            fwd_ret = df['close'].pct_change(fwd).shift(-fwd)
            sig_mask = mask & fwd_ret.notna()
            n = sig_mask.sum()
            
            if n < 10:
                wr_str = "N/A"
                continue
            
            strat_ret = trade_dir[sig_mask] * fwd_ret[sig_mask]
            wins = int((strat_ret > 0).sum())
            wr = wins / n * 100
            avg_ret = strat_ret.mean() * 100
            avg_fwd = fwd_ret[sig_mask].mean() * 100
            
            sig = "YES ✅" if wr >= 52 else "NO ❌"
            print(f"  {method:<10} {fwd:>4} {n:>8} {wins:>6} {wr:>6.1f}% {avg_ret:>+9.4f}% {avg_fwd:>+11.4f}% {sig:>8}")
    
    return True

def main():
    print("="*85)
    print("FIZ/YUR DIVERGENCE TEST — MOEX Futures 5m")
    print(f"Period: {START_DATE} to {END_DATE}")
    print("="*85)
    
    for t in TICKERS:
        run_test(t)
    
    print("\n" + "="*85)
    print("CRITERIA: If WR < 52% → NO SIGNAL | Trade direction = follow yur (institutions)")
    print("="*85)

if __name__ == '__main__':
    main()
