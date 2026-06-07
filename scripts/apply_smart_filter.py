#!/home/user/venvs/tqa/main/bin/python
"""Apply smart per-pair calendar filter to existing unfiltered trades."""
import json, os, sys, warnings
from pathlib import Path
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

warnings.filterwarnings('ignore')

DB = dict(host="10.0.0.64", port=5432, dbname="forex", user="postgres", password="postgres")
OUTDIR = Path("/home/user/.hermes/cache/screenshots/tqa/equity_cluster/2025")
PIP_VALUE_USD = 10.0

SYMBOLS = ['audjpy','audusd','euraud','eurgbp','eurjpy','eurusd',
           'gbpjpy','gbpusd','nzdusd','usdcad','usdchf','usdjpy','xauusd']

# ---- PER-PAIR COUNTRY MAPPING ----
PAIR_COUNTRIES = {
    'audjpy': ['AU', 'JP'], 'audusd': ['AU', 'US'], 'euraud': ['EU', 'AU'],
    'eurgbp': ['EU', 'GB'], 'eurjpy': ['EU', 'JP'], 'eurusd': ['EU', 'US'],
    'gbpjpy': ['GB', 'JP'], 'gbpusd': ['GB', 'US'], 'nzdusd': ['NZ', 'US'],
    'usdcad': ['US', 'CA'], 'usdchf': ['US', 'CH'], 'usdjpy': ['US', 'JP'],
    'xauusd': ['US'],
}

# ---- SMART FILTER RULES ----
# Strategy: all imp≥3 events for relevant countries, ±1d window
# But NO filtering for XAUUSD (gold profits from volatility)
# And for cross-pairs (no USD): only rate decisions
FILTER_RULES = {
    'audjpy': [
        ('AU', 3, 1.0, None),  # all AU imp=3 events
        ('JP', 3, 1.0, ['BoJ', 'Interest Rate', 'CPI', 'GDP']),
    ],
    'audusd': [
        ('AU', 3, 1.0, None),
        ('US', 3, 1.0, ['FOMC', 'NFP', 'CPI', 'Nonfarm', 'Interest Rate', 'Core PCE', 'PPI', 'GDP', 'Unemployment']),
    ],
    'euraud': [
        ('EU', 3, 1.0, None),  # only rate decisions for cross
        ('AU', 3, 1.0, None),
    ],
    'eurgbp': [
        ('EU', 3, 1.0, None),
        ('GB', 3, 1.0, None),
    ],
    'eurjpy': [
        ('EU', 3, 1.0, None),
        ('JP', 3, 1.0, ['BoJ', 'Interest Rate', 'CPI', 'GDP']),
    ],
    'eurusd': [
        ('EU', 3, 1.0, None),
        ('US', 3, 1.0, ['FOMC', 'NFP', 'CPI', 'Nonfarm', 'Interest Rate', 'Core PCE', 'PPI', 'GDP', 'Unemployment', 'Retail Sales', 'ISM']),
    ],
    'gbpjpy': [
        ('GB', 3, 1.0, None),
        ('JP', 3, 1.0, ['BoJ', 'Interest Rate', 'CPI', 'GDP']),
    ],
    'gbpusd': [
        ('GB', 3, 1.0, None),
        ('US', 3, 1.0, ['FOMC', 'NFP', 'CPI', 'Nonfarm', 'Interest Rate', 'Core PCE', 'PPI', 'GDP', 'Unemployment', 'Retail Sales', 'ISM']),
    ],
    'nzdusd': [
        ('NZ', 3, 1.0, None),
        ('US', 3, 1.0, ['FOMC', 'NFP', 'CPI', 'Nonfarm', 'Interest Rate', 'Core PCE', 'PPI', 'GDP', 'Unemployment', 'Retail Sales']),
    ],
    'usdcad': [
        ('CA', 3, 1.0, None),
        ('US', 3, 1.0, ['FOMC', 'NFP', 'CPI', 'Nonfarm', 'Interest Rate', 'Core PCE', 'PPI', 'GDP', 'Unemployment', 'Retail Sales']),
    ],
    'usdchf': [
        ('CH', 3, 1.0, None),
        ('US', 3, 1.0, ['FOMC', 'NFP', 'CPI', 'Nonfarm', 'Interest Rate', 'Core PCE', 'PPI', 'GDP', 'Unemployment', 'Retail Sales']),
    ],
    'usdjpy': [
        ('JP', 3, 1.0, ['BoJ', 'Interest Rate', 'CPI', 'GDP']),
        ('US', 3, 1.0, ['FOMC', 'NFP', 'CPI', 'Nonfarm', 'Interest Rate', 'Core PCE', 'PPI', 'GDP', 'Unemployment', 'Retail Sales']),
    ],
    'xauusd': [],  # gold thrives on volatility
}


def match_event_name(name, keywords):
    if keywords is None:
        return True
    name_lower = name.lower()
    for kw in keywords:
        if kw.lower() in name_lower:
            return True
    return False


def check_trade_blocked(trade, calendar, rules):
    entry = pd.to_datetime(trade['entry'], utc=True)
    exit_ = pd.to_datetime(trade['exit'], utc=True)
    mid = entry + (exit_ - entry) / 2
    for country, imp_threshold, window_days, keywords in rules:
        win_start = mid - pd.Timedelta(days=window_days)
        win_end = mid + pd.Timedelta(days=window_days)
        mask = (calendar['t'] >= win_start) & (calendar['t'] <= win_end) \
             & (calendar['country_code'] == country) & (calendar['importance'] >= imp_threshold)
        if not mask.any():
            continue
        events = calendar[mask]
        for _, ev in events.iterrows():
            if match_event_name(ev['name'], keywords):
                return True
    return False


def calc_metrics(trades, use_alfaforex=False, sym=None):
    """Recalc metrics from raw trade list."""
    if not trades:
        return {'total_pnl': 0, 'win_rate': 0, 'profit_factor': 0,
                'max_drawdown': 0, 'total_trades': 0, 'sharpe_ratio': 0}
    pnls = [float(t['pnl_pips']) for t in trades]
    total_pnl = sum(pnls)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    wr = len(wins) / len(pnls) * 100 if pnls else 0
    gp = sum(wins) if wins else 0
    gl = abs(sum(losses)) if losses else 0
    pf = gp / gl if gl > 0 else (999 if gp > 0 else 0)
    cumulative = np.cumsum(pnls)
    running_max = np.maximum.accumulate(cumulative)
    drawdown = running_max - cumulative
    max_dd = float(np.max(drawdown)) if len(drawdown) > 0 else 0.0
    avg_pnl = np.mean(pnls)
    std_pnl = np.std(pnls) if len(pnls) > 1 else 1.0
    sharpe = float(avg_pnl / std_pnl) if std_pnl > 0 else 0.0
    return {'total_pnl': round(total_pnl, 1), 'win_rate': round(wr, 1),
            'profit_factor': pf, 'max_drawdown': round(max_dd, 1),
            'total_trades': len(pnls), 'sharpe_ratio': round(sharpe, 2)}


def main():
    # Load unfiltered results
    print("📂 Loading unfiltered trades from equity_results.json...")
    with open(OUTDIR / "equity_results.json") as f:
        unfiltered_data = json.load(f)

    # Load calendar
    print("📅 Loading calendar...", end=' ', flush=True)
    import psycopg2
    conn = psycopg2.connect(**DB)
    start_cal = "2024-12-29"
    end_cal = "2026-01-07"
    calendar = pd.read_sql(f"""
        SELECT event_time AT TIME ZONE 'UTC' as t, country_code, name, importance
        FROM economic_calendar
        WHERE event_time >= '{start_cal}' AND event_time < '{end_cal}'
        ORDER BY event_time
    """, conn)
    calendar['t'] = pd.to_datetime(calendar['t'], utc=True)
    conn.close()
    print(f"{len(calendar)} events")

    # Apply filter to each symbol
    filtered_results = {}
    summary_rows = []

    print(f"\n{'='*60}")
    print("SMART FILTER RESULTS")
    print(f"{'='*60}")

    for sym in SYMBOLS:
        entry = unfiltered_data.get(sym, {})
        trades = entry.get('trades', [])
        if not trades:
            print(f"  {sym:10s} No trades")
            filtered_results[sym] = entry
            continue

        rules = FILTER_RULES.get(sym, [])
        total_raw = len(trades)
        
        # Apply filter
        blocked = []
        passed = []
        for t in trades:
            if check_trade_blocked(t, calendar, rules):
                blocked.append(t)
            else:
                passed.append(t)

        # Calculate metrics
        u_metrics = calc_metrics(trades)
        f_metrics = calc_metrics(passed)

        blocked_count = total_raw - len(passed)
        em = '🟢' if f_metrics['win_rate'] >= 60 else ('⚠️' if f_metrics['win_rate'] >= 40 else '❌')

        delta_pnl = f_metrics['total_pnl'] - u_metrics['total_pnl']
        delta_wr = f_metrics['win_rate'] - u_metrics['win_rate']
        delta_sym = '🟢' if delta_pnl > 0 else ('🔴' if delta_pnl < 0 else '⚪')

        print(f"  {sym.upper():8s} "
              f"Filtered: {f_metrics['total_trades']:2d}tr  PnL={f_metrics['total_pnl']:>+6.0f}p  "
              f"WR={f_metrics['win_rate']:>4.1f}%  PF={f_metrics['profit_factor']:>5.2f}  "
              f"DD={f_metrics['max_drawdown']:>5.0f}p  Sharpe={f_metrics['sharpe_ratio']:.2f} {em}"
              f"  | blocked {blocked_count}/{total_raw}"
              f"  | ΔPnL={delta_pnl:>+4.0f}{delta_sym}  ΔWR={delta_wr:>+.1f}%")

        # Save filtered data
        filtered_entry = {
            'trades': passed,
            'blocked_trades': blocked,
            'blocked_count': blocked_count,
            'total_raw': total_raw,
            'total_pnl': f_metrics['total_pnl'],
            'winrate': f_metrics['win_rate'],
            'profit_factor': f_metrics['profit_factor'],
            'max_drawdown': f_metrics['max_drawdown'],
            'num_trades': f_metrics['total_trades'],
            'sharpe_ratio': f_metrics['sharpe_ratio'],
        }
        filtered_results[sym] = filtered_entry
        summary_rows.append({
            'symbol': sym, 'trades': f_metrics['total_trades'],
            'pnl': f_metrics['total_pnl'], 'wr': f_metrics['win_rate'],
            'pf': f_metrics['profit_factor'], 'dd': f_metrics['max_drawdown'],
            'sharpe': f_metrics['sharpe_ratio'], 'blocked': blocked_count,
            'raw': total_raw, 'emoji': em,
            'u_pnl': u_metrics['total_pnl'], 'u_wr': u_metrics['win_rate'],
        })

    # TOTAL
    total_pnl = sum(r['pnl'] for r in summary_rows)
    total_trades = sum(r['trades'] for r in summary_rows)
    total_wins = sum(r['trades'] * r['wr'] / 100 for r in summary_rows)
    total_wr = total_wins / total_trades * 100 if total_trades > 0 else 0
    total_raw = sum(r['raw'] for r in summary_rows)
    total_blocked = sum(r['blocked'] for r in summary_rows)

    u_total_pnl = sum(r['u_pnl'] for r in summary_rows)
    total_delta = total_pnl - u_total_pnl
    print(f"\n  TOTAL: {total_trades} trades, PnL: {total_pnl:+.0f}p, WR: {total_wr:.1f}%")
    print(f"  Blocked: {total_blocked}/{total_raw} ({total_blocked/max(total_raw,1)*100:.0f}%)")
    print(f"  vs Unfiltered: {u_total_pnl:+.0f}p → Δ = {total_delta:+.0f}p {'🟢' if total_delta > 0 else '🔴'}")

    # Save filtered results
    out_path = OUTDIR / "equity_results_smart_filtered.json"
    with open(out_path, 'w') as f:
        json.dump(filtered_results, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n📊 Saved: {out_path}")

    # Plot
    try:
        print("📈 Generating chart...")
        all_trades = {r['symbol']: filtered_results[r['symbol']]['trades'] for r in summary_rows}
        fig, axes = plt.subplots(4, 4, figsize=(24, 18))
        fig.patch.set_facecolor('#0d1117')
        for idx, sym in enumerate(SYMBOLS):
            ax = axes[idx // 4][idx % 4]
            ax.set_facecolor('#161b22')
            trades = all_trades.get(sym, [])
            if trades:
                trades_sorted = sorted(trades, key=lambda t: t['entry'])
                dates = [t['entry'] for t in trades_sorted]
                cum = 0
                equity_vals = []
                for t in trades_sorted:
                    cum += t['pnl_pips']
                    equity_vals.append(cum)
                pnl = cum
                wr = sum(1 for t in trades if t['won']) / len(trades) * 100
                clr = '#3fb950' if pnl >= 0 else '#f85149'
                ax.plot(dates, equity_vals, color=clr, linewidth=2)
                ax.fill_between(dates, 0, equity_vals, color=clr, alpha=0.15)
                for i, t in enumerate(trades_sorted):
                    mc = '#3fb950' if t['won'] else '#f85149'
                    ax.scatter(t['entry'], equity_vals[i], color=mc, s=20, zorder=5)
                ax.set_title(f"{sym.upper()}  PnL={pnl:+.0f}p  WR={wr:.0f}%", color='#e6edf3', fontsize=11)
            else:
                ax.set_title(f"{sym.upper()}  No trades", color='#8b949e', fontsize=11)
            ax.tick_params(colors='#8b949e', labelsize=8)
            ax.grid(color='#21262d', linewidth=0.3)
            for sp in ax.spines.values():
                sp.set_color('#30363d')
            for lbl in ax.get_xticklabels():
                lbl.set_rotation(45)
                lbl.set_fontsize(7)
        for idx in range(len(SYMBOLS), 16):
            axes[idx // 4][idx % 4].set_visible(False)
        plt.tight_layout()
        chart_path = OUTDIR / "equity_all_symbols_smart_filtered.png"
        plt.savefig(str(chart_path), dpi=150, facecolor='#0d1117', bbox_inches='tight')
        plt.close()
        print(f"📈 Chart: {chart_path}")
    except Exception as e:
        print(f"  Plot error: {e}")

    print(f"\n✅ Done")


if __name__ == '__main__':
    main()
