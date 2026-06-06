#!/usr/bin/env python3
"""YUR Volume Surge — вспышки объёма на Eu = YUR входит, цена идёт против FIZ."""
import psycopg2, sys, math
from collections import defaultdict

DB = dict(host='10.0.0.60', port=5432, dbname='moex', user='postgres', password='postgres')

def zscore_past(values, window=20):
    result = [0.0] * len(values)
    for i in range(window, len(values)):
        chunk = values[i-window:i]
        mu = sum(chunk) / window
        var = sum((x-mu)**2 for x in chunk) / window
        sd = var ** 0.5
        result[i] = (values[i] - mu) / sd if sd > 0 else 0.0
    return result

conn = psycopg2.connect(**DB)
cur = conn.cursor()

# Get Eu 5m data with OI + volume
cur.execute("""
    SELECT oi.time, oi.fiz_buy, oi.fiz_sell, oi.yur_buy, oi.yur_sell,
           p.open, p.high, p.low, p.close, p.volume
    FROM moex_prices_5m_oi oi
    JOIN moex_prices_5m p ON p.symbol = oi.symbol AND p.time = oi.time
    WHERE oi.symbol = 'Eu'
    ORDER BY oi.time
""")
rows = cur.fetchall()
cur.close()
conn.close()

print(f"Eu: {len(rows)} баров")

volumes = [r[9] or 0 for r in rows]
fiz_net = [r[1] - r[2] for r in rows]
yur_net = [r[3] - r[4] for r in rows]
vol_z = zscore_past(volumes, window=20)

results = []
for zt in [2.0, 2.5, 3.0, 4.0, 5.0]:
    trades = []
    for i in range(21, len(rows) - 5):  # need 5 bars ahead
        if vol_z[i] < zt:
            continue
        
        # Entry: close of current bar (open next bar)
        entry = rows[i][8]  # close
        if entry is None or entry == 0:
            continue
        
        fiz_before = fiz_net[i]  # FIZ position at volume surge
        yur_before = yur_net[i]
        
        # Check next 5 bars (25 min)
        closes_ahead = [rows[i+j][8] for j in range(1, 6) if rows[i+j][8] is not None]
        if not closes_ahead:
            continue
        
        # Did price go DOWN after volume surge? (YUR buying → price up, then YUR sells to FIZ → price down)
        max_price = max(closes_ahead)
        min_price = min(closes_ahead)
        
        # YUR surge hypothesis: after volume spike, price goes against FIZ direction
        # If FIZ was long (fiz_net > 0), YUR sells → price down
        # If FIZ was short (fiz_net < 0), YUR buys → price up
        
        if fiz_before > 0:  # FIZ long → expect price down
            ret = (min_price - entry) / entry * 100
            win = ret < -0.05  # dropped at least 0.05%
        else:  # FIZ short → expect price up
            ret = (max_price - entry) / entry * 100
            win = ret > 0.05
        
        trades.append({
            'time': str(rows[i][0]),
            'vol_z': round(vol_z[i], 2),
            'volume': volumes[i],
            'fiz_before': fiz_before,
            'entry': entry,
            'best': max_price if fiz_before < 0 else min_price,
            'ret': round(ret, 3),
            'win': win,
            'direction': 'LONG' if fiz_before < 0 else 'SHORT'
        })
    
    if trades:
        wins = sum(1 for t in trades if t['win'])
        win_ret = sum(t['ret'] for t in trades if t['ret'] > 0)
        lose_ret = abs(sum(t['ret'] for t in trades if t['ret'] < 0))
        pf = win_ret / lose_ret if lose_ret > 0 else float('inf')
        avg_ret = sum(t['ret'] for t in trades) / len(trades)
        results.append((zt, len(trades), wins/len(trades)*100, pf, avg_ret, trades[:10]))

print(f"\n{'Порог':>6s} {'Сигн':>6s} {'WR%':>7s} {'PF':>7s} {'AvgRet':>8s}")
print("-" * 40)
for zt, n, wr, pf, avg, _ in results:
    pf_s = f"{pf:.2f}" if pf != float('inf') else "INF"
    print(f"{zt:>5.1f} {n:>6d} {wr:>6.1f}% {pf_s:>7s} {avg:>+7.3f}%")

# Best result detail
if results:
    best = max(results, key=lambda x: x[2])
    print(f"\n\nЛучший: zt={best[0]}, WR={best[2]:.1f}%, PF={best[3]:.2f}, {best[1]} сигналов")
    print("\nПервые 10 сделок:")
    for t in best[4][:10]:
        print(f"  {t['time']} vol_z={t['vol_z']:.1f} FIZ={t['fiz_before']:+d} dir={t['direction']:5s} ret={t['ret']:+.3f}% {'WIN' if t['win'] else 'LOSE'}")

# Also test: pure volume surge trade (no FIZ filter)
print("\n\n--- БЕЗ ФИЛЬТРА FIZ (просто Volume Surge) ---")
for zt in [2.0, 2.5, 3.0, 4.0]:
    trades = []
    for i in range(21, len(rows) - 5):
        if vol_z[i] < zt:
            continue
        entry = rows[i][8]
        if entry is None or entry == 0:
            continue
        closes_ahead = [rows[i+j][8] for j in range(1, 6) if rows[i+j][8] is not None]
        if not closes_ahead:
            continue
        ret = (max(closes_ahead) - entry) / entry * 100  # always long
        trades.append(ret)
    
    if trades:
        wr = sum(1 for r in trades if r > 0.05) / len(trades) * 100
        avg = sum(trades) / len(trades)
        pf = sum(r for r in trades if r > 0) / abs(sum(r for r in trades if r < 0)) if any(r < 0 for r in trades) else float('inf')
        print(f"  z≥{zt:.1f}: sig={len(trades):5d} WR={wr:5.1f}% PF={pf:.2f} avg={avg:+.3f}%")
