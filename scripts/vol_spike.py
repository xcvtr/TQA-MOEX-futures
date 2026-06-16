#!/usr/bin/env python3
"""Volume spike + Yur OI confirmation on M5."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd, psycopg2
from datetime import datetime, timedelta, timezone
from collections import Counter

from scripts.bar_level_sim import BarLevelPortfolio, TICKER_CONFIGS

DB = dict(host='127.0.0.1', port=5432, dbname='moex', user='postgres', password='')
ALL = sorted(TICKER_CONFIGS.keys())
DAYS = 365

def _zs(vals, w=20):
    out = [0.0] * len(vals)
    for i in range(w, len(vals)):
        chunk = vals[i-w:i]
        mu = sum(chunk) / w
        sd = (sum((x-mu)**2 for x in chunk) / w) ** 0.5
        out[i] = (vals[i] - mu) / sd if sd > 0 else 0
    return out

# Load data on 5m with OI
print("[1] Loading 5m data...")
since = datetime.now(timezone.utc) - timedelta(days=DAYS)
conn = psycopg2.connect(**DB)
cur = conn.cursor()

# Get yur share to filter
cur.execute('''SELECT symbol, AVG((yur_buy::float+yur_sell::float)/NULLIF(total_oi::float,0)) as ys 
FROM moex_prices_5m_oi WHERE time>=%s AND total_oi>0 GROUP BY symbol''', (since,))
whale = sorted([r[0] for r in cur.fetchall() if r[1] > 0.50 and r[0] in ALL])
cur.close()
print(f"Whale tickers (yur>50%): {len(whale)}")

# Load merged 5m data for whale tickers
tf = {}
for sym in whale:
    cur = conn.cursor()
    cur.execute("SELECT time,open,high,low,close,volume FROM moex_prices_5m WHERE symbol=%s AND time>=%s AND volume>0 ORDER BY time", (sym, since))
    ohlcv = [{'time': r[0], 'open': float(r[1]), 'high': float(r[2]), 'low': float(r[3]), 'close': float(r[4]), 'volume': float(r[5])} for r in cur]
    cur.close()
    if len(ohlcv) < 500: continue
    cur = conn.cursor()
    cur.execute("SELECT time,fiz_buy,fiz_sell,yur_buy,yur_sell,total_oi FROM moex_prices_5m_oi WHERE symbol=%s AND time>=%s ORDER BY time", (sym, since))
    oi = [{'time': r[0], 'fiz_buy': float(r[1]), 'fiz_sell': float(r[2]), 'yur_buy': float(r[3]), 'yur_sell': float(r[4]), 'total_oi': float(r[5])} for r in cur]
    cur.close()
    om = {r['time'].strftime('%Y-%m-%d %H:%M'): r for r in oi}
    merged = []
    for r in ohlcv:
        o = om.get(r['time'].strftime('%Y-%m-%d %H:%M'))
        if o is None: continue
        merged.append({**r, 'total_oi': o['total_oi'], 'fiz_buy': o['fiz_buy'], 'fiz_sell': o['fiz_sell'], 'yur_buy': o['yur_buy'], 'yur_sell': o['yur_sell'], 'symbol': sym})
    if len(merged) >= 500:
        tf[sym] = merged
conn.close()
print(f"Loaded: {len(tf)} tickers")

# Show volume stats for each ticker
print("\n[2] Volume analysis...")
for sym, rows in tf.items():
    volumes = [r['volume'] for r in rows]
    avg_v = sum(volumes) / len(volumes)
    max_v = max(volumes)
    vol_zs = _zs(volumes, 20)
    n_spikes = sum(1 for vz in vol_zs if vz > 3.0)
    print(f"  {sym:>8}: avg_vol={avg_v:>8.0f} max_vol={max_v:>8.0f} spike3σ={n_spikes:>4}")

print("\n[3] Volume Spike + Yur Confirmation signals...")

# Strategy: volume_z > thresh + yur_confirmation in next bars
def detect_spike_signals(rows, vol_th=3.0, conf_bars=3, hold_bars=12):
    """Volume spike → confirm yur direction over next conf_bars → enter on confirmation bar close."""
    n = len(rows)
    volumes = [r['volume'] for r in rows]
    vol_z = _zs(volumes, 20)
    
    yur_net = [r['yur_buy'] - r['yur_sell'] for r in rows]
    # Smooth yur_net over 3 bars to reduce noise
    yur_smooth = [0.0] * n
    for i in range(2, n):
        yur_smooth[i] = (yur_net[i-2] + yur_net[i-1] + yur_net[i]) / 3.0
    
    signals = []
    min_idx = 30  # need enough history
    
    for i in range(min_idx, n - conf_bars - hold_bars - 1):
        if vol_z[i] < vol_th:
            continue
        
        # Volume spike detected at bar i
        # Check next conf_bars for yur confirmation
        yur_change = yur_smooth[i + conf_bars] - yur_smooth[i]
        
        # yur change must be significant: > 1 std of recent yur changes
        recent_yur = [abs(yur_net[j]) for j in range(i-20, i)]
        yur_noise = sum(recent_yur) / len(recent_yur) if recent_yur else 1
        yur_noise = max(yur_noise, 1)
        
        if abs(yur_change) < yur_noise:
            continue  # not enough confirmation
        
        direction = 'LONG' if yur_change > 0 else 'SHORT'
        
        # Enter on close of confirmation bar
        entry_idx = i + conf_bars
        entry = rows[entry_idx]['close']
        if entry <= 0:
            continue
        
        # Exit after hold_bars
        exit_idx = entry_idx + hold_bars
        if exit_idx >= n:
            continue
        exit_price = rows[exit_idx]['close']
        
        if direction == 'LONG':
            ret = (exit_price - entry) / entry * 100
        else:
            ret = (entry - exit_price) / entry * 100
        
        signals.append({
            'ticker': rows[0].get('symbol', '?'),
            'direction': direction,
            'entry': round(entry, 4),
            'exit': round(exit_price, 4),
            'time': str(rows[entry_idx]['time']),
            'return_pct': round(ret, 4),
            'strategy': 'vol_spike',
            'vol_z': round(vol_z[i], 2),
            'yur_change': round(yur_change, 0),
            'score': min(abs(vol_z[i]) * 0.2 + abs(yur_change) / max(abs(yur_change), yur_noise) * 0.3 + 0.3, 0.9),
            'atr_pct': 0.01,
            'adx_value': 20,
        })
    return signals

# Test multiple param combos
for vol_th, conf_bars, hold_bars, label in [
    (3.0, 3, 12, "V3C3H12"),  # 3σ spike, 3 bars confirm, 12 bars hold (~1h)
    (3.0, 6, 12, "V3C6H12"),  # 3σ spike, 6 bars confirm (~30min), 12 bars hold
    (2.5, 3, 12, "V2.5C3H12"),
    (4.0, 3, 12, "V4C3H12"),
    (3.0, 3, 24, "V3C3H24"),  # hold 2h
    (3.0, 3, 6,  "V3C3H6"),   # hold 30min
]:
    all_sigs = []
    for sym, rows in tf.items():
        sigs = detect_spike_signals(rows, vol_th, conf_bars, hold_bars)
        all_sigs.extend(sigs)
    
    if len(all_sigs) < 3:
        print(f"  {label}: {len(all_sigs)} sigs — too few")
        continue
    
    for s in all_sigs:
        s['_time_dt'] = pd.Timestamp(s['time'])
    
    # WR on raw signals
    wr_raw = sum(1 for s in all_sigs if s['return_pct'] > 0) / len(all_sigs) * 100
    avg_ret = sum(s['return_pct'] for s in all_sigs) / len(all_sigs)
    
    # Ticker breakdown
    tk_ct = Counter(s['ticker'] for s in all_sigs)
    tk_wr = {}
    for t in set(s['ticker'] for s in all_sigs):
        tk_sigs = [s for s in all_sigs if s['ticker'] == t]
        tk_wr[t] = sum(1 for s in tk_sigs if s['return_pct'] > 0) / len(tk_sigs) * 100
    
    print(f"\n  {label}: sigs={len(all_sigs):>5} WR={wr_raw:.0f}% avg={avg_ret:>+.3f}%")
    for tk, n in tk_ct.most_common(5):
        print(f"    {tk}: {n} sigs WR={tk_wr[tk]:.0f}%")
    
    # Portfolio noroll
    p = BarLevelPortfolio(initial_capital=200000, max_dd=0.99, margin_usage=0.10,
        max_concurrent=16, total_margin_limit=0.30, stop_loss_pct=0.02,
        use_score_sizing=True, use_score_eviction=False, atr_stop_mult=2.0,
        use_score_decay=True, use_mtm=True, use_trailing=True, trailing_mult=3.0,
        max_hold_bars=max(hold_bars * 2, 20), allow_rollover=False)
    r = p.run(all_sigs)
    wr = sum(1 for t in r['trades'] if t['pnl'] > 0) / len(r['trades']) * 100 if r['trades'] else 0
    total_pnl = sum(t['pnl'] for t in r['trades']) if r['trades'] else 0
    print(f"  pf: ret={r['total_return_pct']:>7.2f}% DD={r['max_dd_pct']:>6.2f}% Calmar={r['calmar']:.4f} T={len(r['trades']):>2} WR={wr:.0f}%")
    if r['trades']:
        tk_pnl = Counter()
        for t in r['trades']: tk_pnl[t['ticker']] += t['pnl']
        for tk, pnl in tk_pnl.most_common(5):
            print(f"    {tk}: PnL={pnl:>+8.0f}")
        # WF
        if len(r['trades']) >= 12:
            n4 = len(all_sigs) // 4
            for fi in range(4):
                fs = all_sigs[fi*n4:(fi+1)*n4]
                if len(fs) < 5: continue
                gr = {}
                for s in fs: gr.setdefault(s['_time_dt'], []).append(s)
                st = sorted(gr.keys())
                pw = BarLevelPortfolio(initial_capital=200000, max_dd=0.99, margin_usage=0.10,
                    max_concurrent=16, total_margin_limit=0.30, stop_loss_pct=0.02,
                    use_score_sizing=True, use_score_eviction=False, atr_stop_mult=2.0,
                    use_score_decay=True, use_mtm=True, use_trailing=True, trailing_mult=3.0,
                    max_hold_bars=max(hold_bars * 2, 20), allow_rollover=False)
                rw = pw._run_grouped(st, gr)
                print(f"    F{fi+1}: ret={rw['total_return_pct']:.2f}% DD={rw['max_dd_pct']:.2f}% Calmar={rw['calmar']:.4f} T={len(rw['trades'])}")