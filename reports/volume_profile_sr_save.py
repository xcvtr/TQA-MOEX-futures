#!/usr/bin/env python3
"""Save detailed results to CSV."""
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
               d.close, d.lo, d.hi, d.total_vol,
               GREATEST(0, LEAST(19, floor((i.price - d.lo) / GREATEST((d.hi - d.lo)/20, 1))::Int64)) as zone_idx,
               i.vol
        FROM intra_by_day i JOIN daily d ON i.d = d.d
    ),
    zone_totals AS (
        SELECT d, close, lo, hi, total_vol, zone_idx,
               sum(vol) as zone_vol
        FROM zone_volumes GROUP BY d, close, lo, hi, total_vol, zone_idx
    ),
    ranked AS (
        SELECT d, close, lo, hi, total_vol, zone_idx, zone_vol,
               ROW_NUMBER() OVER (PARTITION BY d ORDER BY zone_vol DESC) as rn
        FROM zone_totals
    )
    SELECT d, close, lo, hi,
           lo + ((hi - lo)/20) * zone_idx as poc_low,
           lo + ((hi - lo)/20) * (zone_idx + 1) as poc_high,
           zone_vol, total_vol
    FROM ranked WHERE rn = 1
    ORDER BY d
    """
    result = client.query(query)
    rows = result.result_rows
    df = pd.DataFrame(rows, columns=['date', 'close', 'low', 'high', 'poc_low', 'poc_high', 'zone_vol', 'total_vol'])
    df['date'] = pd.to_datetime(df['date'])
    df['poc_mid'] = (df['poc_low'] + df['poc_high']) / 2
    df['poc_vol_share'] = df['zone_vol'] / df['total_vol'].replace(0, np.nan)
    return df


def get_daily_agg(ticker_symbol):
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
        GROUP BY d ORDER BY d
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
    return df


def generate_signals(df_daily, poc_df, tkr=''):
    poc_by_date = poc_df.set_index('date')
    signals = []
    for i in range(len(df_daily) - 1):
        today, tomorrow = df_daily.iloc[i], df_daily.iloc[i + 1]
        if today['date'] not in poc_by_date.index:
            continue
        poc = poc_by_date.loc[today['date']]
        if isinstance(poc, pd.DataFrame):
            poc = poc.iloc[0]
        poc_mid = float(poc['poc_mid'])
        tol_low = poc_mid * (1 - POC_TOLERANCE)
        tol_high = poc_mid * (1 + POC_TOLERANCE)
        if tomorrow['low'] <= tol_high and tomorrow['close'] > poc_mid:
            sig_type = 'LONG'
        elif tomorrow['high'] >= tol_low and tomorrow['close'] < poc_mid:
            sig_type = 'SHORT'
        else:
            continue
        signals.append({
            'ticker': tkr,
            'poc_date': today['date'].strftime('%Y-%m-%d'),
            'entry_date': tomorrow['date'].strftime('%Y-%m-%d'),
            'type': sig_type,
            'poc_mid': round(poc_mid, 4),
            'poc_low': round(float(poc['poc_low']), 4),
            'poc_high': round(float(poc['poc_high']), 4),
            'poc_vol_share': round(float(poc['poc_vol_share']), 4),
            'entry_price': round(float(tomorrow['close']), 4),
            'daily_low': round(float(tomorrow['low']), 4),
            'daily_high': round(float(tomorrow['high']), 4),
        })
    return signals


def forward_returns(df, signal):
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


all_signals = []
for ticker in TICKERS:
    print(f"Processing {ticker}...")
    poc_df = get_daily_poc(ticker)
    df_daily = get_daily_agg(ticker)
    signals = generate_signals(df_daily, poc_df, tkr=ticker)
    for s in signals:
        s.update(forward_returns(df_daily, s))
    all_signals.extend(signals)
    print(f"  {len(signals)} signals")

df = pd.DataFrame(all_signals)
df.to_csv('/home/user/volume_profile_sr_signals.csv', index=False)
print(f"\nSaved {len(df)} signals to volume_profile_sr_signals.csv")

# Print summary stats
print(f"\n{'='*70}")
print("DETAILED RESULTS TABLE")
print(f"{'='*70}")
for ticker in TICKERS:
    sub = df[df['ticker'] == ticker]
    print(f"\n--- {ticker} ---")
    for st in ['LONG', 'SHORT']:
        st_sub = sub[sub['type'] == st]
        if len(st_sub) == 0:
            continue
        print(f"\n  {st} ({len(st_sub)} signals):")
        for n in FORWARD_BARS:
            r = st_sub[f'ret_{n}'].dropna()
            if len(r) > 0:
                wr = (r > 0).sum() / len(r) * 100
                avg = r.mean()
                tot = r.sum()
                em = "✅" if wr >= MIN_WINRATE*100 else "❌"
                print(f"    {n:>2}b: n={len(r):>4}  W={int((r>0).sum()):>4}/L={int((r<=0).sum()):<4}  WR={wr:.1f}%  Avg={avg:+.2f}%  Tot={tot:+.2f}%  {em}")

    # Combined
    print(f"\n  COMBINED:")
    for n in FORWARD_BARS:
        r = sub[f'ret_{n}'].dropna()
        if len(r) > 0:
            wr = (r > 0).sum() / len(r) * 100
            avg = r.mean()
            em = "✅" if wr >= MIN_WINRATE*100 else "❌"
            print(f"    {n:>2}b: n={len(r):>4}  W={int((r>0).sum()):>4}/L={int((r<=0).sum()):<4}  WR={wr:.1f}%  Avg={avg:+.2f}%  {em}")
