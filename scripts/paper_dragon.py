#!/usr/bin/env python3 -u
"""Dragon Paper Trader — dynamic portfolio, live M1 from MT5 FINAM."""
import sys, os, json, logging
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from dragon.prod.engine import check_signal

TC = 4
MT5_PATH = "C:/Program Files/MetaTrader 5 FINAM/terminal64.exe"

ALL_TICKERS = {
    'MM': {'ms': 0.05, 'sp': 0.5, 'go': 2165.21},
    'GZ': {'ms': 1.0, 'sp': 1.0, 'go': 2898.11},
    'SV': {'ms': 0.01, 'sp': 7.70611, 'go': 15353.35},
    'BR': {'ms': 0.01, 'sp': 7.70611, 'go': 17164.0},
    'NG': {'ms': 0.001, 'sp': 7.70611, 'go': 10259.52},
    'RN': {'ms': 1.0, 'sp': 1.0, 'go': 3847.51},
    'CR': {'ms': 0.001, 'sp': 1.0, 'go': 1821.72},
}
PRIORITY = ['MM', 'GZ', 'SV', 'BR', 'NG', 'RN', 'CR']
RISK_PCT = 7

log = logging.getLogger('paper_dragon')


def get_pg():
    import psycopg2
    return psycopg2.connect(host='10.0.0.60', port=5432, dbname='moex', user='postgres')


def ensure_tables():
    conn = get_pg()
    cur = conn.cursor()
    cur.execute("""
        CREATE SCHEMA IF NOT EXISTS paper;
        CREATE TABLE IF NOT EXISTS paper.state (
            strategy TEXT PRIMARY KEY,
            equity REAL NOT NULL DEFAULT 200000,
            peak_eq REAL NOT NULL DEFAULT 200000,
            mtm_mdd REAL NOT NULL DEFAULT 0,
            total_trades INT NOT NULL DEFAULT 0,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS paper.trades (
            id BIGSERIAL PRIMARY KEY,
            strategy TEXT NOT NULL,
            ticker TEXT NOT NULL,
            direction TEXT NOT NULL,
            entry REAL, exit REAL,
            contracts INT NOT NULL DEFAULT 1,
            pnl REAL, reason TEXT,
            entered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            exited_at TIMESTAMPTZ
        );
        CREATE TABLE IF NOT EXISTS paper.positions (
            strategy TEXT NOT NULL,
            ticker TEXT NOT NULL,
            direction TEXT NOT NULL,
            entry REAL NOT NULL,
            contracts INT NOT NULL DEFAULT 1,
            opened_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (strategy, ticker)
        );
    """)
    conn.commit()
    cur.close(); conn.close()


def select_tickers(equity):
    gl = equity * 0.5
    sel = []
    for t in PRIORITY:
        n = max(len(sel), 1)
        if ALL_TICKERS[t]['go'] * 2 <= gl / n:
            sel.append(t)
    return sel if sel else ['MM', 'GZ']


def load_state():
    conn = get_pg()
    cur = conn.cursor()
    cur.execute("SELECT equity, peak_eq, mtm_mdd, total_trades FROM paper.state WHERE strategy='dragon'")
    row = cur.fetchone()
    if row:
        eq, peak, mdd, trades = row
    else:
        eq, peak, mdd, trades = 200000.0, 200000.0, 0.0, 0
        cur.execute("INSERT INTO paper.state VALUES ('dragon', %s, %s, %s, %s, NOW()) ON CONFLICT DO NOTHING",
                    (eq, peak, mdd, trades))
        conn.commit()
    
    cur.execute("SELECT ticker, direction, entry, contracts FROM paper.positions WHERE strategy='dragon'")
    positions = {r[0]: {'dir': r[1], 'ep': r[2], 'contracts': r[3], 'bi': 0} for r in cur.fetchall()}
    
    alloc = {t: 1.0/len(select_tickers(eq)) for t in select_tickers(eq)}
    ticker_eq = {t: eq * alloc.get(t, 1/len(alloc)) for t in select_tickers(eq)}
    
    cur.close(); conn.close()
    return eq, peak, mdd, trades, positions, ticker_eq


def save_state(eq, peak, mdd, trades, positions):
    conn = get_pg()
    cur = conn.cursor()
    cur.execute("UPDATE paper.state SET equity=%s, peak_eq=%s, mtm_mdd=%s, total_trades=%s, updated_at=NOW() WHERE strategy='dragon'",
                (eq, peak, mdd, trades))
    cur.execute("DELETE FROM paper.positions WHERE strategy='dragon'")
    for t, p in positions.items():
        cur.execute("INSERT INTO paper.positions VALUES ('dragon', %s, %s, %s, %s, NOW())",
                    (t, p['dir'], p['ep'], p['contracts']))
    conn.commit()
    cur.close(); conn.close()


def fetch_bars(tickers):
    """Pull M1 bars for selected tickers from MT5."""
    import subprocess, json
    script = "import MetaTrader5 as mt5, json, sys\nfrom datetime import datetime\n"
    script += 'mt5.initialize(path=r"%s")\n' % MT5_PATH
    for t in tickers:
        mt5_name = 'ALLFUT' + t if t in ['CR','BR','NG','SV'] else ('MOEX' + t if t == 'MM' else ('ALLFUT' + {'GZ':'GAZR','RN':'ROSN'}.get(t, t)))
        # Use simple names for known ones
    name_map = {'MM': 'MOEXMM', 'GZ': 'ALLFUTGAZR', 'SV': 'ALLFUTSILV',
                'BR': 'ALLFUTBR', 'NG': 'ALLFUTNG', 'RN': 'ALLFUTROSN', 'CR': 'ALLFUTCNY'}
    
    result = {}
    for t in tickers:
        mt5_name = name_map.get(t, t)
        s = "import MetaTrader5 as mt5, json, sys\n"
        s += "from datetime import datetime\n"
        s += 'mt5.initialize(path=r"%s")\n' % MT5_PATH
        s += 'rates = mt5.copy_rates_from_pos("%s", mt5.TIMEFRAME_M1, 0, 5)\n' % mt5_name
        s += 'if rates is None: print("null"); mt5.shutdown(); exit()\n'
        s += 'bars = [{"ts":str(datetime.fromtimestamp(r[0])),"opn":float(r[1]),"hi":float(r[2]),"lo":float(r[3]),"prc":float(r[4]),"vol":int(r[5])} for r in rates]\n'
        s += 'print(json.dumps({"bars":bars})); mt5.shutdown()'
        
        r = subprocess.run(['wine', 'python', '-u', '-c', s], capture_output=True, text=True, timeout=30)
        try:
            data = json.loads(r.stdout.strip().split('\n')[-1])
            if 'bars' in data:
                result[t] = data['bars']
        except:
            pass
    return result


def tick():
    eq, peak, mdd, trades_count, positions, ticker_eq = load_state()
    tickers = select_tickers(eq)
    go_limit = eq * 0.5
    go_used = sum(ALL_TICKERS[t]['go'] * p['contracts'] for t, p in positions.items())
    
    bars = fetch_bars(tickers)
    if not bars:
        log.warning("No bars from MT5")
        return
    
    dp = {'impulse_pct': 0.3, 'retrace_max_pct': 70, 'hump_extension': 0.1, 'lookback': 100}
    sl, ta, tt = 0.01, 0.015, 0.005
    
    new_trades = []
    
    for t in tickers:
        if t not in bars or not bars[t]:
            continue
        bar = bars[t][-1]  # latest bar
        ms, sp, go = ALL_TICKERS[t]['ms'], ALL_TICKERS[t]['sp'], ALL_TICKERS[t]['go']
        
        # Tick: check SL/TP
        pos = positions.get(t)
        if pos:
            ep = pos['ep']
            ex = None
            slev = ep*(1-sl) if pos['dir']=='long' else ep*(1+sl)
            if (pos['dir']=='long' and bar['lo']<=slev) or (pos['dir']=='short' and bar['hi']>=slev):
                ex = slev
            if ex is not None:
                raw = ((ex-ep)/ms*sp - TC) * pos['contracts']
                pnl = raw if pos['dir']=='long' else -raw
                eq += pnl
                peak = max(peak, eq + sum(calc_mtm(p, bars[t][-1], ALL_TICKERS[t]) for t2, p in positions.items() if t2 == t))
                mdd = max(mdd, (peak - eq) / peak * 100)
                trades_count += 1
                new_trades.append((t, pos['dir'], ep, ex, pos['contracts'], pnl, 'sl'))
                del positions[t]
                go_used -= go * pos.get('contracts', 1)
                continue
        
        # Detect: check signal
        if t not in positions:
            # Build simple bars_list from last 100 M1 bars
            sig = check_signal({'prc': bar['prc'], 'bars_list': [
                {'opn': b['opn'], 'hi': b['hi'], 'lo': b['lo'], 'prc': b['prc']} for b in bars[t][-100:]
            ]}, t, dp)
            
            if sig:
                risk_a = ticker_eq.get(t, eq/len(tickers)) * RISK_PCT / 100
                sc = sig['entry_price'] * sl / ms * sp + TC
                c = max(1, int(risk_a / sc)) if sc > 0 else 1
                if go_used + go * c <= go_limit:
                    positions[t] = {'dir': sig['direction'], 'ep': sig['entry_price'], 'contracts': c, 'bi': 0}
                    go_used += go * c
    
    # Save trades
    if new_trades:
        conn = get_pg()
        cur = conn.cursor()
        for t, d, ep, ex, c, pnl, reason in new_trades:
            cur.execute(
                "INSERT INTO paper.trades (strategy, ticker, direction, entry, exit, contracts, pnl, reason) "
                "VALUES ('dragon', %s, %s, %s, %s, %s, %s, %s)",
                (t, d, ep, ex, c, pnl, reason))
        conn.commit()
        cur.close(); conn.close()
    
    save_state(eq, peak, mdd, trades_count, positions)
    log.info(f"EQ={eq:.0f} PK={peak:.0f} MDD={mdd:.2f}% POS={len(positions)} TR={trades_count}")


def calc_mtm(pos, bar, specs):
    ms, sp = specs['ms'], specs['sp']
    raw = ((bar['prc'] - pos['ep']) / ms * sp) * pos['contracts']
    return raw if pos['dir'] == 'long' else -raw


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
    ensure_tables()
    log.info("Dragon paper trader started")
    tick()
