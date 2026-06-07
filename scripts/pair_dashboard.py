#!/home/user/venvs/tqa/main/bin/python
"""
Universal Pair Dashboard v1.
Crowd vs Price analysis + country events + US macro + COT.

Usage:
  python scripts/pair_dashboard.py --sym audjpy [--start 2025-01-01] [--end 2026-05-31]
  python scripts/pair_dashboard.py --sym eurusd --start 2025-06-01
  python scripts/pair_dashboard.py --sym gbpjpy --all            # весь доступный период

Для batch-запуска по всем парам: python scripts/run_all_pairs.py
"""
import argparse, sys, json, warnings, os, re
from datetime import datetime, timedelta
import psycopg2
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
warnings.filterwarnings('ignore')

DB_HOST = '10.0.0.64'
DB_NAME = 'forex'
DB_USER = 'postgres'
DB_PASS = 'postgres'
OUTPUT_DIR = '/home/user/.hermes/cache/screenshots/tqa/'

# ─── Currency → COT event_code mapping ───
CURRENCY_COT_MAP = {
    'aud': 'cftc-aud-non-commercial-net-positions',
    'jpy': 'cftc-jpy-non-commercial-net-positions',
    'eur': 'cftc-eur-non-commercial-net-positions',
    'gbp': 'cftc-gbp-non-commercial-net-positions',
    'nzd': 'cftc-nzd-non-commercial-net-positions',
    'cad': 'cftc-cad-non-commercial-net-positions',
    'chf': 'cftc-chf-non-commercial-net-positions',
    'usd': None,  # USD нет в COT как standalone
    'xau': 'cftc-gold-non-commercial-net-positions',
}

# ─── Symbol → (base_currency, quote_currency, base_country, quote_country) ───
SYMBOL_MAP = {
    'audjpy': ('aud', 'jpy', 'AU', 'JP'),
    'audusd': ('aud', 'usd', 'AU', 'US'),
    'euraud': ('eur', 'aud', 'EU', 'AU'),
    'eurgbp': ('eur', 'gbp', 'EU', 'GB'),
    'eurjpy': ('eur', 'jpy', 'EU', 'JP'),
    'eurusd': ('eur', 'usd', 'EU', 'US'),
    'gbpjpy': ('gbp', 'jpy', 'GB', 'JP'),
    'gbpusd': ('gbp', 'usd', 'GB', 'US'),
    'nzdusd': ('nzd', 'usd', 'NZ', 'US'),
    'usdcad': ('usd', 'cad', 'US', 'CA'),
    'usdchf': ('usd', 'chf', 'US', 'CH'),
    'usdjpy': ('usd', 'jpy', 'US', 'JP'),
    'xauusd': ('xau', 'usd', 'XL', 'US'),
}

# ─── US key macro event codes ───
US_KEY_CODES = [
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

US_EVENT_COLORS = {
    'Employment': '#f85149', 'Inflation': '#d29922',
    'FOMC': '#da3633', 'GDP': '#3fb950',
    'ISM': '#58a6ff', 'Consumption': '#bc8cff',
    'Regional Fed': '#79c0ff', 'Other': '#8b949e',
}

def classify_us_event(code):
    if not code: return 'Other'
    c = code.lower()
    if 'payroll' in c or 'nonfarm' in c or 'jobless' in c or 'unemployment' in c: return 'Employment'
    if 'cpi' in c or 'inflation' in c or 'ppi' in c or 'prices' in c: return 'Inflation'
    if 'fed' in c or 'interest-rate' in c: return 'FOMC'
    if 'gdp' in c: return 'GDP'
    if 'ism' in c: return 'ISM'
    if 'retail' in c or 'durable' in c: return 'Consumption'
    if 'philadelphia' in c: return 'Regional Fed'
    return 'Other'


def analyze_pair(sym: str, start: str, end: str, conn) -> dict:
    """Run full analysis for one pair, return results dict."""
    sym_lower = sym.lower()
    if sym_lower not in SYMBOL_MAP:
        raise ValueError(f"Unknown symbol: {sym}. Supported: {list(SYMBOL_MAP.keys())}")
    
    base_ccy, quote_ccy, base_ctry, quote_ctry = SYMBOL_MAP[sym_lower]
    sym_upper = sym_lower.upper()
    results = {'symbol': sym_upper}
    
    # ─── 1. Load price data ───
    price_df = pd.read_sql(f"SELECT time, price FROM {sym_lower}_data WHERE time >= '{start}' AND time <= '{end}' ORDER BY time", conn)
    price_df['time'] = pd.to_datetime(price_df['time'])
    if price_df['time'].dt.tz is not None:
        price_df['time'] = price_df['time'].dt.tz_localize(None)
    price_df = price_df.set_index('time')
    price_df = price_df[~price_df.index.duplicated(keep='first')]
    price_df.columns = ['price']
    results['bars'] = len(price_df)
    print(f"  Price: {len(price_df)} bars")
    
    # ─── 2. Load DOM ───
    dom_df = pd.read_sql(f"""
        SELECT time, price, positions FROM {sym_lower}_dom
        WHERE time >= '{start}' AND positions IS NOT NULL ORDER BY time
    """, conn)
    dom_df['time'] = pd.to_datetime(dom_df['time'])
    if dom_df['time'].dt.tz is not None:
        dom_df['time'] = dom_df['time'].dt.tz_localize(None)
    print(f"  DOM: {len(dom_df)} rows")
    
    # ─── 3. Daily crowd ───
    dom_df['date'] = dom_df['time'].dt.date
    daily = dom_df.groupby('date').apply(lambda g: pd.Series({
        'long_vol': float(np.sum(g['positions'][g['positions'] > 0])) if np.any(g['positions'] > 0) else 0,
        'short_vol': float(abs(np.sum(g['positions'][g['positions'] < 0]))) if np.any(g['positions'] < 0) else 0,
    })).reset_index()
    daily['total'] = daily['long_vol'] + daily['short_vol']
    daily['crowd_balance'] = (daily['long_vol'] / daily['total'].replace(0, 1)) * 2 - 1
    daily['date'] = pd.to_datetime(daily['date'])
    daily = daily.sort_values('date')
    print(f"  Daily crowd: {len(daily)} days")
    
    # ─── 4. Merge with price ───
    price_d = price_df.resample('D').agg({'price': 'last'}).dropna()
    price_d.index = price_d.index.date
    price_d = price_d.reset_index(); price_d.columns = ['date', 'price']
    price_d['date'] = pd.to_datetime(price_d['date'])
    price_d['pct_change'] = price_d['price'].pct_change().shift(-1) * 100
    price_d['pct_change_24h'] = price_d['price'].pct_change() * 100
    
    merged = daily.merge(price_d, on='date', how='inner').dropna()
    results['days'] = len(merged)
    print(f"  Merged: {len(merged)} days")
    
    corr_cb_next = merged['crowd_balance'].corr(merged['pct_change'])
    results['corr_cb_next'] = corr_cb_next
    print(f"  Corr(CB→+24h): r={corr_cb_next:.4f}")
    
    merged['crowd_error'] = ((merged['crowd_balance'] > 0.3) & (merged['pct_change'] < 0)) | \
                            ((merged['crowd_balance'] < -0.3) & (merged['pct_change'] > 0))
    error_rate = merged['crowd_error'].mean() * 100
    results['crowd_error_pct'] = error_rate
    print(f"  Crowd error rate: {error_rate:.1f}%")
    
    merged['month'] = merged['date'].dt.to_period('M')
    monthly_error = merged.groupby('month')['crowd_error'].mean() * 100
    
    # ─── 5. Country-specific events (base + quote) ───
    country_events = pd.read_sql("""
        SELECT event_time, country_code, name, importance
        FROM economic_calendar
        WHERE country_code IN %s
          AND event_time >= %s AND event_time <= %s
          AND importance >= 2
        ORDER BY event_time
    """, conn, params=((base_ctry, quote_ctry), start, end))
    country_events['event_time'] = pd.to_datetime(country_events['event_time'])
    country_events['date'] = country_events['event_time'].dt.date
    country_event_days = set(country_events['date'])
    merged['has_country_event'] = merged['date'].dt.date.isin(country_event_days)
    results['country_event_days'] = len(country_event_days)
    results['country_events_total'] = len(country_events)
    
    if merged['has_country_event'].sum() > 5:
        ctry_err = merged[merged['has_country_event']]['crowd_error'].mean() * 100
        no_ctry_err = merged[~merged['has_country_event']]['crowd_error'].mean() * 100
        results['country_event_error'] = ctry_err
        results['no_country_event_error'] = no_ctry_err
        print(f"  Error ON {base_ctry}/{quote_ctry} events: {ctry_err:.1f}%")
        print(f"  Error OFF {base_ctry}/{quote_ctry} events: {no_ctry_err:.1f}%")
    
    # ─── 6. US macro events ───
    us_macro = pd.read_sql("""
        SELECT event_time, event_code, country_code, name, importance
        FROM economic_calendar WHERE country_code = 'US'
          AND event_time >= %s AND event_time <= %s
          AND event_code = ANY(%s) AND importance >= 3
        ORDER BY event_time
    """, conn, params=(start, end, US_KEY_CODES))
    
    jobless = pd.read_sql("""
        SELECT event_time, event_code, country_code, name, importance
        FROM economic_calendar WHERE country_code = 'US'
          AND event_time >= %s AND event_time <= %s
          AND event_code = 'initial-jobless-claims' AND importance >= 2
        ORDER BY event_time
    """, conn, params=(start, end))
    
    us_macro = pd.concat([us_macro, jobless], ignore_index=True)
    us_macro['event_time'] = pd.to_datetime(us_macro['event_time'])
    us_macro['date'] = us_macro['event_time'].dt.date
    us_macro['category'] = us_macro['event_code'].apply(classify_us_event)
    us_macro_days = set(us_macro['date'])
    merged['has_us_event'] = merged['date'].dt.date.isin(us_macro_days)
    results['us_event_days'] = len(us_macro_days)
    results['us_events_total'] = len(us_macro)
    
    if merged['has_us_event'].sum() > 5:
        us_err = merged[merged['has_us_event']]['crowd_error'].mean() * 100
        no_event_all = merged[~merged['has_us_event'] & ~merged['has_country_event']]
        pure_err = no_event_all['crowd_error'].mean() * 100 if len(no_event_all) > 5 else 0
        results['us_event_error'] = us_err
        results['pure_no_event_error'] = pure_err
        print(f"  Error ON US macro: {us_err:.1f}%")
        if pure_err > 0:
            print(f"  Error OFF all events: {pure_err:.1f}%")
    
    # Error by category
    cat_errors = {}
    for cat in ['Employment', 'Inflation', 'FOMC', 'GDP', 'ISM', 'Consumption', 'Regional Fed']:
        days_cat = merged['date'].dt.date.isin(
            us_macro[us_macro['category'] == cat]['date'].unique()
        )
        if days_cat.sum() >= 3:
            cat_errors[cat] = merged[days_cat]['crowd_error'].mean() * 100
            print(f"    {cat:15s}: {cat_errors[cat]:.1f}%")
    results['cat_errors'] = cat_errors
    
    # ─── 7. COT for both currencies ───
    cot_data = {}
    for ccy_name, ccy_code in [(base_ccy, base_ctry), (quote_ccy, quote_ctry)]:
        cot_event_code = CURRENCY_COT_MAP.get(ccy_name)
        if not cot_event_code or ccy_name == 'usd':
            continue
        cot_df = pd.read_sql(f"""
            SELECT event_time, actual_value FROM economic_calendar
            WHERE event_code = '{cot_event_code}'
              AND actual_value IS NOT NULL AND event_time >= '2024-06-01'
            ORDER BY event_time
        """, conn)
        if len(cot_df) > 15:
            cot_df['event_time'] = pd.to_datetime(cot_df['event_time'])
            vals = cot_df['actual_value'].astype(float).values
            z = np.full(len(vals), np.nan)
            for i in range(52, len(vals)):
                w = vals[i-52:i]
                m, s = w.mean(), w.std()
                z[i] = (vals[i] - m) / s if s > 0 else 0
            cot_df['z_52w'] = z
            cot_data[ccy_name.upper()] = cot_df
            print(f"  COT {ccy_name.upper()}: {len(cot_df)} weeks")
    results['cot'] = cot_data
    
    # ─── Package data for plotting ───
    results['_price_df'] = price_df
    results['_daily'] = daily
    results['_merged'] = merged
    results['_monthly_error'] = monthly_error
    results['_country_events'] = country_events
    results['_us_macro'] = us_macro
    results['_cat_errors'] = cat_errors
    results['_base_ctry'] = base_ctry
    results['_quote_ctry'] = quote_ctry
    results['_error_rate'] = error_rate
    
    return results


def build_dashboard(r: dict, output_path: str):
    """Build Plotly dashboard from analysis results dict."""
    sym = r['symbol']
    price_df = r['_price_df']
    merged = r['_merged']
    daily = r['_daily']
    monthly_error = r['_monthly_error']
    country_events = r['_country_events']
    us_macro = r['_us_macro']
    cat_errors = r['_cat_errors']
    base_ctry = r['_base_ctry']
    quote_ctry = r['_quote_ctry']
    cot_data = r.get('cot', {})
    error_rate = r['_error_rate']
    
    # Stats for title
    us_err = r.get('us_event_error', 0)
    ctry_err = r.get('country_event_error', 0)
    pure_err = r.get('pure_no_event_error', 0)
    
    # Determine COT currencies for subplot titles
    cot_keys = list(cot_data.keys())
    cot1_name = cot_keys[0] if len(cot_keys) > 0 else '—'
    cot2_name = cot_keys[1] if len(cot_keys) > 1 else '—'
    
    # Determine row heights based on available COT data
    n_cot = len(cot_data)
    if n_cot == 0:
        row_heights = [160, 240, 170, 160]
        n_rows = 4
    elif n_cot == 1:
        row_heights = [140, 220, 160, 150, 150]
        n_rows = 5
    else:
        row_heights = [140, 220, 160, 150, 150]
        n_rows = 5
    
    subplot_titles = [
        f'Price + US events', f'Price + {base_ctry}/{quote_ctry} events',
        f'Crowd Balance (daily) — ошибки красным',
        f'Crowd Balance → +24h return',
        f'Ошибки толпы по US макро',
        f'Long / Short volume ratio',
    ]
    if n_cot >= 1:
        subplot_titles.append(f'COT {cot1_name} z-score (52w)')
    if n_cot >= 2:
        subplot_titles.append(f'COT {cot2_name} z-score (52w)')
    
    fig = make_subplots(
        rows=n_rows, cols=2,
        row_heights=row_heights,
        column_widths=[0.5, 0.5],
        subplot_titles=subplot_titles,
        vertical_spacing=0.05,
        horizontal_spacing=0.05
    )
    
    # ─── Row 1 Col 1: Price + US events ───
    fig.add_trace(go.Scatter(
        x=price_df.index, y=price_df['price'],
        mode='lines', name='Price', line=dict(color='royalblue', width=1.0),
        hovertemplate='%{x}<br>$%{y:.4f}<extra></extra>'
    ), row=1, col=1)
    
    us_event_legend = set()
    for _, ev in us_macro.iterrows():
        cat = ev['category']
        clr = US_EVENT_COLORS.get(cat, '#8b949e')
        lbl = cat if cat not in us_event_legend else None
        us_event_legend.add(cat)
        fig.add_trace(go.Scatter(
            x=[ev['event_time']], y=[None],
            mode='markers',
            marker=dict(symbol='triangle-down', size=10, color=clr,
                        line=dict(width=0.5, color='white')),
            name=lbl, showlegend=lbl is not None,
            legendgroup=cat,
            hovertemplate=f'{cat}<br>{ev["name"]}<br>%{{x}}<extra></extra>'
        ), row=1, col=1)
    
    # ─── Row 1 Col 2: Price + country events ───
    fig.add_trace(go.Scatter(
        x=price_df.index, y=price_df['price'],
        mode='lines', name='Price', line=dict(color='royalblue', width=1.0),
        showlegend=False,
        hovertemplate='%{x}<br>$%{y:.4f}<extra></extra>'
    ), row=1, col=2)
    
    ctry_legend = {}
    for _, ev in country_events.iterrows():
        cc = ev['country_code']
        lbl = cc if cc not in ctry_legend else None
        ctry_legend[cc] = True
        clr = '#f85149' if cc == base_ctry else '#d29922'
        fig.add_trace(go.Scatter(
            x=[ev['event_time']], y=[None],
            mode='markers',
            marker=dict(symbol='triangle-down', size=10, color=clr,
                        line=dict(width=0.5, color='white')),
            name=lbl, showlegend=lbl is not None,
            legendgroup=cc,
            hovertemplate=f'{cc}<br>{ev["name"]}<br>%{{x}}<extra></extra>'
        ), row=1, col=2)
    
    # ─── Row 2 Col 1: Crowd Balance ───
    bar_colors = []
    for _, row_data in merged.iterrows():
        if row_data['crowd_error']:
            bar_colors.append('#f85149')
        elif row_data['crowd_balance'] >= 0:
            bar_colors.append('#3fb950')
        else:
            bar_colors.append('#58a6ff')
    
    fig.add_trace(go.Bar(
        x=merged['date'], y=merged['crowd_balance'],
        marker_color=bar_colors, name='Crowd Balance',
        hovertemplate='%{x}<br>CB=%{y:.3f}<extra></extra>'
    ), row=2, col=1)
    fig.add_hline(y=0, line_dash='solid', line_color='gray', line_width=0.5, row=2, col=1)
    fig.add_hline(y=0.3, line_dash='dot', line_color='orange', line_width=0.8, row=2, col=1)
    fig.add_hline(y=-0.3, line_dash='dot', line_color='orange', line_width=0.8, row=2, col=1)
    
    # ─── Row 2 Col 2: Scatter CB → +24h ───
    merged['has_any_event'] = merged.get('has_us_event', False) | merged.get('has_country_event', False)
    
    def get_event_color(row):
        if row.get('has_us_event', False) and row.get('has_country_event', False):
            return 'both'
        if row.get('has_us_event', False):
            return 'us'
        if row.get('has_country_event', False):
            return 'country'
        return 'none'
    
    scatter_data = merged.copy()
    scatter_data['event_group'] = scatter_data.apply(get_event_color, axis=1)
    
    for eg, label, clr in [
        ('none', 'No events', 'royalblue'),
        ('country', f'{base_ctry}/{quote_ctry}', '#d29922'),
        ('us', 'US event', '#f85149'),
        ('both', 'Both', '#da3633'),
    ]:
        subset = scatter_data[scatter_data['event_group'] == eg]
        if len(subset) < 2:
            continue
        fig.add_trace(go.Scattergl(
            x=subset['crowd_balance'], y=subset['pct_change'],
            mode='markers', marker=dict(size=4, color=clr, opacity=0.4),
            name=label,
            hovertemplate='CB=%{x:.3f}<br>Δ=%{y:.2f}%<br>' + label + '<extra></extra>'
        ), row=2, col=2)
    
    corr_val = r.get('corr_cb_next', 0)
    m, b = np.polyfit(merged['crowd_balance'].values, merged['pct_change'].values, 1)
    xr = np.array([merged['crowd_balance'].min(), merged['crowd_balance'].max()])
    fig.add_trace(go.Scatter(
        x=xr, y=m*xr+b,
        mode='lines', name=f'Regr (r={corr_val:.3f})',
        line=dict(color='orange', width=1.5, dash='dash')
    ), row=2, col=2)
    
    # ─── Error by US category ───
    if cat_errors:
        ordered = sorted(cat_errors.items(), key=lambda x: x[1], reverse=True)
        fig.add_trace(go.Bar(
            x=[c[0] for c in ordered], y=[c[1] for c in ordered],
            marker_color=[US_EVENT_COLORS.get(c[0], '#8b949e') for c in ordered],
            name='Error by category',
            hovertemplate='%{x}<br>%{y:.1f}%<extra></extra>'
        ), row=3, col=1)
        fig.add_hline(y=error_rate, line_dash='dash', line_color='gray', line_width=0.8, row=3, col=1)
    
    # ─── Row 3 Col 2: L/S ratio ───
    daily['ratio'] = daily['long_vol'] / daily['short_vol'].replace(0, 1)
    fig.add_trace(go.Scatter(
        x=daily['date'], y=daily['ratio'],
        mode='lines', name='L/S ratio', line=dict(color='#3fb950', width=1),
        hovertemplate='%{x}<br>L/S=%{y:.2f}<extra></extra>'
    ), row=3, col=2)
    fig.add_hline(y=1, line_dash='solid', line_color='gray', line_width=0.5, row=3, col=2)
    
    # ─── COT graphs ───
    for idx, (ccy_name, cot_df) in enumerate(cot_data.items()):
        row = 4 + idx
        col = 1
        if len(cot_df) > 20:
            cot_plot = cot_df.dropna(subset=['z_52w'])
            cot_colors = ['#3fb950' if abs(v) < 1.5 else ('#d29922' if abs(v) < 2.0 else '#f85149')
                          for v in cot_plot['z_52w']]
            fig.add_trace(go.Bar(
                x=cot_plot['event_time'], y=cot_plot['z_52w'],
                marker_color=cot_colors, name=f'COT {ccy_name}',
                hovertemplate='%{x}<br>z=%{y:.2f}<extra></extra>'
            ), row=row, col=col)
            fig.add_hline(y=1.5, line_dash='dot', line_color='orange', line_width=0.8, row=row, col=col)
            fig.add_hline(y=-1.5, line_dash='dot', line_color='orange', line_width=0.8, row=row, col=col)
            fig.add_hline(y=0, line_dash='solid', line_color='gray', line_width=0.5, row=row, col=col)
    
    # ─── Title ───
    title_parts = [f'{sym} | Bars: {r["bars"]} | Days: {r["days"]}']
    if pure_err > 0:
        title_parts.append(f'🟢 None: {pure_err:.0f}%')
    if ctry_err > 0:
        title_parts.append(f'🟠 {base_ctry}/{quote_ctry}: {ctry_err:.0f}%')
    if us_err > 0:
        title_parts.append(f'🔴 US: {us_err:.0f}%')
    title_parts.append(f'📊 Avg: {error_rate:.0f}%')
    
    title = f'{" | ".join(title_parts)}<br>' \
            f'<span style="font-size:11px">Corr(CB→+24h): r={corr_val:.4f} | Period: {price_df.index[0].date()} → {price_df.index[-1].date()} | Events: US={r["us_events_total"]}, {base_ctry}/{quote_ctry}={r["country_events_total"]}</span>'
    
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
    
    for i in range(1, n_rows + 1):
        for j in range(1, 3):
            fig.update_xaxes(gridcolor='#333333', showgrid=True, row=i, col=j)
            fig.update_yaxes(gridcolor='#333333', showgrid=True, row=i, col=j)
    
    # Fix datetime serialization for Plotly
    for idx in range(len(fig.data)):
        try:
            if hasattr(fig.data[idx].x, '__len__') and len(fig.data[idx].x) > 0:
                first = fig.data[idx].x[0]
                if hasattr(first, 'isoformat'):
                    fig.data[idx].x = [d.isoformat() if hasattr(d, 'isoformat') else d for d in fig.data[idx].x]
        except:
            pass
    
    fig.write_html(output_path, include_plotlyjs='cdn', full_html=False)
    print(f"  Dashboard: {output_path}")


def main():
    parser = argparse.ArgumentParser(description='Universal Pair Dashboard')
    parser.add_argument('--sym', required=True, help='Symbol: audjpy, eurusd, ...')
    parser.add_argument('--start', default='2025-01-01', help='Start date (YYYY-MM-DD)')
    parser.add_argument('--end', default='2026-05-31', help='End date (YYYY-MM-DD)')
    parser.add_argument('--output', help=f'Output file (default: {OUTPUT_DIR}{{sym}}_dashboard.html)')
    args = parser.parse_args()
    
    sym = args.sym.lower()
    if not args.output:
        args.output = os.path.join(OUTPUT_DIR, f'{sym}_dashboard.html')
    
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    print(f"═══════════════════════════════════════")
    print(f"  {args.sym.upper()} Dashboard")
    print(f"  {args.start} → {args.end}")
    print(f"═══════════════════════════════════════")
    
    conn = psycopg2.connect(host=DB_HOST, dbname=DB_NAME, user=DB_USER, password=DB_PASS)
    try:
        r = analyze_pair(sym, args.start, args.end, conn)
        build_dashboard(r, args.output)
        print(f"\n✅ {sym.upper()} dashboard: {args.output}")
    finally:
        conn.close()


if __name__ == '__main__':
    main()
