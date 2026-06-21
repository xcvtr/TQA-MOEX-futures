#!/usr/bin/env python3
"""Stress-test портфеля divergence: slippage 0.05%, 0.1%, 0.2%.

Проверяет устойчивость стратегии к ухудшению условий исполнения.
"""
import subprocess, numpy as np

CH = "10.0.0.63"
DB = "moex_algopack_v2"
COMMISSION = 0.0005

TICKERS = ['AFKS', 'AFLT', 'CHMF']
CONFIGS = {'AFKS': (10, 10, 0.01), 'AFLT': (10, 10, 0.01), 'CHMF': (10, 10, 0.01)}

SLIPPAGE_VALUES = [0.0005, 0.001, 0.002]  # 0.05%, 0.1%, 0.2%


def ch(sql):
    r = subprocess.run(['clickhouse-client', '--host', CH, '-d', DB, '--query', sql],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0: raise Exception(r.stderr.strip())
    lines = r.stdout.strip().split('\n')
    return [l.split('\t') for l in lines if l.strip()]


def load(secid):
    sql = f"""SELECT o.put_orders_b, o.put_orders_s,
               t.pr_open, t.pr_close, t.pr_high, t.pr_low, t.trades_b, t.trades_s
        FROM orderstats_local o JOIN tradestats_local t
          ON o.tradedate = t.tradedate AND o.secid = t.ticker AND o.tradetime = t.tradetime
        WHERE o.secid = '{secid}' AND o.tradedate >= '2024-01-01' AND o.tradedate <= '2026-06-18'
        ORDER BY o.tradedate, o.tradetime FORMAT TabSeparated"""
    raw = ch(sql)
    if not raw or len(raw) < 500: return None
    n = len(raw)
    def ci(i): return np.array([int(r[i]) if r[i] and r[i] != '\\N' else 0 for r in raw])
    def cf(i): return np.array([float(r[i]) if r[i] and r[i] != '\\N' else 0.0 for r in raw])
    put_b = ci(0); put_s = ci(1)
    opn = cf(2); close = cf(3); high = cf(4); low = cf(5)
    tb = cf(6); ts = cf(7)
    tot_put = put_b + put_s
    o_imb = np.where(tot_put > 0, (put_b - put_s) / tot_put * 100, 0)
    t_imb = np.where((tb + ts) > 0, (tb - ts) / (tb + ts) * 100, 0)
    w = 5
    o_imb_sm = np.copy(o_imb)
    for i in range(w, n): o_imb_sm[i] = np.mean(o_imb[i-w:i])
    return dict(opn=opn, close=close, high=high, low=low,
                o_imb=o_imb_sm, t_imb=t_imb, n=n)


def run(data, div_thr, hold, stop, slippage, capital=100000.0):
    n = data['n']
    opn = data['opn']; close = data['close']; high = data['high']; low = data['low']
    o_imb = data['o_imb']; t_imb = data['t_imb']
    cash = float(capital); pos = 0; ep = 0.0; eb = 0; tc = 0; wc = 0
    eq = [cash]
    comm = 0.0005
    for i in range(10, n - 2):
        if pos != 0:
            if pos == 1 and low[i] <= ep * (1 - stop):
                cash *= (1 - stop - slippage - comm)
                tc += 1; pos = 0
            elif pos == -1 and high[i] >= ep * (1 + stop):
                cash *= (1 - stop - slippage - comm)
                tc += 1; pos = 0
            elif (i - eb) >= hold:
                ret = (close[i] / ep - 1) * pos
                cash *= (1 + ret - slippage - comm)
                if ret > 0: wc += 1
                tc += 1; pos = 0
        if pos == 0 and i > 10:
            o = o_imb[i]; t = t_imb[i]
            if abs(o - t) > div_thr:
                if t > abs(o) * 0.5 and t > 5:
                    pos = 1; ep = opn[i + 1]; eb = i + 1
                    cash *= (1 - slippage - comm)
                elif t < -abs(o) * 0.5 and t < -5:
                    pos = -1; ep = opn[i + 1]; eb = i + 1
                    cash *= (1 - slippage - comm)
        if pos == 1: eq.append(cash * close[i] / ep)
        elif pos == -1: eq.append(cash * ep / close[i])
        else: eq.append(cash)
    if pos != 0:
        ret = (close[-1] / ep - 1) * pos
        cash *= (1 + ret - slippage - comm)
        if ret > 0: wc += 1
        tc += 1
    eq.append(cash)
    tr = (cash / capital - 1) * 100
    ea = np.array(eq)
    pk = np.maximum.accumulate(ea)
    dd = np.max((pk - ea) / pk * 100)
    wr = wc / max(tc, 1) * 100
    return {'ret': tr, 'dd': dd, 'trades': tc, 'wr': wr, 'final': cash, 'eq': eq}


print("=== STRESS TEST: Divergence portfolio ===")
print(f"Base commission: {COMMISSION:.2%}\n")

# Load data
data_cache = {}
for t in TICKERS:
    d = load(t)
    if d: data_cache[t] = d
    print(f"  {t}: {d['n']} bars" if d else f"  {t}: NO DATA")

print(f"\n{'Slippage':>12} {'AFKS':>8} {'AFLT':>8} {'CHMF':>8} {'Portfolio':>10} {'DD':>8} {'Calmar':>8}")
print("-" * 70)

for slp in SLIPPAGE_VALUES:
    per_t = {}
    for t in TICKERS:
        dt, h, st = CONFIGS[t]
        r = run(data_cache[t], dt, h, st, slp)
        per_t[t] = r
    # Portfolio calc
    cpt = 100000.0 / len(per_t)
    pf = None
    ml = min(len(r['eq']) for r in per_t.values())
    for t in TICKERS:
        eq = np.array(per_t[t]['eq'][:ml])
        es = eq / eq[0] * cpt
        pf = es if pf is None else pf + es
    fv = pf[-1]
    rr = (fv / 100000.0 - 1) * 100
    pk2 = np.maximum.accumulate(pf)
    dd2 = np.max((pk2 - pf) / pk2 * 100)
    cm = rr / max(dd2, 0.01)
    
    print(f"  slp={slp:.2%}  {per_t['AFKS']['ret']:>+7.1f}% {per_t['AFLT']['ret']:>+7.1f}% {per_t['CHMF']['ret']:>+7.1f}% {rr:>+8.1f}%  {dd2:>5.1f}%  {cm:>6.1f}x")
    print()

print("═══ STRESS SUMMARY ═══")
print(f"{'Slippage':>12} {'Ret':>9} {'DD':>7} {'Calmar':>8} {'Final':>10}")
print("-" * 50)
for slp in SLIPPAGE_VALUES:
    per_t = {}
    for t in TICKERS:
        dt, h, st = CONFIGS[t]
        r = run(data_cache[t], dt, h, st, slp)
        per_t[t] = r
    cpt = 100000.0 / len(per_t)
    pf = None
    ml = min(len(r['eq']) for r in per_t.values())
    for t in TICKERS:
        eq = np.array(per_t[t]['eq'][:ml])
        es = eq / eq[0] * cpt
        pf = es if pf is None else pf + es
    fv = pf[-1]
    rr = (fv / 100000.0 - 1) * 100
    dd2 = np.max((np.maximum.accumulate(pf) - pf) / np.maximum.accumulate(pf) * 100)
    cm = rr / max(dd2, 0.01)
    print(f"  slp={slp:.2%}  {rr:>+7.1f}% {dd2:>6.1f}% {cm:>7.1f}x {fv:>9,.0f}")
