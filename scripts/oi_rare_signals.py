#!/usr/bin/env python3 -u
"""Феномен: накопленный day_net → движение (редкие крупные сделки).

Текущая стратегия: мгновенный (b-s)/(b+s), thr 4%, hold 120 мин — сделки частые, edge 5 тиков.
Гипотеза пользователя: нужны РЕДКИЕ и КРУПНЫЕ сделки.

Проверяем:
1. Накопленный за день net: (cur-open)/total — сила паники толпы
2. Движение за 2ч / до конца дня / 1 день / 2 дня после сигнала
3. Только экстремальные пороги (5%, 8%, 12%, 15%)
4. avg PnL в тиках и % для таких сигналов
"""
import sys, bisect
import numpy as np
import clickhouse_connect as cc

sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
from scripts.oi_vol_filter_backtest import load

ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')
TZ_SHIFT = 5 * 3600

def load_daily_net(tk, y):
    """Накопленный day_net: (cur-open)/total * 100 по каждому ts."""
    futoi, prices, spec = load(tk, y)
    fts = sorted(futoi.keys())
    daily_open = {}
    for ts in fts:
        d = int((ts - TZ_SHIFT) // 86400)
        if d not in daily_open:
            daily_open[d] = None
    # нужны сырые значения buy/sell — загрузим заново
    import clickhouse_connect as cc2
    ch2 = cc2.get_client(host='10.0.0.60', port=8123, database='moex')
    FT = {'SV': 'SV', 'NG': 'NG', 'BR': 'BR'}
    START, END = f'{y}-01-01', f'{y}-12-31'
    if y == 2026: END = '2026-08-07'
    r = ch2.query(f"SELECT bt, buy_fiz, sell_fiz, buy_yur, sell_yur FROM moex.futoi "
                  f"WHERE ticker='{FT[tk]}' AND bt>='{START}' AND bt<='{END} 23:59:59'").result_rows
    ch2.close()
    rows = []
    day_start = {}
    for bt, fb, fs, yb, ys in r:
        ts = bt.replace(tzinfo=None).timestamp() + TZ_SHIFT
        d = int((ts - TZ_SHIFT) // 86400)
        if d not in day_start:
            day_start[d] = int(fb) - int(fs)
        total = int(fb) + int(fs) + int(yb) + int(ys)
        if total <= 0: continue
        dn = (int(fb) - int(fs) - day_start[d]) / total * 100
        rows.append((ts, dn))
    return rows, prices

# Собираем сигналы по накопленному day_net с движением на разных горизонтах
all_sigs = []
for tk in ['NG', 'BR', 'SV']:
    for y in [2022, 2023, 2024, 2025, 2026]:
        try:
            rows, prices = load_daily_net(tk, y)
        except Exception as e:
            continue
        pts, pprc = prices
        for ts, dn in rows:
            if dn > -4: continue  # LONG сигнал (накопленные продажи)
            idx = bisect.bisect_right(pts, ts) - 1
            if idx < 0: continue
            prc = pprc[idx]
            if prc <= 0 or (ts - pts[idx]) > 600: continue
            entry = {'ts': ts, 'prc': prc, 'dn': dn, 'tk': tk, 'y': y}
            # движение на 2ч, 4ч, до конца дня, 1 день
            for hold_min, hname in [(120, '2ч'), (240, '4ч'), (480, 'до конца дня~8ч')]:
                j = bisect.bisect_left(pts, ts + hold_min * 60)
                if j >= len(pts): entry[hname] = None
                else: entry[hname] = (pprc[j] - prc) / prc * 100
            # до конца дня (последняя цена дня)
            day_end = int((ts - TZ_SHIFT) // 86400) + 1
            cutoff = (day_end * 86400) - TZ_SHIFT
            j = bisect.bisect_right(pts, cutoff) - 1
            if j > idx:
                entry['EOD'] = (pprc[j] - prc) / prc * 100
            else:
                entry['EOD'] = None
            # следующий день
            j2 = bisect.bisect_left(pts, cutoff + 86400)
            if j2 < len(pts):
                entry['+1д'] = (pprc[j2] - prc) / prc * 100
            else:
                entry['+1д'] = None
            all_sigs.append(entry)

print(f"Сигналов (накопленный dn <= -4): {len(all_sigs)}")

# По порогам и горизонтам
print(f"\n{'порог':<8}{'n':>7}{'2ч%':>9}{'4ч%':>9}{'EOD%':>9}{'+1д%':>9}{'2ч_тик':>9}{'EOD_тик':>9}")
print("-" * 72)
for thr in [4, 6, 8, 10, 15, 20]:
    sigs = [s for s in all_sigs if s['dn'] <= -thr]
    if len(sigs) < 20: continue
    # средние по горизонтам
    def avg(sigs, h):
        v = [s[h] for s in sigs if s.get(h) is not None]
        return np.mean(v) if v else float('nan')
    # тики: NG ms=0.001, BR/SV ms=0.01 — приблизительно для NG
    print(f"<=-{thr:<5}{len(sigs):>7}"
          f"{avg(sigs,'2ч')*100:>+9.3f}{avg(sigs,'4ч')*100:>+9.3f}"
          f"{avg(sigs,'EOD')*100:>+9.3f}{avg(sigs,'+1д')*100:>+9.3f}"
          f"{(avg(sigs,'2ч')/0.001 if not np.isnan(avg(sigs,'2ч')) else 0):>+9.0f}"
          f"{(avg(sigs,'EOD')/0.001 if not np.isnan(avg(sigs,'EOD')) else 0):>+9.0f}")

# Сильные сигналы по тикерам (порог 10)
print(f"\n=== Сильные сигналы (dn <= -10) по тикерам ===")
for tk in ['NG', 'BR', 'SV']:
    sigs = [s for s in all_sigs if s['tk'] == tk and s['dn'] <= -10]
    if len(sigs) < 5: continue
    def avg(sigs, h):
        v = [s[h] for s in sigs if s.get(h) is not None]
        return np.mean(v) if v else float('nan')
    print(f"{tk}: n={len(sigs):>4}  2ч={avg(sigs,'2ч')*100:+.3f}%  EOD={avg(sigs,'EOD')*100:+.3f}%  +1д={avg(sigs,'+1д')*100:+.3f}%")

# Распределение: сколько сделок в год при пороге 10
print(f"\n=== Сделок в год (порог 10) ===")
for tk in ['NG', 'BR', 'SV']:
    for y in [2024, 2025, 2026]:
        sigs = [s for s in all_sigs if s['tk'] == tk and s['y'] == y and s['dn'] <= -10]
        if sigs:
            avg_eod = np.mean([s['EOD'] for s in sigs if s['EOD'] is not None]) * 100
            print(f"{tk} {y}: n={len(sigs):>3}  EOD avg={avg_eod:+.2f}%")

ch.close()
