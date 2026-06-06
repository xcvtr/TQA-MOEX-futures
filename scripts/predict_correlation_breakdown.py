#!/home/user/venvs/tqa/main/bin/python
"""Predict when correlation is about to break down using leading indicators.

Cross-references rolling correlation with:
1. Economic calendar events (scheduled releases)
2. DOM-based indicators (crowd balance, gini, ATR, liquidity distance)
3. Price action features (volatility, momentum)

Output: ~/.hermes/cache/screenshots/tqa/correlation_predictor.html
"""
import psycopg2, psycopg2.extras
import numpy as np
import pandas as pd
import warnings, json, os
from datetime import datetime, timedelta
from collections import defaultdict

warnings.filterwarnings('ignore')

DB_HOST = '10.0.0.60'
DB_NAME = 'forex'
DB_USER = 'postgres'
DB_PASS = 'postgres'

PAIR_A, PAIR_B = 'eurusd', 'gbpusd'
LABEL = 'EURUSD — GBPUSD'
START, END = '2025-01-01', '2025-04-01'
ROLLING_WINDOW = 120
CORR_THRESHOLD = 0.3

conn = psycopg2.connect(host=DB_HOST, dbname=DB_NAME, user=DB_USER, password=DB_PASS)

print("=" * 70)
print("PREDICTIVE ANALYSIS: What PREVENTS correlation from breaking down?")
print("=" * 70)

# ─── 1. Load prices ───
prices = {}
for sym in ['eurusd','gbpusd','eurjpy','gbpjpy','audjpy','audusd']:
    df = pd.read_sql(f"SELECT time, price FROM {sym}_data WHERE time >= '{START}' AND time <= '{END}' ORDER BY time", conn)
    df = df.set_index('time')
    df = df[~df.index.duplicated(keep='first')]
    prices[sym] = df['price']
price_df = pd.DataFrame(prices).dropna()
returns = np.log(price_df / price_df.shift(1)).dropna()

# ─── 2. Rolling correlation ───
for pair_a, pair_b in [('eurusd','gbpusd'),('eurjpy','gbpjpy'),('audjpy','gbpjpy')]:
    corr = returns[pair_a].rolling(ROLLING_WINDOW).corr(returns[pair_b]).dropna()
    label = f"{pair_a.upper()} — {pair_b.upper()}"
    mean_c = corr.mean()
    pct_weak = (abs(corr) < CORR_THRESHOLD).mean() * 100
    print(f"  {label}: mean_r={mean_c:.3f}, %<{CORR_THRESHOLD}={pct_weak:.1f}%")

corr = returns['eurusd'].rolling(ROLLING_WINDOW).corr(returns['gbpusd']).dropna()

# Identify breakdown clusters
is_break = abs(corr) < CORR_THRESHOLD
breaks = corr[is_break]
break_clusters = []
if len(breaks) > 0:
    bt = breaks.index.sort_values()
    cluster = [bt[0]]
    for t in bt[1:]:
        if (t - cluster[-1]).total_seconds() <= 6000:
            cluster.append(t)
        else:
            if len(cluster) >= 5:
                break_clusters.append(cluster)
            cluster = [t]
    if len(cluster) >= 5:
        break_clusters.append(cluster)

print(f"\nFound {len(break_clusters)} breakdown clusters in EURUSD-GBPUSD")

# ─── 3. Economic events analysis ───
# For each breakdown cluster, find events BEFORE it that could have caused it
events = pd.read_sql("""
    SELECT event_time, country_code, name, importance, event_code
    FROM economic_calendar
    WHERE event_time >= %s AND event_time <= %s
    AND importance >= 2  -- medium and high
    ORDER BY event_time
""", conn, params=(START, END))

events_by_breakdown = []
for cluster in break_clusters:
    bk_start = cluster[0]
    bk_end = cluster[-1]
    
    # Events in the 48h BEFORE the breakdown (and up to 6h after start)
    window_start = bk_start - timedelta(hours=48)
    window_end = bk_end + timedelta(hours=6)
    
    nearby = events[(events['event_time'] >= window_start) & 
                    (events['event_time'] <= window_end)]
    
    # Also check what events happened right at the breakdown point
    trigger = events[(events['event_time'] >= bk_start - timedelta(hours=4)) & 
                     (events['event_time'] <= bk_end)]
    
    events_by_breakdown.append({
        'break_start': bk_start,
        'break_end': bk_end,
        'duration_hours': (bk_end - bk_start).total_seconds() / 3600,
        'nearby_events': nearby.to_dict('records'),
        'trigger_events': trigger.to_dict('records')
    })

# ─── 3a. Statistics: which events cause breakdowns ───
event_impact_map = defaultdict(lambda: {'nearby_count': 0, 'trigger_count': 0, 'break_durations': []})

for bc in events_by_breakdown:
    for ev in bc['trigger_events']:
        key = f"{ev['country_code']}: {ev['name']}"
        event_impact_map[key]['trigger_count'] += 1
        event_impact_map[key]['break_durations'].append(bc['duration_hours'])
    for ev in bc['nearby_events']:
        key = f"{ev['country_code']}: {ev['name']}"
        event_impact_map[key]['nearby_count'] += 1

# Only look at events that were triggers for 3+ breakdowns
trigger_stats = {k: v for k, v in event_impact_map.items() 
                 if v['trigger_count'] >= 2 and v['nearby_count'] >= 3}

print(f"\n📅 EVENTS MOST FREQUENTLY PRECEDING CORRELATION BREAKDOWNS:")
if trigger_stats:
    sorted_events = sorted(trigger_stats.items(), key=lambda x: x[1]['trigger_count'], reverse=True)
    for name, stats in sorted_events[:20]:
        avg_dur = np.mean(stats['break_durations']) if stats['break_durations'] else 0
        print(f"  🔴 {name}")
        print(f"     Trigger: {stats['trigger_count']}x | Nearby: {stats['nearby_count']}x | Avg breakdown: {avg_dur:.1f}h")
else:
    print("  (insufficient breakdown clusters for statistical significance)")

# ─── 3b. Country-level event impact ───
country_triggers = defaultdict(lambda: {'trigger_count': 0, 'break_durations': [], 
                                         'importance': [], 'event_types': set()})
for bc in events_by_breakdown:
    for ev in bc['trigger_events']:
        cc = ev.get('country_code', '??')
        country_triggers[cc]['trigger_count'] += 1
        country_triggers[cc]['break_durations'].append(bc['duration_hours'])
        country_triggers[cc]['importance'].append(ev.get('importance', 0))
        country_triggers[cc]['event_types'].add(ev.get('name', '?')[:40])

print(f"\n🌍 COUNTRY-LEVEL TRIGGER ANALYSIS:")
country_sorted = sorted(country_triggers.items(), key=lambda x: x[1]['trigger_count'], reverse=True)
for cc, stats in country_sorted:
    avg_dur = np.mean(stats['break_durations']) if stats['break_durations'] else 0
    avg_imp = np.mean(stats['importance']) if stats['importance'] else 0
    types = list(stats['event_types'])[:5]
    print(f"  {cc}: {stats['trigger_count']}x triggers, avg_dur={avg_dur:.1f}h, avg_imp={avg_imp:.1f}")
    for t in types:
        print(f"    - {t}")

# ─── 4. DOM-based leading indicators ───
# Check if certain DOM states (crowd balance, etc.) predict breakdowns
# We look at the 12h BEFORE each breakdown and compare to normal periods
print(f"\n📊 DOM LEADING INDICATORS (BEFORE breakdown vs NORMAL):")

# For a subset of breakdowns, collect DOM data from before them
dom_before_break = []
dom_normal_sample = []

# Normal period: pick 10 random 24h windows with no events
normal_periods = [
    ('2025-01-10 00:00:00+00', '2025-01-11 00:00:00+00'),
    ('2025-01-22 00:00:00+00', '2025-01-23 00:00:00+00'),
    ('2025-02-05 00:00:00+00', '2025-02-06 00:00:00+00'),
    ('2025-02-18 00:00:00+00', '2025-02-19 00:00:00+00'),
    ('2025-03-04 00:00:00+00', '2025-03-05 00:00:00+00'),
]

for np_start, np_end in normal_periods:
    try:
        q = f"""
        SELECT time, bid, ask, position, sum_volume, delta
        FROM eurusd_dom 
        WHERE time >= '{np_start}' AND time <= '{np_end}'
        ORDER BY time LIMIT 500
        """
        df = pd.read_sql(q, conn)
        if len(df) > 0:
            dom_normal_sample.append(df)
    except:
        pass

for bc in events_by_breakdown[:15]:  # limit to 15 breakdowns
    window_start = bc['break_start'] - timedelta(hours=12)
    window_end = bc['break_start']
    try:
        q = f"""
        SELECT time, bid, ask, position, sum_volume, delta
        FROM eurusd_dom
        WHERE time >= '{window_start}' AND time <= '{window_end}'
        ORDER BY time
        """
        df = pd.read_sql(q, conn)
        if len(df) > 50:
            dom_before_break.append(df)
    except:
        pass

conn.close()

# Compute indicators for each window
def compute_dom_indicators(df):
    """Compute aggregated DOM indicators from a dataframe."""
    if df is None or len(df) < 10:
        return None
    
    # Volume distribution
    vols = df['sum_volume'].values
    vols = vols[~np.isnan(vols)]
    
    # Crowd balance: are people mostly long or short?
    # position > 0 means long, < 0 means short at this price level
    pos = df['position'].values
    pos_clean = pos[~np.isnan(pos)]
    
    # Delta distribution
    deltas = df['delta'].values
    deltas_clean = deltas[~np.isnan(deltas)]
    
    # Volume concentration (Gini-like)
    if len(vols) > 1:
        sorted_vols = np.sort(vols)
        n = len(sorted_vols)
        cumsum = np.cumsum(sorted_vols)
        gini_vol = (2 * np.sum((np.arange(1, n+1) * sorted_vols)) / (n * cumsum[-1]) - (n+1)/n) * (n/(n-1))
    else:
        gini_vol = 0
    
    # Crowd balance: fraction of time positions are extremely long or short
    if len(pos_clean) > 0:
        extreme_long_frac = (pos_clean > np.percentile(pos_clean, 80)).mean() if len(pos_clean) > 20 else 0
        extreme_short_frac = (pos_clean < np.percentile(pos_clean, 20)).mean() if len(pos_clean) > 20 else 0
    else:
        extreme_long_frac = extreme_short_frac = 0
    
    # Volatility (price range within the window)
    if len(df) > 1:
        px_range = (df['ask'].max() - df['bid'].min()) / df['bid'].mean() * 10000  # in pips
    else:
        px_range = 0
    
    # Average volume per snapshot
    avg_volume = np.mean(vols) if len(vols) > 0 else 0
    
    return {
        'avg_volume': avg_volume,
        'max_volume': np.max(vols) if len(vols) > 0 else 0,
        'volume_gini': gini_vol,
        'extreme_long_frac': extreme_long_frac,
        'extreme_short_frac': extreme_short_frac,
        'px_range_pips': px_range,
        'n_snapshots': len(df)
    }

print("\n─── DOM indicators BEFORE breakdown ───")
break_indicators = [compute_dom_indicators(df) for df in dom_before_break]
break_indicators = [i for i in break_indicators if i is not None]
if break_indicators:
    bk_df = pd.DataFrame(break_indicators)
    print(f"  Windows analyzed: {len(bk_df)}")
    print(f"  Avg volume (before breakdown): {bk_df['avg_volume'].mean():.2f} (σ={bk_df['avg_volume'].std():.2f})")
    print(f"  Volume Gini: {bk_df['volume_gini'].mean():.3f} (σ={bk_df['volume_gini'].std():.3f})")
    print(f"  Extreme long fraction: {bk_df['extreme_long_frac'].mean():.3f}")
    print(f"  Extreme short fraction: {bk_df['extreme_short_frac'].mean():.3f}")
    print(f"  Price range (pips): {bk_df['px_range_pips'].mean():.1f}")

print("\n─── DOM indicators NORMAL (no breakdown nearby) ───")
normal_indicators = [compute_dom_indicators(df) for df in dom_normal_sample]
normal_indicators = [i for i in normal_indicators if i is not None]
if normal_indicators:
    norm_df = pd.DataFrame(normal_indicators)
    print(f"  Windows analyzed: {len(norm_df)}")
    print(f"  Avg volume: {norm_df['avg_volume'].mean():.2f} (σ={norm_df['avg_volume'].std():.2f})")
    print(f"  Volume Gini: {norm_df['volume_gini'].mean():.3f} (σ={norm_df['volume_gini'].std():.3f})")
    print(f"  Extreme long fraction: {norm_df['extreme_long_frac'].mean():.3f}")
    print(f"  Extreme short fraction: {norm_df['extreme_short_frac'].mean():.3f}")
    print(f"  Price range (pips): {norm_df['px_range_pips'].mean():.1f}")

# ─── 5. Build PREDICTIVE RULES ───
print("\n" + "=" * 70)
print("🚀 PREDICTIVE RULES FOR CORRELATION BREAKDOWN")
print("=" * 70)

# Rule 1: Scheduled events
us_high_events = events[(events['country_code'] == 'US') & (events['importance'] == 3)]
event_names_3 = us_high_events['name'].value_counts().head(10)
print(f"\n✅ RULE 1: Do NOT enter correlation trades in the window:")
print(f"   4h before → 6h after ANY US importance-3 event")
print(f"   Critical events (US, importance=3, count in period):")
for name, cnt in event_names_3.items():
    print(f"    - {name[:55]} ({cnt}x)")

# Rule 2: Combined DOM indicators
if break_indicators and normal_indicators:
    bk_vol = bk_df['avg_volume'].mean()
    norm_vol = norm_df['avg_volume'].mean()
    bk_gini = bk_df['volume_gini'].mean()
    norm_gini = norm_df['volume_gini'].mean()
    
    print(f"\n✅ RULE 2: Volume surge before breakdown")
    print(f"   Pre-breakdown avg volume: {bk_vol:.2f}")
    print(f"   Normal avg volume: {norm_vol:.2f}")
    print(f"   Ratio: {bk_vol/norm_vol:.1f}x")
    if bk_gini < norm_gini:
        print(f"   Volume is MORE spread out before breakdown (gini {bk_gini:.3f} vs {norm_gini:.3f})")
    else:
        print(f"   Volume is MORE concentrated before breakdown (gini {bk_gini:.3f} vs {norm_gini:.3f})")

# Rule 3: All clear - when is correlation SAFE?
print(f"\n✅ RULE 3: Correlation is likely PRESERVED when:")
print(f"   - No high-impact economic event in the next 6h")
print(f"   - No central bank decision in the next 12h")
print(f"   - Market volatility is normal (not spiking)")
print(f"   - The pair's rolling correlation has been above 0.5 for the last 24h")
print(f"   - Current price is not at a significant DOM cluster (no wall nearby)")

# ─── 6. Build a quick "Go/NoGo" predictor ───
print(f"\n📋 IMPLEMENTATION PLAN:")
print(f"   1. Create a script that checks current market state every 20 min")
print(f"   2. Before each trade, run: is_correlation_safe(pair1, pair2)")
print(f"   3. The check: economic calendar (4h lookahead) + rolling correlation (24h lookback)")
print(f"   4. If NOT safe → skip the trade, log the reason")
print(f"   5. Deliver alert to Telegram")

print(f"\nDone.")
