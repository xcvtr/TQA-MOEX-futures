#!/home/user/venvs/tqa/main/bin/python
"""
Cluster Dashboard v1 — кластеры толпы с точками входа/выхода.
Для любой пары показывает:
  - Уровни скопления позиций толпы (кластеры) на шкале цены
  - Динамику кластеров по времени (растут / исчезают)
  - Точки входа (cluster support) и выхода (cluster breakout)

Usage:
  python scripts/cluster_dashboard.py --sym audjpy
  python scripts/cluster_dashboard.py --sym eurjpy --start 2025-06-01 --end 2025-08-01
  python scripts/cluster_dashboard.py --sym audjpy --all  # весь доступный период
"""
import argparse, sys, json, warnings, os, re
from datetime import datetime, timedelta, date
from collections import defaultdict
import psycopg2
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
warnings.filterwarnings('ignore')

DB_HOST = '10.0.0.60'
DB_NAME = 'forex'
DB_USER = 'postgres'
DB_PASS = 'postgres'
OUTPUT_DIR = '/home/user/.hermes/cache/screenshots/tqa/'

# ─── Symbol → (base, quote) ───
SYMBOL_MAP = {
    'audjpy': ('AUD', 'JPY'), 'audusd': ('AUD', 'USD'),
    'euraud': ('EUR', 'AUD'), 'eurgbp': ('EUR', 'GBP'),
    'eurjpy': ('EUR', 'JPY'), 'eurusd': ('EUR', 'USD'),
    'gbpjpy': ('GBP', 'JPY'), 'gbpusd': ('GBP', 'USD'),
    'nzdusd': ('NZD', 'USD'), 'usdcad': ('USD', 'CAD'),
    'usdchf': ('USD', 'CHF'), 'usdjpy': ('USD', 'JPY'),
    'xauusd': ('XAU', 'USD'),
}


def load_data(sym: str, start: str, end: str, conn):
    """Load price + DOM data for analysis."""
    # Price
    price_df = pd.read_sql(
        f"SELECT time, price FROM {sym}_data WHERE time >= '{start}' AND time <= '{end}' AND price > 0 ORDER BY time", conn)
    price_df['time'] = pd.to_datetime(price_df['time'])
    if price_df['time'].dt.tz is not None:
        price_df['time'] = price_df['time'].dt.tz_localize(None)
    price_df = price_df.set_index('time')
    price_df = price_df[~price_df.index.duplicated(keep='first')]
    # Filter outliers: remove bars where price deviates >1.5% from 36h rolling median
    # Using rolling('36h') adapts to market regime (unlike fixed window)
    prices = price_df[['price']].copy()
    for _ in range(20):
        prices['med'] = prices['price'].rolling('36h', min_periods=10).median()
        prices['dev'] = abs(prices['price'] - prices['med']) / prices['med'] * 100
        n_bad = (prices['dev'] > 1.5).sum()
        if n_bad == 0:
            break
        prices = prices[prices['dev'] <= 1.5].copy()
    print(f"  Price outliers removed: {n_bad}")
    price_df = prices[['price']]

    # DOM (positions = crowd) — берём каждый 12-й срез (≈каждые 4 часа вместо 20 мин)
    dom_df = pd.read_sql(f"""
        SELECT time, price, positions FROM {sym}_dom
        WHERE time >= '{start}' AND positions IS NOT NULL ORDER BY time
    """, conn)
    dom_df['time'] = pd.to_datetime(dom_df['time'])
    if dom_df['time'].dt.tz is not None:
        dom_df['time'] = dom_df['time'].dt.tz_localize(None)
    
    # Sample: берём каждый 12-й временной срез (~4 часа)
    times_unique = sorted(dom_df['time'].unique())
    step = max(1, len(times_unique) // 500)  # макс 500 срезов
    sampled_times = set(times_unique[::step])
    dom_df = dom_df[dom_df['time'].isin(sampled_times)]

    print(f"  Price: {len(price_df)} bars | DOM: {len(dom_df)} rows (sampled {step}x)")
    return price_df, dom_df


def find_clusters(dom_df: pd.DataFrame, min_volume: float = 0.5, price_round: int = 0) -> list[dict]:
    """
    Найти кластеры толпы.
    Группирует позиции по округлённой цене, ищёт уровни с аномальным объёмом.
    
    price_round: 0 = группировка по целой цене, 1 = по десятым, 2 = по сотым
    min_volume: минимальный объём для кластера (лоты)
    """
    if len(dom_df) == 0:
        return []

    df = dom_df.copy()
    df['price_rounded'] = df['price'].round(price_round)
    df['abs_pos'] = df['positions'].abs()

    # Группировка по времени + уровень цены
    df['time_bucket'] = df['time'].dt.floor('6h')  # каждые 6 часов срез

    clusters = []
    for (tbucket, pr), grp in df.groupby(['time_bucket', 'price_rounded']):
        total_vol = grp['abs_pos'].sum()
        if total_vol < min_volume:
            continue
        long_vol = grp[grp['positions'] > 0]['abs_pos'].sum()
        short_vol = grp[grp['positions'] < 0]['abs_pos'].sum()
        cluster_type = 'long' if long_vol > short_vol else 'short'

        clusters.append({
            'time': tbucket,
            'price': pr,
            'volume': total_vol,
            'long_vol': long_vol,
            'short_vol': short_vol,
            'type': cluster_type,
            'n_levels': len(grp),
        })

    # Объединяем соседние уровни в один кластер
    merged = merge_clusters(clusters)
    return merged


def merge_clusters(clusters: list[dict], max_price_gap: float = 0.01) -> list[dict]:
    """Объединить соседние уровни в один кластер по времени."""
    if not clusters:
        return []

    df = pd.DataFrame(clusters)
    if len(df) < 2:
        return clusters

    # Группируем: если разница по цене < max_price_gap и то же время — merge
    df = df.sort_values(['time', 'price'])
    merged = []
    current = None

    for _, row in df.iterrows():
        if current is None:
            current = dict(row)
            continue
        
        time_diff = abs((row['time'] - current['time']).total_seconds())
        price_diff = abs(row['price'] - current['price'])

        if time_diff < 3600 and price_diff < max_price_gap * 5:
            # Тот же кластер — мержим
            total_vol = current['volume'] + row['volume']
            current['volume'] = total_vol
            current['long_vol'] += row['long_vol']
            current['short_vol'] += row['short_vol']
            current['type'] = 'long' if current['long_vol'] > current['short_vol'] else 'short'
            current['n_levels'] += row['n_levels']
            # Price как средневзвешенный
            current['price'] = (current['price'] * current['volume'] + row['price'] * row['volume']) / total_vol
            current['price'] = round(current['price'], 2)
        else:
            merged.append(current)
            current = dict(row)

    if current:
        merged.append(current)

    return merged


def find_entry_exit(clusters: list[dict], price_df: pd.DataFrame) -> list[dict]:
    """
    Определить точки входа/выхода по кластерам.
    Векторизовано: ищет касания цены к кластерным уровням на всём графике.
    """
    signals = []
    if not clusters or len(price_df) < 20:
        return signals

    prices = price_df['price'].values
    times = price_df.index.values
    price_range = prices.max() - prices.min()
    
    # Для каждого кластера создаём маску: где цена касается уровня
    for c in clusters:
        cluster_price = c['price']
        
        # Расстояние каждого бара до уровня
        dist = np.abs(prices - cluster_price)
        near_mask = dist < cluster_price * 0.005  # ближе 0.5%
        
        touch_idxs = np.where(near_mask)[0]
        
        for idx in touch_idxs:
            if idx < 5 or idx >= len(prices) - 5:
                continue
            
            # Движение после касания
            pa = prices[idx+1:idx+4]
            if len(pa) < 2:
                continue
            move = pa[-1] - prices[idx]
            
            if abs(move) < price_range * 0.002:
                continue
            
            if c['type'] == 'long':
                if prices[idx] >= cluster_price and move > 0:
                    signals.append({
                        'time': times[idx], 'price': cluster_price,
                        'type': 'entry_long', 'cluster_vol': c['volume'],
                        'strength': min(100, abs(move) * 1000),
                    })
                elif prices[idx] < cluster_price and move < 0:
                    signals.append({
                        'time': times[idx], 'price': cluster_price,
                        'type': 'exit_long', 'cluster_vol': c['volume'],
                        'strength': min(100, abs(move) * 1000),
                    })
            else:
                if prices[idx] <= cluster_price and move < 0:
                    signals.append({
                        'time': times[idx], 'price': cluster_price,
                        'type': 'entry_short', 'cluster_vol': c['volume'],
                        'strength': min(100, abs(move) * 1000),
                    })
                elif prices[idx] > cluster_price and move > 0:
                    signals.append({
                        'time': times[idx], 'price': cluster_price,
                        'type': 'exit_short', 'cluster_vol': c['volume'],
                        'strength': min(100, abs(move) * 1000),
                    })

    signals.sort(key=lambda x: x['strength'], reverse=True)
    seen = set()
    deduped = []
    for s in signals:
        key = (round(s['price'], 1), s['type'])
        if key not in seen:
            seen.add(key)
            deduped.append(s)
    
    return deduped[:30]


def build_cluster_dashboard(sym: str, price_df, dom_df, clusters, signals, output_path: str):
    """Build interactive cluster dashboard."""
    sym_upper = sym.upper()
    has_signals = len(signals) > 0

    # Prepare data
    price_df_plot = price_df.copy()
    
    # Cluster volume by price level (for heatmap overlay)
    cluster_df = pd.DataFrame(clusters) if clusters else pd.DataFrame()
    
    # Build figure
    fig = make_subplots(
        rows=3, cols=2,
        row_heights=[300, 200, 200],
        column_widths=[0.6, 0.4],
        subplot_titles=(
            'Price + Cluster Heatmap',
            'Cluster Volume by Price Level',
            'Entry / Exit Signals',
            'Cluster Dynamics (volume over time)',
            'Crowd Balance (daily)',
            'Long/Short Cluster Ratio'
        ),
        vertical_spacing=0.06,
        horizontal_spacing=0.05,
    )

    # ── Row 1 Col 1: Price + clusters as background ──
    fig.add_trace(go.Scatter(
        x=price_df_plot.index, y=price_df_plot['price'],
        mode='lines', name='Price',
        line=dict(color='royalblue', width=1.2),
        hovertemplate='%{x}<br>$%{y:.4f}<extra></extra>'
    ), row=1, col=1)

    # Add cluster zones as colored shapes
    if not cluster_df.empty:
        # Unique times for cluster bands
        for _, cl in cluster_df.iterrows():
            if pd.isna(cl['price']) or pd.isna(cl['time']):
                continue
            t = cl['time']
            p = cl['price']
            vol_norm = min(1.0, cl['volume'] / 10.0)
            
            color = '#3fb950' if cl['type'] == 'long' else '#f85149'
            opacity = max(0.1, vol_norm * 0.4)
            
            fig.add_trace(go.Scatter(
                x=[t], y=[p],
                mode='markers',
                marker=dict(
                    symbol='square', size=vol_norm * 15 + 5,
                    color=color, opacity=opacity,
                    line=dict(width=0.5, color=color)
                ),
                name=f"Cluster {cl['type']}",
                showlegend=False,
                legendgroup='clusters',
                hovertemplate=f"{cl['type']} vol={cl['volume']:.1f} lots<br>%{{x}}<br>@%{{y:.4f}}<extra></extra>"
            ), row=1, col=1)

    # Entry/exit arrows
    if has_signals:
        for s in signals:
            t = s['time']
            p = s['price']
            strength = min(30, s['strength'])
            
            if s['type'] == 'entry_long':
                color, symbol = '#3fb950', 'triangle-up'
                label = '→ LONG'
            elif s['type'] == 'exit_long':
                color, symbol = '#f85149', 'triangle-down'
                label = '→ EXIT'
            elif s['type'] == 'entry_short':
                color, symbol = '#f85149', 'triangle-down'
                label = '→ SHORT'
            else:
                color, symbol = '#3fb950', 'triangle-up'
                label = '→ COVER'

            fig.add_trace(go.Scatter(
                x=[t], y=[p],
                mode='markers+text',
                marker=dict(symbol=symbol, size=strength, color=color,
                            line=dict(width=1, color='white')),
                text=label,
                textposition='top center',
                textfont=dict(size=9, color=color),
                name=label,
                showlegend=False,
                hovertemplate=f'{label}<br>%{{x}}<br>@%{{y:.4f}}<extra></extra>'
            ), row=1, col=1)

    # ── Row 1 Col 2: Cluster volume by price level (bar chart) ──
    if not cluster_df.empty:
        # Aggregate volume by price
        price_vol = cluster_df.groupby('price').agg({
            'volume': 'sum', 'long_vol': 'sum', 'short_vol': 'sum',
            'type': lambda x: 'long' if x.value_counts().index[0] == 'long' else 'short'
        }).reset_index().sort_values('price')

        colors = ['#3fb950' if t == 'long' else '#f85149' for t in price_vol['type']]
        fig.add_trace(go.Bar(
            x=price_vol['volume'], y=price_vol['price'],
            orientation='h',
            marker_color=colors,
            name='Volume',
            hovertemplate='%{y:.4f}<br>vol=%{x:.1f}<extra></extra>'
        ), row=1, col=2)

    # ── Row 2 Col 1: Entry/Exit signals timeline ──
    if has_signals:
        sig_types = {'entry_long': ('Entry Long', '#3fb950', 1),
                     'exit_long': ('Exit Long', '#f85149', 0.5),
                     'entry_short': ('Entry Short', '#f85149', -1),
                     'exit_short': ('Exit Short', '#3fb950', -0.5)}
        
        for stype, (label, color, val) in sig_types.items():
            sigs = [s for s in signals if s['type'] == stype]
            if sigs:
                fig.add_trace(go.Scatter(
                    x=[s['time'] for s in sigs],
                    y=[val * s['strength'] / 20 for s in sigs],
                    mode='markers',
                    marker=dict(symbol='triangle-up' if val > 0 else 'triangle-down',
                                size=[min(20, s['strength']) for s in sigs],
                                color=color),
                    name=label,
                    hovertemplate=f'{label}<br>%{{x}}<br>str=%{{marker.size:.0f}}<extra></extra>'
                ), row=2, col=1)
        
        fig.add_hline(y=0, line_dash='solid', line_color='gray', line_width=0.5, row=2, col=1)

    # ── Row 2 Col 2: Cluster volume over time ──
    if not cluster_df.empty:
        vol_time = cluster_df.groupby('time').agg({
            'volume': 'sum', 'long_vol': 'sum', 'short_vol': 'sum'
        }).reset_index().sort_values('time')
        
        fig.add_trace(go.Scatter(
            x=vol_time['time'], y=vol_time['long_vol'],
            mode='lines', name='Long vol',
            line=dict(color='#3fb950', width=1),
            hovertemplate='%{x}<br>Long=%{y:.1f}<extra></extra>'
        ), row=2, col=2)
        fig.add_trace(go.Scatter(
            x=vol_time['time'], y=vol_time['short_vol'],
            mode='lines', name='Short vol',
            line=dict(color='#f85149', width=1),
            hovertemplate='%{x}<br>Short=%{y:.1f}<extra></extra>'
        ), row=2, col=2)

    # ── Row 3 Col 1: Crowd Balance (daily) ──
    dom_df = dom_df.copy()
    dom_df['date'] = dom_df['time'].dt.date
    daily_cb = dom_df.groupby('date').apply(lambda g: pd.Series({
        'long_vol': float(np.sum(g['positions'][g['positions'] > 0])) if np.any(g['positions'] > 0) else 0,
        'short_vol': float(abs(np.sum(g['positions'][g['positions'] < 0]))) if np.any(g['positions'] < 0) else 0,
    })).reset_index()
    daily_cb['balance'] = (daily_cb['long_vol'] / (daily_cb['long_vol'] + daily_cb['short_vol']).replace(0, 1)) * 2 - 1
    daily_cb['date'] = pd.to_datetime(daily_cb['date'])
    daily_cb = daily_cb.sort_values('date')

    bar_colors = ['#3fb950' if v >= 0 else '#f85149' for v in daily_cb['balance']]
    fig.add_trace(go.Bar(
        x=daily_cb['date'], y=daily_cb['balance'],
        marker_color=bar_colors, name='Crowd Balance',
        hovertemplate='%{x}<br>CB=%{y:.3f}<extra></extra>'
    ), row=3, col=1)
    fig.add_hline(y=0, line_dash='solid', line_color='gray', line_width=0.5, row=3, col=1)

    # ── Row 3 Col 2: Long/Short cluster ratio ──
    if not cluster_df.empty:
        lr = cluster_df.groupby(['time', 'type']).size().unstack(fill_value=0)
        if 'long' in lr.columns and 'short' in lr.columns:
            lr['ratio'] = lr['long'] / lr['short'].replace(0, 1)
            fig.add_trace(go.Scatter(
                x=lr.index, y=lr['ratio'],
                mode='lines+markers', name='L/S cluster ratio',
                line=dict(color='#58a6ff', width=1),
                marker=dict(size=4),
                hovertemplate='%{x}<br>L/S=%{y:.2f}<extra></extra>'
            ), row=3, col=2)
            fig.add_hline(y=1, line_dash='solid', line_color='gray', line_width=0.5, row=3, col=2)

    # ── Layout ──
    title = f'{sym_upper} Cluster Analysis'
    if clusters:
        long_vol = sum(c['long_vol'] for c in clusters)
        short_vol = sum(c['short_vol'] for c in clusters)
        n_clusters = len(clusters)
        n_signals = len(signals)
        title += f' | {n_clusters} clusters | {n_signals} signals | L:{long_vol:.0f} S:{short_vol:.0f}'

    fig.update_layout(
        height=1000,
        title_text=title,
        title_font_size=14,
        hovermode='x unified',
        margin=dict(l=50, r=30, t=60, b=20),
        plot_bgcolor='#111111', paper_bgcolor='#1a1a2e',
        font=dict(color='#e0e0e0', size=10),
        legend=dict(orientation='h', yanchor='bottom', y=1.0, xanchor='right', x=1,
                    font=dict(size=8)),
        bargap=0.1,
    )

    for i in range(1, 4):
        for j in range(1, 3):
            fig.update_xaxes(gridcolor='#333333', showgrid=True, row=i, col=j)
            fig.update_yaxes(gridcolor='#333333', showgrid=True, row=i, col=j)

    # Fix datetime
    for idx in range(len(fig.data)):
        try:
            if hasattr(fig.data[idx].x, '__len__') and len(fig.data[idx].x) > 0:
                first = fig.data[idx].x[0]
                if hasattr(first, 'isoformat'):
                    fig.data[idx].x = [d.isoformat() if hasattr(d, 'isoformat') else d for d in fig.data[idx].x]
        except:
            pass

    fig.write_html(output_path, include_plotlyjs='cdn', full_html=False)
    return fig


def main():
    parser = argparse.ArgumentParser(description='Cluster Dashboard — crowd clusters + entry/exit')
    parser.add_argument('--sym', required=True, help='Symbol: audjpy, eurusd, ...')
    parser.add_argument('--start', default='2025-01-01', help='Start date')
    parser.add_argument('--end', default='2026-05-31', help='End date')
    parser.add_argument('--all', action='store_true', help='Use full available range')
    args = parser.parse_args()

    sym = args.sym.lower()
    if sym not in SYMBOL_MAP:
        print(f"Unknown symbol: {sym}")
        sys.exit(1)
    
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    output = os.path.join(OUTPUT_DIR, f'{sym}_cluster_dashboard.html')

    conn = psycopg2.connect(host=DB_HOST, dbname=DB_NAME, user=DB_USER, password=DB_PASS)
    try:
        if args.all:
            cur = conn.cursor()
            cur.execute(f"SELECT min(time), max(time) FROM {sym}_data")
            row = cur.fetchone()
            if row and row[0]:
                args.start = row[0].strftime('%Y-%m-%d')
                args.end = row[1].strftime('%Y-%m-%d')

        print(f"Cluster Dashboard — {sym.upper()}  {args.start} → {args.end}")
        
        price_df, dom_df = load_data(sym, args.start, args.end, conn)
        
        # Check DOM data density
        total_hours = (price_df.index[-1] - price_df.index[0]).total_seconds() / 3600
        dom_hours = len(dom_df) * 0.33  # each DOM row ≈ 20min
        print(f"  DOM density: {dom_hours/total_hours*100:.0f}%")
        
        # Find clusters
        print("  Finding clusters...")
        clusters = find_clusters(dom_df, min_volume=0.3, price_round=1)
        # Round cluster prices for display
        for c in clusters:
            c['price'] = round(c['price'], 2)
        
        print(f"  Clusters found: {len(clusters)}")
        # Оставляем кластеры равномерно по времени — топ-5 за каждую неделю
        cluster_df = pd.DataFrame(clusters)
        cluster_df['week'] = cluster_df['time'].dt.isocalendar().week.astype(int)
        top_per_week = cluster_df.groupby('week').apply(
            lambda g: g.nlargest(min(5, len(g)), 'volume')
        ).reset_index(drop=True)
        clusters = top_per_week.to_dict('records')
        # Round prices
        for c in clusters:
            c['price'] = round(c['price'], 2)
        print(f"    Filtered clusters: {len(clusters)}")
        if clusters:
            long_count = sum(1 for c in clusters if c['type'] == 'long')
            short_count = sum(1 for c in clusters if c['type'] == 'short')
            total_vol = sum(c['volume'] for c in clusters)
            print(f"    Long: {long_count} | Short: {short_count} | Total vol: {total_vol:.1f}")
        
        # Find entry/exit signals
        print("  Finding entry/exit signals...")
        signals = find_entry_exit(clusters, price_df)
        print(f"  Signals: {len(signals)}")
        
        # Build dashboard
        build_cluster_dashboard(sym, price_df, dom_df, clusters, signals, output)
        print(f"\n✅ Cluster dashboard: {output}")
        
    finally:
        conn.close()


if __name__ == '__main__':
    main()
