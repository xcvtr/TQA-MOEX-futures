#!/usr/bin/env python3
"""
OI Divergence Analysis — Multi-TF Systematic Search.

Phase 1: Baseline scan for all tickers across all TF params.
Saves results to reports/oi_divergence_scan/
"""

import sys, os, json, math
from datetime import datetime
from itertools import product

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import clickhouse_connect
import numpy as np
import pandas as pd
from config import CH_HOST, CH_PORT, CH_DB

# ── Params ──────────────────────────────────────────────────────────────────
TICKERS = ["Si","BR","Eu","GD","SR","GZ","LK","VB","RN","NM","AF","AL",
           "SN","SV","NG","PD","PT","RI","MX","ED","CR","IMOEXF",
           "CNYRUBF","USDRUBF","GLDRUBF"]

W_VALUES = [10, 20, 40]
T_VALUES = [1.0, 1.5, 2.0, 2.5]
HOLD_VALUES = [5, 10, 20]
SL_PCT = 0.05
COMMISSION = 2.0  # rub per trade round-trip
CAPITAL = 100000.0

OUT_DIR = "reports/oi_divergence_scan"

# ── Helpers ─────────────────────────────────────────────────────────────────

def get_ch():
    return clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)


def zscore(series, window):
    out = np.full(len(series), 0.0, dtype=np.float64)
    for i in range(window, len(series)):
        chunk = series[i - window:i]
        mu = np.mean(chunk)
        sd = np.std(chunk)
        if sd > 1e-12:
            out[i] = (series[i] - mu) / sd
    return out


def load_data(ch, ticker):
    query = """
    SELECT
        p.time,
        p.open,
        p.high,
        p.low,
        p.close,
        p.volume,
        o.fiz_buy,
        o.fiz_sell,
        o.yur_buy,
        o.yur_sell,
        o.total_oi
    FROM moex.prices_5m_oi AS o
    INNER JOIN moex.prices_5m AS p
        ON p.symbol = o.symbol AND p.time = o.time
    WHERE p.symbol = {ticker:String}
    ORDER BY p.time
    """
    rows = ch.query(query, parameters={"ticker": ticker}).result_rows
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=[
        "time", "open", "high", "low", "close", "volume",
        "fiz_buy", "fiz_sell", "yur_buy", "yur_sell", "total_oi"
    ])
    return df


def load_go(ch, ticker):
    query = "SELECT go_rub FROM moex.securities WHERE ticker = {ticker:String}"
    rows = ch.query(query, parameters={"ticker": ticker}).result_rows
    if rows and rows[0][0] is not None:
        return float(rows[0][0])
    return None


def backtest(df, W, T, hold, sl_pct):
    closes = df["close"].values.astype(np.float64)
    fiz_net = (df["fiz_buy"].values - df["fiz_sell"].values).astype(np.float64)
    yur_net = (df["yur_buy"].values - df["yur_sell"].values).astype(np.float64)

    fiz_z = zscore(fiz_net, W)
    yur_z = zscore(yur_net, W)
    divergence = fiz_z - yur_z

    n = len(df)
    trades = []
    i = W
    while i < n:
        if i >= n:
            break
        div = divergence[i]
        if div > T:
            direction = "SHORT"
        elif div < -T:
            direction = "LONG"
        else:
            i += 1
            continue

        entry_price = closes[i]

        exit_idx = min(i + hold, n - 1)
        exit_price = closes[exit_idx]

        stop_idx = None
        for j in range(i + 1, exit_idx + 1):
            if direction == "LONG":
                ret_to_j = (closes[j] - entry_price) / entry_price
                if ret_to_j <= -sl_pct:
                    exit_price = closes[j]
                    exit_idx = j
                    break
            else:
                ret_to_j = (entry_price - closes[j]) / entry_price
                if ret_to_j <= -sl_pct:
                    exit_price = closes[j]
                    exit_idx = j
                    break

        if direction == "LONG":
            ret = (exit_price - entry_price) / entry_price
        else:
            ret = (entry_price - exit_price) / entry_price

        pnl = ret * CAPITAL
        pnl_net = pnl - COMMISSION
        ret_net = pnl_net / CAPITAL

        trades.append({
            "entry_time": str(df.iloc[i]["time"]),
            "exit_time": str(df.iloc[exit_idx]["time"]),
            "direction": direction,
            "entry_price": round(float(entry_price), 4),
            "exit_price": round(float(exit_price), 4),
            "ret_pct": round(float(ret) * 100, 4),
            "ret_net_pct": round(float(ret_net) * 100, 4),
            "bars_held": exit_idx - i,
        })
        i = exit_idx + 1

    return trades


def compute_stats(trades):
    if not trades:
        return {"trades": 0, "wr": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
                "total_ret_pct": 0.0, "max_dd_pct": 0.0, "calmar": 0.0}

    returns = np.array([t["ret_net_pct"] for t in trades])
    wins = returns[returns > 0]
    losses = returns[returns <= 0]

    wr = len(wins) / len(returns) * 100 if len(returns) > 0 else 0.0
    avg_win = float(np.mean(wins)) if len(wins) > 0 else 0.0
    avg_loss = float(np.mean(losses)) if len(losses) > 0 else 0.0

    cum = np.cumsum(returns)
    peak = np.maximum.accumulate(cum)
    dd = peak - cum
    max_dd = float(np.max(dd)) if len(dd) > 0 else 0.0
    total_ret = float(cum[-1]) if len(cum) > 0 else 0.0
    calmar = total_ret / max_dd if max_dd > 1e-12 else (total_ret if total_ret > 0 else 0.0)

    return {
        "trades": len(trades),
        "wr": round(wr, 2),
        "avg_win": round(avg_win, 4),
        "avg_loss": round(avg_loss, 4),
        "total_ret_pct": round(total_ret, 4),
        "max_dd_pct": round(max_dd, 4),
        "calmar": round(calmar, 4),
    }


def main():
    ch = get_ch()
    os.makedirs(OUT_DIR, exist_ok=True)

    all_results = []
    per_ticker = {}

    for ticker in TICKERS:
        print(f"\n{'='*60}")
        print(f"  {ticker}")
        print(f"{'='*60}")

        df = load_data(ch, ticker)
        if df is None or len(df) < 50:
            print(f"  ⚠ Нет данных (JOIN пуст) — пропуск")
            continue

        go_rub = load_go(ch, ticker)
        if go_rub is None or go_rub <= 0:
            print(f"  ⚠ Нет ГО — пропуск")
            continue

        print(f"  Баров: {len(df)}, ГО: {go_rub:.2f}")

        ticker_results = []
        for W, T, hold in product(W_VALUES, T_VALUES, HOLD_VALUES):
            trades = backtest(df, W, T, hold, SL_PCT)
            stats = compute_stats(trades)
            stats["ticker"] = ticker
            stats["W"] = W
            stats["T"] = T
            stats["hold"] = hold
            stats["go_rub"] = go_rub
            stats["trades_detail"] = trades
            ticker_results.append(stats)
            all_results.append(stats)

        best = max(ticker_results, key=lambda x: x["calmar"])
        print(f"  Лучший: W={best['W']} T={best['T']} hold={best['hold']} -> "
              f"return={best['total_ret_pct']:.2f}% DD={best['max_dd_pct']:.2f}% "
              f"Calmar={best['calmar']:.3f} WR={best['wr']:.1f}% trades={best['trades']}")

        per_ticker[ticker] = ticker_results

        detail_path = os.path.join(OUT_DIR, f"{ticker}_params.json")
        with open(detail_path, "w") as f:
            json.dump(ticker_results, f, indent=2, default=str)
        print(f"  → Сохранено {detail_path}")

    # ── SUMMARY.csv ─────────────────────────────────────────────────────
    csv_rows = []
    for r in all_results:
        csv_rows.append({
            "ticker": r["ticker"],
            "W": r["W"],
            "T": r["T"],
            "hold": r["hold"],
            "trades": r["trades"],
            "wr": r["wr"],
            "total_ret_pct": r["total_ret_pct"],
            "max_dd_pct": r["max_dd_pct"],
            "calmar": r["calmar"],
            "avg_win": r["avg_win"],
            "avg_loss": r["avg_loss"],
        })
    df_summary = pd.DataFrame(csv_rows)
    csv_path = os.path.join(OUT_DIR, "SUMMARY.csv")
    df_summary.to_csv(csv_path, index=False)
    print(f"\n✅ SUMMARY.csv: {csv_path} ({len(df_summary)} строк)")

    # ── Top-10 по Calmar и WR ──────────────────────────────────────────
    top_calmar = sorted(all_results, key=lambda x: x["calmar"], reverse=True)[:10]
    top_wr = sorted(all_results, key=lambda x: x["wr"], reverse=True)[:10]

    # ── SUMMARY.md ──────────────────────────────────────────────────────
    lines = []
    lines.append("# OI Divergence Scan — Summary\n")
    lines.append(f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
    lines.append(f"**Params:** W={W_VALUES}, T={T_VALUES}, hold={HOLD_VALUES}, SL={SL_PCT*100:.0f}%\n")
    lines.append(f"**Tickers tested:** {len(per_ticker)} / {len(TICKERS)}\n")
    lines.append(f"**Commission:** {COMMISSION} руб/сделку, **Capital:** {CAPITAL:.0f} руб\n")

    lines.append("---\n")
    lines.append("## Top-10 by Calmar\n\n")
    lines.append("| # | ticker | W | T | hold | trades | WR% | return% | DD% | Calmar |\n")
    lines.append("|---|--------|---|---|------|--------|-----|---------|-----|--------|\n")
    for i, r in enumerate(top_calmar, 1):
        lines.append(
            f"| {i} | {r['ticker']} | {r['W']} | {r['T']} | {r['hold']} "
            f"| {r['trades']} | {r['wr']:.1f} | {r['total_ret_pct']:.2f} "
            f"| {r['max_dd_pct']:.2f} | {r['calmar']:.3f} |\n"
        )

    lines.append("\n---\n")
    lines.append("## Top-10 by WR\n\n")
    lines.append("| # | ticker | W | T | hold | trades | WR% | return% | DD% | Calmar |\n")
    lines.append("|---|--------|---|---|------|--------|-----|---------|-----|--------|\n")
    for i, r in enumerate(top_wr, 1):
        lines.append(
            f"| {i} | {r['ticker']} | {r['W']} | {r['T']} | {r['hold']} "
            f"| {r['trades']} | {r['wr']:.1f} | {r['total_ret_pct']:.2f} "
            f"| {r['max_dd_pct']:.2f} | {r['calmar']:.3f} |\n"
        )

    lines.append("\n---\n")
    lines.append("## All Tickers Summary (best per ticker by Calmar)\n\n")
    lines.append("| ticker | W | T | hold | trades | WR% | return% | DD% | Calmar |\n")
    lines.append("|--------|---|---|------|--------|-----|---------|-----|--------|\n")
    for tk in sorted(per_ticker.keys()):
        best = max(per_ticker[tk], key=lambda x: x["calmar"])
        lines.append(
            f"| {tk} | {best['W']} | {best['T']} | {best['hold']} "
            f"| {best['trades']} | {best['wr']:.1f} | {best['total_ret_pct']:.2f} "
            f"| {best['max_dd_pct']:.2f} | {best['calmar']:.3f} |\n"
        )

    md_path = os.path.join(OUT_DIR, "SUMMARY.md")
    with open(md_path, "w") as f:
        f.writelines(lines)
    print(f"✅ SUMMARY.md: {md_path}")

    print(f"\n{'='*60}")
    print(f"  Done. Results in {OUT_DIR}/")
    print(f"  Tickers: {len(per_ticker)}/{len(TICKERS)}")
    print(f"  Total param combos: {len(all_results)}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
