#!/usr/bin/env python3
"""
WHALE H4 — MOEX OI Signal Detector на H4 барах.
Побарная детекция, сигналы на каждом H4 баре (6x чаще D1).

Usage:
  python3 whale_detector_h4.py              # Si (default)
  python3 whale_detector_h4.py BR GD        # Multiple tickers
"""
import sys, os
from datetime import datetime, timedelta
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD
import psycopg2, numpy as np

# H4 окна: W=80 баров (~13 дней), LA=20 баров (~3.3 дня)
W = 80; LA = 20

# ── Pattern library (те же, пороги скалированы под H4: дневные / 6) ──
BULLISH = [
    ("FIZ_DROP_12H",  lambda d: d.get("fiz_lnum_dn_streak",0) >= 3),      # 3 H4 = 12ч
    ("YUR_LOAD_20H",  lambda d: d.get("yur_avg_up_streak",0) >= 5),       # 5 H4 = 20ч
    ("FIZ_FLEE_12H",  lambda d: d.get("fiz_lnum_d3",0) < -350),           # d3 → 3 H4, scale ~2000/6
    ("FIZ_FLEE_ACCEL",lambda d: d.get("fiz_lnum_d5",0) < -500 and d.get("fiz_lnum_d3",0) < d.get("fiz_lnum_d5",0)*0.6),
    ("FIZ_PANIC_ACCEL",lambda d: d.get("fiz_snum_d5",0) > 350 and d.get("fiz_snum_d3",0) > d.get("fiz_snum_d5",0)*0.6),
    ("FIZ_SHORT_SURGE",lambda d: d.get("fiz_short_d5",0) > 850000),       # ~5M/6
    ("YUR_CALM_LOAD", lambda d: d.get("yur_avg_d3",0) > 0 and d.get("yur_avg_d5",0) > 0 and abs(d.get("fiz_lnum_d3",0)) < 350),
]

BEARISH = [
    ("FIZ_EUPHORIA",   lambda d: d.get("fiz_long_pct_up_streak",0) >= 5),
    ("FALLING_KNIFE",  lambda d: d.get("price_d5",0) < -1.0 and d.get("fiz_lnum_d5",0) > 350),
    ("RALLY_FLEE",     lambda d: d.get("price_d5",0) > 1.0 and d.get("fiz_lnum_d5",0) < -350),
    ("FIZ_OVERLOAD",   lambda d: d.get("pct_fiz_lnum",50) >= 95 and d.get("price_d5",0) > 0.5),
    ("SHORT_SQZ_EXHAUST", lambda d: d.get("price_d5",0) > 1.0 and d.get("fiz_snum_d5",0) > 170 and d.get("pct_fiz_snum",50) >= 90),
]


def load_h4(sym):
    """Загрузить H4 бары с OI (fiz/yur) + ценами за 1+ год."""
    conn = psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD)
    cur = conn.cursor()

    since = (datetime.now() - timedelta(days=400)).strftime("%Y-%m-%d")

    # ── OI из openinterest_moex (с buy_accounts) — последний снимок в H4 ──
    cur.execute(f"""
        SELECT DISTINCT ON (h4_time)
            date_trunc('hour', time) - (EXTRACT('hour' FROM time)::int %% 4) * interval '1 hour' AS h4_time,
            clgroup, buy_orders, sell_orders, buy_accounts, sell_accounts
        FROM openinterest_moex
        WHERE symbol = %s AND time >= %s::date AND buy_accounts > 0
        ORDER BY h4_time, time DESC
    """, (sym, since))
    oi = {}
    for r in cur.fetchall():
        p = "fiz" if r[1]==0 else "yur"
        t = r[0]
        if t not in oi:
            oi[t] = {}
        for k,v in [("long",r[2]),("short",abs(r[3])),("lnum",r[4]),("snum",r[5])]:
            oi[t][f"{p}_{k}"] = float(v or 0)

    # ── Цена из moex_prices_5m → H4 ──
    cur.execute(f"""
        SELECT
            date_trunc('hour', time) - (EXTRACT('hour' FROM time)::int %% 4) * interval '1 hour' AS h4_time,
            (array_agg(open ORDER BY time))[1] AS open,
            MAX(high) AS high, MIN(low) AS low,
            (array_agg(close ORDER BY time DESC))[1] AS close,
            SUM(volume) AS volume
        FROM moex_prices_5m
        WHERE symbol = %s AND time >= %s::date AND volume > 0
        GROUP BY h4_time
        ORDER BY h4_time
    """, (sym, since))
    price = {r[0]: {"open": r[1], "high": r[2], "low": r[3], "close": r[4], "volume": r[5]} for r in cur.fetchall()}

    conn.close()

    # ── Соединяем OI + price ──
    times = sorted(set(oi) & set(price))
    data = []
    for t in times:
        o = oi[t]; p = price[t]
        d = {"date": t, "open": p["open"], "high": p["high"], "low": p["low"],
             "close": p["close"], "volume": p["volume"]}
        # FIZ/YUR поля
        for prefix in ["fiz", "yur"]:
            for suffix in ["long", "short", "lnum", "snum"]:
                d[f"{prefix}_{suffix}"] = o.get(f"{prefix}_{suffix}", 0)
        data.append(d)
    return data


def compute(data):
    """Вычислить фичи для H4 баров (аналогично D1, но H4-окна)."""
    N = len(data)
    for i in range(N):
        d = data[i]
        d["fiz_avg"] = d["fiz_long"] / max(d["fiz_lnum"], 1)
        d["yur_avg"] = d["yur_long"] / max(d["yur_lnum"], 1)
        d["fiz_net"] = d["fiz_long"] - d["fiz_short"]
        d["yur_net"] = d["yur_long"] - d["yur_short"]
        d["fiz_long_pct"] = d["fiz_long"] / max(d["fiz_long"] + d["yur_long"], 1) * 100

        if i > 0:
            p = data[i-1]
            for k in ["fiz_lnum","fiz_snum","yur_lnum","yur_snum","yur_avg",
                       "fiz_avg","fiz_net","yur_net","fiz_long"]:
                d[f"{k}_d1"] = d[k] - p[k]
            for n in [3, 5]:
                if i >= n:
                    r = data[i-n]
                    for k in ["fiz_lnum","fiz_snum","yur_lnum","yur_snum",
                               "yur_avg","fiz_avg","fiz_long_pct"]:
                        d[f"{k}_d{n}"] = d[k] - r[k]
                    d[f"price_d{n}"] = (d["close"] - r["close"]) / r["close"] * 100
                else:
                    for k in ["fiz_lnum","fiz_snum","yur_lnum","yur_snum",
                               "yur_avg","fiz_avg","fiz_long_pct"]:
                        d[f"{k}_d{n}"] = 0
                    d[f"price_d{n}"] = 0
        else:
            for k in ["fiz_lnum","fiz_snum","yur_lnum","yur_snum","yur_avg",
                       "fiz_avg","fiz_net","yur_net","fiz_long"]:
                d[f"{k}_d1"] = 0

        if i >= W:
            for k in ["fiz_lnum","fiz_snum","yur_lnum","yur_snum","yur_avg",
                       "fiz_avg","fiz_net","yur_net","fiz_long_pct"]:
                a = np.array([data[j][k] for j in range(i-W, i)])
                d[f"z_{k}"] = (d[k] - np.mean(a)) / np.std(a) if np.std(a) > 0 else 0
                d[f"pct_{k}"] = np.sum(a < d[k]) / len(a) * 100
            for k in ["fiz_lnum","fiz_snum","yur_avg","fiz_long_pct"]:
                up = dn = 0
                for j in range(i-1, max(i-60, 0)-1, -1):  # 60 H4 = 10 дней
                    if data[j][k] < data[j+1][k]: up += 1
                    else: break
                for j in range(i-1, max(i-60, 0)-1, -1):
                    if data[j][k] > data[j+1][k]: dn += 1
                    else: break
                d[f"{k}_up_streak"] = up; d[f"{k}_dn_streak"] = dn
        else:
            for k in ["fiz_lnum","fiz_snum","yur_lnum","yur_snum","yur_avg",
                       "fiz_avg","fiz_net","yur_net","fiz_long_pct"]:
                d[f"z_{k}"] = 0; d[f"pct_{k}"] = 50
            for k in ["fiz_lnum","fiz_snum","yur_avg","fiz_long_pct"]:
                d[f"{k}_up_streak"] = d[f"{k}_dn_streak"] = 0

    # forward return: best close of next LA bars
    for i, d in enumerate(data):
        if i + LA < N:
            closes = [data[j]["close"] for j in range(i+1, min(i+LA+1, N))]
            best = max(closes) if d.get("ret_5d", 0) >= 0 else min(closes)  # direction-agnostic placeholder
            d["ret_5d"] = (data[min(i+LA, N-1)]["close"] - d["close"]) / d["close"] * 100
            d["best_close"] = max(closes) if closes else d["close"]
            d["worst_close"] = min(closes) if closes else d["close"]
        else:
            d["ret_5d"] = 0
    return data


def analyze(data, min_score=2, dominance=1.5):
    """Анализ H4 баров — те же паттерны, больше сигналов."""
    signals = []
    for i, d in enumerate(data[W:], start=W):
        bull = sum(1 for _, c in BULLISH if c(d))
        bear = sum(1 for _, c in BEARISH if c(d))
        total = bull + bear
        if total < min_score:
            continue
        if bull >= bear * dominance:
            signals.append({"date": d["date"], "dir": "LONG", "ret": d["ret_5d"],
                "hit": d["ret_5d"] > 0, "bull": bull, "bear": bear})
        elif bear >= bull * dominance:
            signals.append({"date": d["date"], "dir": "SHORT", "ret": d["ret_5d"],
                "hit": d["ret_5d"] < 0, "bull": bull, "bear": bear})
    return signals


def run(sym, min_score=2, dominance=1.5):
    """Запустить H4 детектор для одного тикера."""
    data = compute(load_h4(sym))
    if len(data) < W + 10:
        return print(f"{sym}: too little data ({len(data)} bars)")
    mid = len(data) * 2 // 3
    sigs = analyze(data, min_score, dominance)
    train = [s for s in sigs if s["date"] < data[mid]["date"]]
    test = [s for s in sigs if s["date"] >= data[mid]["date"]]

    def stats(s, l):
        if not s: print(f"  {l}: 0 signals"); return
        h = sum(1 for x in s if x["hit"]); wr = h/len(s)*100
        a = np.mean([x["ret"] for x in s])
        print(f"  {l}: {len(s):>3d} sigs, WR={wr:.1f}%, ret={a:+.2f}%")
        for x in s[-3:]:
            print(f"    {x['date']} {x['dir']:6s} B={x['bull']} S={x['bear']} ret={x['ret']:+.2f}% {'✅' if x['hit'] else '❌'}")

    print(f"\n=== {sym} H4 (min={min_score}, dom={dominance}) ===")
    print(f"  Period: {data[0]['date']}..{data[-1]['date']} ({len(data)} H4 bars)")
    stats(sigs, "All")
    stats(train, "Train")
    stats(test, "Test")


if __name__ == "__main__":
    symbols = sys.argv[1:] if len(sys.argv) > 1 else ["Si"]
    for s in symbols:
        run(s, min_score=2, dominance=1.5)
