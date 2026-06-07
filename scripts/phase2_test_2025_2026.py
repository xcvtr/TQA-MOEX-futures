#!/usr/bin/env python3
"""Test champion strategies from Phase 2 on 2025 and 2026 data."""

import psycopg2

DB = dict(host='10.0.0.64', port=5432, dbname='moex', user='postgres', password='postgres')


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


def load_data(symbol, start_date, end_date=None):
    """Load joined 5m data for symbol in date range."""
    conn = psycopg2.connect(**DB)
    cur = conn.cursor()
    if end_date:
        cur.execute("""
            SELECT oi.time, oi.fiz_buy, oi.fiz_sell, oi.yur_buy, oi.yur_sell,
                   p.close, p.volume, p.open as next_open
            FROM moex_prices_5m_oi oi
            JOIN moex_prices_5m p ON p.symbol=oi.symbol AND p.time=oi.time
            WHERE oi.symbol=%s AND oi.time >= %s AND oi.time < %s
            ORDER BY oi.time
        """, (symbol, start_date, end_date))
    else:
        cur.execute("""
            SELECT oi.time, oi.fiz_buy, oi.fiz_sell, oi.yur_buy, oi.yur_sell,
                   p.close, p.volume, p.open as next_open
            FROM moex_prices_5m_oi oi
            JOIN moex_prices_5m p ON p.symbol=oi.symbol AND p.time=oi.time
            WHERE oi.symbol=%s AND oi.time >= %s
            ORDER BY oi.time
        """, (symbol, start_date))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def calc_drawdown(returns_pct):
    """Maximum peak-to-trough drawdown of cumulative returns series."""
    if not returns_pct:
        return 0.0
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for r in returns_pct:
        cum += r
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd
    return max_dd


# Optimal parameters from Phase 2
CHAMPIONS = [
    ('W4',  'LONG',  2.5, 1.25, 6),   # horizon=30m -> 6 bars
    ('W4',  'SHORT', 3.0, 1.5,  24),  # horizon=120m -> 24 bars
    ('HS',  'LONG',  3.0, 1.5,  48),  # horizon=240m -> 48 bars
    ('BM',  'LONG',  2.5, 1.25, 48),  # horizon=240m -> 48 bars
    ('CE',  'SHORT', 3.0, 1.25, 24),  # horizon=120m -> 24 bars
]


def test_strategy_on_period(label, start_date, end_date=None):
    """Run all champion strategies on a date range and print results."""
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")

    for symbol, direction, vol_z_thr, div_z_thr, horizon in CHAMPIONS:
        rows = load_data(symbol, start_date, end_date)
        n = len(rows)
        if n < 100:
            print(f"  {symbol:4s} {direction:5s}  — NO DATA ({n} rows)")
            continue

        close  = [float(r[5] or 0.0) for r in rows]
        volume = [float(r[6] or 0.0) for r in rows]
        open_px= [float(r[7] or 0.0) for r in rows]
        fiz_net = [float((r[1] or 0) - (r[2] or 0)) for r in rows]
        yur_net = [float((r[3] or 0) - (r[4] or 0)) for r in rows]

        vol_z = zs(volume, 20)
        fiz_z = zs(fiz_net, 20)
        yur_z = zs(yur_net, 20)

        # Signal condition
        if direction == 'LONG':
            def cond(fz, yz): return fz < 0 and yz > 0
        else:
            def cond(fz, yz): return fz > 0 and yz < 0

        returns = []
        max_hor = horizon
        for i in range(20, n - max_hor - 1):
            if vol_z[i] < vol_z_thr:
                continue
            fzi, yzi = fiz_z[i], yur_z[i]
            if abs(fzi) < div_z_thr or abs(yzi) < div_z_thr:
                continue
            if fzi * yzi >= 0:
                continue
            if not cond(fzi, yzi):
                continue

            entry_px = open_px[i + 1]
            if entry_px <= 0 or close[i] <= 0:
                continue

            exit_px = close[i + horizon]
            if exit_px <= 0:
                continue

            ret = (exit_px - entry_px) / entry_px * 100.0
            if direction == 'SHORT':
                ret = -ret
            returns.append(ret)

        if len(returns) < 1:
            print(f"  {symbol:4s} {direction:5s}  n=0  no signals")
            continue

        wins = sum(1 for r in returns if r > 0)
        losses = sum(1 for r in returns if r <= 0)
        wr = wins / len(returns) * 100.0
        gains = sum(r for r in returns if r > 0)
        loss_sum = abs(sum(r for r in returns if r < 0))
        pf = gains / loss_sum if loss_sum > 0 else (99.9 if gains > 0 else 0.0)
        avg_ret = sum(returns) / len(returns)
        dd = calc_drawdown(returns)

        print(f"  {symbol:4s} {direction:5s}  n={len(returns):4d}  WR={wr:5.1f}%  PF={pf:6.2f}  "
              f"avg={avg_ret:+7.3f}%  DD={dd:6.2f}%")


def main():
    test_strategy_on_period("2025 (2025-01-01 — 2025-12-31)", '2025-01-01', '2026-01-01')
    test_strategy_on_period("2026 (2026-01-01 — 2026-06-06)", '2026-01-01')
    print()


if __name__ == '__main__':
    main()
