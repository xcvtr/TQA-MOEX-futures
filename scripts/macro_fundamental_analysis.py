#!/usr/bin/env python3
"""
Direction 3: Macro / Fundamental Analysis
TRIZ-based research: ПРОТИВОРЕЧИЕ → ИКР → РЕШЕНИЕ → РЕЗУЛЬТАТ
"""

import warnings
import sys
import os
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import psycopg2

warnings.filterwarnings("ignore")

DB_CONFIG = {
    "host": "10.0.0.64",
    "dbname": "moex",
    "user": "postgres",
}
TICKERS = ["Si", "BR", "RI", "IMOEXF"]
REPORT_PATH = "/home/user/projects/TQA-MOEX/reports/2026-06-10-macro-fundamental.md"


def load_5m_data():
    """Load 5m OHLCV data from PostgreSQL."""
    conn = psycopg2.connect(**DB_CONFIG)
    query = """
        SELECT symbol, time, open, high, low, close, volume
        FROM moex_prices_5m
        WHERE symbol IN %s
        ORDER BY symbol, time
    """
    df = pd.read_sql_query(query, conn, params=(tuple(TICKERS),))
    conn.close()
    df["time"] = pd.to_datetime(df["time"])
    return df


def resample_to_daily(df):
    """Resample 5m data to daily bars using last close of each day."""
    resampled = []
    for symbol in TICKERS:
        sdf = df[df["symbol"] == symbol].copy()
        sdf = sdf.set_index("time")
        daily = sdf["close"].resample("D").last().dropna().to_frame("close")
        daily["symbol"] = symbol
        daily = daily.reset_index()
        resampled.append(daily)
    return pd.concat(resampled, ignore_index=True)


def compute_weekly_returns(daily_df):
    """Compute weekly returns (5 trading days shifted)."""
    result = {}
    for symbol in TICKERS:
        sdf = daily_df[daily_df["symbol"] == symbol].copy()
        sdf = sdf.set_index("time").sort_index()
        sdf["weekly_return"] = sdf["close"].pct_change(periods=5)
        sdf["weekly_return_shifted"] = sdf["weekly_return"].shift(
            1
        )  # prior week return, for prediction
        result[symbol] = sdf
    return result


def cross_correlation_analysis(weekly_dict):
    """Check cross-correlation between tickers with no look-ahead bias."""
    # Build aligned DataFrame of weekly returns
    frames = {}
    for symbol in TICKERS:
        w = weekly_dict[symbol]["weekly_return"].dropna().to_frame(symbol)
        frames[symbol] = w
    combined = pd.concat(frames.values(), axis=1, join="inner")
    combined = combined.dropna()

    results = []
    for col1 in TICKERS:
        for col2 in TICKERS:
            if col1 == col2:
                continue
            # Pearson correlation of col1 return vs col2 return (same week)
            corr_same = combined[col1].corr(combined[col2])

            # Does col1 predict col2? (col1 return this week vs col2 return next week)
            # Shift col1 forward (so col1's t aligns with col2's t+1)
            col1_shifted = combined[col1].shift(1)
            pred_df = pd.DataFrame(
                {"x": col1_shifted, "y": combined[col2]}
            ).dropna()
            corr_pred = pred_df["x"].corr(pred_df["y"]) if len(pred_df) > 10 else 0.0

            results.append(
                {
                    "predictor": col1,
                    "target": col2,
                    "same_week_corr": round(corr_same, 4),
                    "predictive_corr": round(corr_pred, 4),
                    "n_weeks": len(combined),
                }
            )
    return results


def momentum_reversal_analysis(weekly_dict):
    """Check: if weekly return > 3%, does it reverse the next week?"""
    rows = []
    for symbol in TICKERS:
        sdf = weekly_dict[symbol].copy()
        sdf["next_week_return"] = sdf["weekly_return"].shift(-1)
        sdf["prior_week_return"] = sdf["weekly_return"].shift(1)

        for idx, row in sdf.iterrows():
            if pd.isna(row["weekly_return"]) or pd.isna(row["next_week_return"]):
                continue
            rows.append(
                {
                    "symbol": symbol,
                    "date": idx,
                    "weekly_ret": row["weekly_return"],
                    "next_week_ret": row["next_week_return"],
                    "prior_week_ret": row["prior_week_return"],
                }
            )

    df_all = pd.DataFrame(rows)

    results = {}
    for symbol in TICKERS:
        sym_df = df_all[df_all["symbol"] == symbol].copy()
        if len(sym_df) < 10:
            results[symbol] = {"n_events": 0, "avg_next_ret": 0, "pct_negative": 0}
            continue

        # momentum: weeks with return > +3%
        momentum_events = sym_df[sym_df["weekly_ret"] > 0.03].copy()
        # reversal weeks: weeks with return < -3%
        reversal_events = sym_df[sym_df["weekly_ret"] < -0.03].copy()

        results[symbol] = {
            "total_weeks": len(sym_df),
            "n_momentum_events": len(momentum_events),
            "avg_next_ret_after_momentum": round(
                momentum_events["next_week_ret"].mean(), 4
            )
            if len(momentum_events) > 0
            else 0,
            "pct_negative_after_momentum": round(
                (momentum_events["next_week_ret"] < 0).mean() * 100, 1
            )
            if len(momentum_events) > 0
            else 0,
            "n_reversal_events": len(reversal_events),
            "avg_next_ret_after_reversal": round(
                reversal_events["next_week_ret"].mean(), 4
            )
            if len(reversal_events) > 0
            else 0,
            "pct_positive_after_reversal": round(
                (reversal_events["next_week_ret"] > 0).mean() * 100, 1
            )
            if len(reversal_events) > 0
            else 0,
        }
    return results


def mean_reversion_weekly(weekly_dict):
    """Check mean reversion on weekly timeframe using expanding windows."""
    results = {}
    for symbol in TICKERS:
        sdf = weekly_dict[symbol].copy()
        rets = sdf["weekly_return"].dropna()

        if len(rets) < 20:
            results[symbol] = {"status": "insufficient data"}
            continue

        # Expanding window: compute z-score of current return vs historical distribution
        n_over_2 = 0
        n_under_neg2 = 0
        reversion_after_over2 = []
        reversion_after_under_neg2 = []

        for i in range(20, len(rets)):
            window = rets.iloc[:i]
            current_ret = rets.iloc[i]
            mu = window.mean()
            sigma = window.std()
            if sigma == 0:
                continue
            z = (current_ret - mu) / sigma

            next_ret = rets.iloc[i + 1] if i + 1 < len(rets) else None
            if next_ret is None:
                continue

            if z > 2.0:
                n_over_2 += 1
                reversion_after_over2.append(next_ret)
            elif z < -2.0:
                n_under_neg2 += 1
                reversion_after_under_neg2.append(next_ret)

        results[symbol] = {
            "n_weeks": len(rets),
            "n_z_over_2": n_over_2,
            "avg_next_ret_after_z_over_2": round(
                np.mean(reversion_after_over2), 4
            )
            if reversion_after_over2
            else 0,
            "pct_negative_after_z_over_2": round(
                (np.array(reversion_after_over2) < 0).mean() * 100, 1
            )
            if reversion_after_over2
            else 0,
            "n_z_under_neg2": n_under_neg2,
            "avg_next_ret_after_z_under_neg2": round(
                np.mean(reversion_after_under_neg2), 4
            )
            if reversion_after_under_neg2
            else 0,
            "pct_positive_after_z_under_neg2": round(
                (np.array(reversion_after_under_neg2) > 0).mean() * 100, 1
            )
            if reversion_after_under_neg2
            else 0,
        }
    return results


def generate_report(cross_corr, momentum_rev, mean_rev, weekly_dict):
    """Generate markdown report."""

    def _weekly_table(weekly_dict, tickers):
        lines = []
        lines.append("| Date | " + " | ".join(tickers) + " |")
        lines.append("|------|" + "|".join(["------"] * len(tickers)) + "|")
        # Align dates across tickers
        all_dates = sorted(
            set().union(
                *[weekly_dict[t].index for t in tickers]
            )
        )
        for d in all_dates[-30:]:  # last 30 weeks
            vals = []
            for t in tickers:
                sdf = weekly_dict[t]
                if d in sdf.index and not pd.isna(sdf.loc[d, "weekly_return"]):
                    vals.append(f"{sdf.loc[d, 'weekly_return']*100:.1f}%")
                else:
                    vals.append("-")
            lines.append("| " + str(d.date()) + " | " + " | ".join(vals) + " |")
        return "\n".join(lines)

    report = f"""# Direction 3: Macro / Fundamental Analysis
**Date:** 2026-06-10  
**TRIZ Framework:** ПРОТИВОРЕЧИЕ → ИКР → РЕШЕНИЕ → РЕЗУЛЬТАТ

---

## 1. ПРОТИВОРЕЧИЕ (Contradiction)

> Markets exhibit both momentum AND mean reversion simultaneously.  
> Cross-asset relationships are unstable — Si may predict BR in one regime and lag in another.  
> The contradiction: *short-term momentum signals conflict with medium-term reversal probabilities*.

---

## 2. ИКР (Ideal Final Result)

> A regime-aware model that dynamically weights momentum vs. reversion based on cross-asset correlation structure.  
> ICR: *self-adjusting meta-strategy with zero parameter tuning*.

---

## 3. РЕШЕНИЕ (Solution)

### Data
- Source: `moex_prices_5m` (5-min bars) → resampled to daily close
- Tickers: {', '.join(TICKERS)}
- Period: {weekly_dict[TICKERS[0]].index.min().date()} → {weekly_dict[TICKERS[0]].index.max().date()}
- Weekly returns computed over 5-trading-day rolling windows (no look-ahead)

### 3a. Cross-Correlation Analysis

| Predictor | Target | Same-Week ρ | Predictive ρ | Weeks |
|-----------|--------|-------------|--------------|-------|
"""

    for row in cross_corr:
        report += f"| {row['predictor']} | {row['target']} | {row['same_week_corr']} | {row['predictive_corr']} | {row['n_weeks']} |\n"

    report += """
#### Interpretation
- **Same-week correlation** measures contemporaneous linkage.
- **Predictive correlation** (predictor return this week → target return next week) reveals lead-lag structure.
- |>0.3| suggests economically meaningful relationship.

### 3b. Momentum → Reversal Analysis
> Hypothesis: weekly returns > +3% tend to reverse the following week (negative next-week return).

"""

    for symbol, data in momentum_rev.items():
        report += f"**{symbol}**\n"
        report += f"- Total weeks: {data['total_weeks']}\n"
        report += f"- Strong-up weeks (>+3%): {data['n_momentum_events']}\n"
        report += f"  - Avg next-week return after momentum: {data['avg_next_ret_after_momentum']*100:.2f}%\n"
        report += f"  - % negative next week: {data['pct_negative_after_momentum']}%\n"
        report += f"- Strong-down weeks (<-3%): {data['n_reversal_events']}\n"
        report += f"  - Avg next-week return after reversal: {data['avg_next_ret_after_reversal']*100:.2f}%\n"
        report += f"  - % positive next week: {data['pct_positive_after_reversal']}%\n\n"

    report += """### 3c. Mean Reversion on Weekly Timeframe
> Z-score > |2| based on expanding window (all prior data). Does the market revert after extreme moves?

"""

    for symbol, data in mean_rev.items():
        if "status" in data:
            report += f"**{symbol}**: {data['status']}\n\n"
            continue
        report += f"**{symbol}**\n"
        report += f"- Weeks in sample: {data['n_weeks']}\n"
        report += f"- Z > +2 (extreme up): {data['n_z_over_2']} events\n"
        report += f"  - Avg next-week return: {data['avg_next_ret_after_z_over_2']*100:.2f}%\n"
        report += f"  - % negative next week (reversion): {data['pct_negative_after_z_over_2']}%\n"
        report += f"- Z < -2 (extreme down): {data['n_z_under_neg2']} events\n"
        report += f"  - Avg next-week return: {data['avg_next_ret_after_z_under_neg2']*100:.2f}%\n"
        report += f"  - % positive next week (reversion): {data['pct_positive_after_z_under_neg2']}%\n\n"

    report += """---

## 4. РЕЗУЛЬТАТ (Result)

### Summary of Key Findings
"""

    # Derive summary
    summary_lines = []
    for row in cross_corr:
        if abs(row["predictive_corr"]) > 0.1:
            direction = "positive" if row["predictive_corr"] > 0 else "negative"
            summary_lines.append(
                f"- **{row['predictor']} → {row['target']}**: predictive correlation = {row['predictive_corr']} "
                f"({direction}, same-week = {row['same_week_corr']})"
            )
    if not summary_lines:
        summary_lines.append("- No strong predictive cross-correlations detected (>|0.1|).")

    for symbol, data in momentum_rev.items():
        if data.get("pct_negative_after_momentum", 0) > 55:
            summary_lines.append(
                f"- **{symbol}**: momentum (>+3%) reverses next week "
                f"({data['pct_negative_after_momentum']}% negative)."
            )
        if data.get("pct_positive_after_reversal", 0) > 55:
            summary_lines.append(
                f"- **{symbol}**: sharp drops (<-3%) bounce next week "
                f"({data['pct_positive_after_reversal']}% positive)."
            )

    for symbol, data in mean_rev.items():
        if isinstance(data, dict) and "status" not in data:
            if data.get("pct_negative_after_z_over_2", 0) > 55:
                summary_lines.append(
                    f"- **{symbol}**: extreme z>+2 leads to mean reversion "
                    f"({data['pct_negative_after_z_over_2']}% negative next week)."
                )
            if data.get("pct_positive_after_z_under_neg2", 0) > 55:
                summary_lines.append(
                    f"- **{symbol}**: extreme z<-2 leads to mean reversion "
                    f"({data['pct_positive_after_z_under_neg2']}% positive next week)."
                )

    report += "\n".join(summary_lines) + "\n\n"
    report += "### TRIZ Diagram\n\n"
    report += """```mermaid
graph TD
    A[ПРОТИВОРЕЧИЕ<br>Momentum vs Reversion] --> B[ИКР<br>Regime-Aware Meta-Model]
    B --> C[РЕШЕНИЕ<br>Cross-Correlation + Z-Score Expansion]
    C --> D[РЕЗУЛЬТАТ<br>Dynamic Weight Allocation]
    D --> E[БУДУЩЕЕ<br>Online Regime Detection]
```
"""

    report += "\n---\n*Generated by macro_fundamental_analysis.py — Direction 3 of TRIZ research plan*\n"
    return report


def main():
    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)

    print("=" * 60)
    print("Direction 3: Macro / Fundamental Analysis")
    print("=" * 60)

    print("\n[1/5] Loading 5m OHLCV data from PostgreSQL...")
    df_5m = load_5m_data()
    print(f"  Loaded {len(df_5m):,} rows for {df_5m['symbol'].nunique()} tickers")

    print("\n[2/5] Resampling to daily bars (last close per day)...")
    daily = resample_to_daily(df_5m)
    print(f"  Daily bars: {len(daily):,}")

    print("\n[3/5] Computing weekly returns (5-day rolling)...")
    weekly_dict = compute_weekly_returns(daily)
    for sym in TICKERS:
        n = weekly_dict[sym]["weekly_return"].dropna().shape[0]
        print(f"  {sym}: {n} weekly return observations")

    print("\n[4/5] Running analyses...")
    cross_corr = cross_correlation_analysis(weekly_dict)
    print("  Cross-correlation done.")

    momentum_rev = momentum_reversal_analysis(weekly_dict)
    print("  Momentum/reversal done.")

    mean_rev = mean_reversion_weekly(weekly_dict)
    print("  Mean reversion done.")

    print("\n[5/5] Generating report...")
    report_text = generate_report(cross_corr, momentum_rev, mean_rev, weekly_dict)

    with open(REPORT_PATH, "w") as f:
        f.write(report_text)
    print(f"  Report saved to {REPORT_PATH}")

    # Also print summary to stdout
    print("\n" + "=" * 60)
    print("KEY FINDINGS")
    print("=" * 60)
    print("\n--- Cross-Correlation ---")
    for r in cross_corr:
        flag = " ***" if abs(r["predictive_corr"]) > 0.1 else ""
        print(
            f"  {r['predictor']} -> {r['target']}: "
            f"same={r['same_week_corr']:+.4f}, predict={r['predictive_corr']:+.4f}{flag}"
        )

    print("\n--- Momentum / Reversal ---")
    for sym, d in momentum_rev.items():
        print(f"  {sym}:")
        print(f"    >+3% events: {d['n_momentum_events']}, "
              f"avg next={d['avg_next_ret_after_momentum']*100:+.2f}%, "
              f"neg={d['pct_negative_after_momentum']}%")
        print(f"    <-3% events: {d['n_reversal_events']}, "
              f"avg next={d['avg_next_ret_after_reversal']*100:+.2f}%, "
              f"pos={d['pct_positive_after_reversal']}%")

    print("\n--- Mean Reversion (Z > |2|) ---")
    for sym, d in mean_rev.items():
        if "status" in d:
            print(f"  {sym}: {d['status']}")
            continue
        print(f"  {sym}:")
        print(f"    Z>+2 ({d['n_z_over_2']}x): next avg={d['avg_next_ret_after_z_over_2']*100:+.2f}%, "
              f"reversion={d['pct_negative_after_z_over_2']}%")
        print(f"    Z<-2 ({d['n_z_under_neg2']}x): next avg={d['avg_next_ret_after_z_under_neg2']*100:+.2f}%, "
              f"reversion={d['pct_positive_after_z_under_neg2']}%")

    print("\nDone.")


if __name__ == "__main__":
    main()
