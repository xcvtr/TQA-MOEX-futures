#!/usr/bin/env python3 -u
"""Финальная проверка стабильности: risk15% pyr5 pct0.3 stop1.5%.
По каждому году отдельно (ROI) + OOS 2022 + полный период.
"""
import sys
sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
import importlib.util, numpy as np, clickhouse_connect as cc
spec = importlib.util.spec_from_file_location('sw', '/tmp/oi_sweep_fast.py')
mod = importlib.util.module_from_spec(spec)
src = open('/tmp/oi_sweep_fast.py').read().split('# Sweep')[0]
exec(src, mod.__dict__)
ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')
mod.LIMITS = {'BR':1000,'NG':1000,'SV':1000}

# 2022 данные
D22 = {}
for fut_tk in ['BR','NG','SV']:
    r = ch.query(f"SELECT toUnixTimestamp(toDateTime(bt)), buy_fiz, sell_fiz, buy_yur, sell_yur FROM moex.futoi WHERE ticker='{fut_tk}' AND bt>='2022-01-01' AND bt<='2022-12-31'").result_rows
    day_start = {}; net_map = {}
    for ts, fb, fs, yb, ys in r:
        d = mod.irk_day(ts)
        if d not in day_start: day_start[d] = int(fb)-int(fs)
        total = int(fb)+int(fs)+int(yb)+int(ys)
        if total <= 0: continue
        net_map[ts] = (int(fb)-int(fs)-day_start[d])/total*100
    r2 = ch.query(f"SELECT toUnixTimestamp(toDateTime(bt)), opn, hi, lo, prc FROM moex.mt5_continuous WHERE ticker='{fut_tk}' AND bt>='2022-01-01' AND bt<='2022-12-31'").result_rows
    arr = np.array([(ts,o,h,l,c) for ts,o,h,l,c in r2 if c and c>0], dtype=np.float64)
    arr = arr[np.argsort(arr[:,0])]
    D22[fut_tk] = (net_map, arr)

ORIG = {}
for y in [2023,2024,2025,2026]:
    for tk in ['BR','NG','SV']:
        ORIG[(y,tk)] = mod.DATA[(y,tk)]

CFG = dict(risk=0.15, thr=3, exit_thr=1.5, pyr=5, pyra_pct=0.3, stop_pct=1.5)
print("=== Кандидат: risk15% pyr5 pct0.3 stop1.5% (CAGR 4xгод = ROI года) ===")
print("Год    | ROI     CashMDD  MTM     N    WR   Calmar")
for y in [2022, 2023, 2024, 2025, 2026]:
    for yy in [2023,2024,2025,2026]:
        for tk in ['BR','NG','SV']:
            mod.DATA[(yy,tk)] = D22[tk] if y == 2022 else ORIG[(y,tk)]
    cagr, cd, md, n, wr = mod.run(**CFG)
    lab = f"{y}" + (" OOS" if y==2022 else "")
    print(f"{lab:8s} | {cagr:+6.0f}%  {cd:5.1f}%  {md:5.1f}%  {n:5d}  {wr:.0f}%  {cagr/max(md,0.1):5.0f}", flush=True)

# полный период: восстановить ORIG
for yy in [2023,2024,2025,2026]:
    for tk in ['BR','NG','SV']:
        mod.DATA[(yy,tk)] = ORIG[(yy,tk)]
cagr, cd, md, n, wr = mod.run(**CFG)
print(f"\nПолный 2023-26: CAGR {cagr:+.0f}%  CashMDD {cd:.1f}%  MTM {md:.1f}%  N={n}  WR={wr:.0f}%  Calmar {cagr/max(md,0.1):.0f}")

# 2022+2023-26 (все 5 лет через ORIG+22)
for yy in [2023,2024,2025,2026]:
    for tk in ['BR','NG','SV']:
        mod.DATA[(yy,tk)] = D22[tk]
cagr22, cd22, md22, n22, wr22 = mod.run(**CFG)
print(f"2022 x4 (OOS): ROI {cagr22:+.0f}%  CashMDD {cd22:.1f}%  MTM {md22:.1f}%  N={n22}  WR={wr22:.0f}%")
