#!/home/user/venvs/tqa/main/bin/python
"""
AUDJPY 2025-2026 Full Analysis Dashboard v2.
Crowd vs Price analysis + US events + AU/JP events + COT.
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

DB_HOST = '10.0.0.60'
DB_NAME = 'forex'
DB_USER = 'postgres'
DB_PASS = 'postgres'
START = '2025-01-01'
END = '2026-05-31'
SYM = 'audjpy'
OUTPUT_HTML = '/home/user/.hermes/cache/screenshots/tqa/audjpy_2025_2026_dashboard_v2.html'

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

# ─── 2. DOM data ───
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

# ─── 3. Daily crowd metrics ───
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
daily_crowd['crowd_balance'] = daily_crowd['crowd_balance'] * 2 - 1  # -1 to +1
daily_crowd['date'] = pd.to_datetime(daily_crowd['date'])
daily_crowd = daily_crowd.sort_values('date')
print(f"Daily crowd: {len(daily_crowd)} days")

# ─── 4. Merge with price ───
price_daily = price_df.resample('D').agg({'price': 'last'}).dropna()
price_daily.index = price_daily.index.date
price_daily = price_daily.reset_index()
price_daily.columns = ['date', 'price']
price_daily['date'] = pd.to_datetime(price_daily['date'])
price_daily['pct_change'] = price_daily['price'].pct_change().shift(-1) * 100
price_daily['pct_change_24h'] = price_daily['price'].pct_change() * 100

merged = daily_crowd.merge(price_daily, on='date', how='inner').dropna()
print(f"Merged: {len(merged)} days")

corr_cb_next = merged['crowd_balance'].corr(merged['pct_change'])
corr_cb_prev = merged['crowd_balance'].corr(merged['pct_change_24h'])
print(f"Corr(CB→next day): r={corr_cb_next:.4f}")
print(f"Corr(CB←prev day): r={corr_cb_prev:.4f}")

merged['crowd_error'] = ((merged['crowd_balance'] > 0.3) & (merged['pct_change'] < 0)) | \
                        ((merged['crowd_balance'] < -0.3) & (merged['pct_change'] > 0))
error_rate = merged['crowd_error'].mean() * 100
print(f"Crowd error rate: {error_rate:.1f}%")

merged['month'] = merged['date'].dt.to_period('M')
monthly_error = merged.groupby('month')['crowd_error'].mean() * 100

# ─── 5. AU + JP events ───
au_jp_events = pd.read_sql("""
    SELECT event_time, country_code, name, importance
    FROM economic_calendar
    WHERE (country_code = 'AU' OR country_code = 'JP')
      AND event_time >= %s AND event_time <= %s
      AND importance >= 2
    ORDER BY event_time
""", conn, params=(START, END))
au_jp_events['event_time'] = pd.to_datetime(au_jp_events['event_time'])
au_jp_events['date'] = au_jp_events['event_time'].dt.date
au_jp_event_days = set(au_jp_events['date'])
merged['has_au_jp_event'] = merged['date'].dt.date.isin(au_jp_event_days)

if merged['has_au_jp_event'].sum() > 10:
    au_jp_error = merged[merged['has_au_jp_event']]['crowd_error'].mean() * 100
    no_event_error = merged[~merged['has_au_jp_event']]['crowd_error'].mean() * 100
    print(f"Error rate ON AU/JP event days: {au_jp_error:.1f}%")
    print(f"Error rate OFF AU/JP event days: {no_event_error:.1f}%")

# ─── 6. US events (importance=3 key macro) ───
us_key_codes = [
    'nonfarm-payrolls', 'consumer-price-index', 'consumer-price-index-mm',
    'consumer-price-index-yy', 'fed-interest-rate-decision',
    'gross-domestic-product-qq', 'gdp-sales-qq',
    'unemployment-rate', 'ism-manufacturing-pmi', 'ism-non-manufacturing-pmi',
    'retail-sales-mm', 'retail-sales-ex-autos-mm',
    'producer-price-index-mm', 'durable-goods-orders',
    'adp-nonfarm-employment-change', 'philadelphia-fed-manufacturing-index',
    'consumer-price-index-ex-food-energy-mm', 'ism-prices-paid',
    'ism-non-manufacturing-prices', 'durable-goods-orders-ex-transportation',
    'consumer-price-index-ex-food-energy-nsa-mm',
]
us_macro = pd.read_sql("""
    SELECT event_time, event_code, country_code, name, importance
    FROM economic_calendar
    WHERE country_code = 'US'
      AND event_time >= %s AND event_time <= %s
      AND event_code = ANY(%s)
      AND importance >= 3
    ORDER BY event_time
""", conn, params=(START, END, us_key_codes))

# Also add initial-jobless-claims separately (weekly)
jobless = pd.read_sql("""
    SELECT event_time, event_code, country_code, name, importance
    FROM economic_calendar
    WHERE country_code = 'US'
      AND event_time >= %s AND event_time <= %s
      AND event_code = 'initial-jobless-claims'
      AND importance >= 2
    ORDER BY event_time
""", conn, params=(START, END))

us_macro = pd.concat([us_macro, jobless], ignore_index=True)
us_macro['event_time'] = pd.to_datetime(us_macro['event_time'])
us_macro['date'] = us_macro['event_time'].dt.date
us_macro_days = set(us_macro['date'])
merged['has_us_event'] = merged['date'].dt.date.isin(us_macro_days)

# Event categories for breakdown
def classify_us_event(code):
    if not code:
        return 'Other'
    code = code.lower()
    if 'payroll' in code or 'nonfarm' in code or 'jobless' in code:
        return 'Employment'
    if 'cpi' in code or 'inflation' in code or 'ppi' in code or 'prices' in code:
        return 'Inflation'
    if 'fed' in code or 'interest-rate' in code:
        return 'FOMC'
    if 'gdp' in code:
        return 'GDP'
    if 'ism' in code:
        return 'ISM'
    if 'retail' in code or 'durable' in code:
        return 'Consumption'
    if 'philadelphia' in code:
        return 'Regional Fed'
    if 'unemployment' in code:
        return 'Employment'
    return 'Other'

us_macro['category'] = us_macro['event_code'].apply(classify_us_event)
# For each day, collect categories
day_categories = us_macro.groupby('date')['category'].apply(lambda x: list(set(x))).to_dict()

merged['us_event_cats'] = merged['date'].dt.date.map(
    lambda d: day_categories.get(d, [])
)

if merged['has_us_event'].sum() > 10:
    us_error = merged[merged['has_us_event']]['crowd_error'].mean() * 100
    no_event_2 = merged[~merged['has_us_event'] & ~merged['has_au_jp_event']]['crowd_error'].mean() * 100
    print(f"Error rate ON US macro event days: {us_error:.1f}%")
    print(f"Error rate ON NO events (pure): {no_event_2:.1f}%")

# Error rate by US event category
cat_errors = {}
for cat in ['Employment', 'Inflation', 'FOMC', 'GDP', 'ISM', 'Consumption', 'Regional Fed']:
    days_with_cat = merged['date'].dt.date.isin(
        us_macro[us_macro['category'] == cat]['date'].unique()
    )
    if days_with_cat.sum() >= 3:
        cat_errors[cat] = merged[days_with_cat]['crowd_error'].mean() * 100
        print(f"  {cat:15s}: {cat_errors[cat]:.1f}% error rate ({days_with_cat.sum()} days)")

# ─── 7. COT ───
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

# ─── Build summary text block ───
all_events = merged['has_us_event'] | merged['has_au_jp_event']
pure_no_event = merged[~all_events]
no_event_error_rate = pure_no_event['crowd_error'].mean() * 100

# ─── 8. BUILD DASHBOARD (3 rows now: price+events, crowd, cot) ───
fig = make_subplots(
    rows=5, cols=2,
    row_heights=[140, 220, 160, 150, 150],
    column_widths=[0.5, 0.5],
    subplot_titles=(
        'Price + US events', 'Price + AU/JP events',
        'Crowd Balance (daily) + error overlay',
        'Crowd error breakdown',
        f'Crowd error: US events = {us_error:.0f}% | AU/JP = {au_jp_error:.0f}% | None = {no_event_error_rate:.0f}%',
        'Long / Short volume ratio',
        'COT AUD z-score (52w)', 'COT JPY z-score (52w)'
    ),
    vertical_spacing=0.05,
    horizontal_spacing=0.05
)

# Row 1 Col 1: Price + US event markers
fig.add_trace(go.Scatter(
    x=price_df.index, y=price_df['price'],
    mode='lines', name='Price', line=dict(color='royalblue', width=1.0),
    hovertemplate='%{x}<br>$%{y:.2f}<extra></extra>'
), row=1, col=1)

# US event markers on price
us_event_colors_map = {
    'Employment': '#f85149', 'Inflation': '#d29922',
    'FOMC': '#da3633', 'GDP': '#3fb950',
    'ISM': '#58a6ff', 'Consumption': '#bc8cff',
    'Regional Fed': '#79c0ff', 'Other': '#8b949e'
}
us_event_legend = set()
for _, ev in us_macro.iterrows():
    cat = ev['category']
    clr = us_event_colors_map.get(cat, '#8b949e')
    lbl = cat if cat not in us_event_legend else None
    us_event_legend.add(cat)
    
    # Only show first ~50 events to avoid clutter
    fig.add_trace(go.Scatter(
        x=[ev['event_time']], y=[None],
        mode='markers',
        marker=dict(
            symbol='triangle-down', size=10, color=clr,
            line=dict(width=0.5, color='white')
        ),
        name=lbl, showlegend=lbl is not None,
        legendgroup=cat,
        hovertemplate=f'{cat}<br>{ev["name"]}<br>%{{x}}<extra></extra>'
    ), row=1, col=1)

fig.update_xaxes(type='date', range=['2024-12-15', '2026-06-15'], row=1, col=1)
fig.update_yaxes(title_text='Price', row=1, col=1)

# Row 1 Col 2: Price + AU/JP event markers
fig.add_trace(go.Scatter(
    x=price_df.index, y=price_df['price'],
    mode='lines', name='Price', line=dict(color='royalblue', width=1.0),
    showlegend=False,
    hovertemplate='%{x}<br>$%{y:.2f}<extra></extra>'
), row=1, col=2)

aujp_legend = set()
for _, ev in au_jp_events.iterrows():
    lbl = ev['country_code'] if ev['country_code'] not in aujp_legend else None
    aujp_legend.add(ev['country_code'])
    clr = '#f85149' if ev['country_code'] == 'AU' else '#d29922'
    fig.add_trace(go.Scatter(
        x=[ev['event_time']], y=[None],
        mode='markers',
        marker=dict(symbol='triangle-down', size=10, color=clr,
                    line=dict(width=0.5, color='white')),
        name=lbl, showlegend=lbl is not None,
        legendgroup=ev['country_code'],
        hovertemplate=f'{ev["country_code"]}<br>{ev["name"]}<br>%{{x}}<extra></extra>'
    ), row=1, col=2)

fig.update_xaxes(type='date', range=['2024-12-15', '2026-06-15'], row=1, col=2)
fig.update_yaxes(title_text='Price', row=1, col=2)

# Row 2 Col 1: Crowd Balance with error overlay
# Green bars for correct predictions, red for errors
bar_colors = []
for _, row_data in merged.iterrows():
    if row_data['crowd_error']:
        bar_colors.append('#f85149')  # error = red
    elif row_data['crowd_balance'] >= 0:
        # Correct long bias: green gradient based on CB strength
        bar_colors.append('#3fb950')
    else:
        bar_colors.append('#58a6ff')  # correct short bias: blue

fig.add_trace(go.Bar(
    x=merged['date'], y=merged['crowd_balance'],
    marker_color=bar_colors, name='Crowd Balance',
    hovertemplate='%{x}<br>CB=%{y:.3f}<extra></extra>'
), row=2, col=1)
fig.add_hline(y=0, line_dash='solid', line_color='gray', line_width=0.5, row=2, col=1)
fig.add_hline(y=0.3, line_dash='dot', line_color='orange', line_width=0.8, row=2, col=1)
fig.add_hline(y=-0.3, line_dash='dot', line_color='orange', line_width=0.8, row=2, col=1)
fig.update_xaxes(type='date', range=['2024-12-15', '2026-06-15'], row=2, col=1)

# Row 2 Col 2: Scatter CB vs next day return, colored by event type
merged['has_any_event'] = merged['has_us_event'] | merged['has_au_jp_event']
event_colors_map = {'no_event': 'royalblue', 'au_jp': '#d29922', 'us': '#f85149', 'both': '#da3633'}
def get_event_color(row):
    if row['has_us_event'] and row['has_au_jp_event']:
        return 'both'
    if row['has_us_event']:
        return 'us'
    if row['has_au_jp_event']:
        return 'au_jp'
    return 'no_event'

scatter_data = merged.copy()
scatter_data['event_group'] = scatter_data.apply(get_event_color, axis=1)

for eg, label, clr in [('no_event', 'No events', 'royalblue'),
                         ('au_jp', 'AU/JP event', '#d29922'),
                         ('us', 'US event', '#f85149'),
                         ('both', 'Both', '#da3633')]:
    subset = scatter_data[scatter_data['event_group'] == eg]
    if len(subset) < 2:
        continue
    fig.add_trace(go.Scattergl(
        x=subset['crowd_balance'], y=subset['pct_change'],
        mode='markers', marker=dict(size=4, color=clr, opacity=0.4),
        name=label,
        hovertemplate='CB=%{x:.3f}<br>Δ=%{y:.2f}%<br>' + label + '<extra></extra>'
    ), row=2, col=2)

m, b = np.polyfit(merged['crowd_balance'].values, merged['pct_change'].values, 1)
x_range = np.array([merged['crowd_balance'].min(), merged['crowd_balance'].max()])
fig.add_trace(go.Scatter(
    x=x_range, y=m*x_range+b,
    mode='lines', name=f'Regr (r={corr_cb_next:.3f})',
    line=dict(color='orange', width=1.5, dash='dash')
), row=2, col=2)
fig.add_annotation(xref='paper', yref='paper', x=0.98, y=0.98,
    text=f'r={corr_cb_next:.4f}', showarrow=False,
    font=dict(size=10, color='#8b949e'), row=2, col=2)

# Row 3 Col 1: Error rate by US event category (bar chart)
categories_ordered = sorted(cat_errors.items(), key=lambda x: x[1], reverse=True)
cat_names = [c[0] for c in categories_ordered]
cat_vals = [c[1] for c in categories_ordered]
cat_colors = [us_event_colors_map.get(c[0], '#8b949e') for c in categories_ordered]

if cat_names:
    fig.add_trace(go.Bar(
        x=cat_names, y=cat_vals,
        marker_color=cat_colors,
        name='Error by category',
        hovertemplate='%{x}<br>%{y:.1f}%<extra></extra>'
    ), row=3, col=1)
    fig.add_hline(y=error_rate, line_dash='dash', line_color='gray', line_width=0.8, row=3, col=1)
    fig.add_annotation(xref='paper', yref='paper', x=0.98, y=0.98,
        text=f'Avg: {error_rate:.1f}%', showarrow=False,
        font=dict(size=10, color='#8b949e'), row=3, col=1)

# Row 3 Col 2: L/S ratio as before
daily_crowd_plot = daily_crowd.copy()
merged_dates = set(merged['date'].dt.date)
daily_crowd_plot['ratio'] = daily_crowd_plot['long_vol'] / daily_crowd_plot['short_vol'].replace(0, 1)
fig.add_trace(go.Scatter(
    x=daily_crowd_plot['date'], y=daily_crowd_plot['ratio'],
    mode='lines', name='L/S ratio', line=dict(color='#3fb950', width=1),
    hovertemplate='%{x}<br>L/S=%{y:.2f}<extra></extra>'
), row=3, col=2)
fig.add_hline(y=1, line_dash='solid', line_color='gray', line_width=0.5, row=3, col=2)
fig.update_xaxes(type='date', range=['2024-12-15', '2026-06-15'], row=3, col=2)

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
    fig.update_xaxes(type='date', range=['2024-12-15', '2026-06-15'], row=4, col=1)

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
    fig.update_xaxes(type='date', range=['2024-12-15', '2026-06-15'], row=4, col=2)

# Row 3 Col 1 was already used — let me put a summary annotation at bottom
# Actually let me add a summary panel at the bottom — use the 5th row's col 1+2 span

# Better: Row 4 was COT. Let me use Row 5 for a summary table.
# Actually I already have 5 rows. Row 5 will be used as summary text.
# Let me put big annotations there instead.

# ─── Build US event count by category ───
us_cat_counts = us_macro['category'].value_counts()

# ─── Layout ───
title = (f'AUDJPY 2025-2026 v2 | Bars: {len(price_df)} | Days: {len(merged)}<br>'
         f'<span style="font-size:11px">'
         f'🟢 Error: {no_event_error_rate:.0f}% (no events) | '
         f'🟠 AU/JP: {au_jp_error:.0f}% | '
         f'🔴 US macro: {us_error:.0f}% | '
         f'📊 Avg: {error_rate:.0f}%'
         f'</span>')

fig.update_layout(
    height=1300,
    title_text=title,
    title_font_size=14,
    hovermode='x unified',
    margin=dict(l=50, r=30, t=80, b=20),
    plot_bgcolor='#111111', paper_bgcolor='#1a1a2e',
    font=dict(color='#e0e0e0', size=10),
    legend=dict(orientation='h', yanchor='top', y=0.99, xanchor='center', x=0.5,
                font=dict(size=8), itemwidth=30, tracegroupgap=2),
    bargap=0.1,
)
for i in range(1, 6):
    for j in range(1, 3):
        fig.update_xaxes(gridcolor='#333333', showgrid=True, row=i, col=j)
        fig.update_yaxes(gridcolor='#333333', showgrid=True, row=i, col=j)

# Fix datetime serialization
for idx in range(len(fig.data)):
    try:
        if hasattr(fig.data[idx].x, '__len__') and len(fig.data[idx].x) > 0:
            first = fig.data[idx].x[0]
            if hasattr(first, 'isoformat'):
                fig.data[idx].x = [d.isoformat() if hasattr(d, 'isoformat') else d for d in fig.data[idx].x]
    except:
        pass

fig.write_html(OUTPUT_HTML, include_plotlyjs='cdn', full_html=False)
print(f"\nDashboard saved: {OUTPUT_HTML}")
print(f"  US macro events: {len(us_macro)} ({len(us_macro_days)} unique days)")
print(f"  AU/JP events: {len(au_jp_events)} ({len(au_jp_event_days)} unique days)")
print(f"  NFP days: {len(us_macro[us_macro['category'] == 'Employment']['date'].unique())}")
print(f"  CPI days: {len(us_macro[us_macro['category'] == 'Inflation']['date'].unique())}")
print(f"  FOMC days: {len(us_macro[us_macro['category'] == 'FOMC']['date'].unique())}")
print("DONE")
