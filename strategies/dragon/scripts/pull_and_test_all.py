#!/usr/bin/env python3 -u
"""Pull ALL PG tickers from MT5, test dragon, add to portfolio."""
import sys, os, json, subprocess
from datetime import datetime
sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures/strategies/dragon')
sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures/strategies')
from dragon.prod.engine import check_signal

TC = 4
MT5_PATH = "C:/Program Files/MetaTrader 5 FINAM/terminal64.exe"


def get_pg_tickers():
    """Get all tickers from PG with GO."""
    import psycopg2
    conn = psycopg2.connect(host='10.0.0.60', port=5432, dbname='moex', user='postgres')
    cur = conn.cursor()
    cur.execute("SELECT ticker, go, min_step, step_price FROM futures.ticker_specs WHERE go > 0 ORDER BY ticker")
    rows = cur.fetchall()
    cur.close(); conn.close()
    return {r[0]: {'go': float(r[1]), 'ms': float(r[2]) if r[2] else 0.01, 'sp': float(r[3]) if r[3] else 1.0} for r in rows}


def get_mt5_cont_names():
    """Get ALL indicative continuous names from MT5."""
    script = '''
import MetaTrader5 as mt5, json, sys
mt5.initialize(path=r"%s")
symbols = mt5.symbols_get()
cont = []
for s in symbols:
    if s.path.startswith("Indicative continuous"):
        info = mt5.symbol_info(s.name)
        if info:
            cont.append({"name": s.name, "ms": float(info.point*10) if info.point else 0.01,
                        "sp": float(info.trade_tick_value) if info.trade_tick_value else 1.0})
print(json.dumps(cont))
mt5.shutdown()
''' % MT5_PATH
    r = subprocess.run(['wine', 'python', '-u', '-c', script], capture_output=True, text=True, timeout=30)
    return json.loads(r.stdout.strip().split('\\n')[-1])


def pull_bars(name):
    """Pull 1 year M1 from MT5."""
    s = "import MetaTrader5 as mt5, json, sys\n"
    s += "from datetime import datetime\n"
    s += 'mt5.initialize(path=r"' + MT5_PATH + '")\n'
    s += 'rates = mt5.copy_rates_range("' + name + '", mt5.TIMEFRAME_M1, datetime(2025,7,16), datetime.now())\n'
    s += 'if rates is None or len(rates)<100: print("null"); mt5.shutdown(); exit()\n'
    s += 'bars = [{"ts":str(datetime.fromtimestamp(r[0])),"opn":float(r[1]),"hi":float(r[2]),"lo":float(r[3]),"prc":float(r[4])} for r in rates]\n'
    s += 'print(json.dumps({"bars":bars})); mt5.shutdown()'
    r = subprocess.run(['wine', 'python', '-u', '-c', s], capture_output=True, text=True, timeout=60)
    try:
        return json.loads(r.stdout.strip().split('\\n')[-1])
    except:
        return None


def test_dragon(name, bars, ms, sp):
    """Run dragon backtest, return metrics."""
    filtered = []
    for b in bars:
        ts = datetime.fromisoformat(b['ts'])
        if ts.weekday() >= 5: continue
        h, m = ts.hour, ts.minute
        if h < 15 or h > 23 or (h == 23 and m > 45): continue
        filtered.append({'ts': ts, 'opn': b['opn'], 'hi': b['hi'], 'lo': b['lo'], 'prc': b['prc']})
    if len(filtered) < 100:
        return None
    
    dp = {'impulse_pct': 0.3, 'retrace_max_pct': 70, 'hump_extension': 0.1, 'lookback': 100}
    ta, tt, sl = 0.015, 0.005, 0.01
    
    trades, op = [], None
    m5 = []
    for i in range(30, len(filtered)):
        if i % 5 == 0:
            g = filtered[i-5:i]
            if len(g) >= 3:
                m5.append({'opn': g[0]['opn'], 'hi': max(b['hi'] for b in g),
                           'lo': min(b['lo'] for b in g), 'prc': g[-1]['prc']})
        bar = filtered[i]
        if op:
            ep = op['ep']; ex = None
            slev = ep*(1-sl) if op['dir']=='long' else ep*(1+sl)
            if (op['dir']=='long' and bar['lo']<=slev) or (op['dir']=='short' and bar['hi']>=slev): ex=slev
            if not ex and i%5==4:
                if not op.get('tr'):
                    if (op['dir']=='long' and bar['hi']>=ep*(1+ta)) or (op['dir']=='short' and bar['lo']<=ep*(1-ta)):
                        op['tr']=True; op['tl']=bar['hi']*(1-tt) if op['dir']=='long' else bar['lo']*(1+tt)
                if op.get('tr'):
                    if (op['dir']=='long' and bar['lo']<=op['tl']) or (op['dir']=='short' and bar['hi']>=op['tl']): ex=op['tl']
            if not ex and i-op['bi']>=60: ex=bar['prc']
            if ex is not None:
                raw = ((ex-ep)/ms*sp - TC)
                trades.append(raw if op['dir']=='long' else -raw)
                op = None
        if i%5==0 and not op:
            if len(m5)<6: continue
            slc = m5[-110:]
            sig = check_signal({'prc':slc[-1]['prc'],'bars_list':slc}, name, dp)
            if sig:
                op = {'bi':i,'ep':sig['entry_price'],'dir':sig['direction'],'tr':False,'tl':None}
    
    n = len(trades)
    if n < 5: return None
    wins = [p for p in trades if p > 0]
    total = sum(trades)
    pf = sum(wins)/sum(abs(p) for p in trades if p<=0) if any(p<=0 for p in trades) else float('inf')
    return {'name': name, 'n': n, 'pnl': round(total), 'pf': round(pf,2), 'bars': len(filtered)}


if __name__ == '__main__':
    print('Getting PG tickers...', flush=True)
    pg = get_pg_tickers()
    print(f'  {len(pg)} tickers with GO', flush=True)
    
    print('Getting MT5 continuous names...', flush=True)
    cont = get_mt5_cont_names()
    cont_map = {c['name']: c for c in cont}
    print(f'  {len(cont)} continuous symbols', flush=True)
    
    # Основные тикеры уже в портфеле (пропускаем)
    done = set(['BR','Si','CR','GZ','MM','NG','RN','SV','GD'])
    
    results = []
    for ticker, spec in sorted(pg.items()):
        if ticker in done:
            continue
        ms, sp = spec['ms'], spec['sp']
        
        # Try to find matching MT5 name
        # Try ALLFUT+name pattern
        mt5_name = 'ALLFUT' + ticker.upper()
        
        # Check if this symbol exists in MT5 continuous
        if mt5_name not in cont_map:
            print(f'  {ticker:6s}: no MT5 continuous match', flush=True)
            continue
        
        print(f'  {ticker:6s} (→ {mt5_name})  pulling...', end=' ', flush=True)
        data = pull_bars(mt5_name)
        if not data or 'bars' not in data:
            print('no data', flush=True)
            continue
        
        res = test_dragon(ticker, data['bars'], ms, sp)
        if not res:
            print('skip (<5 trades)', flush=True)
            continue
        
        results.append(res)
        print(f'n={res["n"]:4d}  pnl={res["pnl"]:+8.0f}  pf={res["pf"]:.2f}', flush=True)
    
    print(f'\n=== NEW TICKERS WITH EDGE (PF>1.0) ===', flush=True)
    good = [r for r in results if r['pf'] > 1.0]
    for r in sorted(good, key=lambda x: x['pnl'], reverse=True):
        print(f'  {r["name"]:6s}  n={r["n"]:4d}  pnl={r["pnl"]:+8.0f}  pf={r["pf"]:.2f}  bars={r["bars"]}', flush=True)
