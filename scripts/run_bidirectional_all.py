#!/usr/bin/env python3
"""
Bidirectional Volume Surge + FIZ/YUR Divergence strategy — ALL 34 tickers.
Always trades in YUR direction.
Finds best combos with filtering.
"""
import psycopg2, sys, math
from collections import defaultdict

DB = dict(host='10.0.0.60', port=5432, dbname='moex', user='postgres', password='postgres')

ALL_TICKERS = [
    'GL','AF','CC','CE','DX','HS','HY','MC','MG','NG','NM','NR','OJ','PD','SE',
    'SF','SN','SP','SS','TN','TT','W4','YD','AL','BM','GK','IB','KC','ME','MM',
    'PT','RN','SV','VB'
]

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
        print(f"  ⚠ {symbol}: only {len(rows)} rows, skipping")
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
    total_combos = len(VOL_Z_THRESHOLDS) * len(DIV_Z_THRESHOLDS) * len(EXIT_HORIZONS)
    combo_idx = 0

    for vzt in VOL_Z_THRESHOLDS:
        for dzt in DIV_Z_THRESHOLDS:
            bi_rets = {h: [] for h in EXIT_HORIZONS}

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
                })

            combo_idx += 1

    return results


def main():
    all_results = []
    ticker_count = len(ALL_TICKERS)

    for idx, sym in enumerate(ALL_TICKERS):
        print(f"\n[{idx+1}/{ticker_count}] Loading {sym}...", flush=True)
        ticker_results = analyze_ticker(sym)
        all_results.extend(ticker_results)
        print(f"  -> {len(ticker_results)} param combos", flush=True)

    print(f"\n{'='*100}")
    print(f"TOTAL: {len(all_results)} param combos across {ticker_count} tickers")
    print(f"{'='*100}")

    # Apply filters: n >= 50, WR >= 55%, PF >= 1.3, DD <= 20%
    filtered = [
        r for r in all_results
        if r['n'] >= 50 and r['wr'] >= 55.0 and r['pf'] >= 1.3 and r['dd'] <= 20.0
    ]

    # Sort by score = WR * PF descending
    for r in filtered:
        r['score'] = r['wr'] * r['pf']
    filtered.sort(key=lambda r: r['score'], reverse=True)

    print(f"\nFiltered: {len(filtered)} combos (n>=50, WR>=55%, PF>=1.3, DD<=20%)")
    print(f"\n{'='*100}")
    print("TOP-10 BEST BIDIRECTIONAL STRATEGIES (YUR direction)")
    print('='*100)

    header = (
        f"{'#':>2} | {'Ticker':>5} | {'vol_z':>5} | {'div_z':>4} | {'h':>2} | "
        f"{'n':>5} | {'WR%':>6} | {'PF':>5} | {'avg%':>7} | {'DD%':>6} | {'Score':>7}"
    )
    print(header)
    print('-' * len(header))

    top10 = filtered[:10]
    for rank, r in enumerate(top10, 1):
        print(
            f"{rank:>2} | {r['symbol']:>5} | {r['vol_z']:>5.2f} | {r['div_z']:>4.2f} | "
            f"{r['horizon']:>2} | {r['n']:>5} | {r['wr']:>5.1f}% | {r['pf']:>5.2f} | "
            f"{r['avg_ret']:+>7.2f}% | {r['dd']:>6.1f}% | {r['score']:>7.1f}"
        )

    print(f"\n{'='*100}")
    print("ALL PASSING COMBOS (sorted by score)")
    print('='*100)
    print(header)
    print('-' * len(header))

    for rank, r in enumerate(filtered[:50], 1):  # show top 50 or all
        print(
            f"{rank:>2} | {r['symbol']:>5} | {r['vol_z']:>5.2f} | {r['div_z']:>4.2f} | "
            f"{r['horizon']:>2} | {r['n']:>5} | {r['wr']:>5.1f}% | {r['pf']:>5.2f} | "
            f"{r['avg_ret']:+>7.2f}% | {r['dd']:>6.1f}% | {r['score']:>7.1f}"
        )

    # Per-ticker best
    print(f"\n\n{'='*100}")
    print("BEST PER TICKER (passing filters)")
    print('='*100)
    best_by_ticker = {}
    for r in filtered:
        sym = r['symbol']
        if sym not in best_by_ticker or r['score'] > best_by_ticker[sym]['score']:
            best_by_ticker[sym] = r

    h2 = f"{'Ticker':>5} | {'vol_z':>5} | {'div_z':>4} | {'h':>2} | {'n':>5} | {'WR%':>6} | {'PF':>5} | {'avg%':>7} | {'DD%':>6} | {'Score':>7}"
    print(h2)
    print('-' * len(h2))
    for sym in sorted(best_by_ticker.keys()):
        r = best_by_ticker[sym]
        print(
            f"{r['symbol']:>5} | {r['vol_z']:>5.2f} | {r['div_z']:>4.2f} | "
            f"{r['horizon']:>2} | {r['n']:>5} | {r['wr']:>5.1f}% | {r['pf']:>5.2f} | "
            f"{r['avg_ret']:+>7.2f}% | {r['dd']:>6.1f}% | {r['score']:>7.1f}"
        )

    passing_tickers = len(best_by_ticker)
    print(f"\nTickers with passing combos: {passing_tickers}/{ticker_count}")
    print(f"Total passing combos: {len(filtered)}")


if __name__ == '__main__':
    main()
