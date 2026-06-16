#!/usr/bin/env python3
"""Alternative signal detection: volume-confirmed OI extremes."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import clickhouse_connect
import numpy as np
from datetime import datetime, timedelta
from config import CH_HOST, CH_PORT, CH_DB

ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)

# Все тикеры, где >3 TROUGH→LONG было
TICKERS = ['SN', 'AL', 'AU', 'MG', 'BM', 'LK']

for ticker in TICKERS:
    rows = ch.query('''
        SELECT p.time, p.close, p.volume, o.yur_buy, o.yur_sell, o.total_oi
        FROM moex.prices_5m p
        INNER JOIN moex.prices_5m_oi o ON p.symbol = o.symbol AND p.time = o.time
        WHERE p.symbol = %(t)s AND p.time >= '2025-01-01' AND p.time <= '2026-05-01'
        ORDER BY p.time
    ''', parameters={'t': ticker}).result_rows
    
    if len(rows) < 100:
        continue
    
    vol = np.array([float(r[2]) for r in rows])
    yur_buy = np.array([float(r[3]) for r in rows])
    yur_sell = np.array([float(r[4]) for r in rows])
    total_oi = np.array([float(r[5]) for r in rows])
    total_oi = np.where(total_oi <= 0, 1, total_oi)
    close = np.array([float(r[1]) for r in rows])
    
    yur_net_pct = (yur_buy - yur_sell) / total_oi * 100
    
    # Rolling z-score volume + yur_net
    vol_z = np.zeros(len(vol))
    yn_z = np.zeros(len(yur_net_pct))
    
    for i in range(20, len(vol)):
        v_slice = vol[max(0,i-20):i]
        vol_z[i] = (vol[i] - np.mean(v_slice)) / (np.std(v_slice) + 1e-10)
        y_slice = yur_net_pct[max(0,i-20):i]
        yn_z[i] = (yur_net_pct[i] - np.mean(y_slice)) / (np.std(y_slice) + 1e-10)
    
    print(f'\n=== {ticker} ===')
    print(f'Bars: {len(rows)}')
    
    # Сигнал 1: VOLUME SPIKE + yur_net экстремум
    vol_sig = np.where((vol_z > 2.0) & (yn_z < -1.5))[0]
    print(f'  Volume spike(z>2) + yur_net extreme(z<-1.5): {len(vol_sig)} signals')
    
    # Сигнал 2: ABSOLUTE OI SHIFT — yur_buy вырос > 2z + volume > 1.5z
    yb_z = np.zeros(len(yur_buy))
    for i in range(20, len(yur_buy)):
        yb_slice = yur_buy[max(0,i-20):i]
        yb_z[i] = (yur_buy[i] - np.mean(yb_slice)) / (np.std(yb_slice) + 1e-10)
    
    buy_sig = np.where((vol_z > 1.5) & (yb_z > 2.0))[0]
    print(f'  Volume(z>1.5) + yur_buy spike(z>2): {len(buy_sig)} signals')
    
    # Сигнал 3: yur_net_pct < -80% (почти все продано физакам) - чистый Extreme
    extreme_sig = np.where(yur_net_pct < -80)[0]
    print(f'  yur_net_pct < -80%: {len(extreme_sig)} signals')
    
    # Покажем топ-5 сигналов по типу 2
    if len(buy_sig) > 0:
        print('  Top buy-signals:')
        for idx in buy_sig[:5]:
            t = str(rows[idx][0])[5:19]
            print(f'    {t} close={close[idx]:.2f} vol={vol[idx]:.0f}(z={vol_z[idx]:.1f}) yur_buy={yur_buy[idx]:.0f}(z={yb_z[idx]:.1f}) yur_net={yur_net_pct[idx]:.1f}%')
