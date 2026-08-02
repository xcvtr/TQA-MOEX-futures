import sys, numpy as np, bisect
sys.path.insert(0, "/home/user/projects/TQA-MOEX-futures")
import clickhouse_connect as cc
import psycopg2
from datetime import timedelta
from collections import defaultdict

ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')

MAPPING = {
    'BR': 'BR', 'CR': 'CR', 'ED': 'ED', 'Eu': 'Eu', 'GD': 'GD', 'GZ': 'GZ',
    'MM': 'MM', 'NG': 'NG', 'RN': 'RN', 'SV': 'SV', 'Si': 'Si', 'X5': 'X5',
    'AF': 'AFLT', 'LK': 'LKOH', 'SR': 'SBRF', 'VB': 'VTBR', 'MG': 'MGNT',
    'HY': 'HYDR', 'SN': 'SNGP', 'NM': 'NOTK', 'SP': 'SBPR',
}

conn = psycopg2.connect(host='10.0.0.60', dbname='moex', user='postgres', connect_timeout=5)
cur = conn.cursor()
cur.execute("SELECT ticker, go, step_price, min_step, fee_entry FROM futures.ticker_specs")
specs = {r[0]: {'go': float(r[1]) if r[1] else 10000, 'sp': float(r[2]) if r[2] else 1.0,
                'ms': float(r[3]) if r[3] else 0.01, 'fee': float(r[4]) if r[4] else 4.0}
         for r in cur.fetchall()}
conn.close()

def load(futoi_t, mt5_t):
    """Загрузить futoi (MSK) и цены mt5 (IRK)."""
    oi_rows = ch.query(f"SELECT bt,buy_fiz,sell_fiz,buy_yur,sell_yur FROM moex.futoi WHERE ticker='{futoi_t}' AND bt>='2024-01-01' AND bt<='2026-07-18 23:59:59' ORDER BY bt").result_rows
    p_rows = ch.query(f"SELECT bt, prc FROM moex.mt5_continuous WHERE ticker='{mt5_t}' AND bt>='2024-01-01' ORDER BY bt").result_rows
    p_ts = [r[0] for r in p_rows]; p_prc = [float(r[1]) for r in p_rows]
    daily_open = {}
    for r in oi_rows:
        day = r[0].date()
        if day not in daily_open: daily_open[day] = int(r[1]) - int(r[2])
    return oi_rows, daily_open, p_ts, p_prc

def sweep(futoi_t, mt5_t, thr, hold_min=120):
    """Честный бэктест: futoi MSK + 5ч → цена IRK. Возврат (n, wr, pnl, top5)."""
    oi_rows, daily_open, p_ts, p_prc = load(futoi_t, mt5_t)
    s = specs.get(futoi_t) or specs.get(mt5_t) or {}
    ms = s.get('ms', 0.01); sp = s.get('sp', 1.0); fee = s.get('fee', 4.0)
    if not p_ts:
        return None
    pnls = []
    for i in range(len(oi_rows)):
        bt, fb, fs, yb, ys = oi_rows[i]
        day = bt.date()
        if day not in daily_open: continue
        h = bt.hour
        if h < 10 or h > 18: continue
        if bt.weekday() >= 5: continue
        total = fb+fs+yb+ys
        if total <= 0: continue
        day_net = ((fb-fs) - daily_open[day]) / total * 100
        if day_net > thr: continue
        irk = bt + timedelta(hours=5)
        idx = bisect.bisect_right(p_ts, irk) - 1
        if idx < 0: continue
        entry = p_prc[idx]
        ft2 = irk + timedelta(minutes=hold_min)
        j = bisect.bisect_left(p_ts, ft2)
        if j >= len(p_ts): continue
        exit_p = p_prc[j]
        pnl = (exit_p - entry) / ms * sp - fee
        pnls.append(pnl)
    if len(pnls) < 30:
        return None
    pnls = np.array(pnls)
    n = len(pnls)
    wr = (pnls > 0).mean() * 100
    total = pnls.sum()
    top5 = sum(sorted(pnls, reverse=True)[:5]) / total * 100 if total > 0 else 0
    return (n, wr, total, top5)

print(f"{'тикер':>6} {'mt5':>6} {'порог':>6} {'сдел':>6} {'WR':>6} {'PnL':>10} {'Top5':>6}")
results = []
for ft, mt in MAPPING.items():
    for thr in [-3.0, -5.0, -7.0, -10.0]:
        res = sweep(ft, mt, thr)
        if res:
            n, wr, total, top5 = res
            results.append((ft, mt, thr, n, wr, total, top5))
            print(f"{ft:>6} {mt:>6} {thr:>6.1f} {n:>6} {wr:>5.1f}% {total:>+10.0f} {top5:>5.1f}%")

print("\n=== ТОП по PnL (PF-подобное: n>100, PnL>0) ===")
for r in sorted([x for x in results if x[3] > 100 and x[5] > 0], key=lambda x: -x[5])[:15]:
    ft, mt, thr, n, wr, total, top5 = r
    print(f"  {ft:>5}→{mt:>5} thr={thr:>5.1f} n={n:>5} WR={wr:>5.1f}% PnL={total:>+9.0f} Top5={top5:>5.1f}%")

ch.close()
