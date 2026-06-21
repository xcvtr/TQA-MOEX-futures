#!/usr/bin/env python3
"""
analyze_disb.py — Анализ дисбаланса агрессивных сделок (disb) как предиктора цены.

Данные: moex.tradestats_fo (ClickHouse 10.0.0.63:8123)
Колонки: secid, tradedate, tradetime, pr_open, pr_high, pr_low, pr_close,
         disb, vol_b, vol_s, vol, oi_open, oi_close

Что делает:
  1. Загружает 1-минутные данные за 2020-01-03 … 2026-06-18
  2. Корреляция disb → next_bar_return (shift(1) для избежания look-ahead)
  3. Ресемплинг на 5-минутки, агрегация disb (mean/sum/last), OHLC, корреляция
  4. Простая стратегия: long при disb > threshold, short при disb < -threshold
     threshold 0.3-0.9; комиссия 4 руб/контракт; считаем Sharpe, WinRate, DD
  5. Сравнение disb vs oi_diff (oi_close - oi_open):
     - корреляция oi_diff → next_return
     - комбинация disb + oi_diff (линейная регрессия)
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import warnings
import json
from datetime import datetime

import clickhouse_connect
import numpy as np
import pandas as pd
from scipy import stats as sp_stats

warnings.filterwarnings("ignore")

# ── Конфигурация ──────────────────────────────────────────────────────────
CH_HOST = "10.0.0.63"
CH_PORT = 8123
CH_DB = "moex"
TABLE = "tradestats_fo"

START_DATE = "2020-01-03"
END_DATE = "2026-06-18"

COMMISSION = 4  # руб/контракт
INITIAL_CAPITAL = 1_000_000  # начальный капитал для симуляции

# Тикеры для анализа — основные ликвидные фьючерсы MOEX
TICKERS = [
    "Si", "Eu", "BR", "RI", "GD", "SR", "GZ", "LK", "VB",
    "RN", "MN", "AF", "AL", "SN", "NM", "HY", "GL", "HS",
    "CR", "SV", "PT", "PD", "ED", "MM", "MX", "NG",
]

# Внутридневной диапазон (МСК)
SESSION_START = 10 * 60  # 10:00
SESSION_END = 18 * 60 + 45  # 18:45


def time_to_minutes(t: datetime) -> int:
    """Преобразовать datetime.time в минуты от полуночи."""
    return t.hour * 60 + t.minute


def load_data(ticker: str) -> pd.DataFrame:
    """Загрузить 1-минутные данные для тикера из ClickHouse."""
    ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)

    query = f"""
    SELECT
        secid,
        tradedate,
        tradetime,
        pr_open,
        pr_high,
        pr_low,
        pr_close,
        disb,
        vol_b,
        vol_s,
        vol,
        oi_open,
        oi_close
    FROM {CH_DB}.{TABLE}
    WHERE asset_code = '{ticker}'
      AND tradedate >= '{START_DATE}'
      AND tradedate <= '{END_DATE}'
    ORDER BY tradedate, tradetime
    """

    result = ch.query(query)
    cols = [
        "secid", "tradedate", "tradetime",
        "open", "high", "low", "close",
        "disb", "vol_b", "vol_s", "vol",
        "oi_open", "oi_close",
    ]
    df = pd.DataFrame(result.result_rows, columns=cols)

    if df.empty:
        return df

    # Типы
    df["tradetime"] = pd.to_datetime(df["tradetime"], format="%H:%M:%S").dt.time
    df["tradedate"] = pd.to_datetime(df["tradedate"])

    # Составной datetime index (МСК)
    df["datetime"] = df.apply(
        lambda r: datetime.combine(r["tradedate"].date(), r["tradetime"]), axis=1
    )
    df["datetime"] = pd.to_datetime(df["datetime"])
    df.set_index("datetime", inplace=True)
    df.sort_index(inplace=True)

    # Числовые типы
    for c in ["open", "high", "low", "close", "disb", "vol_b", "vol_s", "vol", "oi_open", "oi_close"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    # Фильтр внутридневной сессии
    df["_minute"] = df["tradetime"].apply(
        lambda t: t.hour * 60 + t.minute
    )
    df = df[(df["_minute"] >= SESSION_START) & (df["_minute"] <= SESSION_END)]
    df.drop(columns=["_minute"], inplace=True)

    # Удаляем нулевые цены и пропуски
    df = df[df["close"] > 0].copy()

    return df


def compute_features(df: pd.DataFrame) -> pd.DataFrame:
    """Посчитать признаки на 1-минутных данных с shift(1) для избежания look-ahead."""
    d = df.copy()

    # Доходность следующего бара (target)
    d["next_return"] = d["close"].pct_change().shift(-1)

    # Текущий disb (используется как сигнал)
    d["disb_lag"] = d["disb"].shift(1)

    # OI diff (изменение OI за минуту)
    d["oi_diff"] = d["oi_close"] - d["oi_open"]
    rolling_std = d["oi_diff"].rolling(20, min_periods=5).std()
    d["oi_diff"] = d["oi_diff"].where(d["oi_diff"].abs() <= 3 * rolling_std.fillna(np.inf), 0.0)
    lo, hi = np.percentile(d["oi_diff"].dropna().values, [1, 99])
    d["oi_diff"] = d["oi_diff"].clip(lo, hi)
    d["oi_diff_lag"] = d["oi_diff"].shift(1)

    # OI change %
    d["oi_pct"] = d["oi_diff"] / d["oi_open"].clip(lower=1)
    d["oi_pct_lag"] = d["oi_pct"].shift(1)

    # Объём
    d["vol_lag"] = d["vol"].shift(1)

    return d


def resample_to_5m(df: pd.DataFrame) -> pd.DataFrame:
    """Ресемплировать 1-минутные данные на 5-минутки."""
    # Цены: OHLC
    ohlc = df["close"].resample("5min").ohlc()
    ohlc.columns = ["open_5m", "high_5m", "low_5m", "close_5m"]

    # disb: mean, sum, last
    disb_mean = df["disb"].resample("5min").mean()
    disb_sum = df["disb"].resample("5min").sum()
    disb_last = df["disb"].resample("5min").last()

    # OI
    oi_last = df["oi_close"].resample("5min").last()
    oi_first = df["oi_open"].resample("5min").first()

    # Vol
    vol_sum = df["vol"].resample("5min").sum()

    res = pd.DataFrame({
        "open_5m": ohlc["open_5m"],
        "high_5m": ohlc["high_5m"],
        "low_5m": ohlc["low_5m"],
        "close_5m": ohlc["close_5m"],
        "disb_mean": disb_mean,
        "disb_sum": disb_sum,
        "disb_last": disb_last,
        "oi_close_5m": oi_last,
        "oi_open_5m": oi_first,
        "vol_5m": vol_sum,
    })
    res.dropna(inplace=True)
    res["next_return_5m"] = res["close_5m"].pct_change().shift(-1)
    res["disb_lag_5m"] = res["disb_last"].shift(1)
    return res


def compute_correlation(
    df: pd.DataFrame,
    x_col: str,
    y_col: str,
    label: str,
) -> dict:
    """Посчитать Пирсон и Спирмен корреляции, вывести результаты."""
    valid = df[[x_col, y_col]].dropna()
    if len(valid) < 30:
        return {"label": label, "n": len(valid), "pearson": np.nan, "spearman": np.nan, "p_pearson": np.nan, "p_spearman": np.nan}

    x = valid[x_col].values.astype(float)
    y = valid[y_col].values.astype(float)

    # Фильтр выбросов x
    x_lo, x_hi = np.percentile(x, [0.5, 99.5])
    y_lo, y_hi = np.percentile(y, [0.5, 99.5])
    mask = (x >= x_lo) & (x <= x_hi) & (y >= y_lo) & (y <= y_hi)

    x, y = x[mask], y[mask]

    if len(x) < 30:
        return {"label": label, "n": len(x), "pearson": np.nan, "spearman": np.nan, "p_pearson": np.nan, "p_spearman": np.nan}

    pr, p_pr = sp_stats.pearsonr(x, y)
    sr, p_sr = sp_stats.spearmanr(x, y)

    print(f"  {label:40s}  N={len(x):>10,d}  Pearson={pr:+.5f} (p={p_pr:.2e})  Spearman={sr:+.5f} (p={p_sr:.2e})")
    return {
        "label": label,
        "n": len(x),
        "pearson": round(pr, 5),
        "spearman": round(sr, 5),
        "p_pearson": round(p_pr, 6),
        "p_spearman": round(p_sr, 6),
    }


def run_strategy(
    df: pd.DataFrame,
    threshold: float,
    ticker: str,
) -> dict:
    """
    Простая стратегия на disb_lag.
    Сигнал: disb_lag > threshold  → long
            disb_lag < -threshold → short
    Держим 1 бар.
    """
    d = df.dropna(subset=["disb_lag", "next_return", "close"]).copy()
    if len(d) < 100:
        return {}

    d["signal"] = 0
    d.loc[d["disb_lag"] > threshold, "signal"] = 1
    d.loc[d["disb_lag"] < -threshold, "signal"] = -1

    # Комиссия: 4 руб/контракт, 1 контракт = full lot
    # Доход в % от цены входа
    d["strategy_return"] = d["signal"] * d["next_return"]

    # Моделируем капитал
    d["trade_cost"] = (d["signal"] != 0).astype(float) * (COMMISSION / d["close"].clip(lower=1))

    d["pnl_pct"] = d["strategy_return"] - d["trade_cost"]
    d["equity"] = (1 + d["pnl_pct"]).cumprod()

    trades = d[d["signal"] != 0]
    n_trades = len(trades)
    if n_trades == 0:
        return {
            "ticker": ticker,
            "threshold": threshold,
            "n_trades": 0,
            "total_return_pct": 0.0,
            "sharpe": np.nan,
            "win_rate": np.nan,
            "max_dd_pct": 0.0,
            "avg_return": 0.0,
            "std_return": 0.0,
        }

    # Win rate
    wins = trades[trades["strategy_return"] > 0]
    win_rate = len(wins) / n_trades

    # Total return
    total_return = d["pnl_pct"].sum() * 100  # %

    # Sharpe (годовой = sqrt(252 * 390) ~ sqrt(98280) ≈ 313.5)
    annual_factor = np.sqrt(252 * 390)
    avg_ret = d["pnl_pct"].mean()
    std_ret = d["pnl_pct"].std()
    sharpe = (avg_ret / std_ret * annual_factor) if std_ret > 0 else 0.0

    # Max drawdown
    equity = d["equity"]
    rolling_max = equity.cummax()
    dd = (rolling_max - equity) / rolling_max
    max_dd = dd.max()

    return {
        "ticker": ticker,
        "threshold": threshold,
        "n_trades": n_trades,
        "total_return_pct": round(total_return, 2),
        "sharpe": round(sharpe, 3),
        "win_rate": round(win_rate, 4),
        "max_dd_pct": round(max_dd * 100, 2),
        "avg_return": round(avg_ret * 100, 4),
        "std_return": round(std_ret * 100, 4),
    }


def run_multilinear_strategy(
    df: pd.DataFrame,
    w_disb: float,
    w_oi: float,
    threshold: float,
    label: str,
    ticker: str,
) -> dict:
    """Комбинированная стратегия: long при combined_signal > threshold, short при < -threshold.
    Позиция держится 1 бар (на следующем баре закрываем по close).
    Используются lag-признаки (disb_lag, oi_diff_lag) — look-ahead исключён."""
    d = df.dropna(subset=["disb_lag", "oi_diff_lag", "next_return", "close"]).copy()
    if len(d) < 100:
        return {}

    d["combined_signal"] = w_disb * d["disb_lag"] + w_oi * d["oi_diff_lag"]
    d["signal"] = 0
    d.loc[d["combined_signal"] > threshold, "signal"] = 1
    d.loc[d["combined_signal"] < -threshold, "signal"] = -1

    d["strategy_return"] = d["signal"] * d["next_return"]

    trades = d[d["signal"] != 0]
    n_trades = len(trades)
    if n_trades < 10:
        return {}
    d["trade_cost"] = (d["signal"] != 0).astype(float) * (COMMISSION / d["close"].clip(lower=1))
    d["pnl_pct"] = d["strategy_return"] - d["trade_cost"]
    d["equity"] = (1 + d["pnl_pct"]).cumprod()

    annual_factor = np.sqrt(252 * 390)
    avg_ret = d["pnl_pct"].mean()
    std_ret = d["pnl_pct"].std()
    sharpe = (avg_ret / std_ret * annual_factor) if std_ret > 0 else 0.0
    total_return = d["pnl_pct"].sum() * 100
    rolling_max = d["equity"].cummax()
    dd = (rolling_max - d["equity"]) / rolling_max
    max_dd = dd.max()

    wins = trades[trades["strategy_return"] > 0]
    win_rate = len(wins) / n_trades

    return {
        "ticker": ticker,
        "label": label,
        "threshold": threshold,
        "n_trades": n_trades,
        "total_return_pct": round(total_return, 2),
        "sharpe": round(sharpe, 3),
        "win_rate": round(win_rate, 4),
        "max_dd_pct": round(max_dd * 100, 2),
    }


def analyze_ticker(ticker: str) -> dict:
    """Полный анализ одного тикера."""
    print(f"\n{'='*70}")
    print(f"  Анализ: {ticker}")
    print(f"{'='*70}")

    df = load_data(ticker)
    if df.empty:
        print(f"  ⚠ Нет данных для {ticker}")
        try:
            ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)
            codes = ch.query("SELECT DISTINCT asset_code FROM moex.tradestats_fo")
            all_codes = sorted(set(r[0] for r in codes.result_rows if r[0]))
            matches = [c for c in all_codes if ticker.lower() in c.lower()]
            if matches:
                print(f"    → Возможные совпадения: {matches}")
            else:
                print(f"    → Доступные asset_code (первые 30): {all_codes[:30]}")
        except Exception as e:
            print(f"    → Диагностика не удалась: {e}")
        return {"ticker": ticker, "status": "no_data"}

    print(f"  Строк: {len(df):,}")
    print(f"  Период: {df.index.min()} — {df.index.max()}")

    d = compute_features(df)

    # ── 1. Корреляция на 1-минутках ──
    print(f"\n  ── 1-минутные корреляции ──")
    corr_1m = {}
    corr_1m["disb_to_return"] = compute_correlation(d, "disb_lag", "next_return",
                                                      "disb_lag → next_return")
    corr_1m["oi_diff_to_return"] = compute_correlation(d, "oi_diff_lag", "next_return",
                                                         "oi_diff_lag → next_return")
    corr_1m["oi_pct_to_return"] = compute_correlation(d, "oi_pct_lag", "next_return",
                                                        "oi_pct_lag → next_return")
    corr_1m["disb_to_oi_diff"] = compute_correlation(d, "disb_lag", "oi_diff_lag",
                                                       "disb_lag → oi_diff_lag")

    # ── 2. Ресемплинг на 5-минутки ──
    print(f"\n  ── 5-минутные корреляции ──")
    d5 = resample_to_5m(d)
    if len(d5) > 30:
        corr_5m = {}
        corr_5m["disb_mean_to_return"] = compute_correlation(
            d5, "disb_mean", "next_return_5m", "disb_mean(5m) → next_return(5m)"
        )
        corr_5m["disb_sum_to_return"] = compute_correlation(
            d5, "disb_sum", "next_return_5m", "disb_sum(5m) → next_return(5m)"
        )
        corr_5m["disb_last_to_return"] = compute_correlation(
            d5, "disb_last", "next_return_5m", "disb_last(5m) → next_return(5m)"
        )
    else:
        corr_5m = {}

    # ── 3. Простая стратегия ──
    print(f"\n  ── Стратегия disb (1-мин бары) ──")
    strat_results = []
    for thresh in [0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]:
        r = run_strategy(d, thresh, ticker)
        if r:
            strat_results.append(r)
            print(f"    threshold={thresh:.1f}: trades={r['n_trades']:>6,d}  "
                  f"return={r['total_return_pct']:>+8.2f}%  "
                  f"Sharpe={r['sharpe']:>7.3f}  "
                  f"WinRate={r['win_rate']:.1%}  "
                  f"MaxDD={r['max_dd_pct']:.1f}%")

    # ── 4. Сравнение disb vs oi_diff ──
    print(f"\n  ── Сравнение: disb vs oi_diff ──")
    multi_results = []
    thresholds = [0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]

    weight_configs = [
        (0, 1, "oi_diff_only"),
        (1, 0, "disb_only"),
        (0.5, 0.5, "disb*0.5+oi*0.5"),
        (0.7, 0.3, "disb*0.7+oi*0.3"),
        (0.3, 0.7, "disb*0.3+oi*0.7"),
        (0.9, 0.1, "disb*0.9+oi*0.1"),
    ]

    for wd, wo, lbl in weight_configs:
        best = None
        for thresh in thresholds:
            r = run_multilinear_strategy(d, wd, wo, thresh, lbl, ticker)
            if r and r["n_trades"] >= 50:
                if best is None or r["sharpe"] > best["sharpe"]:
                    best = r
        if best:
            multi_results.append(best)
            print(f"    {best['label']:20s} thresh={best['threshold']:.1f}  "
                  f"trades={best['n_trades']:>6,d}  "
                  f"return={best['total_return_pct']:>+8.2f}%  "
                  f"Sharpe={best['sharpe']:>7.3f}  "
                  f"WinRate={best['win_rate']:.1%}  "
                  f"MaxDD={best['max_dd_pct']:.1f}%")

    return {
        "ticker": ticker,
        "status": "ok",
        "n_rows": len(d),
        "corr_1m": corr_1m,
        "corr_5m": corr_5m,
        "strategy": strat_results,
        "comparison": multi_results,
    }


def main():
    print("╔═══════════════════════════════════════════════════════════════════╗")
    print("║  analyze_disb.py — Анализ дисбаланса агрессивных сделок (disb)  ║")
    print("║  Данные: moex.tradestats_fo @ 10.0.0.63:8123                    ║")
    print("╚═══════════════════════════════════════════════════════════════════╝")
    print(f"\nПериод: {START_DATE} — {END_DATE}")
    print(f"Тикеры: {', '.join(TICKERS)}")
    print(f"Сессия: МСК 10:00 — 18:45 (внутридневные бары)")

    all_results = {}
    for ticker in TICKERS:
        try:
            res = analyze_ticker(ticker)
            all_results[ticker] = res
        except Exception as e:
            print(f"\n  ✗ Ошибка при анализе {ticker}: {e}")
            all_results[ticker] = {"ticker": ticker, "status": "error", "error": str(e)}

    # ── Сводка ──
    print(f"\n{'='*70}")
    print(f"  СВОДКА ПО ВСЕМ ТИКЕРАМ")
    print(f"{'='*70}")

    # Корреляции
    print(f"\n  ── Корреляция disb_lag → next_return (1m) ──")
    print(f"  {'Тикер':<8s} {'N':>10s} {'Pearson':>10s} {'p_val':>10s} {'Spearman':>10s} {'p_val':>10s}")
    print(f"  {'─'*8} {'─'*10} {'─'*10} {'─'*10} {'─'*10} {'─'*10}")
    for t in TICKERS:
        r = all_results.get(t, {})
        c = r.get("corr_1m", {}).get("disb_to_return", {})
        if c and "pearson" in c and not np.isnan(c.get("pearson", np.nan)):
            n = c.get("n", 0)
            pr = c.get("pearson", np.nan)
            sr = c.get("spearman", np.nan)
            p_pr = c.get("p_pearson", 1)
            p_sr = c.get("p_spearman", 1)
            sig_pr = " ***" if p_pr < 0.001 else " **" if p_pr < 0.01 else " *" if p_pr < 0.05 else ""
            sig_sr = " ***" if p_sr < 0.001 else " **" if p_sr < 0.01 else " *" if p_sr < 0.05 else ""
            print(f"  {t:<8s} {n:>10,d} {pr:>+10.4f}{sig_pr:4s} {p_pr:.2e} {sr:>+10.4f}{sig_sr:4s} {p_sr:.2e}")
        else:
            print(f"  {t:<8s} {'—':>10s} {'—':>10s} {'—':>10s} {'—':>10s} {'—':>10s}")

    # Лучшие стратегии
    print(f"\n  ── Лучшая стратегия по каждому тикеру (макс Sharpe) ──")
    print(f"  {'Тикер':<8s} {'Threshold':>10s} {'Trades':>8s} {'Return%':>10s} {'Sharpe':>8s} {'WinRate':>8s} {'MaxDD%':>8s}")
    print(f"  {'─'*8} {'─'*10} {'─'*8} {'─'*10} {'─'*8} {'─'*8} {'─'*8}")
    for t in TICKERS:
        r = all_results.get(t, {})
        strats = r.get("strategy", [])
        if not strats:
            print(f"  {t:<8s} {'—':>10s}")
            continue
        best = max(strats, key=lambda x: x.get("sharpe", -999) if x.get("n_trades", 0) > 50 else -999)
        print(f"  {t:<8s} {best['threshold']:>10.1f} {best['n_trades']:>8,d} "
              f"{best['total_return_pct']:>+9.2f}% {best['sharpe']:>8.3f} "
              f"{best['win_rate']:.1%} {best['max_dd_pct']:>7.1f}%")

    # Сравнение disb vs oi_diff
    print(f"\n  ── Сравнение disb vs oi_diff (комбинированные стратегии, лучший threshold) ──")
    print(f"  {'Тикер':<8s} {'Метод':<20s} {'Thresh':>7s} {'Trades':>7s} {'Return%':>9s} {'Sharpe':>7s} {'WRate':>7s} {'MaxDD%':>7s}")
    print(f"  {'─'*8} {'─'*20} {'─'*7} {'─'*7} {'─'*9} {'─'*7} {'─'*7} {'─'*7}")
    for t in TICKERS:
        r = all_results.get(t, {})
        comps = r.get("comparison", [])
        if not comps:
            print(f"  {t:<8s} {'—':>50s}")
            continue
        for c in comps:
            print(f"  {t:<8s} {c['label']:<20s} {c['threshold']:>6.1f}  "
                  f"{c['n_trades']:>6,d} {c['total_return_pct']:>+8.2f}% "
                  f"{c['sharpe']:>7.3f} {c['win_rate']:.0%} {c['max_dd_pct']:>6.1f}%")

    # ── Сохранить JSON ──
    report_path = os.path.join(
        os.path.dirname(__file__),
        "..", "reports", "disb_analysis.json"
    )
    report_path = os.path.abspath(report_path)
    os.makedirs(os.path.dirname(report_path), exist_ok=True)

    # Сериализуем
    def serialize(obj):
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        if isinstance(obj, (np.bool_,)):
            return bool(obj)
        return obj

    with open(report_path, "w") as f:
        json.dump(all_results, f, indent=2, ensure_ascii=False, default=serialize)

    print(f"\n  Результаты сохранены: {report_path}")
    print(f"\n✅ Анализ завершён.")


if __name__ == "__main__":
    main()
