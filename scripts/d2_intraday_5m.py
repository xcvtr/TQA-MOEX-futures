#!/usr/bin/env python3
"""D2: Intraday 5-min signals — first 6 bars signal for the rest of the day."""
import sys, os, json, numpy as np, pandas as pd, clickhouse_connect
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import CH_HOST, CH_PORT, CH_DB

INITIAL_CAPITAL = 100_000
COMMISSION = 4

TICKERS = {
    'Si': {'margin': 1000, 'secid': 'Si'},
    'BR': {'margin': 3000, 'secid': 'BR'},
    'CR': {'margin': 5000, 'secid': 'CR'},
    'AF': {'margin': 5000, 'secid': 'AF'},
    'GZ': {'margin': 2065, 'secid': 'GZ'},
    'SR': {'margin': 5719, 'secid': 'SR'},
}

def rz(s, w=48):
    m = s.rolling(w, min_periods=w).mean()
    std = s.rolling(w, min_periods=w).std()
    return (s - m) / std.clip(lower=1e-10)

def load_5m(ticker, db_ticker):
    ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)
    q = f"""
        SELECT tradedate, tradetime, pr_open, pr_high, pr_low, pr_close, vol_sum, oi_change
        FROM moex.supercandles_fo
        WHERE ticker='{db_ticker}' AND tradedate >= '2020-01-01'
        ORDER BY tradetime
    """
    r = ch.query(q)
    df = pd.DataFrame(r.result_rows, columns=r.column_names)
    df['tradedate'] = pd.to_datetime(df['tradedate'])
    df['tradetime'] = pd.to_datetime(df['tradetime'])
    df['hour'] = df['tradetime'].dt.hour
    df['minute'] = df['tradetime'].dt.minute
    return df

def run_backtest(ticker, db_ticker, margin, threshold, long_only):
    df = load_5m(ticker, db_ticker)
    if len(df) < 1000:
        return None

    df['vol_z'] = rz(df['vol_sum'].astype(float))
    df['oi_z'] = rz(df['oi_change'].astype(float))

    dates = sorted(df['tradedate'].unique())
    dates = [d for d in dates if '2020-01-01' <= str(d.date()) <= '2026-06-20']

    eq = float(INITIAL_CAPITAL)
    peak = INITIAL_CAPITAL
    max_dd = 0.0
    n_trades = 0
    daily_returns = []

    for dt in dates:
        day_bars = df[df['tradedate'] == dt].copy().sort_values('tradetime')
        if len(day_bars) < 20:
            continue

        first_6 = day_bars.head(6)
        if len(first_6) < 6:
            continue

        sig_bar = first_6.iloc[-1]
        vz = sig_bar['vol_z']
        if pd.isna(vz):
            daily_returns.append(0.0)
            continue

        signal = 0
        if long_only:
            if vz > threshold:
                signal = 1
        else:
            if vz > threshold:
                signal = 1
            elif vz < -threshold:
                signal = -1

        if signal == 0:
            daily_returns.append(0.0)
            continue

        # Enter at bar #7 close (10:30)
        rest = day_bars.iloc[6:]
        if len(rest) < 2:
            daily_returns.append(0.0)
            continue
        entry_price = rest.iloc[0]['pr_close']
        if pd.isna(entry_price) or entry_price <= 0:
            daily_returns.append(0.0)
            continue

        pos_open = True
        pos_return = 0.0
        if signal == 1:
            stop_price = entry_price * (1 - 0.005)
        else:
            stop_price = entry_price * (1 + 0.005)

        for idx in rest.index:
            bar = rest.loc[idx]
            if not pos_open:
                continue
            if signal == 1 and bar['pr_low'] <= stop_price:
                exit_px = stop_price
                pos_return = (exit_px / entry_price - 1)
                pos_open = False
                break
            elif signal == -1 and bar['pr_high'] >= stop_price:
                exit_px = stop_price
                pos_return = (exit_px / entry_price - 1)
                pos_open = False
                break
            tm = f"{bar['hour']:02d}:{bar['minute']:02d}"
            if tm >= '18:45':
                exit_px = bar['pr_close']
                pos_return = (exit_px / entry_price - 1)
                pos_open = False
                break

        if not pos_open and pos_return != 0.0:
            ret = signal * pos_return
            cap_per = eq
            if cap_per < margin:
                daily_returns.append(0.0)
                continue
            cont = int(cap_per / margin)
            if cont < 1:
                daily_returns.append(0.0)
                continue
            contrib = ret * (cont * margin / eq)
            comm = (cont * COMMISSION * 2) / eq
            day_pnl = contrib - comm
            eq *= (1 + day_pnl)
            if eq > peak:
                peak = eq
            dd = eq / peak - 1
            if dd < max_dd:
                max_dd = dd
            n_trades += 1
            daily_returns.append(day_pnl)
        else:
            daily_returns.append(0.0)

    if len(daily_returns) < 20 or eq <= 0:
        return None

    total_ret = (eq / INITIAL_CAPITAL - 1) * 100
    years = len(daily_returns) / 252
    cagr = ((eq / INITIAL_CAPITAL) ** (1 / years) - 1) * 100 if years > 0 else -100.0
    sharpe = np.mean(daily_returns) / np.std(daily_returns) * np.sqrt(252) if np.std(daily_returns) > 0 else 0
    calmar = cagr / abs(max_dd * 100) if max_dd != 0 else 0

    return {
        'ticker': ticker, 'threshold': threshold, 'long_only': long_only,
        'ret_pct': round(total_ret, 1),
        'cagr': round(cagr, 1),
        'dd': round(max_dd * 100, 1),
        'sharpe': round(sharpe, 2),
        'calmar': round(calmar, 2),
        'trades': n_trades,
    }

def main():
    print("=" * 60)
    print("D2: Intraday 5-min Signals")
    print("=" * 60)

    THRESHOLDS = [1.0, 1.5, 2.0, 2.5, 3.0]

    all_results = []
    for long_only in [True, False]:
        label = "LONG-only" if long_only else "LONG+SHORT"
        print(f"\n=== Mode: {label} ===")
        for ticker, cfg in list(TICKERS.items()):
            print(f"\n--- {ticker} ---")
            for th in THRESHOLDS:
                r = run_backtest(ticker, cfg['secid'], cfg['margin'], th, long_only)
                if r is not None:
                    all_results.append(r)
                    print(f"  th={th:.1f} → CAGR={r['cagr']}% DD={r['dd']}% Calmar={r['calmar']:.2f} Trd={r['trades']}")

    all_results.sort(key=lambda r: (-r['calmar'], -r['cagr'], r['dd']))

    print("\n" + "=" * 80)
    print("D2 RESULTS — Top 30 by Calmar")
    print("=" * 80)
    hdr = f"{'Ticker':>6} {'mode':>10} {'th':>4} {'Ret%':>7} {'CAGR%':>7} {'DD%':>6} {'Sharpe':>7} {'Calmar':>7} {'Trd':>5}"
    print(hdr)
    print("-" * 80)
    for r in all_results[:30]:
        mode = "LO" if r['long_only'] else "LS"
        print(f"{r['ticker']:>6} {mode:>10} {r['threshold']:>4.1f} {r['ret_pct']:>7.1f} {r['cagr']:>7.1f} {r['dd']:>6.1f} {r['sharpe']:>7.2f} {r['calmar']:>7.2f} {r['trades']:>5}")

    best_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'reports', 'd2_best.json')
    with open(best_path, 'w') as f:
        json.dump(all_results, f, indent=2)
    print(f"\nSaved to {best_path}")
    return all_results

if __name__ == '__main__':
    main()
