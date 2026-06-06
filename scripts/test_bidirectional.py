#!/usr/bin/env python3
"""
Bidirectional Volume Surge + FIZ/YUR Divergence strategy.
Always trades in YUR direction (no LONG/SHORT split).
Compares signal count vs unidirectional version.
"""
import psycopg2, sys, math
from collections import defaultdict

DB = dict(host='10.0.0.60', port=5432, dbname='moex', user='postgres', password='postgres')
TICKERS = ['HS', 'W4', 'DX', 'NR', 'KC']
VOL_Z_THRESHOLDS = [1.5, 1.75, 2.0, 2.25, 2.5, 2.75, 3.0]
DIV_Z_THRESHOLDS = [0.5, 0.75, 1.0, 1.25, 1.5]
EXIT_HORIZONS = [3, 6, 12, 24, 48]


def zs(vals, w=20):
    """Rolling z-score, w preceding values (no look-ahead)."""
    out = [0.0] * len(vals)
    for i in range(w, len(vals)):
        chunk = vals[i-w:i]
        mu = sum(chunk) / w
        var = sum((x - mu)**2 for x in chunk) / w
        sd = var ** 0.5
        out[i] = (vals[i] - mu) / sd if sd > 0 else 0.0
    return out


def load_data(symbol):
    conn = psycopg2.connect(**DB)
    cur = conn.cursor()
    cur.execute("""
        SELECT oi.time, oi.fiz_buy, oi.fiz_sell, oi.yur_buy, oi.yur_sell,
               p.close, p.volume, p.open
        FROM moex_prices_5m_oi oi
        JOIN moex_prices_5m p ON p.symbol=oi.symbol AND p.time=oi.time
        WHERE oi.symbol=%s AND oi.time >= '2023-01-01'
        ORDER BY oi.time
    """, (symbol,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def calc_drawdown(returns):
    if not returns:
        return 0.0
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for r in returns:
        cum += r
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd
    return max_dd


def analyze_ticker(symbol):
    """Analyze one ticker across all param combos."""
    rows = load_data(symbol)
    if len(rows) < 500:
        print(f"  ⚠ {symbol}: only {len(rows)} rows")
        return []

    close  = [float(r[5] or 0.0) for r in rows]
    volume = [float(r[6] or 0.0) for r in rows]
    open_px = [float(r[7] or 0.0) for r in rows]
    fiz_net = [float((r[1] or 0) - (r[2] or 0)) for r in rows]
    yur_net = [float((r[3] or 0) - (r[4] or 0)) for r in rows]

    n = len(rows)
    vol_z = zs(volume, 20)
    fiz_z = zs(fiz_net, 20)
    yur_z = zs(yur_net, 20)

    results = []
    max_hor = max(EXIT_HORIZONS)

    for vzt in VOL_Z_THRESHOLDS:
        for dzt in DIV_Z_THRESHOLDS:
            # Collect returns for each horizon
            bi_rets = {h: [] for h in EXIT_HORIZONS}
            uni_n_long  = {h: 0 for h in EXIT_HORIZONS}
            uni_n_short = {h: 0 for h in EXIT_HORIZONS}

            for i in range(20, n - max_hor - 1):
                if vol_z[i] < vzt:
                    continue
                fzi, yzi = fiz_z[i], yur_z[i]
                if abs(fzi) < dzt or abs(yzi) < dzt:
                    continue
                if fzi * yzi >= 0:
                    continue  # no divergence

                entry_px = open_px[i + 1]
                if entry_px <= 0:
                    continue

                # === BIDIRECTIONAL: trade in YUR direction ===
                # (long = buy low sell high, short = sell high buy low)
                for h in EXIT_HORIZONS:
                    if i + 1 + h >= n:
                        continue
                    exit_px = close[i + 1 + h - 1]
                    if exit_px <= 0:
                        continue

                    if yzi > 0:  # YUR positive → LONG
                        ret_bi = (exit_px - entry_px) / entry_px * 100.0
                    else:        # YUR negative → SHORT
                        ret_bi = (entry_px - exit_px) / entry_px * 100.0

                    bi_rets[h].append(ret_bi)

                    # === UNIDIRECTIONAL COUNTS ===
                    is_long_signal  = (fzi < 0 and yzi > 0)
                    is_short_signal = (fzi > 0 and yzi < 0)
                    if is_long_signal:
                        uni_n_long[h] += 1
                    if is_short_signal:
                        uni_n_short[h] += 1

            # Evaluate per horizon
            for h in EXIT_HORIZONS:
                rets = bi_rets[h]
                if len(rets) < 5:
                    continue

                wins = sum(1 for r in rets if r > 0)
                n_sig = len(rets)
                wr = wins / n_sig * 100.0
                gains = sum(r for r in rets if r > 0)
                losses = abs(sum(r for r in rets if r < 0))
                pf = gains / losses if losses > 0 else (99.9 if gains > 0 else 0.0)
                avg_ret = sum(rets) / n_sig
                dd = calc_drawdown(rets)

                n_uni_total = uni_n_long[h] + uni_n_short[h]

                results.append({
                    'symbol': symbol,
                    'vol_z': vzt,
                    'div_z': dzt,
                    'horizon': h,
                    'n': n_sig,
                    'wr': wr,
                    'pf': pf,
                    'avg_ret': avg_ret,
                    'dd': dd,
                    'n_uni_total': n_uni_total,
                    'n_uni_long': uni_n_long[h],
                    'n_uni_short': uni_n_short[h],
                })

    return results


def main():
    all_results = {}
    for sym in TICKERS:
        print(f"\n{'='*90}")
        print(f"  Loading {sym}...")
        all_results[sym] = analyze_ticker(sym)

    # Print results
    header = f"{'Ticker':>5} | {'vol_z':>5} | {'div_z':>4} | {'h':>2} | {'n':>5} | {'WR%':>6} | {'PF':>5} | {'avg%':>7} | {'DD%':>6}"
    print(f"\n\n{'='*90}")
    print("BIDIRECTIONAL RESULTS (trading always in YUR direction)")
    print('='*90)
    print(header)
    print('-' * len(header))

    best_per_ticker = {}

    for sym in TICKERS:
        best_score = -999
        best_r = None
        for r in all_results[sym]:
            print(f"  {r['symbol']:>3} | {r['vol_z']:>5.2f} | {r['div_z']:>4.2f} | {r['horizon']:>2} | {r['n']:>5} | {r['wr']:>5.1f}% | {r['pf']:>5.2f} | {r['avg_ret']:+>7.2f}% | {r['dd']:>6.1f}%")

            # Track best (WR * PF, minimum 10 trades)
            if r['n'] >= 10:
                score = r['wr'] * r['pf']
                if score > best_score:
                    best_score = score
                    best_r = r

        best_per_ticker[sym] = best_r

    # Summary
    print(f"\n\n{'='*90}")
    print("BEST PARAM COMBINATION PER TICKER + UNI COMPARISON")
    print('='*90)
    for sym in TICKERS:
        r = best_per_ticker[sym]
        if r:
            print(f"{r['symbol']}:  vol_z={r['vol_z']:.2f} div_z={r['div_z']:.2f} h={r['horizon']:>2}  "
                  f"n={r['n']:>4}  WR={r['wr']:.1f}%  PF={r['pf']:.2f}  avg={r['avg_ret']:+.2f}%  DD={r['dd']:.1f}%")
            print(f"       Uni-directional total at same params: n={r['n_uni_total']} "
                  f"(LONG={r['n_uni_long']}, SHORT={r['n_uni_short']}) vs BI: n={r['n']}")
        else:
            print(f"{sym}: no valid results (n<5 for all combos)")


if __name__ == '__main__':
    main()
