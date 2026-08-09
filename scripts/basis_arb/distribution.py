import clickhouse_connect as cc
import numpy as np
from collections import Counter

ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')

PAIRS = [
    ('Si', 'USDRUBF', 1000.0, 5309, 60.0),
    ('Eu', 'EURRUBF', 1000.0, 8328, 60.0),
]

def load_h1_clean(f, p):
    def load(tk):
        rows = ch.query(f"""
            SELECT toStartOfHour(bt) h, argMax(prc, bt) prc
            FROM moex.mt5_continuous WHERE ticker='{tk}' AND bt >= '2024-01-01'
            GROUP BY h ORDER BY h
        """).result_rows
        out = {}
        prev = None
        for r in rows:
            t, pr = r[0], float(r[1])
            if prev is not None and abs(pr/prev - 1) > 0.05:
                continue
            out[t] = pr
            prev = pr
        return out
    fu = load(f); pf = load(p)
    common = sorted(set(fu) & set(pf))
    ts = [t for t in common if 7 <= t.hour <= 23]
    return ts, np.array([fu[t] for t in ts]), np.array([pf[t] for t in ts])

def zscore(arr, win):
    out = np.full(len(arr), np.nan)
    for i in range(win, len(arr)):
        w = arr[i-win:i]
        m, s = w.mean(), w.std()
        out[i] = (arr[i]-m)/s if s > 1e-9 else 0.0
    return out

data = {}
for f, p, scale, go, fee in PAIRS:
    ts, fu, pf = load_h1_clean(f, p)
    basis = fu/scale - pf
    z = zscore(basis, 120)
    data[f] = {'ts': ts, 'basis': basis, 'z': z, 'fee': fee}

# Собираем все SHORT сделки (z>3, hold 168) с деталями
trades = []
for f, d in data.items():
    z, basis, ts = d['z'], d['basis'], d['ts']
    n = len(z); i = 120
    while i < n:
        if np.isnan(z[i]): i += 1; continue
        if z[i] > 3.0:
            entry = basis[i]; ei = i; xi = None
            for j in range(i+1, min(i+168+1, n)):
                if not np.isnan(z[j]) and z[j] <= 0: xi = j; break
            if xi is None: xi = min(i+168, n-1)
            pnl = (entry - basis[xi]) * 1000.0 - d['fee']
            hold_h = (ts[xi] - ts[ei]).total_seconds() / 3600
            trades.append({
                'f': f, 'entry': ts[ei], 'exit': ts[xi],
                'pnl': pnl, 'hold_h': hold_h,
                'entry_z': z[ei], 'exit_z': z[xi],
                'entry_basis': entry, 'exit_basis': basis[xi],
            })
            i = xi + 1
        else: i += 1

print(f'Всего сделок: {len(trades)}')
pnls = np.array([t['pnl'] for t in trades])

# 1. По годам
print(f'\n=== ПО ГОДАМ ===')
by_year = Counter(t['entry'].year for t in trades)
for y in sorted(by_year):
    yt = [t for t in trades if t['entry'].year == y]
    p = np.array([t['pnl'] for t in yt])
    print(f'{y}: N={len(yt)} WR={(p>0).mean()*100:.0f}% net={p.sum()/1000:.0f}K avg={p.mean():.0f}₽')

# 2. По месяцам
print(f'\n=== ПО МЕСЯЦАМ (все годы) ===')
by_month = Counter(t['entry'].month for t in trades)
for m in sorted(by_month):
    mt = [t for t in trades if t['entry'].month == m]
    p = np.array([t['pnl'] for t in mt])
    print(f'{m:2d} мес: N={len(mt):2d} net={p.sum()/1000:6.0f}K avg={p.mean():7.0f}₽')

# 3. По часам входа (IRK)
print(f'\n=== ПО ЧАСУ ВХОДА (IRK+8) ===')
by_hour = Counter(t['entry'].hour for t in trades)
for h in sorted(by_hour):
    ht = [t for t in trades if t['entry'].hour == h]
    p = np.array([t['pnl'] for t in ht])
    print(f'{h:2d}:00 IRK ({h-5:2d}:00 МСК): N={len(ht):2d} avg={p.mean():7.0f}₽')

# 4. Hold
print(f'\n=== HOLD ===')
holds = np.array([t['hold_h'] for t in trades])
print(f'hold: min={holds.min():.0f}ч median={np.median(holds):.0f}ч max={holds.max():.0f}ч')
for h in [24, 48, 72, 96, 120, 144, 168]:
    cnt = (holds <= h).sum()
    print(f'  ≤{h}ч: {cnt}/{len(holds)} = {cnt/len(holds)*100:.0f}%')

# 5. PnL распределение
print(f'\n=== PNL РАСПРЕДЕЛЕНИЕ ===')
print(f'min={pnls.min():.0f} p25={np.percentile(pnls,25):.0f} med={np.median(pnls):.0f} p75={np.percentile(pnls,75):.0f} max={pnls.max():.0f}')
print(f'сумма={pnls.sum()/1000:.0f}K')
print(f'Топ-3 сделки:')
for t in sorted(trades, key=lambda x: -x['pnl'])[:3]:
    print(f'  {t["f"]} {t["entry"]} → {t["exit"]}: +{t["pnl"]:.0f}₽ (hold {t["hold_h"]:.0f}ч, z {t["entry_z"]:.1f}→{t["exit_z"]:.1f})')
print(f'Худшие 3:')
for t in sorted(trades, key=lambda x: x['pnl'])[:3]:
    print(f'  {t["f"]} {t["entry"]} → {t["exit"]}: {t["pnl"]:.0f}₽ (hold {t["hold_h"]:.0f}ч, z {t["entry_z"]:.1f}→{t["exit_z"]:.1f})')

# 6. По парам + z входа
print(f'\n=== ПО ПАРАМ И Z ВХОДА ===')
for f in data:
    ft = [t for t in trades if t['f'] == f]
    p = np.array([t['pnl'] for t in ft])
    zs = np.array([t['entry_z'] for t in ft])
    print(f'{f}: N={len(ft)} net={p.sum()/1000:.0f}K avg={p.mean():.0f}₽ z_входа: {zs.min():.1f}..{zs.max():.1f} (медиана {np.median(zs):.1f})')

# 7. Дни недели
print(f'\n=== ПО ДНЯМ НЕДЕЛИ ===')
by_dow = Counter(t['entry'].weekday() for t in trades)
names = ['Пн','Вт','Ср','Чт','Пт']
for d in sorted(by_dow):
    dt = [t for t in trades if t['entry'].weekday() == d]
    p = np.array([t['pnl'] for t in dt])
    print(f'{names[d]}: N={len(dt):2d} net={p.sum()/1000:6.0f}K avg={p.mean():7.0f}₽')
