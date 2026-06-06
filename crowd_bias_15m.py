#!/usr/bin/env python3
"""
CROWD BIAS V1 — торговля против толпы на 15m.

Идея: FIZ (розница) почти всегда в лонге на Si (79-80% от всех лонгов).
Когда FIZ начинает экстремально набирать или сбрасывать — киты (YUR) давят
в обратную сторону.

Сигнал: OI bias (15m) + цена (15m OHLCV).

Usage:
  python3 crowd_bias_15m.py              # Si (default)
  python3 crowd_bias_15m.py BR GD        # Multiple tickers
"""
import sys, os
from datetime import datetime, timedelta
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD
import psycopg2, numpy as np

W = 96      # 96 * 15m = 24 часа
LOOKBACK = 20  # 20 * 15m = 5 часов
TARGET = 12    # 12 * 15m = 3 часа forward


def load(sym):
    """Загрузить 15m OI + цены."""
    conn = psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD)
    cur = conn.cursor()

    since = (datetime.now() - timedelta(days=400)).strftime("%Y-%m-%d")

    # ── 15m OI из moex_prices_5m_oi ──
    cur.execute(f"""
        SELECT
            date_trunc('hour', time) + floor(EXTRACT('minute' FROM time) / 15) * interval '15 minute' AS bar,
            AVG(fiz_buy)::float AS fiz_buy, AVG(fiz_sell)::float AS fiz_sell,
            AVG(yur_buy)::float AS yur_buy, AVG(yur_sell)::float AS yur_sell
        FROM moex_prices_5m_oi
        WHERE symbol = %s AND time >= %s::date
        GROUP BY bar ORDER BY bar
    """, (sym, since))
    oi = {r[0]: {"fiz_buy": r[1], "fiz_sell": r[2], "yur_buy": r[3], "yur_sell": r[4]} for r in cur.fetchall()}

    # ── 15m OHLCV из moex_prices_5m ──
    cur.execute(f"""
        SELECT
            date_trunc('hour', time) + floor(EXTRACT('minute' FROM time) / 15) * interval '15 minute' AS bar,
            (array_agg(open ORDER BY time))[1] AS open,
            MAX(high) AS high, MIN(low) AS low,
            (array_agg(close ORDER BY time DESC))[1] AS close,
            SUM(volume) AS volume
        FROM moex_prices_5m
        WHERE symbol = %s AND time >= %s::date AND volume > 0
        GROUP BY bar ORDER BY bar
    """, (sym, since))
    price = {r[0]: {"open": r[1], "high": r[2], "low": r[3], "close": r[4], "volume": r[5]} for r in cur.fetchall()}
    conn.close()

    # ── Соединяем ──
    times = sorted(set(oi) & set(price))
    data = []
    for t in times:
        o = oi[t]; p = price[t]
        fiz_net = o["fiz_buy"] - o["fiz_sell"]
        yur_net = o["yur_buy"] - o["yur_sell"]
        total_oi = o["fiz_buy"] + o["fiz_sell"] + o["yur_buy"] + o["yur_sell"]
        fiz_ratio = fiz_net / total_oi * 100 if total_oi > 0 else 0  # % от всего OI
        fiz_long_pct = o["fiz_buy"] / (o["fiz_buy"] + o["yur_buy"]) * 100 if (o["fiz_buy"] + o["yur_buy"]) > 0 else 50
        fiz_short_pct = o["fiz_sell"] / (o["fiz_sell"] + o["yur_sell"]) * 100 if (o["fiz_sell"] + o["yur_sell"]) > 0 else 50

        data.append({
            "date": t, "open": p["open"], "high": p["high"], "low": p["low"],
            "close": p["close"], "volume": p["volume"],
            "fiz_net": fiz_net, "yur_net": yur_net,
            "fiz_ratio": fiz_ratio,
            "fiz_long_pct": fiz_long_pct, "fiz_short_pct": fiz_short_pct,
            "fiz_buy": o["fiz_buy"], "fiz_sell": o["fiz_sell"],
            "yur_buy": o["yur_buy"], "yur_sell": o["yur_sell"],
        })
    return data


def compute(data):
    """Вычислить фичи для 15m баров."""
    N = len(data)
    for i in range(N):
        d = data[i]
        # Дельта fiz_ratio (изменение перекоса)
        if i > 0:
            p = data[i-1]
            d["ratio_d1"] = d["fiz_ratio"] - p["fiz_ratio"]
            d["price_d1"] = (d["close"] - p["close"]) / p["close"] * 100
        else:
            d["ratio_d1"] = 0; d["price_d1"] = 0

        for n in [5, 10, 20]:
            if i >= n:
                r = data[i-n]
                d[f"ratio_d{n}"] = d["fiz_ratio"] - r["fiz_ratio"]
                d[f"price_d{n}"] = (d["close"] - r["close"]) / r["close"] * 100
                d[f"volume_d{n}"] = d["volume"] / max(sum(x["volume"] for x in data[i-n+1:i+1]), 1)
            else:
                d[f"ratio_d{n}"] = 0; d[f"price_d{n}"] = 0; d[f"volume_d{n}"] = 1.0

        # Z-score и перцентили
        if i >= W:
            for k in ["fiz_ratio", "fiz_long_pct", "fiz_short_pct", "fiz_net"]:
                a = np.array([data[j][k] for j in range(i-W, i)])
                std = np.std(a)
                d[f"z_{k}"] = (d[k] - np.mean(a)) / std if std > 0 else 0
                d[f"pct_{k}"] = np.sum(a < d[k]) / len(a) * 100

            # Streak: как долго fiz_ratio растёт/падает
            up = dn = 0
            for j in range(i-1, max(i-40, 0)-1, -1):
                if data[j]["fiz_ratio"] < data[j+1]["fiz_ratio"]: up += 1
                else: break
            for j in range(i-1, max(i-40, 0)-1, -1):
                if data[j]["fiz_ratio"] > data[j+1]["fiz_ratio"]: dn += 1
                else: break
            d["ratio_up_streak"] = up; d["ratio_dn_streak"] = dn
        else:
            for k in ["fiz_ratio", "fiz_long_pct", "fiz_short_pct", "fiz_net"]:
                d[f"z_{k}"] = 0; d[f"pct_{k}"] = 50
            d["ratio_up_streak"] = d["ratio_dn_streak"] = 0

    # Forward return
    for i, d in enumerate(data):
        if i + TARGET < N:
            future = data[i+1:i+TARGET+1]
            d["best_close"] = max(x["close"] for x in future)
            d["worst_close"] = min(x["close"] for x in future)
            d["ret_fwd"] = (future[-1]["close"] - d["close"]) / d["close"] * 100
        else:
            d["best_close"] = d["worst_close"] = d["close"]
            d["ret_fwd"] = 0
    return data


# ── Signal logic ──────────────────────────────────────────────
BULLISH = [
    # FIZ перекосило в шорты — они паникуют, киты будут давить вверх
    ("CROWD_PANIC_SHORT", lambda d: d.get("z_fiz_ratio", 0) < -2.0),
    # FIZ резко сбрасывает лонги (ratio падает)
    ("CROWD_FLEE_LONG", lambda d: d.get("ratio_d20", 0) < -0.3 and d.get("z_fiz_ratio", 0) < -1.5),
    # FIZ в шортах на 95+ перцентиле
    ("CROWD_SHORT_PEAK", lambda d: d.get("pct_fiz_ratio", 50) <= 5 and d.get("fiz_short_pct", 0) > 80),
]

BEARISH = [
    # FIZ перегружены в лонг — киты развернут
    ("CROWD_EUPHORIA_LONG", lambda d: d.get("z_fiz_ratio", 0) > 2.0),
    # FIZ ускоряет набор лонгов (ratio растёт)
    ("CROWD_CHASING", lambda d: d.get("ratio_d20", 0) > 0.3 and d.get("z_fiz_ratio", 0) > 1.5),
    # FIZ в лонгах на 95+ перцентиле
    ("CROWD_LONG_PEAK", lambda d: d.get("pct_fiz_ratio", 50) >= 95 and d.get("fiz_long_pct", 0) > 80),
]


def analyze(data, min_score=1, dominance=1.5):
    signals = []
    for i, d in enumerate(data[W:], start=W):
        bull = sum(1 for _, c in BULLISH if c(d))
        bear = sum(1 for _, c in BEARISH if c(d))
        total = bull + bear
        if total < min_score:
            continue
        if bull >= bear * dominance:
            signals.append({"date": d["date"], "dir": "LONG", "ret": d["ret_fwd"],
                "hit": d["ret_fwd"] > 0, "bull": bull, "bear": bear,
                "ratio": d["fiz_ratio"], "z": d.get("z_fiz_ratio", 0)})
        elif bear >= bull * dominance:
            signals.append({"date": d["date"], "dir": "SHORT", "ret": d["ret_fwd"],
                "hit": d["ret_fwd"] < 0, "bull": bull, "bear": bear,
                "ratio": d["fiz_ratio"], "z": d.get("z_fiz_ratio", 0)})
    return signals


def run(sym, min_score=1, dominance=1.5):
    data = compute(load(sym))
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
            print(f"    {x['date']} {x['dir']:6s} r={x['ratio']:+.1f}% z={x['z']:+.1f} ret={x['ret']:+.3f}% {'✅' if x['hit'] else '❌'}")

    print(f"\n=== {sym} CROWD BIAS 15m (min={min_score}, dom={dominance}) ===")
    print(f"  Period: {data[0]['date']}..{data[-1]['date']} ({len(data)} bars)")
    stats(sigs, "All")
    stats(train, "Train")
    stats(test, "Test")


if __name__ == "__main__":
    symbols = sys.argv[1:] if len(sys.argv) > 1 else ["Si"]
    for s in symbols:
        run(s, min_score=1, dominance=1.5)
