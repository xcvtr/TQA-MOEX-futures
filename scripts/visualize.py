#!/usr/bin/env python3
"""Equity curve with datetime axis + per-ticker breakdown."""
import sys, os, numpy as np, pandas as pd, matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')

import strategies.common.backtester
import strategies.common.engine
import importlib
importlib.reload(strategies.common.backtester)
importlib.reload(strategies.common.engine)

from strategies.common.backtester import Backtester
from strategies.common.broker import BrokerSim
from strategies.common.executor import Executor
from strategies.common.engine import PortfolioEngine
from strategies.stop_hunt.prod.engine import check_signal as sh_check

# ── Backtest ──────────────────────────────────────────
bt = Backtester(capital=100_000, commission=4)
portfolio = bt.load_portfolio()
portfolio_sh = [(a, t, ['stop_hunt']) for a, t, s in portfolio]

data = bt.load_data(portfolio_sh)
if not data:
    print("No data loaded"); sys.exit(1)

tickers = list(data.keys())
specs = bt.load_specs(tickers)

strategies = []
for asset, ticker, strats in portfolio_sh:
    if ticker not in data: continue
    for sname in strats:
        fn = {'stop_hunt': sh_check}.get(sname)
        if fn: strategies.append((sname, fn, [ticker], None))

broker = BrokerSim(commission=4)
engine = PortfolioEngine(strategies, broker=broker, capital=100_000)
engine.executor.load_portfolio()
result = engine.run(data, specs)

eq_curve = np.array(result.eq_curve)
trades = result.trades
initial = 100_000.0
final = result.equity

# ── Build datetime axis from data ──
# Find the ticker with the most bars (max_len)
max_len = max(len(data[t]['bt']) for t in data)
# Get timestamps from the ticker that has max_len (most likely the one engine used)
for t in data:
    if len(data[t]['bt']) >= max_len:
        ts_ticker = t
        break
ts_df = data[ts_ticker]
timestamps = ts_df['bt'].iloc[50:50+len(eq_curve)].values
# Convert IRK→MSK and make naive (for plotting)
# Convert IRK→MSK and make naive (for plotting)
timestamps_msk = pd.to_datetime(timestamps)
if timestamps_msk.tz is not None:
    timestamps_msk = timestamps_msk.tz_convert('Europe/Moscow')
else:
    timestamps_msk = timestamps_msk.tz_localize('Asia/Irkutsk').tz_convert('Europe/Moscow')
timestamps_msk = timestamps_msk.tz_localize(None)

# ── Per-ticker cumulative PnL over time ──
ticker_eq = {t: np.full(len(eq_curve), initial/len(tickers)) for t in tickers}
current_pnl = {t: 0.0 for t in tickers}
for t in trades:
    if t.entry_bar < len(eq_curve):
        current_pnl[t.ticker] += t.pnl
        # We'll fill forward - for simplicity, just add at exit bar
        ticker_eq[t.ticker][t.entry_bar:] = initial/len(tickers) + current_pnl[t.ticker]

# ── Stats ─────────────────────────────────────────────
pnls = np.array([t.pnl for t in trades])
wins = pnls[pnls > 0]
losses = pnls[pnls <= 0]
n = len(pnls)
wr = len(wins)/n*100 if n > 0 else 0
pf = abs(sum(wins)/sum(losses)) if len(losses) > 0 and sum(losses) != 0 else float('inf')
peak = np.maximum.accumulate(eq_curve)
dd = (peak - eq_curve) / peak * 100
mdd = np.max(dd)
ret_pct = (final/initial - 1)*100

# ── Plot ──────────────────────────────────────────────
fig = plt.figure(figsize=(16, 10))
gs = fig.add_gridspec(3, 2, height_ratios=[2, 1, 1.3], hspace=0.3, wspace=0.25)

# === Top: Equity curve (datetime axis) ===
ax1 = fig.add_subplot(gs[0, :])
ax1.plot(timestamps_msk, eq_curve, color='#2196F3', linewidth=1.5, alpha=0.85)
ax1.fill_between(timestamps_msk, eq_curve, initial, color='#2196F3', alpha=0.08)
ax1.axhline(y=initial, color='#666', linestyle='--', linewidth=0.8, alpha=0.4)
ax1.set_ylabel('Equity (RUB)', fontsize=11)
ax1.set_title(f'Stop Hunt Portfolio — Equity Curve', fontsize=13, fontweight='bold')
ax1.xaxis.set_major_formatter(mdates.DateFormatter('%b %Y'))
ax1.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
plt.setp(ax1.xaxis.get_majorticklabels(), rotation=45, ha='right')

# Stats box on the chart
stats = (
    f'{initial:,.0f} ₽ → {final:,.0f} ₽\n'
    f'+{ret_pct:,.0f}%  |  MDD: {mdd:.2f}%\n'
    f'Trades: {n}  |  WR: {wr:.1f}%  |  PF: {pf:.3f}'
)
ax1.text(0.02, 0.95, stats, transform=ax1.transAxes, fontsize=10,
         verticalalignment='top', bbox=dict(boxstyle='round,pad=0.5',
         facecolor='wheat', alpha=0.85))

def fmt(x, p):
    if abs(x) >= 1e6: return f'{x/1e6:.1f}M'
    if abs(x) >= 1e3: return f'{x/1e3:.0f}K'
    return f'{x:.0f}'
ax1.yaxis.set_major_formatter(plt.FuncFormatter(fmt))

# === Middle: Drawdown ===
ax2 = fig.add_subplot(gs[1, :])
ax2.fill_between(timestamps_msk, dd, 0, color='#f44336', alpha=0.4)
ax2.plot(timestamps_msk, dd, color='#f44336', linewidth=1, alpha=0.7)
ax2.set_ylabel('Drawdown (%)', fontsize=11)
ax2.set_title(f'Drawdown (Max: {mdd:.2f}%)', fontsize=11)
ax2.xaxis.set_major_formatter(mdates.DateFormatter('%b %Y'))
ax2.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
plt.setp(ax2.xaxis.get_majorticklabels(), rotation=45, ha='right')

# === Bottom left: Per-ticker PnL breakdown ===
ax3 = fig.add_subplot(gs[2, 0])
ticker_names = []
ticker_pnls = []
ticker_colors = ['#FF6384', '#36A2EB', '#FFCE56', '#4BC0C0', '#9966FF']
by_ticker = {}
for t in trades:
    by_ticker.setdefault(t.ticker, []).append(t.pnl)
for i, (tk, pnls_list) in enumerate(sorted(by_ticker.items())):
    ticker_names.append(tk)
    ticker_pnls.append(sum(pnls_list))

bars = ax3.barh(ticker_names, ticker_pnls, color=ticker_colors[:len(ticker_names)])
ax3.axvline(x=0, color='#333', linewidth=0.5)
ax3.set_xlabel('PnL (RUB)', fontsize=11)
ax3.set_title('Per-Ticker PnL', fontsize=11)
ax3.xaxis.set_major_formatter(plt.FuncFormatter(fmt))
for bar, val in zip(bars, ticker_pnls):
    ax3.text(val, bar.get_y() + bar.get_height()/2,
             f'{val:+,.0f} ₽', ha='left' if val >= 0 else 'right',
             va='center', fontsize=9, fontweight='bold')

# === Bottom right: Win/Loss stats ===
ax4 = fig.add_subplot(gs[2, 1])
ax4.axis('off')
win_avg = np.mean(wins) if len(wins) > 0 else 0
loss_avg = np.mean(losses) if len(losses) > 0 else 0
avg_pnl = np.mean(pnls) if n > 0 else 0

info_lines = [
    f'Win Rate:      {wr:.1f}%',
    f'Profit Factor:  {pf:.3f}',
    f'Avg Win:       {win_avg:+,.0f} ₽',
    f'Avg Loss:      {loss_avg:+,.0f} ₽',
    f'Avg Trade:     {avg_pnl:+,.0f} ₽',
    f'Max Win:       {np.max(wins):+,.0f} ₽' if len(wins) > 0 else '',
    f'Max Loss:      {np.min(losses):+,.0f} ₽' if len(losses) > 0 else '',
    f'Sharpe:        {np.mean(pnls)/np.std(pnls)*np.sqrt(252*78):.3f}' if n > 1 else '',
    '',
    f'Period: {timestamps_msk[0].strftime("%b %Y")} — {timestamps_msk[-1].strftime("%b %Y")}',
    f'Bars: {len(eq_curve):,}',
]

for i, line in enumerate(info_lines):
    if line:
        ax4.text(0.05, 0.95 - i*0.07, line, fontsize=10,
                 transform=ax4.transAxes, verticalalignment='top',
                 fontfamily='monospace')

plt.suptitle('Stop Hunt Portfolio — 5 Tickers, Reinvest, 100K Initial',
             fontsize=14, fontweight='bold', y=0.98)
plt.savefig('/home/user/.hermes/image_cache/equity_curve_time.png', dpi=150, bbox_inches='tight')
plt.close()

print(f'Image: /home/user/.hermes/image_cache/equity_curve_time.png')
print(f'Stats: equity={final:,.0f}₽ ret={ret_pct:+.0f}% MDD={mdd:.2f}% trades={n} WR={wr:.1f}% PF={pf:.3f}')
print(f'Period: {timestamps_msk[0]} — {timestamps_msk[-1]}')
