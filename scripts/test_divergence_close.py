#!/usr/bin/env python3
"""Volume Surge + Divergence — close-based exit"""
import psycopg2
DB = dict(host='10.0.0.60', port=5432, dbname='moex', user='postgres', password='postgres')

def zscore(values, window=20):
    r = [0.0]*len(values)
    for i in range(window, len(values)):
        c = values[i-window:i]; m = sum(c)/window; s = (sum((x-m)**2 for x in c)/window)**0.5
        r[i] = (values[i]-m)/s if s>0 else 0.0
    return r

conn = psycopg2.connect(**DB)
cur = conn.cursor()
cur.execute("""
    SELECT oi.time, oi.fiz_buy, oi.fiz_sell, oi.yur_buy, oi.yur_sell, p.close, p.volume
    FROM moex_prices_5m_oi oi
    JOIN moex_prices_5m p ON p.symbol=oi.symbol AND p.time=oi.time
    WHERE oi.symbol='Eu' ORDER BY oi.time
""")
rows = cur.fetchall()
cur.close(); conn.close()

vol = [r[6] or 0 for r in rows]
fiz_net = [r[1]-r[2] for r in rows]
yur_net = [r[3]-r[4] for r in rows]

vol_z = zscore(vol, 20)
fiz_z = zscore(fiz_net, 20)
yur_z = zscore(yur_net, 20)

print("Close-based, exit через N баров")
print(f"{'Exit':>4s} {'Div z≥0.5':>18s} {'Div z≥1.0':>18s} {'Div z≥1.5':>18s} {'Div z≥2.0':>18s}")
print("-" * 80)

for exit_bars in [3, 6, 12, 24, 48]:  # 15min, 30min, 1h, 2h, 4h
    line = f"{exit_bars*5:>3d}мин"
    for div_zt in [0.5, 1.0, 1.5, 2.0]:
        for vol_zt in [2.0]:
            trades = []
            for i in range(21, len(rows)-exit_bars):
                if vol_z[i] < vol_zt: continue
                fz, yz = fiz_z[i], yur_z[i]
                if fz * yz >= 0: continue  # no divergence
                if abs(fz) < div_zt or abs(yz) < div_zt: continue
                
                entry = rows[i][5]
                if entry is None or entry == 0: continue
                
                # LONG only: FIZ short + YUR long
                if not (fz < 0 and yz > 0): continue
                
                exit_p = rows[i+exit_bars][5]
                if exit_p is None: continue
                
                trades.append((exit_p - entry) / entry * 100)
            
            if trades:
                wr = sum(1 for r in trades if r > 0)/len(trades)*100
                avg = sum(trades)/len(trades)
                pf = sum(r for r in trades if r>0)/abs(sum(r for r in trades if r<0)) if any(r<0 for r in trades) else float('inf')
                line += f" {len(trades):3d}s {wr:4.1f}%"
            else:
                line += f" {'—':>12s}"
    print(line)

# Best config detail
print("\n\nДетально: div z≥1.0, exit 12 bars (1h):")
trades = []
for i in range(21, len(rows)-12):
    if vol_z[i] < 2.0: continue
    fz, yz = fiz_z[i], yur_z[i]
    if fz * yz >= 0: continue
    if abs(fz) < 1.0 or abs(yz) < 1.0: continue
    if not (fz < 0 and yz > 0): continue
    entry = rows[i][5]
    if entry is None: continue
    exit_p = rows[i+12][5]
    if exit_p is None: continue
    trades.append({
        'time': str(rows[i][0]),
        'fiz_z': round(fz,2), 'yur_z': round(yz,2),
        'entry': entry, 'exit': exit_p,
        'ret': round((exit_p-entry)/entry*100, 3),
        'vol': rows[i][6]
    })

wr = sum(1 for t in trades if t['ret']>0)/len(trades)*100
avg_r = sum(t['ret'] for t in trades)/len(trades)
print(f"Сигналов: {len(trades)}, WR: {wr:.1f}%, Avg: {avg_r:+.3f}%")
print("\nПервые 20:")
for t in trades[:20]:
    print(f"  {t['time']} FIZ={t['fiz_z']:+.1f} YUR={t['yur_z']:+.1f} vol={t['vol']:>6d} ret={t['ret']:+.3f}% {'WIN' if t['ret']>0 else 'LOSE'}")
