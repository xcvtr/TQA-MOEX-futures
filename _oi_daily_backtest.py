#!/usr/bin/env python3
"""Daily OI backtest: yur_buy↑ → next day LONG with walk-forward."""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import clickhouse_connect
from config import CH_HOST, CH_PORT, CH_DB

CAPITAL = 100_000
# Контрактные спецификации (cs=contract_size, go_mult=GO множитель)
SPECS = {
    'BR': {'cs': 10, 'comm': 4, 'type': 'futures'},    # нефть
    'BM': {'cs': 10, 'comm': 4, 'type': 'futures'},    # 
    'GD': {'cs': 10, 'comm': 4, 'type': 'futures'},    # золото-доллар
}

ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)

# Один скрипт для всех трёх тикеров
all_results = {}

for ticker in ['BR', 'BM', 'GD']:
    spec = SPECS[ticker]
    rows = ch.query('''
        SELECT toDate(p.time) as d,
               argMax(p.open, p.time) as open,
               argMax(p.high, p.time) as high,
               argMax(p.low, p.time) as low,
               argMax(p.close, p.time) as close,
               argMax(p.volume, p.time) as volume,
               argMax(o.yur_buy, p.time) as yur_buy,
               argMax(o.yur_sell, p.time) as yur_sell,
               argMax(o.fiz_buy, p.time) as fiz_buy,
               argMax(o.fiz_sell, p.time) as fiz_sell,
               argMax(o.total_oi, p.time) as total_oi
        FROM moex.prices_5m p
        INNER JOIN moex.prices_5m_oi o ON p.symbol = o.symbol AND p.time = o.time
        WHERE p.symbol = %(t)s AND p.time >= '2025-01-01' AND p.time <= '2026-05-01'
        GROUP BY d ORDER BY d
    ''', parameters={'t': ticker}).result_rows

    if len(rows) < 30:
        print(f'{ticker}: only {len(rows)} days — skip')
        continue

    # Парсим
    dates = [r[0] for r in rows]
    opn = np.array([r[1] for r in rows], dtype=float)
    high = np.array([r[2] for r in rows], dtype=float)
    low = np.array([r[3] for r in rows], dtype=float)
    close = np.array([r[4] for r in rows], dtype=float)
    volume = np.array([r[5] for r in rows], dtype=float)
    yb = np.array([r[6] for r in rows], dtype=float)
    ys = np.array([r[7] for r in rows], dtype=float)
    fb = np.array([r[8] for r in rows], dtype=float)
    fs = np.array([r[9] for r in rows], dtype=float)
    toi = np.array([r[10] for r in rows], dtype=float)
    toi = np.where(toi <= 0, 1, toi)

    # Сигнал: yur_buy_today > yur_buy_yesterday
    yb_up = np.diff(yb) > 0
    
    # Smart money filter: fiz_net падает И yb растёт
    fiz_net = (fb - fs) / toi * 100
    fiz_down = np.diff(fiz_net) < 0
    
    # Дневная доходность
    ret = np.diff(close) / close[:-1] * 100  # ret[i] = return от дня i к i+1

    print(f'\n=== {ticker} ({len(rows)} days) ===')

    # Walk-forward: 5 folds
    folds = [
        ('2025-01', '2025-03', '2025-01', '2025-03'),
        ('2025-04', '2025-06', '2025-04', '2025-06'),
        ('2025-07', '2025-09', '2025-07', '2025-09'),
        ('2025-10', '2025-12', '2025-10', '2025-12'),
        ('2026-01', '2026-05', '2026-01', '2026-05'),
    ]
    
    # Проще: делим на 5 хронологических фолдов
    n = len(rows)
    fold_size = n // 5
    fold_results = []
    
    for f in range(5):
        start = f * fold_size
        end = n if f == 4 else (f + 1) * fold_size
        
        # Тестовый период = этот фолд, тренировочный = всё до него
        # Но наша стратегия без параметров — просто yb_up, ничего учить не надо
        # Проверяем прямо на тестовом периоде
        test_mask = np.zeros(n, dtype=bool)
        test_mask[start:end] = True
        
        # Сигналы в тестовом периоде (нужен yb_up[i] → entry на i+1)
        # Сигнал срабатывает в день i, entry на i+1
        sig_idx = np.where(test_mask[1:] & yb_up)[0]
        
        # Торги
        equity = CAPITAL
        trades = []
        max_dd = 0
        peak = equity
        
        for si in sig_idx:
            entry_idx = si + 1  # entry на следующий день
            exit_idx = entry_idx + 1  # hold 1 день
            if exit_idx >= len(close):
                continue
            
            ep = float(opn[entry_idx])
            xp = float(close[exit_idx])
            
            # Stop check
            sp = ep * 0.98
            stop_hit = float(low[entry_idx]) <= sp
            if stop_hit:
                xp = sp
            
            # Размер позиции
            cs = spec['cs']
            go = ep * cs
            n_con = max(1, int(CAPITAL // go)) if go > 0 else 1
            
            gp = n_con * cs * (xp - ep)
            comm = n_con * spec['comm']
            npnl = gp - comm
            
            equity += npnl
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak * 100
            max_dd = max(max_dd, dd)
            
            trades.append({
                'entry': str(dates[entry_idx]),
                'exit': str(dates[exit_idx]),
                'ep': float(ep), 'xp': float(xp),
                'gp': round(gp, 2), 'comm': round(comm, 2), 'npnl': round(npnl, 2),
                'n': n_con, 'stop': stop_hit
            })
        
        ret_f = (equity - CAPITAL) / CAPITAL * 100
        calmar = ret_f / max_dd if max_dd > 0 else 0
        wins = sum(1 for t in trades if t['npnl'] > 0)
        wr = wins / len(trades) * 100 if trades else 0
        tot_comm = sum(t['comm'] for t in trades)
        gross_pnl = sum(t['gp'] for t in trades)
        pf = abs(sum(t['npnl'] for t in trades if t['npnl'] > 0) / (sum(abs(t['npnl']) for t in trades if t['npnl'] < 0) + 1))
        
        fold_results.append({
            'fold': f+1, 'trades': len(trades), 'ret': round(ret_f, 2),
            'dd': round(max_dd, 2), 'calmar': round(calmar, 2),
            'wr': round(wr, 1), 'pf': round(pf, 2), 'comm': round(tot_comm, 2)
        })
        
        print(f'  Fold {f+1}: tr={len(trades):>3} ret={ret_f:>+7.2f}% DD={max_dd:>5.2f}% Calmar={calmar:>6.2f} WR={wr:>5.1f}% PF={pf:>4.2f} comm={tot_comm:>8.0f}')
    
    all_results[ticker] = fold_results

print('\n=== Сводка ===')
for t, fr in all_results.items():
    rets = [f['ret'] for f in fr]
    dds = [f['dd'] for f in fr]
    wrs = [f['wr'] for f in fr]
    trs = [f['trades'] for f in fr]
    print(f'{t}: trades={sum(trs)}, mean_ret={np.mean(rets):+.1f}%, mean_dd={np.mean(dds):.1f}%, mean_wr={np.mean(wrs):.0f}%')

with open('reports/oi_daily_backtest.json', 'w') as f:
    json.dump(all_results, f, indent=2, default=str)
print('\nSaved to reports/oi_daily_backtest.json')
