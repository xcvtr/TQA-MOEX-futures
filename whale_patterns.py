"""
whale_patterns.py — Whale detection pattern analysis for MOEX OI + price data.

Pipeline:
  1. load_daily_metrics() — EOD OI snapshots + daily price bars
  2. compute_pattern_signals() — 4 patterns + combinations
  3. backtest() — evaluate pattern performance

Patterns:
  WHALE_TRAP: YUR avg position ↑ + FIZ accounts ↑ → reversal
  RETAIL_CLIMAX: FIZ account count Z > 2.5 → reversal  
  WHALE_DUMP: YUR avg ↓ while price ↑ → distribution
  FIZ_PANIC: FIZ short accounts spike → reversal up
"""
import sys, os
from datetime import datetime, date, timedelta
import json
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD

import psycopg2
import numpy as np
from scipy import stats as scipy_stats

# ── Config ────────────────────────────────────────────────────────────────
ZSCORE_WINDOW = 20  # rolling window for z-score calculation
LOOKAHEAD_BARS = 5  # bars to look ahead for reversal confirmation
MIN_MOVE_PCT = 0.5  # minimum move for a valid signal (%)
MIN_SAMPLES = 30    # minimum samples for statistical significance

# ── Data Loading ──────────────────────────────────────────────────────────

def get_db():
    conn = psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD)
    return conn

def load_daily_oi(conn, symbol: str, start_date: date = None, end_date: date = None) -> list[dict]:
    """
    Load end-of-day OI snapshots for FIZ and YUR.
    Returns list of dicts: {date, fiz_long, fiz_short, fiz_lnum, fiz_snum,
                            yur_long, yur_short, yur_lnum, yur_snum}
    """
    conditions = ["symbol = %s"]
    params = [symbol]
    if start_date:
        conditions.append("time::date >= %s")
        params.append(start_date)
    if end_date:
        conditions.append("time::date <= %s")
        params.append(end_date)

    cur = conn.cursor()
    cur.execute(f"""
        SELECT DISTINCT ON (time::date, clgroup)
            time::date as dt, clgroup,
            buy_orders, sell_orders,
            buy_accounts, sell_accounts
        FROM openinterest_moex
        WHERE {' AND '.join(conditions)}
        ORDER BY time::date, clgroup, time DESC
    """, params)

    # Group by date
    daily = defaultdict(lambda: {"date": None, "fiz_long": 0, "fiz_short": 0,
                                  "fiz_lnum": 0, "fiz_snum": 0,
                                  "yur_long": 0, "yur_short": 0,
                                  "yur_lnum": 0, "yur_snum": 0})
    for r in cur.fetchall():
        dt, cg, plong, pshort, lnum, snum = r
        prefix = "fiz" if cg == 0 else "yur"
        daily[dt]["date"] = dt
        daily[dt][f"{prefix}_long"] = plong or 0
        daily[dt][f"{prefix}_short"] = abs(pshort or 0)
        daily[dt][f"{prefix}_lnum"] = lnum or 0
        daily[dt][f"{prefix}_snum"] = snum or 0

    cur.close()
    result = sorted(daily.values(), key=lambda x: x["date"])
    # Filter out days with no account data
    result = [r for r in result if r["fiz_lnum"] > 0 and r["yur_lnum"] > 0]
    return result


def load_daily_prices(conn, symbol: str, start_date: date = None, end_date: date = None) -> list[dict]:
    """Load daily price bars from moex_prices (D1)."""
    conditions = ["symbol = %s"]
    params = [symbol]
    if start_date:
        conditions.append("time::date >= %s")
        params.append(start_date)
    if end_date:
        conditions.append("time::date <= %s")
        params.append(end_date)

    cur = conn.cursor()
    cur.execute(f"""
        SELECT time::date as dt, open, high, low, close, volume
        FROM moex_prices
        WHERE {' AND '.join(conditions)}
        ORDER BY time::date
    """, params)

    result = [{"date": r[0], "open": float(r[1]), "high": float(r[2]),
               "low": float(r[3]), "close": float(r[4]), "volume": r[5] or 0}
              for r in cur.fetchall()]
    cur.close()
    return result


def create_combined_dataset(oi_data: list[dict], price_data: list[dict]) -> list[dict]:
    """Merge OI and price data by date. Returns only dates with both."""
    price_map = {p["date"]: p for p in price_data}
    combined = []
    for oi in oi_data:
        dt = oi["date"]
        if dt not in price_map:
            continue
        p = price_map[dt]
        combined.append({
            "date": dt,
            # OI
            "fiz_long": oi["fiz_long"],
            "fiz_short": oi["fiz_short"],
            "fiz_lnum": oi["fiz_lnum"],
            "fiz_snum": oi["fiz_snum"],
            "yur_long": oi["yur_long"],
            "yur_short": oi["yur_short"],
            "yur_lnum": oi["yur_lnum"],
            "yur_snum": oi["yur_snum"],
            # Derived
            "fiz_avg_long": oi["fiz_long"] / max(oi["fiz_lnum"], 1),
            "fiz_avg_short": oi["fiz_short"] / max(oi["fiz_snum"], 1),
            "yur_avg_long": oi["yur_long"] / max(oi["yur_lnum"], 1),
            "yur_avg_short": oi["yur_short"] / max(oi["yur_snum"], 1),
            "fiz_net": oi["fiz_long"] - oi["fiz_short"],
            "yur_net": oi["yur_long"] - oi["yur_short"],
            "yur_fiz_ratio": (oi["yur_avg_long"] / max(oi["fiz_avg_long"], 1)),
            # Price
            "close": p["close"],
            "high": p["high"],
            "low": p["low"],
            "open": p["open"],
            "volume": p["volume"],
        })
    return combined


# ── Feature Computation ───────────────────────────────────────────────────

def compute_zscore(series: list, window: int = ZSCORE_WINDOW) -> list:
    """Rolling Z-score. First window-1 values get NaN."""
    arr = np.array(series, dtype=float)
    zs = np.full_like(arr, np.nan)
    for i in range(window - 1, len(arr)):
        window_data = arr[i - window + 1:i + 1]
        if np.std(window_data) > 0:
            zs[i] = (arr[i] - np.mean(window_data)) / np.std(window_data)
        else:
            zs[i] = 0.0
    return zs.tolist()


def compute_derived_metrics(data: list[dict], window: int = ZSCORE_WINDOW) -> list[dict]:
    """Add z-scores and derived metrics to combined dataset."""
    if len(data) < window:
        return data

    # Extract series
    fiz_lnum = [r["fiz_lnum"] for r in data]
    fiz_snum = [r["fiz_snum"] for r in data]
    yur_lnum = [r["yur_lnum"] for r in data]
    yur_avg_long = [r["yur_avg_long"] for r in data]
    yur_avg_short = [r["yur_avg_short"] for r in data]
    fiz_avg_long = [r["fiz_avg_long"] for r in data]
    yur_fiz_ratio = [r["yur_fiz_ratio"] for r in data]

    # Compute z-scores
    fiz_lnum_z = compute_zscore(fiz_lnum, window)
    fiz_snum_z = compute_zscore(fiz_snum, window)
    yur_avg_long_z = compute_zscore(yur_avg_long, window)
    yur_avg_short_z = compute_zscore(yur_avg_short, window)
    fiz_avg_long_z = compute_zscore(fiz_avg_long, window)
    yur_fiz_ratio_z = compute_zscore(yur_fiz_ratio, window)

    # Compute deltas
    fiz_lnum_delta = [0] + [fiz_lnum[i] - fiz_lnum[i-1] for i in range(1, len(fiz_lnum))]
    yur_avg_long_delta = [0] + [yur_avg_long[i] - yur_avg_long[i-1] for i in range(1, len(yur_avg_long))]

    # 1-day forward price return
    future_returns = []
    for i in range(len(data)):
        if i + 1 < len(data):
            future_returns.append((data[i+1]["close"] - data[i]["close"]) / data[i]["close"] * 100)
        else:
            future_returns.append(0.0)

    # 5-day forward price return
    future_returns_5 = []
    for i in range(len(data)):
        if i + LOOKAHEAD_BARS < len(data):
            future_returns_5.append((data[i + LOOKAHEAD_BARS]["close"] - data[i]["close"]) / data[i]["close"] * 100)
        else:
            future_returns_5.append(0.0)

    for i, r in enumerate(data):
        r["fiz_lnum_z"] = fiz_lnum_z[i]
        r["fiz_snum_z"] = fiz_snum_z[i]
        r["yur_avg_long_z"] = yur_avg_long_z[i]
        r["yur_avg_short_z"] = yur_avg_short_z[i]
        r["fiz_avg_long_z"] = fiz_avg_long_z[i]
        r["yur_fiz_ratio_z"] = yur_fiz_ratio_z[i]
        r["fiz_lnum_delta"] = fiz_lnum_delta[i]
        r["yur_avg_long_delta"] = yur_avg_long_delta[i]
        r["return_1d"] = future_returns[i]
        r["return_5d"] = future_returns_5[i]

    return data


# ── Pattern Detectors ─────────────────────────────────────────────────────

def detect_whale_trap(data: list[dict], i: int) -> dict | None:
    """
    WHALE_TRAP: YUR avg position spikes up (z > 2.0) AND
                FIZ accounts also increase (z > 1.5) →
                reversal expected within N bars.
    """
    if i < ZSCORE_WINDOW or i + LOOKAHEAD_BARS >= len(data):
        return None
    r = data[i]

    if r["yur_avg_long_z"] is None or np.isnan(r["yur_avg_long_z"]):
        return None
    if r["fiz_lnum_z"] is None or np.isnan(r["fiz_lnum_z"]):
        return None

    # YUR whales adding longs aggressively
    if r["yur_avg_long_z"] < 2.0:
        return None
    # FIZ retail following
    if r["fiz_lnum_z"] < 1.5:
        return None

    # Price should NOT have already moved significantly (otherwise it's chase, not trap)
    if abs(r["return_1d"]) > 2.0:
        return None

    # Check: reversal happens? (close direction in N days)
    future_ret = data[min(i + LOOKAHEAD_BARS, len(data) - 1)]["return_5d"]
    # Signal is bearish (trap → price down)
    # But could also be bullish (short trap → price up)
    # For now: if price was up before trap, expect reversal down
    #          if price was down, expect reversal up
    price_trend = 0
    if i > 5:
        price_trend = (data[i]["close"] - data[i-5]["close"]) / data[i-5]["close"] * 100

    predicted_direction = -1 if price_trend > 0 else 1  # reverse
    actual_direction = 1 if future_ret > MIN_MOVE_PCT else (-1 if future_ret < -MIN_MOVE_PCT else 0)
    hit = (predicted_direction == actual_direction)

    return {
        "type": "WHALE_TRAP",
        "date": r["date"],
        "price": r["close"],
        "signal": predicted_direction,
        "future_return_pct": round(future_ret, 2),
        "hit": hit,
        "metrics": {
            "yur_avg_long_z": round(r["yur_avg_long_z"], 2),
            "fiz_lnum_z": round(r["fiz_lnum_z"], 2),
            "price_trend_5d": round(price_trend, 2),
        }
    }


def detect_retail_climax(data: list[dict], i: int) -> dict | None:
    """
    RETAIL_CLIMAX: FIZ account count Z > 2.5 (extreme).
                  Retail crowding → reversal.
    """
    if i < ZSCORE_WINDOW or i + LOOKAHEAD_BARS >= len(data):
        return None
    r = data[i]

    if r["fiz_lnum_z"] is None or np.isnan(r["fiz_lnum_z"]):
        return None

    if r["fiz_lnum_z"] < 2.5:
        return None

    # Direction: check if retail is predominantly long or short
    fiz_long_pct = r["fiz_long"] / max(r["fiz_long"] + r["fiz_short"], 1) * 100
    predicted_direction = -1 if fiz_long_pct > 60 else (1 if fiz_long_pct < 40 else 0)

    if predicted_direction == 0:
        return None

    future_ret = data[min(i + LOOKAHEAD_BARS, len(data) - 1)]["return_5d"]
    actual_direction = 1 if future_ret > MIN_MOVE_PCT else (-1 if future_ret < -MIN_MOVE_PCT else 0)
    hit = (predicted_direction == actual_direction)

    return {
        "type": "RETAIL_CLIMAX",
        "date": r["date"],
        "price": r["close"],
        "signal": predicted_direction,
        "future_return_pct": round(future_ret, 2),
        "hit": hit,
        "metrics": {
            "fiz_lnum_z": round(r["fiz_lnum_z"], 2),
            "fiz_long_pct": round(fiz_long_pct, 1),
        }
    }


def detect_whale_dump(data: list[dict], i: int) -> dict | None:
    """
    WHALE_DUMP: YUR avg position DECREASES while price still RISING.
               Whales distributing to retail.
    """
    if i < ZSCORE_WINDOW or i + LOOKAHEAD_BARS >= len(data):
        return None
    r = data[i]

    if r["yur_avg_long_z"] is None or np.isnan(r["yur_avg_long_z"]):
        return None
    if r["fiz_lnum_z"] is None or np.isnan(r["fiz_lnum_z"]):
        return None

    # YUR avg long DECREASING (z < -1.5)
    if r["yur_avg_long_z"] > -1.5:
        return None

    # Price still RISING (up > 1% in last 5 days)
    price_trend = 0
    if i > 5:
        price_trend = (r["close"] - data[i-5]["close"]) / data[i-5]["close"] * 100
    if price_trend < 1.0:
        return None

    # FIZ accounts still INCREASING (retail buying the dip? no, buying the top)
    if r["fiz_lnum_z"] < 1.0:
        return None

    future_ret = data[min(i + LOOKAHEAD_BARS, len(data) - 1)]["return_5d"]
    predicted_direction = -1  # dump → price down
    actual_direction = 1 if future_ret > MIN_MOVE_PCT else (-1 if future_ret < -MIN_MOVE_PCT else 0)
    hit = (predicted_direction == actual_direction)

    return {
        "type": "WHALE_DUMP",
        "date": r["date"],
        "price": r["close"],
        "signal": predicted_direction,
        "future_return_pct": round(future_ret, 2),
        "hit": hit,
        "metrics": {
            "yur_avg_long_z": round(r["yur_avg_long_z"], 2),
            "price_trend_5d": round(price_trend, 2),
            "fiz_lnum_z": round(r["fiz_lnum_z"], 2),
        }
    }


def detect_fiz_panic(data: list[dict], i: int) -> dict | None:
    """
    FIZ_PANIC: FIZ short accounts spike (Z > 2.0).
              Retail piling into shorts → reversal up.
    """
    if i < ZSCORE_WINDOW or i + LOOKAHEAD_BARS >= len(data):
        return None
    r = data[i]

    if r["fiz_snum_z"] is None or np.isnan(r["fiz_snum_z"]):
        return None

    if r["fiz_snum_z"] < 2.0:
        return None

    future_ret = data[min(i + LOOKAHEAD_BARS, len(data) - 1)]["return_5d"]
    predicted_direction = 1  # panic shorts → reversal UP
    actual_direction = 1 if future_ret > MIN_MOVE_PCT else (-1 if future_ret < -MIN_MOVE_PCT else 0)
    hit = (predicted_direction == actual_direction)

    return {
        "type": "FIZ_PANIC",
        "date": r["date"],
        "price": r["close"],
        "signal": predicted_direction,
        "future_return_pct": round(future_ret, 2),
        "hit": hit,
        "metrics": {
            "fiz_snum_z": round(r["fiz_snum_z"], 2),
            "fiz_short_pct": round(r["fiz_short"] / max(r["fiz_long"] + r["fiz_short"], 1) * 100, 1),
        }
    }


# ── Pattern Combinations (Filters) ───────────────────────────────────────

def detect_combined(data: list[dict], i: int) -> list[dict]:
    """
    Run all detectors. Then build combinations.
    Returns list of signals, including combo signals.
    """
    signals = []
    detectors = [
        ("WHALE_TRAP", detect_whale_trap),
        ("RETAIL_CLIMAX", detect_retail_climax),
        ("WHALE_DUMP", detect_whale_dump),
        ("FIZ_PANIC", detect_fiz_panic),
    ]

    for name, detector in detectors:
        sig = detector(data, i)
        if sig:
            signals.append(sig)

    # Combinations: two patterns at the same time
    if len(signals) >= 2:
        # All pairs
        names = [s["type"] for s in signals]
        combo = {
            "type": "+".join(sorted(names)),
            "date": signals[0]["date"],
            "price": signals[0]["price"],
            "signal": signals[0]["signal"],
            "future_return_pct": signals[0]["future_return_pct"],
            "hit": all(s["hit"] for s in signals),
            "metrics": {f"{s['type']}_{k}": v for s in signals for k, v in s["metrics"].items()},
            "sub_signals": [s["type"] for s in signals],
        }
        signals.append(combo)

        # Triple combos
        if len(signals) >= 3:
            combo3 = {
                "type": "+".join(sorted(names)),
                "date": signals[0]["date"],
                "price": signals[0]["price"],
                "signal": signals[0]["signal"],
                "future_return_pct": signals[0]["future_return_pct"],
                "hit": all(s["hit"] for s in signals),
                "metrics": dict,
                "sub_signals": names,
            }
            signals.append(combo3)

    return signals


# ── Backtester ────────────────────────────────────────────────────────────

def backtest(data: list[dict], min_samples: int = MIN_SAMPLES) -> dict:
    """Run full backtest: detect patterns, compute winrate, profit factor."""
    if len(data) < ZSCORE_WINDOW + LOOKAHEAD_BARS:
        return {"error": f"Not enough data: {len(data)} rows (need {ZSCORE_WINDOW + LOOKAHEAD_BARS})"}

    all_signals = []
    for i in range(len(data)):
        sigs = detect_combined(data, i)
        all_signals.extend(sigs)

    # Group by pattern type
    by_type = defaultdict(list)
    for s in all_signals:
        by_type[s["type"]].append(s)

    results = {}
    for ptype, sigs in sorted(by_type.items()):
        total = len(sigs)
        hits = sum(1 for s in sigs if s["hit"])
        winrate = hits / total * 100 if total > 0 else 0
        avg_return = sum(s["future_return_pct"] for s in sigs) / total if total > 0 else 0
        win_returns = [s["future_return_pct"] for s in sigs if s["hit"]]
        loss_returns = [s["future_return_pct"] for s in sigs if not s["hit"]]
        avg_win = sum(win_returns) / len(win_returns) if win_returns else 0
        avg_loss = sum(loss_returns) / len(loss_returns) if loss_returns else 0
        profit_factor = abs(sum(win_returns) / sum(loss_returns)) if loss_returns and sum(loss_returns) != 0 else float('inf')

        results[ptype] = {
            "signals": total,
            "hits": hits,
            "winrate_pct": round(winrate, 1),
            "avg_return_pct": round(avg_return, 2),
            "avg_win_pct": round(avg_win, 2),
            "avg_loss_pct": round(avg_loss, 2),
            "profit_factor": round(profit_factor, 2),
        }

    # Overall stats
    totals = sum(results[p]["signals"] for p in results)
    total_hits = sum(results[p]["hits"] for p in results)
    return {
        "symbol": "Si",
        "date_range": f"{data[0]['date']}..{data[-1]['date']}",
        "total_days": len(data),
        "total_signals": totals,
        "overall_winrate": round(total_hits / totals * 100, 1) if totals > 0 else 0,
        "patterns": results,
        "signals": all_signals,  # full signal list for analysis
    }


# ── Main ──────────────────────────────────────────────────────────────────

def analyze_symbol(symbol: str, start_date: date = None, end_date: date = None) -> dict:
    """Full analysis pipeline for one symbol."""
    conn = get_db()
    try:
        oi = load_daily_oi(conn, symbol, start_date, end_date)
        prices = load_daily_prices(conn, symbol, start_date, end_date)
        data = create_combined_dataset(oi, prices)
        data = compute_derived_metrics(data)
        result = backtest(data)
        return result
    finally:
        conn.close()


def print_results(results: dict):
    """Pretty-print backtest results."""
    print(f"\n{'='*70}")
    print(f"  Pattern Backtest: {results.get('symbol', '?')}")
    print(f"  Period: {results.get('date_range', '?')}")
    print(f"  Days: {results.get('total_days', 0)}, Signals: {results.get('total_signals', 0)}")
    print(f"  Overall Winrate: {results.get('overall_winrate', 0)}%")
    print(f"{'='*70}")

    patterns = results.get("patterns", {})
    if not patterns:
        print("  No patterns detected")
        return

    print(f"\n  {'Pattern':25s} {'Signals':>8s} {'Hits':>6s} {'Winrate':>9s} {'AvgRet':>8s} {'AvgWin':>8s} {'AvgLoss':>8s} {'PF':>8s}")
    print(f"  {'-'*25:>25s} {'-'*8:>8s} {'-'*6:>6s} {'-'*9:>9s} {'-'*8:>8s} {'-'*8:>8s} {'-'*8:>8s} {'-'*8:>8s}")
    for ptype in sorted(patterns.keys()):
        p = patterns[ptype]
        print(f"  {ptype:25s} {p['signals']:>8d} {p['hits']:>6d} {p['winrate_pct']:>8.1f}% {p['avg_return_pct']:>8.2f}% {p['avg_win_pct']:>8.2f}% {p['avg_loss_pct']:>8.2f}% {p['profit_factor']:>8.2f}")


if __name__ == "__main__":
    sym = sys.argv[1] if len(sys.argv) > 1 else "Si"
    print(f"Running analysis for {sym}...")
    results = analyze_symbol(sym)
    print_results(results)

    # Save detailed signals
    outfile = f"/home/user/projects/TQA-MOEX/backtest_{sym}_{date.today()}.json"
    # Strip signal details for storage
    summary = {k: v for k, v in results.items() if k != "signals"}
    summary["signal_count"] = len(results.get("signals", []))
    with open(outfile, "w") as f:
        json.dump(summary, f, indent=2, default=str)
    print(f"\nResults saved to {outfile}")
