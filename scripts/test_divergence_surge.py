#!/usr/bin/env python3
"""Volume Surge + FIZ/YUR Divergence — фильтр для отлова лонгов YUR."""
import psycopg2
DB = dict(host='10.0.0.60', port=5432, dbname='moex', user='postgres', password='postgres')

def zscore_past(values, window=20):
    result = [0.0] * len(values)
    for i in range(window, len(values)):
        chunk = values[i-window:i]
        mu = sum(chunk)/window; sd = (sum((x-mu)**2 for x in chunk)/window)**0.5
        result[i] = (values[i]-mu)/sd if sd > 0 else 0.0
    return result

conn = psycopg2.connect(**DB)
cur = conn.cursor()
cur.execute("""
    SELECT oi.time, oi.fiz_buy, oi.fiz_sell, oi.yur_buy, oi.yur_sell,
           p.open, p.high, p.low, p.close, p.volume
    FROM moex_prices_5m_oi oi
    JOIN moex_prices_5m p ON p.symbol = oi.symbol AND p.time = oi.time
    WHERE oi.symbol = 'Eu'
    ORDER BY oi.time
""")
rows = cur.fetchall()
cur.close(); conn.close()

print(f"Eu: {len(rows)} баров\n")

volumes = [r[9] or 0 for r in rows]
fiz_net = [r[1] - r[2] for r in rows]   # FIZ net position (+ = long)
yur_net = [r[3] - r[4] for r in rows]   # YUR net position
fiz_ratio = [(r[1]-r[2])/(r[1]+r[2])*100 if (r[1]+r[2]) > 0 else 0 for r in rows]
yur_pct_net = yur_net  # raw difference

vol_z = zscore_past(volumes, 20)
fiz_z = zscore_past(fiz_net, 20)
yur_z = zscore_past(yur_pct_net, 20)

for vol_zt in [2.0, 2.5, 3.0, 4.0]:
    print(f"\n=== Volume z ≥ {vol_zt:.1f} ===")
    
    # Analyser: группируем сделки по divergence порогам
    for div_zt in [0.5, 1.0, 1.5, 2.0]:
        all_signals = []
        long_signals = []
        
        for i in range(21, len(rows) - 24):  # 24 bars ahead = 2h
            if vol_z[i] < vol_zt:
                continue
            
            # Divergence: FIZ one way, YUR the other
            fz = fiz_z[i]
            yz = yur_z[i]
            
            if abs(fz) < div_zt and abs(yz) < div_zt:
                continue
            
            # FIZ и YUR должны быть в разные стороны
            if fz * yz >= 0:
                continue  # в одном направлении — шум
            
            entry = rows[i][8]  # close of surge bar
            if entry is None or entry == 0:
                continue
            
            # Смотрим 2ч вперёд (24 бара)
            future_closes = [rows[i+j][8] for j in range(1, 25) if rows[i+j][8] is not None]
            if not future_closes:
                continue
            
            max_ret = (max(future_closes) - entry) / entry * 100
            min_ret = (min(future_closes) - entry) / entry * 100
            
            signal_type = '???'
            if fz > 0 and yz < 0: signal_type = 'FIZ_LONG_YUR_SHORT'
            if fz < 0 and yz > 0: signal_type = 'FIZ_SHORT_YUR_LONG'
            
            all_signals.append({
                'type': signal_type,
                'fiz_z': fz, 'yur_z': yz,
                'entry': entry, 'max_ret': max_ret, 'min_ret': min_ret,
                'vol': volumes[i], 'vol_z': vol_z[i],
                'time': str(rows[i][0])
            })
            
            # Только лонговые сигналы (YUR long = FIZ short)
            if signal_type == 'FIZ_SHORT_YUR_LONG':
                long_signals.append(all_signals[-1])
        
        # Статистика по лонгам
        if long_signals:
            # Entry at close, TP = max in 2h, SL = min in 2h
            tp_hits = sum(1 for s in long_signals if s['max_ret'] > 0.1)  # hit TP if price went up 0.1%+
            tp_wr = tp_hits / len(long_signals) * 100
            avg_max = sum(s['max_ret'] for s in long_signals) / len(long_signals)
            avg_min = sum(s['min_ret'] for s in long_signals) / len(long_signals)
            
            print(f"  Div z≥{div_zt:.1f}: LONG sig={len(long_signals):4d} "
                  f"TP-hit={tp_wr:5.1f}% avg_max={avg_max:+.3f}% avg_min={avg_min:+.3f}%")
        
        # Все сигналы
        if all_signals and div_zt == 1.0:
            short_signals = [s for s in all_signals if s['type'] == 'FIZ_LONG_YUR_SHORT']
            if short_signals:
                stp = sum(1 for s in short_signals if s['max_ret'] > 0.1)
                print(f"    SHORT sig={len(short_signals):4d} TP-hit={stp/len(short_signals)*100:.1f}%")

# --- Паттерн-анализ: разбивка по величине divergence ---
print("\n\n=== ПАТТЕРН-АНАЛИЗ FIZ_SHORT_YUR_LONG ===")
print(f"{'FIZ z':>7s} {'YUR z':>7s} {'Сигн':>5s} {'TP%':>6s} {'avg_max':>8s} {'avg_min':>8s}")
print("-" * 50)

for fz_t in [(-3, -2), (-2, -1.5), (-1.5, -1), (-1, -0.5)]:
    for yz_t in [(0.5, 1), (1, 1.5), (1.5, 2), (2, 3)]:
        sigs = []
        for i in range(21, len(rows) - 24):
            if vol_z[i] < 2.0: continue
            fz = fiz_z[i]; yz = yur_z[i]
            if not (fz_t[0] <= fz < fz_t[1] and yz_t[0] <= yz < yz_t[1]): continue
            
            entry = rows[i][8]
            if entry is None: continue
            closes = [rows[i+j][8] for j in range(1,25) if rows[i+j][8] is not None]
            if not closes: continue
            sigs.append((max(closes)-entry)/entry*100)
        
        if sigs:
            tp = sum(1 for r in sigs if r > 0.1)/len(sigs)*100
            print(f"{fz_t} {yz_t} {len(sigs):5d} {tp:5.1f}% {sum(sigs)/len(sigs):>+7.3f}% {min(sigs):>+7.3f}%")
