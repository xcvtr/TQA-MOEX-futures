#!/usr/bin/env python3
"""
MOEX Manipulation Dashboard — сервер.
"""
import sys, os, json, argparse
from pathlib import Path

from manipulation_search import (
    load_price_data, load_oi_data, prepare_data,
    detect_all, resolve_symbol, ZSCORE_THRESHOLD
)

import numpy as np
import pandas as pd
from starlette.applications import Starlette
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

TEMPLATE_DIR = Path(__file__).parent / 'templates'
TICKERS = [
    'Si', 'BR', 'Eu', 'ED', 'GD', 'GZ', 'LK', 'SR', 'VB',
    'RN', 'AF', 'AL', 'SN', 'SP', 'TT', 'HY', 'NM', 'MG', 'MM',
    'ME', 'SV', 'NG', 'PD', 'PT', 'RI', 'CNYRUBF', 'USDRUBF',
    'EURRUBF', 'GLDRUBF', 'IMOEXF', 'GAZPF', 'SBERF', 'GL', 'X5',
    'YD', 'BM', 'CC', 'CE', 'CH', 'DX', 'FF', 'GK', 'HS',
    'IB', 'KC', 'MC', 'NA', 'OJ', 'RM', 'SE', 'SF', 'SS',
    'TN', 'UC', 'W4',
]

_cache = {'key': None, 'df': None, 'patterns': None, 'error': None}


def load_cache(symbol: str, days: int, zscore: float):
    key = (symbol, days, zscore)
    if key == _cache.get('key'):
        return
    print(f"Loading {symbol} {days}d...")
    df_p = load_price_data(symbol, days)
    if df_p.empty:
        _cache.update({'key': key, 'df': None, 'patterns': None, 'error': f'No data for {symbol}'})
        return
    df_oi = load_oi_data(symbol, days)
    df = prepare_data(df_p, df_oi, symbol)
    if 'yur_flow_zscore' not in df.columns and 'yur_flow' in df.columns:
        fm = df['yur_flow'].rolling(288, min_periods=50).mean()
        fs = df['yur_flow'].rolling(288, min_periods=50).std()
        df['yur_flow_zscore'] = ((df['yur_flow'] - fm) / fs.replace(0, np.nan)).fillna(0)
    patterns = detect_all(df, zscore, use_oi=('fiz_net' in df.columns and df['has_oi'].any()))
    _cache.update({'key': key, 'df': df, 'patterns': patterns, 'symbol': symbol, 'days': days, 'error': None})
    print(f"  {len(df)} bars, {len(patterns)} patterns")


def df_to_json(df):
    d = df.copy()
    d['time_ts'] = d['time'].astype(np.int64) // 10**6
    cols = ['time_ts', 'open', 'high', 'low', 'close', 'volume']
    for c in ['fiz_net', 'fiz_buy', 'fiz_sell', 'yur_net', 'yur_buy', 'yur_sell',
              'fiz_flow_zscore', 'yur_flow_zscore', 'fiz_flow', 'yur_flow']:
        if c in d.columns:
            cols.append(c)
    return {c: d[c].fillna(0).tolist() for c in cols if c in d.columns}


def patterns_to_json(patterns):
    result = []
    for p in patterns:
        item = {k: v for k, v in p.items() if not k.startswith('fwd_') and k != 'swing_idx'}
        if isinstance(item.get('time'), pd.Timestamp):
            item['time'] = item['time'].isoformat()
        for h in ['1h','2h','3h','4h','5h','6h']:
            if f'fwd_ret_{h}' in p:
                item[f'fwd_ret_{h}'] = p[f'fwd_ret_{h}']
        item['success'] = p.get('success')
        result.append(item)
    return result


def calc_equity(patterns, capital=10000):
    """Calculate equity curve trading 1 lot at pattern signals.
    BULL → long, BEAR → short. Exit at 6h forward return.
    For Si: 1 lot notional ≈ entry_price RUB.
    """
    trades = []
    for p in patterns:
        direction = p.get('direction')
        if direction not in ('BULL', 'BEAR'):
            continue
        fwd = p.get('fwd_ret_6h')
        if fwd is None:
            continue
        sign = 1 if direction == 'BULL' else -1
        entry = p.get('entry_price', 0)
        # 1 lot Si: notional = entry_price * 1 (price in RUB per 1000 USD)
        pnl_pct = sign * fwd / 100.0
        pnl_rub = entry * pnl_pct  # 1 lot
        capital += pnl_rub
        t = p.get('time')
        if isinstance(t, pd.Timestamp):
            t = t.isoformat()
        trades.append({
            'time': str(t),
            'equity': round(capital, 2),
            'pnl': round(pnl_rub, 2),
            'type': p.get('type', ''),
            'direction': direction,
        })
    trades.sort(key=lambda x: x['time'])
    total_pnl = round(capital - 10000, 2)
    n_trades = len(trades)
    winners = sum(1 for t in trades if t['pnl'] > 0)
    summary = {
        'initial': 10000,
        'final': round(capital, 2),
        'totalPnl': total_pnl,
        'nTrades': n_trades,
        'winners': winners,
        'losers': n_trades - winners,
        'winRate': round(winners / n_trades * 100, 1) if n_trades else 0,
    }
    return trades, summary


async def api_data(request):
    symbol = request.query_params.get('symbol', 'Si').strip()
    days = int(request.query_params.get('days', '60'))
    try:
        symbol = resolve_symbol(symbol)
    except Exception:
        pass
    load_cache(symbol, days, ZSCORE_THRESHOLD)
    if _cache.get('error'):
        return JSONResponse({'error': _cache['error']}, status_code=404)
    df = _cache['df']
    patterns = _cache['patterns']
    return JSONResponse({
        'barData': df_to_json(df),
        'patterns': patterns_to_json(patterns),
        'n_bars': len(df),
        'n_patterns': len(patterns),
    })


async def index(request):
    symbol = request.query_params.get('symbol', 'Si').strip()
    days = int(request.query_params.get('days', '60'))
    try:
        symbol = resolve_symbol(symbol)
    except Exception:
        pass
    load_cache(symbol, days, ZSCORE_THRESHOLD)

    tmpl_path = TEMPLATE_DIR / 'dashboard.html'
    html = tmpl_path.read_text(encoding='utf-8')

    if _cache.get('error'):
        return HTMLResponse(f'<h2>{_cache["error"]}</h2><a href="/">Back</a>')

    df = _cache['df']
    patterns = _cache['patterns']

    # Stats
    oi_pats = [p for p in patterns if p['type'] in ('OI_TRAP','OI_EXTREME','FLOW_EXTREME','FLOW_DIVERGENCE')]
    oi_ok = sum(1 for p in oi_pats if p.get('success'))
    oi_all = len(oi_pats)
    oi_str = f'{oi_ok}/{oi_all} ({oi_ok/oi_all*100:.0f}%)' if oi_all else '-'
    bulls = sum(1 for p in patterns if p.get('direction') == 'BULL')
    bears = sum(1 for p in patterns if p.get('direction') == 'BEAR')
    stats = (
        f'<div class="stat"><span class="stat-label">Symbol:</span><span class="stat-value">{symbol}</span></div>'
        f'<div class="stat"><span class="stat-label">Bars:</span><span class="stat-value">{len(df)}</span></div>'
        f'<div class="stat"><span class="stat-label">Period:</span><span class="stat-value">{df["time"].min():%d.%m} - {df["time"].max():%d.%m}</span></div>'
        f'<div class="stat"><span class="stat-label">Patterns:</span><span class="stat-value">{len(patterns)}</span></div>'
        f'<div class="stat"><span class="stat-label">OI Success:</span><span class="stat-value">{oi_str}</span></div>'
        f'<div class="stat"><span class="stat-label">BULL/BEAR:</span><span class="stat-value">{bulls}/{bears}</span></div>'
    )

    # Symbol options
    sym_opts = ''.join(f'<option value="{t}"{" selected" if t==symbol else ""}>{t}</option>' for t in TICKERS)

    # Days options
    days_opts = ''.join(f'<option value="{d}"{" selected" if days==d else ""}>{d} days</option>' for d in [7, 14, 21, 30, 60, 90])

    # Table rows
    dir_colors = {'BULL': 'bull', 'BEAR': 'bear', 'NEUTRAL': ''}
    rows = ''
    for p in patterns[:200]:
        t = pd.Timestamp(p['time'])
        dcl = dir_colors.get(p.get('direction', ''), '')
        z = p.get('fiz_zscore') or p.get('fiz_flow_zscore') or 0
        ok = p.get('success')
        ok_html = '<span class="success">&#10003;</span>' if ok else ('<span class="fail">&#10007;</span>' if ok is False else '-')
        rows += (
            f'<tr><td>{t.strftime("%d.%m %H:%M")}</td>'
            f'<td><span class="tag tag-{p["type"].lower()}">{p["type"]}</span></td>'
            f'<td class="{dcl}">{p.get("direction","-")}</td>'
            f'<td>{p.get("entry_price","")}</td>'
            f'<td>{p.get("fiz_net","")}</td>'
            f'<td>{z:.1f}</td>'
            f'<td>{p.get("fwd_ret_1h","")}</td>'
            f'<td>{p.get("fwd_ret_3h","")}</td>'
            f'<td>{p.get("fwd_ret_6h","")}</td>'
            f'<td>{ok_html}</td></tr>'
        )

    table = (
        f'<h3 style="padding:12px 12px 4px;font-size:14px;color:#555;">&#128203; Patterns ({len(patterns)})</h3>'
        '<table><thead><tr>'
        '<th>Time</th><th>Type</th><th>Dir</th><th>Price</th><th>FIZ net</th><th>z</th><th>1h</th><th>3h</th><th>6h</th><th>OK</th>'
        '</tr></thead><tbody>' + rows + '</tbody></table>'
    )

    # Chart data JSON
    eq_points, eq_total = calc_equity(patterns, capital=10000)
    chart_data = json.dumps({
        'barData': df_to_json(df),
        'patterns': patterns_to_json(patterns),
        'equity': eq_points,
        'equitySummary': eq_total,
    }, ensure_ascii=False, default=str)

    html = html.replace('__SYMBOL_OPTIONS__', sym_opts)
    html = html.replace('__DAYS_OPTIONS__', days_opts)
    html = html.replace('__STATS__', stats)
    html = html.replace('__TABLE__', table)
    html = html.replace('__CHART_DATA__', chart_data)

    return HTMLResponse(html, headers={'Cache-Control': 'no-store, no-cache, must-revalidate, max-age=0'})


app = Starlette(routes=[
    Route('/', index),
    Route('/v2', index),
    Route('/api/data', api_data),
])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8080)
    parser.add_argument('--host', default='0.0.0.0')
    args = parser.parse_args()
    print(f"Dashboard: http://{args.host}:{args.port}")
    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port, log_level='info')


if __name__ == '__main__':
    main()
