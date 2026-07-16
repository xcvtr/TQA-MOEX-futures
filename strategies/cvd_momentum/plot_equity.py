#!/usr/bin/env python3
"""CVD Momentum — plot equity curves from trades JSON."""
import sys, json, os
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

def plot_equity(trades_path, output_path):
    with open(trades_path) as f:
        trades = json.load(f)
    
    if not trades:
        print(f"No trades in {trades_path}")
        return
    
    # Sort by time
    trades.sort(key=lambda t: t['bt'])
    
    times = [t['bt'][:19] for t in trades]
    pnls = np.cumsum([t.get('pnl_rub', 0) for t in trades])
    
    # By ticker
    by_ticker = {}
    for t in trades:
        by_ticker.setdefault(t['ticker'], []).append(t)
    
    fig, axes = plt.subplots(3, 1, figsize=(14, 10), gridspec_kw={'height_ratios': [3, 1, 1]})
    
    # 1. Equity curve
    ax = axes[0]
    ax.plot(pnls / 1000, color='#2196F3', linewidth=0.8)
    ax.fill_between(range(len(pnls)), 0, pnls / 1000, alpha=0.1, color='#2196F3')
    ax.axhline(y=0, color='black', linewidth=0.3)
    ax.set_ylabel('PnL, тыс ₽')
    ax.set_title(f'CVD Momentum — Equity Curve (5 tickers, {len(trades)} trades)')
    ax.grid(True, alpha=0.3)
    
    # 2. Drawdown
    ax2 = axes[1]
    peak = np.maximum.accumulate(pnls)
    dd = (peak - pnls) / 1000
    ax2.fill_between(range(len(dd)), 0, dd, color='#f44336', alpha=0.5)
    ax2.set_ylabel('Drawdown, тыс ₽')
    ax2.grid(True, alpha=0.3)
    ax2.set_xlabel('Trade #')
    max_dd = np.max(dd)
    ax2.axhline(y=max_dd, color='red', linestyle='--', alpha=0.5, label=f'Max DD: {max_dd:.0f}K')
    ax2.legend()
    
    # 3. Monthly bars
    ax3 = axes[2]
    monthly = {}
    for t in trades:
        ym = t['bt'][:7]
        monthly.setdefault(ym, 0)
        monthly[ym] += t.get('pnl_rub', 0)
    
    months = sorted(monthly.keys())
    vals = [monthly[m] / 1000 for m in months]
    colors = ['#4CAF50' if v > 0 else '#f44336' for v in vals]
    ax3.bar(range(len(months)), vals, color=colors, width=0.8)
    ax3.set_ylabel('PnL, тыс ₽')
    ax3.set_xticks(range(len(months)))
    ax3.set_xticklabels([m[2:7] for m in months], rotation=45, fontsize=7)
    ax3.axhline(y=0, color='black', linewidth=0.3)
    ax3.grid(True, alpha=0.3)
    
    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close()
    print(f"Saved: {output_path} (final PnL: {pnls[-1]/1000:.0f}K, trades: {len(trades)})")

if __name__ == '__main__':
    import glob
    files = glob.glob('/home/user/projects/TQA-MOEX-futures/reports/trades_*.json')
    if not files:
        print("No trades_*.json files found in reports/")
        sys.exit(1)
    
    for f in sorted(files):
        basename = os.path.splitext(os.path.basename(f))[0].replace('trades_', '')
        out = os.path.join(os.path.dirname(f), f'equity_{basename}.png')
        plot_equity(f, out)
    
    # Combined plot
    if len(files) > 1:
        fig, ax = plt.subplots(figsize=(14, 6))
        colors = plt.cm.Set1(np.linspace(0, 1, len(files)))
        for f, c in zip(sorted(files), colors):
            with open(f) as fh:
                trades = json.load(fh)
            trades.sort(key=lambda t: t['bt'])
            pnls = np.cumsum([t.get('pnl_rub', 0) for t in trades]) / 1000
            label = os.path.splitext(os.path.basename(f))[0].replace('trades_', '')
            ax.plot(pnls, color=c, label=label, linewidth=0.8)
        
        ax.axhline(y=0, color='black', linewidth=0.3)
        ax.set_ylabel('PnL, тыс ₽')
        ax.set_title('CVD Momentum — Combined Equity Curves')
        ax.legend(fontsize=8)
        ax.grid(True, alpha=0.3)
        plt.tight_layout()
        plt.savefig('/home/user/projects/TQA-MOEX-futures/reports/equity_combined.png', dpi=150)
        plt.close()
        print(f"Saved: reports/equity_combined.png")
