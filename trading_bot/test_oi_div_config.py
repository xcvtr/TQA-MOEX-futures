#!/usr/bin/env python3
"""Test OI Divergence with correct config keys."""
import sys
sys.path.insert(0, '/home/user/projects/TQA-MOEX')
from trading_bot.new_strategies import _load_data_cached, detect_oi_divergence_signals

TICKERS = [
    'AF','AL','AU','BM','BR','CC','CE','CH','CNYRUBF','CR','DX','ED',
    'EURRUBF','Eu','FF','GAZPF','GD','GK','GL','GLDRUBF','GZ','HS',
    'HY','IB','IMOEXF','KC','LK','MC','ME','MG','MM','MN','MX','MY',
    'NA','NG','NM','NR','OJ','PD','PT','RB','RI','RL','RM','RN',
    'SBERF','SE','SF','Si','SN','SP','SR','SS','SV','TN','TT','UC',
    'USDRUBF','VB','VI','W4','X5','YD',
]
CFG = {'lookback': 20, 'extreme_window': 10, 'bear_threshold': 0.95, 'bull_threshold': 1.05, 'horizon': 6}
MIN_SIGS = 10

row_data = []
for sym in TICKERS:
    data = _load_data_cached(sym, 720, with_oi=True)
    if not data:
        continue
    filtered = [d for d in data if d.get('time', '') >= '2025-09-01']
    if len(filtered) < 100:
        continue

    sigs = detect_oi_divergence_signals(filtered, CFG)
    if len(sigs) < MIN_SIGS:
        continue

    wins = sum(1 for s in sigs if s.get('return_pct', 0) > 0)
    wr = (wins / len(sigs)) * 100
    rets = [s['return_pct'] for s in sigs if 'return_pct' in s]
    gross_win = sum(r for r in rets if r > 0)
    gross_loss = abs(sum(r for r in rets if r < 0))
    pf = gross_win / gross_loss if gross_loss > 0 else 999.99
    avg_ret = sum(rets) / len(rets) if rets else 0
    score = int(len(sigs) * (wr - 50) * pf / 100)
    row_data.append((sym, len(sigs), wr, pf, avg_ret, score))

row_data.sort(key=lambda x: -x[5])

print('OI Divergence (сен2025-июн2026) — С ПРАВИЛЬНЫМ КОНФИГОМ')
print('=' * 65)
print(f'  {"Тикер":6s} {"n":>5s} {"WR":>6s} {"PF":>6s} {"AvgRet":>8s} {"Score":>6s}')
print(f'  {"-"*35}')
for sym, n, wr, pf, avg, sc in row_data:
    print(f'  {sym:6s} n={n:>4d}  WR={wr:>5.1f}% PF={pf:>5.2f}  avg={avg:>+.4f}  ★={sc:>4d}')

if row_data:
    avg_wr = sum(r[2] for r in row_data) / len(row_data)
    avg_pf = sum(r[3] for r in row_data) / len(row_data)
    good = [r for r in row_data if r[2] >= 55]
    print(f'  {"-"*35}')
    print(f'  AVG WR={avg_wr:.1f}%  AVG PF={avg_pf:.2f}  WR>=55%: {len(good)}/{len(row_data)}')
