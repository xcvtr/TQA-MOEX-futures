import clickhouse_connect as cc
import numpy as np

ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')
ch64 = cc.get_client(host='10.0.0.64', port=8123, database='forex')

# ── Lead-lag: USDRUBrfd (AlfaForex) vs Si (MOEX) ──
# H1 2022-2026
rows = ch.query("""
    SELECT toStartOfHour(bt) h, argMax(prc, bt) prc
    FROM moex.mt5_continuous WHERE ticker='Si' AND bt >= '2022-01-01'
    GROUP BY h ORDER BY h
""").result_rows
si = {r[0]: float(r[1]) for r in rows}
rows2 = ch64.query("""
    SELECT toStartOfHour(time) h, argMax(close, time) c
    FROM forex.usdrubrfd WHERE time >= '2022-01-01'
    GROUP BY h ORDER BY h
""").result_rows
usd = {r[0]: float(r[1]) for r in rows2}
common = sorted(set(si) & set(usd))
ts = [t for t in common if 7 <= t.hour <= 23]
print(f'Общих часов: {len(ts)}')

# Нормализуем: возвраты (логи) и считаем lead-lag корреляцию
si_v = np.array([si[t] for t in ts])
usd_v = np.array([usd[t] for t in ts])
r_si = np.diff(np.log(si_v))
r_usd = np.diff(np.log(usd_v))
print(f'\n=== LEAD-LAG: корреляция возвратов (H1) ===')
print(f'положительный лаг = USDRUB опережает Si (USDRUB[t] vs Si[t+lag])')
for lag in range(-12, 13):
    if lag >= 0:
        a = r_usd[:-lag] if lag else r_usd
        b = r_si[lag:] if lag else r_si
        # corr(USDRUB[t], Si[t+lag]) — USDRUB опережает на lag
        n = min(len(a), len(b))
        c = np.corrcoef(a[:n], b[:n])[0,1]
        tag = 'USDRUB->Si' if lag > 0 else ('синхрон' if lag == 0 else 'Si->USDRUB')
        if lag >= 0:
            print(f'lag={lag:+3d}ч ({tag}): corr={c:.4f}')
for lag in range(1, 13):
    a = r_usd[lag:]
    b = r_si[:-lag]
    n = min(len(a), len(b))
    c = np.corrcoef(a[:n], b[:n])[0,1]
    print(f'lag={-lag:+3d}ч (Si->USDRUB): corr={c:.4f}')
