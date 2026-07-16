#!/usr/bin/env python3 -u
"""Финальный портфель — MT5 specs, топ тикеры, реинвест."""
import sys, os, json, subprocess
from datetime import datetime
sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures/strategies/dragon')
sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures/strategies')
from dragon.prod.engine import check_signal

TC, CAPITAL = 4, 200000
MT5_PATH = "C:/Program Files/MetaTrader 5 FINAM/terminal64.exe"

# MT5 specs from trade_tick_value
MT5_SPECS = {
    'ALLFUTBR': {'ms': 0.1, 'sp': 1.0}, 'ALLFUTSi': {'ms': 10.0, 'sp': 1.0},
    'ALLFUTCNY': {'ms': 0.01, 'sp': 1.0}, 'ALLFUTGAZR': {'ms': 10.0, 'sp': 1.0},
    'ALLFUTGOLD': {'ms': 1.0, 'sp': 1.0}, 'ALLFUTNG': {'ms': 0.01, 'sp': 1.0},
    'ALLFUTROSN': {'ms': 10.0, 'sp': 1.0}, 'ALLFUTSILV': {'ms': 0.1, 'sp': 1.0},
    'ALLFUTAFLT': {'ms': 10.0, 'sp': 1.0}, 'ALLFUTLKOH': {'ms': 10.0, 'sp': 1.0},
    'ALLFUTSBRF': {'ms': 10.0, 'sp': 1.0}, 'ALLFUTTATN': {'ms': 10.0, 'sp': 1.0},
    'ALLFUTNOTK': {'ms': 10.0, 'sp': 1.0}, 'ALLFUTTRNF': {'ms': 10.0, 'sp': 1.0},
    'ALLFUTHYDR': {'ms': 10.0, 'sp': 1.0}, 'ALLFUTFEES': {'ms': 10.0, 'sp': 1.0},
    'ALLFUTRTKM': {'ms': 10.0, 'sp': 1.0}, 'ALLFUTMTSI': {'ms': 10.0, 'sp': 1.0},
    'ALLFUTSBPR': {'ms': 10.0, 'sp': 1.0}, 'ALLFUTVTBR': {'ms': 10.0, 'sp': 1.0},
    'ALLFUTMIX': {'ms': 10.0, 'sp': 25.0}, 'ALLFUTNASD': {'ms': 10.0, 'sp': 1.0},
    'ALLFUTSPYF': {'ms': 0.1, 'sp': 1.0}, 'ALLFUTSNGR': {'ms': 10.0, 'sp': 1.0},
    'ALLFUTSNGP': {'ms': 10.0, 'sp': 1.0}, 'ALLFUTMGNT': {'ms': 10.0, 'sp': 1.0},
    'ALLFUTEu': {'ms': 10.0, 'sp': 1.0}, 'ALLFUTES': {'ms': 0.1, 'sp': 1.0},
    'ALLFUTED': {'ms': 0.001, 'sp': 1.0}, 'ALLFUTBTC': {'ms': 10.0, 'sp': 1.0},
    'ALLFUTETH': {'ms': 0.1, 'sp': 1.0}, 'ALLFUTHANG': {'ms': 10.0, 'sp': 1.0},
    'ALLFUTRTSI': {'ms': 10.0, 'sp': 100.0},
    # CH names for our 9 original tickers
    'BR': {'ms': 0.01, 'sp': 7.66647}, 'Si': {'ms': 1.0, 'sp': 1.0},
    'CR': {'ms': 0.01, 'sp': 1.0}, 'GZ': {'ms': 1.0, 'sp': 1.0},
    'GD': {'ms': 0.05, 'sp': 1.0}, 'MM': {'ms': 0.01, 'sp': 1.0},
    'NG': {'ms': 0.001, 'sp': 7.66647}, 'RN': {'ms': 1.0, 'sp': 1.0},
    'SV': {'ms': 0.01, 'sp': 7.66647},
}


def pull(name):
    s = "import MetaTrader5 as mt5, json, sys\n"
    s += "from datetime import datetime\n"
    s += 'mt5.initialize(path=r"' + MT5_PATH + '")\n'
    s += 'rates = mt5.copy_rates_range("' + name + '", mt5.TIMEFRAME_M1, datetime(2025,7,16), datetime.now())\n'
    s += 'if rates is None or len(rates) < 100: print("null"); mt5.shutdown(); exit()\n'
    s += 'bars = [{"ts":str(datetime.fromtimestamp(r[0])),"opn":float(r[1]),"hi":float(r[2]),"lo":float(r[3]),"prc":float(r[4])} for r in rates]\n'
    s += 'print(json.dumps({"bars":bars})); mt5.shutdown()'
    r = subprocess.run(['wine', 'python', '-u', '-c', s], capture_output=True, text=True, timeout=60)
    try:
        return json.loads(r.stdout.strip().split('\n')[-1])
    except:
        return None


def backtest(name, bars, specs, contracts=1, reinvest=False, eq_start=CAPITAL):
    ms, sp = specs['ms'], specs['sp']
    dp = {'impulse_pct': 0.3, 'retrace_max_pct': 70, 'hump_extension': 0.1, 'lookback': 100}
    ta, tt, sl = 0.015, 0.005, 0.01

    trades, op = [], None
    m5, cur_cont = [], contracts
    cur_eq = eq_start
    peak_eq = eq_start
    daily_high = eq_start

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
            if (op['dir']=='long' and bar['lo']<=slev) or (op['dir']=='short' and bar['hi']>=slev): ex=slev
            if not ex and i%5==4:
                if not op.get('tr'):
                    if (op['dir']=='long' and bar['hi']>=ep*(1+ta)) or (op['dir']=='short' and bar['lo']<=ep*(1-ta)):
                        op['tr']=True; op['tl']=bar['hi']*(1-tt) if op['dir']=='long' else bar['lo']*(1+tt)
                if op.get('tr'):
                    if (op['dir']=='long' and bar['lo']<=op['tl']) or (op['dir']=='short' and bar['hi']>=op['tl']): ex=op['tl']
            if not ex and i-op['bi']>=60: ex=bar['prc']
            if ex is not None:
                raw = ((ex-ep)/ms*sp - TC) * cur_cont
                raw = raw if op['dir']=='long' else -raw
                trades.append(raw)
                cur_eq += raw
                peak_eq = max(peak_eq, cur_eq)
                daily_high = max(daily_high, cur_eq)
                op = None
        if i%5==0 and not op:
            if len(m5)<6: continue
            slc = m5[-110:]
            sig = check_signal({'prc':slc[-1]['prc'],'bars_list':slc}, name, dp)
            if sig:
                entry = sig['entry_price']
                if reinvest:
                    risk = cur_eq * 0.01
                    sl_cost = entry * sl / ms * sp + TC
                    cur_cont = max(1, int(risk / sl_cost)) if sl_cost > 0 else contracts
                else:
                    cur_cont = contracts
                op = {'bi':i, 'ep':entry, 'dir':sig['direction'], 'tr':False, 'tl':None}
    return trades


if __name__ == '__main__':
    # Top candidates from sweep (PF>1.2, MDD low, enough trades)
    portfolio_candidates = [
        ('BR', 'CH', 2), ('NG', 'CH', 2), ('RN', 'CH', 2), ('SV', 'CH', 2),
        ('MM', 'CH', 2), ('Si', 'CH', 10), ('CR', 'CH', 100), ('GZ', 'CH', 10),
        ('ALLFUTROSN', 'MT5', 2), ('ALLFUTSILV', 'MT5', 2), ('ALLFUTBR', 'MT5', 2),
    ]

    print('=== FINAL PORTFOLIO — MT5 Continuous, reinvest 1% ===', flush=True)
    print('', flush=True)

    for re_inv, label in [(False, 'Fixed contracts (scaled)'), (True, 'Reinvest 1%')]:
        print(f'\n--- {label} ---', flush=True)
        all_trades, cur_eq = [], CAPITAL

        for name, src, base_cont in portfolio_candidates:
            if src == 'CH':
                from grid_continuous import load_bars
                bars = load_bars(name, 365)
            else:
                data = pull(name)
                if not data or 'bars' not in data: continue
                raw = data['bars']
                bars = []
                for b in raw:
                    ts = datetime.fromisoformat(b['ts'])
                    if ts.weekday() >= 5: continue
                    h, m = ts.hour, ts.minute
                    if h < 15 or h > 23 or (h == 23 and m > 45): continue
                    bars.append({'ts': ts, 'opn': b['opn'], 'hi': b['hi'], 'lo': b['lo'], 'prc': b['prc']})
                if len(bars) < 100: continue

            name2 = name if src == 'CH' else name
            trades = backtest(name2, bars, MT5_SPECS[name2], base_cont, re_inv, cur_eq)
            n = len(trades)
            if n < 5: continue
            wins = [p for p in trades if p > 0]
            total = sum(trades)
            pf = sum(wins)/sum(abs(p) for p in trades if p<=0) if any(p<=0 for p in trades) else float('inf')
            peak, mdd = CAPITAL, 0
            eq_tmp = CAPITAL
            for p in trades:
                eq_tmp += p
                peak = max(peak, eq_tmp)
                mdd = max(mdd, (peak-eq_tmp)/peak*100)
            cur_eq += total
            print(f'  {name2:20s} ×{base_cont:3d} | tr={n:4d} pnl={total:+9.0f} pf={pf:.2f} mdd={mdd:.2f}%', flush=True)
            all_trades.extend(trades)

        if all_trades:
            wins = [p for p in all_trades if p > 0]
            total = sum(all_trades)
            n = len(all_trades)
            pf = sum(wins)/sum(abs(p) for p in all_trades if p<=0) if any(p<=0 for p in all_trades) else float('inf')
            peak, mdd = CAPITAL, 0
            eq = CAPITAL
            for p in all_trades:
                eq += p; peak = max(peak, eq); mdd = max(mdd, (peak-eq)/peak*100)
            ret = (eq-CAPITAL)/CAPITAL*100
            print(f'  {"="*55}')
            print(f'  PORTFOLIO ({len(all_trades)} tr)')
            print(f'  Capital: {CAPITAL:,} -> {eq:,.0f} ({ret:+.1f}%)')
            print(f'  PF: {pf:.2f} | MDD: {mdd:.2f}% | Calmar: {ret/mdd if mdd>0 else 0:.1f}')
