#!/usr/bin/env python3
"""NM portfolio — all 5 both-direction strategies, chandelier, reinvest, yearly breakdown."""
import sys, os, json
sys.path.insert(0, '/home/user/projects/TQA-MOEX')
os.chdir('/home/user/projects/TQA-MOEX')
import numpy as np
import clickhouse_connect
from config import CH_HOST, CH_PORT, CH_DB

ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)

CAPITAL = 200_000; COMM = 4; MAX_LOT = 5; RISK_PCT = 0.02; MAX_LEV = 3.0
CS = 10

PATTERNS = {
    'vol_up_oi_up_yb_up': lambda dv,dyb,dys,dfn,dtoi: dv>0 and dtoi>0 and dyb>0,
    'smart_money': lambda dv,dyb,dys,dfn,dtoi: dv>0 and dyb>0 and dfn<0,
    'vol_up_yb_down_fiz_up': lambda dv,dyb,dys,dfn,dtoi: dv>0 and dyb<0 and dfn>0,
}

rows = ch.query("""
    SELECT toDate(p.time) as d, argMax(p.open,p.time), argMax(p.high,p.time),
           argMax(p.low,p.time), argMax(p.close,p.time), argMax(p.volume,p.time),
           argMax(o.yur_buy,p.time), argMax(o.yur_sell,p.time),
           argMax(o.fiz_buy,p.time), argMax(o.fiz_sell,p.time),
           argMax(o.total_oi,p.time)
    FROM moex.prices_5m p INNER JOIN moex.prices_5m_oi o ON p.symbol=o.symbol AND p.time=o.time
    WHERE p.symbol='NM' AND p.time>='2024-01-01' AND p.time<='2026-05-01'
    GROUP BY d ORDER BY d
""").result_rows

a = np.array([list(r) for r in rows], dtype=object)
dates = [str(r[0]) for r in rows]; N = len(dates)
opn=a[:,1].astype(float); high=a[:,2].astype(float); low=a[:,3].astype(float)
close=a[:,4].astype(float); vol=a[:,5].astype(float)
yb=a[:,6].astype(float); ys=a[:,7].astype(float)
fb=a[:,8].astype(float); fs=a[:,9].astype(float); toi=a[:,10].astype(float)
toi=np.where(toi<=0,1,toi)

tr=np.zeros(N)
tr[1:]=np.maximum(high[1:]-low[1:],np.maximum(abs(high[1:]-close[:-1]),abs(low[1:]-close[:-1])))
atr=np.full(N,np.nan)
for i in range(14,N): atr[i]=np.mean(tr[i-13:i+1])

v_m=np.mean(vol)+1; yb_m=np.mean(yb)+1; ys_m=np.mean(ys)+1; toi_m=np.mean(toi)+1
dv=np.diff(vol)/v_m; dyb=np.diff(yb)/yb_m; dys=np.diff(ys)/ys_m; dtoi=np.diff(toi)/toi_m
fiz_net=(fb-fs)/toi*100; dfn=np.diff(fiz_net)

# 5 strategies
STRATS = [
    ('vol_up_oi_up_yb_up', 'L', 8, 2),
    ('vol_up_oi_up_yb_up', 'S', 8, 2),
    ('vol_up_yb_down_fiz_up', 'L', 21, 2),
    ('vol_up_yb_down_fiz_up', 'S', 21, 2),
    ('smart_money', 'L', 13, 2),
    ('smart_money', 'S', 13, 2),
]

print(f"{'='*80}")
print(f"NM PORTFOLIO — 6 стратегий (3 паттерна × 2 направления)")
print(f"{'='*80}")
print(f"Capital: {CAPITAL:,}₽ | CS: {CS} | MAX_LOT: {MAX_LOT} | MAX_LEV: {MAX_LEV}x | Comm: {COMM}₽")
print(f"Period: {dates[0]} — {dates[-1]} ({N} trading days)")

# Collect entries for each strategy separately (for audit)
strat_entries = {s[0]+'_'+s[1]+'_'+str(s[2]): [] for s in STRATS}

for pname, direction, hold, am in STRATS:
    pfunc = PATTERNS[pname]
    for i in range(50, N - hold - 1):
        if i >= len(dv): break
        ep = float(opn[i+1])
        if not pfunc(dv[i], dyb[i], dys[i], dfn[i], dtoi[i]): continue
        if vol[i] < np.mean(vol[:i]) * 1.2: continue

        if direction == 'L':
            sp = ep*(1-min(max(atr[i]/ep*am,0.005),0.05)) if not np.isnan(atr[i]) else ep*0.95
            r_h = ep
            for j in range(i+1, min(i+hold+1, N)):
                bh = float(high[j])
                if bh > r_h:
                    r_h = bh
                    if not np.isnan(atr[j]): sp = max(sp, r_h*(1-min(max(atr[j]/r_h*am,0.005),0.05)))
                if float(low[j]) <= sp:
                    strat_entries[pname+'_L_'+str(hold)].append((i+1, j, 'L', ep, sp, dates[i+1], dates[j]))
                    break
            else:
                strat_entries[pname+'_L_'+str(hold)].append((i+1, min(i+hold,N-1), 'L', ep, float(close[min(i+hold,N-1)]), dates[i+1], dates[min(i+hold,N-1)]))
        else:
            sp = ep*(1+min(max(atr[i]/ep*am,0.005),0.05)) if not np.isnan(atr[i]) else ep*1.05
            r_l = ep
            for j in range(i+1, min(i+hold+1, N)):
                bl = float(low[j])
                if bl < r_l:
                    r_l = bl
                    if not np.isnan(atr[j]): sp = min(sp, r_l*(1+min(max(atr[j]/r_l*am,0.005),0.05)))
                if float(high[j]) >= sp:
                    strat_entries[pname+'_S_'+str(hold)].append((i+1, j, 'S', ep, sp, dates[i+1], dates[j]))
                    break
            else:
                strat_entries[pname+'_S_'+str(hold)].append((i+1, min(i+hold,N-1), 'S', ep, float(close[min(i+hold,N-1)]), dates[i+1], dates[min(i+hold,N-1)]))

# Audit per strategy
print(f"\n--- AUDIT PER STRATEGY ---")
total_entries = 0
for key, entries in strat_entries.items():
    total_entries += len(entries)
    print(f"  {key:40s}: {len(entries)} raw signals")

print(f"\nTotal raw signals: {total_entries}")

# Merge all entries, sort by entry date
all_entries = []
for key, entries in strat_entries.items():
    for e in entries:
        all_entries.append(e)

all_entries.sort(key=lambda x: x[0])
print(f"Sorted entries: {len(all_entries)}")

# Run simulation with reinvestment (step-by-step audit)
eq = float(CAPITAL)
peak = eq
mdd = 0.0
trades_log = []
eq_curve = [(0, CAPITAL, dates[0])]
n_strats = len(STRATS)

for idx, (entry_idx, exit_idx, direction, ep, xp, entry_date, exit_date) in enumerate(all_entries):
    go_val = ep * CS
    if go_val <= 0: continue

    # Sizing: equal allocation per strategy
    sig_eq = eq / n_strats
    base_nc = max(1, int(sig_eq * RISK_PCT / (go_val * 0.005)))
    nc = min(base_nc, MAX_LOT)
    max_by = int(eq * MAX_LEV / go_val) if go_val > 0 else 99
    nc = min(nc, max_by)
    if nc < 1: continue

    if direction == 'L':
        pnl = nc * CS * (xp - ep) - nc * COMM
    else:
        pnl = nc * CS * (ep - xp) - nc * COMM

    eq += pnl
    peak = max(peak, eq)

    if peak > 0:
        dd = (peak - eq) / peak * 100
    else:
        dd = 0.0
    mdd = max(mdd, dd)

    trades_log.append({
        '#': idx + 1, 'entry': entry_date, 'exit': exit_date,
        'dir': direction, 'ep': round(ep,2), 'xp': round(xp,2),
        'nc': nc, 'pnl': round(pnl,0), 'eq': round(eq,0), 'dd%': round(dd,2)
    })
    eq_curve.append((idx+1, eq, exit_date))

# Final report
ret = (eq - CAPITAL) / CAPITAL * 100
wins = sum(1 for t in trades_log if t['pnl'] > 0)
wr = wins / len(trades_log) * 100 if trades_log else 0
gp = sum(t['pnl'] for t in trades_log if t['pnl'] > 0)
gl_ = sum(t['pnl'] for t in trades_log if t['pnl'] < 0)
pf = abs(gp / (gl_ + 0.001))

print(f"\n{'='*80}")
print(f"FINAL RESULTS")
print(f"{'='*80}")
print(f"Return:  {ret:+.1f}%")
print(f"Max DD:  {mdd:.1f}%")
print(f"Calmar:  {ret/mdd:.1f}" if mdd > 0 else "Calmar:  N/A")
print(f"WinRate: {wr:.0f}% ({wins}/{len(trades_log)})")
print(f"Profit Factor: {pf:.2f}")
print(f"Total trades: {len(trades_log)}")
print(f"Final equity: {round(eq,0):,.0f}₽")
print(f"Net PnL: {round(eq-CAPITAL,0):,.0f}₽")

# Yearly breakdown
print(f"\n--- YEARLY BREAKDOWN ---")
yearly = {}
for t in trades_log:
    year = t['entry'][:4]
    if year not in yearly:
        yearly[year] = {'pnl': 0, 'wins': 0, 'total': 0}
    yearly[year]['pnl'] += t['pnl']
    yearly[year]['total'] += 1
    if t['pnl'] > 0:
        yearly[year]['wins'] += 1

for year in sorted(yearly.keys()):
    y = yearly[year]
    cap = CAPITAL if year == '2024' else CAPITAL * (1 + yearly.get('2024', {}).get('pnl', 0) / CAPITAL)
    yr = y['pnl'] / cap * 100
    print(f"  {year}: {y['pnl']:>+10,.0f}₽ ({yr:+.1f}%) | WR: {y['wins']/y['total']*100:.0f}% | {y['total']} trades")

# Trade list (last 20)
print(f"\n--- LAST 20 TRADES ---")
print(f"{'#':>3} {'Entry':>10} {'Exit':>10} {'Dir':>3} {'Entry₽':>8} {'Exit₽':>8} {'Cnt':>4} {'PnL':>8} {'DD%':>6}")
for t in trades_log[-20:]:
    print(f"{t['#']:>3} {t['entry']:>10} {t['exit']:>10} {t['dir']:>3} {t['ep']:>8} {t['xp']:>8} {t['nc']:>4} {t['pnl']:>+8,.0f} {t['dd%']:>5.1f}%")

# Save to JSON
report = {
    'config': {'capital': CAPITAL, 'cs': CS, 'max_lot': MAX_LOT, 'max_lev': MAX_LEV, 'comm': COMM},
    'strategies': [{'pattern': s[0], 'dir': s[1], 'hold': s[2], 'atr': s[3]} for s in STRATS],
    'results': {
        'return_pct': ret, 'mdd_pct': mdd, 'calmar': round(ret/mdd,2) if mdd>0 else 0,
        'wr_pct': wr, 'trades': len(trades_log), 'final_equity': round(eq,0),
        'net_pnl': round(eq-CAPITAL,0), 'profit_factor': pf
    },
    'yearly': {y: {'pnl': d['pnl'], 'trades': d['total'], 'wr': d['wins']/d['total']*100}
               for y,d in sorted(yearly.items())},
    'trades': trades_log
}
with open('reports/triz_phase4/nm_portfolio/result.json', 'w') as f:
    json.dump(report, f, indent=2)
print(f"\nReport saved to reports/triz_phase4/nm_portfolio/result.json")
