#!/usr/bin/env python3 -u
"""Верификация Dragon GD 10m: воспроизведение + OOS по годам.

Заявлено (new-portfolio-results.md, 180 дней янв-июль 2026):
  Dragon GD 10m risk=7%: 131 сделок, WR 58.0%, PF 3.59, PnL +736K

Проверяем на mt5_continuous (полные данные):
  1) тот же период янв-июль 2026 (воспроизведение)
  2) OOS по годам 2022-2026
"""
import sys, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
import numpy as np, clickhouse_connect as cc
from strategies.dragon.prod.engine import check_signal as dragon_check

ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')

# GD spec (portfolio_run.py CONFIGS)
GO, MS, SP = 54380, 0.1, 7.84756
TA, TT, SL, TO = 0.015, 0.005, 0.01, 60
TC = 4
PARAMS = {'impulse_pct': 0.3, 'retrace_max_pct': 70, 'hump_extension': 0.1, 'lookback': 100}

def load_m1(since, till='2027-01-01', table='mt5_continuous'):
    rows = ch.query(f"SELECT bt,opn,hi,lo,prc,vol FROM moex.{table} "
                    f"WHERE ticker='GD' AND bt>='{since}' AND bt<'{till}' ORDER BY bt").result_rows
    bars = []
    for r in rows:
        ts = r[0]; h, m = ts.hour, ts.minute
        if ts.weekday() >= 5: continue
        if h < 15 or h > 23 or (h == 23 and m > 45): continue
        bars.append({'ts': ts, 'opn': float(r[1]), 'hi': float(r[2]),
                     'lo': float(r[3]), 'prc': float(r[4]), 'vol': float(r[5])})
    return bars

def resample_n(m1, n):
    g = {}
    for b in m1:
        tm = b['ts'].hour * 60 + b['ts'].minute; km = (tm // n) * n
        k = b['ts'].replace(minute=km % 60, hour=km // 60, second=0)
        if k not in g:
            g[k] = {'ts': k, 'opn': b['opn'], 'hi': b['hi'], 'lo': b['lo'], 'prc': b['prc']}
        else:
            gg = g[k]; gg['hi'] = max(gg['hi'], b['hi']); gg['lo'] = min(gg['lo'], b['lo']); gg['prc'] = b['prc']
    return sorted(g.values(), key=lambda x: x['ts'])

def backtest(bars, risk, tf=10):
    dbars = resample_n(bars, tf)
    d2m = {}; di = 0
    for mi in range(len(bars)):
        if di < len(dbars) and bars[mi]['ts'] >= dbars[di]['ts']:
            d2m[di] = mi; di += 1
    eq = 200000.0; peak = eq; mtm_pk = eq; cdd = mdd = 0
    pos = None; tr = []
    df = set(); dxi = 0
    n = len(bars)
    for mi in range(80, n):
        b = bars[mi]
        if pos:
            ex = None
            slv = pos['ep'] * (1 - SL) if pos['dir'] == 'long' else pos['ep'] * (1 + SL)
            if (pos['dir'] == 'long' and b['lo'] <= slv) or (pos['dir'] == 'short' and b['hi'] >= slv):
                ex = slv
            if not ex:
                if not pos.get('tr'):
                    act = pos['ep'] * (1 + TA) if pos['dir'] == 'long' else pos['ep'] * (1 - TA)
                    if (pos['dir'] == 'long' and b['hi'] >= act) or (pos['dir'] == 'short' and b['lo'] <= act):
                        pos['tr'] = True
                        pos['tl'] = b['hi'] * (1 - TT) if pos['dir'] == 'long' else b['lo'] * (1 + TT)
                if pos.get('tr') and ((pos['dir'] == 'long' and b['lo'] <= pos['tl']) or
                                      (pos['dir'] == 'short' and b['hi'] >= pos['tl'])):
                    ex = pos['tl']
            if not ex and mi - pos['bi'] >= TO:
                ex = b['prc']
            if ex:
                pnl = (ex - pos['ep']) / MS * SP * (-1 if pos['dir'] == 'short' else 1) * pos['shares'] - TC * pos['shares']
                eq += pnl; tr.append(pnl)
                peak = max(peak, eq); cdd = max(cdd, (peak - eq) / peak * 100)
                pos = None
        if not pos and dxi < len(dbars) and dxi not in df and mi >= d2m.get(dxi, 999999999):
            df.add(dxi)
            db = dbars[dxi]
            dh = dbars[max(0, dxi - 130):dxi]
            if len(dh) >= 30:
                # bars_list: прошлые бары + текущий (как в portfolio_run: dh + [db])
                bars_list = dh + [db]
                bd = {'prc': db['prc'], 'hi': db['hi'], 'lo': db['lo'],
                      'bars_list': bars_list}
                sig = dragon_check(bd, 'GD', PARAMS)
                if sig:
                    sh = max(1, int(eq * risk / GO))
                    b_vol = b['vol'] if mi < n else 999999
                    if b_vol > 0:
                        sh = min(sh, max(1, int(b_vol * 0.1)))
                    if GO * sh <= eq:
                        slip = MS
                        ep2 = sig['entry_price'] + (slip if sig['direction'] == 'long' else -slip)
                        pos = {'dir': sig['direction'], 'ep': ep2, 'bi': mi, 'shares': sh, 'tr': False}
            dxi += 1
        fl = (b['prc'] - pos['ep']) / MS * SP * (-1 if pos['dir'] == 'short' else 1) * pos['shares'] if pos else 0
        mv = eq + fl; mtm_pk = max(mtm_pk, mv)
        mdd = max(mdd, (mtm_pk - mv) / mtm_pk * 100) if mtm_pk > 0 else 0
    if not tr:
        return None
    tr = np.array(tr)
    w = tr[tr > 0]; l = tr[tr <= 0]
    wr = len(w) / len(tr) * 100
    pf = sum(w) / sum(abs(l)) if len(l) else 0
    rt = (eq - 200000) / 200000 * 100
    return dict(n=len(tr), wr=wr, pf=pf, roi=rt, mdd=mdd, final=eq,
                wins=len(w), losses=len(l), avg=float(tr.mean()), pnl_sum=float(tr.sum()))

# === 1) воспроизведение: янв-июль 2026 ===
bars = load_m1('2026-01-01', '2026-08-01')
print(f"GD M1 mt5_continuous янв-июль 2026: {len(bars)} баров")
res = backtest(bars, 0.07)
if res:
    print(f"ВОСПРОИЗВЕДЕНИЕ risk=7%: n={res['n']} WR={res['wr']:.1f}% ROI={res['roi']:+.1f}% "
          f"PF={res['pf']:.2f} MDD={res['mdd']:.1f}% PnL={res['pnl_sum']:+,.0f}₽")
    print(f"  (заявлено: n=131 WR=58.0% PF=3.59 PnL=+736K)")
else:
    print("НЕТ СДЕЛОК ❌")

# === 2) OOS по годам ===
print("\n=== OOS по годам (mt5_continuous, риск 7%) ===")
print(f"{'Период':<22}{'n':>6}{'WR%':>7}{'ROI%':>9}{'PF':>6}{'MDD%':>7}{'PnL₽':>12}")
print("-" * 70)
for label, lo, hi in [("2022", '2022-01-01', '2023-01-01'),
                      ("2023", '2023-01-01', '2024-01-01'),
                      ("2024", '2024-01-01', '2025-01-01'),
                      ("2025", '2025-01-01', '2026-01-01'),
                      ("2026 H1 (in-sample)", '2026-01-01', '2026-08-01')]:
    seg = load_m1(lo, hi)
    r = backtest(seg, 0.07)
    if r:
        print(f"{label:<22}{r['n']:>6}{r['wr']:>7.1f}{r['roi']:>+9.1f}{r['pf']:>6.2f}{r['mdd']:>7.1f}{r['pnl_sum']:>12,.0f}")
    else:
        print(f"{label:<22}{'нет сделок':>20}")

# === 3) полная история ===
print("\n=== Полная история 2022-2026 (риск 7%) ===")
seg = load_m1('2022-01-01', '2027-01-01')
r = backtest(seg, 0.07)
if r:
    print(f"n={r['n']} WR={r['wr']:.1f}% ROI={r['roi']:+.1f}% PF={r['pf']:.2f} MDD={r['mdd']:.1f}% "
          f"PnL={r['pnl_sum']:+,.0f}₽ win/loss={r['wins']}/{r['losses']}")
ch.close()
