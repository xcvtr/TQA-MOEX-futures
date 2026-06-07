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

def _strategy_for_symbol(symbol: str) -> str:
    if symbol in VS_TICKERS:
        return 'VS'
    if symbol in REV_TICKERS:
        return 'Reversion'
    return 'Other'

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

    total_stats = _calc_stats(trades)
    vs_stats = _calc_stats(vs_trades)
    rev_stats = _calc_stats(rev_trades)

    return {
        'total_trades': total_stats['trades'],
        'win_rate': total_stats['win_rate'],
        'profit_factor': total_stats['profit_factor'],
        'total_pnl_rub': total_stats['pnl'],
        'equity_curve': total_stats['equity'],
        'open_positions': open_pos,
        'vs': vs_stats,
        'reversion': rev_stats,
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
</div>

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
