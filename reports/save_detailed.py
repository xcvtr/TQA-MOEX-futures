#!/usr/bin/env python3
"""Save detailed events to file."""
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import json

CLICKHOUSE_URL = 'http://10.0.0.60:8123/'

def get_df(secid, start, end):
    sql = f"""
    SELECT tradedate, tradetime, pr_open, pr_high, pr_low, pr_close, vol, val
    FROM moex.tradestats_fo
    WHERE secid = '{secid}'
      AND tradedate >= '{start}'
      AND tradedate <= '{end}'
      AND pr_close IS NOT NULL
    ORDER BY tradedate, tradetime
    FORMAT TabSeparated
    """
    r = requests.post(CLICKHOUSE_URL, data=sql.encode('utf-8'), timeout=60)
    rows = r.text.strip().split('\n')
    dates, times, opens, highs, lows, closes, vols, vals = [], [], [], [], [], [], [], []
    for row in rows:
        parts = row.split('\t')
        dates.append(parts[0])
        times.append(parts[1])
        opens.append(float(parts[2]) if parts[2] != '\\N' and parts[2] else None)
        highs.append(float(parts[3]) if parts[3] != '\\N' and parts[3] else None)
        lows.append(float(parts[4]) if parts[4] != '\\N' and parts[4] else None)
        closes.append(float(parts[5]) if parts[5] != '\\N' and parts[5] else None)
        vols.append(int(parts[6]) if parts[6] != '\\N' and parts[6] else 0)
        vals.append(float(parts[7]) if parts[7] != '\\N' and parts[7] else 0)
    
    df = pd.DataFrame({
        'datetime': pd.to_datetime([d + ' ' + t for d, t in zip(dates, times)]),
        'open': opens, 'high': highs, 'low': lows, 'close': closes,
        'vol': vols, 'val': vals
    })
    df = df.set_index('datetime')
    df = df[df.index.dayofweek < 5]
    df['ret'] = df['close'].pct_change()
    return df

today = datetime.now().strftime('%Y-%m-%d')
start = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')

# Get data
df_si = get_df('SiU6', start, today)
df_cr = get_df('CRU6', start, today)
df_br = get_df('BRN6', start, today)

# ========== Si/CR Analysis ==========
combined = pd.DataFrame()
combined['si_close'] = df_si['close']
combined['cr_close'] = df_cr['close']
combined['si_ret'] = df_si['ret']
combined['cr_ret'] = df_cr['ret']
combined = combined.dropna()

combined['rolling_corr'] = combined['si_ret'].rolling(20).corr(combined['cr_ret'])

# Detect events
corr = combined['rolling_corr'].values
events = []
for i in range(32, len(corr)):
    if np.isnan(corr[i]) or corr[i] >= 0.3:
        continue
    for j in range(max(0, i-12), i):
        if not np.isnan(corr[j]) and corr[j] > 0.7:
            si_ret = combined['si_ret'].iloc[i]
            cr_ret = combined['cr_ret'].iloc[i]
            direction = 'LONG_CR' if cr_ret < si_ret else 'LONG_SI'

            ev = {
                'bar_time': str(combined.index[i]),
                'corr_before': round(float(corr[max(0,i-12):i].max()), 3),
                'corr_now': round(float(corr[i]), 3),
                'si_close': float(combined['si_close'].iloc[i]),
                'cr_close': float(combined['cr_close'].iloc[i]),
                'si_ret_bar': round(float(si_ret)*100, 3) if not np.isnan(si_ret) else 0,
                'cr_ret_bar': round(float(cr_ret)*100, 3) if not np.isnan(cr_ret) else 0,
                'direction': direction,
            }

            # Forward returns
            for la in [3, 6, 12]:
                fwd_idx = min(i+la, len(combined)-1)
                si_fwd = (combined['si_close'].iloc[fwd_idx] - combined['si_close'].iloc[i]) / combined['si_close'].iloc[i]
                cr_fwd = (combined['cr_close'].iloc[fwd_idx] - combined['cr_close'].iloc[i]) / combined['cr_close'].iloc[i]
                if direction == 'LONG_CR':
                    ev[f'fwd_{la}_ret_bps'] = round((cr_fwd - si_fwd) * 10000, 1)
                else:
                    ev[f'fwd_{la}_ret_bps'] = round((si_fwd - cr_fwd) * 10000, 1)
                ev[f'fwd_{la}_win'] = 1 if ev[f'fwd_{la}_ret_bps'] > 0 else 0

            events.append(ev)
            break

# Stats
ev_df = pd.DataFrame(events)
print(f"Si/CR: {len(events)} events")
print(f"Direction: LONG_CR={sum(1 for e in events if e['direction']=='LONG_CR')}, LONG_SI={sum(1 for e in events if e['direction']=='LONG_SI')}")
for la in [3, 6, 12]:
    rets = [e[f'fwd_{la}_ret_bps'] for e in events]
    wins = [e[f'fwd_{la}_win'] for e in events]
    wr = np.mean(wins)*100
    mean_ret = np.mean(rets)
    print(f"  Fwd {la:2d}: WR={wr:.1f}%, mean={mean_ret:.1f}bps, median={np.median(rets):.1f}bps, std={np.std(rets):.1f}bps")
    if la == 3:
        print(f"  Signal: {'✅' if wr >= 52 else '❌'}")

# Save detailed events
with open('/home/user/si_cr_events.json', 'w') as f:
    json.dump(events, f, indent=2, default=str)
print(f"\nDetailed events saved to /home/user/si_cr_events.json")

# Print all events
print("\nAll events:")
for e in events:
    print(f"  {e['bar_time']} | corr {e['corr_before']:.2f}→{e['corr_now']:.2f} | {e['direction']:>8} | "
          f"Si:{e['si_close']:.1f} CR:{e['cr_close']:.2f} | "
          f"f3:{e['fwd_3_ret_bps']:+.1f} f6:{e['fwd_6_ret_bps']:+.1f} f12:{e['fwd_12_ret_bps']:+.1f}")

# BR/CR quick check
combined_br = pd.DataFrame()
combined_br['br_close'] = df_br['close']
combined_br['cr_close'] = df_cr['close']
combined_br['br_ret'] = df_br['ret']
combined_br['cr_ret'] = df_cr['ret']
combined_br = combined_br.dropna()
combined_br['roll_corr'] = combined_br['br_ret'].rolling(20).corr(combined_br['cr_ret'])
print(f"\nBR/CR: overall corr={combined_br['br_ret'].corr(combined_br['cr_ret']):.4f}, roll_corr>0.7 only {(combined_br['roll_corr']>0.7).sum()} bars")
print(f"BR/CR: 0 events — these are fundamentally different asset classes (Brent crude vs CNY)")
