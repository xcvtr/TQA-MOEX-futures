import clickhouse_connect as cc
import numpy as np

ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')

PAIRS = [
    ('Si', 'USDRUBF', 1000.0, 5309, 60.0),
    ('Eu', 'EURRUBF', 1000.0, 8328, 60.0),
]

def load_h1(f, p):
    rows = ch.query(f"""
        SELECT toStartOfHour(bt) h, argMax(prc, bt) prc
        FROM moex.mt5_continuous WHERE ticker='{f}' AND bt >= '2022-04-28'
        GROUP BY h ORDER BY h
    """).result_rows
    fu = {r[0]: float(r[1]) for r in rows}
    rows2 = ch.query(f"""
        SELECT toStartOfHour(bt) h, argMax(prc, bt) prc
        FROM moex.mt5_continuous WHERE ticker='{p}' AND bt >= '2022-04-28'
        GROUP BY h ORDER BY h
    """).result_rows
    pf = {r[0]: float(r[1]) for r in rows2}
    common = sorted(set(fu) & set(pf))
    ts = [t for t in common if 7 <= t.hour <= 23]
    basis = np.array([fu[t]/scale - pf[t] for t in ts])
    return ts, basis

def zscore(arr, win):
    out = np.full(len(arr), np.nan)
    for i in range(win, len(arr)):
        w = arr[i-win:i]
        m, s = w.mean(), w.std()
        out[i] = (arr[i]-m)/s if s > 1e-9 else 0.0
    return out

data = {}
for f, p, scale, go, fee in PAIRS:
    ts, basis = load_h1(f, p)
    z = zscore(basis, 120)
    data[f] = {'ts': ts, 'basis': basis, 'z': z, 'go': go, 'fee': fee}

def collect_trades(zthr, max_hold, sides='S'):
    out = {}
    for f, d in data.items():
        z, basis, ts = d['z'], d['basis'], d['ts']
        n = len(z); i = 120; tr = []
        while i < n:
            if np.isnan(z[i]): i += 1; continue
            if z[i] > zthr and sides in (None, 'S'):
                entry = basis[i]; ei = i; xi = None
                for j in range(i+1, min(i+max_hold+1, n)):
                    if not np.isnan(z[j]) and z[j] <= 0: xi = j; break
                if xi is None: xi = min(i+max_hold, n-1)
                tr.append((ts[ei], ts[xi], -1, (entry-basis[xi])*1000.0 - d['fee']))
                i = xi + 1
            elif z[i] < -zthr and sides == 'L':
                entry = basis[i]; ei = i; xi = None
                for j in range(i+1, min(i+max_hold+1, n)):
                    if not np.isnan(z[j]) and z[j] >= 0: xi = j; break
                if xi is None: xi = min(i+max_hold, n-1)
                tr.append((ts[ei], ts[xi], 1, (basis[xi]-entry)*1000.0 - d['fee']))
                i = xi + 1
            else: i += 1
        out[f] = tr
    return out

def simulate(capital, zthr, max_hold, risk_pct, avg_loss, max_lots_abs=30, margin_cap=0.5, sides='S'):
    equity = capital; peak = capital
    trades = []; positions = {}
    all_times = sorted(set().union(*[set(d['ts']) for d in data.values()]))
    t_idx = {f: {t: i for i, t in enumerate(d['ts'])} for f, d in data.items()}
    max_dd = 0.0
    for t in all_times:
        for f in list(positions):
            d = data[f]; i = t_idx[f].get(t); pos = positions[f]
            if i is not None:
                exit_cond = (d['z'][i] >= 0) if pos['dir'] == 1 else (d['z'][i] <= 0)
                if exit_cond or (i - pos['entry_i']) >= max_hold:
                    pnl = (d['basis'][i] - pos['entry_basis']) * pos['dir'] * 1000.0 * pos['lots'] - d['fee'] * pos['lots']
                    equity += pnl
                    trades.append((t, f, pnl, pos['lots']))
                    del positions[f]
        for f, d in data.items():
            if f in positions: continue
            i = t_idx[f].get(t)
            if i is None or i < 120 or np.isnan(d['z'][i]): continue
            loss = avg_loss.get(f, 1000)
            max_lots = max(1, int(equity * risk_pct / loss))
            max_lots = min(max_lots, max_lots_abs)
            max_lots = min(max_lots, max(1, int(equity * margin_cap / d['go'])))
            if sides == 'S':
                if d['z'][i] > zthr:
                    positions[f] = {'entry_i': i, 'entry_basis': d['basis'][i], 'dir': -1, 'lots': max_lots}
            elif sides == 'L':
                if d['z'][i] < -zthr:
                    positions[f] = {'entry_i': i, 'entry_basis': d['basis'][i], 'dir': 1, 'lots': max_lots}
        equity_mtm = equity
        for f, pos in positions.items():
            d = data[f]; i = t_idx[f].get(t)
            if i is not None:
                equity_mtm += (d['basis'][i] - pos['entry_basis']) * pos['dir'] * 1000.0 * pos['lots']
        peak = max(peak, equity_mtm)
        max_dd = max(max_dd, (peak-equity_mtm)/peak if peak else 0)
    for f, pos in positions.items():
        d = data[f]
        i = min(len(d['ts'])-1, pos['entry_i'] + max_hold)
        pnl = (d['basis'][i] - pos['entry_basis']) * pos['dir'] * 1000.0 * pos['lots'] - d['fee'] * pos['lots']
        equity += pnl
        trades.append((d['ts'][i], f, pnl, pos['lots']))
    return equity, trades, max_dd

# SHORT-only: скан zthr/hold/risk/lots
print('=== SHORT-only: скан ===')
results = []
for zthr in [2.5, 3.0, 3.5]:
    for hold in [96, 168, 240]:
        for rp in [0.03, 0.04]:
            for mla in [20, 30]:
                tr1 = collect_trades(zthr, hold, 'S')
                avg_loss = {}
                for f, tr in tr1.items():
                    pnls = np.array([t[3] for t in tr])
                    losses = pnls[pnls < 0]
                    avg_loss[f] = abs(losses.mean()) if len(losses) else 1000
                eq, tr, dd = simulate(200000, zthr, hold, rp, avg_loss, mla, 0.5, 'S')
                p = np.array([t[2] for t in tr])
                if not len(p): continue
                years = 4.33
                cagr = (eq/200000)**(1/years) - 1 if eq > 0 else -1
                if dd <= 0.20:
                    results.append((cagr/dd if dd else 0, cagr, dd, zthr, hold, rp, mla, len(p), (p>0).mean()))

results.sort(reverse=True)
print(f'Вариантов DD≤20%: {len(results)}')
print('ТОП-8 по Calmar:')
for cal, cagr, dd, zthr, hold, rp, mla, n, wr in results[:8]:
    print(f'z>{zthr} hold<={hold}h risk={rp:.0%} lots<={mla}: CAGR={cagr*100:.0f}% DD={dd*100:.0f}% Calmar={cal:.1f} N={n} WR={wr*100:.0f}%')

# Лучший: детальный разбор
zthr, hold, rp, mla = 3.0, 168, 0.04, 30
tr1 = collect_trades(zthr, hold, 'S')
avg_loss = {}
for f, tr in tr1.items():
    pnls = np.array([t[3] for t in tr])
    losses = pnls[pnls < 0]
    avg_loss[f] = abs(losses.mean()) if len(losses) else 1000
eq, tr, dd = simulate(200000, zthr, hold, rp, avg_loss, mla, 0.5, 'S')
p = np.array([t[2] for t in tr])
years = 4.33
cagr = (eq/200000)**(1/years) - 1
print(f'\n=== ДЕТАЛИ: S z>{zthr} hold<={hold} risk={rp:.0%} lots<={mla} ===')
print(f'eq={eq/1000:.0f}K CAGR={cagr*100:.0f}% DD={dd*100:.0f}% Calmar={cagr/dd:.1f} N={len(p)} WR={(p>0).mean()*100:.0f}%')
by_year = {}
for t in tr:
    by_year.setdefault(t[0].year, []).append(t[2])
print('По годам:')
for y, v in sorted(by_year.items()):
    print(f'  {y}: net={sum(v)/1000:.0f}K N={len(v)}')
by_pair = {}
for t in tr:
    by_pair.setdefault(t[1], []).append(t[2])
print('По парам:')
for f, v in by_pair.items():
    print(f'  {f}: net={sum(v)/1000:.0f}K N={len(v)}')
print(f'avg_lots={np.mean([t[3] for t in tr]):.0f} max_lots={max(t[3] for t in tr)}')
