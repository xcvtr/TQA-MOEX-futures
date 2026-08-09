#!/usr/bin/env python3 -u
"""Базис-арбитраж Si/Eu ↔ перпетуалы (USDRUBF/EURRUBF/CNYRUBF).

Стратегия: SHORT-only mean-reversion базиса фьючерс−перпетуал.
- Базис = Si/1000 − USDRUBF (или Eu/1000 − EURRUBF, CNY − CNYRUBF)
- z-score базиса от MA(win), вход SHORT при z > zthr, выход при z ≤ 0 или hold
- ГО с КСУР (пониженное): Si 2676 + USDRUBF 2633 = 5309; Eu 3835+4493=8328; CNY 174+128=302
- Реинвест: risk% от equity, max_lots лимит
- Только H1, сессия 7-23ч IRK, фильтр битых баров (скачок >5%)

Usage: python3 scripts/basis_arb/backtest.py [--zthr 1.5] [--hold 168] [--risk 0.04] [--lots 30] [--pairs Si,Eu,CNY]
"""
import argparse
import clickhouse_connect as cc
import numpy as np
from collections import Counter

CH_HOST = '10.0.0.60'

# ГО с КСУР (medium × ksur из FINAM XLS, обновлено 09.08.2026)
PAIRS = {
    'Si':  ('USDRUBF', 1000.0, 5309, 60.0),
    'Eu':  ('EURRUBF', 1000.0, 8328, 60.0),
    'CNY': ('CNYRUBF', 1.0,     302, 60.0),
}

def load_h1_clean(ch, f, p):
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
                continue  # битый бар
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

def collect_trades(data, zthr, max_hold, sides='S'):
    """Собрать сделки по 1 лоту (для калибровки avg_loss)"""
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

def simulate(data, capital, zthr, max_hold, risk_pct, avg_loss, max_lots_abs, margin_cap=0.5, sides='S', pairs=None):
    equity = capital; peak = capital
    trades = []; positions = {}
    fs = [f for f in data if pairs is None or f in pairs]
    all_times = sorted(set().union(*[set(data[f]['ts']) for f in fs]))
    t_idx = {f: {t: i for i, t in enumerate(data[f]['ts'])} for f in fs}
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
        for f in fs:
            if f in positions: continue
            d = data[f]; i = t_idx[f].get(t)
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
            else:
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

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--zthr', type=float, default=1.5)
    ap.add_argument('--hold', type=int, default=168)
    ap.add_argument('--risk', type=float, default=0.04)
    ap.add_argument('--lots', type=int, default=30)
    ap.add_argument('--pairs', default='Si,Eu,CNY')
    ap.add_argument('--sides', default='S', choices=['S', 'L', 'both'])
    ap.add_argument('--capital', type=float, default=200000)
    args = ap.parse_args()

    ch = cc.get_client(host=CH_HOST, port=8123, database='moex')
    pairs = [p.strip() for p in args.pairs.split(',') if p.strip() in PAIRS]
    data = {}
    for f in pairs:
        p, scale, go, fee = PAIRS[f]
        ts, fu, pf = load_h1_clean(ch, f, p)
        basis = fu/scale - pf
        z = zscore(basis, 120)
        data[f] = {'ts': ts, 'basis': basis, 'z': z, 'go': go, 'fee': fee}
        print(f'{f}/{p}: n={len(ts)} corr={np.corrcoef(fu/scale, pf)[0,1]:.4f} базис mean={basis.mean():.3f} std={basis.std():.3f}')

    tr1 = collect_trades(data, args.zthr, args.hold, args.sides)
    avg_loss = {}
    for f, tr in tr1.items():
        pnls = np.array([t[3] for t in tr])
        losses = pnls[pnls < 0]
        avg_loss[f] = abs(losses.mean()) if len(losses) else 1000
        print(f'{f}: сигналов(1лот)={len(tr)} WR={(pnls>0).mean()*100:.0f}% avg_loss={avg_loss[f]:.0f}₽')

    eq, trades, mdd = simulate(data, args.capital, args.zthr, args.hold, args.risk, avg_loss, args.lots, sides=args.sides)
    pnls = np.array([t[2] for t in trades])
    years = 2.6
    cagr = (eq/args.capital)**(1/years) - 1 if eq > 0 else -1
    print(f'\n=== РЕЗУЛЬТАТ: z>{args.zthr} hold<={args.hold} risk={args.risk:.0%} lots<={args.lots} {args.sides} ===')
    print(f'N={len(pnls)} WR={(pnls>0).mean()*100:.0f}% eq={eq/1000:.0f}K CAGR={cagr*100:.0f}% DD={mdd*100:.0f}% Calmar={cagr/mdd if mdd else 0:.1f}')
    by_year = {}
    for t in trades:
        by_year.setdefault(t[0].year, []).append(t[2])
    for y, v in sorted(by_year.items()):
        print(f'  {y}: N={len(v)} net={sum(v)/1000:.0f}K')
    by_pair = {}
    for t in trades:
        by_pair.setdefault(t[1], []).append(t[2])
    for f, v in by_pair.items():
        print(f'  {f}: N={len(v)} net={sum(v)/1000:.0f}K')

if __name__ == '__main__':
    main()
