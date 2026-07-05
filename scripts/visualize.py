#!/usr/bin/env python3
"""Equity curve with MTM (balance + floating), datetime axis, 200K start."""
import sys, os, numpy as np, pandas as pd, matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')

import strategies.common.backtester, strategies.common.engine, importlib
importlib.reload(strategies.common.backtester)
importlib.reload(strategies.common.engine)

from strategies.common.backtester import Backtester
from strategies.common.broker import BrokerSim
from strategies.common.executor import Executor
from strategies.common.engine import PortfolioEngine
from strategies.stop_hunt.prod.engine import check_signal as sh_check

CAPITAL = 200_000

# ── Backtest ──
bt = Backtester(capital=CAPITAL, commission=4)
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
engine = PortfolioEngine(strategies, broker=broker, capital=CAPITAL)
engine.executor.load_portfolio()
result = engine.run(data, specs)

balance = np.array(result.balance_curve)
mtm = np.array(result.mtm_curve)
trades = result.trades
n_bars = len(balance)

# ── Timestamps ──
max_len = max(len(data[t]['bt']) for t in data)
for t in data:
    if len(data[t]['bt']) >= max_len:
        ts_ticker = t; break
ts_df = data[ts_ticker]
timestamps = ts_df['bt'].iloc[50:50+n_bars].values
ts = pd.to_datetime(timestamps)
if ts.tz is not None:
    ts = ts.tz_convert('Europe/Moscow')
else:
    ts = ts.tz_localize('Asia/Irkutsk').tz_convert('Europe/Moscow')
ts = ts.tz_localize(None)

# ── Downsample for readability ──
MAX_POINTS = 3000
n_bars = len(balance)
if n_bars > MAX_POINTS:
    step = n_bars // MAX_POINTS
    idx = np.arange(0, n_bars, step)
    balance = balance[idx]
    mtm = mtm[idx]
    ts = ts[idx]
    n_bars = len(balance)
    print(f"Downsampled: {n_bars * step} → {n_bars} (step={step})", flush=True)

# ── Stats ──
pnls = np.array([t.pnl for t in trades])
wins = pnls[pnls > 0]; losses = pnls[pnls <= 0]
n = len(pnls); wr = len(wins)/n*100 if n > 0 else 0
pf = abs(sum(wins)/sum(losses)) if len(losses) > 0 and sum(losses) != 0 else float('inf')
final_bal = balance[-1]; final_mtm = mtm[-1]
peak_mtm = np.maximum.accumulate(mtm)
dd_mtm = (peak_mtm - mtm) / peak_mtm * 100
mdd_mtm = np.max(dd_mtm)
ret_bal = (final_bal/CAPITAL - 1)*100

# ── Plot ──
fig = plt.figure(figsize=(16, 11))
gs = fig.add_gridspec(3, 1, height_ratios=[2, 1, 0.8], hspace=0.3)

ax1 = fig.add_subplot(gs[0])
ax1.plot(ts, balance, color='#2196F3', linewidth=1.5, alpha=0.85, label='Balance (closed PnL)')
ax1.plot(ts, mtm, color='#FF9800', linewidth=1.5, alpha=0.85, label='Equity (MTM = balance + floating)')
ax1.axhline(y=CAPITAL, color='#666', linestyle='--', linewidth=0.8, alpha=0.4)
ax1.set_ylabel('RUB', fontsize=11)
ax1.set_title(f'Stop Hunt Portfolio — Balance vs Equity (MTM)', fontsize=13, fontweight='bold')
ax1.legend(fontsize=10, loc='upper left')
ax1.set_ylim(bottom=0)
ax1.xaxis.set_major_formatter(mdates.DateFormatter('%b %Y'))
ax1.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
plt.setp(ax1.xaxis.get_majorticklabels(), rotation=45, ha='right')

# Stats box
stats = (
    f'Start: {CAPITAL:,.0f} ₽\n'
    f'Balance: {final_bal:,.0f} ₽  (+{ret_bal:,.0f}%)\n'
    f'MTM: {final_mtm:,.0f} ₽\n'
    f'MTM MDD: {mdd_mtm:.2f}%\n'
    f'Trades: {n}  |  WR: {wr:.1f}%  |  PF: {pf:.3f}'
)
ax1.text(0.02, 0.97, stats, transform=ax1.transAxes, fontsize=9,
         verticalalignment='top', bbox=dict(boxstyle='round,pad=0.5',
         facecolor='wheat', alpha=0.85))

def fmt(x, p):
    if abs(x) >= 1e6: return f'{x/1e6:.1f}M'
    if abs(x) >= 1e3: return f'{x/1e3:.0f}K'
    return f'{x:.0f}'
ax1.yaxis.set_major_formatter(plt.FuncFormatter(fmt))

# ── Drawdown ──
ax2 = fig.add_subplot(gs[1])
ax2.fill_between(ts, dd_mtm, 0, color='#f44336', alpha=0.4)
ax2.plot(ts, dd_mtm, color='#f44336', linewidth=1, alpha=0.7, label='Drawdown (MTM)')
ax2.axhline(y=mdd_mtm, color='#f44336', linestyle=':', linewidth=0.8, alpha=0.5)
ax2.set_ylabel('Drawdown (%)', fontsize=11)
ax2.set_title(f'MTM Drawdown (Max: {mdd_mtm:.2f}%)', fontsize=11)
ax2.legend(fontsize=9)
ax2.xaxis.set_major_formatter(mdates.DateFormatter('%b %Y'))
ax2.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
plt.setp(ax2.xaxis.get_majorticklabels(), rotation=45, ha='right')

# ── Trade markers ──
ax3 = fig.add_subplot(gs[2])
win_ts = []; win_pnl = []; loss_ts = []; loss_pnl = []
# Get trade exit times (approximate from bar index)
for t in trades[-500:]:  # show last 500 trades only
    if t.entry_bar < n_bars:
        bar_time = ts[min(t.entry_bar, len(ts)-1)]
        val = t.pnl / final_mtm * 100 if final_mtm != 0 else 0
        if t.pnl > 0:
            win_ts.append(bar_time); win_pnl.append(val)
        else:
            loss_ts.append(bar_time); loss_pnl.append(val)

ax3.scatter(win_ts, win_pnl, c='#4CAF50', s=8, alpha=0.5, label='Win')
ax3.scatter(loss_ts, loss_pnl, c='#f44336', s=8, alpha=0.5, label='Loss')
ax3.axhline(y=0, color='#333', linewidth=0.5)
ax3.set_ylabel('Trade % of Equity', fontsize=11)
ax3.set_title(f'Trade PnL (last {min(500, n)}) — % of final equity', fontsize=11)
ax3.legend(fontsize=9, loc='upper left')
ax3.xaxis.set_major_formatter(mdates.DateFormatter('%b %Y'))
ax3.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
plt.setp(ax3.xaxis.get_majorticklabels(), rotation=45, ha='right')

plt.suptitle(f'Stop Hunt Portfolio — 5 Tickers, {CAPITAL:,} Start',
             fontsize=14, fontweight='bold', y=0.98)
plt.savefig('/home/user/.hermes/image_cache/equity_mtm.png', dpi=150, bbox_inches='tight')
plt.close()

print(f'Image: /home/user/.hermes/image_cache/equity_mtm.png')
print(f'Stats: balance={final_bal:,.0f} MTM={final_mtm:,.0f} ret={ret_bal:+.0f}% MDD={mdd_mtm:.2f}%')
print(f'Trades={n} WR={wr:.1f}% PF={pf:.3f}')
