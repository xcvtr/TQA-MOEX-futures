#!/usr/bin/env python3
"""TF OI Rescue v6: H1, 0.95/1.05, min_gap=horizon=12."""
import sys, os
from datetime import datetime, timedelta, timezone
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import psycopg2

from trading_bot.new_strategies import detect_oi_divergence_signals
from scripts.bar_level_sim import (
    BarLevelPortfolio, TICKER_CONFIGS,
)

DB = dict(host='127.0.0.1', port=5432, dbname='moex', user='postgres', password='')
ALL_TICKERS = sorted(TICKER_CONFIGS.keys())
DAYS = 365

PARAM = dict(lookback=20, extreme_window=10, horizon=12,
    bear_threshold=0.95, bull_threshold=1.05, min_gap_bars=12)

def load_data():
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

def resample(merged):
    df = pd.DataFrame(merged)
    df['time'] = pd.to_datetime(df['time'])
    df.set_index('time', inplace=True)
    agg = {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum',
           'total_oi': 'last', 'fiz_buy': 'last', 'fiz_sell': 'last', 'yur_buy': 'last', 'yur_sell': 'last'}
    res = df.resample('1h').agg(agg).dropna(subset=['close'])
    out = []
    for idx, row in res.iterrows():
        out.append({'time': idx, 'open': float(row['open']), 'high': float(row['high']),
            'low': float(row['low']), 'close': float(row['close']), 'volume': float(row['volume']),
            'total_oi': float(row['total_oi']), 'fiz_buy': float(row['fiz_buy']),
            'fiz_sell': float(row['fiz_sell']), 'yur_buy': float(row['yur_buy']),
            'yur_sell': float(row['yur_sell']), 'symbol': merged[0].get('symbol', '?')})
    return out

def detect(tf_data):
    all_sigs = []
    for sym, rows in tf_data.items():
        sigs = detect_oi_divergence_signals(rows, PARAM)
        for s in sigs:
            idx = s.get('idx')
            if idx is None: continue
            s['ticker'] = sym
            s['time'] = str(rows[idx]['time'])
            s['score'] = 0.5
            s['atr_pct'] = 0.01
            s['adx_value'] = 20
            all_sigs.append(s)
    return all_sigs

def run_portfolio(sigs, label, capital=100000, max_conc=8, margin=0.10, hold=40, sl=0.02, noroll=True, trail=True):
    if len(sigs) < 5: return None
    for s in sigs: s['_time_dt'] = pd.Timestamp(s['time'])
    p = BarLevelPortfolio(initial_capital=capital, max_dd=0.20, margin_usage=margin,
        max_concurrent=max_conc, total_margin_limit=0.20, stop_loss_pct=sl,
        use_score_sizing=True, use_score_eviction=False, atr_stop_mult=2.0,
        use_score_decay=True, use_mtm=True, use_trailing=trail, trailing_mult=3.0,
        max_hold_bars=hold, allow_rollover=not noroll)
    r = p.run(sigs)
    wr = sum(1 for t in r['trades'] if t['pnl'] > 0) / len(r['trades']) * 100 if r['trades'] else 0
    reasons = Counter(t['exit_reason'] for t in r['trades'])
    roll_pct = reasons.get('rollover', 0) / len(r['trades']) * 100 if r['trades'] else 0
    print(f"  {label:<35} ret={r['total_return_pct']:>7.2f}% DD={r['max_dd_pct']:>6.2f}% Calmar={r['calmar']:.4f} T={len(r['trades']):>2} WR={wr:.0f}% R={roll_pct:.0f}%")
    print(f"    Reasons: {dict(reasons)}")
    for t in r['trades']:
        print(f"    {t['ticker']:>6} {t['direction']:5} PnL={t['pnl']:>+8.0f} {t['exit_reason']:10}")
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

t0 = __import__('time').time()
print(f"v6 — {datetime.now():%Y-%m-%d}")
print(f"Params: bear=0.95, bull=1.05, min_gap=12, h=12")

print("\n[1] Loading...")
all_data = load_data()

print("\n[2] H1 signals...")
tf = {s: resample(m) for s, m in all_data.items() if len(resample(m)) >= 50}
print(f"  Tickers: {len(tf)}")

sigs = detect(tf)
print(f"  Signals: {len(sigs)} ({len(sigs)/365:.1f}/day)")

# Ticker breakdown
tk_cnt = Counter(s['ticker'] for s in sigs)
print(f"  Active tickers: {len(tk_cnt)}")
for tk, n in tk_cnt.most_common(10):
    print(f"    {tk}: {n}")

print("\n[3] Portfolio...")

# Base config: noroll + min_gap_bars=12
run_portfolio(sigs, "noroll 100K/8/10%/2%SL", 100000, 8, 0.10, 40, 0.02, noroll=True)
print()

# Higher capital
run_portfolio(sigs, "noroll 200K/16/10%/2%SL", 200000, 16, 0.10, 40, 0.02, noroll=True)
print()

# Even higher
run_portfolio(sigs, "noroll 300K/24/10%/2%SL", 300000, 24, 0.10, 40, 0.02, noroll=True)
print()

# rollover allowed (min_gap=12 prevents cascades but allows clean roll)
run_portfolio(sigs, "roll 100K/8/10%/2%SL", 100000, 8, 0.10, 40, 0.02, noroll=False)
print()

# roll + trail
run_portfolio(sigs, "roll+trail 100K/8/10%/2%SL", 100000, 8, 0.10, 40, 0.02, noroll=False, trail=True)
print()

# No stop loss, noroll, higher capital
run_portfolio(sigs, "noroll 200K/16/15%/noSL", 200000, 16, 0.15, 80, 0.0, noroll=True)
print()

# WF if enough trades
best_params = dict(initial_capital=200000, max_dd=0.20, margin_usage=0.10,
    max_concurrent=16, total_margin_limit=0.20, stop_loss_pct=0.02,
    use_score_sizing=True, use_score_eviction=False, atr_stop_mult=2.0,
    use_score_decay=True, use_mtm=True, use_trailing=True, trailing_mult=3.0,
    max_hold_bars=40, allow_rollover=False)
print("\n[4] WF...")
wf(sigs, best_params)

print(f"\n⏱ {__import__('time').time()-t0:.0f}s")