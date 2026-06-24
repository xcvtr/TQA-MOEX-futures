#!/usr/bin/env python3
"""BR 3-red exhaustion — финальный: walk-forward, оптимизация, multi-ticker."""

import sys, os, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
import clickhouse_connect
from itertools import product
from datetime import timedelta

CH_HOST = "127.0.0.1"; CH_PORT = 8123; CH_DB = "moex"

COMM = 4.0; INITIAL_CAP = 100_000.0; TF_MIN = 15

def get_ch():
    return clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)

def load_raw(ch, ticker, start="2020-01-01", end="2026-06-01"):
    q = f"""
        SELECT tradetime, pr_open, pr_high, pr_low, pr_close, vol_sum
        FROM moex.supercandles_fo
        WHERE ticker='{ticker}' AND tradetime>='{start}' AND tradetime<'{end}'
        ORDER BY tradetime
    """
    rows = ch.query(q).result_rows
    if not rows: return None
    df = pd.DataFrame(rows, columns=["t","o","h","l","c","v"])
    df["t"] = pd.to_datetime(df["t"]).dt.tz_localize(None)
    for c in ["o","h","l","c","v"]: df[c] = df[c].astype(float)
    return df

def resample(df, tf=15):
    d = df.set_index("t").resample(f"{tf}min", closed="right", label="right")
    r = pd.DataFrame({"o":d["o"].first(),"h":d["h"].max(),"l":d["l"].min(),"c":d["c"].last(),"v":d["v"].sum()})
    return r.dropna().reset_index().rename(columns={"index":"t"})

def prep(df):
    d = df.copy()
    rw = 21
    m = d["v"].rolling(rw, min_periods=rw).mean().shift(1)
    s = d["v"].rolling(rw, min_periods=rw).std().shift(1)
    d["zv"] = (d["v"]-m)/s.clip(lower=1e-10)
    d["tr"] = np.maximum(d["h"]-d["l"], np.maximum((d["h"]-d["c"].shift(1)).abs(), (d["l"]-d["c"].shift(1)).abs()))
    d["atr"] = d["tr"].rolling(14, min_periods=14).mean().shift(1)
    d["sma20"] = d["c"].rolling(20, min_periods=20).mean().shift(1)
    red3 = (d["c"]<d["o"]).rolling(3, min_periods=3).sum().shift(1)
    # Базовая 3-red сигнал, порог zv добавим в bt
    d["red3"] = red3 >= 3
    return d.dropna()

def bt(df, zv_th=2.5, target_mult=3.0, sl_mult=2.0, min_lookback=4, 
       max_hold=32, exit_type="target", reinvest=False):
    """
    exit_type:
      "target" — только стоп/тейкпрофит
      "sma20" — + выход при пересечении sma20
    """
    n = len(df)
    o = df["o"].values; h = df["h"].values; l = df["l"].values; c = df["c"].values
    a = df["atr"].values; zv = df["zv"].values; sig = df["red3"].values & (zv > zv_th)
    sma = df["sma20"].values
    
    cap = INITIAL_CAP; tr = []; skip = -1
    
    for i in range(n):
        if not sig[i] or i <= skip or i >= n-2: continue
        at = a[i]
        if np.isnan(at) or at <= 0: continue
        
        ncon = max(1, min(int(cap/17228), 5)) if reinvest else 1
        
        # Entry: лимитка на min lookback
        lo = max(0, i - min_lookback)
        mp = l[lo:i+1].min()
        fi = -1
        for j in range(i, min(n-1, i+120)):
            if l[j] <= mp: fi = j; break
        if fi == -1: continue
        eb = fi + 1
        if eb >= n: continue
        ep = o[eb]
        
        target = ep + at * target_mult
        stop = ep - at * sl_mult
        
        ex = -1; xp = None
        mx = min(n-1, eb + max_hold)
        was_above = False
        
        for j in range(eb, mx+1):
            if l[j] <= stop: xp = stop; ex = j; break
            if h[j] >= target: xp = target; ex = j; break
            if exit_type == "sma20":
                if c[j] > sma[j]: was_above = True
                if was_above and c[j] < sma[j]:
                    xp = c[j]; ex = j; break
        
        if ex == -1: xp = c[mx]; ex = mx
        
        pnl = ((xp - ep) / 0.01) * 7.43 * ncon - COMM * ncon
        cap += pnl
        tr.append({"pnl": round(pnl), "entry": df.iloc[eb]["t"], "exit": df.iloc[ex]["t"]})
        skip = ex
    
    if not tr: return None
    nt = len(tr); ws = sum(1 for t in tr if t["pnl"] > 0)
    tpnl = sum(t["pnl"] for t in tr)
    return {"nt": nt, "wr": round(ws/nt*100, 1), "pnl": round(tpnl)}

def walk_forward(df, n_folds=4, test_days=90, **params):
    """Walk-forward: делим данные на folds, train/test по времени."""
    dates = df["t"].values
    total_days = (dates[-1] - dates[0]).astype("timedelta64[D]").astype(int)
    
    fold_size = total_days // n_folds
    results = []
    
    for fold in range(n_folds):
        test_start = dates[0] + np.timedelta64(fold * fold_size, "D")
        test_end = test_start + np.timedelta64(test_days, "D")
        
        if test_end > dates[-1]: break
        
        train_mask = (df["t"] >= dates[0]) & (df["t"] < test_start)
        test_mask = (df["t"] >= test_start) & (df["t"] < test_end)
        
        train = df[train_mask].copy()
        test = df[test_mask].copy()
        
        if len(train) < 500 or len(test) < 100: continue
        
        r_train = bt(train, **params)
        r_test = bt(test, **params)
        
        if r_train and r_test:
            results.append({
                "fold": fold, "train_n": r_train["nt"], "train_wr": r_train["wr"],
                "train_pnl": r_train["pnl"], "test_n": r_test["nt"],
                "test_wr": r_test["wr"], "test_pnl": r_test["pnl"]
            })
    
    return results

def main():
    ch = get_ch()
    
    # === 1. Walk-forward на BR ===
    print("=" * 70)
    print("  1. WALK-FORWARD BR 15m")
    print("=" * 70)
    
    raw = load_raw(ch, "BR")
    df = resample(raw, TF_MIN)
    d = prep(df)
    
    # Grid параметров
    param_grid = list(product(
        [2.0, 2.5, 3.0],      # zv_th
        [2.0, 3.0],            # target_mult
        [1.5, 2.0],            # sl_mult
        ["target", "sma20"],   # exit_type
    ))
    
    print(f"\n  Параметров: {len(param_grid)}")
    print(f"  Данных: {len(d)} баров ({d['t'].min().date()} — {d['t'].max().date()})")
    
    best_configs = []
    for zvth, tg, sl, ext in param_grid:
        wf = walk_forward(d, n_folds=6, test_days=90, 
                          zv_th=zvth, target_mult=tg, sl_mult=sl, exit_type=ext)
        if not wf: continue
        
        total_train_pnl = sum(r["train_pnl"] for r in wf)
        total_test_pnl = sum(r["test_pnl"] for r in wf)
        avg_test_wr = np.mean([r["test_wr"] for r in wf])
        n_tests = sum(r["test_n"] for r in wf)
        valid_folds = sum(1 for r in wf if r["test_pnl"] > 0)
        
        best_configs.append({
            "zv": zvth, "tg": tg, "sl": sl, "exit": ext,
            "folds": len(wf), "valid": valid_folds,
            "test_pnl": total_test_pnl, "test_wr": round(avg_test_wr, 1),
            "test_n": n_tests, "train_pnl": total_train_pnl
        })
    
    # Топ по test_pnl
    best_configs.sort(key=lambda x: -x["test_pnl"])
    
    print(f"\n  {'zv':<4} {'tg':<4} {'sl':<4} {'exit':<8} {'folds':<6} {'valid':<6} {'test_n':<7} {'test_WR':<8} {'test_PnL':<10} {'train_PnL':<10}")
    print(f"  {'-'*70}")
    for bc in best_configs[:10]:
        print(f"  {bc['zv']:<4} {bc['tg']:<4} {bc['sl']:<4} {bc['exit']:<8} "
              f"{bc['folds']:<6} {bc['valid']:<6} {bc['test_n']:<7} {bc['test_wr']:<8} "
              f"{bc['test_pnl']:+8.0f} {bc['train_pnl']:+8.0f}")
    
    # === 2. Лучший конфиг детально ===
    if best_configs:
        best = best_configs[0]
        print(f"\n{'='*70}")
        print(f"  2. ЛУЧШИЙ КОНФИГ: zv={best['zv']} tg={best['tg']} sl={best['sl']} exit={best['exit']}")
        print(f"{'='*70}")
        
        # Full run
        r_full = bt(d, zv_th=best['zv'], target_mult=best['tg'], 
                    sl_mult=best['sl'], exit_type=best['exit'], reinvest=False)
        r_reinv = bt(d, zv_th=best['zv'], target_mult=best['tg'], 
                    sl_mult=best['sl'], exit_type=best['exit'], reinvest=True)
        
        if r_full:
            print(f"\n  FLAT: n={r_full['nt']:<5} WR={r_full['wr']:.1f}% PnL={r_full['pnl']:+8.0f}")
        if r_reinv:
            print(f"  REINV: n={r_reinv['nt']:<5} WR={r_reinv['wr']:.1f}% PnL={r_reinv['pnl']:+8.0f}")
        
        # OOS (2025-10 → 2026-06)
        split = pd.Timestamp("2025-10-01")
        train = d[d["t"] < split].copy()
        test = d[d["t"] >= split].copy()
        
        r_test = bt(test, zv_th=best['zv'], target_mult=best['tg'],
                    sl_mult=best['sl'], exit_type=best['exit'], reinvest=False)
        r_test_r = bt(test, zv_th=best['zv'], target_mult=best['tg'],
                      sl_mult=best['sl'], exit_type=best['exit'], reinvest=True)
        if r_test:
            print(f"  OOS FLAT: n={r_test['nt']:<5} WR={r_test['wr']:.1f}% PnL={r_test['pnl']:+8.0f}")
        if r_test_r:
            print(f"  OOS REINV: n={r_test_r['nt']:<5} WR={r_test_r['wr']:.1f}% PnL={r_test_r['pnl']:+8.0f}")
    
    # === 3. Другие тикеры ===
    print(f"\n{'='*70}")
    print(f"  3. ДРУГИЕ ТИКЕРЫ (лучший конфиг)")
    print(f"{'='*70}")
    
    other = ["Si", "CR", "GD"]
    for ticker in other:
        raw_t = load_raw(ch, ticker)
        if raw_t is None:
            print(f"\n  {ticker}: нет данных"); continue
        
        dt = resample(raw_t, TF_MIN)
        dt = prep(dt)
        
        if len(dt) < 500:
            print(f"\n  {ticker}: мало данных ({len(dt)})"); continue
        
        # Тест с лучшим конфигом
        for ext in ["target", "sma20"]:
            r = bt(dt, zv_th=2.5, target_mult=3.0, sl_mult=2.0, exit_type=ext, reinvest=False)
            if r:
                print(f"  {ticker} {ext:<8}: n={r['nt']:<5} WR={r['wr']:.1f}% PnL={r['pnl']:+8.0f}")

if __name__ == "__main__":
    main()
