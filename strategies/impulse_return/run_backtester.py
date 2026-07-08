#!/usr/bin/env python3
"""Impulse Return — портфельный тест через Backtester."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from strategies.common.backtester import Backtester

# Портфель: impulse_return на всех тикерах
portfolio = [
    ('Si', 'Si', ['impulse_return']),
    ('GAZR', 'GZ', ['impulse_return']),
    ('ROSN', 'RN', ['impulse_return']),
    ('MIX', 'MX', ['impulse_return']),
    ('LKOH', 'LK', ['impulse_return']),
    ('SNGP', 'SN', ['impulse_return']),
    ('MTSI', 'MN', ['impulse_return']),
    ('TATN', 'TT', ['impulse_return']),
]

bt = Backtester(capital=200_000, commission=4)
metrics = bt.run(portfolio=portfolio, start='2024-06-01', capital=200_000)

print(f"\n{'='*55}")
print(f"  IMPULSE RETURN — портфельный тест")
print(f"{'='*55}")
print(f"  Capital: 200,000 → {metrics.get('equity',0):,.0f} ₽")
print(f"  Return: {metrics.get('return_pct',0):+.1f}%")
print(f"  Trades: {metrics.get('n_trades',0)}")
print(f"  WR: {metrics.get('win_rate',0):.1f}%")
print(f"  MDD: {metrics.get('mdd_pct',0):.1f}%")
print(f"  PF: {metrics.get('profit_factor',0):.2f}")
print(f"  Sharpe: {metrics.get('sharpe',0):.2f}")

if 'by_ticker' in metrics:
    print(f"\n  Per ticker:")
    for t, m in sorted(metrics['by_ticker'].items()):
        print(f"    {t}: {m['trades']} tr, WR={m['wr']}%, PnL={m['pnl']:,.0f}")
