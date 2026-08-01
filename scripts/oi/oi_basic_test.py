import sys, numpy as np
sys.path.insert(0, "/home/user/projects/TQA-MOEX-futures")
import clickhouse_connect as cc
from datetime import datetime, timedelta

ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')

# Базовый тест: перекос физлиц vs будущее движение цены
# Гипотеза: если физлица массово покупают (net_fiz >> 0), цена скорее упадёт (contrarian)
# Если массово продают (net_fiz << 0) — цена вырастет

for ticker in ['Si', 'GD', 'BR', 'NG', 'RN']:
    # Цены M1
    prices = {}
    rows = ch.query(f"SELECT bt, prc FROM moex.mt5_continuous WHERE ticker='{ticker}' AND bt>='2024-01-01' ORDER BY bt").result_rows
    for r in rows:
        prices[r[0]] = float(r[1])
    
    # OI физ/юр
    oi_rows = ch.query(f"SELECT bt,buy_fiz,sell_fiz,buy_yur,sell_yur FROM moex.futoi WHERE ticker='{ticker}' AND bt>='2024-01-01' ORDER BY bt").result_rows
    
    # Собираем сигналы: net_fiz перекос на каждом OI баре (5 мин)
    # Выход: цена через 30/60/120 мин
    signals = []  # (net_fiz_norm, ret_30, ret_60, ret_120)
    
    for i in range(1, len(oi_rows)):
        bt, fb, fs, yb, ys = oi_rows[i]
        # net_fiz текущий
        net_fiz = fb - fs
        total = fb + fs + yb + ys
        if total <= 0: continue
        net_fiz_norm = net_fiz / total * 100  # % от всего OI
        
        # Будущая цена: +30, +60, +120 мин
        future_times = [bt + timedelta(minutes=m) for m in [30, 60, 120]]
        rets = []
        ok = True
        cur_price = None
        # цена на текущий момент
        best_ts = None
        for ts, p in prices.items():
            if ts <= bt: best_ts = (ts, p)
            else: break
        if best_ts: cur_price = best_ts[1]
        if not cur_price: continue
        
        for ft in future_times:
            fut = None
            for ts, p in prices.items():
                if ts >= ft:
                    fut = p
                    break
            if fut:
                rets.append((fut - cur_price) / cur_price * 100)
            else:
                rets.append(None)
        
        if rets[0] is not None:
            signals.append((net_fiz_norm, rets[0], rets[1], rets[2]))
    
    if len(signals) < 100: 
        print(f"{ticker}: мало сигналов {len(signals)}")
        continue
    
    # Корреляция
    arr = np.array(signals)
    net = arr[:, 0]
    corr30 = np.corrcoef(net, arr[:, 1])[0,1] if np.std(net)>0 else 0
    corr60 = np.corrcoef(net, arr[:, 2])[0,1] if np.std(net)>0 else 0
    corr120 = np.corrcoef(net, arr[:, 3])[0,1] if np.std(net)>0 else 0
    
    # Контрарный тест: физ покупают сильно (net>0) → шортим
    # Квантили net_fiz_norm
    q75 = np.percentile(net, 75)
    q25 = np.percentile(net, 25)
    
    buy_sig = arr[net > q75]   # физ массово покупают
    sell_sig = arr[net < q25]  # физ массово продают
    
    print(f"\n{ticker}: сигналов {len(signals)}")
    print(f"  Corr: 30m={corr30:+.3f} 60m={corr60:+.3f} 120m={corr120:+.3f}")
    print(f"  q25={q25:+.2f}% q75={q75:+.2f}%")
    if len(buy_sig) > 20:
        print(f"  Физ покупают (n={len(buy_sig)}): ret30={np.mean(buy_sig[:,1]):+.3f}% ret60={np.mean(buy_sig[:,2]):+.3f}% ret120={np.mean(buy_sig[:,3]):+.3f}%")
    if len(sell_sig) > 20:
        print(f"  Физ продают (n={len(sell_sig)}): ret30={np.mean(sell_sig[:,1]):+.3f}% ret60={np.mean(sell_sig[:,2]):+.3f}% ret120={np.mean(sell_sig[:,3]):+.3f}%")

ch.close()
