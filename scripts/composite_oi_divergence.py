#!/usr/bin/env python3
"""
composite_oi_divergence.py — Composite OI Divergence Strategy Research.

Combines:
  a) OI divergence z-score (3 variants: V1=fiz-yur, V2=yur*2-fiz, V3=directional)
  b) Volume percentile filter (optional: anomalous volume)
  c) Session breakdown (open/mid/close by original convention)
  d) Parameter sweep: W=[5,10,20,40], T=[1.0,1.5,2.0,2.5], hold=[3,5,10,15,20]

Post-recovery only for tickers with MM recovery dates.
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
W_VALUES = [5, 10, 20, 40]
T_VALUES = [1.0, 1.5, 2.0, 2.5]
HOLD_VALUES = [3, 5, 10, 15, 20]
SL_PCT = 0.05
COMMISSION = 2.0
CAPITAL = 100000.0
OUT_DIR = "reports/oi_divergence_phase2_composite"

# Session definitions (in Irkutsk time, UTC+8)
# Original script: hour<12=open, hour<17=mid, else=close
# Irkutsk = MSK + 5h
# open = <7 MSK, mid = 7-12 MSK, close = >=12 MSK
IRK_OPEN_END = 12    # Irkutsk hour < 12 → open
IRK_MID_END = 17     # Irkutsk hour < 17 → mid

# Recovery dates (known from analysis)
RECOVERY = {
    'AF': '2025-08', 'AL': '2021-11', 'BR': '2026-04', 'ED': '2022-05',
    'Eu': '2022-10', 'GZ': '2023-05', 'IMOEXF': '2024-07', 'LK': '2022-11',
    'MX': '2024-02', 'NG': '2023-01', 'NM': '2023-05', 'PD': '2022-10',
    'RI': '2023-04', 'RN': '2021-11', 'SN': '2021-10', 'SR': '2025-07',
    'SV': '2024-02', 'VB': '2024-06',
    'Si': None, 'CR': None, 'CNYRUBF': None, 'USDRUBF': None, 'GLDRUBF': None,
}
RECOVERY['GD'] = 'EXCLUDE'
RECOVERY['PT'] = 'EXCLUDE'

TARGET_TICKERS = ['BR', 'IMOEXF', 'AF', 'SR', 'Eu', 'CR']


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


def load_data(ch, ticker, recovery_date=None):
    conditions = ["p.symbol = {ticker:String}"]
    params = {"ticker": ticker}
    if recovery_date:
        conditions.append("p.time >= {rec_cut:String}")
        params["rec_cut"] = f"{recovery_date}-01"
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


def get_session(ts):
    """
    Classify bar into session using original convention (Irkutsk time).
    hour < 12 Irkutsk (< 7 MSK) = open
    hour < 17 Irkutsk (7-12 MSK) = mid
    else = close
    """
    if isinstance(ts, np.datetime64):
        dt = pd.Timestamp(ts).to_pydatetime()
    else:
        dt = ts
    hour = dt.hour + dt.minute / 60.0
    if hour < IRK_OPEN_END:
        return "open"
    elif hour < IRK_MID_END:
        return "mid"
    else:
        return "close"


def run_backtest(closes, divergence, W, T, hold, sl_pct, times,
                 volume_pct=None, vol_threshold=0, session_filter=None):
    """
    Backtest with optional filters:
    - volume_pct / vol_threshold: only trade when volume > threshold
    - session_filter: 'open', 'mid', 'close' or None (any)
    """
    n = len(closes)
    trades = []
    i = W
    while i < n:
        div = divergence[i]

        # Session filter
        if session_filter:
            sess = get_session(times[i])
            if sess != session_filter:
                i += 1
                continue

        # Volume filter
        if volume_pct is not None and vol_threshold > 0:
            if volume_pct[i] <= vol_threshold:
                i += 1
                continue

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

        entry_price = closes[i]
        exit_idx = min(i + hold, n - 1)
        exit_price = closes[exit_idx]

        # Stop-loss
        for j in range(i + 1, exit_idx + 1):
            if direction == "LONG":
                if (closes[j] - entry_price) / entry_price <= -sl_pct:
                    exit_price = closes[j]
                    exit_idx = j
                    break
            else:
                if (entry_price - closes[j]) / entry_price <= -sl_pct:
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
            "session": get_session(times[i]),
            "entry_price": round(float(entry_price), 4),
            "exit_price": round(float(exit_price), 4),
            "ret_pct": round(float(ret) * 100, 4),
            "ret_net_pct": round(float(pnl_net) / CAPITAL * 100, 4),
            "bars_held": exit_idx - i,
        })
        i = exit_idx + 1

    return trades


def compute_stats(trades):
    """Compute statistics including capital growth simulation."""
    if not trades:
        return {
            "trades": 0, "wr": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
            "total_ret_pct": 0.0, "max_dd_pct": 0.0, "calmar": 0.0,
            "trades_per_year": 0.0, "growth_final": CAPITAL, "growth_annual_pct": 0.0,
        }

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

    # Trades per year
    if len(trades) > 1:
        try:
            t0 = pd.Timestamp(trades[0]["entry_time"])
            t1 = pd.Timestamp(trades[-1]["entry_time"])
            years = max((t1 - t0).days / 365.25, 0.1)
            trades_per_year = len(trades) / years
        except:
            trades_per_year = len(trades)
    else:
        trades_per_year = len(trades)

    # Capital growth with reinvestment
    growth = float(CAPITAL)
    for t in trades:
        ret_pct = t["ret_net_pct"] / 100.0
        growth *= (1.0 + ret_pct)

    growth_final = growth
    years_for_ann = years if len(trades) > 1 and 'years' in dir() else 1.0
    if len(trades) > 1 and years_for_ann > 0.1:
        growth_annual_pct = ((growth_final / CAPITAL) ** (1.0 / years_for_ann) - 1.0) * 100.0
    else:
        growth_annual_pct = 0.0

    # WR by session
    sessions = {"open": [], "mid": [], "close": []}
    for t in trades:
        s = t.get("session", "unknown")
        sessions.get(s, []).append(t["ret_net_pct"])

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
        "trades_per_year": round(trades_per_year, 1),
        "growth_final": round(growth_final, 2),
        "growth_annual_pct": round(growth_annual_pct, 2),
        **wr_s,
    }


def sweep_params(ch, ticker, recovery_date, variant, session_filter=None,
                 vol_percentile=None):
    """Sweep all W, T, hold combos for one ticker+variant+filter."""
    df = load_data(ch, ticker, recovery_date=recovery_date)
    if df is None or len(df) < 50:
        return []

    closes = df["close"].values.astype(np.float64)
    times = df["time"].values
    volumes = df["volume"].values.astype(np.float64)
    fiz_net = (df["fiz_buy"].values - df["fiz_sell"].values).astype(np.float64)
    yur_net = (df["yur_buy"].values - df["yur_sell"].values).astype(np.float64)

    # Volume percentile threshold
    vol_thresh = None
    vol_pct_arr = None
    if vol_percentile is not None:
        vol_thresh = np.percentile(volumes, vol_percentile)
        vol_pct_arr = volumes  # we use raw volume, compare to threshold

    # Precompute z-scores
    zscores = {}
    for W in W_VALUES:
        zscores[W] = (zscore(fiz_net, W), zscore(yur_net, W))

    results = []
    for W, T, hold in product(W_VALUES, T_VALUES, HOLD_VALUES):
        fiz_z, yur_z = zscores[W]

        if variant == 1:
            divergence = fiz_z - yur_z
        elif variant == 2:
            divergence = yur_z * 2.0 - fiz_z
        else:
            divergence = fiz_z - yur_z
            divergence[(fiz_z * yur_z) >= 0] = 0.0

        trades = run_backtest(closes, divergence, W, T, hold, SL_PCT, times,
                              volume_pct=vol_pct_arr, vol_threshold=vol_thresh,
                              session_filter=session_filter)
        stats = compute_stats(trades)
        stats["ticker"] = ticker
        stats["variant"] = variant
        stats["W"] = W
        stats["T"] = T
        stats["hold"] = hold
        stats["session_filter"] = session_filter or "all"
        stats["vol_filter"] = vol_percentile or 0
        results.append(stats)

    return results


def walk_forward(ch, ticker, recovery_date, W, T, hold, variant,
                 session_filter=None, n_folds=4):
    """Walk-forward analysis."""
    df = load_data(ch, ticker, recovery_date=recovery_date)
    if df is None or len(df) < 200:
        return None

    closes = df["close"].values.astype(np.float64)
    times = df["time"].values
    fiz_net = (df["fiz_buy"].values - df["fiz_sell"].values).astype(np.float64)
    yur_net = (df["yur_buy"].values - df["yur_sell"].values).astype(np.float64)

    fiz_z, yur_z = zscore(fiz_net, W), zscore(yur_net, W)

    if variant == 1:
        divergence = fiz_z - yur_z
    elif variant == 2:
        divergence = yur_z * 2.0 - fiz_z
    else:
        divergence = fiz_z - yur_z
        divergence[(fiz_z * yur_z) >= 0] = 0.0

    n = len(closes)
    fold_size = n // n_folds

    fold_results = []
    for fold in range(n_folds):
        test_start = fold * fold_size
        test_end = (fold + 1) * fold_size if fold < n_folds - 1 else n

        slc = slice(test_start, test_end)
        trades = run_backtest(
            closes[slc], divergence[slc], W, T, hold, SL_PCT, times[slc],
            session_filter=session_filter
        )
        stats = compute_stats(trades)
        stats["fold"] = fold + 1
        stats["test_start"] = str(pd.Timestamp(times[test_start]))
        stats["test_end"] = str(pd.Timestamp(times[test_end - 1]))
        stats["n_bars"] = test_end - test_start
        fold_results.append(stats)

    return fold_results


def main():
    ch = get_ch()
    os.makedirs(OUT_DIR, exist_ok=True)

    print("=" * 80)
    print("  Composite OI Divergence Strategy Research")
    print("  Post-Recovery Only | Session-specific backtest")
    print("=" * 80)

    # ── Phase 1: Base sweep (no filters) per ticker ─────────────────────
    print("\n" + "=" * 80)
    print("  Phase 1: Per-ticker per-variant sweep (baseline, all sessions)")
    print("=" * 80)

    all_results = []

    for ticker in TARGET_TICKERS:
        rec = RECOVERY.get(ticker)
        if rec == 'EXCLUDE':
            continue
        recovery_date = rec

        for variant in [1, 2, 3]:
            results = sweep_params(ch, ticker, recovery_date, variant,
                                    session_filter=None, vol_percentile=None)
            all_results.extend(results)

            if results:
                best = max(results, key=lambda x: x["calmar"])
                print(f"  {ticker:8s} V{variant}: best W={best['W']:2d} T={best['T']:.1f} "
                      f"hold={best['hold']:2d} -> ret={best['total_ret_pct']:7.2f}% "
                      f"DD={best['max_dd_pct']:6.2f}% Calmar={best['calmar']:.3f} "
                      f"WR={best['wr']:.1f}% trades={best['trades']:4d} "
                      f"({best['trades_per_year']:.0f}/yr) "
                      f"growth={best['growth_annual_pct']:.1f}%/yr")

    # Save all baseline results
    df_all = pd.DataFrame(all_results)
    cols = ["ticker","variant","W","T","hold","session_filter","vol_filter",
            "trades","trades_per_year","wr","total_ret_pct","max_dd_pct","calmar",
            "avg_win","avg_loss","growth_final","growth_annual_pct",
            "wr_open","wr_mid","wr_close"]
    df_all[cols].to_csv(os.path.join(OUT_DIR, "baseline_sweep.csv"), index=False)
    print(f"\n  ✅ Saved {len(all_results)} baseline combos")

    # ── Phase 1b: Session-filtered sweeps ────────────────────────────────
    print("\n" + "=" * 80)
    print("  Phase 1b: Session-filtered sweeps (open/mid/close)")
    print("=" * 80)

    session_results = []
    for session in ['open', 'mid', 'close']:
        for ticker in TARGET_TICKERS:
            rec = RECOVERY.get(ticker)
            if rec == 'EXCLUDE':
                continue
            recovery_date = rec

            for variant in [1, 2, 3]:
                results = sweep_params(ch, ticker, recovery_date, variant,
                                        session_filter=session, vol_percentile=None)
                session_results.extend(results)
                if results:
                    best = max(results, key=lambda x: x["calmar"])
                    n_qual = sum(1 for r in results if r["trades"] >= 20)
                    print(f"  {ticker:8s} V{variant} {session:5s}: best W={best['W']:2d} T={best['T']:.1f} "
                          f"hold={best['hold']:2d} -> Calmar={best['calmar']:.3f} "
                          f"WR={best['wr']:.1f}% trades={best['trades']:4d} "
                          f"growth={best['growth_annual_pct']:.1f}%/yr "
                          f"(qual={n_qual})")

    all_results.extend(session_results)
    df_all = pd.DataFrame(all_results)
    df_all[cols].to_csv(os.path.join(OUT_DIR, "all_sweep.csv"), index=False)
    print(f"\n  ✅ Saved {len(all_results)} total combos")

    # ── Phase 2: Best results table ─────────────────────────────────────
    print("\n" + "=" * 80)
    print("  Phase 2: Best Results by Category")
    print("=" * 80)

    def fmt_stats(r):
        sess = r.get('session_filter', 'all')
        vol = r.get('vol_filter', 0)
        vol_str = f"v>P{vol}" if vol > 0 else ""
        return (f"  {r['ticker']:8s} V{r['variant']} W={r['W']:2d} T={r['T']:.1f} "
                f"hold={r['hold']:2d} sess={sess:5s} {vol_str:8s} "
                f"trades={r['trades']:4d} WR={r['wr']:5.1f}% "
                f"ret={r['total_ret_pct']:7.2f}% DD={r['max_dd_pct']:6.2f}% "
                f"Calmar={r['calmar']:.3f} growth={r['growth_annual_pct']:.1f}%/yr")

    print("\n  Top-15 by Calmar (>=20 trades):")
    top_calmar = sorted([r for r in all_results if r["trades"] >= 20],
                        key=lambda x: x["calmar"], reverse=True)[:15]
    for i, r in enumerate(top_calmar):
        print(f"  {i+1:2d}. {fmt_stats(r)}")

    print("\n  Top-15 by WR (>=20 trades):")
    top_wr = sorted([r for r in all_results if r["trades"] >= 20 and r["wr"] > 0],
                    key=lambda x: x["wr"], reverse=True)[:15]
    for i, r in enumerate(top_wr):
        print(f"  {i+1:2d}. {fmt_stats(r)}")

    print("\n  Top-15 by Growth/yr (>=20 trades):")
    top_growth = sorted([r for r in all_results if r["trades"] >= 20],
                        key=lambda x: x["growth_annual_pct"], reverse=True)[:15]
    for i, r in enumerate(top_growth):
        print(f"  {i+1:2d}. {fmt_stats(r)}")

    # ── Phase 3: Meeting targets report ──────────────────────────────────
    print("\n" + "=" * 80)
    print("  Phase 3: Targets Check")
    print("  WR>=55%, >=200 trades/yr, Calmar>3, growth>=50%/yr")
    print("=" * 80)

    qualified = [r for r in all_results
                 if r["wr"] >= 55.0
                 and r["trades_per_year"] >= 200
                 and r["calmar"] > 3.0
                 and r["growth_annual_pct"] >= 50.0
                 and r["trades"] >= 50]

    qualified.sort(key=lambda x: x["growth_annual_pct"], reverse=True)
    print(f"\n  Qualified combos: {len(qualified)}")
    for r in qualified:
        print(f"  ✅ {fmt_stats(r)}")

    # Also check partial matches
    high_wr = [r for r in all_results if r["wr"] >= 55.0 and r["trades"] >= 20]
    high_calmar = [r for r in all_results if r["calmar"] > 3.0 and r["trades"] >= 20]
    high_trades = [r for r in all_results if r["trades_per_year"] >= 200 and r["trades"] >= 20]
    high_growth = [r for r in all_results if r["growth_annual_pct"] >= 50.0 and r["trades"] >= 20]

    print(f"\n  Partial matches:")
    print(f"    WR>=55%:              {len(high_wr)} combos")
    print(f"    Calmar>3:             {len(high_calmar)} combos")
    print(f"    >=200 trades/yr:      {len(high_trades)} combos")
    print(f"    Growth>=50%/yr:       {len(high_growth)} combos")

    # ── Phase 4: Walk-forward for top combos ─────────────────────────────
    print("\n" + "=" * 80)
    print("  Phase 4: Walk-Forward Analysis (4 folds)")
    print("=" * 80)

    # Select best combos for WF
    wf_combos = []
    for ticker in TARGET_TICKERS:
        tr = [r for r in all_results if r["ticker"] == ticker and r["trades"] >= 30]
        if tr:
            best = max(tr, key=lambda x: x["calmar"])
            wf_combos.append(best)

    # Also add top 3 overall
    for r in top_calmar[:3]:
        if r not in wf_combos:
            wf_combos.append(r)

    wf_summaries = []
    for combo in wf_combos[:8]:
        ticker = combo["ticker"]
        W = combo["W"]
        T = combo["T"]
        hold = combo["hold"]
        variant = combo["variant"]
        session_filter = combo.get("session_filter") or None
        if session_filter == "all":
            session_filter = None

        print(f"\n  WF: {ticker} V{variant} W={W} T={T} hold={hold} sess={session_filter or 'all'}")
        print(f"  {'Fold':<6} {'Bars':<7} {'Trades':<8} {'WR%':<7} {'Ret%':<9} {'DD%':<8} {'Calmar':<9} {'Growth%':<9}")
        print(f"  {'─'*58}")

        wf_results = walk_forward(ch, ticker, RECOVERY.get(ticker), W, T, hold, variant, session_filter)
        if wf_results is None:
            print(f"  ⚠ Not enough data")
            continue

        fold_stats = []
        for fr in wf_results:
            print(f"  Fold {fr['fold']:<4} {fr['n_bars']:<7} {fr['trades']:<8} "
                  f"{fr['wr']:<7.1f} {fr['total_ret_pct']:<9.2f} {fr['max_dd_pct']:<8.2f} "
                  f"{fr['calmar']:<9.3f} {fr.get('growth_annual_pct',0):<8.1f}")
            fold_stats.append(fr)

        # Average stats
        avg_wr = np.mean([f["wr"] for f in wf_results if f["trades"] > 0])
        avg_calmar = np.mean([f["calmar"] for f in wf_results if f["trades"] > 0])
        avg_trades = np.mean([f["trades"] for f in wf_results])
        min_calmar = min([f["calmar"] for f in wf_results])
        std_calmar = np.std([f["calmar"] for f in wf_results])
        print(f"  {'Avg':<6} {'':<7} {avg_trades:<8.0f} {avg_wr:<7.1f} {'':<9} {'':<8} {avg_calmar:<9.3f}")
        print(f"  {'Min Calmar':>12}: {min_calmar:.3f}, Std: {std_calmar:.3f}")

        wf_summaries.append({
            "ticker": ticker, "variant": variant, "W": W, "T": T, "hold": hold,
            "session": session_filter or "all",
            "avg_trades": round(avg_trades, 1), "avg_wr": round(avg_wr, 2),
            "avg_calmar": round(avg_calmar, 3), "min_calmar": round(min_calmar, 3),
            "std_calmar": round(std_calmar, 3),
            "n_valid_folds": sum(1 for f in wf_results if f["trades"] > 0),
        })

        pd.DataFrame(wf_results).to_csv(
            os.path.join(OUT_DIR, f"wf_{ticker}_V{variant}_W{W}_T{T}_hold{hold}.csv"),
            index=False
        )

    # ── Phase 5: Capital Growth Projection ───────────────────────────────
    print("\n" + "=" * 80)
    print("  Phase 5: Capital Growth Projection (100K RUB with reinvestment)")
    print("=" * 80)

    growth_candidates = sorted(
        [r for r in all_results if r["trades"] >= 20 and r["calmar"] > 2.0],
        key=lambda x: x["growth_annual_pct"], reverse=True
    )[:10]

    for r in growth_candidates:
        print(f"\n  {r['ticker']} V{r['variant']} W={r['W']} T={r['T']} "
              f"hold={r['hold']} sess={r.get('session_filter','all')}:")
        print(f"    Start: 100,000 RUB")
        print(f"    Final: {r['growth_final']:>10,.0f} RUB")
        print(f"    Return: {r['total_ret_pct']:.1f}% over "
              f"{r['trades']/max(r['trades_per_year'],1):.1f} yrs")
        print(f"    Annual (reinvest): {r['growth_annual_pct']:.1f}%")
        print(f"    Max DD: {r['max_dd_pct']:.2f}%")
        print(f"    Calmar: {r['calmar']:.3f}")
        print(f"    WR: {r['wr']:.1f}% ({r['trades']} trades, {r['trades_per_year']:.0f}/yr)")
        for s in ['open','mid','close']:
            kw = f"wr_{s}"
            if kw in r and r.get(kw, 0) > 0:
                print(f"    {s} WR: {r[kw]:.1f}%")

    # ── Final Summary ────────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("  FINAL SUMMARY")
    print("=" * 80)

    # Comparison table: best single-ticker single-variant vs composite
    print(f"\n  Comparison: Best Single-Ticker Single-Variant (original) vs Composite")
    print(f"  {'Ticker':<8} {'Mode':<12} {'V':<3} {'W':<4} {'T':<5} {'Hold':<6} "
          f"{'Trades':<7} {'T/yr':<6} {'WR%':<6} {'Ret%':<7} {'DD%':<6} {'Calmar':<7} {'Growth%/yr':<10}")
    print(f"  {'─'*80}")
    for ticker in TARGET_TICKERS:
        # Original best (from known results)
        tr = [r for r in all_results if r["ticker"] == ticker and r["trades"] >= 20]
        if not tr:
            # Show best even with 0 trades
            tr = [r for r in all_results if r["ticker"] == ticker]
        if tr:
            best_comp = max(tr, key=lambda x: x["calmar"])
            sess = best_comp.get("session_filter", "all")
            print(f"  {ticker:<8} {'composite':<12} V{best_comp['variant']:<2} {best_comp['W']:<4} "
                  f"{best_comp['T']:<5.1f} {best_comp['hold']:<6} "
                  f"{best_comp['trades']:<7} {best_comp['trades_per_year']:<6.0f} "
                  f"{best_comp['wr']:<6.1f} {best_comp['total_ret_pct']:<7.2f} "
                  f"{best_comp['max_dd_pct']:<6.2f} {best_comp['calmar']:<7.3f} "
                  f"{best_comp['growth_annual_pct']:<9.1f}")

    # Write summary JSON
    summary = {
        "total_combos": len(all_results),
        "qualified_all_targets": len(qualified),
        "best_calmar": {k: v for k, v in top_calmar[0].items()
                        if k in ["ticker","variant","W","T","hold","session_filter",
                                 "trades","wr","total_ret_pct","max_dd_pct","calmar",
                                 "growth_annual_pct"]} if top_calmar else None,
        "best_growth": {k: v for k, v in growth_candidates[0].items()
                        if k in ["ticker","variant","W","T","hold","session_filter",
                                 "trades","wr","total_ret_pct","max_dd_pct","calmar",
                                 "growth_annual_pct"]} if growth_candidates else None,
        "walk_forward": wf_summaries,
    }
    with open(os.path.join(OUT_DIR, "summary.json"), "w") as f:
        json.dump(summary, f, indent=2, default=str)

    print(f"\n✅ All results saved to {OUT_DIR}/")
    print("Done.")


if __name__ == "__main__":
    main()
