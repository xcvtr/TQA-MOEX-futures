#!/usr/bin/env python3
"""Retail Trap + Yur Divergence — правильная fiz/yur стратегия."""
import sys, os
from datetime import datetime, timedelta, timezone
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import psycopg2

from scripts.bar_level_sim import (
    BarLevelPortfolio, TICKER_CONFIGS,
)

DB = dict(host='127.0.0.1', port=5432, dbname='moex', user='postgres', password='')
ALL_TICKERS = sorted(TICKER_CONFIGS.keys())
DAYS = 365
ZSCORE_WINDOW = 20

# ── Helpers ──────────────────────────────────────────────────────────────────

def _zs(vals, w=ZSCORE_WINDOW):
    out = [0.0] * len(vals)
    for i in range(w, len(vals)):
        chunk = vals[i-w:i]
        mu = sum(chunk) / w
        var = sum((x-mu)**2 for x in chunk) / w
        sd = var**0.5
        out[i] = (vals[i] - mu) / sd if sd > 0 else 0.0
    return out

# ── Data ─────────────────────────────────────────────────────────────────────

def load_all():
    since = datetime.now(timezone.utc) - timedelta(days=DAYS)
    all_data = {}
    conn = psycopg2.connect(**DB)
    try:
        for sym in ALL_TICKERS:
            cur = conn.cursor()
            cur.execute("SELECT time, open, high, low, close, volume FROM moex_prices_5m WHERE symbol=%s AND time>=%s ORDER BY time", (sym, since))
            ohlcv = [{'time': r[0], 'open': float(r[1]), 'high': float(r[2]), 'low': float(r[3]), 'close': float(r[4]), 'volume': float(r[5])} for r in cur]
            cur.close()
            if len(ohlcv) < 100: continue
            cur = conn.cursor()
            cur.execute("SELECT time, fiz_buy, fiz_sell, yur_buy, yur_sell, total_oi FROM moex_prices_5m_oi WHERE symbol=%s AND time>=%s ORDER BY time", (sym, since))
            oi_rows = [{'time': r[0], 'fiz_buy': float(r[1]), 'fiz_sell': float(r[2]), 'yur_buy': float(r[3]), 'yur_sell': float(r[4]), 'total_oi': float(r[5])} for r in cur]
            cur.close()
            oi_map = {r['time'].strftime('%Y-%m-%d %H:%M'): r for r in oi_rows}
            merged = []
            for r in ohlcv:
                t_str = r['time'].strftime('%Y-%m-%d %H:%M')
                o = oi_map.get(t_str)
                if o is None: continue
                merged.append({**r, 'total_oi': o['total_oi'], 'fiz_buy': o['fiz_buy'], 'fiz_sell': o['fiz_sell'], 'yur_buy': o['yur_buy'], 'yur_sell': o['yur_sell'], 'symbol': sym})
            if len(merged) >= 100:
                all_data[sym] = merged
    finally:
        conn.close()
    print(f"  Loaded {len(all_data)}/{len(ALL_TICKERS)}")
    return all_data

def resample(merged, rule='1h'):
    df = pd.DataFrame(merged)
    df['time'] = pd.to_datetime(df['time'])
    df.set_index('time', inplace=True)
    agg = {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum',
           'total_oi': 'last', 'fiz_buy': 'last', 'fiz_sell': 'last', 'yur_buy': 'last', 'yur_sell': 'last'}
    res = df.resample(rule).agg(agg).dropna(subset=['close'])
    sym = merged[0].get('symbol', '?')
    out = []
    for idx, row in res.iterrows():
        out.append({'time': idx, 'open': float(row['open']), 'high': float(row['high']),
            'low': float(row['low']), 'close': float(row['close']), 'volume': float(row['volume']),
            'total_oi': float(row['total_oi']), 'fiz_buy': float(row['fiz_buy']),
            'fiz_sell': float(row['fiz_sell']), 'yur_buy': float(row['yur_buy']),
            'yur_sell': float(row['yur_sell']), 'symbol': sym})
    return out

# ── Strategies ───────────────────────────────────────────────────────────────

def retail_trap(rows, fiz_th=1.5, horizon=12, min_gap=0):
    """fiz_z extreme → trade against crowd."""
    n = len(rows)
    if n < 40: return []
    
    fiz_ratio = []
    for r in rows:
        total = r['fiz_buy'] + r['fiz_sell']
        net = r['fiz_buy'] - r['fiz_sell']
        fiz_ratio.append(net / max(total, 1))
    
    fiz_z = _zs(fiz_ratio, ZSCORE_WINDOW)
    closes = [r['close'] for r in rows]
    atr = _calc_atr(rows, 14)
    
    signals = []
    last = {}
    for i in range(25, n):
        tk = rows[0]['symbol']
        if tk in last and i - last[tk] < min_gap:
            continue
        if i + horizon >= n: continue
        
        z = fiz_z[i]
        if abs(z) < fiz_th: continue
        
        direction = 'SHORT' if z > fiz_th else 'LONG'
        entry = rows[i+1]['open']
        if entry <= 0: continue
        exit_price = rows[i+horizon]['close']
        
        if direction == 'LONG':
            ret = (exit_price - entry) / entry * 100
        else:
            ret = (entry - exit_price) / entry * 100
        
        signals.append({'ticker': tk, 'direction': direction,
            'entry': round(entry, 4), 'exit': round(exit_price, 4),
            'time': str(rows[i]['time']), 'return_pct': round(ret, 4),
            'strategy': 'retail_trap', 'idx': i,
            'fiz_z': round(z, 4), 'score': 0.5, 'atr_pct': atr[i] if atr and i < len(atr) else 0.01,
            'adx_value': 20})
        last[tk] = i
    return signals

def yur_divergence(rows, div_th=1.0, horizon=12, min_gap=0):
    """yur_z vs price_z divergence."""
    n = len(rows)
    if n < 40: return []
    
    yur_net = [(r['yur_buy'] - r['yur_sell']) / max(r['yur_buy'] + r['yur_sell'], 1) for r in rows]
    yur_z = _zs(yur_net, ZSCORE_WINDOW)
    
    closes = [r['close'] for r in rows]
    # % change instead of z-score for price (more intuitive for divergence)
    price_chg = [0.0] * n
    for i in range(1, n):
        price_chg[i] = (closes[i] - closes[i-1]) / closes[i-1] * 100
    
    price_z = _zs(price_chg, ZSCORE_WINDOW)
    atr = _calc_atr(rows, 14)
    
    signals = []
    last = {}
    for i in range(25, n):
        tk = rows[0]['symbol']
        if tk in last and i - last[tk] < min_gap:
            continue
        if i + horizon >= n: continue
        
        yz = yur_z[i]
        pz = price_z[i]
        
        # Yur going up, price weak → LONG (yur accumulating)
        # Yur going down, price strong → SHORT (yur distributing)
        if yz > div_th and pz < -div_th:
            direction = 'LONG'
        elif yz < -div_th and pz > div_th:
            direction = 'SHORT'
        else:
            continue
        
        entry = rows[i+1]['open']
        if entry <= 0: continue
        exit_price = rows[i+horizon]['close']
        
        if direction == 'LONG':
            ret = (exit_price - entry) / entry * 100
        else:
            ret = (entry - exit_price) / entry * 100
        
        signals.append({'ticker': tk, 'direction': direction,
            'entry': round(entry, 4), 'exit': round(exit_price, 4),
            'time': str(rows[i]['time']), 'return_pct': round(ret, 4),
            'strategy': 'yur_div', 'idx': i,
            'yur_z': round(yz, 4), 'price_z': round(pz, 4),
            'score': 0.5, 'atr_pct': atr[i] if atr and i < len(atr) else 0.01,
            'adx_value': 20})
        last[tk] = i
    return signals

def _calc_atr(rows, period=14):
    n = len(rows)
    if n < period + 1: return []
    tr = [0.0] * n
    for i in range(1, n):
        h, l, pc = rows[i]['high'], rows[i]['low'], rows[i-1]['close']
        tr[i] = max(h-l, abs(h-pc), abs(l-pc))
    atr = [0.0] * n
    for i in range(period, n):
        atr[i] = sum(tr[i-period:i]) / period
    return atr

# ── Portfolio ────────────────────────────────────────────────────────────────

def run_pf(sigs, label, **kwargs):
    if len(sigs) < 3: return None
    for s in sigs: s['_time_dt'] = pd.Timestamp(s['time'])
    params = dict(initial_capital=kwargs.get('cap', 100000), max_dd=0.20,
        margin_usage=kwargs.get('margin', 0.10), max_concurrent=kwargs.get('mc', 8),
        total_margin_limit=0.20, stop_loss_pct=kwargs.get('sl', 0.02),
        use_score_sizing=True, use_score_eviction=False, atr_stop_mult=2.0,
        use_score_decay=True, use_mtm=True, use_trailing=True, trailing_mult=3.0,
        max_hold_bars=kwargs.get('hold', 40), allow_rollover=kwargs.get('roll', False))
    p = BarLevelPortfolio(**params)
    r = p.run(sigs)
    wr = sum(1 for t in r['trades'] if t['pnl'] > 0) / len(r['trades']) * 100 if r['trades'] else 0
    reasons = Counter(t['exit_reason'] for t in r['trades'])
    roll_pct = reasons.get('rollover', 0) / len(r['trades']) * 100 if r['trades'] else 0
    print(f"  {label:<40} sigs={len(sigs):>5} ret={r['total_return_pct']:>7.2f}% DD={r['max_dd_pct']:>6.2f}% Calmar={r['calmar']:.4f} T={len(r['trades']):>2} WR={wr:.0f}% R={roll_pct:.0f}%")
    return r

def wf(sigs, params):
    if len(sigs) < 10: return
    n4 = len(sigs) // 4
    folds = [sigs[:n4], sigs[n4:2*n4], sigs[2*n4:3*n4], sigs[3*n4:]]
    for fi, fs in enumerate(folds):
        if len(fs) < 5: continue
        groups = {}
        for s in fs: groups.setdefault(s['_time_dt'], []).append(s)
        st = sorted(groups.keys())
        p = BarLevelPortfolio(**params)
        r = p._run_grouped(st, groups)
        print(f"    Fold {fi+1}: ret={r['total_return_pct']:.2f}% DD={r['max_dd_pct']:.2f}% Calmar={r['calmar']:.4f} T={len(r['trades'])}")

# ── Main ─────────────────────────────────────────────────────────────────────

t0 = __import__('time').time()
print(f"══════ Retail Trap + Yur Div — {datetime.now():%Y-%m-%d} ══════")

print("\n[1] Loading...")
all_data = load_all()

print("\n[2] Resampling H1 + calculating yur share...")

# Calc yur share per ticker
yur_share = {}
for sym, rows in all_data.items():
    total_yur = sum(r['yur_buy'] + r['yur_sell'] for r in rows if r['total_oi'] > 0)
    total_oi_sum = sum(r['total_oi'] for r in rows if r['total_oi'] > 0)
    yur_share[sym] = total_yur / total_oi_sum if total_oi_sum > 0 else 0

# Filter: yur > 50%
whale_tickers = sorted([s for s, y in yur_share.items() if y > 0.50])
print(f"  Whale tickers (yur>50%): {len(whale_tickers)} — {', '.join(whale_tickers[:10])}...")

tf = {}
for sym in whale_tickers:
    rows = resample(all_data[sym], '1h')
    if len(rows) >= 50:
        tf[sym] = rows
print(f"  H1 tickers: {len(tf)}")

# ── Signals ──────────────────────────────────────────────────────────────────

print("\n[3] Generating signals...")

all_retail = []
all_yur = []
for sym, rows in tf.items():
    all_retail.extend(retail_trap(rows, fiz_th=1.5, horizon=12, min_gap=12))
    all_yur.extend(yur_divergence(rows, div_th=1.0, horizon=12, min_gap=12))

# Also try fiz_th=2.0 (stricter)
all_retail_strict = []
for sym, rows in tf.items():
    all_retail_strict.extend(retail_trap(rows, fiz_th=2.0, horizon=12, min_gap=12))

# Also try yur_div with stricter threshold
all_yur_strict = []
for sym, rows in tf.items():
    all_yur_strict.extend(yur_divergence(rows, div_th=1.5, horizon=12, min_gap=12))

print(f"\n  Retail Trap (fiz_z>1.5): {len(all_retail)} ({len(all_retail)/365:.1f}/d)")
print(f"  Retail Trap (fiz_z>2.0): {len(all_retail_strict)} ({len(all_retail_strict)/365:.1f}/d)")
print(f"  Yur Div (div>1.0):      {len(all_yur)} ({len(all_yur)/365:.1f}/d)")
print(f"  Yur Div (div>1.5):      {len(all_yur_strict)} ({len(all_yur_strict)/365:.1f}/d)")

# Ticker breakdown
for name, ss in [("Retail 1.5", all_retail), ("Retail 2.0", all_retail_strict), 
                  ("YurDiv 1.0", all_yur), ("YurDiv 1.5", all_yur_strict)]:
    if ss:
        print(f"  {name}: {', '.join(f'{k}={v}' for k,v in Counter(s['ticker'] for s in ss).most_common(5))}")

# ── Portfolio ────────────────────────────────────────────────────────────────

print("\n[4] Portfolio tests...")

for name, sigs in [("RT-1.5 noroll", all_retail), ("RT-2.0 noroll", all_retail_strict),
                    ("YD-1.0 noroll", all_yur), ("YD-1.5 noroll", all_yur_strict)]:
    run_pf(sigs, name, cap=100000, mc=8, margin=0.10, sl=0.02, roll=False)

print()
# Higher capital for best one
for name, sigs in [("RT-1.5 200K noroll", all_retail), ("YD-1.0 200K noroll", all_yur)]:
    run_pf(sigs, name, cap=200000, mc=16, margin=0.10, sl=0.02, roll=False)

print()
# Allow rollover (min_gap=12 should protect)
for name, sigs in [("RT-1.5 roll", all_retail), ("YD-1.0 roll", all_yur)]:
    run_pf(sigs, name, cap=100000, mc=8, margin=0.10, sl=0.02, roll=True)

print()
# Try horizon=6 (shorter hold)
all_retail_h6 = []
for sym, rows in tf.items():
    all_retail_h6.extend(retail_trap(rows, fiz_th=1.5, horizon=6, min_gap=6))
run_pf(all_retail_h6, "RT-1.5 h=6 noroll", cap=100000, mc=8, margin=0.10, sl=0.02, roll=False)

all_yur_h6 = []
for sym, rows in tf.items():
    all_yur_h6.extend(yur_divergence(rows, div_th=1.0, horizon=6, min_gap=6))
run_pf(all_yur_h6, "YD-1.0 h=6 noroll", cap=100000, mc=8, margin=0.10, sl=0.02, roll=False)

# ── WF ───────────────────────────────────────────────────────────────────────
print("\n[5] Walk-Forward (best configs)...")

for name, sigs, params in [
    ("RT-1.5 noroll 100K", all_retail, dict(initial_capital=100000, max_dd=0.20, margin_usage=0.10,
        max_concurrent=8, total_margin_limit=0.20, stop_loss_pct=0.02,
        use_score_sizing=True, use_score_eviction=False, atr_stop_mult=2.0,
        use_score_decay=True, use_mtm=True, use_trailing=True, trailing_mult=3.0,
        max_hold_bars=40, allow_rollover=False)),
    ("YD-1.0 noroll 100K", all_yur, dict(initial_capital=100000, max_dd=0.20, margin_usage=0.10,
        max_concurrent=8, total_margin_limit=0.20, stop_loss_pct=0.02,
        use_score_sizing=True, use_score_eviction=False, atr_stop_mult=2.0,
        use_score_decay=True, use_mtm=True, use_trailing=True, trailing_mult=3.0,
        max_hold_bars=40, allow_rollover=False)),
]:
    if len(sigs) >= 8:
        print(f"  {name}:")
        wf(sigs, params)

print(f"\n⏱ {__import__('time').time()-t0:.0f}s")