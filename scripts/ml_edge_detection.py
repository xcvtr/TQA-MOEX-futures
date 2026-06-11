"""
ML Edge Detection — TRIZ Direction 4
======================================
GradientBoostingClassifier on 5m OHLCV+OI features.
Predicts next-bar / next-5-bar direction.
"""

import warnings
import sys
import os
from datetime import datetime

import numpy as np
import pandas as pd
import psycopg2

warnings.filterwarnings("ignore")
os.environ["PYTHONWARNINGS"] = "ignore"

DB_HOST = "10.0.0.64"
DB_NAME = "moex"
DB_USER = "postgres"

TICKERS = ["Si", "BR", "RI", "GD", "CNYRUBF", "SR", "GZ", "SBERF"]
REPORT_PATH = "/home/user/projects/TQA-MOEX/reports/2026-06-10-ml-edge.md"

N_ESTIMATORS = 100
MAX_DEPTH = 3
LR = 0.1
TRAIN_RATIO = 0.70
AUC_THRESHOLD = 0.55
MAX_TRAIN_SAMPLES = 50000  # subsample for speed


def get_conn():
    return psycopg2.connect(host=DB_HOST, dbname=DB_NAME, user=DB_USER)


def fetch_data(ticker):
    """Load OHLCV and OI for a single ticker, merged on (symbol, time)."""
    conn = get_conn()
    sql_prices = """
        SELECT time, open, high, low, close, volume
        FROM moex_prices_5m
        WHERE symbol = %s
        ORDER BY time
    """
    sql_oi = """
        SELECT time, total_oi
        FROM moex_prices_5m_oi
        WHERE symbol = %s
        ORDER BY time
    """
    df_p = pd.read_sql(sql_prices, conn, params=(ticker,), parse_dates=["time"])
    df_o = pd.read_sql(sql_oi, conn, params=(ticker,), parse_dates=["time"])
    conn.close()

    if df_p.empty:
        return pd.DataFrame()

    df_o = df_o.drop_duplicates(subset=["time"]).rename(
        columns={"total_oi": "oi"}
    )
    df = pd.merge_asof(df_p, df_o, on="time", direction="nearest")
    # Forward-fill any missing OI
    df["oi"] = df["oi"].ffill()
    return df


def compute_atr(df, period=14):
    high, low, close = df["high"].values, df["low"].values, df["close"].values
    tr = np.maximum(
        high[1:] - low[1:],
        np.maximum(
            np.abs(high[1:] - close[:-1]),
            np.abs(low[1:] - close[:-1]),
        ),
    )
    tr = np.concatenate([[tr[0]], tr])
    atr = np.full(len(close), np.nan)
    atr[period - 1] = tr[:period].mean()
    for i in range(period, len(close)):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
    return atr


def compute_adx(df, period=14):
    high, low, close = (
        df["high"].values,
        df["low"].values,
        df["close"].values,
    )
    up = high[1:] - high[:-1]
    down = low[:-1] - low[1:]
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    tr = np.maximum(
        high[1:] - low[1:],
        np.maximum(np.abs(high[1:] - close[:-1]), np.abs(low[1:] - close[:-1])),
    )

    def _wilder_smooth(x, p):
        out = np.full(len(x), np.nan)
        out[p - 1] = np.mean(x[:p])
        for i in range(p, len(x)):
            out[i] = (out[i - 1] * (p - 1) + x[i]) / p
        return out

    tr_s = _wilder_smooth(tr, period)
    plus_s = _wilder_smooth(plus_dm, period)
    minus_s = _wilder_smooth(minus_dm, period)

    plus_di = 100 * plus_s / tr_s
    minus_di = 100 * minus_s / tr_s
    dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)
    adx = np.full(len(close), np.nan)
    adx[period * 2 - 1] = np.nanmean(dx[: period * 2])
    for i in range(period * 2, len(close)):
        adx[i] = (adx[i - 1] * (period - 1) + dx[i - 1]) / period
    return adx


def compute_rsi(close, period=14):
    deltas = np.diff(close)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = np.full(len(close), np.nan)
    avg_loss = np.full(len(close), np.nan)
    avg_gain[period] = np.mean(gains[:period])
    avg_loss[period] = np.mean(losses[:period])
    for i in range(period + 1, len(close)):
        avg_gain[i] = (avg_gain[i - 1] * (period - 1) + gains[i - 1]) / period
        avg_loss[i] = (avg_loss[i - 1] * (period - 1) + losses[i - 1]) / period
    rs = avg_gain / (avg_loss + 1e-10)
    rsi = 100 - 100 / (1 + rs)
    return rsi


def compute_bb_pct(close, period=20, nstd=2):
    df_bb = pd.DataFrame({"close": close})
    sma = df_bb["close"].rolling(period).mean().values
    std = df_bb["close"].rolling(period).std().values
    upper = sma + nstd * std
    lower = sma - nstd * std
    pct_b = (np.array(close) - lower) / (upper - lower + 1e-10)
    return pct_b


def engineer_features(df):
    """Add all features to the dataframe. Returns a copy."""
    df = df.copy()
    close = df["close"].values
    volume = df["volume"].values
    oi = df["oi"].values

    # ——— MA z-scores ———
    for period in [20, 50, 100]:
        ma = pd.Series(close).rolling(period).mean().values
        std = pd.Series(close).rolling(period).std().values + 1e-10
        df[f"zscore_{period}"] = (close - ma) / std

    # ——— ATR ———
    df["atr_14"] = compute_atr(df, 14)
    df["atr_20"] = compute_atr(df, 20)
    df["atr_14_norm"] = df["atr_14"] / (close + 1e-10)
    df["atr_20_norm"] = df["atr_20"] / (close + 1e-10)

    # ——— ADX ———
    df["adx_14"] = compute_adx(df, 14)

    # ——— Volume ratio ———
    vol_sma20 = pd.Series(volume).rolling(20).mean().values + 1e-10
    df["vol_ratio"] = volume / vol_sma20

    # ——— OI ratio ———
    oi_sma20 = pd.Series(oi).rolling(20).mean().values + 1e-10
    df["oi_ratio"] = oi / oi_sma20

    # ——— Time features ———
    df["dow"] = df["time"].dt.dayofweek
    df["hour"] = df["time"].dt.hour

    # ——— Return lags ———
    for lag in [5, 10, 20]:
        df[f"ret_{lag}"] = (
            close / pd.Series(close).shift(lag).values - 1
        )

    # ——— RSI ———
    df["rsi_14"] = compute_rsi(close, 14)

    # ——— Bollinger %B ———
    df["bb_pct"] = compute_bb_pct(close, 20, 2)

    # ——— Targets ———
    df["ret_next"] = (
        pd.Series(close).shift(-1).values / close - 1
    )
    df["ret_next_5"] = (
        pd.Series(close).shift(-5).values / close - 1
    )
    df["target_1"] = (df["ret_next"] > 0).astype(int)
    df["target_5"] = (df["ret_next_5"] > 0).astype(int)

    return df


def build_model():
    from sklearn.ensemble import GradientBoostingClassifier

    return GradientBoostingClassifier(
        n_estimators=N_ESTIMATORS,
        max_depth=MAX_DEPTH,
        learning_rate=LR,
        random_state=42,
    )


def subsample(X, y, max_samples=MAX_TRAIN_SAMPLES, rng=None):
    """Random subsample while preserving class ratio."""
    if rng is None:
        rng = np.random.RandomState(42)
    if len(X) <= max_samples:
        return X, y
    pos_idx = np.where(y == 1)[0]
    neg_idx = np.where(y == 0)[0]
    n_pos = len(pos_idx)
    n_neg = len(neg_idx)
    pos_frac = n_pos / (n_pos + n_neg)
    n_pos_target = int(max_samples * pos_frac)
    n_neg_target = max_samples - n_pos_target
    if n_pos_target > n_pos:
        n_pos_target = n_pos
        n_neg_target = max_samples - n_pos_target
    if n_neg_target > n_neg:
        n_neg_target = n_neg
        n_pos_target = max_samples - n_neg_target
    chosen_pos = rng.choice(pos_idx, n_pos_target, replace=False)
    chosen_neg = rng.choice(neg_idx, n_neg_target, replace=False)
    chosen = np.concatenate([chosen_pos, chosen_neg])
    rng.shuffle(chosen)
    return X[chosen], y[chosen]


def evaluate(y_true, y_pred, y_prob):
    from sklearn.metrics import (
        accuracy_score,
        precision_score,
        recall_score,
        roc_auc_score,
    )

    acc = accuracy_score(y_true, y_pred)
    prec = precision_score(y_true, y_pred, zero_division=0)
    rec = recall_score(y_true, y_pred, zero_division=0)
    try:
        auc = roc_auc_score(y_true, y_prob)
    except Exception:
        auc = 0.5
    return acc, prec, rec, auc


FEATURE_COLS = [
    "zscore_20",
    "zscore_50",
    "zscore_100",
    "atr_14_norm",
    "atr_20_norm",
    "adx_14",
    "vol_ratio",
    "oi_ratio",
    "dow",
    "hour",
    "ret_5",
    "ret_10",
    "ret_20",
    "rsi_14",
    "bb_pct",
]


def run_ticker(ticker):
    print(f"\n{'='*60}")
    print(f"  Processing {ticker}")
    print(f"{'='*60}")

    df = fetch_data(ticker)
    if df.empty:
        print(f"  SKIP: no data for {ticker}")
        return None

    print(f"  Raw rows: {len(df)}")
    df = engineer_features(df)
    df = df.dropna(subset=FEATURE_COLS + ["target_1", "target_5"]).reset_index(
        drop=True
    )
    print(f"  After feature engineering + dropna: {len(df)}")

    if len(df) < 500:
        print(f"  SKIP: insufficient data ({len(df)} rows)")
        return None

    n_train = int(len(df) * TRAIN_RATIO)

    results = {}
    for target_name, target_col in [("next_bar", "target_1"), ("next_5_bar", "target_5")]:
        X = df[FEATURE_COLS].values
        y = df[target_col].values

        X_train_raw, X_test = X[:n_train], X[n_train:]
        y_train_raw, y_test = y[:n_train], y[n_train:]

        # class balance
        pos = y_train_raw.sum()
        neg = len(y_train_raw) - pos
        # subsample for speed
        X_train, y_train = subsample(X_train_raw, y_train_raw)

        print(f"  [{target_name}] train: {len(X_train_raw)} "
              f"(subsampled to {len(X_train)}) "
              f"| pos={pos} neg={neg} "
              f"(ratio={pos/max(1,len(y_train_raw)):.2f})")

        model = build_model()
        model.fit(X_train, y_train)

        y_pred = model.predict(X_test)
        y_prob = model.predict_proba(X_test)[:, 1]

        acc, prec, rec, auc = evaluate(y_test, y_pred, y_prob)
        results[target_name] = {
            "n_train": len(X_train),
            "n_test": len(X_test),
            "accuracy": acc,
            "precision": prec,
            "recall": rec,
            "auc": auc,
        }
        print(f"  [{target_name}] acc={acc:.4f} prec={prec:.4f} "
              f"rec={rec:.4f} auc={auc:.4f}")

    return results


def write_report(all_results):
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = []
    lines.append("# ML Edge Detection — TRIZ Direction 4")
    lines.append("")
    lines.append(f"**Generated:** {now_str}")
    lines.append("")
    lines.append("## ПРОТИВОРЕЧИЕ → ИКР → РЕШЕНИЕ → РЕЗУЛЬТАТ")
    lines.append("")
    lines.append("### ПРОТИВОРЕЧИЕ (Contradiction)")
    lines.append(
        "Рынки финансовых инструментов содержат слабый предсказуемый сигнал, "
        "скрытый шумом. Стандартные технические индикаторы дают много ложных "
        "сигналов, а ML-модели сложно внедрять из-за переобучения и look-ahead bias."
    )
    lines.append("")
    lines.append("### ИКР (Ideal Final Result)")
    lines.append(
        "Система на основе Gradient Boosting, обученная на 5-минутных данных "
        "с OI-характеристиками, стабильно предсказывает направление движения "
        "следующего бара с AUC > 0.55 хотя бы на одном инструменте."
    )
    lines.append("")
    lines.append("### РЕШЕНИЕ (Solution)")
    lines.append(
        "Feature engineering: z-score от MA(20/50/100), ATR(14/20), ADX(14), "
        "объём / SMA_volume(20), OI / SMA_OI(20), день недели, час, "
        "лаговые доходности (5/10/20), RSI(14), Bollinger %B(20,2). "
        "Модель: GradientBoostingClassifier(n_estimators=100, max_depth=3, lr=0.1). "
        "Train/test split 70/30 по времени без перемешивания. "
        "Целевая переменная: доходность следующего бара > 0 (бинарная) "
        "и доходность следующих 5 баров > 0."
    )
    lines.append("")

    # Collate edges
    edges_found = []
    for ticker, res in sorted(all_results.items()):
        if res is None:
            continue
        for target_name, metrics in res.items():
            if metrics["auc"] > AUC_THRESHOLD:
                edges_found.append((ticker, target_name, metrics))

    lines.append("### РЕЗУЛЬТАТ (Result)")
    lines.append("")
    if edges_found:
        lines.append(f"**✅ EDGE FOUND — {len(edges_found)} edge(s) detected:**")
        for ticker, tname, m in sorted(
            edges_found, key=lambda x: -x[2]["auc"]
        ):
            lines.append(
                f"  - **{ticker}** [{tname}]: "
                f"AUC={m['auc']:.4f}, acc={m['accuracy']:.4f}, "
                f"prec={m['precision']:.4f}, rec={m['recall']:.4f} "
                f"(n_test={m['n_test']})"
            )
    else:
        lines.append("**❌ NO EDGE FOUND** — All AUC ≤ 0.55")
    lines.append("")

    # Detailed table
    lines.append("## Per-Ticker Results")
    lines.append("")
    lines.append(
        "| Ticker | Target | Acc | Prec | Rec | AUC | n_train | n_test |"
    )
    lines.append(
        "|--------|--------|-----|------|-----|-----|---------|--------|"
    )
    for ticker in sorted(all_results.keys()):
        res = all_results[ticker]
        if res is None:
            lines.append(f"| {ticker} | — | — | — | — | — | — | — |")
            continue
        for tname in ["next_bar", "next_5_bar"]:
            m = res[tname]
            lines.append(
                f"| {ticker} | {tname} | {m['accuracy']:.4f} | "
                f"{m['precision']:.4f} | {m['recall']:.4f} | "
                f"{m['auc']:.4f} | {m['n_train']} | {m['n_test']} |"
            )

    lines.append("")
    lines.append(f"## Configuration")
    lines.append("")
    lines.append(f"- **Tickers**: {', '.join(TICKERS)}")
    lines.append(f"- **Model**: GradientBoostingClassifier")
    lines.append(f"- **n_estimators**: {N_ESTIMATORS}")
    lines.append(f"- **max_depth**: {MAX_DEPTH}")
    lines.append(f"- **learning_rate**: {LR}")
    lines.append(f"- **Train ratio**: {TRAIN_RATIO}")
    lines.append(f"- **AUC threshold**: {AUC_THRESHOLD}")
    lines.append(f"- **Features ({len(FEATURE_COLS)})**: "
                 f"{', '.join(FEATURE_COLS)}")
    lines.append("")
    lines.append(f"---")
    lines.append(f"*Report generated at {now_str}*")

    with open(REPORT_PATH, "w") as f:
        f.write("\n".join(lines))
    print(f"\nReport saved to {REPORT_PATH}")


def main():
    print("ML Edge Detection — TRIZ Direction 4")
    print(f"Tickers: {TICKERS}")
    print(f"Model: GradientBoostingClassifier({N_ESTIMATORS}, {MAX_DEPTH}, {LR})")
    print(f"AUC threshold: {AUC_THRESHOLD}")

    all_results = {}
    for ticker in TICKERS:
        try:
            res = run_ticker(ticker)
            all_results[ticker] = res
        except Exception as e:
            print(f"  ERROR processing {ticker}: {e}")
            all_results[ticker] = None

    write_report(all_results)

    # Summary
    print(f"\n{'='*60}")
    print("  SUMMARY")
    print(f"{'='*60}")
    edges = 0
    for ticker in sorted(all_results.keys()):
        res = all_results[ticker]
        if res is None:
            print(f"  {ticker}: FAILED")
            continue
        for tname in ["next_bar", "next_5_bar"]:
            auc = res[tname]["auc"]
            mark = "✅ EDGE" if auc > AUC_THRESHOLD else ""
            if auc > AUC_THRESHOLD:
                edges += 1
            print(f"  {ticker} [{tname}]: AUC={auc:.4f} {mark}")
    print(f"\n  Total edges found: {edges}")
    if edges > 0:
        print("  ✅ EDGE FOUND — ML model shows predictive power")
    else:
        print("  ❌ NO EDGE — AUC <= 0.55 on all tickers/targets")


if __name__ == "__main__":
    main()
