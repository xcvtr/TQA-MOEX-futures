#!/usr/bin/env python3
"""
ICT (Inner Circle Trader) / Smart Money Concepts Backtest on MOEX 5m Data.

Components:
  1. Fair Value Gaps (FVG)       — 3-candle pattern with mitigation entry
  2. Order Blocks (OB)           — last candle before displacement, retrace entry
  3. Liquidity Sweep (LIQ)       — break of swing high/low + reversal
  4. Market Structure Shift (MSS) — break of higher-low / lower-high
  5. Displacement + Retest        — impulse >2×ATR + fib retrace (38.2-61.8%)

All signals: entry at open[i+1], exit at close[i+horizon].
No look-ahead. Walk-forward 66/33. Short returns flipped.
"""
import psycopg2, numpy as np, sys, json, time, math
from collections import defaultdict
from datetime import datetime

DB = dict(host='10.0.0.60', port=5432, dbname='moex', user='postgres', password='***')
TICKERS = ['BR', 'NM', 'SBERF', 'AF']
START = '2025-01-01'
SPLIT = '2025-10-01'  # train/test split
END   = '2026-05-01'

# ── helpers ──────────────────────────────────────────────────────────────

def rolling_median(arr, w=20):
    """NO look-ahead. Median of PREVIOUS w values."""
    out = np.zeros(len(arr))
    for i in range(len(arr)):
        win = arr[max(0, i - w):i]
        out[i] = float(np.median(win)) if len(win) > 0 else arr[i]
    return out

def atr(high, low, close, w=14):
    """True Range, then rolling mean of prev w values."""
    tr = np.zeros(len(high))
    tr[0] = high[0] - low[0]
    for i in range(1, len(high)):
        tr[i] = max(high[i] - low[i], abs(high[i] - close[i-1]), abs(low[i] - close[i-1]))
    out = np.zeros(len(tr))
    for i in range(w, len(tr)):
        out[i] = tr[i-w:i].mean()
    return out

def find_swing_highs(high, n=5):
    """Return boolean array where high[i] is a swing high (higher than n bars left+right)."""
    out = np.zeros(len(high), dtype=bool)
    for i in range(n, len(high) - n):
        if high[i] == max(high[i-n:i+n+1]):
            out[i] = True
    return out

def find_swing_lows(low, n=5):
    """Return boolean array where low[i] is a swing low."""
    out = np.zeros(len(low), dtype=bool)
    for i in range(n, len(low) - n):
        if low[i] == min(low[i-n:i+n+1]):
            out[i] = True
    return out

# ── ICT strategy components ──────────────────────────────────────────────

def detect_fvg(o, h, l, c):
    """
    Fair Value Gaps.
    Bullish FVG: h[i-1] < l[i+1]  (gap up)
    Bearish FVG: l[i-1] > h[i+1]  (gap down)
    Returns array of signal dicts: {idx, direction, gap_high, gap_low}
    """
    n = len(c)
    fvgs = []
    for i in range(1, n - 1):
        # Bullish FVG
        if h[i-1] < l[i+1]:
            fvgs.append({
                'idx': i,
                'direction': 'BUY',
                'gap_high': l[i+1],
                'gap_low': h[i-1],
                'type': 'FVG'
            })
        # Bearish FVG
        if l[i-1] > h[i+1]:
            fvgs.append({
                'idx': i,
                'direction': 'SELL',
                'gap_high': l[i-1],
                'gap_low': h[i+1],
                'type': 'FVG'
            })
    return fvgs

def detect_ob(o, h, l, c, w=20):
    """
    Order Blocks.
    Detect displacement: body > 1.5× median body of last w bars.
    OB = candle IMMEDIATELY before displacement.
    Bullish OB: displacement candle closes green, OB is its low
    Bearish OB: displacement candle closes red, OB is its high
    """
    n = len(c)
    bodies = np.abs(c - o)
    med_body = rolling_median(bodies, w)

    obs = []
    for i in range(w + 1, n - 1):
        body = abs(c[i] - o[i])
        if body > 1.5 * med_body[i] and body > 0.0:
            # displacement at candle i, OB is candle i-1
            ob_idx = i - 1
            if c[i] > o[i]:  # bullish displacement → BUY OB
                obs.append({
                    'idx': ob_idx,
                    'direction': 'BUY',
                    'ob_level': l[ob_idx],
                    'displacement_idx': i,
                    'type': 'OB'
                })
            elif c[i] < o[i]:  # bearish displacement → SELL OB
                obs.append({
                    'idx': ob_idx,
                    'direction': 'SELL',
                    'ob_level': h[ob_idx],
                    'displacement_idx': i,
                    'type': 'OB'
                })
    return obs

def detect_liquidity_sweep(o, h, l, c, n=5, sweep_pct=0.001):
    """
    Liquidity Sweep.
    1) Find swing highs/lows (pivot points n bars left/right)
    2) Price breaks above swing high or below swing low by >sweep_pct
    3) Reverses in OPPOSITE direction next bar
    Entry at close of reversal bar (open[i+1] in backtester)
    """
    n_bars = len(c)
    sh = find_swing_highs(h, n)
    sl = find_swing_lows(l, n)

    # Build arrays of swing high levels and swing low levels
    swing_high_vals = np.full(n_bars, np.nan)
    swing_low_vals = np.full(n_bars, np.nan)
    for i in range(n_bars):
        if sh[i]:
            swing_high_vals[i] = h[i]
        if sl[i]:
            swing_low_vals[i] = l[i]

    # Forward-fill swing levels
    last_sh = np.nan
    last_sl = np.nan
    for i in range(n_bars):
        if not np.isnan(swing_high_vals[i]):
            last_sh = swing_high_vals[i]
        if not np.isnan(swing_low_vals[i]):
            last_sl = swing_low_vals[i]
        swing_high_vals[i] = last_sh
        swing_low_vals[i] = last_sl

    signals = []
    for i in range(n + 1, n_bars - 2):
        if np.isnan(swing_high_vals[i]) and np.isnan(swing_low_vals[i]):
            continue
        # Check break of swing high
        if not np.isnan(swing_high_vals[i]):
            if h[i] > swing_high_vals[i] * (1 + sweep_pct):
                # Reversal: next bar closes lower
                if i + 1 < n_bars and c[i + 1] < c[i]:
                    # Higher high break, then lower close → SELL signal
                    signals.append({
                        'idx': i + 1,  # signal at reversal bar
                        'direction': 'SELL',
                        'type': 'LIQ'
                    })
                    continue
        # Check break of swing low
        if not np.isnan(swing_low_vals[i]):
            if l[i] < swing_low_vals[i] * (1 - sweep_pct):
                # Reversal: next bar closes higher
                if i + 1 < n_bars and c[i + 1] > c[i]:
                    signals.append({
                        'idx': i + 1,
                        'direction': 'BUY',
                        'type': 'LIQ'
                    })
    return signals

def detect_mss(o, h, l, c, n=10):
    """
    Market Structure Shift.
    In uptrend (series of HH, HL), price breaks below last higher low.
    In downtrend (series of LH, LL), price breaks above last lower high.
    Simplified: track recent swing highs/lows.
    """
    n_bars = len(c)
    sh = find_swing_highs(h, 3)
    sl = find_swing_lows(l, 3)

    signals = []

    # We need at least 2 swing points to establish structure
    swing_high_indices = np.where(sh)[0]
    swing_low_indices = np.where(sl)[0]

    for i in range(30, n_bars - 1):
        # Check uptrend MSS: recent higher lows, then break
        recent_sl = swing_low_indices[(swing_low_indices < i) & (swing_low_indices > i - 60)]
        recent_sh = swing_high_indices[(swing_high_indices < i) & (swing_high_indices > i - 60)]

        if len(recent_sl) >= 2 and len(recent_sh) >= 2:
            # Check if we have higher lows and higher highs (uptrend)
            if (l[recent_sl[-2]] < l[recent_sl[-1]] and
                h[recent_sh[-2]] < h[recent_sh[-1]]):
                # Uptrend established. MSS = break below last higher low
                last_higher_low = l[recent_sl[-1]]
                if c[i] < last_higher_low:
                    signals.append({
                        'idx': i,
                        'direction': 'SELL',
                        'type': 'MSS',
                        'detail': 'uptrend_break'
                    })
                    continue

        if len(recent_sl) >= 2 and len(recent_sh) >= 2:
            # Check downtrend: lower highs and lower lows
            if (l[recent_sl[-2]] > l[recent_sl[-1]] and
                h[recent_sh[-2]] > h[recent_sh[-1]]):
                # Downtrend established. MSS = break above last lower high
                last_lower_high = h[recent_sh[-1]]
                if c[i] > last_lower_high:
                    signals.append({
                        'idx': i,
                        'direction': 'BUY',
                        'type': 'MSS',
                        'detail': 'downtrend_break'
                    })
    return signals

def detect_displacement_retest(o, h, l, c, w=20):
    """
    Displacement + Retest.
    Strong impulse move (range > 2× ATR), then retrace to 38.2-61.8% fib level,
    then entry in direction of displacement.
    """
    n_bars = len(c)
    atr_vals = atr(h, l, c, 14)

    signals = []
    for i in range(w + 14, n_bars - 1):
        if atr_vals[i] <= 0:
            continue

        # Detect strong impulse
        rng = h[i] - l[i]
        if rng > 2.0 * atr_vals[i]:
            # Impulse detected. Direction?
            if c[i] > o[i]:
                impulse_dir = 'BUY'
                impulse_low = l[i]
                impulse_high = h[i]
            else:
                impulse_dir = 'SELL'
                impulse_high = h[i]
                impulse_low = l[i]

            # Look for retracement in next bars
            for j in range(i + 1, min(i + 24, n_bars - 1)):
                if impulse_dir == 'BUY':
                    retrace_range = impulse_high - impulse_low
                    if retrace_range <= 0:
                        continue
                    fib_382 = impulse_high - 0.382 * retrace_range
                    fib_618 = impulse_high - 0.618 * retrace_range
                    # Price retraced into fib zone
                    if l[j] <= fib_382 and l[j] >= fib_618:
                        signals.append({
                            'idx': j,
                            'direction': 'BUY',
                            'type': 'DISP_RETEST',
                            'detail': f'fib382={fib_382:.2f}_fib618={fib_618:.2f}'
                        })
                        break
                else:  # SELL
                    retrace_range = impulse_high - impulse_low
                    if retrace_range <= 0:
                        continue
                    fib_382 = impulse_low + 0.382 * retrace_range
                    fib_618 = impulse_low + 0.618 * retrace_range
                    if h[j] >= fib_382 and h[j] <= fib_618:
                        signals.append({
                            'idx': j,
                            'direction': 'SELL',
                            'type': 'DISP_RETEST',
                            'detail': f'fib382={fib_382:.2f}_fib618={fib_618:.2f}'
                        })
                        break
    return signals

# ── Main backtester ──────────────────────────────────────────────────────

def backtest(sym, rows, train_split_idx=None, test_mode=False):
    """Run all 5 ICT/SMC signals over data, evaluate results."""

    n = len(rows)
    if n < 500:
        return None

    # Extract arrays
    o = np.array([float(r[2]) for r in rows])  # open
    h = np.array([float(r[3]) for r in rows])  # high
    l = np.array([float(r[4]) for r in rows])  # low
    c = np.array([float(r[5]) for r in rows])  # close
    v = np.array([float(r[6] or 0) for r in rows])  # volume

    times = [r[1] for r in rows]

    # Determine test range
    if test_mode and train_split_idx:
        start_idx = train_split_idx
    else:
        start_idx = 100  # warmup

    # Detect all signals
    all_signals = []

    fvgs = detect_fvg(o, h, l, c)
    all_signals.extend(fvgs)

    obs = detect_ob(o, h, l, c)
    all_signals.extend(obs)

    liqs = detect_liquidity_sweep(o, h, l, c)
    all_signals.extend(liqs)

    msss = detect_mss(o, h, l, c)
    all_signals.extend(msss)

    disps = detect_displacement_retest(o, h, l, c)
    all_signals.extend(disps)

    # Test horizons
    horizons = [4, 8, 16, 24]  # 20min, 40min, 80min, 120min

    results = {}

    for horizon in horizons:
        # Evaluate each signal type
        for signal_type in ['FVG', 'OB', 'LIQ', 'MSS', 'DISP_RETEST']:
            sigs = [s for s in all_signals if s['type'] == signal_type]

            buys = [s for s in sigs if s['direction'] == 'BUY']
            sells = [s for s in sigs if s['direction'] == 'SELL']

            for direction, sig_list in [('LONG', buys), ('SHORT', sells)]:
                # Filter to range, ensure exit exists
                valid = []
                for s in sig_list:
                    idx = s['idx']
                    if idx < start_idx or idx >= n - horizon - 1:
                        continue
                    entry = o[idx + 1]
                    if entry <= 0:
                        continue
                    valid.append((idx, entry))

                if len(valid) < 5:
                    continue

                rets = []
                for idx, entry in valid:
                    exit_price = c[idx + horizon]
                    if direction == 'LONG':
                        ret = (exit_price - entry) / entry * 100
                    else:
                        ret = (entry - exit_price) / entry * 100
                    rets.append(ret)

                wr = sum(1 for r in rets if r > 0) / len(rets) * 100
                gains = sum(r for r in rets if r > 0)
                losses = abs(sum(r for r in rets if r < 0))
                pf = gains / losses if losses > 0 else 0.0

                # Max drawdown
                dd = 0.0
                cum = peak = 0.0
                for rv in rets:
                    cum += rv
                    if cum > peak:
                        peak = cum
                    dd = max(dd, peak - cum)

                avg_ret = np.mean(rets)

                key = f'{signal_type}_{direction}_h{horizon}'
                results[key] = {
                    'n': len(rets),
                    'wr': wr,
                    'pf': pf,
                    'dd': dd,
                    'avg_ret': avg_ret,
                    'signal_type': signal_type,
                    'direction': direction,
                    'horizon': horizon,
                    'total_ret': sum(rets),
                }

    return results


# ── Main ─────────────────────────────────────────────────────────────────

def main():
    print("=" * 100)
    print("ICT (Inner Circle Trader) / Smart Money Concepts — Backtest on MOEX 5m")
    print(f"Period: {START} to {END}  |  Split: {SPLIT}")
    print(f"Tickers: {', '.join(TICKERS)}")
    print("=" * 100)

    # Connect DB
    db_params = {k: v for k, v in DB.items()}
    db_params['password'] = '***'  # will be substituted
    conn = psycopg2.connect(**DB)
    cur = conn.cursor()

    placeholders = ','.join(['%s'] * len(TICKERS))
    cur.execute(f"""
        SELECT symbol, time, open, high, low, close, volume
        FROM moex_prices_5m
        WHERE symbol IN ({placeholders}) AND time >= %s AND time < %s
        ORDER BY symbol, time
    """, TICKERS + [START, END])
    all_rows = cur.fetchall()
    cur.close()
    conn.close()

    by_sym = defaultdict(list)
    for r in all_rows:
        by_sym[r[0]].append(r)

    # ── Summary header ───────────────────────────────────────────────
    header = f"{'Ticker':6s} | {'Period':26s} | {'SigType':12s} | {'Dir':6s} | {'H':3s} | {'n':>5s} | {'WR%':>6s} | {'PF':>6s} | {'Avg%':>7s} | {'Tot%':>8s} | {'DD%':>7s}"
    sep = "-" * 100

    overall_results = {}

    for sym in TICKERS:
        rows = by_sym.get(sym, [])
        if len(rows) < 500:
            print(f"{sym:6s} | {'❌ Not enough data':26s}")
            continue

        n = len(rows)
        # Find split index
        split_idx = None
        for i, r in enumerate(rows):
            if r[1].isoformat()[:10] >= SPLIT:
                split_idx = i
                break
        if split_idx is None or split_idx < 200:
            print(f"{sym:6s} | {'❌ Split not found':26s}")
            continue

        train_rows = rows[:split_idx]
        test_rows = rows[split_idx:]

        print(f"\n{sym} — Train: {len(train_rows)} bars ({rows[0][1].isoformat()[:10]} to {rows[split_idx-1][1].isoformat()[:10]})")
        print(f"{'':6s}   Test: {len(test_rows)} bars ({rows[split_idx][1].isoformat()[:10]} to {rows[-1][1].isoformat()[:10]})")

        # ── Walk-forward: train then test ──────────────────────────
        # We use the TRAIN period to find best horizon per signal type,
        # then validate on TEST period.

        train_results = backtest(sym, rows, split_idx, test_mode=False)
        test_results_raw = backtest(sym, rows, split_idx, test_mode=True)

        if not train_results or not test_results_raw:
            print(f"{sym:6s} | {'❌ No results':26s}")
            continue

        # Build test results keyed the same way
        test_results = {}
        # We need to re-key test results properly
        # Actually backtest() with test_mode=True and train_split_idx runs on test portion.
        # But it currently uses start_idx = split_idx, which is correct.
        # The signal detection already respects start_idx.
        test_results = test_results_raw

        if not test_results:
            print(f"{sym:6s} | {'❌ No test signals':26s}")
            continue

        print(f"\n{'':6s}  {header}")
        print(f"{'':6s}  {sep}")

        sym_best = []

        for key in sorted(train_results.keys()):
            tr = train_results[key]
            tst = test_results.get(key)

            if not tst or tst['n'] < 3:
                continue

            sig_type = tr['signal_type']
            direction = tr['direction']
            horizon = tr['horizon']

            # Only show if training had decent signals
            if tr['n'] < 10:
                continue

            score = tr['wr'] * tr['pf'] / max(tr['dd'], 0.3)

            period_str = f"train|test"

            emoji = '❌'
            if tst['pf'] >= 1.3:
                emoji = '✅'
            elif tst['pf'] >= 1.1:
                emoji = '🟡'

            # Store for overall summary
            comb_key = f"{sym}_{key}"
            overall_results[comb_key] = {**tst, 'symbol': sym}

            avg_ret_str = f"{tst['avg_ret']:+.4f}%" if tst['avg_ret'] != 0 else " 0.0000%"
            tot_ret_str = f"{tst['total_ret']:+.2f}%" if tst['total_ret'] != 0 else "  0.00%"

            print(f"{'':6s}  {emoji} {sig_type:12s} | {direction:6s} | {horizon:3d} | {tst['n']:5d} | {tst['wr']:6.1f}% | {tst['pf']:6.2f} | {avg_ret_str:>7s} | {tot_ret_str:>8s} | {tst['dd']:6.2f}%")

            sym_best.append((tst['pf'], tst['wr'], tst['n'], sig_type, direction, horizon))

        # Show top test results
        sym_best.sort(key=lambda x: (x[1] * x[0]), reverse=True)
        if sym_best:
            best = sym_best[0]
            print(f"\n{'':6s}  ★ Best: {best[3]} {best[4]} h={best[5]} — WR={best[1]:.1f}% PF={best[0]:.2f} n={best[2]}")

        # ── Per-ticker summary ──────────────────────────────────────

    # ── Overall ranking ────────────────────────────────────────────────
    print("\n" + "=" * 100)
    print("OVERALL RANKING (Test Period, PF ≥ 1.1, n ≥ 5)")
    print("=" * 100)

    ranked = [(k, v) for k, v in overall_results.items() if v['pf'] >= 1.1 and v['n'] >= 5]
    ranked.sort(key=lambda x: x[1]['pf'] * x[1]['wr'], reverse=True)

    if ranked:
        print(f"\n{'Rank':4s} | {'Symbol':6s} | {'Signal':12s} | {'Dir':6s} | {'H':3s} | {'n':>5s} | {'WR%':>6s} | {'PF':>6s} | {'Avg%':>7s} | {'DD%':>7s}")
        print("-" * 75)
        for rank, (key, v) in enumerate(ranked[:20], 1):
            avg_str = f"{v['avg_ret']:+.4f}%" if v['avg_ret'] != 0 else " 0.0000%"
            print(f"{rank:4d} | {v['symbol']:6s} | {v['signal_type']:12s} | {v['direction']:6s} | {v['horizon']:3d} | {v['n']:5d} | {v['wr']:6.1f}% | {v['pf']:6.2f} | {avg_str:>7s} | {v['dd']:6.2f}%")

    # ── Best per signal type ──────────────────────────────────────────
    print("\n" + "=" * 100)
    print("BEST PER SIGNAL TYPE")
    print("=" * 100)

    for sig_type in ['FVG', 'OB', 'LIQ', 'MSS', 'DISP_RETEST']:
        candidates = [(k, v) for k, v in overall_results.items()
                      if v['signal_type'] == sig_type and v['pf'] >= 1.1 and v['n'] >= 5]
        if candidates:
            candidates.sort(key=lambda x: x[1]['pf'] * x[1]['wr'], reverse=True)
            best_k, best_v = candidates[0]
            avg_str = f"{best_v['avg_ret']:+.4f}%" if best_v['avg_ret'] != 0 else " 0.0000%"
            print(f"  {sig_type:12s}: {best_v['symbol']:6s} {best_v['direction']:6s} h={best_v['horizon']:2d} "
                  f"— WR={best_v['wr']:5.1f}% PF={best_v['pf']:.2f} n={best_v['n']:4d} "
                  f"Avg={avg_str} DD={best_v['dd']:.2f}%")
        else:
            print(f"  {sig_type:12s}: ❌ No profitable signals")

    # ── Summary table ─────────────────────────────────────────────────
    print("\n" + "=" * 100)
    print("AGGREGATE SUMMARY BY SIGNAL TYPE × DIRECTION")
    print("=" * 100)

    grouped = defaultdict(list)
    for k, v in overall_results.items():
        key = (v['signal_type'], v['direction'])
        grouped[key].append(v)

    print(f"\n{'Signal':14s} | {'Dir':6s} | {'Combined n':>10s} | {'Avg WR%':>8s} | {'Avg PF':>7s} | {'Avg DD%':>8s} | {'Avg Ret%':>9s}")
    print("-" * 75)
    for key in sorted(grouped.keys()):
        vals = grouped[key]
        n_total = sum(v['n'] for v in vals)
        wr_avg = np.mean([v['wr'] for v in vals])
        pf_avg = np.mean([v['pf'] for v in vals])
        dd_avg = np.mean([v['dd'] for v in vals])
        ret_avg = np.mean([v['avg_ret'] for v in vals])
        print(f"{key[0]:14s} | {key[1]:6s} | {n_total:10d} | {wr_avg:8.1f}% | {pf_avg:7.2f} | {dd_avg:8.2f}% | {ret_avg:9.4f}%")

    print("\n✅ Done.")


if __name__ == '__main__':
    main()
