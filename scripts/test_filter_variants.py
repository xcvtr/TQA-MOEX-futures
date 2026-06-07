#!/home/user/venvs/tqa/main/bin/python
"""Test multiple filter variants per pair, find best by WR."""
import json, os, sys, warnings
from pathlib import Path
import pandas as pd
import numpy as np

warnings.filterwarnings('ignore')

DB = dict(host="10.0.0.64", port=5432, dbname="forex", user="postgres", password="postgres")
OUTDIR = Path("/home/user/.hermes/cache/screenshots/tqa/equity_cluster/2025")
PIP_VALUE_USD = 10.0

SYMBOLS = ['audjpy','audusd','euraud','eurgbp','eurjpy','eurusd',
           'gbpjpy','gbpusd','nzdusd','usdcad','usdchf','usdjpy','xauusd']

PAIR_COUNTRIES = {
    'audjpy': ['AU', 'JP'], 'audusd': ['AU', 'US'], 'euraud': ['EU', 'AU'],
    'eurgbp': ['EU', 'GB'], 'eurjpy': ['EU', 'JP'], 'eurusd': ['EU', 'US'],
    'gbpjpy': ['GB', 'JP'], 'gbpusd': ['GB', 'US'], 'nzdusd': ['NZ', 'US'],
    'usdcad': ['US', 'CA'], 'usdchf': ['US', 'CH'], 'usdjpy': ['US', 'JP'],
    'xauusd': ['US'],
}

# ---- FILTER VARIANTS TO TEST ----
# Each variant is a list of (country, imp_threshold, window_days, keywords_or_None)
# keywords=None means ALL event types at that importance level

FILTER_VARIANTS = {
    'rates_only': {
        'desc': 'Только ставки ±1д',
        'rules_builder': lambda countries: [
            (c, 3, 1.0, ['Interest Rate', 'Rate Decision']) for c in countries
        ],
    },
    'rates_cpi': {
        'desc': 'Ставки + CPI ±1д',
        'rules_builder': lambda countries: [
            (c, 3, 1.0, ['Interest Rate', 'Rate Decision', 'CPI']) for c in countries
        ],
    },
    'rates_cpi_nfp': {
        'desc': 'Ставки + CPI + NFP ±1д',
        'rules_builder': lambda countries: [
            (c, 3, 1.0, ['Interest Rate', 'Rate Decision', 'CPI', 'Nonfarm', 'NFP', 'Unemployment']) for c in countries
        ],
    },
    'key_us': {
        'desc': 'Ключевые US (FOMC,NFP,CPI,GDP,PCE) + ставки своих стран',
        'rules_builder': lambda countries: [
            (c, 3, 1.0, ['Interest Rate', 'Rate Decision']) for c in countries if c != 'US'
        ] + ([('US', 3, 1.0, ['FOMC', 'NFP', 'CPI', 'Nonfarm', 'Interest Rate', 'GDP', 'Core PCE', 'Unemployment'])] if 'US' in countries else []),
    },
    'all_imp3_country': {
        'desc': 'Все imp≥3 релевантных стран ±1д',
        'rules_builder': lambda countries: [
            (c, 3, 1.0, None) for c in countries if c not in ['US', 'EU']  # non-US/EU: all events
        ] + ([('US', 3, 1.0, ['FOMC', 'NFP', 'CPI', 'Nonfarm', 'Interest Rate', 'Core PCE', 'PPI', 'GDP', 'Unemployment', 'Retail Sales', 'ISM'])] if 'US' in countries else [])  # US: key only
          + ([('EU', 3, 1.0, None)] if 'EU' in countries else []),  # EU: all
    },
    'us_heavy': {
        'desc': 'US все imp≥3 + свои ставки',
        'rules_builder': lambda countries: [
            (c, 3, 1.0, ['Interest Rate', 'Rate Decision']) for c in countries if c != 'US'
        ] + ([('US', 3, 1.0, None)] if 'US' in countries else []),
    },
    'all_imp3_wide': {
        'desc': 'Все imp≥3 ±2д',
        'rules_builder': lambda countries: [
            (c, 3, 2.0, None) for c in countries
        ],
    },
    'no_us_noise': {
        'desc': 'US без weekly noise (нет EIA/Claims) + свои ставки',
        'rules_builder': lambda countries: [
            (c, 3, 1.0, ['Interest Rate', 'Rate Decision']) for c in countries if c != 'US'
        ] + ([('US', 3, 1.0, ['FOMC', 'NFP', 'CPI', 'Nonfarm', 'Interest Rate', 'Core PCE', 'PPI', 'GDP', 'Unemployment', 'Retail Sales', 'ISM', 'Durable Goods', 'JOLTS'])] if 'US' in countries else []),
    },
    'huge': {
        'desc': 'Только FOMC+NFP+CPI ±2д',
        'rules_builder': lambda countries: [
            (c, 3, 2.0, ['Interest Rate', 'Rate Decision']) for c in countries
        ] + ([('US', 3, 2.0, ['FOMC', 'NFP', 'CPI', 'Nonfarm'])] if 'US' in countries else []),
    },
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


def calc_metrics(trades):
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
    # Load trades
    print("📂 Loading trades from equity_results.json...")
    with open(OUTDIR / "equity_results.json") as f:
        all_data = json.load(f)

    # Load calendar
    print("📅 Loading calendar...", end=' ', flush=True)
    import psycopg2
    conn = psycopg2.connect(**DB)
    calendar = pd.read_sql("""
        SELECT event_time AT TIME ZONE 'UTC' as t, country_code, name, importance
        FROM economic_calendar
        WHERE event_time >= '2024-12-29' AND event_time < '2026-01-07'
        ORDER BY event_time
    """, conn)
    calendar['t'] = pd.to_datetime(calendar['t'], utc=True)
    conn.close()
    print(f"{len(calendar)} events loaded\n")

    # For each symbol, test all variants
    best_configs = {}

    for sym in SYMBOLS:
        entry = all_data.get(sym, {})
        trades = entry.get('trades', [])
        if not trades:
            print(f"\n{'='*60}")
            print(f"  {sym.upper()} — NO TRADES")
            print(f"{'='*60}")
            continue

        base = calc_metrics(trades)
        countries = PAIR_COUNTRIES.get(sym, [])
        print(f"\n{'='*60}")
        print(f"  {sym.upper()} | {base['total_trades']} trades | "
              f"Base: PnL={base['total_pnl']:+.0f}p WR={base['win_rate']:.1f}% PF={base['profit_factor']:.2f}")
        print(f"  Countries: {', '.join(countries)}")
        print(f"{'='*60}")

        results = []
        for vname, vinfo in FILTER_VARIANTS.items():
            rules = vinfo['rules_builder'](countries)
            if not rules:
                continue

            passed = []
            blocked = []
            for t in trades:
                if check_trade_blocked(t, calendar, rules):
                    blocked.append(t)
                else:
                    passed.append(t)

            if not passed:
                print(f"  ❌ {vname:20s} {vinfo['desc']:45s} ALL TRADES BLOCKED")
                continue

            m = calc_metrics(passed)
            delta_pnl = m['total_pnl'] - base['total_pnl']
            delta_wr = m['win_rate'] - base['win_rate']
            em = '🟢' if delta_pnl > 0 else ('🔴' if delta_pnl < 0 else '⚪')
            em_wr = '🟢' if delta_wr > 0 else ('🔴' if delta_wr < 0 else '⚪')

            results.append({
                'name': vname,
                'desc': vinfo['desc'],
                'trades': m['total_trades'],
                'pnl': m['total_pnl'],
                'wr': m['win_rate'],
                'pf': m['profit_factor'],
                'dd': m['max_drawdown'],
                'sharpe': m['sharpe_ratio'],
                'blocked': len(blocked),
                'blocked_pct': len(blocked) / len(trades) * 100,
                'delta_pnl': delta_pnl,
                'delta_wr': delta_wr,
                'rules': rules,
            })

            print(f"  {em:2s} {vname:20s} {m['total_trades']:2d}tr  "
                  f"PnL={m['total_pnl']:>+6.0f}p  "
                  f"WR={m['win_rate']:>5.1f}%  "
                  f"PF={m['profit_factor']:>5.2f}  "
                  f"DD={m['max_drawdown']:>5.0f}p  "
                  f"Sharpe={m['sharpe_ratio']:.2f}  "
                  f"blocked {len(blocked):2d}/{len(trades):2d}  "
                  f"ΔPnL={delta_pnl:>+4.0f}{em}  ΔWR={delta_wr:>+.1f}%{em_wr}")

        if not results:
            continue

        # Rank by WR (primary), then PnL (secondary)
        ranked_by_wr = sorted(results, key=lambda r: (-r['wr'], -r['pnl']))
        best_wr = ranked_by_wr[0]
        best_by_scr = ranked_by_wr[0]  # same ranking

        print(f"\n  🏆 Best by WR: {best_wr['name']:20s} → "
              f"WR={best_wr['wr']:.1f}%  PnL={best_wr['pnl']:+.0f}p  "
              f"PF={best_wr['pf']:.2f}  blocked {best_wr['blocked']}/{best_wr['blocked']+best_wr['trades']}")

        # Also find best by PnL
        best_by_pnl = max(results, key=lambda r: r['pnl'])
        if best_by_pnl['name'] != best_wr['name']:
            print(f"  🥇 Best by PnL: {best_by_pnl['name']:20s} → "
                  f"PnL={best_by_pnl['pnl']:+.0f}p  WR={best_by_pnl['wr']:.1f}%  "
                  f"PF={best_by_pnl['pf']:.2f}")

        # Store best config
        best_configs[sym] = {
            'best_wr': {'variant': best_wr['name'], 'desc': best_wr['desc'], 'metrics': {
                'trades': best_wr['trades'], 'pnl': best_wr['pnl'],
                'wr': best_wr['wr'], 'pf': best_wr['pf'],
                'dd': best_wr['dd'], 'sharpe': best_wr['sharpe'],
                'blocked': best_wr['blocked']}},
            'best_pnl': {'variant': best_by_pnl['name'], 'desc': best_by_pnl['desc'], 'metrics': {
                'trades': best_by_pnl['trades'], 'pnl': best_by_pnl['pnl'],
                'wr': best_by_pnl['wr'], 'pf': best_by_pnl['pf'],
                'dd': best_by_pnl['dd'], 'sharpe': best_by_pnl['sharpe'],
                'blocked': best_by_pnl['blocked']}},
            'base': {'trades': base['total_trades'], 'pnl': base['total_pnl'],
                     'wr': base['win_rate'], 'pf': base['profit_factor']},
        }

    # Final summary
    print(f"\n\n{'='*60}")
    print("FINAL SUMMARY — BEST PER PAIR")
    print(f"{'='*60}")
    print(f"{'Symbol':8s} {'Base WR':>8s} {'Base PnL':>8s} | {'Best WR':>8s} {'Var':18s} {'PnL':>8s} {'PF':>5s} {'Blocked':>7s} | {'Best PnL':>8s} {'Var':18s}")
    print("-" * 95)

    total_base_pnl = 0
    total_best_wr_pnl = 0
    total_best_pnl = 0
    total_base_trades = 0
    total_best_wr_trades = 0

    for sym in SYMBOLS:
        cfg = best_configs.get(sym)
        if not cfg:
            continue
        b = cfg['base']
        wr = cfg['best_wr']
        pnl = cfg['best_pnl']
        em_wr = '🟢' if wr['metrics']['wr'] >= 60 else ('⚠️' if wr['metrics']['wr'] >= 40 else '❌')
        print(f"  {sym:8s} {b['wr']:>6.1f}%  {b['pnl']:>+6.0f}p | "
              f"{wr['metrics']['wr']:>5.1f}%  {wr['variant']:18s} {wr['metrics']['pnl']:>+6.0f}p "
              f"{wr['metrics']['pf']:>4.2f}  {wr['metrics']['blocked']:>3d}/{b['trades']:<2d} | "
              f"{pnl['metrics']['pnl']:>+6.0f}p  {pnl['variant']:18s} {em_wr}")
        total_base_pnl += b['pnl']
        total_best_wr_pnl += wr['metrics']['pnl']
        total_best_pnl += pnl['metrics']['pnl']
        total_base_trades += b['trades']
        total_best_wr_trades += wr['metrics']['trades']

    print("-" * 95)
    print(f"  {'TOTAL':8s} {total_base_trades:3d}tr  {total_base_pnl:>+6.0f}p | "
          f"{total_best_wr_trades:3d}tr  {'':18s} {total_best_wr_pnl:>+6.0f}p | "
          f"{total_best_pnl:>+6.0f}p")
    delta_wr = total_best_wr_pnl - total_base_pnl
    delta_pnl = total_best_pnl - total_base_pnl
    print(f"  Δ PnL (best WR): {delta_wr:+.0f}p {'🟢' if delta_wr > 0 else '🔴'}")
    print(f"  Δ PnL (best PnL): {delta_pnl:+.0f}p {'🟢' if delta_pnl > 0 else '🔴'}")

    # Save best configs
    with open(OUTDIR / "best_filter_configs.json", 'w') as f:
        json.dump(best_configs, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n📊 Saved: {OUTDIR / 'best_filter_configs.json'}")


if __name__ == '__main__':
    main()
