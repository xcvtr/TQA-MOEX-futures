#!/usr/bin/env python3
"""
MOEX Manipulation Visualizer — график цены + OI физ/юр + входы/выходы.

Usage:
    python3 services/MOEX_LOADER/manipulation_viz.py --symbol Si --days 60
    python3 services/MOEX_LOADER/manipulation_viz.py --symbol BR --days 30 --output chart.png
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import FancyBboxPatch
from datetime import datetime

from services.MOEX_LOADER.manipulation_search import (
    load_price_data, load_oi_data, prepare_data,
    find_swing_points, detect_all, add_forward_returns,
    resolve_symbol, ZSCORE_THRESHOLD, SWING_WINDOW
)

# ── Конфигурация графика ────────────────────────────────────────────
STYLE = {
    'figsize': (20, 14),
    'dpi': 150,
    'colors': {
        'up': '#26a69a', 'down': '#ef5350',
        'fiz': '#1a73e8', 'yur': '#e8710a',
        'grid': '#e0e0e0', 'bg': '#fafafa',
    },
    'markers': {
        'FLOW_DIVERGENCE': {'marker': 'D', 's': 120, 'label': 'Дивергенция потоков'},
        'FLOW_EXTREME':    {'marker': '*', 's': 140, 'label': 'Экстр. поток'},
        'OI_EXTREME':      {'marker': 'P', 's': 120, 'label': 'OI-экстремум'},
        'OI_TRAP':         {'marker': '^', 's': 110, 'label': 'OI-ловушка'},
        'OI_DIVERGENCE':   {'marker': 's', 's': 110, 'label': 'OI-дивергенция'},
        'FALSE_BREAK':     {'marker': 'o', 's': 90,  'label': 'Ложный пробой'},
        'STOP_HUNT':       {'marker': 'X', 's': 120, 'label': 'Стоп-хант'},
        'VOL_CLIMAX':      {'marker': 's', 's': 100, 'label': 'Объёмный климакс'},
    },
    'entry_exit_alpha': 0.25,
    'exit_horizon_bars': 72,  # ~6 часов
}


def load_data(symbol: str, days: int) -> pd.DataFrame:
    """Загрузить цены + OI, подготовить."""
    df_p = load_price_data(symbol, days)
    if df_p.empty:
        print(f"Нет данных для {symbol}")
        sys.exit(1)
    df_oi = load_oi_data(symbol, days)
    df = prepare_data(df_p, df_oi, symbol)
    # Добавить yur_flow_zscore если нет
    if 'yur_flow_zscore' not in df.columns and 'yur_flow' in df.columns:
        flow_mean = df['yur_flow'].rolling(288, min_periods=50).mean()
        flow_std = df['yur_flow'].rolling(288, min_periods=50).std()
        df['yur_flow_zscore'] = ((df['yur_flow'] - flow_mean) / flow_std.replace(0, np.nan)).fillna(0)
    return df


def plot_chart(df: pd.DataFrame, patterns: list, symbol: str, output: str = None):
    """Построить 4-панельный график."""
    colors = STYLE['colors']
    fig, axes = plt.subplots(4, 1, figsize=STYLE['figsize'], dpi=STYLE['dpi'],
                             gridspec_kw={'height_ratios': [3, 1.2, 1.2, 1]},
                             sharex=True)
    fig.patch.set_facecolor(colors['bg'])
    fig.suptitle(f'MOEX Manipulation Scan — {symbol}  ({df["time"].min():%d.%m} — {df["time"].max():%d.%m %Y})',
                 fontsize=16, fontweight='bold', y=0.98)

    times = df['time'].values
    closes = df['close'].values

    # ── Панель 1: Цена ────────────────────────────────────────────
    ax1 = axes[0]
    _plot_price(ax1, df, patterns, colors)
    ax1.set_ylabel(f'{symbol} Цена', fontsize=10)
    ax1.legend(fontsize=8, loc='upper left', ncol=4)
    ax1.grid(True, alpha=0.3)

    # ── Панель 2: OI физлица ──────────────────────────────────────
    ax2 = axes[1]
    if 'fiz_net' in df.columns:
        _plot_oi_panel(ax2, df, 'fiz', colors)
    ax2.set_ylabel('OI Физлица', fontsize=10, color=colors['fiz'])
    ax2.legend(fontsize=8, loc='upper left')
    ax2.grid(True, alpha=0.3)
    ax2.axhline(0, color='gray', linewidth=0.5, linestyle='--')

    # ── Панель 3: OI юрлица ────────────────────────────────────────
    ax3 = axes[2]
    if 'yur_net' in df.columns:
        _plot_oi_panel(ax3, df, 'yur', colors)
    ax3.set_ylabel('OI Юрлица', fontsize=10, color=colors['yur'])
    ax3.legend(fontsize=8, loc='upper left')
    ax3.grid(True, alpha=0.3)
    ax3.axhline(0, color='gray', linewidth=0.5, linestyle='--')

    # ── Панель 4: Flow z-score ─────────────────────────────────────
    ax4 = axes[3]
    _plot_zscore(ax4, df, patterns, colors)
    ax4.set_ylabel('Flow z-score', fontsize=10)
    ax4.legend(fontsize=8, loc='upper left')
    ax4.grid(True, alpha=0.3)
    ax4.axhline(0, color='gray', linewidth=0.5)

    # Формат оси X
    for ax in axes:
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%d.%m\n%H:%M'))
        ax.xaxis.set_major_locator(mdates.AutoDateLocator())
        plt.setp(ax.xaxis.get_majorticklabels(), rotation=0, ha='center', fontsize=7)

    plt.tight_layout(rect=[0, 0, 1, 0.96])
    if output:
        plt.savefig(output, dpi=STYLE['dpi'], bbox_inches='tight')
        print(f"График сохранён: {output}")
    plt.close(fig)


def _plot_price(ax, df, patterns, colors):
    """Свечной график цены + маркеры паттернов + входы/выходы."""
    times = pd.to_datetime(df['time'])
    closes = df['close'].values
    opens = df['open'].values
    highs = df['high'].values
    lows = df['low'].values

    # Свечи (упрощённо: линия close + цветные свечи)
    for i in range(len(df)):
        color = colors['up'] if closes[i] >= opens[i] else colors['down']
        ax.plot([times[i], times[i]], [lows[i], highs[i]], color=color, linewidth=0.5, alpha=0.5)
        ax.plot([times[i], times[i]], [opens[i], closes[i]], color=color, linewidth=3, alpha=0.7)

    # Свинговые уровни
    for col, style, label in [('swing_high', '--', 'Свинг-хай'),
                               ('swing_low', '--', 'Свинг-лой')]:
        if col in df.columns:
            valid = df[col].dropna()
            if not valid.empty:
                ax.scatter(valid.index, valid.values, marker='_' if 'high' in col else '_',
                          color='gray', s=30, alpha=0.4, label=label, zorder=1)

    # Маркеры паттернов
    markers = STYLE['markers']
    for p in patterns:
        if p['type'] not in markers:
            continue
        t = pd.Timestamp(p['time'])
        idx = p.get('swing_idx', 0)
        if idx >= len(df):
            continue
        price = closes[idx]
        direction = p.get('direction', 'NEUTRAL')
        color = colors['up'] if direction == 'BULL' else (colors['down'] if direction == 'BEAR' else 'gray')
        m = markers[p['type']]
        ax.scatter(t, price, marker=m['marker'], s=m['s'],
                  color=color, edgecolors='black', linewidths=0.5,
                  zorder=5, alpha=0.9, label=m['label'] if p == patterns[0] else '')

    # Входы/выходы соединительными линиями
    exit_bars = STYLE['exit_horizon_bars']
    for p in patterns:
        if p['type'] not in ('FLOW_DIVERGENCE', 'OI_EXTREME', 'FLOW_EXTREME', 'OI_TRAP'):
            continue
        idx = p.get('swing_idx', 0)
        exit_idx = idx + exit_bars
        if exit_idx >= len(df):
            continue
        entry_t = pd.Timestamp(p['time'])
        exit_t = pd.Timestamp(df.iloc[exit_idx]['time'])
        entry_price = closes[idx]
        exit_price = closes[exit_idx]

        direction = p.get('direction', 'BEAR')
        color = colors['up'] if direction == 'BULL' else (colors['down'] if direction == 'BEAR' else 'gray')

        ax.plot([entry_t, exit_t], [entry_price, exit_price],
               color=color, linewidth=1.5, linestyle='-',
               alpha=STYLE['entry_exit_alpha'], zorder=2)
        # Точки входа/выхода
        ax.scatter([entry_t, exit_t], [entry_price, exit_price],
                  marker='o', s=30, color=color, alpha=0.5, zorder=3)


def _plot_oi_panel(ax, df, prefix, colors):
    """Панель OI: net линия + buy/sell заливка."""
    times = pd.to_datetime(df['time'])
    color = colors[prefix]
    for col, label, a in [(f'{prefix}_net', f'{prefix.upper()} net', 0.8),
                          (f'{prefix}_buy', f'{prefix.upper()} buy', 0.3),
                          (f'{prefix}_sell', f'{prefix.upper()} sell', 0.3)]:
        if col in df.columns:
            ax.plot(times, df[col], color=color, alpha=a, linewidth=0.6, label=label)
    # Заливка fiz_net > 0 / < 0
    net_col = f'{prefix}_net'
    if net_col in df.columns:
        ax.fill_between(times, 0, df[net_col], where=df[net_col] > 0,
                        color=colors['up'], alpha=0.1)
        ax.fill_between(times, 0, df[net_col], where=df[net_col] < 0,
                        color=colors['down'], alpha=0.1)


def _plot_zscore(ax, df, patterns, colors):
    """Панель flow z-score для fiz и yur."""
    times = pd.to_datetime(df['time'])
    for col, color, label in [('fiz_flow_zscore', colors['fiz'], 'FIZ flow z'),
                              ('yur_flow_zscore', colors['yur'], 'YUR flow z')]:
        if col in df.columns:
            ax.plot(times, df[col], color=color, alpha=0.7, linewidth=0.5, label=label)
    # Пороги ±2
    for thresh in [-2, 2]:
        ax.axhline(thresh, color='red', linewidth=0.5, linestyle=':', alpha=0.5)

    # Вертикальные линии на паттернах
    for p in patterns:
        if p['type'] not in ('FLOW_DIVERGENCE', 'FLOW_EXTREME', 'OI_EXTREME'):
            continue
        t = pd.Timestamp(p['time'])
        ax.axvline(t, color='gray', linewidth=0.3, alpha=0.2)


def print_summary(patterns: list):
    """Краткая сводка по паттернам с успешностью."""
    if not patterns:
        print("Паттернов не обнаружено.")
        return
    print(f"\n{'='*60}")
    print(f"ВСЕГО: {len(patterns)} паттернов")
    print(f"{'='*60}")

    # Группировка по типам
    from collections import Counter
    type_counts = Counter(p['type'] for p in patterns)
    for t, cnt in type_counts.most_common():
        verif = [p for p in patterns if p['type'] == t and 'fwd_ret_1h' in p]
        if verif:
            success = sum(1 for p in verif if p.get('success'))
            avg_1h = np.mean([p.get('fwd_ret_1h', 0) or 0 for p in verif])
            print(f"  {t:20s}: {cnt:>4d}  (успех {success}/{len(verif)} = {success/len(verif)*100:.0f}%, avg 1h={avg_1h:+.2f}%)")
        else:
            print(f"  {t:20s}: {cnt:>4d}")

    bulls = sum(1 for p in patterns if p['direction'] == 'BULL')
    bears = sum(1 for p in patterns if p['direction'] == 'BEAR')
    print(f"\n  BULL: {bulls}  BEAR: {bears}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description='MOEX Manipulation Visualizer')
    parser.add_argument('--symbol', default='Si', help='Тикер фьючерса')
    parser.add_argument('--days', type=int, default=60)
    parser.add_argument('--zscore', type=float, default=ZSCORE_THRESHOLD)
    parser.add_argument('--output', default=None, help='Сохранить PNG (по умолч. показать)')
    parser.add_argument('--no-show', action='store_true')
    args = parser.parse_args()

    symbol = resolve_symbol(args.symbol.strip())
    print(f"Загрузка данных {symbol} за {args.days} дней...")

    df = load_data(symbol, args.days)
    print(f"  Свечей: {len(df)} ({df['time'].min():%Y-%m-%d} — {df['time'].max():%Y-%m-%d})")
    has_oi = 'fiz_net' in df.columns and df['has_oi'].any()
    print(f"  OI: {'да' if has_oi else 'нет'}")

    patterns = detect_all(df, args.zscore, use_oi=has_oi)
    print(f"  Найдено: {len(patterns)} паттернов")

    output = args.output or f'moex_manip_{symbol}_{args.days}d.png'
    plot_chart(df, patterns, symbol, output)
    print_summary(patterns)


if __name__ == '__main__':
    main()
