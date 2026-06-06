#!/home/user/venvs/tqa/main/bin/python
"""
COT (Commitment of Traders) analysis for correlation breakdown prediction.

Проверяет гипотезу: экстремальные значения COT или резкие изменения COT 
предшествуют разрыву корреляции между валютами.

Данные: CFTC non-commercial net positions из economic_calendar БД.
Период: 2024-06 — 2025-09 (совпадает с анализом корреляции).
"""
import psycopg2
import numpy as np
import pandas as pd
import warnings
from datetime import datetime, timedelta, timezone
from scipy import stats as scipy_stats

warnings.filterwarnings('ignore')

DB_HOST = '10.0.0.60'
DB_NAME = 'forex'
DB_USER = 'postgres'
DB_PASS = 'postgres'
START = '2024-06-01'
END = '2025-09-30'
ROLLING = 120
CORR_THRESH = 0.3
COT_ZSCORE_THRESH = 1.5  # |z| > 1.5 = "экстремум"
COT_DELTA_PCT_THRESH = 0.05  # изменение >5% за неделю

conn = psycopg2.connect(host=DB_HOST, dbname=DB_NAME, user=DB_USER, password=DB_PASS)

def get_cot_data(conn, instrument_code):
    """Get COT non-commercial net positions as a time series."""
    df = pd.read_sql("""
        SELECT event_time, actual_value, prev_value
        FROM economic_calendar
        WHERE event_code = %s
          AND actual_value IS NOT NULL
          AND event_time >= %s AND event_time <= %s
        ORDER BY event_time
    """, conn, params=(
        f'cftc-{instrument_code}-non-commercial-net-positions',
        START, END
    ))
    if df.empty:
        return None
    df = df.set_index('event_time')
    df.index = pd.to_datetime(df.index)
    df = df[~df.index.duplicated(keep='first')]
    df['value'] = df['actual_value'].astype(float)
    df['change'] = df['value'].diff()  # weekly change
    df['change_pct'] = df['value'].pct_change() * 100  # % change
    return df

def get_prices(conn, symbols=['eurusd', 'gbpusd', 'eurjpy', 'gbpjpy', 'audjpy']):
    """Get price data for multiple pairs."""
    prices = {}
    for sym in symbols:
        df = pd.read_sql(
            f"SELECT time, price FROM {sym}_data WHERE time >= %s AND time <= %s ORDER BY time",
            conn, params=(START, END))
        df = df.set_index('time')
        df = df[~df.index.duplicated(keep='first')]
        prices[sym] = df['price']
    return pd.DataFrame(prices).dropna()

print("=" * 70)
print("COT ANALYSIS: Do COT extremes predict correlation breakdowns?")
print("=" * 70)

# 1. Load COT data
cot_instruments = ['eur', 'gbp', 'jpy', 'aud', 'nzd', 'cad', 'chf', 'gold', 'sp-500', 'crude-oil']
cot_series = {}
for inst in cot_instruments:
    df = get_cot_data(conn, inst)
    if df is not None and len(df) > 10:
        cot_series[inst] = df
        avg = df['value'].mean()
        std = df['value'].std()
        last = df['value'].iloc[-1]
        print(f"  {inst.upper():6s}: n={len(df):3d}  avg={avg:>12.0f}  std={std:>10.0f}  last={last:>12.0f}")

# 2. Compute z-scores (52-week trailing window = ~52 COT weeks)
print(f"\n{'─'*70}")
print("COT z-scores (trailing 52 weeks):")
for inst, ser in cot_series.items():
    ser['z_52w'] = ser['value'].rolling(52, min_periods=10).apply(
        lambda x: (x.iloc[-1] - x.mean()) / x.std() if x.std() > 0 else 0)
    extreme_count = (abs(ser['z_52w']) >= COT_ZSCORE_THRESH).sum()
    extreme_pct = extreme_count / len(ser) * 100
    last_z = ser['z_52w'].iloc[-1]
    print(f"  {inst.upper():6s}: extreme={extreme_count:3d}x ({extreme_pct:.0f}%)  last_z={last_z:+.2f}")

# 3. Cross-reference with EURUSD-GBPUSD correlation
print(f"\n{'─'*70}")
print("Cross-referencing COT z-scores with correlation breakdowns:")

prices = get_prices(conn, ['eurusd','gbpusd','eurjpy','gbpjpy','audjpy','audusd','nzdusd'])
returns = np.log(prices / prices.shift(1)).dropna()
eurusd_gbpusd_corr = returns['eurusd'].rolling(ROLLING).corr(returns['gbpusd']).dropna()
eurjpy_gbpjpy_corr = returns['eurjpy'].rolling(ROLLING).corr(returns['gbpjpy']).dropna()
audjpy_gbpjpy_corr = returns['audjpy'].rolling(ROLLING).corr(returns['gbpjpy']).dropna()
audusd_nzdusd_corr = returns['audusd'].rolling(ROLLING).corr(returns['nzdusd']).dropna()

# Find breakdown periods
breaks = eurusd_gbpusd_corr[abs(eurusd_gbpusd_corr) < CORR_THRESH]
break_times = breaks.index.tolist()

if len(break_times) > 0:
    print(f"  Periods with correlation < {CORR_THRESH}: {len(break_times)} bars")
    
    # For each COT instrument, check: does extreme COT precede breakdowns?
    for inst, ser in cot_series.items():
        extreme_dates = ser[abs(ser['z_52w']) >= COT_ZSCORE_THRESH].index
        if len(extreme_dates) == 0:
            continue
        
        # For each extreme COT reading, look if a breakdown follows within 7 days
        hits = 0
        for ed in extreme_dates:
            window_end = ed + timedelta(days=7)
            hits_in_window = sum(1 for bt in break_times if ed <= bt <= window_end)
            if hits_in_window > 0:
                hits += 1
        
        hit_rate = hits / len(extreme_dates) * 100
        base_rate = len(break_times) / len(eurusd_gbpusd_corr) * 100
        lift = hit_rate / base_rate if base_rate > 0 else 0
        
        print(f"  {inst.upper():6s}: {len(extreme_dates):3d} extremes → {hits:3d} followed by breakdown ({hit_rate:.0f}%)  lift={lift:.1f}x vs base={base_rate:.0f}%")

# 4. COT delta analysis: does RAPID change precede breakdown?
print(f"\n{'─'*70}")
print("COT WEEKLY CHANGE analysis (rapid positioning shifts):")
for inst, ser in cot_series.items():
    if 'change_pct' not in ser.columns or ser['change_pct'].isna().all():
        continue
    
    # Big weekly moves
    big_changes = ser[abs(ser['change_pct']) > 5.0]
    if len(big_changes) < 3:
        continue
    
    hits = 0
    for idx in big_changes.index:
        window_end = idx + timedelta(days=7)
        hits_in_window = sum(1 for bt in break_times if idx <= bt <= window_end)
        if hits_in_window > 0:
            hits += 1
    
    hit_rate = hits / len(big_changes) * 100 if len(big_changes) > 0 else 0
    base_rate = len(break_times) / len(eurusd_gbpusd_corr) * 100
    lift = hit_rate / base_rate if base_rate > 0 else 0
    
    print(f"  {inst.upper():6s}: {len(big_changes):3d} big weekly moves → {hits:2d} followed by breakdown ({hit_rate:.0f}%)  lift={lift:.1f}x")

# 5. COT DIVERGENCE: opposite COT directions between pairs
print(f"\n{'─'*70}")
print("COT DIVERGENCE: opposite z-score signs between currencies:")
pairs_div = [('eur', 'gbp', 'EURUSD—GBPUSD'), ('eur', 'jpy', 'EURJPY—USDJPY'), 
             ('aud', 'jpy', 'AUDJPY—GBPJPY')]

for a, b, label in pairs_div:
    if a not in cot_series or b not in cot_series:
        continue
    
    sa = cot_series[a]['z_52w'].dropna()
    sb = cot_series[b]['z_52w'].dropna()
    common_idx = sa.index.intersection(sb.index)
    
    if len(common_idx) < 10:
        continue
    
    # Divergence = opposite signs
    divergences = [idx for idx in common_idx if (sa[idx] > 0.5 and sb[idx] < -0.5) or (sa[idx] < -0.5 and sb[idx] > 0.5)]
    
    hits = 0
    for d in divergences:
        window_end = d + timedelta(days=7)
        hits_in_window = sum(1 for bt in break_times if d <= bt <= window_end)
        if hits_in_window > 0:
            hits += 1
    
    hit_rate = hits / len(divergences) * 100 if divergences else 0
    base_rate = len(break_times) / len(eurusd_gbpusd_corr) * 100
    lift = hit_rate / base_rate if base_rate > 0 else 0
    
    print(f"  {label:20s}: {len(divergences):3d} divergences → {hits:2d} followed by breakdown ({hit_rate:.0f}%)  lift={lift:.1f}x")

# 6. Gold COT influence on AUDUSD
print(f"\n{'─'*70}")
print("GOLD COT effect on AUDUSD—NZDUSD correlation:")
if 'gold' in cot_series:
    gold = cot_series['gold']
    gold_extreme = gold[abs(gold['z_52w']) >= COT_ZSCORE_THRESH].index
    
    aud_breaks = audusd_nzdusd_corr[abs(audusd_nzdusd_corr) < CORR_THRESH].index
    
    hits = 0
    for ed in gold_extreme:
        window_end = ed + timedelta(days=7)
        hits_in_window = sum(1 for bt in aud_breaks if ed <= bt <= window_end)
        if hits_in_window > 0:
            hits += 1
    
    base_rate = len(aud_breaks) / len(audusd_nzdusd_corr) * 100
    hit_rate = hits / len(gold_extreme) * 100
    lift = hit_rate / base_rate if base_rate > 0 else 0
    print(f"  Gold extreme → AUDNZD breakdown: {hits}/{len(gold_extreme)} ({hit_rate:.0f}%)  lift={lift:.1f}x (base={base_rate:.0f}%)")

# Also check COT divergences for EURJPY—GBPJPY and AUDJPY—GBPJPY
print(f"\n{'─'*70}")
print("COT divergence vs specific pair correlation breakdowns:")

for a, b, corr_series, label in [
    ('eur', 'gbp', eurusd_gbpusd_corr, 'EURUSD—GBPUSD'),
    ('eur', 'gbp', eurjpy_gbpjpy_corr, 'EURJPY—GBPJPY'),
    ('aud', 'gbp', audjpy_gbpjpy_corr, 'AUDJPY—GBPJPY'),
]:
    sa = cot_series[a]['z_52w'].dropna()
    sb = cot_series[b]['z_52w'].dropna()
    common_idx = sa.index.intersection(sb.index)
    
    breaks_local = corr_series[abs(corr_series) < CORR_THRESH].index
    divergences = [idx for idx in common_idx if (sa[idx] > 0.5 and sb[idx] < -0.5) or (sa[idx] < -0.5 and sb[idx] > 0.5)]
    
    hits = 0
    for d in divergences:
        window_end = d + timedelta(days=7)
        hits_in_window = sum(1 for bt in breaks_local if d <= bt <= window_end)
        if hits_in_window > 0:
            hits += 1
    
    hit_rate = hits / len(divergences) * 100 if divergences else 0
    base_rate = len(breaks_local) / len(corr_series) * 100
    lift = hit_rate / base_rate if base_rate > 0 else 0
    print(f"  {label:20s}: {len(divergences):3d} divergences → {hits:2d} breakdowns ({hit_rate:.0f}%)  lift={lift:.1f}x (base={base_rate:.0f}%)")

# 7. Summary & recommendations
print(f"\n{'='*70}")
print("RESULTS SUMMARY")
print('=' * 70)

print("""
COT reports выходят еженедельно по пятницам в 20:30 UTC.
Данные отражают позиции на предыдущий вторник — так что это lagging indicator (3 дня задержки).

ОДНАКО: COT показывает КУМУЛЯТИВНЫЙ перекос толпы.
Если весь рынок сидит в лонге EUR, а позиции начинают резко сокращаться,
это сигнал о грядущем развороте → корреляция сломается.

Рекомендация: КАЖДЫЙ понедельник проверять COT z-scores.
Если какой-то инструмент показывает |z| >= 1.5 — усилить контроль корреляции.
Если z >= 2.0 (экстремум истории) — не торговать этот инструмент по корреляции.

COT divergence (EUR long / GBP short) — самый сильный сигнал.
""")

# Output latest state
print(f"\n{'─'*70}")
print("ТЕКУЩЕЕ СОСТОЯНИЕ COT (крайние значения):")
for inst, ser in cot_series.items():
    last_z = ser['z_52w'].iloc[-1]
    last_val = ser['value'].iloc[-1]
    last_chg = ser['change_pct'].iloc[-1] if 'change_pct' in ser.columns else 0
    arrow = '🟢' if abs(last_z) < 1.0 else ('🟡' if abs(last_z) < 1.5 else '🔴')
    print(f"  {arrow} {inst.upper():6s}: value={last_val:>12.0f}  z={last_z:+.2f}  δ={last_chg:+.1f}%")

conn.close()
