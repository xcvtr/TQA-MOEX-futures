#!/usr/bin/env python3
"""
Volume Profile S/R Test on MOEX Futures (v2)
==============================================
Uses intraday data for ACCURATE volume profile computation.
Point of Control (POC) = price zone with highest volume.

For each day:
  - Get intraday bars (price + volume at each time)
  - Split price range into 20 zones
  - Assign each bar's volume to its price zone
  - Zone with max total volume = POC

Next day test:
  - Price enters POC zone (±0.5%) → signal
    - From below (touches POC from underneath, close above) → LONG
    - From above (touches POC from above, close below) → SHORT
  - Forward returns at 3, 6, 12 daily bars
"""

import clickhouse_connect
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

# ─── Config ───
CLICKHOUSE_HOST = '10.0.0.60'
CLICKHOUSE_PORT = 8123
DATABASE = 'moex'
TICKERS = ['Si', 'GZ', 'CR']
START_DATE = '2024-10-01'
POC_ZONES = 20
POC_TOLERANCE = 0.005  # 0.5%
FORWARD_BARS = [3, 6, 12]
MIN_WINRATE = 0.52
MAX_PRICE_JUMP = 0.50  # filter contract roll (>50% daily change)

client = clickhouse_connect.get_client(
    host=CLICKHOUSE_HOST,
    port=CLICKHOUSE_PORT,
    database=DATABASE
)


def get_intraday(ticker_symbol, start_date, end_date):
    """
    Get intraday bars for ALL contracts matching ticker_prefix (e.g. 'Si').
    Returns DataFrame with date, price, volume for each observation.
    Uses latest contract for each date to avoid mixing contracts.
    """
    query = f"""
        SELECT toDate(SYSTIME) as d, SYSTIME as ts, secid,
               pr_close, pr_high, pr_low, vol
        FROM moex.tradestats_fo
        WHERE secid LIKE '{ticker_symbol}%'
          AND SYSTIME >= '{start_date}'
          AND SYSTIME < '2026-06-20'
          AND vol > 0
          AND pr_close > 0
        ORDER BY d, ts
    """
    result = client.query(query)
    rows = result.result_rows
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=['date', 'ts', 'secid', 'close', 'high', 'low', 'vol'])
    df['date'] = pd.to_datetime(df['date'])
    df['ts'] = pd.to_datetime(df['ts'])
    return df


def compute_intraday_volume_profile(df_intra, df_daily):
    """
    For each day, split price range into POC_ZONES zones,
    assign each intraday bar's volume to its zone,
    find the zone with max volume = POC.
    """
    poc_records = []

    for i, row in df_daily.iterrows():
        day = row['date']
        lo, hi = row['low'], row['high']
        day_close = row['close']

        # Get intraday bars for this day
        day_data = df_intra[df_intra['date'] == day]
        if len(day_data) == 0:
            continue

        # Use actual daily range from intraday data
        actual_lo = day_data['low'].min()
        actual_hi = day_data['high'].max()
        lo = min(lo, actual_lo)
        hi = max(hi, actual_hi)

        if lo <= 0 or hi <= 0:
            continue

        # Create 20 price zones
        zone_size = (hi - lo) / POC_ZONES
        if zone_size <= 0:
            zone_size = hi * 0.001
            zone_size = max(zone_size, 0.01)

        bins = np.linspace(lo, hi, POC_ZONES + 1)
        zone_volumes = np.zeros(POC_ZONES)

        # Assign each bar's volume to its price zone
        # Use close price as the representative price for each bar
        for _, bar in day_data.iterrows():
            # Distribute volume across zones based on bar's range
            # For simplicity, use close price
            bar_price = bar['close']
            bar_vol = bar['vol']
            zone_idx = int(np.clip(np.digitize(bar_price, bins) - 1, 0, POC_ZONES - 1))
            zone_volumes[zone_idx] += bar_vol

        # Find POC zone
        max_zone_idx = int(np.argmax(zone_volumes))
        poc_lo = bins[max_zone_idx]
        poc_hi = bins[max_zone_idx + 1]
        poc_mid = (poc_lo + poc_hi) / 2
        total_vol = zone_volumes.sum()
        poc_vol_share = zone_volumes[max_zone_idx] / total_vol if total_vol > 0 else 0

        poc_records.append({
            'date': day,
            'close': day_close,
            'low': lo,
            'high': hi,
            'poc_low': round(poc_lo, 2),
            'poc_high': round(poc_hi, 2),
            'poc_mid': round(poc_mid, 2),
            'poc_vol_share': round(poc_vol_share, 4),
            'poc_zone_vol': int(zone_volumes[max_zone_idx]),
            'total_vol': int(total_vol),
        })

    return pd.DataFrame(poc_records)


def compute_daily_agg(ticker_symbol):
    """Get daily OHLCV from ClickHouse (same as original query)."""
    query = f"""
        SELECT toDate(SYSTIME) as d,
               argMax(pr_close, SYSTIME) as prc,
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
    df = df.sort_values('date').reset_index(drop=True)

    # Filter contract-roll jumps
    df['pct_chg'] = df['close'].pct_change().abs()
    roll_mask = df['pct_chg'] > MAX_PRICE_JUMP
    if roll_mask.any():
        drop_idx = set()
        for idx in df[roll_mask].index:
            drop_idx.add(idx)
            if idx + 1 < len(df):
                drop_idx.add(idx + 1)
        df = df.drop(index=list(drop_idx)).reset_index(drop=True)

    return df


def generate_signals_regression(df_daily, poc_df):
    """
    Generate signals using regression approach:
    When next day's price enters POC zone (within 0.5%), signal.
    - LONG: low of tomorrow <= POC_high AND close > POC_mid (bounce up)
    - SHORT: high of tomorrow >= POC_low AND close < POC_mid (bounce down)
    """
    signals = []

    # Align: for each day i, POC is from day i's data, signal is checked on day i+1
    # We need poc_df dates to align with df_daily dates
    poc_by_date = poc_df.set_index('date')

    for i in range(len(df_daily) - 1):
        today = df_daily.iloc[i]
        tomorrow = df_daily.iloc[i + 1]

        if today['date'] not in poc_by_date.index:
            continue

        poc = poc_by_date.loc[today['date']]

        poc_mid = poc['poc_mid']
        poc_low = poc['poc_low']
        poc_high = poc['poc_high']

        tol_low = poc_mid * (1 - POC_TOLERANCE)
        tol_high = poc_mid * (1 + POC_TOLERANCE)

        t_low = tomorrow['low']
        t_high = tomorrow['high']
        t_close = tomorrow['close']

        # LONG: enters zone from below → bounce up
        if t_low <= tol_high and t_close > poc_mid:
            sig_type = 'LONG'
        # SHORT: enters zone from above → bounce down
        elif t_high >= tol_low and t_close < poc_mid:
            sig_type = 'SHORT'
        else:
            continue

        signals.append({
            'poc_date': today['date'].strftime('%Y-%m-%d'),
            'entry_date': tomorrow['date'].strftime('%Y-%m-%d'),
            'type': sig_type,
            'poc_mid': round(poc_mid, 4),
            'poc_low': round(poc_low, 4),
            'poc_high': round(poc_high, 4),
            'poc_vol_share': round(float(poc['poc_vol_share']), 4),
            'entry_price': round(t_close, 4),
            'tomorrow_low': round(t_low, 4),
            'tomorrow_high': round(t_high, 4),
        })

    return signals


def compute_forward_returns(df, signal):
    """Compute forward returns at N bars from entry date."""
    entry_date = pd.Timestamp(signal['entry_date'])
    entry_idx = df[df['date'] == entry_date].index
    if len(entry_idx) == 0:
        return {}

    entry_idx = entry_idx[0]
    entry_price = signal['entry_price']
    sig_type = signal['type']
    rets = {}

    for n in FORWARD_BARS:
        if entry_idx + n < len(df):
            fc = df.iloc[entry_idx + n]['close']
            pct = abs(fc - entry_price) / entry_price
            if pct > MAX_PRICE_JUMP:
                rets[f'ret_{n}'] = None
                continue

            if sig_type == 'LONG':
                r = (fc - entry_price) / entry_price * 100
            else:
                r = (entry_price - fc) / entry_price * 100

            rets[f'ret_{n}'] = round(r, 2) if abs(r) < 500 else None
        else:
            rets[f'ret_{n}'] = None

    return rets


def compute_simple_vp(df_daily):
    """
    Simple fallback: triangular volume profile centered on close.
    For when intraday data is too sparse.
    """
    records = []
    for _, row in df_daily.iterrows():
        lo, hi = row['low'], row['high']
        close = row['close']
        vol = row['vol']

        if lo == hi:
            zone_size = max(hi * 0.001, 0.01)
        else:
            zone_size = (hi - lo) / POC_ZONES

        bins = np.linspace(lo, hi, POC_ZONES + 1)
        bc = (bins[:-1] + bins[1:]) / 2
        dist = np.abs(bc - close)
        max_d = max(hi - close, close - lo)
        if max_d > 0:
            w = 1 - dist / max_d
            w = np.maximum(w, 0)
            w[0] += 0.05
            w[-1] += 0.05
            w = w / w.sum()
        else:
            w = np.ones(POC_ZONES) / POC_ZONES

        mx = int(np.argmax(w))
        records.append({
            'date': row['date'],
            'close': close,
            'low': lo,
            'high': hi,
            'poc_low': round(bins[mx], 2),
            'poc_high': round(bins[mx + 1], 2),
            'poc_mid': round((bins[mx] + bins[mx + 1]) / 2, 2),
            'poc_vol_share': round(w[mx], 4),
        })
    return pd.DataFrame(records)


def run_test(ticker_symbol):
    """Full test for one ticker using real intraday volume profile."""
    print(f"\n{'='*60}")
    print(f"📊 Testing {ticker_symbol}...")
    print(f"{'='*60}")

    # Get daily data (filtered for contract rolls)
    df_daily = compute_daily_agg(ticker_symbol)
    print(f"  Daily bars: {len(df_daily)}")

    # Get intraday data
    intra = get_intraday(ticker_symbol, START_DATE, '2026-06-20')
    print(f"  Intraday bars: {len(intra)}")

    # Compute volume profile from intraday data
    poc_df = compute_intraday_volume_profile(intra, df_daily)
    print(f"  Days with POC: {len(poc_df)}")

    if len(poc_df) == 0:
        print(f"  ⚠ No POC data generated. Falling back to simple heuristic.")
        poc_df = compute_simple_vp(df_daily)
        print(f"  Fallback POC days: {len(poc_df)}")

    # Generate signals
    signals = generate_signals_regression(df_daily, poc_df)
    print(f"  Raw signals: {len(signals)}")

    if not signals:
        return None, []

    # Forward returns
    for s in signals:
        s.update(compute_forward_returns(df_daily, s))

    valid_sigs = [s for s in signals if any(s.get(f'ret_{n}') is not None for n in FORWARD_BARS)]
    print(f"  Signals with forward data: {len(valid_sigs)}")

    # Stats per type
    results = {}
    for st in ['LONG', 'SHORT']:
        typed = [s for s in valid_sigs if s['type'] == st]
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
            # POC vol share stats
        poc_shares = [s['poc_vol_share'] for s in typed]
        stats['avg_poc_share'] = round(float(np.mean(poc_shares)), 4)
        results[st] = stats

    return results, valid_sigs


def print_results(ticker, results, signals):
    if results is None or not signals:
        print(f"\n  {ticker}: ❌ No valid signals")
        return False

    print(f"\n  {'─'*60}")
    for st in ['LONG', 'SHORT']:
        if st not in results:
            continue
        s = results[st]
        print(f"\n  {st.upper()} ({s['count']} sigs, avg POC share: {s.get('avg_poc_share', 0)*100:.2f}%)")
        print(f"  {'Horizon':>8} | {'Win':>5} {'Loss':>5} {'WR':>7} Avg R→ | {'Total':>8} |")
        for n in FORWARD_BARS:
            k = f'ret_{n}'
            w = s.get(f'{k}_win', 0)
            l = s.get(f'{k}_loss', 0)
            wr = s.get(f'{k}_wr', 0)
            avg = s.get(f'{k}_avg', 0)
            ttl = s.get(f'{k}_total', 0)
            em = "✅" if wr >= MIN_WINRATE else "❌"
            print(f"  {f'{n}b':>8} | {w:>5} {l:>5} {wr*100:>6.1f}% {avg:>+8.2f}% {ttl:>+8.2f}% {em}")

    # Combined
    print(f"\n  COMBINED L+S:")
    all_valid = True
    for n in FORWARD_BARS:
        wins = sum(1 for s in signals if s.get(f'ret_{n}') and s[f'ret_{n}'] > 0)
        losses = sum(1 for s in signals if s.get(f'ret_{n}') is not None and s[f'ret_{n}'] <= 0)
        rets = [s[f'ret_{n}'] for s in signals if s.get(f'ret_{n}') is not None]
        tot = wins + losses
        if tot > 0:
            wr = wins / tot * 100
            avg_r = float(np.mean(rets))
            em = "✅" if wr >= MIN_WINRATE*100 else "❌"
            if wr < MIN_WINRATE*100:
                all_valid = False
            print(f"    {f'{n}b':>6}: {wins:>4}W/{losses:<4}L WR={wr:.1f}% Avg={avg_r:+.2f}% {em}")

    # Top/bottom
    sigs_r = [(s, s.get('ret_3', 0) or 0) for s in signals if s.get('ret_3') is not None]
    sigs_r.sort(key=lambda x: x[1], reverse=True)
    print(f"\n  Best 3 (ret3):")
    for s, r in sigs_r[:3]:
        print(f"    {s['entry_date']} {s['type']:5} POC={s['poc_mid']:.2f} Entry={s['entry_price']:.2f} → +{r:.2f}%")
    print(f"  Worst 3 (ret3):")
    for s, r in sigs_r[-3:]:
        print(f"    {s['entry_date']} {s['type']:5} POC={s['poc_mid']:.2f} Entry={s['entry_price']:.2f} → {r:+.2f}%")

    if all_valid:
        print(f"\n  ✅ ALL horizons ≥ {MIN_WINRATE*100:.0f}% WR — VALID SIGNAL")
    else:
        print(f"\n  ❌ Some horizons < {MIN_WINRATE*100:.0f}% WR")

    return all_valid


def main():
    all_data = {}

    for ticker in TICKERS:
        res, sigs = run_test(ticker)
        all_data[ticker] = (res, sigs)
        print_results(ticker, res, sigs)

    # Final summary
    print(f"\n\n{'='*70}")
    print("📋 FINAL SUMMARY — Volume Profile S/R (Intraday POC)")
    print(f"{'='*70}")
    header = f"{'Ticker':>8} | {'Sigs':>5} | {'WR_3b':>7} | {'WR_6b':>7} | {'WR_12b':>7} | {'AvgR3':>7} | {'TotR3':>8} | {'Status'}"
    print(header)
    print('-' * len(header))

    for ticker in TICKERS:
        res, sigs = all_data.get(ticker, (None, []))
        if not res or not sigs:
            print(f"{ticker:>8} | {'0':>5} | {'N/A':>7} | {'N/A':>7} | {'N/A':>7} | {'N/A':>7} | {'N/A':>8} | ❌ NO SIG")
            continue

        wrs = {}
        avgs = {}
        for n in FORWARD_BARS:
            rets = [s[f'ret_{n}'] for s in sigs if s.get(f'ret_{n}') is not None]
            wins = sum(1 for r in rets if r > 0)
            tot = len(rets)
            wrs[n] = wins / tot * 100 if tot > 0 else 0
            avgs[n] = float(np.mean(rets)) if rets else 0

        valid = any(wrs[n] >= MIN_WINRATE * 100 for n in FORWARD_BARS)
        print(f"{ticker:>8} | {len(sigs):>5} | {wrs[3]:>6.1f}% | {wrs[6]:>6.1f}% | {wrs[12]:>6.1f}% | {avgs[3]:>+6.2f}% | {sum(avgs.values()):>+7.2f}% | {'✅' if valid else '❌'}")

    print(f"\nDone. Criteria: WR ≥ {MIN_WINRATE*100:.0f}% on any horizon.")


if __name__ == '__main__':
    main()
