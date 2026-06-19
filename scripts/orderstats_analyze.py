#!/usr/bin/env python3
"""Quick orderstats analysis: put/cancel ratio, daily heatmap, anomaly scan.
Uses 10.0.0.63 directly (where the data lives).

Usage:
  python3 scripts/orderstats_analyze.py              # SBER last 30d
  python3 scripts/orderstats_analyze.py --secid GAZP  # Any ticker
  python3 scripts/orderstats_analyze.py --anomaly     # Scan all for anomalies
  python3 scripts/orderstats_analyze.py --heatmap     # Intraday heatmap
"""
import subprocess
import sys

CH_HOST = "10.0.0.63"
CH_DB = "moex_algopack_v2"

def ch_query(sql):
    r = subprocess.run(['clickhouse-client', '--host', CH_HOST, '--query', sql],
                       capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        print("ERROR:", r.stderr.strip())
        return None
    return r.stdout

def analyze_secid(secid, days=30):
    print(f"\n=== {secid}: Daily orderstats (last {days}d) ===\n")
    sql = f"""
        SELECT tradedate,
               sum(put_orders_b + put_orders_s) as put_total,
               sum(cancel_orders_b + cancel_orders_s) as cancel_total,
               sum(put_val_b + put_val_s) as put_val,
               sum(cancel_val_b + cancel_val_s) as cancel_val,
               round(avg(put_orders_b + put_orders_s)) as avg_put_per_bar,
               round(put_total / greatest(cancel_total, 1), 2) as pc_ratio,
               round((sum(put_orders_b) - sum(put_orders_s)) / greatest(sum(put_orders_b + put_orders_s), 1) * 100, 1) as order_imb_pct
        FROM {CH_DB}.orderstats_local
        WHERE secid = '{secid}'
          AND tradedate >= today() - {days}
        GROUP BY tradedate
        ORDER BY tradedate
        FORMAT PrettyCompact
    """
    print(ch_query(sql))

def anomaly_scan(days=7):
    print(f"\n=== Anomaly scan: cancel >> put (last {days}d) ===\n")
    sql = f"""
        SELECT tradedate, secid,
               sum(put_orders_b + put_orders_s) as put,
               sum(cancel_orders_b + cancel_orders_s) as cancel,
               round(cancel / greatest(put, 1), 2) as cancel_to_put
        FROM {CH_DB}.orderstats_local
        WHERE tradedate >= today() - {days}
        GROUP BY tradedate, secid
        HAVING cancel > put AND put > 10
        ORDER BY cancel_to_put DESC, tradedate DESC
        LIMIT 30
        FORMAT PrettyCompact
    """
    print(ch_query(sql))

def intraday_heatmap(secid='SBER', days=5):
    print(f"\n=== {secid}: Intraday 15min PC ratio (last {days}d) ===\n")
    sql = f"""
        SELECT
            toHour(parseDateTimeBestEffortOrNull(tradetime)) as h,
            toMinute(parseDateTimeBestEffortOrNull(tradetime)) / 15 * 15 as m,
            count() as bars,
            round(avg((put_orders_b + put_orders_s) / greatest(cancel_orders_b + cancel_orders_s, 1)), 2) as avg_ratio,
            round(avg(put_val_b + put_val_s), 0) as avg_val,
            round(quantile(0.9)((put_orders_b + put_orders_s) / greatest(cancel_orders_b + cancel_orders_s, 1)), 2) as p90_ratio
        FROM {CH_DB}.orderstats_local
        WHERE secid = '{secid}'
          AND tradedate >= today() - {days}
          AND cancel_orders_b + cancel_orders_s > 0
        GROUP BY h, m
        ORDER BY h, m
        FORMAT PrettyCompact
    """
    print(ch_query(sql))

if __name__ == "__main__":
    args = sys.argv[1:]
    if '--anomaly' in args:
        anomaly_scan()
    elif '--heatmap' in args:
        secid = args[args.index('--heatmap') + 1] if '--heatmap' in args and len(args) > args.index('--heatmap') + 1 and not args[args.index('--heatmap') + 1].startswith('--') else 'SBER'
        intraday_heatmap(secid)
    elif '--secid' in args:
        secid = args[args.index('--secid') + 1]
        days = int(args[args.index('--days') + 1]) if '--days' in args else 30
        analyze_secid(secid, days)
    else:
        analyze_secid('SBER')
        anomaly_scan(days=7)
        intraday_heatmap('SBER')
