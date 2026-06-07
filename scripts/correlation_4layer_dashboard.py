#!/home/user/venvs/tqa/main/bin/python
"""
Correlation 4-layer dashboard — v2 clean.
"""
import psycopg2
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, timedelta
import warnings, sys
warnings.filterwarnings('ignore')

DB_HOST = '10.0.0.64'
DB_NAME = 'forex'
DB_USER = 'postgres'
DB_PASS = 'postgres'
OUTPUT_HTML = '/home/user/.hermes/cache/screenshots/tqa/correlation_4layer_dashboard.html'

conn = psycopg2.connect(host=DB_HOST, dbname=DB_NAME, user=DB_USER, password=DB_PASS)

# ─── 1. Load prices (all at once, minimal transforms) ───
print("Loading prices...")
START, END = '2024-06-01', '2025-09-30'
ROLLING = 120

def load_price(sym):
    df = pd.read_sql(f"SELECT time, price FROM {sym}_data WHERE time >= '{START}' AND time <= '{END}' ORDER BY time", conn)
    df['time'] = pd.to_datetime(df['time'])
    if df['time'].dt.tz is not None:
        df['time'] = df['time'].dt.tz_localize(None)
    df = df.set_index('time')
    return df['price'][~df.index.duplicated(keep='first')]

prices = {}
for sym in ['eurusd','gbpusd','eurjpy','gbpjpy','audjpy']:
    prices[sym] = load_price(sym)
    print(f"  {sym}: {len(prices[sym])} bars")

price_df = pd.DataFrame(prices).dropna()
print(f"  Merged: {len(price_df)} bars")

returns = np.log(price_df / price_df.shift(1)).dropna()

pairs = [
    ('eurusd','gbpusd','EURUSD — GBPUSD', 'royalblue'),
    ('eurjpy','gbpjpy','EURJPY — GBPJPY', '#44cc44'),
    ('audjpy','gbpjpy','AUDJPY — GBPJPY', '#ff9944'),
]

corr_data = {}
for a,b,label,clr in pairs:
    c = returns[a].rolling(ROLLING).corr(returns[b]).dropna()
    corr_data[label] = {'corr': c, 'color': clr}
    print(f"  Corr {label}: {len(c)} bars")

main_corr = corr_data['EURUSD — GBPUSD']['corr']
t0, t1 = main_corr.index[0], main_corr.index[-1]

# ─── 2. Calendar events (Layer 1) ───
print("\nLoading events...")
events = pd.read_sql("""
    SELECT event_time, country_code, name, importance
    FROM economic_calendar
    WHERE event_time >= %s AND event_time <= %s AND importance >= 2
    ORDER BY event_time
""", conn, params=('2024-01-01', '2025-12-31'))

events['event_time'] = events['event_time'].values.astype('datetime64[ns]')
critical = events[events['importance'] == 3].copy()
critical['start_b'] = critical['event_time'] - timedelta(hours=4)
critical['end_b'] = critical['event_time'] + timedelta(hours=6)
print(f"  Critical events: {len(critical)}")

# ─── 3. COT extremes (Layer 2) ───
print("Loading COT...")
cot_raw = pd.read_sql("""
    SELECT event_time, event_code, actual_value
    FROM economic_calendar
    WHERE event_code LIKE 'cftc-%%-non-commercial-net-positions'
      AND actual_value IS NOT NULL AND event_time >= '2023-01-01'
    ORDER BY event_time
""", conn)
cot_raw['event_time'] = cot_raw['event_time'].values.astype('datetime64[ns]')

cot_codes = {
    'cftc-eur-non-commercial-net-positions': 'EUR',
    'cftc-gbp-non-commercial-net-positions': 'GBP',
    'cftc-jpy-non-commercial-net-positions': 'JPY',
    'cftc-aud-non-commercial-net-positions': 'AUD',
}

cot_list = []
for code, name in cot_codes.items():
    sub = cot_raw[cot_raw['event_code'] == code].copy()
    if len(sub) < 25: continue
    v = sub['actual_value'].astype(float).values
    z = np.full(len(sub), np.nan)
    for i in range(52, len(sub)):
        w = v[i-52:i]
        m, s = w.mean(), w.std()
        z[i] = (v[i] - m) / s if s > 0 else 0
    sub['z'] = z
    extreme = sub[abs(sub['z']) >= 1.5]
    for _, r in extreme.iterrows():
        cot_list.append({
            'time': r['event_time'], 'inst': name, 'z': r['z'],
            'start_b': r['event_time'] - timedelta(hours=12),
            'end_b': r['event_time'] + timedelta(days=7)
        })

cot_df = pd.DataFrame(cot_list) if cot_list else pd.DataFrame()
print(f"  COT extremes: {len(cot_df)}")

# ─── 4. DOM extremes (Layer 3) — light sampling ───
print("Loading DOM...")
dom_list = []
for d in pd.date_range(start='2024-06-15', end='2025-09-15', freq='MS'):
    ds, de = d - timedelta(hours=6), d + timedelta(hours=6)
    try:
        df = pd.read_sql(f"SELECT time, positions FROM eurusd_dom WHERE time >= '{ds}' AND time <= '{de}' AND positions IS NOT NULL ORDER BY time LIMIT 300", conn)
        if len(df) < 10: continue
        p = df['positions'].values
        pc = p[~np.isnan(p)]
        if len(pc) < 5: continue
        lo = float(np.sum(pc[pc > 0])) if np.any(pc > 0) else 0
        sh = float(abs(np.sum(pc[pc < 0]))) if np.any(pc < 0) else 0
        r = lo / (lo + sh) if (lo + sh) > 0 else 0.5
        if r > 0.8 or r < 0.2:
            dom_list.append({'time': d, 'ratio': r, 'side': 'LONG' if r > 0.5 else 'SHORT',
                             'start_b': d - timedelta(days=14), 'end_b': d + timedelta(days=14)})
    except: pass

dom_df = pd.DataFrame(dom_list) if dom_list else pd.DataFrame()
print(f"  DOM extremes: {len(dom_df)}")

conn.close()

# ─── 5. Correlation self-block (Layer 4) ───
corr_break = main_corr[abs(main_corr) < 0.3]

# ─── BUILD CHART ───
print("\nBuilding chart...")
fig = make_subplots(rows=5, cols=1, shared_xaxes=True,
    vertical_spacing=0.025,
    row_heights=[160, 45, 45, 45, 45],
    subplot_titles=[
        'Correlation (all pairs) — красные зоны = блокировка по любому признаку',
        '🔴 LAYER 1: Economic Calendar (importance=3)',
        '🟠 LAYER 2: COT extreme (|z| ≥ 1.5)',
        '🟢 LAYER 3: DOM extreme (>80% one side)',
        '⚠️ LAYER 4: Correlation < 0.3 (self-block)',
    ])

# Row 1: Correlation lines
for label, data in corr_data.items():
    fig.add_trace(go.Scatter(
        x=data['corr'].index, y=data['corr'].values,
        mode='lines', name=label,
        line=dict(color=data['color'], width=1.2),
        hovertemplate='%{x}<br>%{meta}: r=%{y:.3f}<extra></extra>',
        meta=[label]
    ), row=1, col=1)

for y, c, s in [(0.5, 'green', 'dash'), (0.3, 'orange', 'dot'), (0, 'gray', 'solid'), (-0.3, 'orange', 'dot')]:
    fig.add_hline(y=y, line_dash=s, line_color=c, line_width=0.8, row=1, col=1)

# Helper to add shaded regions
def add_vrects(dates, row, color):
    for s, e in dates:
        fig.add_vrect(x0=s, x1=e, fillcolor=color, layer='below', line_width=0, row=row, col=1)

# Row 2: Calendar
add_vrects(list(zip(critical['start_b'], critical['end_b'])), 2, 'rgba(255,0,0,0.10)')
fig.add_trace(go.Scatter(
    x=critical['event_time'], y=[1]*len(critical),
    mode='markers', marker=dict(size=5, color='red', symbol='diamond'),
    text=critical.apply(lambda r: f"{r['country_code']}: {r['name'][:35]}", axis=1),
    hoverinfo='text+x', showlegend=False
), row=2, col=1)

# Row 3: COT
if not cot_df.empty:
    add_vrects(list(zip(cot_df['start_b'], cot_df['end_b'])), 3, 'rgba(255,165,0,0.10)')
    colors = {'EUR': '#ff4444', 'GBP': '#ffaa00', 'JPY': '#44aaff', 'AUD': '#44ff44'}
    for inst in cot_df['inst'].unique():
        sub = cot_df[cot_df['inst'] == inst]
        fig.add_trace(go.Scatter(
            x=sub['time'], y=[1]*len(sub),
            mode='markers', marker=dict(size=5, color=colors.get(inst, 'orange'), symbol='triangle-up'),
            name=f'COT {inst}',
            text=sub.apply(lambda r: f"COT {r['inst']} z={r['z']:.1f}", axis=1),
            hoverinfo='text+x'
        ), row=3, col=1)

# Row 4: DOM
if not dom_df.empty:
    add_vrects(list(zip(dom_df['start_b'], dom_df['end_b'])), 4, 'rgba(0,200,0,0.08)')
    for side, clr in [('LONG', '#44ff44'), ('SHORT', '#ff4444')]:
        sub = dom_df[dom_df['side'] == side]
        if not sub.empty:
            fig.add_trace(go.Scatter(
                x=sub['time'], y=[1]*len(sub),
                mode='markers', marker=dict(size=5, color=clr, symbol='square'),
                name=f'DOM {side}',
                text=sub.apply(lambda r: f"DOM {r['side']} {r['ratio']:.0%}", axis=1),
                hoverinfo='text+x'
            ), row=4, col=1)

# Row 5: Self-block
if len(corr_break) > 0:
    fig.add_trace(go.Scatter(
        x=corr_break.index, y=[1]*len(corr_break),
        mode='markers', marker=dict(size=1.5, color='white', opacity=0.4),
        name='Corr < 0.3', hoverinfo='skip'
    ), row=5, col=1)

fig.update_layout(
    height=750,
    title_text=f'Correlation with 4 Blocking Layers | {START} — {END}',
    title_font_size=13,
    hovermode='x unified',
    margin=dict(l=50, r=15, t=50, b=15),
    plot_bgcolor='#111111', paper_bgcolor='#1a1a2e',
    font=dict(color='#e0e0e0', size=9),
    legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='left', x=0),
)

for i in range(1, 6):
    fig.update_xaxes(gridcolor='#333333', showgrid=True, row=i, col=1, range=[t0, t1])
    if i > 1:
        fig.update_yaxes(visible=False, row=i, col=1)

fig.write_html(OUTPUT_HTML, include_plotlyjs='cdn', full_html=False)
print(f"\nDashboard saved: {OUTPUT_HTML}")
print("DONE")
