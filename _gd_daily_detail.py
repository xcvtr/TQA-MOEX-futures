#!/usr/bin/env python3
"""GD smart_money hold=10 sl=1% — детальный разбор."""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import clickhouse_connect
from config import CH_HOST, CH_PORT, CH_DB

CAPITAL = 100_000
CS = 10
COMM = 4

ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)
rows = ch.query("""
    SELECT toDate(p.time) as d,
           argMax(p.open, p.time) as open,
           argMax(p.high, p.time) as high,
           argMax(p.low, p.time) as low,
           argMax(p.close, p.time) as close,
           argMax(o.yur_buy, p.time) as yur_buy,
           argMax(o.yur_sell, p.time) as yur_sell,
           argMax(o.fiz_buy, p.time) as fiz_buy,
           argMax(o.fiz_sell, p.time) as fiz_sell,
           argMax(o.total_oi, p.time) as total_oi
    FROM moex.prices_5m p
    INNER JOIN moex.prices_5m_oi o ON p.symbol = o.symbol AND p.time = o.time
    WHERE p.symbol = 'GD' AND p.time >= '2025-01-01' AND p.time <= '2026-05-01'
    GROUP BY d ORDER BY d
""").result_rows

dates = [str(r[0]) for r in rows]
opn = np.array([r[1] for r in rows], dtype=float)
high = np.array([r[2] for r in rows], dtype=float)
low = np.array([r[3] for r in rows], dtype=float)
close = np.array([r[4] for r in rows], dtype=float)
yb = np.array([r[5] for r in rows], dtype=float)
fb = np.array([r[7] for r in rows], dtype=float)
fs = np.array([r[8] for r in rows], dtype=float)
toi = np.array([r[9] for r in rows], dtype=float)
toi = np.where(toi <= 0, 1, toi)

dyb = np.diff(yb)
fiz_net = (fb - fs) / toi * 100
dfiz = np.diff(fiz_net)

n = len(rows)
HOLD = 10
SL = 0.01

# Walk-forward 5 folds
nf = 5
fsize = n // nf

print(f'GD smart_money hold={HOLD} sl={SL:.0%}')
print(f'{n} days, {nf} folds x ~{fsize} days')
print()

all_trades = []

for f in range(nf):
    s = f * fsize
    e = n if f == 4 else (f + 1) * fsize
    eq = CAPITAL
    eq_curve = [eq]
    peak = eq
    mdd = 0
    trades = []
    
    for i in range(s, e - 1):
        if not (dyb[i] > 0 and dfiz[i] < 0):
            continue
        ei = i + 1
        xi = min(ei + HOLD, n - 1)
        if ei >= n - 1:
            continue
        
        ep = float(opn[ei])
        sp = ep * (1 - SL)
        stop_hit = False
        xp = float(close[xi])
        
        for j in range(ei, xi + 1):
            if float(low[j]) <= sp:
                xp = sp
                stop_hit = True
                break
        
        go = ep * CS
        nc = max(1, int(eq // go)) if go > 0 else 1
        gp = nc * CS * (xp - ep)
        cm = nc * COMM
        npnl = gp - cm
        eq += npnl
        eq_curve.append(eq)
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak * 100
        mdd = max(mdd, dd)
        
        pnl_pct = npnl / eq * 100
        trades.append({
            'fold': f+1,
            'entry': dates[ei],
            'exit': dates[xi],
            'entry_price': float(ep),
            'exit_price': float(xp),
            'direction': 'LONG',
            'contracts': nc,
            'gross_pnl': round(gp, 0),
            'commission': round(cm, 0),
            'net_pnl': round(npnl, 0),
            'pnl_pct': round(pnl_pct, 2),
            'stop_hit': stop_hit,
            'bars_held': xi - ei,
            'yb_change': round(float(dyb[i]), 0),
            'fiz_change': round(float(dfiz[i]), 2),
        })
    
    ret = (eq - CAPITAL) / CAPITAL * 100
    calmar = ret / mdd if mdd > 0 else 0
    wins = sum(1 for t in trades if t['net_pnl'] > 0)
    wr = wins / len(trades) * 100 if trades else 0
    gross = sum(t['gross_pnl'] for t in trades)
    tot_cm = sum(t['commission'] for t in trades)
    net = sum(t['net_pnl'] for t in trades)
    pf = abs(sum(t['net_pnl'] for t in trades if t['net_pnl'] > 0) / (sum(abs(t['net_pnl']) for t in trades if t['net_pnl'] < 0) + 1))
    
    all_trades.extend(trades)
    
    # Дата диапазон
    d_start = dates[s] if s < len(dates) else '?'
    d_end = dates[min(e-1, len(dates)-1)]
    
    print(f'Fold {f+1} ({d_start} – {d_end}):')
    print(f'  {len(trades)} trades | ret={ret:+.1f}% | DD={mdd:.1f}% | Calmar={calmar:.2f} | WR={wr:.0f}% | PF={pf:.2f}')
    print(f'  Gross={gross:+.0f} | Comm={tot_cm:.0f} | Net={net:+.0f}')
    
    # Топ-3 лучшие и худшие
    sorted_t = sorted(trades, key=lambda x: -x['net_pnl'])
    print(f'  Best trades:')
    for t in sorted_t[:3]:
        print(f'    {t["entry"]}→{t["exit"]} ep={t["entry_price"]:.0f} xp={t["exit_price"]:.0f} pnl={t["net_pnl"]:+.0f} ({t["pnl_pct"]:+.1f}%) n={t["contracts"]}')
    print(f'  Worst trades:')
    for t in sorted_t[-3:]:
        print(f'    {t["entry"]}→{t["exit"]} ep={t["entry_price"]:.0f} xp={t["exit_price"]:.0f} pnl={t["net_pnl"]:+.0f} ({t["pnl_pct"]:+.1f}%) stop={t["stop_hit"]}')
    print()

# Итого
rets_by_fold = {}
for f in range(nf):
    ft = [t for t in all_trades if t['fold'] == f+1]
    ret = sum(t['net_pnl'] for t in ft) / CAPITAL * 100
    rets_by_fold[f+1] = round(ret, 2)

total_net = sum(t['net_pnl'] for t in all_trades)
total_ret = total_net / CAPITAL * 100
total_wins = sum(1 for t in all_trades if t['net_pnl'] > 0)
total_wr = total_wins / len(all_trades) * 100 if all_trades else 0

print(f'=== ИТОГО ===')
print(f'Всего сделок: {len(all_trades)}')
print(f'Суммарная доходность: {total_ret:+.1f}%')
print(f'Доходность по фолдам: {rets_by_fold}')
print(f'WR: {total_wr:.0f}%')
print(f'Комиссий всего: {sum(t["commission"] for t in all_trades):.0f} RUB')

# Сохраняем
with open('reports/oi_daily_gd_detail.json', 'w') as f:
    json.dump(all_trades, f, indent=2, default=str)
print(f'\nСделки сохранены в reports/oi_daily_gd_detail.json')
