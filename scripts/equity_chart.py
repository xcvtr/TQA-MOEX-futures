#!/usr/bin/env python3
"""Equity curve chart for E6 portfolio."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import numpy as np
import pandas as pd

df = pd.read_json('reports/equity_e6.json')
df['time'] = pd.to_datetime(df['time'])
df = df.sort_values('time')

initial = 100000
equity = df['equity'].values

# Compute DD
peak = np.maximum.accumulate(equity)
dd = (peak - equity) / peak * 100

# Stats
final = equity[-1]
ret = (final - initial) / initial * 100
days = (df['time'].iloc[-1] - df['time'].iloc[0]).total_seconds() / 86400
years = max(days / 365.25, 0.1)
cagr = ((final / initial) ** (1 / years) - 1) * 100
max_dd = dd.max()
calmar = (ret / 100) / max(max_dd / 100, 0.001)

print(f"Initial: {initial:,.0f}")
print(f"Final:   {final:,.0f}")
print(f"Return:  {ret:.1f}%")
print(f"CAGR:    {cagr:.1f}%")
print(f"Max DD:  {max_dd:.1f}%")
print(f"Calmar:  {calmar:.1f}")
print(f"Days:    {days:.0f}")
print(f"Points:  {len(df)}")

# try plot
try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 8), gridspec_kw={'height_ratios': [3, 1]}, sharex=True)
    fig.patch.set_facecolor('#1a1a2e')

    for ax in [ax1, ax2]:
        ax.set_facecolor('#16213e')
        ax.tick_params(colors='#888')
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.spines['left'].set_color('#0f3460')
        ax.spines['bottom'].set_color('#0f3460')

    times = df['time']

    ax1.fill_between(times, equity, alpha=0.15, color='#00d2ff')
    ax1.plot(times, equity, color='#00d2ff', linewidth=1.5, label='Portfolio Equity')
    ax1.axhline(y=initial, color='#ffab40', linestyle='--', linewidth=0.8, alpha=0.5)
    ax1.set_ylabel('Capital (₽)', color='#e0e0e0', fontsize=12)
    ax1.legend(loc='upper left', facecolor='#16213e', edgecolor='#0f3460', labelcolor='#e0e0e0')

    # Annotations
    ax1.text(0.02, 0.95, f'CAGR: {cagr:.1f}% | Max DD: {max_dd:.1f}% | Calmar: {calmar:.1f}',
             transform=ax1.transAxes, color='#00e676', fontsize=11, verticalalignment='top',
             bbox=dict(boxstyle='round,pad=0.3', facecolor='#16213e', edgecolor='#0f3460'))

    ax2.fill_between(times, -dd, alpha=0.3, color='#ff5252')
    ax2.plot(times, -dd, color='#ff5252', linewidth=1)
    ax2.set_ylabel('Drawdown %', color='#e0e0e0', fontsize=12)
    ax2.set_xlabel('Date', color='#e0e0e0', fontsize=12)

    ax1.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
    ax1.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
    plt.xticks(rotation=45, color='#888')

    plt.tight_layout()
    plt.savefig('reports/equity_e6_chart.png', dpi=150, bbox_inches='tight', facecolor='#1a1a2e')
    print(f"\nChart saved: reports/equity_e6_chart.png")
except ImportError:
    print("\nmatplotlib not available — no chart")
