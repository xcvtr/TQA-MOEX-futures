#!/usr/bin/env python3
import psycopg2
import psycopg2.extras
import pandas as pd
import numpy as np
from datetime import datetime

conn = psycopg2.connect(host='10.0.0.60', dbname='moex', user='postgres', password='postgres')
cur = conn.cursor()

# ── 1. Daily close prices ──
print("Loading daily prices ...")
prices = pd.read_sql("""
    SELECT symbol, time::date AS dt, AVG(close) AS close
    FROM moex_prices_5m
    WHERE symbol IN ('GD','GL','Si')
      AND time >= '2024-01-01' AND time < '2027-01-01'
    GROUP BY symbol, time::date
    ORDER BY dt
""", conn)

# Pivot
piv = prices.pivot(index='dt', columns='symbol', values='close').dropna()
piv.columns = ['GD_close','GL_close','Si_close']
piv = piv[['GD_close','GL_close','Si_close']]

# ── 2. Daily OI ──
print("Loading daily OI ...")
oi = pd.read_sql("""
    SELECT symbol, time::date AS dt,
           SUM(fiz_buy) AS fiz_buy, SUM(fiz_sell) AS fiz_sell,
           SUM(yur_buy) AS yur_buy, SUM(yur_sell) AS yur_sell
    FROM moex_prices_5m_oi
    WHERE symbol IN ('GD','GL')
      AND time >= '2024-01-01' AND time < '2027-01-01'
    GROUP BY symbol, time::date
    ORDER BY dt
""", conn)

oi_gd = oi[oi.symbol=='GD'].copy().drop(columns=['symbol']).set_index('dt').add_prefix('GD_')
oi_gl = oi[oi.symbol=='GL'].copy().drop(columns=['symbol']).set_index('dt').add_prefix('GL_')
oij = oi_gd.join(oi_gl, how='outer').fillna(0).astype(float)

# ── 3. Merge ──
df = piv.join(oij, how='inner').dropna(subset=['GD_close','GL_close','Si_close'])
print(f"Total trading days: {len(df)}")

# ── 4. Signals ──
# Unit conversion: GD = USD/troy_oz, GL = RUB/gram, Si = RUB per 1000 USD
# 1 troy oz = 31.1035 grams; Si/1000 = RUB/USD
# Parity: GL (RUB/g) = GD (USD/oz) * (Si/1000) / 31.1035
# So: GL / (GD * Si / 31103.5) == 1 at parity
TROY_OZ_TO_GRAM = 31.1035
df['parity_ratio'] = df['GL_close'] * TROY_OZ_TO_GRAM * 1000 / (df['GD_close'] * df['Si_close'])
df['parity_deviation_pct'] = (df['parity_ratio'] - 1.0) * 100
df['roll_corr_30'] = df['GD_close'].rolling(30).corr(df['GL_close'])
df['roll_corr_60'] = df['GD_close'].rolling(60).corr(df['GL_close'])

# OI spreads
df['GD_oi_spread'] = (df['GD_fiz_buy'] - df['GD_fiz_sell']) - (df['GD_yur_buy'] - df['GD_yur_sell'])
df['GL_oi_spread'] = (df['GL_fiz_buy'] - df['GL_fiz_sell']) - (df['GL_yur_buy'] - df['GL_yur_sell'])

# Remove NaN rolling window
df_valid = df.dropna(subset=['roll_corr_30']).copy()
print(f"Valid days with rolling data: {len(df_valid)}")

# ── 5. Breakdown analysis ──
breakdown = df_valid[df_valid['roll_corr_30'] < 0.2].copy()
print(f"\nBreakdown days (r<0.2): {len(breakdown)} ({len(breakdown)/len(df_valid)*100:.1f}% of days)")

if len(breakdown) > 0:
    # Forward price moves
    for fwd in [3,5,10]:
        breakdown[f'GD_fwd_{fwd}d'] = df_valid['GD_close'].shift(-fwd).reindex(breakdown.index)
        breakdown[f'GL_fwd_{fwd}d'] = df_valid['GL_close'].shift(-fwd).reindex(breakdown.index)
        breakdown[f'GD_ret_{fwd}d'] = (breakdown[f'GD_fwd_{fwd}d'] / breakdown['GD_close'] - 1) * 100
        breakdown[f'GL_ret_{fwd}d'] = (breakdown[f'GL_fwd_{fwd}d'] / breakdown['GL_close'] - 1) * 100

    stats_breakdown = {
        'count': len(breakdown),
        'parity_dev_avg': breakdown['parity_deviation_pct'].mean(),
        'parity_dev_std': breakdown['parity_deviation_pct'].std(),
        'parity_dev_med': breakdown['parity_deviation_pct'].median(),
        'GD_oi_spread_avg': breakdown['GD_oi_spread'].mean(),
        'GL_oi_spread_avg': breakdown['GL_oi_spread'].mean(),
        'GD_oi_spread_med': breakdown['GD_oi_spread'].median(),
        'GL_oi_spread_med': breakdown['GL_oi_spread'].median(),
    }
    for fwd in [3,5,10]:
        stats_breakdown[f'GD_ret_{fwd}d_avg'] = breakdown[f'GD_ret_{fwd}d'].mean()
        stats_breakdown[f'GL_ret_{fwd}d_avg'] = breakdown[f'GL_ret_{fwd}d'].mean()
        stats_breakdown[f'GD_ret_{fwd}d_med'] = breakdown[f'GD_ret_{fwd}d'].median()
        stats_breakdown[f'GL_ret_{fwd}d_med'] = breakdown[f'GL_ret_{fwd}d'].median()
        stats_breakdown[f'GD_ret_{fwd}d_pos'] = (breakdown[f'GD_ret_{fwd}d'] > 0).mean() * 100
        stats_breakdown[f'GL_ret_{fwd}d_pos'] = (breakdown[f'GL_ret_{fwd}d'] > 0).mean() * 100

    # ── 6. Mean-reversion strategy on parity ──
    # Use z-score of deviation for signal
    df_valid['deviation'] = df_valid['parity_deviation_pct']
    df_valid['z_dev'] = df_valid['deviation'].rolling(30).apply(
        lambda x: (x.iloc[-1] - x.mean()) / x.std() if x.std() > 0 else 0
    )
    df_valid = df_valid.dropna(subset=['z_dev'])

    # Thresholds: 1 sigma bands
    dev_std = df_valid['deviation'].std()
    LONG_TH = -dev_std          # GL cheap → buy
    SHORT_TH = dev_std           # GL expensive → short
    EXIT_LONG = -dev_std * 0.3   # exit long near center
    EXIT_SHORT = dev_std * 0.3   # exit short near center

    pos = 0; trades_long = []
    for idx, row in df_valid.iterrows():
        if row['deviation'] < LONG_TH and pos == 0:
            pos = 1
            entry_price = row['GL_close']
            entry_idx = idx
        elif row['deviation'] > EXIT_LONG and pos == 1:
            pos = 0
            ret = (row['GL_close'] / entry_price - 1) * 100
            trades_long.append({'entry': str(entry_idx), 'exit': str(idx), 'ret': ret})
        # timeout exit after 20 days
        if pos == 1 and (idx - entry_idx).days >= 20:
            pos = 0
            ret = (row['GL_close'] / entry_price - 1) * 100
            trades_long.append({'entry': str(entry_idx), 'exit': str(idx), 'ret': ret, 'timeout': True})

    pos = 0; trades_short = []
    for idx, row in df_valid.iterrows():
        if row['deviation'] > SHORT_TH and pos == 0:
            pos = 1
            entry_price = row['GL_close']
            entry_idx = idx
        elif row['deviation'] < EXIT_SHORT and pos == 1:
            pos = 0
            ret = (entry_price / row['GL_close'] - 1) * 100  # short
            trades_short.append({'entry': str(entry_idx), 'exit': str(idx), 'ret': ret})
        if pos == 1 and (idx - entry_idx).days >= 20:
            pos = 0
            ret = (entry_price / row['GL_close'] - 1) * 100
            trades_short.append({'entry': str(entry_idx), 'exit': str(idx), 'ret': ret, 'timeout': True})

    stats_trades = {}
    def trade_stats(trades, prefix):
        if not trades:
            return {}
        rets = [t['ret'] for t in trades]
        return {
            f'{prefix}_trades': len(trades),
            f'{prefix}_winrate': sum(1 for r in rets if r > 0) / len(rets) * 100,
            f'{prefix}_avg_ret': np.mean(rets),
            f'{prefix}_median_ret': np.median(rets),
            f'{prefix}_total_ret': np.sum(rets),
        }
    stats_trades.update(trade_stats(trades_long, 'long'))
    stats_trades.update(trade_stats(trades_short, 'short'))

else:
    stats_breakdown = {'count': 0}
    stats_trades = {}

# ── Print results ──

print("\n" + "=" * 65)
print("   GD vs GL CORRELATION & PARITY ANALYSIS  |  2024–2026")
print("=" * 65)

print(f"\n  Period:         {df.index.min()}  →  {df.index.max()}")
print(f"  Trading days:   {len(df)}")
print(f"  GD mean price:  ${df['GD_close'].mean():.2f}")
print(f"  GL mean price:  ₽{df['GL_close'].mean():.2f}")
print(f"  Si mean price:  ₽{df['Si_close'].mean():.2f}")

print(f"\n── Rolling Correlation Statistics ──")
print(f"  R(30) mean:     {df_valid['roll_corr_30'].mean():.3f}")
print(f"  R(30) median:   {df_valid['roll_corr_30'].median():.3f}")
print(f"  R(30) min:      {df_valid['roll_corr_30'].min():.3f}")
print(f"  R(30) < 0.2:    {len(breakdown)} days  /  {len(breakdown)/len(df_valid)*100:.1f}%")

print(f"\n── Parity Deviation (GL/(GD×Si)) ──")
print(f"  Mean:           {df_valid['parity_deviation_pct'].mean():+.2f}%")
print(f"  Median:         {df_valid['parity_deviation_pct'].median():+.2f}%")
print(f"  Std:            {df_valid['parity_deviation_pct'].std():.2f}%")
print(f"  Min:            {df_valid['parity_deviation_pct'].min():+.2f}%")
print(f"  Max:            {df_valid['parity_deviation_pct'].max():+.2f}%")

if stats_breakdown['count'] > 0:
    print(f"\n{'='*65}")
    print(f"   BREAKDOWN REGIME ANALYSIS  (rolling 30d r < 0.2)")
    print(f"{'='*65}")
    print(f"\n  ┌──────────────────────────┬──────────┬──────────┐")
    print(f"  │ Metric                   │   Avg    │  Median  │")
    print(f"  ├──────────────────────────┼──────────┼──────────┤")
    print(f"  │ Parity deviation %       │ {stats_breakdown['parity_dev_avg']:>+7.2f}% │ {stats_breakdown['parity_dev_med']:>+7.2f}% │")
    print(f"  │ GD OI spread (fiz-yur)   │ {stats_breakdown['GD_oi_spread_avg']:>+8.0f} │ {stats_breakdown['GD_oi_spread_med']:>+8.0f} │")
    print(f"  │ GL OI spread (fiz-yur)   │ {stats_breakdown['GL_oi_spread_avg']:>+8.0f} │ {stats_breakdown['GL_oi_spread_med']:>+8.0f} │")
    print(f"  └──────────────────────────┴──────────┴──────────┘")

    print(f"\n  ── Forward Returns After Breakdown ──")
    print(f"  ┌──────┬──────────────────────┬──────────────────────┐")
    print(f"  │ Days │   GD return (avg/med) │   GL return (avg/med) │")
    print(f"  ├──────┼──────────────────────┼──────────────────────┤")
    for fwd in [3,5,10]:
        ga = stats_breakdown[f'GD_ret_{fwd}d_avg']
        gm = stats_breakdown[f'GD_ret_{fwd}d_med']
        la = stats_breakdown[f'GL_ret_{fwd}d_avg']
        lm = stats_breakdown[f'GL_ret_{fwd}d_med']
        print(f"  │ {fwd:>4d} │ {ga:>+6.2f}%  / {gm:>+5.2f}%      │ {la:>+6.2f}%  / {lm:>+5.2f}%      │")
    print(f"  └──────┴──────────────────────┴──────────────────────┘")

    print(f"\n  ── Hit Rate (positive return after breakdown) ──")
    print(f"  ┌──────┬────────┬────────┐")
    print(f"  │ Days │  GD %  │  GL %  │")
    print(f"  ├──────┼────────┼────────┤")
    for fwd in [3,5,10]:
        gpos = stats_breakdown[f'GD_ret_{fwd}d_pos']
        lpos = stats_breakdown[f'GL_ret_{fwd}d_pos']
        print(f"  │ {fwd:>4d} │ {gpos:>5.1f}% │ {lpos:>5.1f}% │")
    print(f"  └──────┴────────┴────────┘")

if stats_trades:
    dev_std_print = df_valid['deviation'].std()
    long_th = -dev_std_print
    short_th = dev_std_print
    print(f"\n{'='*65}")
    print(f"   MEAN-REVERSION STRATEGY  (±{dev_std_print:.2f}% sigma, 20d timeout)")
    print(f"{'='*65}")
    if 'long_trades' in stats_trades:
        print(f"\n  ── Long GL (deviation < {long_th:.2f}%) ──")
        print(f"  Trades:         {stats_trades['long_trades']}")
        print(f"  Win rate:       {stats_trades['long_winrate']:.1f}%")
        print(f"  Avg return:     {stats_trades['long_avg_ret']:+.2f}%")
        print(f"  Med return:     {stats_trades['long_median_ret']:+.2f}%")
        print(f"  Total return:   {stats_trades['long_total_ret']:+.2f}%")
    if 'short_trades' in stats_trades:
        print(f"\n  ── Short GL (deviation > {short_th:+.2f}%) ──")
        print(f"  Trades:         {stats_trades['short_trades']}")
        print(f"  Win rate:       {stats_trades['short_winrate']:.1f}%")
        print(f"  Avg return:     {stats_trades['short_avg_ret']:+.2f}%")
        print(f"  Med return:     {stats_trades['short_median_ret']:+.2f}%")
        print(f"  Total return:   {stats_trades['short_total_ret']:+.2f}%")

print(f"\n{'='*65}")
print(f"   Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
print(f"{'='*65}")

cur.close()
conn.close()
