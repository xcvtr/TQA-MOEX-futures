"""Trading Bot Dashboard — простой web-мониторинг на http.server.

Запуск: python -m trading_bot.dashboard [--port 5080]

Без внешних зависимостей (только http.server, json, csv).
Тёмная тема, equity curve SVG, открытые позиции, история сделок.
"""

import http.server
import json
import os
import csv
import argparse
from datetime import datetime
from typing import Any

from .tracker import load_positions
from . import CAPITAL

HERE = os.path.dirname(os.path.abspath(__file__))
TRADES_LOG = os.path.join(HERE, 'trades.csv')


def read_trades() -> list[dict]:
    """Прочитать trades.csv, вернуть список закрытых сделок с PnL."""
    if not os.path.exists(TRADES_LOG):
        return []
    trades = []
    with open(TRADES_LOG, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            pnl_str = row.get('pnl_rub', '').strip()
            if pnl_str:
                trades.append(row)
    return trades


VS_TICKERS = {'HS', 'KC', 'DX', 'HY'}
REV_TICKERS = {'NM', 'BR', 'SBERF', 'AF'}
OB_TICKERS = {'SBERF', 'BR', 'NM', 'AF'}

def _strategy_for_symbol(symbol: str) -> str:
    if symbol in VS_TICKERS:
        return 'VS'
    if symbol in REV_TICKERS:
        return 'Reversion'
    if symbol in OB_TICKERS:
        return 'OB'
    return 'Other'

def _rolling_winrate(trades: list[dict], window: int = 50) -> list[dict]:
    """Calculate rolling WR over sliding window. Returns list of {n, wr, cum_pnl} snapshots."""
    pnls = [float(t.get('pnl_rub', 0)) for t in trades]
    snapshots = []
    for i in range(window, len(pnls)+1):
        chunk = pnls[i-window:i]
        wins = sum(1 for p in chunk if p > 0)
        wr = round(wins / window * 100, 1)
        cum = round(sum(pnls[:i]), 0)
        snapshots.append({'n': i, 'wr': wr, 'cum_pnl': cum})
    return snapshots


def _calc_stats(trades: list[dict]) -> dict:
    """Посчитать статистику по списку сделок."""
    total = len(trades)
    if total == 0:
        return {'trades': 0, 'win_rate': 0.0, 'profit_factor': 0.0, 'pnl': 0.0, 'equity': []}

    pnls = [float(t.get('pnl_rub', 0)) for t in trades]
    total_pnl = round(sum(pnls), 2)
    wins = sum(1 for p in pnls if p > 0)
    wr = round(wins / total * 100, 1) if total else 0.0
    gross_profit = sum(p for p in pnls if p > 0)
    gross_loss = abs(sum(p for p in pnls if p < 0))
    pf = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (gross_profit if gross_profit > 0 else 0.0)

    equity = []
    cum = 0.0
    for p in pnls:
        cum = round(cum + p, 2)
        equity.append(cum)

    return {'trades': total, 'win_rate': wr, 'profit_factor': pf, 'pnl': total_pnl, 'pnls': pnls, 'equity': equity}


def get_portfolio_stats() -> dict[str, Any]:
    """
    Посчитать статистику портфеля.

    Returns
    -------
    dict с ключами:
        total_trades     — всего закрытых сделок
        win_rate         — процент прибыльных (%)
        profit_factor    — отношение прибыли к убыткам
        total_pnl_rub    — суммарный PnL в рублях
        equity_curve     — список cumulative PnL
        open_positions   — список открытых позиций из tracker.load_positions
        vs               — статистика по Volume Surge сделкам
        reversion        — статистика по Reversion сделкам
    """
    trades = read_trades()
    positions = load_positions()
    open_pos = [p for p in positions if p.get('status') == 'open']

    # Разделяем по стратегиям
    vs_trades = [t for t in trades if _strategy_for_symbol(t.get('symbol', '')) == 'VS']
    rev_trades = [t for t in trades if _strategy_for_symbol(t.get('symbol', '')) == 'Reversion']
    ob_trades = [t for t in trades if _strategy_for_symbol(t.get('symbol', '')) == 'OB']

    total_stats = _calc_stats(trades)
    vs_stats = _calc_stats(vs_trades)
    rev_stats = _calc_stats(rev_trades)
    ob_stats = _calc_stats(ob_trades)

    rolling = _rolling_winrate(trades)
    return {
        'total_trades': total_stats['trades'],
        'win_rate': total_stats['win_rate'],
        'profit_factor': total_stats['profit_factor'],
        'total_pnl_rub': total_stats['pnl'],
        'equity_curve': total_stats['equity'],
        'open_positions': open_pos,
        'vs': vs_stats,
        'reversion': rev_stats,
        'order_block': ob_stats,
        'rolling_wr': rolling,
    }


def _max_drawdown(equity_curve: list[float]) -> float:
    """Рассчитать максимальную просадку в процентах."""
    if not equity_curve:
        return 0.0
    peak = equity_curve[0]
    max_dd = 0.0
    for val in equity_curve:
        if val > peak:
            peak = val
        dd = (peak - val) / peak * 100 if peak != 0 else 0.0
        if dd > max_dd:
            max_dd = dd
    return round(max_dd, 1)


def _equity_svg(equity_curve: list[float], width: int = 800, height: int = 200) -> str:
    """Сгенерировать SVG polyline для equity curve.

    y = 100 - cumulative_return_scaled
    """
    if not equity_curve:
        return '<svg width="800" height="200"><text x="10" y="105" fill="#8b949e">Нет данных</text></svg>'

    mn = min(equity_curve)
    mx = max(equity_curve)
    rng = mx - mn if mx != mn else 1

    n = len(equity_curve)
    points = []
    for i, val in enumerate(equity_curve):
        x = i / max(n - 1, 1) * width
        # scale to [0..200] where min -> bottom, max -> top
        y = height - ((val - mn) / rng * (height - 20)) - 10
        points.append(f"{x:.1f},{y:.1f}")

    color = '#4CAF50' if equity_curve[-1] >= equity_curve[0] else '#f44336'
    return f'''<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}">
    <rect width="{width}" height="{height}" fill="#161b22" rx="4"/>
    <polyline points="{" ".join(points)}" stroke="{color}" fill="none" stroke-width="2"/>
  </svg>'''


def render_html(stats: dict) -> str:
    """
    Сгенерировать HTML страницу дашборда.

    Параметры
    ---------
    stats : dict
        Результат get_portfolio_stats()

    Возвращает
    ----------
    str — HTML с тёмной темой, таблицами и equity curve SVG.
    """
    trades = read_trades()
    ec = stats.get('equity_curve', [])
    open_pos = stats.get('open_positions', [])
    total_trades = stats['total_trades']
    wr = stats['win_rate']
    pf = stats['profit_factor']
    total_pnl = stats['total_pnl_rub']
    max_dd = _max_drawdown(ec)

    # Последние 20 закрытых сделок
    closed_trades = [t for t in trades if 'closed' in t.get('status', '')]
    last_trades = closed_trades[-20:] if len(closed_trades) > 20 else closed_trades

    pnl_color = 'green' if total_pnl >= 0 else 'red'

    svg = _equity_svg(ec)
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    vs_stats = stats.get('vs', {'trades': 0, 'win_rate': 0.0, 'profit_factor': 0.0})
    rev_stats = stats.get('reversion', {'trades': 0, 'win_rate': 0.0, 'profit_factor': 0.0})
    ob_stats = stats.get('order_block', {'trades': 0, 'win_rate': 0.0, 'profit_factor': 0.0})

    # ── Сборка HTML ──────────────────────────────────────────────
    html = f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Trading Bot Dashboard</title>
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: #1a1d23;
    color: #e1e4e8;
    padding: 24px;
  }}
  h1 {{ font-size: 24px; color: #58a6ff; margin-bottom: 24px; }}
  h2 {{ font-size: 16px; color: #c9d1d9; margin: 24px 0 12px; }}
  .grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 16px;
    margin-bottom: 24px;
  }}
  .card {{
    background: #21262d;
    border: 1px solid #30363d;
    border-radius: 8px;
    padding: 16px;
  }}
  .card h3 {{
    font-size: 12px;
    color: #8b949e;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    margin-bottom: 8px;
  }}
  .card .value {{
    font-size: 28px;
    font-weight: 700;
  }}
  .card .value.green {{ color: #3fb950; }}
  .card .value.red {{ color: #f85149; }}
  .card .value.white {{ color: #e1e4e8; }}
  .card .value.yellow {{ color: #d29922; }}
  table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
  }}
  th {{
    text-align: left;
    color: #8b949e;
    font-size: 11px;
    text-transform: uppercase;
    padding: 8px 12px;
    border-bottom: 2px solid #30363d;
  }}
  td {{
    padding: 8px 12px;
    border-bottom: 1px solid #21262d;
  }}
  tr:hover td {{ background: #1c2025; }}
  .dir-LONG {{ color: #3fb950; }}
  .dir-SHORT {{ color: #f85149; }}
  .pnl-pos {{ color: #3fb950; }}
  .pnl-neg {{ color: #f85149; }}
  .svg-wrap {{
    background: #21262d;
    border: 1px solid #30363d;
    border-radius: 8px;
    padding: 16px;
    margin-bottom: 24px;
  }}
  .svg-wrap h3 {{
    font-size: 12px;
    color: #8b949e;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    margin-bottom: 12px;
  }}
  footer {{
    margin-top: 32px;
    color: #484f58;
    font-size: 12px;
    text-align: center;
  }}
</style>
</head>
<body>

<h1>Trading Bot Dashboard</h1>
<p style="font-size:12px; color:#8b949e; margin-bottom:16px;">
  <a href="/backtest" style="color:#58a6ff;">🧪 Backtest Results</a>
</p>

<div class="grid">
  <div class="card">
    <h3>Капитал</h3>
    <div class="value white">{CAPITAL:,} ₽</div>
  </div>
  <div class="card">
    <h3>Total PnL</h3>
    <div class="value {pnl_color}">{total_pnl:+,.0f} ₽</div>
  </div>
  <div class="card">
    <h3>Сделки</h3>
    <div class="value white">{total_trades}</div>
  </div>
  <div class="card">
    <h3>Win Rate</h3>
    <div class="value green">{wr}%</div>
  </div>
  <div class="card">
    <h3>Profit Factor</h3>
    <div class="value yellow">{pf}</div>
  </div>
  <div class="card">
    <h3>Max DD</h3>
    <div class="value red">{max_dd}%</div>
  </div>
</div>

<!-- Panels by strategy -->
<h2>По стратегиям</h2>
<div class="grid">
  <div class="card">
    <h3>🔵 Volume Surge</h3>
    <div class="value white">{vs_stats['trades']}</div>
    <div style="font-size:12px;color:#8b949e;margin-top:4px;">WR {vs_stats['win_rate']}% · PF {vs_stats['profit_factor']}</div>
  </div>
  <div class="card">
    <h3>🟢 Mean Reversion</h3>
    <div class="value white">{rev_stats['trades']}</div>
    <div style="font-size:12px;color:#8b949e;margin-top:4px;">WR {rev_stats['win_rate']}% · PF {rev_stats['profit_factor']}</div>
  </div>
  <div class="card">
    <h3>🟣 Order Block (ICT)</h3>
    <div class="value white">{ob_stats['trades']}</div>
    <div style="font-size:12px;color:#8b949e;margin-top:4px;">WR {ob_stats['win_rate']}% · PF {ob_stats['profit_factor']}</div>
  </div>
</div>

<!-- Rolling WR -->
<h2>Rolling WR (последние 50 сделок)</h2>
<div class="svg-wrap" style="overflow-x:auto;white-space:nowrap">
"""

    rolling = stats.get('rolling_wr', [])
    if rolling:
        last10 = rolling[-10:]
        roll_warn = '⚠️ ' if last10[-1]['wr'] < 40 else ''
        # Sparkline as mini SVG
        vals = [s['wr'] for s in rolling]
        mn, mx = min(vals), max(vals)
        rng = mx - mn if mx != mn else 1
        n = len(vals)
        sw, sh = max(n * 3, 200), 60
        pts = []
        for i, v in enumerate(vals):
            x = i / max(n - 1, 1) * sw
            y = sh - ((v - mn) / rng * (sh - 10)) - 5
            pts.append(f"{x:.1f},{y:.1f}")
        roll_color = '#f85149' if last10[-1]['wr'] < 40 else '#3fb950'
        roll_svg = f'<svg width="{sw}" height="{sh}" viewBox="0 0 {sw} {sh}"><rect width="{sw}" height="{sh}" fill="#21262d" rx="2"/><polyline points="{" ".join(pts)}" stroke="{roll_color}" fill="none" stroke-width="1.5"/><text x="4" y="10" fill="#8b949e" font-size="9">{mn:.0f}%</text><text x="{sw-4}" y="{sh-4}" text-anchor="end" fill="#8b949e" font-size="9">{mx:.0f}%</text></svg>'

        html += f'<p style="font-size:12px;color:#8b949e;margin-bottom:8px">{roll_warn}Последние 10: {", ".join(f"{s["wr"]}%" for s in last10)}</p>'
        html += f'<div style="margin-bottom:8px">{roll_svg}</div>'
        html += f"""<table>
  <tr><th>#</th><th>WR %</th><th>Cum PnL</th></tr>
"""
        for s in reversed(last10):
            wr_cls = 'pnl-pos' if s['wr'] >= 50 else 'pnl-neg'
            html += f"""  <tr>
    <td>{s['n']}</td>
    <td class="{wr_cls}">{s['wr']}%</td>
    <td class="{'pnl-pos' if s['cum_pnl'] >= 0 else 'pnl-neg'}">{s['cum_pnl']:+,.0f}</td>
  </tr>
"""
        html += '</table>'
    else:
        html += '<p style="color:#484f58;">Недостаточно данных (нужно ≥50 сделок)</p>'

    html += """
<div class="svg-wrap">
  <h3>Equity Curve</h3>
  {svg}
</div>

<h2>Открытые позиции</h2>
"""

    if open_pos:
        html += """<table>
  <tr><th>Symbol</th><th>Direction</th><th>Entry</th><th>Contracts</th><th>Bars Held</th></tr>
"""
        for p in open_pos:
            direction = p.get('direction', '?')
            dir_cls = f"dir-{direction}"
            entry = p.get('entry_price', 0)
            html += f"""  <tr>
    <td>{p.get('symbol', '?')}</td>
    <td class="{dir_cls}">{direction}</td>
    <td>{entry:.4f}</td>
    <td>{p.get('contracts', 0)}</td>
    <td>{p.get('bars_held', 0)}</td>
  </tr>
"""
        html += '</table>'
    else:
        html += '<p style="color: #484f58;">Нет открытых позиций</p>'

    html += """
<h2>Последние 20 сделок</h2>
"""
    if last_trades:
        html += """<table>
  <tr><th>Time</th><th>Symbol</th><th>Strategy</th><th>Direction</th><th>Entry</th><th>Exit</th><th>PnL</th></tr>
"""
        for t in reversed(last_trades):
            pnl = float(t.get('pnl_rub', 0))
            pnl_cls = 'pnl-pos' if pnl >= 0 else 'pnl-neg'
            direction = t.get('direction', '?')
            dir_cls = f"dir-{direction}"
            sym = t.get('symbol', '?')
            strat = _strategy_for_symbol(sym)
            html += f"""  <tr>
    <td>{t.get('time', '?')}</td>
    <td>{sym}</td>
    <td style="font-size:11px;color:#8b949e;">{strat}</td>
    <td class="{dir_cls}">{direction}</td>
    <td>{t.get('entry', '?')}</td>
    <td>{t.get('exit', '?')}</td>
    <td class="{pnl_cls}">{pnl:+,.0f}</td>
  </tr>
"""
        html += '</table>'
    else:
        html += '<p style="color: #484f58;">История пуста</p>'

    html += f"""
<footer>Обновлено: {now_str}</footer>
</body>
</html>"""

    return html


# ── Backtest Results Data ──────────────────────────────────────────────────

BACKTEST_RESULTS = {
    'volume_surge': {
        'name': 'Volume Surge + ADX',
        'desc': 'Vol z-score ≥2.5 + divergence FIZ/YUR, ADX>20 фильтр, MoEx 5m+OI',
        'tickers': [
            ('HS', 29, 69.0, 1.23, 2.2, 50, 'H4', 'vol_surge'),
            ('BM', 55, 63.0, 3.72, 1.1, 55, 'H4', 'vol_surge'),
            ('HY', 30, 65.0, 1.28, 5.0, 30, 'H4', 'yur_dom'),
            ('KC', 90, 57.0, 1.54, 7.1, 90, 'H4', 'vol_surge'),
            ('DX', 61, 58.0, 2.00, 8.0, 61, 'H4', 'vol_surge'),
        ]
    },
    'mean_reversion': {
        'name': 'Mean Reversion After Vol Exhaustion',
        'desc': '3-bar impulse + vol_z≥1.5 + wide range + mid close, walkforward OOS',
        'tickers': [
            ('NM', 24, 87.5, 11.43, 0.5, 12, '5m', 'reversion'),
            ('BR', 69, 66.7, 4.91, 1.7, 6, '5m', 'reversion'),
            ('SBERF', 29, 72.4, 3.44, 0.4, 12, '5m', 'reversion'),
            ('AF', 22, 64.7, 2.08, 1.2, 12, '5m', 'reversion'),
            ('TN', 18, 77.8, 8.52, 0.1, 12, '5m', 'reversion'),
            ('TT', 18, 77.8, 8.52, 0.1, 12, '5m', 'reversion'),
        ]
    },
    'order_block': {
        'name': 'Order Blocks (ICT Smart Money)',
        'desc': 'Displacement >1.5×median body → OB entry, walkforward OOS, 149K сигналов',
        'tickers': [
            ('SBERF', 4697, 69.9, 4.27, 2.0, 4, '5m', 'order_block'),
            ('SBERF', 4816, 70.8, 3.60, 2.6, 4, '5m', 'order_block'),
            ('BR', 5201, 71.7, 2.06, 192.0, 4, '5m', 'order_block'),
            ('BR', 5038, 71.7, 2.38, 46.6, 4, '5m', 'order_block'),
            ('AF', 4390, 67.4, 2.17, 28.4, 4, '5m', 'order_block'),
            ('AF', 4690, 67.7, 1.71, 40.5, 4, '5m', 'order_block'),
            ('NM', 4096, 67.1, 2.16, 30.2, 4, '5m', 'order_block'),
            ('NM', 4353, 67.0, 1.41, 111.4, 4, '5m', 'order_block'),
        ]
    }
}

STRATEGY_COLORS = {
    'vol_surge': '#58a6ff',
    'yur_dom': '#d29922',
    'reversion': '#3fb950',
    'order_block': '#bc8cff',
}


def _bar_svg(strategies: list, width: int = 400, height: int = 180) -> str:
    """Сгенерировать SVG bar chart для сравнения WR по тикерам стратегии."""
    if not strategies:
        return '<svg width="400" height="180"><text x="10" y="90" fill="#8b949e">Нет данных</text></svg>'

    n = len(strategies)
    bar_w = min(30, (width - 40) // max(n, 1))
    gap = 6
    left = 40
    bottom = height - 10

    max_wr = max(s[2] for s in strategies) * 1.1

    bars = []
    labels = []
    values = []
    for i, s in enumerate(strategies):
        ticker, n_sig, wr, pf, dd, h, tf, strat = s
        h_px = (wr / max_wr) * (height - 30)
        x = left + i * (bar_w + gap)
        y = bottom - h_px
        color = STRATEGY_COLORS.get(strat, '#58a6ff')
        bars.append(f'<rect x="{x}" y="{y:.0f}" width="{bar_w}" height="{h_px:.0f}" fill="{color}" rx="2" opacity="0.85"/>')
        labels.append(f'<text x="{x + bar_w/2}" y="{bottom + 14}" text-anchor="middle" fill="#8b949e" font-size="9">{ticker}</text>')
        values.append(f'<text x="{x + bar_w/2}" y="{y - 4}" text-anchor="middle" fill="{color}" font-size="9">{wr:.0f}%</text>')

    return f'''<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}">
    <rect width="{width}" height="{height}" fill="#161b22" rx="4"/>
    {"".join(bars)}
    {"".join(labels)}
    {"".join(values)}
  </svg>'''


def render_backtest_html() -> str:
    """Сгенерировать HTML страницу с результатами backtest стратегий."""
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    html = f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Backtest Results — TQA-MOEX</title>
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: #1a1d23;
    color: #e1e4e8;
    padding: 24px;
  }}
  h1 {{ font-size: 24px; color: #58a6ff; margin-bottom: 8px; }}
  .nav {{ margin-bottom: 24px; }}
  .nav a {{ color: #58a6ff; text-decoration: none; font-size: 13px; }}
  .nav a:hover {{ text-decoration: underline; }}
  .strat-section {{ margin-bottom: 32px; }}
  .strat-section h2 {{ font-size: 18px; color: #c9d1d9; margin-bottom: 4px; }}
  .strat-section .desc {{ font-size: 12px; color: #8b949e; margin-bottom: 12px; }}
  .chart-wrap {{
    background: #21262d;
    border: 1px solid #30363d;
    border-radius: 8px;
    padding: 16px;
    margin-bottom: 16px;
    display: inline-block;
  }}
  table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
    margin-bottom: 16px;
  }}
  th {{
    text-align: left;
    color: #8b949e;
    font-size: 11px;
    text-transform: uppercase;
    padding: 8px 12px;
    border-bottom: 2px solid #30363d;
  }}
  td {{
    padding: 8px 12px;
    border-bottom: 1px solid #21262d;
  }}
  tr:hover td {{ background: #1c2025; }}
  .ticker {{ font-weight: 600; }}
  .wr-good {{ color: #3fb950; }}
  .wr-ok {{ color: #d29922; }}
  .wr-bad {{ color: #f85149; }}
  .pf-high {{ color: #3fb950; }}
  .pf-low {{ color: #f85149; }}
  .dd-small {{ color: #3fb950; }}
  .dd-large {{ color: #f85149; }}
  footer {{
    margin-top: 32px;
    color: #484f58;
    font-size: 12px;
    text-align: center;
  }}
  .legend {{
    display: flex;
    gap: 16px;
    font-size: 11px;
    color: #8b949e;
    margin-bottom: 16px;
  }}
  .legend-item {{ display: flex; align-items: center; gap: 6px; }}
  .legend-dot {{ width: 10px; height: 10px; border-radius: 2px; }}
  .best-rank {{
    font-size: 11px;
    color: #8b949e;
    margin-bottom: 8px;
  }}
</style>
</head>
<body>

<h1>🧪 Backtest Results — TQA-MOEX</h1>
<div class="nav">
  <a href="/">&larr; Live Dashboard</a>
</div>

<div class="legend">
  <div class="legend-item"><div class="legend-dot" style="background:#58a6ff"></div> Volume Surge</div>
  <div class="legend-item"><div class="legend-dot" style="background:#3fb950"></div> Mean Reversion</div>
  <div class="legend-item"><div class="legend-dot" style="background:#bc8cff"></div> Order Block (ICT)</div>
</div>
"""

    for key, data in BACKTEST_RESULTS.items():
        tickers = data['tickers']
        strat = tickers[0][7] if tickers else key

        # WR bar chart
        svg = _bar_svg(tickers)

        html += f"""
<div class="strat-section">
  <h2>{data['name']}</h2>
  <div class="desc">{data['desc']}</div>
  <div class="chart-wrap">{svg}</div>
  <table>
    <tr><th>Тикер</th><th>Напр</th><th>N</th><th>WR%</th><th>PF</th><th>DD%</th><th>H</th></tr>
"""
        for s in tickers:
            ticker, n_sig, wr, pf, dd, h, tf, st = s
            wr_cls = 'wr-good' if wr >= 65 else ('wr-ok' if wr >= 55 else 'wr-bad')
            pf_cls = 'pf-high' if pf >= 2.0 else ('pf-low' if pf < 1.3 else '')
            dd_cls = 'dd-small' if dd <= 10 else ('dd-large' if dd > 20 else '')
            # Direction from second SBERF/BR/AF/NM entries
            dirs = {5: 'SHORT', 3: 'LONG'}
            # Determine direction from order
            idx = [r[0] for r in tickers].index(ticker)
            # But we need the individual entry's direction - for OB we have pairs
            # Simple heuristic: alternate LONG/SHORT for tickers with 2 entries
            same_tickers = [r for r in tickers if r[0] == ticker]
            if len(same_tickers) >= 2:
                pair_idx = [r for r in tickers].index(s)
                first_of_pair = [r for r in tickers].index(same_tickers[0])
                if pair_idx == first_of_pair:
                    direction = 'LONG'
                else:
                    direction = 'SHORT'
            else:
                direction = '—'

            html += f"""    <tr>
    <td class="ticker">{ticker}</td>
    <td>{direction}</td>
    <td>{n_sig:,}</td>
    <td class="{wr_cls}">{wr}%</td>
    <td class="{pf_cls}">{pf}</td>
    <td class="{dd_cls}">{dd}%</td>
    <td>h={h}</td>
  </tr>
"""
        html += """  </table>
</div>
"""

    # Overall ranking
    html += """
<div class="strat-section">
  <h2>🏆 Best Overall Configurations</h2>
  <div class="best-rank">Ranked by Score = WR² × PF / DD</div>
  <table>
    <tr><th>#</th><th>Стратегия</th><th>Тикер</th><th>Dir</th><th>N</th><th>WR%</th><th>PF</th><th>DD%</th><th>Score</th></tr>
"""
    all_configs = []
    for key, data in BACKTEST_RESULTS.items():
        tickers = data['tickers']
        same_count = {}
        for s in tickers:
            t = s[0]
            same_count[t] = same_count.get(t, 0) + 1
        seen = {}
        for s in tickers:
            t = s[0]
            seen[t] = seen.get(t, 0) + 1
            direction = 'SHORT' if seen[t] == 2 else 'LONG' if seen[t] == 1 else '—'
            if direction == 'LONG' and seen[t] > 1:
                direction = 'SHORT'
            score = (s[2] ** 2) * s[3] / max(s[4], 0.1)
            all_configs.append((data['name'], t, direction, s[1], s[2], s[3], s[4], score))

    all_configs.sort(key=lambda x: x[7], reverse=True)
    for i, (strat, ticker, direction, n, wr, pf, dd, score) in enumerate(all_configs[:15]):
        badge = {'Volume Surge': '🔵', 'Mean Reversion': '🟢', 'Order Blocks': '🟣'}.get(strat.split('(')[0].strip(), '•')
        html += f"""    <tr>
    <td>{i+1}</td>
    <td>{badge} {strat}</td>
    <td class="ticker">{ticker}</td>
    <td>{direction}</td>
    <td>{n:,}</td>
    <td class="wr-good">{wr}%</td>
    <td class="pf-high">{pf}</td>
    <td class="dd-small">{dd}%</td>
    <td><strong>{score:,.0f}</strong></td>
  </tr>
"""
    html += """  </table>
</div>
"""

    html += f"""
<footer>Обновлено: {now_str}</footer>
</body>
</html>"""
    return html


def run(port: int = 5080) -> None:
    """Запустить HTTP сервер с дашбордом."""

    class DashboardHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == '/':
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.end_headers()
                stats = get_portfolio_stats()
                html = render_html(stats)
                self.wfile.write(html.encode('utf-8'))
            elif self.path == '/backtest':
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.end_headers()
                html = render_backtest_html()
                self.wfile.write(html.encode('utf-8'))
            elif self.path == '/api/status':
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                stats = get_portfolio_stats()
                self.wfile.write(json.dumps({
                    'positions_open': len(stats['open_positions']),
                    'total_trades': stats['total_trades'],
                    'win_rate': stats['win_rate'],
                    'profit_factor': stats['profit_factor'],
                    'total_pnl_rub': stats['total_pnl_rub'],
                    'capital': CAPITAL,
                }, ensure_ascii=False).encode('utf-8'))
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, format, *args):
            """Тихий лог — только строка для stdout."""
            if args:
                print(f"[{datetime.now():%H:%M:%S}] {args[0]} {args[1] if len(args) > 1 else ''} {args[2] if len(args) > 2 else ''}")

    server = http.server.HTTPServer(('0.0.0.0', port), DashboardHandler)
    print(f'📊 Trading Bot Dashboard: http://0.0.0.0:{port}')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nDashboard stopped.')
        server.server_close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Trading Bot Dashboard')
    parser.add_argument('--port', type=int, default=5080, help='HTTP port')
    args = parser.parse_args()
    run(args.port)
