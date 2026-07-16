#!/usr/bin/env python3 -u
"""Полный sweep всех 113 indicative continuous из MT5 FINAM."""
import sys, json, subprocess
from datetime import datetime
sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures/strategies/dragon')
sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures/strategies')
from dragon.prod.engine import check_signal

TC, CAPITAL = 4, 200000
MT5_PATH = "C:/Program Files/MetaTrader 5 FINAM/terminal64.exe"


def get_cont():
    script = '''
import MetaTrader5 as mt5, json, sys
mt5.initialize(path=r"%s")
symbols = mt5.symbols_get()
cont = []
for s in symbols:
    if s.path.startswith("Indicative continuous"):
        info = mt5.symbol_info(s.name)
        if info:
            cont.append({
                "name": s.name, "trade_mode": info.trade_mode,
                "min_step": float(info.point*10) if info.point else 0.01,
                "contract_size": info.trade_contract_size or 1,
                "margin": info.margin_initial or 0
            })
print(json.dumps(sorted(cont, key=lambda x: x["name"])))
mt5.shutdown()
''' % MT5_PATH
    r = subprocess.run(['wine', 'python', '-u', '-c', script], capture_output=True, text=True, timeout=30)
    return json.loads(r.stdout.strip().split('\n')[-1])


def test_one(name, ms):
    script = "import MetaTrader5 as mt5, json, sys\n"
    script += "from datetime import datetime\n"
    script += 'mt5.initialize(path=r"%s")\n' % MT5_PATH
    script += 'rates = mt5.copy_rates_range("%s", mt5.TIMEFRAME_M1, datetime(2025,7,16), datetime.now())\n' % name
    script += 'if rates is None or len(rates) < 100:\n'
    script += '    print("null")\n'
    script += '    mt5.shutdown()\n'
    script += '    exit()\n'
    script += 'bars = [{"ts":str(datetime.fromtimestamp(r[0])),"opn":float(r[1]),"hi":float(r[2]),"lo":float(r[3]),"prc":float(r[4])} for r in rates]\n'
    script += 'print(json.dumps({"bars":bars}))\n'
    script += 'mt5.shutdown()\n'
    
    r = subprocess.run(['wine', 'python', '-u', '-c', script], capture_output=True, text=True, timeout=60)
    out = r.stdout.strip().split('\n')[-1]
    try:
        data = json.loads(out)
    except:
        return None
    if not data or 'bars' not in data:
        return None
    
    raw = data['bars']
    bars = []
    for b in raw:
        ts = datetime.fromisoformat(b['ts'])
        if ts.weekday() >= 5: continue
        h, m = ts.hour, ts.minute
        if h < 15 or h > 23 or (h == 23 and m > 45): continue
        bars.append({'ts': ts, 'opn': b['opn'], 'hi': b['hi'], 'lo': b['lo'], 'prc': b['prc']})
    if len(bars) < 100:
        return None
    
    sp = 1.0
    dp = {'impulse_pct': 0.3, 'retrace_max_pct': 70, 'hump_extension': 0.1, 'lookback': 100}
    ta, tt, sl = 0.015, 0.005, 0.01
    
    trades, op = [], None
    m5 = []
    for i in range(30, len(bars)):
        if i % 5 == 0:
            g = bars[i-5:i]
            if len(g) >= 3:
                m5.append({'opn': g[0]['opn'], 'hi': max(b['hi'] for b in g),
                           'lo': min(b['lo'] for b in g), 'prc': g[-1]['prc']})
        bar = bars[i]
        if op:
            ep = op['ep']; ex = None
            slev = ep*(1-sl) if op['dir']=='long' else ep*(1+sl)
            if (op['dir']=='long' and bar['lo']<=slev) or (op['dir']=='short' and bar['hi']>=slev):
                ex = slev
            if not ex and i%5==4:
                if not op.get('tr'):
                    if (op['dir']=='long' and bar['hi']>=ep*(1+ta)) or (op['dir']=='short' and bar['lo']<=ep*(1-ta)):
                        op['tr']=True; op['tl']=bar['hi']*(1-tt) if op['dir']=='long' else bar['lo']*(1+tt)
                if op.get('tr'):
                    if (op['dir']=='long' and bar['lo']<=op['tl']) or (op['dir']=='short' and bar['hi']>=op['tl']):
                        ex = op['tl']
            if not ex and i-op['bi']>=60:
                ex = bar['prc']
            if ex is not None:
                raw = (ex-ep)/ms*sp - TC
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
    losses = [p for p in trades if p <= 0]
    wr = len(wins)/n*100
    total = sum(trades)
    tp = sum(wins); tn = sum(abs(p) for p in losses)
    pf = tp/tn if tn else 0
    cap = CAPITAL; peak = cap; mdd = 0
    for p in trades:
        cap += p; peak = max(peak, cap); mdd = max(mdd, (peak-cap)/peak*100)
    ret = (cap-CAPITAL)/CAPITAL*100
    cm = ret/mdd if mdd > 0 else 0
    return {'name': name, 'n': n, 'wr': round(wr,1), 'pnl': round(total),
            'pf': round(pf,2), 'mdd': round(mdd,2), 'bars': len(bars),
            'min_step': ms, 'calmar': round(cm,1)}


if __name__ == '__main__':
    print('Getting 113 continuous symbols...', flush=True)
    all_cont = get_cont()
    print('Found: %d' % len(all_cont), flush=True)
    
    results = []
    for i, cs in enumerate(all_cont):
        name = cs['name']
        ms_v = cs['min_step']
        print('[%d/%d] %s ms=%s' % (i+1, len(all_cont), name, ms_v), end=' ', flush=True)
        res = test_one(name, ms_v)
        if res:
            results.append(res)
            print('OK: %d tr pnl=%+d wr=%.1f%% pf=%.2f mdd=%.2f%%' % (
                res['n'], res['pnl'], res['wr'], res['pf'], res['mdd']), flush=True)
        else:
            print('SKIP', flush=True)
    
    print('\n' + '='*80, flush=True)
    print('TOP by PnL (PF>1.0, MDD<20%%)')
    print('='*80, flush=True)
    good = [r for r in results if r['pf'] > 1.0 and r['mdd'] < 20 and r['n'] >= 10]
    for r in sorted(good, key=lambda x: x['pnl'], reverse=True):
        print('  %s n=%d wr=%.1f%% pnl=%+d pf=%.2f mdd=%.2f%% calmar=%.1f' % (
            r['name'].ljust(20), r['n'], r['wr'], r['pnl'], r['pf'], r['mdd'], r['calmar']), flush=True)
