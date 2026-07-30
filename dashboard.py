#!/usr/bin/env python3
"""
Dashboard for MOEX futures - v5 (sim) + v6 (dom) + DOM + MT5 snapshot.
Usage: python3 dashboard.py [port]
"""
import os, json, html
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timezone

PG = dict(host='10.0.0.60', port=5432, dbname='moex', user='postgres', connect_timeout=3)
CH_URL = "http://10.0.0.60:8123"

def q(query, params=None):
    import psycopg2
    conn = psycopg2.connect(**PG)
    cur = conn.cursor()
    cur.execute(query, params or ())
    rows = cur.fetchall()
    cur.close(); conn.close()
    return rows

def ch(query):
    import urllib.request, urllib.parse
    try:
        req = urllib.request.Request(f"{CH_URL}/?query={urllib.parse.quote(query)}&format=JSON", method='GET')
        resp = urllib.request.urlopen(req, timeout=3)
        data = json.loads(resp.read())
        return data.get('data', [])
    except: return []

def get_state(sk):
    rows = q(f"SELECT capital, equity, peak, mtm_equity, mtm_peak, positions_json, updated_at FROM futures.paper_state_{sk} ORDER BY updated_at DESC LIMIT 1")
    if not rows: return None
    r = rows[0]
    pos = json.loads(r[5]) if r[5] else []
    return {'capital': float(r[0]), 'equity': float(r[1]), 'peak': float(r[2]),
            'mtm_eq': float(r[3] or r[1]), 'mtm_pk': float(r[4] or r[2]),
            'positions': pos, 'pos_count': len(pos), 'ts': str(r[6])[:19]}

def get_trades(sk, limit=10):
    rows = q(f"SELECT ticker, strategy, direction, pnl_rub, exit_reason, exit_time FROM futures.paper_trades_{sk} ORDER BY exit_time DESC NULLS LAST LIMIT {limit}")
    return rows

def get_latest_bars():
    rows = q("SELECT ticker, max(bt) as bt, count(*) as bars FROM futures.bars_1m GROUP BY ticker ORDER BY bt DESC")
    return [(r[0], str(r[1])[:19] if r[1] else '-', r[2]) for r in rows]

def get_dom_stats():
    rows = q("SELECT ticker, max(ts) as ts, count(*) as rows FROM futures.dom WHERE ts > now() - interval '5 minute' GROUP BY ticker ORDER BY ts DESC")
    return [(r[0], str(r[1])[:19] if r[1] else '-', r[2]) for r in rows]

def get_specs():
    """Get ticker specs from PG."""
    rows = q("SELECT ticker, min_step, step_price FROM futures.ticker_specs")
    return {r[0]: {'ms': float(r[1] or 0.01), 'sp': float(r[2] or 1.0)} for r in rows}

def get_current_prices():
    """Get latest close prices from PG bars_1m."""
    rows = q("""
        SELECT DISTINCT ON (ticker) ticker, prc
        FROM futures.bars_1m
        ORDER BY ticker, bt DESC
    """)
    return {r[0]: float(r[1]) for r in rows if r[1]}

def calc_upnl(pos, prices, specs):
    """Calculate unrealized PnL for a position."""
    ticker = pos['ticker']
    price = prices.get(ticker)
    if not price:
        return 0, 0
    entry = pos['entry_price']
    contracts = pos.get('contracts', 1)
    s = specs.get(ticker, {'ms': 0.01, 'sp': 1.0})
    ms = s['ms']
    sp = s['sp']
    if ms <= 0:
        return 0, 0
    if pos['direction'] == 'short':
        ticks = (entry - price) / ms
        pnl_per_ct = ticks * sp
    else:
        ticks = (price - entry) / ms
        pnl_per_ct = ticks * sp
    total = pnl_per_ct * contracts
    # subtract commission
    total -= pos.get('commission', 0)
    return round(total, 0), round(pnl_per_ct, 0)

def get_mt5_account():
    rows = q("SELECT ts, balance, equity, margin, margin_free, margin_level FROM mt5_account ORDER BY ts DESC LIMIT 1")
    if not rows: return None
    r = rows[0]
    return {'ts': str(r[0])[:19], 'balance': float(r[1] or 0), 'equity': float(r[2] or 0),
            'margin': float(r[3] or 0), 'margin_free': float(r[4] or 0), 'margin_level': float(r[5] or 0)}

def calc_dd(pk, eq):
    return (pk - eq) / pk * 100 if pk > 0 else 0

def state_card(name, sk, state, trades, prices, specs):
    if not state:
        return f'<div class="card"><h2>{name}</h2><div class="empty">Нет данных</div></div>'
    dd = calc_dd(state['peak'], state['equity'])
    mtm_dd = calc_dd(state['mtm_pk'], state['mtm_eq'])
    total_pnl = state['equity'] - state['capital']
    ret = total_pnl / state['capital'] * 100
    colors = {'v5': '#4a9eff', 'v6': '#ff6b6b'}
    color = colors.get(sk, '#888')
    pos_html = ''
    total_upnl = 0
    for p in state['positions']:
        tkr = p['ticker']
        price = prices.get(tkr)
        s = specs.get(tkr, {'ms': 0.01, 'sp': 1.0})
        ms = s['ms']; sp = s['sp']
        if price and ms > 0:
            if p['direction'] == 'short':
                upnl_ct = (p['entry_price'] - price) / ms * sp
            else:
                upnl_ct = (price - p['entry_price']) / ms * sp
            upnl = upnl_ct * p.get('contracts', 1)
        else:
            upnl = 0
        total_upnl += upnl
        upnl_str = f'{upnl:+.0f}' if upnl != 0 else '0'
        cls = 'positive' if upnl > 0 else ('negative' if upnl < 0 else '')
        pos_html += f'<div class="pos">{p["direction"].upper()} {tkr} {p["strategy"]} entry={p["entry_price"]} <span class="{cls}">UPnL={upnl_str}₽</span></div>'
    equity_with_upnl = state['equity'] + total_upnl
    
    trades_html = ''
    for t in trades[:5]:
        tkr, strat, direc, pnl, reason, ts = t
        sign = '🟢' if pnl and pnl > 0 else ('🔴' if pnl and pnl < 0 else '⚪')
        pnl_str = f'{pnl:+.0f}' if pnl else '0'
        trades_html += f'<div class="trade">{sign} {tkr} {direc} {strat} PnL={pnl_str} ({reason})</div>'
    
    return f'''
    <div class="card" style="border-left: 4px solid {color}">
        <h2>{name}</h2>
        <div class="state-row">
            <span class="label">Капитал</span><span class="value">{state["capital"]:>.0f}₽</span>
        </div>
        <div class="state-row">
            <span class="label">Equity (с UPnL)</span><span class="value">{equity_with_upnl:>.0f}₽</span>
        </div>
        <div class="state-row">
            <span class="label">Balance (realized)</span><span class="value">{state["equity"]:>.0f}₽</span>
        </div>
        <div class="state-row">
            <span class="label">Peak</span><span class="value">{state["peak"]:>.0f}₽</span>
        </div>
        <div class="state-row">
            <span class="label">Cash DD</span><span class="value {'negative' if dd > 5 else ''}">{dd:.1f}%</span>
        </div>
        <div class="state-row">
            <span class="label">MTM DD</span><span class="value {'negative' if mtm_dd > 5 else ''}">{mtm_dd:.1f}%</span>
        </div>
        <div class="state-row">
            <span class="label">PnL</span><span class="value {'positive' if total_pnl > 0 else 'negative'}">{total_pnl:+.0f}₽ ({ret:+.1f}%)</span>
        </div>
        <div class="state-row">
            <span class="label">Позиции</span><span class="value">{state["pos_count"]}</span>
        </div>
        <div class="state-row">
            <span class="label">Обновлено</span><span class="value">{state["ts"]}</span>
        </div>
        <h3>Открытые позиции</h3>
        {pos_html if state["positions"] else '<div class="empty">Нет открытых позиций</div>'}
        <div class="state-row">
            <span class="label">Unrealized PnL</span><span class="value {'positive' if total_upnl > 0 else 'negative'}">{total_upnl:+.0f}₽</span>
        </div>
        <h3>Последние сделки</h3>
        {trades_html if trades else '<div class="empty">Нет сделок</div>'}
    </div>'''

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            page = self._build_page()
            self.wfile.write(page.encode('utf-8'))
        except Exception as e:
            self.send_response(500)
            self.send_header('Content-Type', 'text/plain')
            self.end_headers()
            self.wfile.write(f'Error: {e}'.encode('utf-8'))
    
    def _build_page(self):
        state_v5 = get_state('portfolio_v5')
        state_v6 = get_state('portfolio_v6')
        trades_v5 = get_trades('portfolio_v5') if state_v5 else []
        trades_v6 = get_trades('portfolio_v6') if state_v6 else []
        prices = get_current_prices()
        specs = get_specs()
        bars = get_latest_bars()
        dom = get_dom_stats()
        acc = get_mt5_account()

        bars_html = ''
        for tkr, bt, cnt in bars[:11]:
            bars_html += f'<div class="bar-row"><span class="label">{tkr}</span><span class="value">{bt}</span><span class="value-small">{cnt} bars</span></div>'
        
        dom_html = ''
        for tkr, ts, cnt in dom[:11]:
            dom_html += f'<div class="bar-row"><span class="label">{tkr}</span><span class="value">{ts}</span><span class="value-small">{cnt} rows</span></div>'
        
        acc_html = ''
        if acc:
            acc_html = f'''
            <div class="card">
                <h2>MT5 Account (FINAM)</h2>
                <div class="state-row"><span class="label">Баланс</span><span class="value">{acc["balance"]:>.0f}₽</span></div>
                <div class="state-row"><span class="label">Equity</span><span class="value">{acc["equity"]:>.0f}₽</span></div>
                <div class="state-row"><span class="label">Margin</span><span class="value">{acc["margin"]:>.0f}₽</span></div>
                <div class="state-row"><span class="label">Free Margin</span><span class="value">{acc["margin_free"]:>.0f}₽</span></div>
                <div class="state-row"><span class="label">Обновлено</span><span class="value">{acc["ts"]}</span></div>
            </div>'''

        html_page = f'''<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta http-equiv="refresh" content="30">
<title>MOEX Futures Dashboard</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif; background:#0d1117; color:#c9d1d9; padding:20px; }}
h1 {{ color:#58a6ff; font-size:24px; margin-bottom:20px; }}
h2 {{ color:#8b949e; font-size:16px; margin-bottom:12px; border-bottom:1px solid #21262d; padding-bottom:6px; }}
h3 {{ color:#8b949e; font-size:13px; margin:12px 0 6px; }}
.grid {{ display:flex; gap:16px; flex-wrap:wrap; }}
.card {{ background:#161b22; border:1px solid #30363d; border-radius:8px; padding:16px; min-width:320px; flex:1; }}
.state-row {{ display:flex; justify-content:space-between; padding:4px 0; font-size:13px; border-bottom:1px solid #21262d; }}
.label {{ color:#8b949e; }}
.value {{ color:#c9d1d9; font-weight:600; }}
.value-small {{ color:#8b949e; font-size:11px; }}
.positive {{ color:#3fb950; }}
.negative {{ color:#f85149; }}
.pos {{ padding:3px 8px; margin:3px 0; background:#1c2128; border-radius:4px; font-size:12px; }}
.trade {{ padding:2px 0; font-size:12px; }}
.bar-row {{ display:flex; justify-content:space-between; padding:2px 0; font-size:12px; border-bottom:1px solid #1c2128; }}
.empty {{ color:#484f58; font-style:italic; font-size:12px; padding:8px 0; }}
.footer {{ color:#484f58; font-size:11px; margin-top:20px; text-align:center; }}
</style></head><body>
<h1>📊 MOEX Futures — Paper Trader Dashboard</h1>
<div class="grid">
            {state_card('v5 (BrokerSim)', 'v5', state_v5, trades_v5, prices, specs)}
            {state_card('v6 (BrokerDOM)', 'v6', state_v6, trades_v6, prices, specs)}
    {acc_html}
</div>
<div class="grid" style="margin-top:16px">
    <div class="card">
        <h2>📈 M1 Бары (FINAM)</h2>
        {bars_html if bars else '<div class="empty">Нет данных</div>'}
    </div>
    <div class="card">
        <h2>📊 Стакан (DOM) — последние 5 мин</h2>
        {dom_html if dom else '<div class="empty">Нет данных</div>'}
    </div>
</div>
<div class="footer">
    Обновление каждые 30 сек · {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} IRKT
</div>
</body></html>'''
        return html_page
    def log_message(self, *a): pass

if __name__ == '__main__':
    port = int(os.sys.argv[1]) if len(os.sys.argv) > 1 else 8085
    server = HTTPServer(('0.0.0.0', port), Handler)
    print(f"Dashboard: http://10.0.0.60:{port}")
    server.serve_forever()
