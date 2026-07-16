#!/usr/bin/env python3 -u
"""Dragon sweep по всем MOEX futures — TZ filter в SQL, flush, быстрый."""
import sys, os, argparse
from datetime import datetime, timezone, timedelta
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import clickhouse_connect as cc

CH = dict(host="10.0.0.60", port=8123, database="moex")
TRADE_COST = 4
TIMEOUT_BARS = 12

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dragon.prod.engine import check_signal

DP = {"impulse_pct": 0.3, "retrace_max_pct": 70, "hump_extension": 0.1, "lookback": 100}


def get_all_tickers():
    import psycopg2
    conn = psycopg2.connect(host="10.0.0.60", port=5432, dbname="moex", user="postgres")
    cur = conn.cursor()
    cur.execute("SELECT ticker, asset_code, min_step, step_price FROM futures.ticker_specs ORDER BY ticker")
    rows = cur.fetchall()
    cur.close(); conn.close()
    return {r[0]: {"asset": r[1], "ms": float(r[2]) if r[2] else 0.01, "sp": float(r[3]) if r[3] else 1.0}
            for r in rows}


def load_ohlc(asset_code, days=365):
    """Load MOEX-hours only bars — TZ filter in SQL."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    ch = cc.get_client(**CH)
    # Filter 15:00-23:45 IRK right in SQL
    rows = ch.query(
        "SELECT SYSTIME, pr_open, pr_high, pr_low, pr_close "
        "FROM moex.tradestats_fo "
        "WHERE asset_code = %(asset)s AND SYSTIME >= %(cutoff)s "
        "  AND toDayOfWeek(SYSTIME) BETWEEN 1 AND 5 "
        "  AND toHour(SYSTIME) BETWEEN 15 AND 23 "
        "  AND NOT (toHour(SYSTIME) = 23 AND toMinute(SYSTIME) > 45) "
        "ORDER BY SYSTIME",
        parameters={"asset": asset_code, "cutoff": cutoff}
    ).result_rows
    ch.close()
    return [{"ts": r[0], "opn": float(r[1]) or 0, "hi": float(r[2]) or 0,
             "lo": float(r[3]) or 0, "prc": float(r[4]) or 0} for r in rows]


def calc_pnl(entry, exit_, direction, ms, sp):
    raw = (exit_ - entry) / ms * sp - TRADE_COST
    return raw if direction == "long" else -raw


def backtest_one(ticker, spec, days=365):
    bars = load_ohlc(spec["asset"], days)
    if len(bars) < 50:
        return []
    ms, sp = spec["ms"], spec["sp"]
    trades, open_pos = [], None
    for i in range(30, len(bars)):
        bd = {"prc": bars[i]["prc"], "bars_list": bars[:i+1]}
        sig = check_signal(bd, ticker, DP)
        if open_pos:
            bar = bars[i]
            if i - open_pos["bar_idx"] >= TIMEOUT_BARS:
                pnl = calc_pnl(open_pos["price"], bar["prc"], open_pos["dir"], ms, sp)
                trades.append({"pnl": pnl, "reason": "timeout"})
                open_pos = None
                continue
            ep = open_pos["price"]
            if not open_pos.get("trail"):
                if (open_pos["dir"] == "long" and bar["hi"] >= ep * 1.005) or \
                   (open_pos["dir"] == "short" and bar["lo"] <= ep * 0.995):
                    open_pos["trail"] = True
                    open_pos["tl"] = bar["hi"] * 0.997 if open_pos["dir"] == "long" else bar["lo"] * 1.003
            exit_p = None
            if open_pos.get("trail"):
                if (open_pos["dir"] == "long" and bar["lo"] <= open_pos["tl"]) or \
                   (open_pos["dir"] == "short" and bar["hi"] >= open_pos["tl"]):
                    exit_p = open_pos["tl"]
            if not exit_p:
                sl = ep * 0.993 if open_pos["dir"] == "long" else ep * 1.007
                if (open_pos["dir"] == "long" and bar["lo"] <= sl) or \
                   (open_pos["dir"] == "short" and bar["hi"] >= sl):
                    exit_p = sl
            if exit_p:
                pnl = calc_pnl(ep, exit_p, open_pos["dir"], ms, sp)
                trades.append({"pnl": pnl, "reason": "exit"})
                open_pos = None
        if sig and not open_pos:
            open_pos = {"bar_idx": i, "price": sig["entry_price"], "dir": sig["direction"]}
    if open_pos:
        pnl = calc_pnl(open_pos["price"], bars[-1]["prc"], open_pos["dir"], ms, sp)
        trades.append({"pnl": pnl, "reason": "eof"})
    return trades


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--min-trades", type=int, default=10)
    args = parser.parse_args()

    tickers = get_all_tickers()
    results = {}
    total = len(tickers)
    for idx, (ticker, spec) in enumerate(sorted(tickers.items())):
        print(f"[{idx+1}/{total}] {ticker}...", end=" ", flush=True)
        try:
            trades = backtest_one(ticker, spec, args.days)
            n = len(trades)
            if n < args.min_trades:
                print(f"n={n} skp", flush=True)
                continue
            pnl = sum(t["pnl"] for t in trades)
            wins = [t for t in trades if t["pnl"] > 0]
            losses = [t for t in trades if t["pnl"] <= 0]
            wr = len(wins) / n * 100
            tp = sum(t["pnl"] for t in wins)
            tn = sum(abs(t["pnl"]) for t in losses)
            pf = tp / tn if tn > 0 else float("inf")
            aw = tp / len(wins) if wins else 0
            al = tn / len(losses) if losses else 0
            results[ticker] = {"n": n, "wr": round(wr, 1), "pnl": round(pnl), "pf": round(pf, 2),
                               "aw": round(aw), "al": round(al)}
            print(f"n={n:5d} wr={wr:5.1f}% pnl={pnl:+8.0f} pf={pf:.2f}", flush=True)
        except Exception as e:
            print(f"ERR: {e}", flush=True)

    print(f"\n=== PF>1.2 ({len(results)} tickers) ===", flush=True)
    good = {t: r for t, r in results.items() if r["pf"] > 1.2 and r["n"] >= args.min_trades}
    for t in sorted(good, key=lambda x: good[x]["pnl"] / max(good[x]["n"], 1), reverse=True):
        r = good[t]
        print(f"  {t:4s} n={r['n']:5d} wr={r['wr']:5.1f}% pnl={r['pnl']:+8.0f} pf={r['pf']:.2f}", flush=True)

    if good:
        print(f"\n=== TOP 20 by PnL ===", flush=True)
        for t in sorted(good, key=lambda x: good[x]["pnl"], reverse=True)[:20]:
            r = good[t]
            print(f"  {t:4s} n={r['n']:5d} wr={r['wr']:5.1f}% pnl={r['pnl']:+8.0f} pf={r['pf']:.2f}", flush=True)
