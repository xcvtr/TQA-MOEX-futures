#!/usr/bin/env python3
"""
Direction 2: Futures Curve & Basis Analysis (TRIZ research plan).

Connects to PostgreSQL, loads OHLCV 5m data for Si, BR, RI,
aggregates to daily EOD per contract,
computes rolling basis between front and back contracts,
and checks if extreme contango/backwardation predicts price direction.

Key finding: individual 5m timestamps contain only ONE contract;
contracts overlap at the *daily* level (different contracts trade
on the same day at different times).  We use EOD close per contract.

Usage:
    /home/user/projects/TQA-MOEX/.venv/bin/python3 scripts/futures_curve_analysis.py
"""

import sys, os, re
from datetime import datetime, date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
import psycopg2

# ── config ──────────────────────────────────────────────────────────────
DB_CONFIG = dict(host="10.0.0.64", dbname="moex", user="postgres")
TODAY = date(2026, 6, 10)
REPORT_PATH = f"reports/{TODAY.isoformat()}-futures-curve.md"

TICKER_CFG = {
    "Si": {
        "label": "Si (USD/RUB)",
        "month_re": r"(?:GEN_SI-(\d+)\.(\d+))|Si([A-Z])(\d)",
    },
    "BR": {
        "label": "BR (Brent)",
        "month_re": r"(?:GEN_BR-(\d+)\.(\d+))|BR([A-Z])(\d)",
    },
    "RI": {
        "label": "RI (RTS Index)",
        "month_re": r"(?:GEN_RTS-(\d+)\.(\d+))|RI([A-Z])(\d)",
    },
}

MONTH_LETTER = {
    "F": 1, "G": 2, "H": 3, "J": 4, "K": 5, "M": 6,
    "N": 7, "Q": 8, "U": 9, "V": 10, "X": 11, "Z": 12,
}

# Analysis thresholds
CONTANGO_THRESHOLDS = [0.001, 0.002, 0.005, 0.01, 0.02, 0.03, 0.05]
BACKWARDATION_THRESHOLDS = [-0.001, -0.002, -0.005, -0.01, -0.02, -0.03, -0.05]
FORWARD_DAYS = [1, 2, 3, 5, 10, 21]


# ── helpers ────────────────────────────────────────────────────────────

def parse_contract(symbol: str, contract: str):
    """Return (expiry_month, expiry_year) or None."""
    cfg = TICKER_CFG[symbol]
    m = re.match(cfg["month_re"], contract)
    if not m:
        return None
    g = m.groups()
    if g[0] is not None:          # GEN_ format: month.year (e.g. GEN_SI-9.25)
        month = int(g[0])
        year = 2000 + int(g[1])
        return month, year
    if g[2] is not None:          # Short code: letter + digit (e.g. SiM6)
        letter = g[2]
        year_digit = int(g[3])
        month = MONTH_LETTER.get(letter)
        if month is None:
            return None
        year = 2020 + year_digit
        return month, year
    return None


# ── data loading & daily aggregation ───────────────────────────────────

def load_data(symbol: str) -> pd.DataFrame:
    """Load all 5m data for symbol, parse contracts, return raw DataFrame."""
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        df = pd.read_sql(
            "SELECT time, open, high, low, close, volume, contract "
            "FROM moex_prices_5m WHERE symbol = %s ORDER BY time, contract",
            conn, params=[symbol], parse_dates=["time"],
        )
    finally:
        conn.close()

    parsed = df["contract"].apply(lambda c: parse_contract(symbol, c))
    df["exp_month"] = parsed.apply(lambda x: x[0] if x else None)
    df["exp_year"] = parsed.apply(lambda x: x[1] if x else None)
    df = df.dropna(subset=["exp_month", "exp_year"]).reset_index(drop=True)
    df["exp_month"] = df["exp_month"].astype(int)
    df["exp_year"] = df["exp_year"].astype(int)
    df["exp_key"] = list(zip(df["exp_year"], df["exp_month"]))
    return df


def daily_eod(df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate 5m data to daily EOD close per (date, contract).

    Returns columns: date, exp_key, close (last trade of the day).
    """
    df = df.copy()
    df["date"] = df["time"].dt.date
    # Take last bar per (date, contract)
    eod = (
        df.sort_values("time")
        .groupby(["date", "exp_key", "contract"], sort=False)
        .last()
        .reset_index()
    )
    # Multiple contracts can map to same exp_key (e.g. GEN_SI-9.25 and SiU6);
    # keep the last-observed contract for each (date, exp_key)
    eod = (
        eod.sort_values("time")
        .groupby(["date", "exp_key"], sort=False)
        .last()
        .reset_index()
    )
    return eod[["date", "exp_key", "close"]]


# ── basis computation ──────────────────────────────────────────────────

def compute_basis(eod: pd.DataFrame) -> pd.DataFrame:
    """For each day with 2+ contracts, compute front-back basis.

    Basis = (front_close - back_close) / front_close
    Positive → contango (front cheaper than back)
    Negative → backwardation (front more expensive than back)

    For quarterly contracts (Si, RI), we use the next quarterly expiry
    if the immediate next monthly is not available.
    """
    rows = []
    for day, grp in eod.groupby("date", sort=True):
        if len(grp) < 2:
            continue
        grp = grp.sort_values("exp_key")  # (year, month) ascending
        front = grp.iloc[0]
        back = grp.iloc[1]

        # Also grab the next available back (skip one expiry if front is very close to expiry)
        # For quarterly, the 2nd contract is often the next quarterly
        # We compute both front-back pairs
        for i in range(1, min(3, len(grp))):
            back = grp.iloc[i]
            if front["exp_key"] == back["exp_key"]:
                continue
            basis = (front["close"] - back["close"]) / front["close"]
            rows.append({
                "date": day,
                "front_exp": front["exp_key"],
                "back_exp": back["exp_key"],
                "front_close": front["close"],
                "back_close": back["close"],
                "basis": basis,
            })

    out = pd.DataFrame(rows)
    return out


# ── predictive analysis ────────────────────────────────────────────────

def analyze_predictive_power(
    pairs: pd.DataFrame, eod: pd.DataFrame, df_5m: pd.DataFrame, symbol: str
) -> pd.DataFrame:
    """Test if extreme basis predicts forward returns of the front contract.

    At each signal date:
      - Entry = last close of front contract on signal day
      - Exit  = close of front contract N trading days later
      - Return = (exit - entry) / entry

    Contango → expect price UP (long front)
    Backwardation → expect price DOWN (short front)
    """
    # Build front close lookup: (date, exp_key) → close
    front_lookup = eod.set_index(["date", "exp_key"])["close"]

    # Get unique trading dates sorted
    trading_dates = sorted(eod["date"].unique())
    date_to_idx = {d: i for i, d in enumerate(trading_dates)}

    results = []

    for direction, thresholds, expected_sign in [
        ("contango", CONTANGO_THRESHOLDS, 1),
        ("backwardation", BACKWARDATION_THRESHOLDS, -1),
    ]:
        for thr in thresholds:
            if direction == "contango":
                triggered = pairs[pairs["basis"] > thr].copy()
            else:
                triggered = pairs[pairs["basis"] < thr].copy()

            if triggered.empty:
                for fwd in FORWARD_DAYS:
                    results.append(_null_result(symbol, direction, thr, fwd))
                continue

            for fwd in FORWARD_DAYS:
                stats = _test_fwd(
                    triggered, front_lookup, date_to_idx,
                    trading_dates, fwd, expected_sign,
                    symbol, direction, thr,
                )
                results.append(stats)

    return pd.DataFrame(results)


def _null_result(symbol, direction, threshold, fwd_days):
    return {
        "symbol": symbol,
        "direction": direction,
        "threshold": threshold,
        "fwd_days": fwd_days,
        "n_signals": 0,
        "n_win": 0,
        "win_rate": np.nan,
        "avg_return": np.nan,
        "sharpe": np.nan,
    }


def _test_fwd(triggered, front_lookup, date_to_idx, trading_dates,
              fwd_days, expected_sign, symbol, direction, thr):
    """Run forward test for one (threshold, fwd_days) combo."""
    entries = []
    exits = []

    for _, row in triggered.iterrows():
        d = row["date"]
        exp_key = row["front_exp"]

        entry = front_lookup.get((d, exp_key))
        if entry is None or pd.isna(entry):
            continue

        idx = date_to_idx.get(d)
        if idx is None:
            continue

        exit_idx = idx + fwd_days
        if exit_idx >= len(trading_dates):
            continue

        exit_date = trading_dates[exit_idx]
        exit_price = front_lookup.get((exit_date, exp_key))
        if exit_price is None or pd.isna(exit_price):
            continue

        entries.append(entry)
        exits.append(exit_price)

    if len(entries) == 0:
        return _null_result(symbol, direction, thr, fwd_days)

    entries = np.array(entries)
    exits = np.array(exits)
    returns = (exits - entries) / entries

    n = len(returns)
    # Win if return has the expected sign (positive for contango → long, negative for backwardation → short)
    if expected_sign == 1:
        n_win = int((returns > 0).sum())
    else:
        n_win = int((returns < 0).sum())

    win_rate = n_win / n
    avg_ret = float(returns.mean())
    sharpe = float(returns.mean() / returns.std()) if returns.std() > 0 else 0.0

    return {
        "symbol": symbol,
        "direction": direction,
        "threshold": thr,
        "fwd_days": fwd_days,
        "n_signals": n,
        "n_win": n_win,
        "win_rate": win_rate,
        "avg_return": avg_ret,
        "sharpe": sharpe,
    }


# ── report generation ──────────────────────────────────────────────────

def generate_report(all_stats, all_predictions, all_pairs, all_eod):
    lines = []
    lines.append("# Futures Curve & Basis Analysis")
    lines.append(f"**Date:** {TODAY.isoformat()}")
    lines.append(f"**Data source:** PostgreSQL moex_prices_5m (5m OHLCV → daily EOD per contract)")
    lines.append("")
    lines.append("---")
    lines.append("## TRIZ Problem-Solving Framework")
    lines.append("")
    lines.append("| Этап | Описание |")
    lines.append("|------|----------|")
    lines.append(
        "| **ПРОТИВОРЕЧИЕ** | Фьючерсная кривая (контанго/бэквордейшн) содержит информацию "
        "о будущем движении цены, но её predictive power не используется в стратегиях |"
    )
    lines.append(
        "| **ИКР** | Стратегия, которая на основе анализа формы кривой и экстремумов "
        "базиса предсказывает краткосрочные развороты и продолжения движения |"
    )
    lines.append(
        "| **РЕШЕНИЕ** | Количественная оценка rolling basis между фронт и второй позицией; "
        "проверка гипотезы: экстремальный базис → предсказание движения цены в ближайшие N дней |"
    )
    lines.append(
        "| **РЕЗУЛЬТАТ** | См. таблицы predictive power ниже. "
        "Оценка: при каких threshold и horizon базис даёт сигнал с win rate > 55% |"
    )
    lines.append("")

    for symbol in ["Si", "BR", "RI"]:
        label = TICKER_CFG[symbol]["label"]
        pairs = all_pairs.get(symbol)
        stats = all_stats.get(symbol, {})
        preds = all_predictions.get(symbol)
        eod = all_eod.get(symbol)

        lines.append("---")
        lines.append(f"## {label}")

        if pairs is None or pairs.empty:
            lines.append("")
            lines.append("*No overlapping contract pairs found.*")
            lines.append("")
            continue

        # Contract pairs summary
        lines.append("")
        lines.append("### Contract Pairs Analyzed (daily)")
        lines.append("")
        pair_summary = (
            pairs.groupby(["front_exp", "back_exp"])
            .agg(n_days=("date", "count"), mean_basis=("basis", "mean"))
            .reset_index()
            .sort_values("n_days", ascending=False)
        )
        lines.append("| Front Expiry | Back Expiry | # Days | Mean Basis |")
        lines.append("|-------------|-------------|--------|------------|")
        for _, r in pair_summary.head(20).iterrows():
            lines.append(
                f"| {r['front_exp'][0]}-{r['front_exp'][1]:02d} "
                f"| {r['back_exp'][0]}-{r['back_exp'][1]:02d} "
                f"| {r['n_days']} "
                f"| {r['mean_basis']:+.6f} |"
            )
        if len(pair_summary) > 20:
            lines.append(f"| *... and {len(pair_summary) - 20} more* | | | |")
        lines.append("")

        # Basis stats
        lines.append("### Rolling Basis Statistics")
        lines.append("")
        lines.append("| Metric | Value |")
        lines.append("|--------|-------|")
        lines.append(f"| # observations | {stats.get('count', 'N/A'):,} |")
        lines.append(f"| Mean basis | {stats.get('mean', 'N/A'):+.6f} |")
        lines.append(f"| Std basis | {stats.get('std', 'N/A'):+.6f} |")
        lines.append(f"| Min basis | {stats.get('min', 'N/A'):+.6f} |")
        lines.append(f"| Max basis | {stats.get('max', 'N/A'):+.6f} |")
        lines.append(f"| Median basis | {stats.get('median', 'N/A'):+.6f} |")
        lines.append(f"| % contango (>0) | {stats.get('pct_contango', 'N/A'):.1f}% |")
        lines.append(f"| % backwardation (<0) | {stats.get('pct_backwardation', 'N/A'):.1f}% |")
        lines.append("")

        # Basis distribution
        lines.append("### Basis Distribution (Percentiles)")
        lines.append("")
        if pairs is not None and not pairs.empty:
            pct = pairs["basis"].quantile([0.01, 0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95, 0.99])
            lines.append("| Percentile | Basis |")
            lines.append("|------------|-------|")
            for p, v in pct.items():
                lines.append(f"| {p*100:.0f}% | {v:+.6f} |")
        lines.append("")

        # Predictive power
        lines.append("### Predictive Power Table")
        lines.append("")
        lines.append(
            "When basis crosses threshold, does the front contract price move in the "
            "expected direction (contango→up, backwardation→down) over the next N days?"
        )
        lines.append("")
        lines.append(
            "| Direction | Threshold | Fwd Days | # Signals | Win Rate | Avg Return | Sharpe |"
        )
        lines.append(
            "|-----------|-----------|----------|-----------|----------|------------|--------|"
        )
        if preds is not None and not preds.empty:
            for _, r in preds.sort_values(["direction", "threshold", "fwd_days"]).iterrows():
                if r["n_signals"] == 0:
                    continue
                lines.append(
                    f"| {r['direction']:13s} | {r['threshold']:+.4f} "
                    f"| {r['fwd_days']:2d} | {r['n_signals']:3d} "
                    f"| {r['win_rate']:.1%} "
                    f"| {r['avg_return']:+.4f} "
                    f"| {r['sharpe']:+.2f} |"
                )
        else:
            lines.append("| *No signals generated* | | | | | |")
        lines.append("")

        # Best configurations
        lines.append("### Best Performing Configurations (≥10 signals)")
        lines.append("")
        if preds is not None and not preds.empty:
            high_win = preds[preds["n_signals"] >= 10].copy()
            if not high_win.empty:
                for dir_label in ["contango", "backwardation"]:
                    subset = high_win[high_win["direction"] == dir_label]
                    if subset.empty:
                        lines.append(f"**{dir_label.title()}:** None with ≥10 signals")
                        lines.append("")
                        continue
                    best = subset.nlargest(5, "win_rate")
                    lines.append(f"**{dir_label.title()} → expected direction (best 5):**")
                    for _, r in best.iterrows():
                        lines.append(
                            f"- Threshold {r['threshold']:+.4f}, "
                            f"Fwd {r['fwd_days']}d: "
                            f"win {r['win_rate']:.1%}, "
                            f"ret {r['avg_return']:+.4f}, "
                            f"sharpe {r['sharpe']:+.2f} "
                            f"(n={r['n_signals']})"
                        )
                    lines.append("")
            else:
                lines.append("- No configurations with ≥10 signals")
                lines.append("")
        lines.append("")

    lines.append("---")
    lines.append(
        "*Generated by scripts/futures_curve_analysis.py on "
        f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*"
    )
    return "\n".join(lines)


# ── main ───────────────────────────────────────────────────────────────

def main():
    os.makedirs("reports", exist_ok=True)

    all_stats = {}
    all_predictions = {}
    all_pairs = {}
    all_eod = {}

    for symbol in ["Si", "BR", "RI"]:
        label = TICKER_CFG[symbol]["label"]
        print(f"\n{'='*60}")
        print(f"Processing {label} ({symbol})")
        print(f"{'='*60}")

        print(" Loading 5m data...", end=" ", flush=True)
        df_5m = load_data(symbol)
        print(f"{len(df_5m):,} rows")

        print(" Aggregating to daily EOD...", end=" ", flush=True)
        eod = daily_eod(df_5m)
        print(f"{len(eod):,} contract-day records")
        all_eod[symbol] = eod

        print(" Computing rolling basis...", end=" ", flush=True)
        pairs = compute_basis(eod)
        print(f"{len(pairs):,} pair-observations")

        if pairs.empty:
            print(" WARNING: No overlapping pairs found")
            all_pairs[symbol] = pairs
            all_stats[symbol] = {}
            all_predictions[symbol] = None
            continue

        all_pairs[symbol] = pairs

        stats = {
            "count": len(pairs),
            "mean": pairs["basis"].mean(),
            "std": pairs["basis"].std(),
            "min": pairs["basis"].min(),
            "max": pairs["basis"].max(),
            "median": pairs["basis"].median(),
            "pct_contango": (pairs["basis"] > 0).mean() * 100,
            "pct_backwardation": (pairs["basis"] < 0).mean() * 100,
        }
        all_stats[symbol] = stats
        print(
            f" mean={stats['mean']:+.6f}, "
            f"std={stats['std']:.6f}, "
            f"contango={stats['pct_contango']:.1f}%, "
            f"back={stats['pct_backwardation']:.1f}%"
        )

        print(" Running predictive analysis...", end=" ", flush=True)
        preds = analyze_predictive_power(pairs, eod, df_5m, symbol)
        all_predictions[symbol] = preds
        print(f"{len(preds)} combos tested")

        if preds is not None and not preds.empty:
            high_win = preds[preds["n_signals"] >= 10]
            if not high_win.empty:
                best = high_win.nlargest(5, "win_rate")
                print(" Top 5 by win rate (≥10 signals):")
                for _, r in best.iterrows():
                    print(
                        f"   {r['direction']:14s} | "
                        f"thresh={r['threshold']:+.4f} | "
                        f"fwd={r['fwd_days']:2d}d | "
                        f"win={r['win_rate']:.1%} | "
                        f"ret={r['avg_return']:+.4f} | "
                        f"sharpe={r['sharpe']:+.2f} | "
                        f"n={r['n_signals']}"
                    )

    print(f"\n{'='*60}")
    print("Generating report...")
    report = generate_report(all_stats, all_predictions, all_pairs, all_eod)
    with open(REPORT_PATH, "w") as f:
        f.write(report)
    print(f"Report saved: {REPORT_PATH}")
    print("Done.")


if __name__ == "__main__":
    main()
