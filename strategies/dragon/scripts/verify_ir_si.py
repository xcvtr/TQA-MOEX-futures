#!/usr/bin/env python3 -u
"""Верификация IR Si: воспроизведение sweep + OOS по годам на полной истории.

1) Воспроизводим sweep_full_results.txt (Si 1m, 2025-07-16+, risk 2%, TC=4):
   ожидаем n=152 WR=61.2% ROI=+614.1% PF=3.44 MDD=9.2%
2) Тот же бэктест на полной истории mt5_continuous (2020-2026) по годам:
   стабилен ли edge (OOS: 2022/2023/2024 до 2025-07).
3) Проверка look-ahead: сигнал по бару i, вход с этого же бара.
"""
import sys, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
import numpy as np, clickhouse_connect as cc
from strategies.impulse_return.prod.engine import check_signal as ir_check, reset_state

ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')

TA, TT, SL, TO = 0.005, 0.003, 0.007, 12
TC = 4
MS, SP, GO = 1.0, 1.0, 6453

def load_m1(since, table='mt5_continuous'):
    rows = ch.query(f"SELECT bt,opn,hi,lo,prc,vol FROM moex.{table} "
                    f"WHERE ticker='Si' AND bt>='{since}' ORDER BY bt").result_rows
    bars = []
    for r in rows:
        ts = r[0]; h, m = ts.hour, ts.minute
        if ts.weekday() >= 5: continue
        if h < 15 or h > 23 or (h == 23 and m > 45): continue
        bars.append({'ts': ts, 'opn': float(r[1]), 'hi': float(r[2]),
                     'lo': float(r[3]), 'prc': float(r[4]), 'vol': float(r[5])})
    return bars

def backtest(bars, risk, dbg=False):
    """Точная копия логики ir_si_sweep.py."""
    reset_state()
    eq = 200000.0; peak = eq; mtm_pk = eq; cdd = mdd = 0
    pos = None; tr = []; df = set(); dxi = 0
    par = {'impulse_bars': 12, 'impulse_pct': 0.3, 'cooldown': 12, 'min_vol_pct': 0}
    # detect = 1m (тот же бар)
    n = len(bars)
    for mi in range(60, n):
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
        if not pos and dxi < n and dxi not in df and mi >= dxi:
            df.add(dxi)
            # движку нужны только последние ~30 баров (imp_bars+1 для close_hist,
            # медиана vol_hist=100 константа при min_vol_pct=0)
            dh = bars[max(0, dxi - 30):dxi]
            if len(dh) >= 20:
                bd = {'prc': b['prc'], 'hi': b['hi'], 'lo': b['lo'], 'vol': 100,
                      'bars_list': dh, 'lo_hist': [x['lo'] for x in dh],
                      'hi_hist': [x['hi'] for x in dh], 'close_hist': [x['prc'] for x in dh],
                      'vol_hist': [100] * len(dh)}
                sig = ir_check(bd, 'Si', par)
                if sig:
                    sh = max(1, int(eq * risk / GO))
                    b_vol = b['vol'] if mi < n else 999999
                    if b_vol > 0:
                        sh = min(sh, max(1, int(b_vol * 0.1)))
                    if GO * sh <= eq:
                        slip = 1
                        ep2 = sig['entry_price'] + (slip if sig['direction'] == 'long' else -slip)
                        pos = {'dir': sig['direction'], 'ep': ep2, 'bi': mi, 'shares': sh, 'tr': False}
            dxi += 1
        fl = (b['prc'] - pos['ep']) / MS * SP * (-1 if pos['dir'] == 'short' else 1) * pos['shares'] if pos else 0
        mv = eq + fl
        mtm_pk = max(mtm_pk, mv)
        mdd = max(mdd, (mtm_pk - mv) / mtm_pk * 100) if mtm_pk > 0 else 0
    if not tr:
        return None
    tr = np.array(tr)
    w = tr[tr > 0]; l = tr[tr <= 0]
    wr = len(w) / len(tr) * 100
    pf = sum(w) / sum(abs(l)) if len(l) else 0
    rt = (eq - 200000) / 200000 * 100
    return dict(n=len(tr), wr=wr, pf=pf, roi=rt, mdd=mdd, final=eq,
                wins=len(w), losses=len(l), avg=float(tr.mean()), med=float(np.median(tr)))

# === 1) Воспроизведение sweep (2025-07-16+, таблица mt5_bars КАК В ОРИГИНАЛЕ) ===
bars = load_m1('2025-07-16', table='mt5_bars')
print(f"Si M1 mt5_bars с 2025-07-16: {len(bars)} баров")
res = backtest(bars, 0.02)
if res:
    print(f"ВОСПРОИЗВЕДЕНИЕ risk=2%: n={res['n']} WR={res['wr']:.1f}% ROI={res['roi']:+.1f}% "
          f"PF={res['pf']:.2f} MDD={res['mdd']:.1f}%  (ожидалось: n=152 WR=61.2% ROI=+614.1% PF=3.44 MDD=9.2%)")
    ok = (abs(res['n'] - 152) <= 3 and abs(res['wr'] - 61.2) < 2 and abs(res['roi'] - 614.1) < 40)
    print("ВОСПРОИЗВОДИМО ✅" if ok else "РАСХОЖДЕНИЕ ❌")
else:
    print("НЕТ СДЕЛОК ❌")

# === 2) OOS по годам на полной истории ===
print("\n=== OOS по годам (mt5_continuous, риск 2%) ===")
print(f"{'Период':<22}{'n':>5}{'WR%':>7}{'ROI%':>9}{'PF':>6}{'MDD%':>7}{'avg₽':>9}")
print("-" * 65)
all_bars = load_m1('2020-01-01')
print(f"Всего M1 баров (2020+): {len(all_bars)}")
for label, lo, hi in [("2022", '2022-01-01', '2023-01-01'),
                      ("2023", '2023-01-01', '2024-01-01'),
                      ("2024", '2024-01-01', '2025-01-01'),
                      ("2025 H1", '2025-01-01', '2025-07-01'),
                      ("2025 H2 (in-sample)", '2025-07-01', '2026-01-01'),
                      ("2026", '2026-01-01', '2027-01-01')]:
    seg = [b for b in all_bars if lo <= b['ts'].strftime('%Y-%m-%d') < hi]
    r = backtest(seg, 0.02)
    if r:
        print(f"{label:<22}{r['n']:>5}{r['wr']:>7.1f}{r['roi']:>+9.1f}{r['pf']:>6.2f}{r['mdd']:>7.1f}{r['avg']:>9.0f}")
    else:
        print(f"{label:<22}{'нет сделок':>20}")

# === 3) Полная история 2022-2026 ===
print("\n=== Полная история 2022-2026 (риск 2%) ===")
seg = [b for b in all_bars if '2022-01-01' <= b['ts'].strftime('%Y-%m-%d') < '2027-01-01']
r = backtest(seg, 0.02)
if r:
    print(f"n={r['n']} WR={r['wr']:.1f}% ROI={r['roi']:+.1f}% PF={r['pf']:.2f} MDD={r['mdd']:.1f}% "
          f"avg={r['avg']:.0f}₽ win/loss={r['wins']}/{r['losses']}")
ch.close()
