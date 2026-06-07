#!/home/user/venvs/tqa/main/bin/python
"""
AUDJPY 2025-2026 Full Analysis Dashboard.
Crowd vs Price analysis: DOM clusters, crowd balance, correlation breakdowns.
"""
import psycopg2
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import plotly.utils
from datetime import datetime, timedelta
import json, warnings, os
warnings.filterwarnings('ignore')

DB_HOST = '10.0.0.64'
DB_NAME = 'forex'
DB_USER = 'postgres'
DB_PASS = 'postgres'
START = '2025-01-01'
END = '2026-05-31'
SYM = 'audjpy'
OUTPUT_HTML = '/home/user/.hermes/cache/screenshots/tqa/audjpy_2025_2026_dashboard.html'
OUTPUT_PNG = '/home/user/.hermes/cache/screenshots/tqa/audjpy_2025_2026_dashboard.png'

conn = psycopg2.connect(host=DB_HOST, dbname=DB_NAME, user=DB_USER, password=DB_PASS)

# ─── 1. Price data ───
price_df = pd.read_sql(f"SELECT time, price FROM {SYM}_data WHERE time >= '{START}' AND time <= '{END}' ORDER BY time", conn)
price_df['time'] = pd.to_datetime(price_df['time'])
if price_df['time'].dt.tz is not None:
    price_df['time'] = price_df['time'].dt.tz_localize(None)
price_df = price_df.set_index('time')
price_df = price_df[~price_df.index.duplicated(keep='first')]
price_df.columns = ['price']
print(f"Price: {len(price_df)} bars")

# ─── 2. DOM data (sample for crowd analysis) ───
# Get daily DOM snapshots
dom_df = pd.read_sql(f"""
    SELECT time, price, positions 
    FROM {SYM}_dom 
    WHERE time >= '{START}' AND positions IS NOT NULL
    ORDER BY time
""", conn)
dom_df['time'] = pd.to_datetime(dom_df['time'])
if dom_df['time'].dt.tz is not None:
    dom_df['time'] = dom_df['time'].dt.tz_localize(None)
print(f"DOM: {len(dom_df)} rows")

# ─── 3. Compute daily crowd metrics ───
dom_df['date'] = dom_df['time'].dt.date
daily_crowd = dom_df.groupby('date').apply(lambda g: pd.Series({
    'long_vol': float(np.sum(g['positions'][g['positions'] > 0])) if np.any(g['positions'] > 0) else 0,
    'short_vol': float(abs(np.sum(g['positions'][g['positions'] < 0]))) if np.any(g['positions'] < 0) else 0,
    'total_vol': float(np.sum(abs(g['positions']))),
    'n_levels': len(g),
    'max_pos': float(g['positions'].max()),
    'min_pos': float(g['positions'].min()),
})).reset_index()
daily_crowd['total'] = daily_crowd['long_vol'] + daily_crowd['short_vol']
daily_crowd['crowd_balance'] = daily_crowd['long_vol'] / daily_crowd['total'].replace(0, 1)
daily_crowd['crowd_balance'] = daily_crowd['crowd_balance'] * 2 - 1  # -1 (all short) to +1 (all long)
daily_crowd['date'] = pd.to_datetime(daily_crowd['date'])
daily_crowd = daily_crowd.sort_values('date')
print(f"Daily crowd: {len(daily_crowd)} days")

# ─── 4. Merge with price ───
price_daily = price_df.resample('D').agg({'price': 'last'}).dropna()
price_daily.index = price_daily.index.date
price_daily = price_daily.reset_index()
price_daily.columns = ['date', 'price']
price_daily['date'] = pd.to_datetime(price_daily['date'])
price_daily['pct_change'] = price_daily['price'].pct_change().shift(-1) * 100  # next day change %
price_daily['pct_change_24h'] = price_daily['price'].pct_change() * 100  # prev day change %

merged = daily_crowd.merge(price_daily, on='date', how='inner').dropna()
print(f"Merged: {len(merged)} days")

# ─── 5. Correlation stats ───
corr_cb_next = merged['crowd_balance'].corr(merged['pct_change'])
corr_cb_prev = merged['crowd_balance'].corr(merged['pct_change_24h'])
print(f"Corr(CB→next day): r={corr_cb_next:.4f}")
print(f"Corr(CB←prev day): r={corr_cb_prev:.4f}")

# Crowd error: when crowd balance is >0 (long bias) but price goes down next day
merged['crowd_error'] = ((merged['crowd_balance'] > 0.3) & (merged['pct_change'] < 0)) | \
                        ((merged['crowd_balance'] < -0.3) & (merged['pct_change'] > 0))
error_rate = merged['crowd_error'].mean() * 100
print(f"Crowd error rate: {error_rate:.1f}%")

# Monthly error rates
merged['month'] = merged['date'].dt.to_period('M')
monthly_error = merged.groupby('month')['crowd_error'].mean() * 100

# ─── 6. Economic events on AUDJPY ───
events = pd.read_sql("""
    SELECT event_time, country_code, name, importance
    FROM economic_calendar
    WHERE (country_code = 'AU' OR country_code = 'JP')
      AND event_time >= %s AND event_time <= %s
      AND importance >= 2
    ORDER BY event_time
""", conn, params=(START, END))
events['event_time'] = pd.to_datetime(events['event_time'])
# Mark event days
events['date'] = events['event_time'].dt.date
event_days = set(events['date'])
merged['has_event'] = merged['date'].dt.date.isin(event_days)

# Event day vs non-event day error rates
if merged['has_event'].sum() > 10:
    event_error = merged[merged['has_event']]['crowd_error'].mean() * 100
    no_event_error = merged[~merged['has_event']]['crowd_error'].mean() * 100
    print(f"Error rate ON event days: {event_error:.1f}%")
    print(f"Error rate OFF event days: {no_event_error:.1f}%")

# ─── 7. COT for AUD/JPY ───
cot_aud = pd.read_sql("""
    SELECT event_time, actual_value FROM economic_calendar
    WHERE event_code = 'cftc-aud-non-commercial-net-positions'
      AND actual_value IS NOT NULL AND event_time >= '2024-06-01'
    ORDER BY event_time
""", conn)
cot_jpy = pd.read_sql("""
    SELECT event_time, actual_value FROM economic_calendar
    WHERE event_code = 'cftc-jpy-non-commercial-net-positions'
      AND actual_value IS NOT NULL AND event_time >= '2024-06-01'
    ORDER BY event_time
""", conn)

for name, df in [('AUD', cot_aud), ('JPY', cot_jpy)]:
    if len(df) > 15:
        df['event_time'] = pd.to_datetime(df['event_time'])
        vals = df['actual_value'].astype(float).values
        z = np.full(len(vals), np.nan)
        for i in range(52, len(vals)):
            w = vals[i-52:i]
            m, s = w.mean(), w.std()
            z[i] = (vals[i] - m) / s if s > 0 else 0
        df['z_52w'] = z
    print(f"COT {name}: {len(df)} weeks")

conn.close()

# ─── 8. BUILD DASHBOARD ───
fig = make_subplots(
    rows=4, cols=2,
    row_heights=[200, 200, 180, 180],
    column_widths=[0.5, 0.5],
    subplot_titles=(
        'Price', 'Crowd Balance (daily)',
        'Crowd Balance → +24h return', 'Crowd Balance distribution',
        f'Crowd error rate (avg {error_rate:.0f}%)', 'Long vs Short volume ratio',
        'COT AUD z-score (52w)', 'COT JPY z-score (52w)'
    ),
    vertical_spacing=0.06,
    horizontal_spacing=0.05
)

# Row 1 Col 1: Price
fig.add_trace(go.Scatter(
    x=price_df.index, y=price_df['price'],
    mode='lines', name='Price', line=dict(color='royalblue', width=1.2),
    hovertemplate='%{x}<br>$%{y:.2f}<extra></extra>'
), row=1, col=1)
fig.update_xaxes(type='date', row=1, col=1)
fig.add_annotation(xref='paper', yref='paper', x=0.02, y=0.98,
    text=f'Max: {price_df["price"].max():.2f}', showarrow=False,
    font=dict(size=10, color='#8b949e'), row=1, col=1)
fig.add_annotation(xref='paper', yref='paper', x=0.02, y=0.88,
    text=f'Min: {price_df["price"].min():.2f}', showarrow=False,
    font=dict(size=10, color='#8b949e'), row=1, col=1)

# Row 1 Col 2: Crowd Balance
colors = ['#3fb950' if v >= 0 else '#f85149' for v in merged['crowd_balance']]
fig.add_trace(go.Bar(
    x=merged['date'], y=merged['crowd_balance'],
    marker_color=colors, name='Crowd Balance',
    hovertemplate='%{x}<br>CB=%{y:.3f}<extra></extra>'
), row=1, col=2)
fig.add_hline(y=0, line_dash='solid', line_color='gray', line_width=0.5, row=1, col=2)
fig.add_hline(y=0.3, line_dash='dot', line_color='orange', line_width=0.8, row=1, col=2)
fig.add_hline(y=-0.3, line_dash='dot', line_color='orange', line_width=0.8, row=1, col=2)
fig.add_annotation(xref='paper', yref='paper', x=0.98, y=0.98,
    text=f'Средний CB: {merged["crowd_balance"].mean():.3f}', showarrow=False,
    font=dict(size=10, color='#8b949e'), row=1, col=2)

# Row 2 Col 1: Scatter CB vs next day return
fig.add_trace(go.Scattergl(
    x=merged['crowd_balance'], y=merged['pct_change'],
    mode='markers', marker=dict(size=3, color='royalblue', opacity=0.4),
    name='CB→+24h',
    hovertemplate='CB=%{x:.3f}<br>Δ=%{y:.2f}%<extra></extra>'
), row=2, col=1)
# Regression line
from numpy import polyfit
m, b = polyfit(merged['crowd_balance'].values, merged['pct_change'].values, 1)
x_range = np.array([merged['crowd_balance'].min(), merged['crowd_balance'].max()])
fig.add_trace(go.Scatter(
    x=x_range, y=m*x_range+b,
    mode='lines', name=f'Регрессия (r={corr_cb_next:.3f})',
    line=dict(color='orange', width=1.5, dash='dash')
), row=2, col=1)
fig.add_annotation(xref='paper', yref='paper', x=0.98, y=0.98,
    text=f'Corr(CB→+24h): r={corr_cb_next:.4f}', showarrow=False,
    font=dict(size=10, color='#8b949e'), row=2, col=1)

# Row 2 Col 2: Distribution
fig.add_trace(go.Histogram(
    x=merged['crowd_balance'], nbinsx=30,
    marker_color='#f85149', name='Распределение',
    hovertemplate='CB=%{x:.2f}<br>дней=%{y}<extra></extra>'
), row=2, col=2)

# Row 3 Col 1: Crowd error rate by month
fig.add_trace(go.Scatter(
    x=[str(m) for m in monthly_error.index], y=monthly_error.values,
    mode='lines+markers', name='% crowd errors',
    line=dict(color='#d29922', width=1.5),
    marker=dict(size=6),
    hovertemplate='%{x}<br>%{y:.1f}%<extra></extra>'
), row=3, col=1)
fig.add_hline(y=error_rate, line_dash='dash', line_color='gray', line_width=0.8, row=3, col=1)
fig.add_annotation(xref='paper', yref='paper', x=0.98, y=0.98,
    text=f'Средняя: {error_rate:.1f}%', showarrow=False,
    font=dict(size=10, color='#8b949e'), row=3, col=1)

# Row 3 Col 2: Longs/Shorts ratio
daily_crowd['ratio'] = daily_crowd['long_vol'] / daily_crowd['short_vol'].replace(0, 1)
fig.add_trace(go.Scatter(
    x=daily_crowd['date'], y=daily_crowd['ratio'],
    mode='lines', name='L/S ratio', line=dict(color='#3fb950', width=1),
    hovertemplate='%{x}<br>L/S=%{y:.2f}<extra></extra>'
), row=3, col=2)
fig.add_hline(y=1, line_dash='solid', line_color='gray', line_width=0.5, row=3, col=2)

# Row 4 Col 1: COT AUD
if len(cot_aud) > 20:
    cot_aud_plot = cot_aud.dropna(subset=['z_52w'])
    colors_cot = ['#3fb950' if abs(v) < 1.5 else ('#d29922' if abs(v) < 2.0 else '#f85149') 
                  for v in cot_aud_plot['z_52w']]
    fig.add_trace(go.Bar(
        x=cot_aud_plot['event_time'], y=cot_aud_plot['z_52w'],
        marker_color=colors_cot, name='COT AUD',
        hovertemplate='%{x}<br>z=%{y:.2f}<extra></extra>'
    ), row=4, col=1)
    fig.add_hline(y=1.5, line_dash='dot', line_color='orange', line_width=0.8, row=4, col=1)
    fig.add_hline(y=-1.5, line_dash='dot', line_color='orange', line_width=0.8, row=4, col=1)
    fig.add_hline(y=0, line_dash='solid', line_color='gray', line_width=0.5, row=4, col=1)

# Row 4 Col 2: COT JPY
if len(cot_jpy) > 20:
    cot_jpy_plot = cot_jpy.dropna(subset=['z_52w'])
    colors_cot = ['#3fb950' if abs(v) < 1.5 else ('#d29922' if abs(v) < 2.0 else '#f85149') 
                  for v in cot_jpy_plot['z_52w']]
    fig.add_trace(go.Bar(
        x=cot_jpy_plot['event_time'], y=cot_jpy_plot['z_52w'],
        marker_color=colors_cot, name='COT JPY',
        hovertemplate='%{x}<br>z=%{y:.2f}<extra></extra>'
    ), row=4, col=2)
    fig.add_hline(y=1.5, line_dash='dot', line_color='orange', line_width=0.8, row=4, col=2)
    fig.add_hline(y=-1.5, line_dash='dot', line_color='orange', line_width=0.8, row=4, col=2)
    fig.add_hline(y=0, line_dash='solid', line_color='gray', line_width=0.5, row=4, col=2)

# ─── Layout ───
fig.update_layout(
    height=1100,
    title_text=f'AUDJPY 2025-2026 - Crowd vs Price Analysis | Bars: {len(price_df)} | Days: {len(merged)}',
    title_font_size=14,
    hovermode='x unified',
    margin=dict(l=50, r=30, t=60, b=20),
    plot_bgcolor='#111111', paper_bgcolor='#1a1a2e',
    font=dict(color='#e0e0e0', size=10),
    legend=dict(orientation='h', yanchor='bottom', y=1.0, xanchor='right', x=1),
    bargap=0.1,
)
for i in range(1, 5):
    for j in range(1, 3):
        fig.update_xaxes(gridcolor='#333333', showgrid=True, row=i, col=j)
        fig.update_yaxes(gridcolor='#333333', showgrid=True, row=i, col=j)
# Fix date axes — convert all datetime traces to ISO strings
for idx in range(len(fig.data)):
    try:
        if hasattr(fig.data[idx].x, '__len__') and len(fig.data[idx].x) > 0:
            first = fig.data[idx].x[0]
            if hasattr(first, 'isoformat'):
                fig.data[idx].x = [d.isoformat() if hasattr(d, 'isoformat') else d for d in fig.data[idx].x]
    except:
        pass
# Set explicit date range for all date plots
for row, col in [(1,1), (1,2), (3,2), (4,1), (4,2)]:
    fig.update_xaxes(type='date', range=['2024-12-15', '2026-06-15'], row=row, col=col)

fig.write_html(OUTPUT_HTML, include_plotlyjs='cdn', full_html=False)
print(f"\nDashboard saved: {OUTPUT_HTML}")
print(f"  Price range: {price_df.index[0].date()} → {price_df.index[-1].date()}")
print(f"  Crowd Balance avg: {merged['crowd_balance'].mean():.3f}")
print(f"  Corr(CB→+24h): {corr_cb_next:.4f}")
print(f"  Crowd error rate: {error_rate:.1f}%")
print("DONE")
