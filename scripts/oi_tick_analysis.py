#!/usr/bin/env python3 -u
"""Диагностика: сколько тиков прибыли на сделку LONG+h120?

Вопрос пользователя: «не верю, что 1 пункт убивает edge».
Ответ: если средняя сделка зарабатывает 4-6 тиков, то slippage 2-5 тиков = 50-100% edge.
Смотрим распределение PnL в тиках и причины.
"""
import sys, bisect
import numpy as np
import clickhouse_connect as cc

sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
from scripts.oi_vol_filter_backtest import load, THR

ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')
TZ_SHIFT = 5 * 3600

all_trades = []
for tk in ['NG', 'BR', 'SV']:
    for y in [2021, 2022, 2023, 2024, 2025, 2026]:
        try:
            futoi, prices, spec = load(tk, y)
        except Exception:
            continue
        pts, pprc = prices
        ms, sp = spec[1], spec[2]
        for ts in sorted(futoi.keys()):
            dn = futoi[ts]
            if dn > -THR: continue
            idx = bisect.bisect_right(pts, ts) - 1
            if idx < 0: continue
            prc = pprc[idx]
            if prc <= 0 or (ts - pts[idx]) > 600: continue
            j = bisect.bisect_left(pts, ts + 7200)
            if j >= len(pts): continue
            exit_p = pprc[j]
            # прибыль в тиках (шагах цены)
            ticks = round((exit_p - prc) / ms)
            pnl_rub = ticks * sp  # без комиссии
            fee = spec[3] * 2  # комиссия round-trip на контракт
            net_rub = pnl_rub - fee
            all_trades.append({'tk': tk, 'y': y, 'ticks': ticks, 'pnl_rub': pnl_rub,
                               'fee': fee, 'net_rub': net_rub, 'prc': prc})

print(f"Сделок: {len(all_trades)}")
print(f"\n{'тикер':<5}{'n':>7}{'avg_ticks':>10}{'med_ticks':>10}{'avg_pnl₽':>10}{'fee₽':>7}{'net₽':>10}{'WR%':>7}")
print("-" * 66)
for tk in ['NG', 'BR', 'SV']:
    rows = [t for t in all_trades if t['tk'] == tk]
    t = np.array([r['ticks'] for r in rows])
    net = np.array([r['net_rub'] for r in rows])
    print(f"{tk:<5}{len(rows):>7}{t.mean():>10.1f}{np.median(t):>10.1f}"
          f"{np.array([r['pnl_rub'] for r in rows]).mean():>10.1f}"
          f"{rows[0]['fee']:>7.0f}{net.mean():>10.1f}{(net>0).mean()*100:>7.1f}")

# распределение в тиках
all_t = np.array([r['ticks'] for r in all_trades])
print(f"\nРаспределение PnL в тиках (все):")
print(f"  p10={np.percentile(all_t,10):.0f}  p25={np.percentile(all_t,25):.0f}  "
      f"p50={np.percentile(all_t,50):.0f}  p75={np.percentile(all_t,75):.0f}  "
      f"p90={np.percentile(all_t,90):.0f}")
print(f"  доля сделок с PnL <= 1 тик: {(all_t<=1).mean()*100:.1f}%")
print(f"  доля сделок с PnL <= 3 тика: {(all_t<=3).mean()*100:.1f}%")
print(f"  доля сделок с PnL <= 5 тиков: {(all_t<=5).mean()*100:.1f}%")

# эффект slippage
print(f"\nЭффект slippage на среднюю сделку (в тиках):")
for sl in [1, 2, 3, 5]:
    # slippage съедает sl тиков с входа + sl с выхода = 2*sl
    eat = 2 * sl
    net_after = all_t - eat
    wr_after = (net_after > 0).mean() * 100
    print(f"  {sl} тик × 2 (вход+выход): средняя сделка {all_t.mean()-eat:.1f} тиков, WR={wr_after:.1f}%")

# сколько стоит 1 тик в % от цены
print(f"\nСтоимость 1 тика в % от цены:")
for tk in ['NG', 'BR', 'SV']:
    rows = [r for r in all_trades if r['tk'] == tk]
    ms = load(tk, 2024)[1][2][1]  # min_step
    avg_price = np.mean([r['prc'] for r in rows])
    print(f"  {tk}: ms={ms} цена≈{avg_price:.2f} → 1 тик = {ms/avg_price*100:.3f}% цены")

ch.close()
