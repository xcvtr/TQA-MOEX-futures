import clickhouse_connect as cc
import numpy as np

ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')

# ГО с КСУР (medium × ksur):
# Si 2676, USDRUBF 2633, Eu 39714 (нет в MAP — без КСУР? проверим), EURRUBF 4493, CNY 174, CNYRUBF 128
PAIRS = [
    # (фьючерс, перпетуал, scale, go_pair_ksur, fee)
    ('Si', 'USDRUBF', 1000.0, 2676+2633, 60.0),
    ('Eu', 'EURRUBF', 1000.0, 39714+4493, 60.0),
    ('CNY', 'CNYRUBF', 1000.0, 174+128, 60.0),
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
    print(f'{f}/{p}: n={len(ts)} go_ksur={go}')

def simulate(capital=200000, zthr=2.0, max_hold=72, risk_pct=0.10):
    """risk_pct = % капитала на маржу пары (сколько лотов можно)"""
    equity = capital; peak = capital
    trades = []; positions = {}
    all_times = sorted(set().union(*[set(d['ts']) for d in data.values()]))
    t_idx = {f: {t: i for i, t in enumerate(d['ts'])} for f, d in data.items()}
    max_dd = 0.0
    for t in all_times:
        for f in list(positions):
            d = data[f]; i = t_idx[f].get(t); pos = positions[f]
            if i is not None:
                hold = i - pos['entry_i']
                exit_cond = (d['z'][i] >= 0) if pos['dir'] == 1 else (d['z'][i] <= 0)
                if exit_cond or hold >= max_hold:
                    pnl = (d['basis'][i] - pos['entry_basis']) * pos['dir'] * 1000.0 * pos['lots'] - d['fee'] * pos['lots']
                    equity += pnl
                    trades.append((t, f, pnl, pos['lots']))
                    del positions[f]
        for f, d in data.items():
            if f in positions: continue
            i = t_idx[f].get(t)
            if i is None or i < 120 or np.isnan(d['z'][i]): continue
            max_lots = max(1, int(equity * risk_pct / d['go']))
            if d['z'][i] < -zthr:
                positions[f] = {'entry_i': i, 'entry_basis': d['basis'][i], 'dir': 1, 'lots': max_lots}
            elif d['z'][i] > zthr:
                positions[f] = {'entry_i': i, 'entry_basis': d['basis'][i], 'dir': -1, 'lots': max_lots}
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

print(f'\n=== ПОРТФЕЛЬ с КСУР-ГО: common pool 200K ===')
for zthr in [1.5, 2.0, 2.5]:
    for hold in [48, 72]:
        for rp in [0.05, 0.10, 0.20]:
            eq, trades, mdd = simulate(capital=200000, zthr=zthr, max_hold=hold, risk_pct=rp)
            pnls = np.array([t[2] for t in trades])
            if not len(pnls): continue
            years = 4.33
            cagr = (eq/200000)**(1/years) - 1 if eq > 0 else -1
            avg_lots = np.mean([t[3] for t in trades])
            print(f'z>{zthr} hold<={hold} risk={rp:.0%}: N={len(pnls)} WR={(pnls>0).mean()*100:.0f}% eq={eq/1000:.0f}K CAGR={cagr*100:.0f}% DD={mdd*100:.0f}% avg_lots={avg_lots:.1f}')
