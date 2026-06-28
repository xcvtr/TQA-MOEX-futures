#!/usr/bin/env python3
"""
Volume Profile S/R Test on MOEX Futures
========================================
Test: Point of Control (POC) levels as support/resistance.
For each day: split price range into 20 zones, find zone with max volume = POC.
Next day: if price enters POC zone (within 0.5%), signal:
  - LONG if price comes from below POC
  - SHORT if price comes from above POC
Forward returns measured at 3, 6, 12 bars (daily bars).
Skip tickers with win rate < 52%.
"""

import clickhouse_connect
import pandas as pd
import numpy as np
from datetime import datetime, date, timedelta

# ─── Config ───
CLICKHOUSE_HOST = '10.0.0.60'
CLICKHOUSE_PORT = 8123
DATABASE = 'moex'
TICKERS = ['Si', 'GZ', 'CR']
START_DATE = '2024-10-01'
END_DATE = '2026-06-28'
POC_ZONES = 20
POC_TOLERANCE = 0.005  # 0.5%
FORWARD_BARS = [3, 6, 12]
MIN_WINRATE = 0.52
MAX_PRICE_JUMP_PCT = 0.50  # 50% max price jump filter

client = clickhouse_connect.get_client(
    host=CLICKHOUSE_HOST,
    port=CLICKHOUSE_PORT,
    database=DATABASE
)

def get_daily_data(ticker):
    """Fetch daily OHLCV from ClickHouse."""
    query = f"""
        SELECT toDate(SYSTIME) as d,
               argMax(pr_close, SYSTIME) as prc,
               sum(vol) as vol,
               argMax(pr_low, SYSTIME) as lo,
               argMax(pr_high, SYSTIME) as hi
        FROM moex.tradestats_fo
        WHERE secid LIKE '{ticker}%'
          AND SYSTIME >= '{START_DATE}'
        GROUP BY d
        ORDER BY d
    """
    result = client.query(query)
    rows = result.result_rows
    df = pd.DataFrame(rows, columns=['date', 'close', 'vol', 'low', 'high'])
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date').reset_index(drop=True)

    # Filter out contract-roll days: price change > 50% in one day
    df['pct_chg'] = df['close'].pct_change().abs()
    roll_days = df[df['pct_chg'] > MAX_PRICE_JUMP_PCT].index.tolist()
    if roll_days:
        print(f"  Found {len(roll_days)} contract-roll days (>{MAX_PRICE_JUMP_PCT*100:.0f}% jump): {[df.loc[i,'date'].strftime('%Y-%m-%d') for i in roll_days[:10]]}")
        # Remove the roll days AND the day after (to avoid stale POC from different contract)
        drop_indices = set()
        for idx in roll_days:
            drop_indices.add(idx)
            if idx + 1 < len(df):
                drop_indices.add(idx + 1)
        df = df.drop(index=list(drop_indices)).reset_index(drop=True)
        print(f"  After filter: {len(df)} days")

    return df


def compute_poc(df):
    """
    For each day, split price range [low, high] into 20 zones,
    estimate volume per zone using a triangular distribution centered at close.
    Find zone with max volume = Point of Control (POC).
    """
    records = []
    for i, row in df.iterrows():
        lo, hi = row['low'], row['high']
        close = row['close']
        vol = row['vol']

        if lo == hi:
            zone_size = hi * 0.001
        else:
            zone_size = (hi - lo) / POC_ZONES

        if zone_size <= 0:
            zone_size = hi * 0.001

        bins = np.linspace(lo, hi, POC_ZONES + 1)
        bin_centers = (bins[:-1] + bins[1:]) / 2

        # Volume distribution heuristic: triangular centered at close
        dist_from_close = np.abs(bin_centers - close)
        max_dist = max(hi - close, close - lo)
        if max_dist > 0:
            weights = 1 - dist_from_close / max_dist
            weights = np.maximum(weights, 0)
            # Add slight weight to range extremes (support/resistance levels)
            weights[0] += 0.05
            weights[-1] += 0.05
            weights = weights / weights.sum()
        else:
            weights = np.ones(POC_ZONES) / POC_ZONES

        max_idx = int(np.argmax(weights))
        poc_lo = bins[max_idx]
        poc_hi = bins[max_idx + 1]

        records.append({
            'date': row['date'],
            'close': close,
            'low': lo,
            'high': hi,
            'poc_low': poc_lo,
            'poc_high': poc_hi,
            'poc_mid': (poc_lo + poc_hi) / 2,
            'poc_zone_idx': max_idx,
        })

    return pd.DataFrame(records)


def generate_signals(df, poc_df):
    """
    Generate LONG/SHORT signals when next day's price enters POC zone.
    - LONG if price comes from below POC (low <= POC_high AND close > POC_mid)
    - SHORT if price comes from above POC (high >= POC_low AND close < POC_mid)
    """
    signals = []

    for i in range(len(df) - 1):
        today = df.iloc[i]
        tomorrow = df.iloc[i + 1]
        poc = poc_df.iloc[i]

        poc_mid = poc['poc_mid']
        poc_low = poc['poc_low']
        poc_high = poc['poc_high']

        # 0.5% tolerance band around POC
        tol_low = poc_mid * (1 - POC_TOLERANCE)
        tol_high = poc_mid * (1 + POC_TOLERANCE)

        tomorrow_low = tomorrow['low']
        tomorrow_high = tomorrow['high']
        tomorrow_close = tomorrow['close']

        # LONG: price enters POC zone from below → price bounces up
        entered_from_below = (tomorrow_low <= tol_high) and (tomorrow_close > poc_mid)

        # SHORT: price enters POC zone from above → price bounces down
        entered_from_above = (tomorrow_high >= tol_low) and (tomorrow_close < poc_mid)

        signal_type = None
        if entered_from_below:
            signal_type = 'LONG'
        elif entered_from_above:
            signal_type = 'SHORT'
        else:
            continue

        signals.append({
            'poc_date': today['date'].strftime('%Y-%m-%d'),
            'entry_date': tomorrow['date'].strftime('%Y-%m-%d'),
            'type': signal_type,
            'poc_mid': round(poc_mid, 2),
            'poc_low': round(poc_low, 2),
            'poc_high': round(poc_high, 2),
            'entry_price': round(tomorrow_close, 2),
            'tomorrow_low': round(tomorrow_low, 2),
            'tomorrow_high': round(tomorrow_high, 2),
            'tomorrow_close': round(tomorrow_close, 2),
        })

    return signals


def compute_forward_returns(df, signal):
    """Compute forward returns at N bars from the entry date."""
    entry_date = pd.Timestamp(signal['entry_date'])
    entry_idx = df[df['date'] == entry_date].index
    if len(entry_idx) == 0:
        return {}

    entry_idx = entry_idx[0]
    entry_price = signal['entry_price']
    signal_type = signal['type']
    returns = {}

    for n in FORWARD_BARS:
        if entry_idx + n < len(df):
            future_close = df.iloc[entry_idx + n]['close']
            # Filter out contract-roll jumps in forward period
            pct_chg = abs(future_close - entry_price) / entry_price
            if pct_chg > MAX_PRICE_JUMP_PCT:
                returns[f'ret_{n}'] = None
                continue

            if signal_type == 'LONG':
                ret = (future_close - entry_price) / entry_price * 100
            else:
                ret = (entry_price - future_close) / entry_price * 100

            # Sanity check: max realistic return for futures
            if abs(ret) < 500:  # 500% max
                returns[f'ret_{n}'] = round(ret, 2)
            else:
                returns[f'ret_{n}'] = None
        else:
            returns[f'ret_{n}'] = None

    return returns


def run_test(ticker):
    """Run the full Volume Profile S/R test for a ticker."""
    print(f"\n{'='*60}")
    print(f"Testing {ticker}...")
    print(f"{'='*60}")

    df = get_daily_data(ticker)
    print(f"  Days: {len(df)}")
    print(f"  Period: {df['date'].min().date()} → {df['date'].max().date()}")
    print(f"  Price range: {df['low'].min():.2f} - {df['high'].max():.2f}")

    # Compute POC for each day
    poc_df = compute_poc(df)

    # Generate signals
    signals = generate_signals(df, poc_df)
    print(f"  Raw signals: {len(signals)}")

    if len(signals) == 0:
        print(f"  ⚠ NO SIGNALS generated")
        return None, []

    # Enrich with forward returns
    for s in signals:
        s.update(compute_forward_returns(df, s))

    # Filter out signals with no forward returns (shouldn't happen for most)
    valid_signals = [s for s in signals if any(s.get(f'ret_{n}') is not None for n in FORWARD_BARS)]
    print(f"  Signals with forward data: {len(valid_signals)}")

    # Analyze per signal type
    results = {}
    for sig_type in ['LONG', 'SHORT']:
        type_signals = [s for s in valid_signals if s['type'] == sig_type]
        if not type_signals:
            continue

        stats = {'count': len(type_signals)}
        for n in FORWARD_BARS:
            key = f'ret_{n}'
            rets = [s.get(key) for s in type_signals if s.get(key) is not None]
            if rets:
                stats[f'{key}_win'] = sum(1 for r in rets if r > 0)
                stats[f'{key}_loss'] = sum(1 for r in rets if r <= 0)
                stats[f'{key}_wr'] = round(stats[f'{key}_win'] / len(rets), 4)
                stats[f'{key}_avg'] = round(float(np.mean(rets)), 2)
                stats[f'{key}_total'] = round(float(np.sum(rets)), 2)
                stats[f'{key}_count'] = len(rets)
            else:
                stats[f'{key}_wr'] = 0
                stats[f'{key}_avg'] = 0
                stats[f'{key}_total'] = 0
                stats[f'{key}_count'] = 0

        results[sig_type] = stats

    return results, valid_signals


def print_results_table(ticker, results, signals):
    """Print formatted results."""
    print(f"\n{'='*60}")
    print(f"📊 RESULTS: {ticker}")
    print(f"{'='*60}")

    if results is None or len(signals) == 0:
        print("  ❌ No valid signals found.")
        return False

    for sig_type in ['LONG', 'SHORT']:
        if sig_type not in results:
            continue
        stats = results[sig_type]

        print(f"\n  {sig_type.upper()} Signals: {stats['count']} total")
        print(f"  {'─'*55}")
        print(f"  {'Horizon':>10} | {'Win':>5} {'Loss':>5} {'WR':>7} {'Avg Ret':>8} {'Total':>8} {'N':>5}")
        print(f"  {'─'*55}")
        for n in FORWARD_BARS:
            key = f'ret_{n}'
            wins = stats.get(f'{key}_win', 0)
            losses = stats.get(f'{key}_loss', 0)
            wr = stats.get(f'{key}_wr', 0)
            avg = stats.get(f'{key}_avg', 0)
            total = stats.get(f'{key}_total', 0)
            cnt = stats.get(f'{key}_count', 0)
            wr_str = f"{wr*100:.1f}%"
            emoji = "✅" if wr >= MIN_WINRATE else "❌"
            print(f"  {f'{n} bars':>10} | {wins:>5} {losses:>5} {wr_str:>7} {avg:>+8.2f}% {total:>+8.2f}% {cnt:>5} {emoji}")

        # Direction stats
        all_wins = sum(stats.get(f'ret_{n}_win', 0) for n in FORWARD_BARS)
        all_loss = sum(stats.get(f'ret_{n}_loss', 0) for n in FORWARD_BARS)
        all_count = all_wins + all_loss
        if all_count > 0:
            overall_wr = all_wins / all_count * 100
            print(f"  {'─'*55}")
            print(f"  {'OVERALL':>10} | {int(all_wins):>5} {int(all_loss):>5} {overall_wr:>6.1f}% {'':>8} {'':>8} {all_count:>5}")

    # Combined L+S
    print(f"\n  COMBINED L+S:")
    print(f"  {'─'*55}")
    all_valid = True
    for n in FORWARD_BARS:
        wins = 0
        losses = 0
        rets = []
        for s in signals:
            ret = s.get(f'ret_{n}')
            if ret is not None:
                rets.append(ret)
                if ret > 0:
                    wins += 1
                else:
                    losses += 1
        total = wins + losses
        if total > 0:
            wr = wins / total * 100
            avg_ret = float(np.mean(rets))
            total_pnl = float(np.sum(rets))
            emoji = "✅" if wr >= MIN_WINRATE * 100 else "❌"
            if wr < MIN_WINRATE * 100:
                all_valid = False
            print(f"  {f'{n} bars':>10} | {wins:>5} {losses:>5} {wr:>6.1f}% {avg_ret:>+8.2f}% {total_pnl:>+8.2f}% {total:>5} {emoji}")

    # Top/Bottom signals
    if len(signals) > 0:
        sigs_with_ret = [(s, s.get('ret_3', 0) or 0) for s in signals if s.get('ret_3') is not None]
        sigs_with_ret.sort(key=lambda x: x[1], reverse=True)
        print(f"\n  TOP 5 signals (ret 3b):")
        for s, ret in sigs_with_ret[:5]:
            print(f"    {s['entry_date']} {s['type']:5} POC={s['poc_mid']:.2f} Entry={s['entry_price']:.2f} → ret3={ret:+.2f}%")

        print(f"\n  WORST 5 signals (ret 3b):")
        for s, ret in sigs_with_ret[-5:]:
            print(f"    {s['entry_date']} {s['type']:5} POC={s['poc_mid']:.2f} Entry={s['entry_price']:.2f} → ret3={ret:+.2f}%")

    if all_valid:
        print(f"\n  ✅ OVERALL: WR >= {MIN_WINRATE*100:.0f}% — SIGNAL IS VALID")
    else:
        print(f"\n  ❌ OVERALL: WR < {MIN_WINRATE*100:.0f}% — NO SIGNAL")

    return all_valid


def main():
    all_results = {}
    for ticker in TICKERS:
        try:
            res, sigs = run_test(ticker)
            all_results[ticker] = (res, sigs)
            if res and sigs:
                print_results_table(ticker, res, sigs)
            else:
                print(f"\n{ticker}: ❌ No signals")
        except Exception as e:
            print(f"\n{ticker}: ❌ ERROR: {e}")
            import traceback
            traceback.print_exc()
            all_results[ticker] = (None, [])

    # Summary
    print(f"\n\n{'='*70}")
    print("📋 FINAL SUMMARY — Volume Profile S/R on MOEX Futures")
    print(f"{'='*70}")
    print(f"{'Ticker':>8} | {'Signals':>8} | {'WR 3b':>8} | {'WR 6b':>8} | {'WR 12b':>8} | {'Avg R3':>8} | {'TotalR3':>9} | {'Status':>10}")
    print(f"{'─'*8}-+-{'─'*8}-+-{'─'*8}-+-{'─'*8}-+-{'─'*8}-+-{'─'*8}-+-{'─'*9}-+-{'─'*10}")

    for ticker in TICKERS:
        res, sigs = all_results.get(ticker, (None, []))
        if not res or not sigs:
            print(f"{ticker:>8} | {'0':>8} | {'N/A':>8} | {'N/A':>8} | {'N/A':>8} | {'N/A':>8} | {'N/A':>9} | {'❌ NO SIG':>10}")
            continue

        # Combined stats across LONG+SHORT
        wrs = {}
        avgs = {}
        total_pnl_3 = 0
        n_3 = 0
        for n in FORWARD_BARS:
            wins = sum(res.get(s, {}).get(f'ret_{n}_win', 0) for s in ['LONG', 'SHORT'] if s in res)
            losses = sum(res.get(s, {}).get(f'ret_{n}_loss', 0) for s in ['LONG', 'SHORT'] if s in res)
            rets = [s.get(f'ret_{n}') for s in sigs if s.get(f'ret_{n}') is not None]
            total = wins + losses
            wrs[n] = wins / total * 100 if total > 0 else 0
            avgs[n] = float(np.mean(rets)) if rets else 0
            if n == 3:
                total_pnl_3 = float(np.sum(rets)) if rets else 0
                n_3 = len(rets)

        valid = any(wrs[n] >= MIN_WINRATE * 100 for n in FORWARD_BARS)
        print(f"{ticker:>8} | {len(sigs):>8} | {wrs[3]:>7.1f}% | {wrs[6]:>7.1f}% | {wrs[12]:>7.1f}% | {avgs[3]:>+7.2f}% | {total_pnl_3:>+8.2f}% | {'✅ VALID' if valid else '❌ INVALID':>10}")

    print(f"\n{'='*70}")
    print("Key: WR = Win Rate, Avg R3 = avg return at 3 bars, TotalR3 = sum of returns at 3 bars")
    print("Criteria: WR >= 52% for at least one horizon = VALID signal")
    print(f"{'='*70}")


if __name__ == '__main__':
    main()
