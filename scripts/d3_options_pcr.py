#!/usr/bin/env python3
"""D3: Options Put/Call Ratio strategy."""
import sys, os, json, numpy as np, pandas as pd, clickhouse_connect
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import CH_HOST, CH_PORT, CH_DB

INITIAL_CAPITAL = 100_000
COMMISSION_PCT = 0.005  # 0.5% per trade

TICKERS = ['Si', 'BR']

def load_options(base):
    ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)
    q = f"""
        SELECT toDate(time) as dt,
               option_type,
               sum(volume_today) as vol
        FROM moex.options_board
        WHERE base='{base}' AND volume_today > 0
        GROUP BY dt, option_type
        ORDER BY dt, option_type
    """
    r = ch.query(q)
    df = pd.DataFrame(r.result_rows, columns=r.column_names)
    df['dt'] = pd.to_datetime(df['dt'])
    return df

def rz(s, w=5):
    m = s.rolling(w, min_periods=w).mean()
    std = s.rolling(w, min_periods=w).std()
    return (s - m) / std.clip(lower=1e-10)

def run_backtest(base, threshold):
    df = load_options(base)
    if len(df) < 10:
        return None

    # Pivot: get put and call volumes per day
    calls = df[df['option_type'] == 'C'][['dt', 'vol']].rename(columns={'vol': 'call_vol'})
    puts = df[df['option_type'] == 'P'][['dt', 'vol']].rename(columns={'vol': 'put_vol'})
    merged = pd.merge(calls, puts, on='dt', how='inner')
    if len(merged) < 5:
        return None

    merged = merged.sort_values('dt')
    merged['pc_ratio'] = merged['put_vol'] / merged['call_vol'].clip(lower=1)
    merged['pc_z'] = rz(merged['pc_ratio'])
    merged['ret_next'] = merged['pc_ratio'].pct_change().shift(-1)
    merged = merged.dropna(subset=['pc_z', 'ret_next'])

    eq = float(INITIAL_CAPITAL)
    peak = INITIAL_CAPITAL
    max_dd = 0.0
    n_trades = 0
    daily_returns = []

    for _, row in merged.iterrows():
        sig = 0
        if row['pc_z'] > threshold:
            sig = -1  # High put/call → bearish → SHORT
        elif row['pc_z'] < -threshold:
            sig = 1   # Low put/call → bullish → LONG

        if sig == 0:
            daily_returns.append(0.0)
            continue

        ret = sig * row['ret_next']
        comm = COMMISSION_PCT * 2  # 0.5% entry + 0.5% exit
        net_ret = ret - comm
        n_trades += 1

        eq *= (1 + net_ret)
        if eq > peak:
            peak = eq
        dd = eq / peak - 1
        if dd < max_dd:
            max_dd = dd
        daily_returns.append(net_ret)

    if len(daily_returns) < 3:
        return None

    total_ret = (eq / INITIAL_CAPITAL - 1) * 100
    years = len(daily_returns) / 252
    if years > 0 and eq > 0:
        cagr = ((eq / INITIAL_CAPITAL) ** (1 / years) - 1) * 100
    elif eq <= 0:
        cagr = -100.0
    else:
        cagr = 0.0
    sharpe = np.mean(daily_returns) / np.std(daily_returns) * np.sqrt(252) if np.std(daily_returns) > 0 else 0
    calmar = cagr / abs(max_dd * 100) if max_dd != 0 else 0

    return {
        'base': base, 'threshold': threshold,
        'ret_pct': round(total_ret, 1),
        'cagr': round(cagr, 1),
        'dd': round(max_dd * 100, 1),
        'sharpe': round(sharpe, 2),
        'calmar': round(calmar, 2),
        'trades': n_trades,
        'n_days': len(daily_returns),
    }

def main():
    print("=" * 60)
    print("D3: Options Put/Call Ratio")
    print("=" * 60)

    THRESHOLDS = [0.5, 1.0, 1.5, 2.0]

    all_results = []
    for base in TICKERS:
        print(f"\n--- {base} ---")
        for th in THRESHOLDS:
            r = run_backtest(base, th)
            if r is not None:
                all_results.append(r)
                print(f"  th={th:.1f} → CAGR={r['cagr']}% DD={r['dd']}% Calmar={r['calmar']:.2f} Trd={r['trades']} ({r['n_days']}d)")

    all_results.sort(key=lambda r: (-r['calmar'], -r['cagr'], r['dd']))

    print("\n" + "=" * 70)
    print("D3 RESULTS — All Configs by Calmar")
    print("=" * 70)
    hdr = f"{'Base':>6} {'th':>4} {'Ret%':>7} {'CAGR%':>7} {'DD%':>6} {'Sharpe':>7} {'Calmar':>7} {'Trd':>5}"
    print(hdr)
    print("-" * 70)
    for r in all_results:
        print(f"{r['base']:>6} {r['threshold']:>4.1f} {r['ret_pct']:>7.1f} {r['cagr']:>7.1f} {r['dd']:>6.1f} {r['sharpe']:>7.2f} {r['calmar']:>7.2f} {r['trades']:>5}")

    best_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'reports', 'd3_best.json')
    with open(best_path, 'w') as f:
        json.dump(all_results, f, indent=2)
    print(f"\nSaved to {best_path}")
    return all_results

if __name__ == '__main__':
    main()
