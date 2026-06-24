#!/usr/bin/env python3
"""BR 3-red exhaustion — умный выход вместо фиксированного hold."""

import sys, os, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
import clickhouse_connect

CH_HOST = "127.0.0.1"; CH_PORT = 8123; CH_DB = "moex"
TICKER = "BR"

GO = 17228.0; STEPPRICE = 7.43; MINSTEP = 0.01
COMM = 4.0; INITIAL_CAP = 100_000.0

TF_MIN = 15
MIN_LOOKBACK = 4
VZ_TH = 2.5

def get_ch():
    return clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)

def load_raw(ch, start="2020-01-01", end="2026-06-01"):
    q = f"""
        SELECT tradetime, pr_open, pr_high, pr_low, pr_close, vol_sum
        FROM moex.supercandles_fo
        WHERE ticker = '{TICKER}' AND tradetime >= '{start}' AND tradetime < '{end}'
        ORDER BY tradetime
    """
    rows = ch.query(q).result_rows
    df = pd.DataFrame(rows, columns=["t","o","h","l","c","v"])
    df["t"] = pd.to_datetime(df["t"]).dt.tz_localize(None)
    for c in ["o","h","l","c","v"]: df[c] = df[c].astype(float)
    return df

def resample(df, tf=15):
    d = df.set_index("t").resample(f"{tf}min", closed="right", label="right")
    r = pd.DataFrame({"o":d["o"].first(),"h":d["h"].max(),"l":d["l"].min(),"c":d["c"].last(),"v":d["v"].sum()})
    return r.dropna().reset_index().rename(columns={"index":"t"})

def prep(df, sessions=["main","aftn","even"]):
    d = df.copy()
    d["hour"] = d["t"].dt.hour + d["t"].dt.minute/60
    mask = np.zeros(len(d), dtype=bool)
    for s in sessions:
        hlo, hhi = {"main":(5,9), "aftn":(9,14), "even":(14,20)}[s]
        mask |= (d["hour"]>=hlo)&(d["hour"]<hhi)
    d["ok"] = mask

    rw = max(21, int(240/TF_MIN))
    for col in ["v"]:
        m = d[col].rolling(rw, min_periods=rw).mean().shift(1)
        s = d[col].rolling(rw, min_periods=rw).std().shift(1)
        d["z_"+col] = (d[col]-m)/s.clip(lower=1e-10)
    d["tr"] = np.maximum(d["h"]-d["l"], np.maximum((d["h"]-d["c"].shift(1)).abs(), (d["l"]-d["c"].shift(1)).abs()))
    d["atr"] = d["tr"].rolling(14, min_periods=14).mean().shift(1)
    d["sma20"] = d["c"].rolling(20, min_periods=20).mean().shift(1)
    d["sma50"] = d["c"].rolling(50, min_periods=50).mean().shift(1)

    red3 = (d["c"]<d["o"]).rolling(3, min_periods=3).sum().shift(1)
    d["sig"] = (red3>=3)&(d["z_v"]>VZ_TH)&d["ok"]
    d["sig"] = d["sig"].shift(1).fillna(False).astype(bool)
    return d.dropna()

def run_bt_exit(df, exit_type="fix", exit_param=8, sl_mult=2.0, target_mult=0.75, reinvest=False):
    """
    exit_type:
      "fix": держим exit_param баров
      "smacross": выход при пересечении sma20 сверху вниз
      "atrtrail": trailing stop ATR*exit_param от максимума позиции
      "vol_decay": выход когда vol_z падает ниже порога
    """
    n = len(df)
    o, h, l, c = df["o"].values, df["h"].values, df["l"].values, df["c"].values
    a, sig = df["atr"].values, df["sig"].values
    zv = df["z_v"].values if "z_v" in df.columns else None
    sma20 = df["sma20"].values if "sma20" in df.columns else None

    cap = INITIAL_CAP; eq = [cap]; pk = cap
    tr = []; skip = -1

    for i in range(n):
        if not sig[i] or i <= skip or i >= n-2:
            eq.append(cap); pk = max(pk, cap); continue
        atr = a[i]
        if np.isnan(atr) or atr<=0:
            eq.append(cap); pk = max(pk, cap); continue

        ncon = max(1, min(int(cap/GO),5)) if reinvest else 1

        # Entry: min4 лимитка
        lo = max(0, i-MIN_LOOKBACK)
        mp = l[lo:i+1].min()
        fi = -1; mx_scan = min(n-1, i+120)
        for j in range(i, mx_scan+1):
            if l[j]<=mp: fi=j; break
        if fi==-1: eq.append(cap); pk=max(pk,cap); continue
        eb = fi+1
        if eb>=n: eq.append(cap); pk=max(pk,cap); continue
        epx = o[eb]

        target = epx + atr * target_mult
        stop_price = epx - atr * sl_mult

        # Ищем выход
        ex_i = -1; xp = None; reason = "?"

        if exit_type == "fix":
            mx = min(n-1, eb + exit_param)
            for j in range(eb, mx+1):
                if l[j]<=stop_price: xp=stop_price; ex_i=j; reason="SL"; break
                if h[j]>=target: xp=target; ex_i=j; reason="TP"; break
            if ex_i==-1: xp=c[mx]; ex_i=mx; reason="EXP"

        elif exit_type == "smacross":
            mx = min(n-1, eb + 120)
            was_above = False
            for j in range(eb, mx+1):
                if l[j]<=stop_price: xp=stop_price; ex_i=j; reason="SL"; break
                if h[j]>=target: xp=target; ex_i=j; reason="TP"; break
                if sma20 is not None:
                    if c[j] > sma20[j]: was_above = True
                    if was_above and c[j] < sma20[j]:
                        xp=c[j]; ex_i=j; reason="SMA_X"; break
            if ex_i==-1: xp=c[mx]; ex_i=mx; reason="EXP"

        elif exit_type == "atrtrail":
            mx = min(n-1, eb + 120)
            peak = epx
            for j in range(eb, mx+1):
                trail = peak - atr * exit_param
                if l[j]<=trail: xp=trail; ex_i=j; reason="TRAIL"; break
                if h[j]>=target: xp=target; ex_i=j; reason="TP"; break
                if c[j] > peak: peak = c[j]
            if ex_i==-1: xp=c[mx]; ex_i=mx; reason="EXP"

        elif exit_type == "vol_decay":
            mx = min(n-1, eb + 120)
            for j in range(eb, mx+1):
                if l[j]<=stop_price: xp=stop_price; ex_i=j; reason="SL"; break
                if h[j]>=target: xp=target; ex_i=j; reason="TP"; break
                if zv is not None and j > i+4 and zv[j] < 1.0:
                    xp=c[j]; ex_i=j; reason="VOL_DECAY"; break
            if ex_i==-1: xp=c[mx]; ex_i=mx; reason="EXP"

        pnl = ((xp-epx)/MINSTEP)*STEPPRICE*ncon - COMM*ncon
        cap += pnl
        dur = (df.iloc[ex_i]["t"]-df.iloc[eb]["t"]).total_seconds()/60
        tr.append({"pnl":round(pnl), "dur":round(dur,1), "reason":reason})
        skip = ex_i
        eq.append(cap); pk=max(pk,cap)

    if not tr: return None
    nt=len(tr); ws=sum(1 for t in tr if t["pnl"]>0)
    wr=ws/nt*100; tpnl=sum(t["pnl"] for t in tr)
    eqa=np.array(eq); rp=np.maximum.accumulate(eqa)
    dd=np.where(rp>0,(rp-eqa)/rp*100,0); mdd=dd.max()
    yrs=(df.iloc[-1]["t"]-df.iloc[0]["t"]).total_seconds()/(365.25*86400)
    if yrs<0.1: yrs=0.1
    cagr=(cap/INITIAL_CAP)**(1/yrs)-1 if cap>0 else -1.0
    cal=cagr/(mdd/100) if mdd>0 else 0
    return {"nt":nt,"wr":round(wr,1),"pnl":round(tpnl),"dd":round(mdd,1),
            "cagr":round(cagr*100,1),"cal":round(cal,2),
            "reasons":{k:sum(1 for t in tr if t["reason"]==k) for k in set(t["reason"] for t in tr)}}

def main():
    ch = get_ch()
    raw = load_raw(ch)
    df = resample(raw, TF_MIN)
    d = prep(df)

    split = pd.Timestamp("2025-10-01")
    train=d[d["t"]<split].copy(); test=d[d["t"]>=split].copy()

    exit_configs = [
        # exit_type, param, sl_mult, target_mult
        ("fix", 8, 2.0, 0.75),
        ("fix", 16, 2.0, 0.75),
        ("fix", 32, 2.0, 0.75),
        ("smacross", 0, 2.0, 0.75),        # выход при пересечении sma20
        ("atrtrail", 1.0, 0, 0.75),        # trailing 1 ATR
        ("atrtrail", 1.5, 0, 0.75),        # trailing 1.5 ATR
        ("atrtrail", 2.0, 0, 0.75),        # trailing 2 ATR
        ("vol_decay", 0, 2.0, 0.75),       # выход когда vol_z падает
    ]

    print(f"BR 15m | 3-red exhaustion | умный выход\n")
    print(f"{'ExitType':<14} {'Par':<5} {'Set':<4} {'n':<4} {'WR%':<6} {'PnL':<10} {'DD%':<6} {'CAGR':<8} {'Calmar':<7} Причины")
    print("-"*90)

    for et, par, sl_m, tg_m in exit_configs:
        for lbl, ddset in [("FULL", d), ("OOS", test)]:
            r = run_bt_exit(ddset, exit_type=et, exit_param=par, sl_mult=sl_m, target_mult=tg_m)
            if r is None: continue
            rsn = r["reasons"]
            rsn_str = " ".join(f"{k}={v}" for k,v in sorted(rsn.items()))
            pnl_pct = r["pnl"]/INITIAL_CAP*100
            print(f"{et:<14} {par:<5} {lbl:<4} {r['nt']:<4} {r['wr']:<6.1f} "
                  f"{r['pnl']:+8.0f} ({pnl_pct:+.1f}%)  {r['dd']:<6.1f} "
                  f"{r['cagr']:<8.1f} {r['cal']:<7.2f} {rsn_str}")
        print()

if __name__ == "__main__":
    main()
