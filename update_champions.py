#!/usr/bin/env python3
"""
Auto-отбор чемпионов: сканирует все 59+ тикеров, отбирает топ-N,
обновляет CHAMPIONS и GO_DATA в moex_equity_dashboard.py, перезапускает дашборд.
"""
import os, sys, json, re, time, psycopg2, numpy as np
from datetime import datetime, timedelta

DB = dict(host='10.0.0.60', port=5432, dbname='moex', user='postgres', password=os.environ.get('MOEX_DB_PASSWORD', '***'))
HISTORY_FILE = os.path.join(os.path.dirname(__file__), 'champions_history.json')
DASHBOARD_FILE = os.path.join(os.path.dirname(__file__), 'moex_equity_dashboard.py')
TOP_N = 19
ROLLING_MONTHS = 12  # взять последние 12 месяцев для скоринга
MIN_SIGNALS = 30     # минимум сигналов за период
MIN_WR = 55          # минимальный WR для включения

# ── ISS GO map (front-month, static) ──
GO_MAP = {
    'CC': {'go': 473, 'lev': 6.4}, 'PD': {'go': 22173, 'lev': 4.6},
    'SS': {'go': 205, 'lev': 2.0}, 'GZ': {'go': 2065, 'lev': 5.7},
    'NG': {'go': 6565, 'lev': 3.5}, 'GL': {'go': 1220, 'lev': 8.7},
    'SE': {'go': 625, 'lev': 1.4},  'SN': {'go': 8180, 'lev': 4.9},
    'HY': {'go': 804, 'lev': 4.9},  'IB': {'go': 803, 'lev': 3.5},
    'NM': {'go': 1405, 'lev': 5.8},
    'GK': {'go': 234, 'lev': 5.8},  'MG': {'go': 4096, 'lev': 5.8},
    'RN': {'go': 8180, 'lev': 4.9}, 'AL': {'go': 660, 'lev': 3.9},
    'SP': {'go': 1008, 'lev': 2.0}, 'ME': {'go': 3149, 'lev': 5.8},
    'CE': {'go': 1187, 'lev': 11.9},'HS': {'go': 231, 'lev': 114.9},
    'BR': {'go': 1702, 'lev': 3.8}, 'RI': {'go': 24668, 'lev': 6.6},
    'W4': {'go': 1758, 'lev': 9.2}, 'CH': {'go': 538, 'lev': 7.8},
    'OJ': {'go': 2019, 'lev': 5.9}, 'DX': {'go': 0, 'lev': 5.0},
    'BM': {'go': 0, 'lev': 5.0},    'NR': {'go': 1536, 'lev': 4.9},
    'SV': {'go': 11487, 'lev': 4.8},'VB': {'go': 1363, 'lev': 5.7},
    'LK': {'go': 10218, 'lev': 4.9},'GD': {'go': 26922, 'lev': 12.1},
    'SR': {'go': 5719, 'lev': 5.8}, 'Si': {'go': 11093, 'lev': 6.7},
}

# Friendly names
NAMES = {
    'ME':'MOEX','GK':'NorNickel','CC':'Cocoa C','PD':'Palladium','SP':'SPBE',
    'SS':'Sugar','NM':'NLMK','GZ':'Gazprom','NG':'Nat Gas','IB':'I-Bonds',
    'GL':'Gold L','SE':'Soybean','AL':'Alrosa','MG':'MMK','RN':'Rosneft',
    'CE':'Copper','HS':'Hang Seng','HY':'Hryvnia','SN':'Tin',
    'BR':'Brent','RI':'RTS','Si':'USD/RUB','W4':'Wheat','CH':'Cocoa',
    'OJ':'Orange J.','DX':'Dollar I.','BM':'Butter','NR':'Nat Rubber',
    'SV':'Silver','VB':'VTB','LK':'Lukoil','GD':'Gold','SR':'Sberbank',
}

def run_strategy(sym, since_date):
    """Run Volume Climax strategy and return signals."""
    go_info = GO_MAP.get(sym, {'lev': 5.0})
    lev = go_info.get('lev', 5.0)

    conn = psycopg2.connect(**DB)
    cur = conn.cursor()
    cur.execute("SELECT time, open, high, low, close, volume FROM moex_prices_5m WHERE symbol = %s AND time >= %s AND volume > 0 ORDER BY time", (sym, since_date))
    rows = cur.fetchall()
    conn.close()
    if len(rows) < 200:
        return None, None, None

    h4 = {}
    for t, o, h, l, c, v in rows:
        h4_key = t.replace(minute=0, second=0, microsecond=0) - timedelta(hours=t.hour % 4)
        if h4_key not in h4:
            h4[h4_key] = [t, o, h, l, c, v]
        else:
            prev = h4[h4_key]
            h4[h4_key] = [prev[0], prev[1], max(prev[2], h), min(prev[3], l), c, prev[5] + v]
    h4_bars = sorted(h4.values(), key=lambda x: x[0])
    if len(h4_bars) < 35:
        return None, None, None

    data = []
    for i, (t, o, h, l, c, v) in enumerate(h4_bars):
        d = {'time': t, 'open': o, 'high': h, 'low': l, 'close': c, 'volume': v,
             'range_pct': (h - l) / l * 100 if l else 0}
        if i >= 20:
            window = h4_bars[i - 20:i]
            vols = [w[5] for w in window]
            med_vol = np.median(vols) if vols else 1
            d['vol_ratio'] = v / max(med_vol, 1)
            ranges = [(w[2] - w[3]) / w[3] * 100 for w in window if w[3] > 0]
            d['avg_range_pct'] = np.mean(ranges) if ranges else 0
            d['close_pos'] = (c - l) / (h - l) if h != l else 0.5
        else:
            d['vol_ratio'] = 0; d['avg_range_pct'] = 0; d['close_pos'] = 0.5
        data.append(d)

    sigs = []
    for i, d in enumerate(data):
        if d['vol_ratio'] <= 2 or d['range_pct'] <= d.get('avg_range_pct', 0):
            continue
        is_red = d['close'] < d['open']
        is_green = d['close'] > d['open']
        is_bear = is_red and d['close_pos'] <= 0.35
        is_bull = is_green and d['close_pos'] >= 0.65
        if not is_bear and not is_bull: continue
        if i + 1 + 2 >= len(data): continue

        entry = data[i+1]['open'] * 1.001
        hold = [data[i+1+k] for k in range(2)]

        if is_bear:
            tp = entry * 1.004; sl = entry * 0.992; trail_be = entry * 1.001
            trail_sl = sl; trailed = False; reason = 'timeout'
            for bar in hold:
                if bar['high'] >= tp: reason = 'tp'; break
                if bar['low'] <= trail_sl: reason = 'sl'; break
                if not trailed and bar['high'] >= entry * 1.005:
                    trail_sl = trail_be; trailed = True
            exit_p = {'tp': tp, 'sl': trail_sl, 'timeout': hold[-1]['close']}[reason]
            ret = (exit_p - entry) / entry * 100
        else:
            tp = entry * 0.996; sl = entry * 1.008; trail_be = entry * 0.999
            trail_sl = sl; trailed = False; reason = 'timeout'
            for bar in hold:
                if bar['low'] <= tp: reason = 'tp'; break
                if bar['high'] >= trail_sl: reason = 'sl'; break
                if not trailed and bar['low'] <= entry * 0.995:
                    trail_sl = trail_be; trailed = True
            exit_p = {'tp': tp, 'sl': trail_sl, 'timeout': hold[-1]['close']}[reason]
            ret = (entry - exit_p) / entry * 100

        sigs.append({'ret': ret, 'win': ret > 0, 'reason': reason, 'time': d['time']})
    return sigs, lev, rows

def compute_stats(sigs, lev):
    """Compute metrics from signals."""
    n = len(sigs)
    rets = [s['ret'] for s in sigs]
    wins = sum(1 for s in sigs if s['win'])
    wr = wins / n * 100
    total = sum(rets)
    avg = np.mean(rets)
    gp = sum(p for p in rets if p > 0)
    gl = abs(sum(p for p in rets if p < 0))
    pf = gp / max(gl, 0.001)
    cum = np.cumsum(rets)
    peak = np.maximum.accumulate(cum)
    dd = cum - peak
    max_dd = min(dd)
    go_total = total * lev
    go_dd = max_dd * lev
    tp_cnt = sum(1 for s in sigs if s['reason'] == 'tp')
    sl_cnt = sum(1 for s in sigs if s['reason'] == 'sl')
    exp_cnt = sum(1 for s in sigs if s['reason'] == 'timeout')
    score = wr * pf * (1 + max(go_total, 0) / 100) / max(abs(go_dd) / 50, 0.5)
    return {
        'signals': n, 'wr': round(wr, 1), 'pf': round(pf, 2),
        'total_pnl': round(total, 2), 'go_pnl': round(go_total, 1),
        'max_dd': round(max_dd, 1), 'go_dd': round(go_dd, 0),
        'avg_ret': round(avg, 3), 'lev': lev,
        'tp': tp_cnt, 'sl': sl_cnt, 'exp': exp_cnt,
        'score': round(score, 0),
    }


def scan_all(since_date):
    """Scan all symbols in DB and return ranked list."""
    conn = psycopg2.connect(**DB)
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT symbol FROM moex_prices_5m WHERE volume > 0 AND time >= %s ORDER BY symbol", (since_date,))
    symbols = [r[0] for r in cur.fetchall()]
    conn.close()

    results = []
    for sym in symbols:
        sigs, lev, _ = run_strategy(sym, since_date)
        if not sigs or len(sigs) < MIN_SIGNALS:
            continue
        stats = compute_stats(sigs, lev)
        if stats['wr'] < MIN_WR:
            continue
        results.append({'symbol': sym, 'name': NAMES.get(sym, sym), **stats})
        print(f"  {sym:>6} {stats['signals']:>4} sig  WR {stats['wr']:5.1f}%  PF {stats['pf']:.2f}  GO {stats['go_pnl']:+.1f}%  DD {stats['go_dd']:+.0f}%  score {stats['score']:.0f}")

    results.sort(key=lambda x: x['score'], reverse=True)
    return results

def generate_dashboard_code(champions, prev_champions_set):
    """Generate GO_DATA and CHAMPIONS blocks as text."""
    now_set = set(c['symbol'] for c in champions)
    new_entries = now_set - prev_champions_set
    out_entries = prev_champions_set - now_set

    # Build GO_DATA
    go_lines = ['GO_DATA = {']
    for c in champions:
        sym = c['symbol']
        if sym in GO_MAP:
            g = GO_MAP[sym]
            go_lines.append(f'    "{sym}": {{"go_rub": {g["go"]}, "lev": {g["lev"]}}},')
    go_lines.append('}')

    # Build CHAMPIONS
    champ_lines = ['CHAMPIONS = [']
    for c in champions:
        sym = c['symbol']
        name = NAMES.get(sym, sym)
        suffix = '  # NEW' if sym in new_entries else ''
        champ_lines.append(f'    ("{sym}", "{name}"),{suffix}')
    champ_lines.append(']')

    return '\n'.join(go_lines) + '\n\n' + '\n'.join(champ_lines) + '\n', new_entries, out_entries

def load_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE) as f:
            return json.load(f)
    return {'versions': []}

def save_history(history):
    with open(HISTORY_FILE, 'w') as f:
        json.dump(history, f, indent=2, ensure_ascii=False)


def main():
    since = (datetime.now() - timedelta(days=ROLLING_MONTHS * 30)).strftime('%Y-%m-%d')
    print(f"=== Auto Champion Selection ===")
    print(f"Rolling window: {ROLLING_MONTHS} months (since {since})")
    print(f"Min signals: {MIN_SIGNALS}, Min WR: {MIN_WR}%\n")

    # Load previous champions
    history = load_history()
    prev_set = set()
    if history['versions']:
        prev_set = set(history['versions'][-1]['champions'])

    # Scan all
    print("Scanning all symbols...")
    results = scan_all(since)
    print(f"\nTotal qualified: {len(results)}")
    if not results:
        print("ERROR: no qualified tickers!")
        sys.exit(1)

    # Select top N
    top = results[:TOP_N]
    print(f"\n=== TOP {TOP_N} Champions ===")
    for i, c in enumerate(top):
        print(f"  {i+1:>2}. {c['symbol']:>6} {c['name']:>12} WR {c['wr']:5.1f}% PF {c['pf']:.2f} GO {c['go_pnl']:+.0f}% DD {c['go_dd']:+.0f}% score {c['score']:.0f}")

    # Generate new blocks
    full_new_code, new_ents, out_ents = generate_dashboard_code(top, prev_set)
    print(f"\nNew entries: {sorted(new_ents) if new_ents else 'none'}")
    print(f"Removed: {sorted(out_ents) if out_ents else 'none'}")

    if not new_ents and not out_ents:
        print("No changes — skipping dashboard update.")
        # Still save history
        version = {
            'date': datetime.now().strftime('%Y-%m-%d %H:%M'),
            'champions': [c['symbol'] for c in top],
            'changed': False,
        }
        history['versions'].append(version)
        save_history(history)
        return

    # Patch dashboard file — replace GO_DATA and CHAMPIONS blocks
    with open(DASHBOARD_FILE) as f:
        content = f.read()

    # Find GO_DATA = { ... } block
    gd_start = content.find('GO_DATA = {')
    if gd_start == -1:
        print("ERROR: cannot find GO_DATA block in dashboard")
        sys.exit(1)
    depth = 0
    gd_end = gd_start
    for i in range(gd_start, len(content)):
        if content[i] == '{': depth += 1
        elif content[i] == '}': depth -= 1
        if depth == 0:
            gd_end = i + 1
            break

    # Find CHAMPIONS = [ ... ] block
    ch_start = content.find('CHAMPIONS = [')
    if ch_start == -1:
        print("ERROR: cannot find CHAMPIONS block in dashboard")
        sys.exit(1)
    depth = 0
    ch_end = ch_start
    for i in range(ch_start, len(content)):
        if content[i] == '[': depth += 1
        elif content[i] == ']': depth -= 1
        if depth == 0:
            ch_end = i + 1
            break

    # Build new GO_DATA and CHAMPIONS blocks
    # Extract just the GO_DATA block (up to CHAMPIONS)
    go_block = full_new_code[:full_new_code.find('\nCHAMPIONS')]
    ch_block = full_new_code[full_new_code.find('CHAMPIONS = ['):]

    # Replace
    new_content = content[:gd_start] + go_block + content[gd_end:ch_start] + ch_block + content[ch_end:]

    # Write
    with open(DASHBOARD_FILE, 'w') as f:
        f.write(new_content)
    print(f"\nPatched {DASHBOARD_FILE}")

    # Save history
    version = {
        'date': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'champions': [c['symbol'] for c in top],
        'new': sorted(new_ents),
        'out': sorted(out_ents),
        'changed': True,
    }
    history['versions'].append(version)
    save_history(history)

    # Restart dashboard
    os.system('pkill -9 -f moex_equity_dashboard.py 2>/dev/null; sleep 1')
    os.system(f'cd {os.path.dirname(DASHBOARD_FILE)} && nohup /home/user/venvs/tqa/main/bin/python -u moex_equity_dashboard.py > /tmp/dashboard.log 2>&1 &')
    print("Dashboard restarted.")
    print(f"History saved to {HISTORY_FILE}")

if __name__ == '__main__':
    main()
