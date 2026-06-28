#!/usr/bin/env python3
"""
Volume Profile S/R Test on MOEX Futures (v3 — SQL-optimized)
=============================================================
Computes Point of Control directly in ClickHouse using intraday data.
Batches: compute POC per ticker per day in a single SQL query.
"""

import clickhouse_connect
import pandas as pd
import numpy as np

CLICKHOUSE_HOST = '10.0.0.60'
CLICKHOUSE_PORT = 8123
DATABASE = 'moex'
TICKERS = ['Si', 'GZ', 'CR']
START_DATE = '2024-10-01'
POC_ZONES = 20
POC_TOLERANCE = 0.005
FORWARD_BARS = [3, 6, 12]
MIN_WINRATE = 0.52
MAX_PRICE_JUMP = 0.50

client = clickhouse_connect.get_client(host=CLICKHOUSE_HOST, port=CLICKHOUSE_PORT, database=DATABASE)


def get_daily_poc(ticker_symbol):
    """
    Compute POC directly in ClickHouse SQL.
    For each day:
      1. Get daily high/low
      2. For each intraday bar, assign to one of 20 price zones
      3. Sum volume per zone, find zone with max volume = POC
    Returns: date, close, low, high, poc_mid, poc_vol_share
    """
    query = f"""
    WITH daily AS (
        SELECT toDate(SYSTIME) as d,
               argMax(pr_close, SYSTIME) as close,
               argMax(pr_low, SYSTIME) as lo,
               argMax(pr_high, SYSTIME) as hi,
               sum(vol) as total_vol
        FROM moex.tradestats_fo
        WHERE secid LIKE '{ticker_symbol}%'
          AND SYSTIME >= '{START_DATE}'
          AND SYSTIME < '2026-06-20'
          AND vol > 0 AND pr_close > 0
        GROUP BY d
        HAVING total_vol > 0
    ),
    intra_by_day AS (
        SELECT toDate(SYSTIME) as d,
               pr_close as price,
               vol
        FROM moex.tradestats_fo
        WHERE secid LIKE '{ticker_symbol}%'
          AND SYSTIME >= '{START_DATE}'
          AND SYSTIME < '2026-06-20'
          AND vol > 0 AND pr_close > 0
    ),
    zone_volumes AS (
        SELECT i.d,
               d.close,
               d.lo,
               d.hi,
               d.total_vol,
               -- Compute zone index: floor((price - lo) / ((hi - lo)/20))
               -- Clamp to [0, 19]
               GREATEST(0, LEAST(19, floor((i.price - d.lo) / GREATEST((d.hi - d.lo)/20, 1))::Int64)) as zone_idx,
               i.vol
        FROM intra_by_day i
        JOIN daily d ON i.d = d.d
    ),
    zone_totals AS (
        SELECT d, close, lo, hi, total_vol, zone_idx,
               sum(vol) as zone_vol
        FROM zone_volumes
        GROUP BY d, close, lo, hi, total_vol, zone_idx
    ),
    ranked AS (
        SELECT d, close, lo, hi, total_vol, zone_idx, zone_vol,
               ROW_NUMBER() OVER (PARTITION BY d ORDER BY zone_vol DESC) as rn
        FROM zone_totals
    ),
    poc_zone AS (
        SELECT d, close, lo, hi, total_vol, zone_idx, zone_vol
        FROM ranked
        WHERE rn = 1
    )
    SELECT d, close, lo, hi,
           lo + ((hi - lo)/20) * zone_idx as poc_low,
           lo + ((hi - lo)/20) * (zone_idx + 1) as poc_high,
           zone_vol,
           total_vol
    FROM poc_zone
    ORDER BY d
    """
    result = client.query(query)
    rows = result.result_rows
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=['date', 'close', 'low', 'high', 'poc_low', 'poc_high', 'zone_vol', 'total_vol'])
    df['date'] = pd.to_datetime(df['date'])
    df['poc_mid'] = (df['poc_low'] + df['poc_high']) / 2
    df['poc_vol_share'] = df['zone_vol'] / df['total_vol'].replace(0, np.nan)
    return df


def get_daily_agg(ticker_symbol):
    """Daily OHLCV with contract-roll filtering."""
    query = f"""
        SELECT toDate(SYSTIME) as d,
               argMax(pr_close, SYSTIME) as close,
               sum(vol) as vol,
               argMax(pr_low, SYSTIME) as lo,
               argMax(pr_high, SYSTIME) as hi
        FROM moex.tradestats_fo
        WHERE secid LIKE '{ticker_symbol}%'
          AND SYSTIME >= '{START_DATE}'
          AND SYSTIME < '2026-06-20'
        GROUP BY d
        ORDER BY d
    """
    result = client.query(query)
    rows = result.result_rows
    df = pd.DataFrame(rows, columns=['date', 'close', 'vol', 'low', 'high'])
    df['date'] = pd.to_datetime(df['date'])
    df['pct_chg'] = df['close'].pct_change().abs()
    roll = df['pct_chg'] > MAX_PRICE_JUMP
    if roll.any():
        drop_idx = set()
        for idx in df[roll].index:
            drop_idx.add(idx)
            if idx + 1 < len(df):
                drop_idx.add(idx + 1)
        df = df.drop(index=list(drop_idx)).reset_index(drop=True)
        print(f"  Removed {len(drop_idx)} contract-roll days")
    return df


def generate_signals(df_daily, poc_df):
    """Generate signals from POC S/R test."""
    poc_by_date = poc_df.set_index('date')
    signals = []

    for i in range(len(df_daily) - 1):
        today = df_daily.iloc[i]
        tomorrow = df_daily.iloc[i + 1]

        if today['date'] not in poc_by_date.index:
            continue

        poc = poc_by_date.loc[today['date']]
        if isinstance(poc, pd.DataFrame):
            poc = poc.iloc[0]

        poc_mid = float(poc['poc_mid'])
        tol_low = poc_mid * (1 - POC_TOLERANCE)
        tol_high = poc_mid * (1 + POC_TOLERANCE)

        t_low = tomorrow['low']
        t_high = tomorrow['high']
        t_close = tomorrow['close']

        if t_low <= tol_high and t_close > poc_mid:
            sig_type = 'LONG'
        elif t_high >= tol_low and t_close < poc_mid:
            sig_type = 'SHORT'
        else:
            continue

        signals.append({
            'poc_date': today['date'].strftime('%Y-%m-%d'),
            'entry_date': tomorrow['date'].strftime('%Y-%m-%d'),
            'type': sig_type,
            'poc_mid': round(poc_mid, 4),
            'poc_low': round(float(poc['poc_low']), 4),
            'poc_high': round(float(poc['poc_high']), 4),
            'poc_vol_share': round(float(poc['poc_vol_share']), 4),
            'entry_price': round(t_close, 4),
        })

    return signals


def forward_returns(df, signal):
    """Forward N-bar returns."""
    ed = pd.Timestamp(signal['entry_date'])
    idx = df[df['date'] == ed].index
    if len(idx) == 0:
        return {}
    idx = idx[0]
    ep = signal['entry_price']
    st = signal['type']
    rets = {}
    for n in FORWARD_BARS:
        if idx + n < len(df):
            fc = df.iloc[idx + n]['close']
            if abs(fc - ep) / ep > MAX_PRICE_JUMP:
                rets[f'ret_{n}'] = None
                continue
            r = (fc - ep) / ep * 100 if st == 'LONG' else (ep - fc) / ep * 100
            rets[f'ret_{n}'] = round(r, 2) if abs(r) < 500 else None
        else:
            rets[f'ret_{n}'] = None
    return rets


def run_test(ticker):
    print(f"\n{'='*60}")
    print(f"📊 Testing {ticker}...")
    print(f"{'='*60}")

    print("  Querying daily POC from intraday data...")
    poc_df = get_daily_poc(ticker)
    print(f"  POC days: {len(poc_df)}")

    df_daily = get_daily_agg(ticker)
    print(f"  Daily bars (filtered): {len(df_daily)}")

    if len(poc_df) == 0:
        print("  ❌ No POC data")
        return None, []

    signals = generate_signals(df_daily, poc_df)
    print(f"  Raw signals: {len(signals)}")

    if not signals:
        return None, []

    for s in signals:
        s.update(forward_returns(df_daily, s))

    valid = [s for s in signals if any(s.get(f'ret_{n}') is not None for n in FORWARD_BARS)]
    print(f"  Valid signals: {len(valid)}")

    # Stats
    results = {}
    for st in ['LONG', 'SHORT']:
        typed = [s for s in valid if s['type'] == st]
        if not typed:
            continue
        stats = {'count': len(typed)}
        for n in FORWARD_BARS:
            k = f'ret_{n}'
            rets = [s[k] for s in typed if s.get(k) is not None]
            if rets:
                wins = sum(1 for r in rets if r > 0)
                stats[f'{k}_win'] = wins
                stats[f'{k}_loss'] = len(rets) - wins
                stats[f'{k}_wr'] = round(wins / len(rets), 4)
                stats[f'{k}_avg'] = round(float(np.mean(rets)), 2)
                stats[f'{k}_total'] = round(float(np.sum(rets)), 2)
                stats[f'{k}_count'] = len(rets)
        stats['avg_poc_share'] = round(float(np.mean([s['poc_vol_share'] for s in typed])), 4)
        results[st] = stats

    return results, valid


def print_results(ticker, results, signals):
    if results is None or not signals:
        print(f"\n  {ticker}: ❌ No signals")
        return

    print(f"\n  {'─'*60}")
    for st in ['LONG', 'SHORT']:
        if st not in results:
            continue
        s = results[st]
        print(f"\n  {st} ({s['count']} sigs, POC share={s.get('avg_poc_share',0)*100:.1f}%)")
        for n in FORWARD_BARS:
            k = f'ret_{n}'
            w = s.get(f'{k}_win', 0)
            l = s.get(f'{k}_loss', 0)
            wr = s.get(f'{k}_wr', 0)
            avg = s.get(f'{k}_avg', 0)
            ttl = s.get(f'{k}_total', 0)
            em = "✅" if wr >= MIN_WINRATE else "❌"
            print(f"    {n:>2}b: {w:>3}W/{l:<3}L  WR={wr*100:.1f}%  Avg={avg:+.2f}%  Tot={ttl:+.2f}%  {em}")

    print(f"\n  COMBINED:")
    all_ok = True
    for n in FORWARD_BARS:
        rets = [s[f'ret_{n}'] for s in signals if s.get(f'ret_{n}') is not None]
        if rets:
            wins = sum(1 for r in rets if r > 0)
            wr = wins / len(rets) * 100
            avg = float(np.mean(rets))
            em = "✅" if wr >= MIN_WINRATE*100 else "❌"
            if wr < MIN_WINRATE*100:
                all_ok = False
            print(f"    {n:>2}b: {wins:>3}W/{len(rets)-wins:<3}L  WR={wr:.1f}%  Avg={avg:+.2f}%  {em}")

    # Best/worst
    sigs_r = [(s, s.get('ret_3', 0) or 0) for s in signals if s.get('ret_3') is not None]
    sigs_r.sort(key=lambda x: x[1], reverse=True)
    print(f"\n  Best3 (ret3b):")
    for s, r in sigs_r[:3]:
        print(f"    {s['entry_date']} {s['type']:5} POC={s['poc_mid']:.2f} → ret3={r:+.2f}%")
    print(f"  Worst3 (ret3b):")
    for s, r in sigs_r[-3:]:
        print(f"    {s['entry_date']} {s['type']:5} POC={s['poc_mid']:.2f} → ret3={r:+.2f}%")


def main():
    all_data = {}
    for ticker in TICKERS:
        res, sigs = run_test(ticker)
        all_data[ticker] = (res, sigs)
        print_results(ticker, res, sigs)

    # Summary
    print(f"\n\n{'='*70}")
    print("📋 FINAL SUMMARY — Volume Profile S/R (Intraday POC)")
    print(f"{'='*70}")
    print(f"{'Ticker':>8} | {'Sigs':>6} | {'POCshr':>7} | {'WR3':>6} | {'WR6':>6} | {'WR12':>6} | {'AvgR3':>7} | {'Status':>10}")
    print('-' * 65)

    for ticker in TICKERS:
        res, sigs = all_data.get(ticker, (None, []))
        if not res or not sigs:
            print(f"{ticker:>8} | {'0':>6} | {'N/A':>7} | {'N/A':>6} | {'N/A':>6} | {'N/A':>6} | {'N/A':>7} | {'❌ NO SIG':>10}")
            continue

        wrs, avgs = {}, {}
        for n in FORWARD_BARS:
            rets = [s[f'ret_{n}'] for s in sigs if s.get(f'ret_{n}') is not None]
            wins = sum(1 for r in rets if r > 0) if rets else 0
            tot = len(rets)
            wrs[n] = wins / tot * 100 if tot > 0 else 0
            avgs[n] = float(np.mean(rets)) if rets else 0

        valid = any(wrs[n] >= MIN_WINRATE * 100 for n in FORWARD_BARS)
        avg_ps = float(np.mean([s['poc_vol_share'] for s in sigs]))
        print(f"{ticker:>8} | {len(sigs):>6} | {avg_ps*100:>6.1f}% | {wrs[3]:>5.1f}% | {wrs[6]:>5.1f}% | {wrs[12]:>5.1f}% | {avgs[3]:>+6.2f}% | {'✅' if valid else '❌'}")

    print(f"\nDone. WR threshold: {MIN_WINRATE*100:.0f}%")


if __name__ == '__main__':
    main()
