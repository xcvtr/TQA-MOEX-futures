#!/usr/bin/env python3
"""BR 3-red exhaustion + vol climax — финальный честный backtest."""

import sys, os, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
import clickhouse_connect

CH_HOST = "127.0.0.1"
CH_PORT = 8123
CH_DB = "moex"
TICKER = "BR"

GO = 17228.0
STEPPRICE = 7.43
MINSTEP = 0.01
COMM = 4.0
INITIAL_CAP = 100_000.0

# Параметры (из предыдущего теста)
MULT = 0.75       # цель = entry + ATR * 0.75
SL_MULT = 3.0     # стоп = entry - ATR * 3.0
MAX_HOLD = 120    # макс баров удержания
VZ_TH = 2.5       # порог vol_z

def get_ch():
    return clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)

def load_bars(ch, start="2025-01-01", end="2026-06-01"):
    q = f"""
        SELECT tradetime, pr_open, pr_high, pr_low, pr_close, vol_sum
        FROM moex.supercandles_fo_v3
        WHERE ticker = '{TICKER}'
          AND tradetime >= '{start}' AND tradetime < '{end}'
        ORDER BY tradetime
    """
    rows = ch.query(q).result_rows
    df = pd.DataFrame(rows, columns=["tradetime","o","h","l","c","v"])
    df["tradetime"] = pd.to_datetime(df["tradetime"]).dt.tz_localize(None)
    for c in ["o","h","l","c","v"]: df[c] = df[c].astype(float)
    return df

def prep_data(df):
    d = df.copy()
    m = d["v"].rolling(48, min_periods=48).mean().shift(1)
    s = d["v"].rolling(48, min_periods=48).std().shift(1)
    d["v_z"] = (d["v"] - m) / s.clip(lower=1e-10)
    d["tr"] = np.maximum(d["h"] - d["l"], 
                         np.maximum((d["h"] - d["c"].shift(1)).abs(), 
                                     (d["l"] - d["c"].shift(1)).abs()))
    d["atr"] = d["tr"].rolling(14, min_periods=14).mean().shift(1)
    
    # 3-red exhaustion
    red3 = (d["c"] < d["o"]).rolling(3, min_periods=3).sum().shift(1)
    d["signal"] = (red3 >= 3) & (d["v_z"] > VZ_TH)
    d["signal"] = d["signal"].shift(1).fillna(False).astype(bool)
    return d.dropna()

def run_backtest(df, reinvest=False):
    """Честный bar-level backtest. Возвращает метрики и trades."""
    n = len(df)
    o_arr = df["o"].values; h_arr = df["h"].values; l_arr = df["l"].values
    c_arr = df["c"].values; a_arr = df["atr"].values; sig_arr = df["signal"].values
    
    capital = INITIAL_CAP
    equity_curve = [capital]
    peak = capital
    
    trades = []
    skip_until = -1
    
    for i in range(n):
        if not sig_arr[i] or i <= skip_until or i >= n - 2:
            equity_curve.append(capital)
            if capital > peak: peak = capital
            continue
        
        ep = o_arr[i]; atr = a_arr[i]
        if np.isnan(atr) or atr <= 0:
            equity_curve.append(capital)
            if capital > peak: peak = capital
            continue
        
        n_contracts = 1
        if reinvest:
            n_contracts = max(1, min(int(capital / GO), 5))
        
        target = ep + atr * MULT
        stop = ep - atr * SL_MULT
        mx = min(n - 1, i + MAX_HOLD)
        
        exit_idx = -1; exit_px = None
        for j in range(i, mx + 1):
            if l_arr[j] <= stop:
                exit_px = stop; exit_idx = j; break
            if h_arr[j] >= target:
                exit_px = target; exit_idx = j; break
        
        if exit_idx == -1:
            exit_px = c_arr[mx]; exit_idx = mx
        
        pnl = ((exit_px - ep) / MINSTEP) * STEPPRICE * n_contracts - COMM * n_contracts
        capital += pnl
        
        trades.append({
            "entry_time": df.iloc[i]["tradetime"],
            "exit_time": df.iloc[exit_idx]["tradetime"],
            "entry_px": round(ep, 2),
            "exit_px": round(exit_px, 2),
            "n_contracts": n_contracts,
            "pnl_rub": round(pnl),
            "bars_held": exit_idx - i,
            "hit_target": exit_px == target,
            "hit_stop": exit_px == stop,
            "expired": exit_px not in (target, stop),
        })
        
        skip_until = exit_idx
        equity_curve.append(capital)
        if capital > peak: peak = capital
    
    n_trades = len(trades)
    if n_trades == 0:
        return None
    
    wins = sum(1 for t in trades if t["pnl_rub"] > 0)
    wr = wins / n_trades * 100
    total_pnl = sum(t["pnl_rub"] for t in trades)
    
    # DD
    eq = np.array(equity_curve)
    rp = np.maximum.accumulate(eq)
    dd = np.where(rp > 0, (rp - eq) / rp * 100, 0)
    max_dd = dd.max()
    
    # CAGR
    years = (df.iloc[-1]["tradetime"] - df.iloc[0]["tradetime"]).total_seconds() / (365.25 * 86400)
    if years < 0.1: years = 0.1
    cagr = (capital / INITIAL_CAP) ** (1 / years) - 1 if capital > 0 else -1.0
    calmar = cagr / (max_dd / 100) if max_dd > 0 else 0
    
    return {
        "n_trades": n_trades, "wr": round(wr, 1),
        "total_pnl": round(total_pnl), "max_dd_pct": round(max_dd, 1),
        "cagr_pct": round(cagr * 100, 1), "calmar": round(calmar, 2),
        "final_capital": round(capital),
        "avg_pnl": round(total_pnl / n_trades, 1),
        "trades": trades,
    }

def main():
    ch = get_ch()
    full = load_bars(ch)
    df = prep_data(full)
    
    n_sig = df["signal"].sum()
    print(f"BR {TICKER} | {len(df)} баров | {n_sig} сигналов")
    print(f"Период: {df['tradetime'].min().date()} — {df['tradetime'].max().date()}")
    print(f"Параметры: mult={MULT} sl_m={SL_MULT} max_h={MAX_HOLD}")
    print()
    
    # Train / Test split
    split = pd.Timestamp("2025-10-01")
    train = df[df["tradetime"] < split].copy()
    test = df[df["tradetime"] >= split].copy()
    
    for label, d in [("TRAIN 2025-01→09", train), ("TEST  2025-10→2026-05", test), ("FULL 2025-01→2026-05", df)]:
        for reinvest in [False, True]:
            r = run_backtest(d, reinvest=reinvest)
            if r is None: continue
            mode = "FLAT" if not reinvest else "REINV"
            target_pct = r["total_pnl"] / INITIAL_CAP * 100
            print(f"{mode} {label}: n={r['n_trades']:<4} WR={r['wr']:.1f}% "
                  f"PnL={r['total_pnl']:+8.0f} ({target_pct:+.1f}%) "
                  f"DD={r['max_dd_pct']:.1f}% CAGR={r['cagr_pct']:+.1f}% "
                  f"Calmar={r['calmar']:.2f} | avg={r['avg_pnl']:+.0f}")
    
    # Full detail (flat)
    r = run_backtest(df, reinvest=False)
    if r and r["trades"]:
        print(f"\n=== Детали сделок (flat, первые 10) ===")
        for t in r["trades"][:10]:
            dur = (t["exit_time"] - t["entry_time"]).total_seconds() / 60
            reason = "TARGET" if t["hit_target"] else ("STOP" if t["hit_stop"] else "EXPIRED")
            print(f"  {str(t['entry_time'].date()):<12} → {str(t['exit_time'].date()):<12} "
                  f"dur={dur:3.0f}мин PnL={t['pnl_rub']:+6d} ({reason})")
        
        # Распределение причин выхода
        reasons = {"TARGET": sum(1 for t in r["trades"] if t["hit_target"])}
        reasons["STOP"] = sum(1 for t in r["trades"] if t["hit_stop"])
        reasons["EXPIRED"] = sum(1 for t in r["trades"] if t["expired"])
        print(f"\n=== Распределение выходов ===")
        for k, v in reasons.items():
            print(f"  {k}: {v} ({v/len(r['trades'])*100:.1f}%)")
        
        # Средняя длительность
        durs = [(t["exit_time"] - t["entry_time"]).total_seconds() / 60 for t in r["trades"]]
        print(f"\n=== Длительность ===")
        print(f"  Средняя: {np.mean(durs):.0f} мин | Медиана: {np.median(durs):.0f} мин")
        print(f"  Мин: {min(durs):.0f} | Макс: {max(durs):.0f}")
        
        # Месячная разбивка
        trades_df = pd.DataFrame(r["trades"])
        trades_df["month"] = trades_df["entry_time"].dt.to_period("M")
        monthly = trades_df.groupby("month").agg(
            n=("pnl_rub", "count"), 
            wr=("pnl_rub", lambda x: (x>0).mean()*100),
            pnl=("pnl_rub", "sum")
        )
        print(f"\n=== Месячная разбивка ===")
        for idx, row in monthly.iterrows():
            print(f"  {idx}: n={int(row['n'])} WR={row['wr']:.0f}% PnL={row['pnl']:+.0f}")

if __name__ == "__main__":
    main()
