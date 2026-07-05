#!/usr/bin/env python3
"""Equity curve with MTM (balance + floating), datetime axis, 200K start.
Usage:
    python3 scripts/visualize.py                        # full backtest (slow)
    python3 scripts/visualize.py --run <run_id>         # from PG (fast)
"""
import sys, os, argparse, numpy as np, pandas as pd, matplotlib, psycopg2
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

parser = argparse.ArgumentParser()
parser.add_argument('--run', type=str, help='Run ID from backtest.summary')
args = parser.parse_args()

if args.run:
    # ── Load from PG ──
    CAPITAL = 200_000
    pg = psycopg2.connect(host='10.0.0.60', port=5432, dbname='moex', user='user')
    
    eq_df = pd.read_sql("SELECT bar_idx, ts_msk, balance, mtm FROM backtest.equity_curve WHERE run_id=%s ORDER BY bar_idx", pg, params=(args.run,))
    trades_df = pd.read_sql("SELECT ticker, direction, entry_price, exit_price, pnl, exit_reason, entry_time FROM backtest.trades WHERE run_id=%s", pg, params=(args.run,))
    summary = pd.read_sql("SELECT * FROM backtest.summary WHERE run_id=%s", pg, params=(args.run,))
    pg.close()
    
    if eq_df.empty:
        print(f"Run ID '{args.run}' not found"); sys.exit(1)
    
    ts = pd.to_datetime(eq_df['ts_msk'].values)
    balance = eq_df['balance'].values.astype(float)
    mtm = eq_df['mtm'].values.astype(float)
    
    # Reconstruct trades list
    class Trade:
        def __init__(self, r):
            self.ticker = r['ticker']
            self.direction = r['direction']
            self.entry_price = float(r['entry_price']) if r['entry_price'] else 0
            self.exit_price = float(r['exit_price']) if r['exit_price'] else 0
            self.pnl = float(r['pnl']) if r['pnl'] else 0
            self.exit_reason = r['exit_reason']
    trades = [Trade(r) for _, r in trades_df.iterrows()]
    
    s = summary.iloc[0] if not summary.empty else None
    n_bars = len(balance)
else:
    # ── Fallback: run full backtest ──
    sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
    import strategies.common.backtester, strategies.common.engine, importlib
    importlib.reload(strategies.common.backtester)
    importlib.reload(strategies.common.engine)
    from strategies.common.backtester import Backtester
    from strategies.common.broker import BrokerSim
    from strategies.common.engine import PortfolioEngine
    from strategies.stop_hunt.prod.engine import check_signal as sh_check
    
    CAPITAL = 200_000
    bt = Backtester(capital=CAPITAL, commission=4)
    portfolio = bt.load_portfolio()
    portfolio_sh = [(a, t, ['stop_hunt']) for a, t, s in portfolio]
    data = bt.load_data(portfolio_sh)
    if not data: print("No data loaded"); sys.exit(1)
    tickers = list(data.keys()); specs = bt.load_specs(tickers)
    
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
    
    max_len = max(len(data[t]['bt']) for t in data)
    ts_ticker = next(t for t in data if len(data[t]['bt']) >= max_len)
    timestamps = pd.to_datetime(data[ts_ticker]['bt'].iloc[50:50+n_bars].values)
    if timestamps.tz is not None:
        ts = timestamps.tz_convert('Europe/Moscow')
    else:
        ts = timestamps.tz_localize('Asia/Irkutsk').tz_convert('Europe/Moscow')
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
peak_bal = np.maximum.accumulate(balance)
dd_bal = (peak_bal - balance) / peak_bal * 100
mdd_bal = np.max(dd_bal)
ret_bal = (final_bal/CAPITAL - 1)*100

# Floating PnL (MTM - Balance) — visible on its own scale
floating = mtm - balance
max_floating = np.max(np.abs(floating)) if len(floating) > 0 else 1

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
    f'Cash MDD: {mdd_bal:.2f}%\n'
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

# ── Floating PnL (MTM - Balance) ──
ax2 = fig.add_subplot(gs[1])
ax2.plot(ts, floating, color='#FF9800', linewidth=1.5, alpha=0.85)
ax2.fill_between(ts, floating, 0, where=(floating >= 0), color='#4CAF50', alpha=0.3)
ax2.fill_between(ts, floating, 0, where=(floating < 0), color='#f44336', alpha=0.3)
ax2.axhline(y=0, color='#333', linewidth=0.5)
ax2.set_ylabel('Floating PnL (RUB)', fontsize=11)
ax2.set_title(f'Floating PnL (MTM - Balance) — max ±{max_floating:,.0f} ₽', fontsize=11)
ax2.yaxis.set_major_formatter(plt.FuncFormatter(fmt))
ax2.xaxis.set_major_formatter(mdates.DateFormatter('%b %Y'))
ax2.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
plt.setp(ax2.xaxis.get_majorticklabels(), rotation=45, ha='right')

# ── Cash Drawdown (Balance) ──
ax3 = fig.add_subplot(gs[2])
ax3.fill_between(ts, dd_bal, 0, color='#f44336', alpha=0.4)
ax3.plot(ts, dd_bal, color='#f44336', linewidth=1, alpha=0.7)
ax3.axhline(y=mdd_bal, color='#f44336', linestyle=':', linewidth=0.8, alpha=0.5)
ax3.set_ylabel('Drawdown (%)', fontsize=11)
ax3.set_title(f'Cash Drawdown (Balance) — Max: {mdd_bal:.2f}%', fontsize=11)
ax3.xaxis.set_major_formatter(mdates.DateFormatter('%b %Y'))
ax3.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
plt.setp(ax3.xaxis.get_majorticklabels(), rotation=45, ha='right')

plt.suptitle(f'Stop Hunt Portfolio — 5 Tickers, {CAPITAL:,} Start',
             fontsize=14, fontweight='bold', y=0.98)
plt.savefig('/home/user/.hermes/image_cache/equity_mtm.png', dpi=150, bbox_inches='tight')
plt.close()

print(f'Image: /home/user/.hermes/image_cache/equity_mtm.png')
print(f'Stats: balance={final_bal:,.0f} MTM={final_mtm:,.0f} ret={ret_bal:+.0f}% Cash MDD={mdd_bal:.2f}%')
print(f'Trades={n} WR={wr:.1f}% PF={pf:.3f}')
