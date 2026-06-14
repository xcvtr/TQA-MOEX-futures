#!/usr/bin/env python3
"""NM full sweep — both directions, chandelier, reinvest."""
import sys, os
sys.path.insert(0, '/home/user/projects/TQA-MOEX')
os.chdir('/home/user/projects/TQA-MOEX')
import numpy as np
import clickhouse_connect
from config import CH_HOST, CH_PORT, CH_DB
from datetime import datetime

ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)

CAPITAL = 200_000; COMM = 4; MAX_LOT = 5; RISK_PCT = 0.02; MAX_LEV = 3.0
CS = 10

PATTERNS = {
    'vol_up_oi_up_yb_up': lambda dv,dyb,dys,dfn,dtoi: dv>0 and dtoi>0 and dyb>0,
    'smart_money': lambda dv,dyb,dys,dfn,dtoi: dv>0 and dyb>0 and dfn<0,
    'vol_up_oi_down': lambda dv,dyb,dys,dfn,dtoi: dv>0 and dtoi<0,
    'vol_up_yb_down_fiz_up': lambda dv,dyb,dys,dfn,dtoi: dv>0 and dyb<0 and dfn>0,
    'fiz_extreme_vol_up': lambda dv,dyb,dys,dfn,dtoi: dv>0 and abs(dfn)>5,
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
dates = [str(r[0]) for r in rows]
opn=a[:,1].astype(float); high=a[:,2].astype(float); low=a[:,3].astype(float)
close=a[:,4].astype(float); vol=a[:,5].astype(float); yb=a[:,6].astype(float)
ys=a[:,7].astype(float); fb=a[:,8].astype(float); fs=a[:,9].astype(float); toi=a[:,10].astype(float)
toi=np.where(toi<=0,1,toi)
tr=np.zeros(len(close))
tr[1:]=np.maximum(high[1:]-low[1:],np.maximum(abs(high[1:]-close[:-1]),abs(low[1:]-close[:-1])))
atr=np.full(len(close),np.nan)
for i in range(14,len(close)): atr[i]=np.mean(tr[i-13:i+1])
v_m=np.mean(vol)+1; yb_m=np.mean(yb)+1; ys_m=np.mean(ys)+1; toi_m=np.mean(toi)+1
dv=np.diff(vol)/v_m; dyb=np.diff(yb)/yb_m; dys=np.diff(ys)/ys_m; dtoi=np.diff(toi)/toi_m
fiz_net=(fb-fs)/toi*100; dfn=np.diff(fiz_net)

N = len(dates)

def bt(configs):
    """configs: [(direction, pfunc, hold, atr_mult), ...]"""
    entries = []
    for direction, pfunc, hold, atr_mult in configs:
        for i in range(50, N - hold - 1):
            if i >= len(dv): break
            ep = float(opn[i+1])
            if not pfunc(dv[i], dyb[i], dys[i], dfn[i], dtoi[i]): continue
            if vol[i] < np.mean(vol[:i]) * 1.2: continue

            if direction == 'L':
                sp = ep*(1-min(max(atr[i]/ep*atr_mult,0.005),0.05)) if not np.isnan(atr[i]) else ep*0.95
                r_h = ep
                for j in range(i+1, min(i+hold+1, N)):
                    bh = float(high[j])
                    if bh > r_h:
                        r_h = bh
                        if not np.isnan(atr[j]): sp = max(sp, r_h*(1-min(max(atr[j]/r_h*atr_mult,0.005),0.05)))
                    if float(low[j]) <= sp:
                        entries.append((i+1, j, direction, ep, sp)); break
                else:
                    entries.append((i+1, min(i+hold,N-1), direction, ep, float(close[min(i+hold,N-1)])))
            else:
                sp = ep*(1+min(max(atr[i]/ep*atr_mult,0.005),0.05)) if not np.isnan(atr[i]) else ep*1.05
                r_l = ep
                for j in range(i+1, min(i+hold+1, N)):
                    bl = float(low[j])
                    if bl < r_l:
                        r_l = bl
                        if not np.isnan(atr[j]): sp = min(sp, r_l*(1+min(max(atr[j]/r_l*atr_mult,0.005),0.05)))
                    if float(high[j]) >= sp:
                        entries.append((i+1, j, direction, ep, sp)); break
                else:
                    entries.append((i+1, min(i+hold,N-1), direction, ep, float(close[min(i+hold,N-1)])))

    entries.sort(key=lambda x: x[0])
    eq = float(CAPITAL); peak = eq; mdd = 0.0; pnls = []
    n_sig = max(len(configs), 1)

    for entry_idx, exit_idx, direction, ep, xp in entries:
        go_val = ep * CS
        if go_val <= 0: continue
        sig_eq = eq / n_sig
        base_nc = max(1, int(sig_eq*RISK_PCT/(go_val*0.005)))
        nc = min(base_nc, MAX_LOT)
        max_by = int(eq*MAX_LEV/go_val) if go_val>0 else 99
        nc = min(nc, max_by)
        if nc < 1: continue

        pnl = nc*CS*(xp-ep)-nc*COMM if direction=='L' else nc*CS*(ep-xp)-nc*COMM
        eq += pnl; pnls.append(pnl)
        if eq > peak: peak = eq
        dd = (peak-eq)/peak*100 if peak>0 else 0
        mdd = max(mdd, dd)

    if not pnls: return None
    ret = (eq-CAPITAL)/CAPITAL*100
    wins = sum(1 for p in pnls if p>0)
    gp = sum(p for p in pnls if p>0); gl = sum(p for p in pnls if p<0)
    return dict(ret=round(ret,2), mdd=round(mdd,2), wr=round(wins/len(pnls)*100,1),
                pf=round(abs(gp/(gl+0.001)),2), tr=len(pnls),
                cal=round(ret/mdd,2) if mdd>0 else 0)

print(f"{'Pat':>22} {'Dir':>4} {'Hold':>4} {'AM':>4} {'Ret':>8} {'DD':>6} {'Calmar':>7} {'WR':>5} {'Tr':>4}")
print("-"*70)

best_both = []
for pname, pfunc in PATTERNS.items():
    for hold in [5, 8, 13, 21]:
        for am in [2, 3, 5]:
            r_l = bt([('L', pfunc, hold, am)])
            r_s = bt([('S', pfunc, hold, am)])
            r_b = bt([('L', pfunc, hold, am), ('S', pfunc, hold, am)])

            if r_l and r_s and r_b and r_l['tr']>=5 and r_s['tr']>=5:
                if r_l['ret'] > 0 and r_s['ret'] > 0:
                    print(f"{pname:>22} {'L+S':>4} {hold:>4} {am:>4} {r_b['ret']:>+7.1f}% {r_b['mdd']:>5.1f}% {r_b['cal']:>6.1f} {r_b['wr']:>4.0f}% {r_b['tr']:>4d}")
                    best_both.append((r_b, pname, hold, am))
                    if r_l['cal'] > 3:
                        print(f"{'':>22} {'L':>4} {hold:>4} {am:>4} {r_l['ret']:>+7.1f}% {r_l['mdd']:>5.1f}% {r_l['cal']:>6.1f} {r_l['wr']:>4.0f}% {r_l['tr']:>4d}")
                    if r_s['cal'] > 3:
                        print(f"{'':>22} {'S':>4} {hold:>4} {am:>4} {r_s['ret']:>+7.1f}% {r_s['mdd']:>5.1f}% {r_s['cal']:>6.1f} {r_s['wr']:>4.0f}% {r_s['tr']:>4d}")
                    print()

best_both.sort(key=lambda x: -x[0]['cal'])

print(f"\n{'='*70}")
print(f"TOP 5 BOTH (by Calmar)")
print(f"{'='*70}")
for r, pname, hold, am in best_both[:5]:
    print(f"{pname:>22} hold={hold} am={am}: ret={r['ret']:+.1f}% dd={r['mdd']:.1f}% calmar={r['cal']:.1f} wr={r['wr']:.0f}% tr={r['tr']}")
