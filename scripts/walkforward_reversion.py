#!/usr/bin/env python3
"""
Walk-Forward Validation: Mean Reversion After Volatility Exhaustion (MRAVE)

8 tickers, 50/50 train/test split, grid search mid×horizon.
Entry: open[i+1] (realistic)
Exit: close[i+horizon]
Score = WR × PF / max(DD, 0.5)
"""
import psycopg2, numpy as np, sys, time

DB = dict(host='10.0.0.60', port=5432, dbname='moex', user='postgres', password='postgres')
TICKERS = ['NM', 'BR', 'SBERF', 'MM', 'AF', 'HS', 'KC', 'DX']
SINCE = '2025-07-01'

# ── helpers ──────────────────────────────────────────────────────────

def zs(vals, w=20):
    """Rolling z-score, NO look-ahead."""
    out = np.zeros(len(vals))
    for i in range(w, len(vals)):
        c = vals[i - w:i]
        mu = c.mean()
        sd = c.std(ddof=0)
        out[i] = (vals[i] - mu) / sd if sd > 0 else 0.0
    return out


def rolling_median(arr, w=50):
    """Rolling median over PREVIOUS w values, NO look-ahead. Excludes current bar."""
    out = np.zeros(len(arr))
    for i in range(len(arr)):
        if i == 0:
            win = arr[:1]
        elif i < w:
            win = arr[:i]
        else:
            win = arr[i - w:i]
        out[i] = float(np.median(win)) if len(win) > 0 else 0.0
    return out


def evaluate(rows, mid_low, mid_high, horizon):
    """
    Run full signal detection on rows.
    Returns dict or None.
    """
    n = len(rows)
    c = np.array([float(r[3]) for r in rows])  # close
    o = np.array([float(r[0]) for r in rows])  # open
    hi = np.array([float(r[1]) for r in rows])  # high
    lo = np.array([float(r[2]) for r in rows])  # low
    v = np.array([float(r[4] or 0) for r in rows])  # volume

    rng = hi - lo
    wz = zs(v, 20)
    pos = (c - lo) / np.maximum(rng, 0.001)
    mr = rolling_median(rng, 50)

    # Candidates meeting all conditions
    cond = (wz >= 1.5) & (rng >= mr * 1.5) & (pos >= mid_low) & (pos <= mid_high)
    cand = np.where(cond)[0]
    cand = cand[(cand >= 25) & (cand < n - horizon - 1)]

    if len(cand) < 8:
        return None

    long_rets, short_rets = [], []
    for i in cand:
        pc = c[i - 3:i] - o[i - 3:i]
        entry = o[i + 1]
        if entry <= 0:
            continue
        if np.all(pc > 0):
            ret = (entry - c[i + horizon]) / entry * 100
            short_rets.append(ret)
        elif np.all(pc < 0):
            ret = (c[i + horizon] - entry) / entry * 100
            long_rets.append(ret)

    all_rets = long_rets + short_rets
    if len(all_rets) < 8:
        return None

    wr = sum(1 for r in all_rets if r > 0) / len(all_rets) * 100
    gains = sum(r for r in all_rets if r > 0)
    losses = abs(sum(r for r in all_rets if r < 0))
    pf = gains / losses if losses > 0 else 0.0

    dd = 0.0
    cum = peak = 0.0
    for rv in all_rets:
        cum += rv
        if cum > peak:
            peak = cum
        dd = max(dd, peak - cum)

    long_wr = sum(1 for r in long_rets if r > 0) / len(long_rets) * 100 if len(long_rets) >= 5 else 0
    short_wr = sum(1 for r in short_rets if r > 0) / len(short_rets) * 100 if len(short_rets) >= 5 else 0

    return {
        'wr': wr, 'pf': pf, 'dd': dd, 'n': len(all_rets),
        'long_n': len(long_rets), 'short_n': len(short_rets),
        'long_wr': long_wr, 'short_wr': short_wr,
    }


def grid_search(rows):
    """Grid search for best params: mid=[(0.3,0.7),(0.2,0.8)] × horizon=[6,12,24]."""
    best_score = -1
    best = None
    for ml, mh in [(0.3, 0.7), (0.2, 0.8)]:
        for hz in [6, 12, 24]:
            res = evaluate(rows, ml, mh, hz)
            if res is None:
                continue
            score = res['wr'] * res['pf'] / max(res['dd'], 0.5)
            if score > best_score:
                best_score = score
                best = (ml, mh, hz, score, res)
    return best


def load_ticker_data(sym):
    """Load data for one ticker since SINCE, retrying on connection error."""
    for attempt in range(3):
        try:
            conn = psycopg2.connect(**DB, connect_timeout=30)
            cur = conn.cursor()
            cur.execute("""
                SELECT open, high, low, close, volume
                FROM moex_prices_5m
                WHERE symbol = %s AND time >= %s
                ORDER BY time
            """, (sym, SINCE))
            rows = cur.fetchall()
            cur.close()
            conn.close()
            return rows
        except Exception as e:
            print(f"  [RETRY {attempt+1}] {sym}: {e}", flush=True)
            time.sleep(2)
    return []


def main():
    t0 = time.time()
    print("=" * 90)
    print("  WALK-FORWARD VALIDATION: Mean Reversion After Volatility Exhaustion")
    print(f"  Data since {SINCE} | 8 tickers | 50/50 split | Score = WR × PF / max(DD, 0.5)")
    print("=" * 90)
    print()
    print(f"{'Ticker':>7s} | {'Train WR':>8s} {'PF':>5s} {'n':>4s} {'DD':>5s} | "
          f"{'Test WR':>8s} {'PF':>5s} {'n':>4s} {'DD':>5s} | {'Diff':>6s} | {'Params':>16s} | Verdict")
    print("-" * 90)

    results = []
    for sym in TICKERS:
        print(f"\n[{sym}] Loading data...", flush=True)
        rows = load_ticker_data(sym)
        print(f"[{sym}] {len(rows)} rows loaded", flush=True)

        if len(rows) < 400:
            print(f"  {sym:>6s} |  {'—':>8s} {'—':>5s} {'—':>4s} {'—':>5s} | "
                  f"{'—':>8s} {'—':>5s} {'—':>4s} {'—':>5s} | {'—':>6s} | {'—':>16s} | ❌ NO DATA")
            continue

        mid = len(rows) // 2
        train_rows = rows[:mid]
        test_rows = rows[mid:]
        print(f"[{sym}] Train: {len(train_rows)} bars, Test: {len(test_rows)} bars", flush=True)

        best = grid_search(train_rows)
        if best is None:
            print(f"  {sym:>6s} |  {'—':>8s} {'—':>5s} {'—':>4s} {'—':>5s} | "
                  f"{'—':>8s} {'—':>5s} {'—':>4s} {'—':>5s} | {'—':>6s} | {'—':>16s} | ❌ NO TRAIN SIG")
            continue

        ml, mh, hz, train_score, train_res = best
        print(f"[{sym}] Best train: mid({ml},{mh}) h={hz} "
              f"WR={train_res['wr']:.1f}% PF={train_res['pf']:.2f} n={train_res['n']} "
              f"Score={train_score:.1f}", flush=True)

        test_res = evaluate(test_rows, ml, mh, hz)
        if test_res is None:
            print(f"  {sym:>6s} | {train_res['wr']:>7.1f}% {train_res['pf']:>4.2f} {train_res['n']:>4d} {train_res['dd']:>4.1f}% | "
                  f"{'—':>8s} {'—':>5s} {'—':>4s} {'—':>5s} | {'N/A':>6s} | mid({ml},{mh}) h={hz:>2d} | ❌ NO TEST SIG")
            continue

        diff = test_res['wr'] - train_res['wr']
        passed = test_res['wr'] >= train_res['wr'] - 10
        diff_str = f"{diff:+.1f}%" if abs(diff) >= 0.1 else " 0.0%"
        verdict = "✅ PASS" if passed else "❌ FAIL"

        print(f"  {sym:>6s} | {train_res['wr']:>7.1f}% {train_res['pf']:>4.2f} {train_res['n']:>4d} {train_res['dd']:>4.1f}% | "
              f"{test_res['wr']:>7.1f}% {test_res['pf']:>4.2f} {test_res['n']:>4d} {test_res['dd']:>4.1f}% | "
              f"{diff_str:>6s} | mid({ml},{mh}) h={hz:>2d} | {verdict}")

        results.append({
            'ticker': sym, 'passed': passed,
            'train_wr': train_res['wr'], 'test_wr': test_res['wr'],
            'params': (ml, mh, hz),
        })

    passed = sum(1 for r in results if r['passed'])
    total = len(results)
    print()
    print("-" * 90)
    print(f"  OOS Pass rate: {passed}/{total} tickers passed (WR within 10pp of train)")

    approved = passed >= 4
    if approved:
        print("  ✅ INTEGRATION APPROVED — OOS WR within 10pp of train for 4+ tickers")
    else:
        print("  ❌ INTEGRATION DENIED — OOS too weak")

    print(f"  Total time: {time.time()-t0:.0f}s")
    return approved


if __name__ == '__main__':
    approved = main()
    sys.exit(0 if approved else 1)
