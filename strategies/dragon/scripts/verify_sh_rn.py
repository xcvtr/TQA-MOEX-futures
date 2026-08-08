#!/usr/bin/env python3 -u
"""Верификация SH RN 1m: воспроизведение + OOS по годам на полной истории.

Заявлено (new-portfolio-results.md, 180 дней янв-июль 2026, mt5_bars):
  SH RN 1m risk=5%: 1522 сделок, WR 40.7%, PF 10.22, PnL +3,024K

Проверяем на mt5_continuous (полные данные):
  1) тот же период янв-июль 2026 (воспроизведение)
  2) OOS по годам 2022-2026
  3) look-ahead: lo_hist/hi_hist без текущего бара
"""
import sys, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
import numpy as np, clickhouse_connect as cc
from strategies.stop_hunt.prod.engine import check_signal as sh_check

ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')

GO, MS, SP = 4002, 1.0, 1.0
TA, TT, SL, TO = 0.005, 0.003, 0.007, 12
TC = 4
LB, RETR = 60, 0.05

def load_m1(since, till='2027-01-01', table='mt5_continuous'):
    rows = ch.query(f"SELECT bt,opn,hi,lo,prc,vol FROM moex.{table} "
                    f"WHERE ticker='RN' AND bt>='{since}' AND bt<'{till}' ORDER BY bt").result_rows
    bars = []
    for r in rows:
        ts = r[0]; h, m = ts.hour, ts.minute
        if ts.weekday() >= 5: continue
        if h < 15 or h > 23 or (h == 23 and m > 45): continue
        bars.append({'ts': ts, 'opn': float(r[1]), 'hi': float(r[2]),
                     'lo': float(r[3]), 'prc': float(r[4]), 'vol': float(r[5])})
    return bars

def backtest(bars, risk):
    eq = 200000.0; peak = eq; mtm_pk = eq; cdd = mdd = 0
    pos = None; tr = []
    n = len(bars)
    for mi in range(80, n):
        b = bars[mi]
        # tick first
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
        # detect: каждый бар (1m detect = тот же бар)
        if not pos:
            dh = bars[max(0, mi - LB - 2):mi]
            if len(dh) >= LB:
                bd = {'prc': b['prc'], 'hi': b['hi'], 'lo': b['lo'],
                      'lo_hist': [x['lo'] for x in dh[-LB:]],
                      'hi_hist': [x['hi'] for x in dh[-LB:]]}
                sig = sh_check(bd, 'RN', {'lookback': LB, 'retrace': RETR})
                if sig:
                    sh = max(1, int(eq * risk / GO))
                    b_vol = b['vol'] if mi < n else 999999
                    if b_vol > 0:
                        sh = min(sh, max(1, int(b_vol * 0.1)))
                    if GO * sh <= eq:
                        ep2 = sig['entry_price'] + (1 if sig['direction'] == 'long' else -1)
                        pos = {'dir': sig['direction'], 'ep': ep2, 'bi': mi, 'shares': sh, 'tr': False}
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

# === 1) воспроизведение: янв-июль 2026, mt5_continuous ===
bars = load_m1('2026-01-01', '2026-08-01')
print(f"RN M1 mt5_continuous янв-июль 2026: {len(bars)} баров")
res = backtest(bars, 0.05)
if res:
    print(f"ВОСПРОИЗВЕДЕНИЕ risk=5%: n={res['n']} WR={res['wr']:.1f}% ROI={res['roi']:+.1f}% "
          f"PF={res['pf']:.2f} MDD={res['mdd']:.1f}% PnL={res['pnl_sum']:+,.0f}₽")
    print(f"  (заявлено: n=1522 WR=40.7% PF=10.22 PnL=+3,024K)")
else:
    print("НЕТ СДЕЛОК ❌")

# === 2) OOS по годам ===
print("\n=== OOS по годам (mt5_continuous, риск 5%) ===")
print(f"{'Период':<22}{'n':>6}{'WR%':>7}{'ROI%':>9}{'PF':>6}{'MDD%':>7}{'PnL₽':>12}")
print("-" * 70)
for label, lo, hi in [("2022", '2022-01-01', '2023-01-01'),
                      ("2023", '2023-01-01', '2024-01-01'),
                      ("2024", '2024-01-01', '2025-01-01'),
                      ("2025", '2025-01-01', '2026-01-01'),
                      ("2026 H1 (in-sample)", '2026-01-01', '2026-08-01')]:
    seg = load_m1(lo, hi)
    r = backtest(seg, 0.05)
    if r:
        print(f"{label:<22}{r['n']:>6}{r['wr']:>7.1f}{r['roi']:>+9.1f}{r['pf']:>6.2f}{r['mdd']:>7.1f}{r['pnl_sum']:>12,.0f}")
    else:
        print(f"{label:<22}{'нет сделок':>20}")

# === 3) полная история ===
print("\n=== Полная история 2022-2026 (риск 5%) ===")
seg = load_m1('2022-01-01', '2027-01-01')
r = backtest(seg, 0.05)
if r:
    print(f"n={r['n']} WR={r['wr']:.1f}% ROI={r['roi']:+.1f}% PF={r['pf']:.2f} MDD={r['mdd']:.1f}% "
          f"PnL={r['pnl_sum']:+,.0f}₽ win/loss={r['wins']}/{r['losses']}")
ch.close()
