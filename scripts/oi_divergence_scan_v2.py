#!/usr/bin/env python3
"""
OI Divergence Scan v2 — с учётом структурных режимов рынка MOEX.

Режим A: full_market — данные до 2022-02 + после recovery per ticker
Режим B: post_recovery — данные только после recovery per ticker

3 варианта сигнала:
  V1: divergence = fiz_net_z - yur_net_z
  V2: divergence = yur_net_z * 2 - fiz_net_z
  V3: divergence = fiz_net_z - yur_net_z, но ТОЛЬКО если fiz и yur в разные стороны

Читает recovery_dates из CSV. Temporal filter: open/mid/close.
"""

import sys, os, json, csv
from datetime import datetime, time as dtime
from itertools import product

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import clickhouse_connect
import numpy as np
import pandas as pd
from config import CH_HOST, CH_PORT, CH_DB

# ── Params ──────────────────────────────────────────────────────────────────
W_VALUES = [10, 20, 40]
T_VALUES = [1.0, 1.5, 2.0, 2.5]
HOLD_VALUES = [5, 10, 20]
SL_PCT = 0.05
COMMISSION = 2.0
CAPITAL = 100000.0
OUT_DIR = "reports/oi_divergence_phase2"

# ── Recovery dates ───────────────────────────────────────────────────────────
# ticker -> recovery YYYY-MM or None (always active) or 'EXCLUDE'
def load_recovery():
    rec = {}
    path = "reports/recovery_dates.csv"
    if os.path.exists(path):
        with open(path) as f:
            reader = csv.DictReader(f)
            for row in reader:
                t = row['ticker'].strip()
                brk = row['structural_break_month'].strip()
                rmo = row['recovery_month'].strip()
                if brk == 'NONE DETECTED' or rmo == 'N/A':
                    rec[t] = None
                elif rmo == 'NOT YET':
                    rec[t] = 'EXCLUDE'
                else:
                    rec[t] = rmo
    else:
        # hardcoded fallback
        raw = {
            'AF': '2025-08', 'AL': '2021-11', 'BR': '2026-04', 'ED': '2022-05',
            'Eu': '2022-10', 'GZ': '2023-05', 'IMOEXF': '2024-07', 'LK': '2022-11',
            'MX': '2024-02', 'NG': '2023-01', 'NM': '2023-05', 'PD': '2022-10',
            'RI': '2023-04', 'RN': '2021-11', 'SN': '2021-10', 'SR': '2025-07',
            'SV': '2024-02', 'VB': '2024-06',
            'Si': None, 'CR': None, 'CNYRUBF': None, 'USDRUBF': None, 'GLDRUBF': None,
        }
        rec = raw
        rec['GD'] = 'EXCLUDE'
        rec['PT'] = 'EXCLUDE'
    return rec

RECOVERY = load_recovery()
TICKERS = sorted([t for t, v in RECOVERY.items() if v != 'EXCLUDE'])

# ── Helpers ──────────────────────────────────────────────────────────────────

def get_ch():
    return clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)


def zscore(series, window):
    s = pd.Series(series)
    mu = s.rolling(window, min_periods=window).mean()
    sd = s.rolling(window, min_periods=window).std(ddof=0)
    out = np.full(len(series), 0.0, dtype=np.float64)
    mask = sd > 1e-12
    out[mask] = ((series - mu.values) / sd.values)[mask]
    return out


def load_data(ch, ticker, recovery_date=None, pre_svo_cutoff=None):
    """
    Загрузить prices_5m_oi + prices_5m JOIN.
    Если recovery_date задан — ТОЛЬКО данные после recovery.
    Если pre_svo_cutoff задан — ТОЛЬКО данные до pre_svo_cutoff.
    """
    conditions = ["p.symbol = {ticker:String}"]
    params = {"ticker": ticker}

    if recovery_date and pre_svo_cutoff:
        # Режим A: до СВО + после recovery
        conditions.append(
            "(p.time < {pre_cut:String} OR p.time >= {rec_cut:String})"
        )
        params["pre_cut"] = pre_svo_cutoff
        params["rec_cut"] = f"{recovery_date}-01"
    elif recovery_date:
        conditions.append("p.time >= {rec_cut:String}")
        params["rec_cut"] = f"{recovery_date}-01"
    elif pre_svo_cutoff:
        conditions.append("p.time < {pre_cut:String}")
        params["pre_cut"] = pre_svo_cutoff

    where = " AND ".join(conditions)

    query = f"""
    SELECT
        p.time, p.open, p.high, p.low, p.close, p.volume,
        o.fiz_buy, o.fiz_sell, o.yur_buy, o.yur_sell, o.total_oi
    FROM moex.prices_5m_oi AS o
    INNER JOIN moex.prices_5m AS p
        ON p.symbol = o.symbol AND p.time = o.time
    WHERE {where}
    ORDER BY p.time
    """
    rows = ch.query(query, parameters=params).result_rows
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=[
        "time", "open", "high", "low", "close", "volume",
        "fiz_buy", "fiz_sell", "yur_buy", "yur_sell", "total_oi"
    ])
    return df


# ── Backtest ─────────────────────────────────────────────────────────────────

def run_backtest(closes, divergence, W, T, hold, sl_pct, times):
    n = len(closes)
    trades = []
    i = W
    while i < n:
        div = divergence[i]
        short_sig = div > T
        long_sig = div < -T

        direction = None
        if short_sig:
            direction = "SHORT"
        elif long_sig:
            direction = "LONG"
        else:
            i += 1
            continue

        ts = times[i]
        # Temporal classification
        if isinstance(ts, np.datetime64):
            dt = pd.Timestamp(ts).to_pydatetime()
        else:
            dt = ts
        hour = dt.hour + dt.minute / 60.0
        if hour < 12:
            session = "open"
        elif hour < 17:
            session = "mid"
        else:
            session = "close"

        entry_price = closes[i]
        exit_idx = min(i + hold, n - 1)
        exit_price = closes[exit_idx]

        # Stop-loss
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

        trades.append({
            "entry_time": str(pd.Timestamp(times[i])),
            "exit_time": str(pd.Timestamp(times[exit_idx])),
            "direction": direction,
            "session": session,
            "entry_price": round(float(entry_price), 4),
            "exit_price": round(float(exit_price), 4),
            "ret_pct": round(float(ret) * 100, 4),
            "ret_net_pct": round(float(pnl_net) / CAPITAL * 100, 4),
            "bars_held": exit_idx - i,
        })
        i = exit_idx + 1

    return trades


def compute_stats(trades):
    if not trades:
        return {"trades": 0, "wr": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
                "total_ret_pct": 0.0, "max_dd_pct": 0.0, "calmar": 0.0,
                "wr_open": 0.0, "wr_mid": 0.0, "wr_close": 0.0}

    returns = np.array([t["ret_net_pct"] for t in trades])
    wins = returns[returns > 0]
    losses = returns[returns <= 0]

    wr = len(wins) / len(returns) * 100
    avg_win = float(np.mean(wins)) if len(wins) > 0 else 0.0
    avg_loss = float(np.mean(losses)) if len(losses) > 0 else 0.0

    cum = np.cumsum(returns)
    peak = np.maximum.accumulate(cum)
    dd = peak - cum
    max_dd = float(np.max(dd)) if len(dd) > 0 else 0.0
    total_ret = float(cum[-1]) if len(cum) > 0 else 0.0
    calmar = total_ret / max_dd if max_dd > 1e-12 else (total_ret if total_ret > 0 else 0.0)

    # WR by session
    sessions = {"open": [], "mid": [], "close": []}
    for t in trades:
        sessions.get(t["session"], []).append(t["ret_net_pct"])
    wr_s = {}
    for s, rets in sessions.items():
        if rets:
            wr_s[f"wr_{s}"] = len([r for r in rets if r > 0]) / len(rets) * 100
        else:
            wr_s[f"wr_{s}"] = 0.0

    return {
        "trades": len(trades),
        "wr": round(wr, 2),
        "avg_win": round(avg_win, 4),
        "avg_loss": round(avg_loss, 4),
        "total_ret_pct": round(total_ret, 4),
        "max_dd_pct": round(max_dd, 4),
        "calmar": round(calmar, 4),
        **wr_s,
    }


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    ch = get_ch()
    os.makedirs(OUT_DIR, exist_ok=True)

    for mode_name, use_pre_svo, use_recovery in [
        ("A_full_market", True, True),
        ("B_post_recovery", False, True),
    ]:
        print(f"\n{'='*70}")
        print(f"  Режим {mode_name}")
        print(f"{'='*70}")

        all_rows = []
        per_ticker_best = {}

        for ticker in TICKERS:
            rec = RECOVERY.get(ticker)
            if rec == 'EXCLUDE':
                continue

            recovery_date = rec  # YYYY-MM or None
            pre_cut = "2022-03-01" if use_pre_svo else None

            df = load_data(ch, ticker, recovery_date=recovery_date,
                          pre_svo_cutoff=pre_cut)
            if df is None or len(df) < 50:
                print(f"  {ticker:10s}: ⚠ нет данных, пропуск")
                continue

            print(f"\n  {ticker:10s}: {len(df)} баров, recovery={recovery_date or 'весь'}")

            # ── Precompute z-scores for all W ──────────────────────────
            closes = df["close"].values.astype(np.float64)
            times = df["time"].values
            fiz_net = (df["fiz_buy"].values - df["fiz_sell"].values).astype(np.float64)
            yur_net = (df["yur_buy"].values - df["yur_sell"].values).astype(np.float64)

            zscores = {}  # W -> (fiz_z, yur_z)
            for W in W_VALUES:
                zscores[W] = (zscore(fiz_net, W), zscore(yur_net, W))

            for variant in [1, 2, 3]:
                ticker_rows = []
                for W, T, hold in product(W_VALUES, T_VALUES, HOLD_VALUES):
                    fiz_z, yur_z = zscores[W]

                    if variant == 1:
                        divergence = fiz_z - yur_z
                    elif variant == 2:
                        divergence = yur_z * 2.0 - fiz_z
                    else:
                        divergence = fiz_z - yur_z
                        divergence[(fiz_z * yur_z) >= 0] = 0.0

                    trades = run_backtest(closes, divergence, W, T, hold, SL_PCT, times)
                    stats = compute_stats(trades)
                    stats["ticker"] = ticker
                    stats["variant"] = variant
                    stats["W"] = W
                    stats["T"] = T
                    stats["hold"] = hold
                    stats["mode"] = mode_name
                    ticker_rows.append(stats)
                    all_rows.append(stats)

                best_var = max(ticker_rows, key=lambda x: x["calmar"])
                print(f"    V{variant}: best W={best_var['W']} T={best_var['T']} "
                      f"hold={best_var['hold']} -> return={best_var['total_ret_pct']:.2f}% "
                      f"DD={best_var['max_dd_pct']:.2f}% Calmar={best_var['calmar']:.3f} "
                      f"WR={best_var['wr']:.1f}% trades={best_var['trades']}"
                      f"  (openWR={best_var.get('wr_open',0):.0f}% midWR={best_var.get('wr_mid',0):.0f}% "
                      f"closeWR={best_var.get('wr_close',0):.0f}%)")

        # ── Save results ─────────────────────────────────────────────────
        if not all_rows:
            print(f"  ⚠ Нет результатов для {mode_name}")
            continue

        df_out = pd.DataFrame(all_rows)
        csv_path = os.path.join(OUT_DIR, f"{mode_name}.csv")
        cols = ["mode","ticker","variant","W","T","hold","trades","wr",
                "total_ret_pct","max_dd_pct","calmar","avg_win","avg_loss",
                "wr_open","wr_mid","wr_close"]
        df_out[cols].to_csv(csv_path, index=False)
        print(f"\n  ✅ {csv_path} ({len(df_out)} строк)")

    # ── Full summary ─────────────────────────────────────────────────────────
    print(f"\n{'='*70}")
    print("  Сводный анализ")
    print(f"{'='*70}")

    for mode in ["A_full_market", "B_post_recovery"]:
        csv_path = os.path.join(OUT_DIR, f"{mode}.csv")
        if not os.path.exists(csv_path):
            continue
        df = pd.read_csv(csv_path)

        print(f"\n--- {mode} ---")
        for var in [1, 2, 3]:
            sub = df[df.variant == var]
            if len(sub) == 0:
                continue
            best = sub.sort_values("calmar", ascending=False).head(10)
            best = best[best.trades >= 20]
            print(f"\n  V{var} Top-5 by Calmar (>=20 trades):")
            for _, r in best.head(5).iterrows():
                print(f"    {r['ticker']:10s} W={int(r['W']):2d} T={r['T']:.1f} "
                      f"hold={int(r['hold']):2d} trades={int(r['trades']):4d} "
                      f"WR={r['wr']:.1f}% ret={r['total_ret_pct']:7.2f}% "
                      f"DD={r['max_dd_pct']:6.2f}% Calmar={r['calmar']:.3f} "
                      f"oWR={r['wr_open']:.0f}% mWR={r['wr_mid']:.0f}% cWR={r['wr_close']:.0f}%")

    print(f"\n{'='*70}")
    print("  Done.")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
