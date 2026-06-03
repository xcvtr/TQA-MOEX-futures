#!/usr/bin/env python3
"""MOEX Volume Climax Equity Dashboard — Canvas-based, no CDN."""

import os, sys, json, math, http.server, socket
from datetime import datetime, timedelta, timezone
import psycopg2
import numpy as np

DB = dict(host="10.0.0.60", port=5432, dbname="moex", user="postgres", password=os.environ.get("MOEX_DB_PASSWORD", "***"))
PORT = 5057

# ── Guarantee (GO) by ticker (from MOEX ISS front-month) ──
# go_rub: initial margin in RUB for 1 contract. lev computed dynamically from Alor price.
GO_DATA = {
    # Commodity/currency futures (per ISS front-month GO)
    "CC": {"go_rub": 473}, "PD": {"go_rub": 22173}, "SS": {"go_rub": 205},
    "GZ": {"go_rub": 2065}, "NG": {"go_rub": 6565}, "GL": {"go_rub": 1220},
    "SE": {"go_rub": 625},  "SN": {"go_rub": 8180}, "HY": {"go_rub": 804},
    "IB": {"go_rub": 803},  "NM": {"go_rub": 1405},
    # Stock futures (ISS front-month GO)
    "GK": {"go_rub": 234},  "MG": {"go_rub": 4096}, "RN": {"go_rub": 8180},
    "AL": {"go_rub": 660},  "SP": {"go_rub": 1008},  "ME": {"go_rub": 3149},
    "CE": {"go_rub": 1187}, "HS": {"go_rub": 231},
}
DEFAULT_LEV = 5.0
# New champions: strongest performers (Real WR × PF × GO return) + new stock futures
# GK=NorNickel, MG=Magnitogorsk, RN=Rosneft, AL=Alrosa, SP=SPBE, ME=MOEX
CHAMPIONS = [
    ("ME", "MOEX"), ("GK", "NorNickel"), ("CC", "Cocoa C"),
    ("PD", "Palladium"), ("SP", "SPBE"),   ("SS", "Sugar"),
    ("NM", "NLMK"),     ("GZ", "Gazprom"), ("NG", "Nat Gas"),
    ("IB", "I-Bonds"),  ("GL", "Gold L"),  ("SE", "Soybean"),
    ("AL", "Alrosa"),   ("MG", "MMK"),     ("RN", "Rosneft"),
    ("CE", "Copper"),   ("HS", "Hang Seng"), ("HY", "Hryvnia"),
    ("SN", "Tin"),
]

H4_WINDOW = 20  # rolling median window
TARGET_BARS = 2  # max hold in H4 bars
ENTRY_SLIPPAGE = 0.001  # 0.1% slippage on entry
TP_PCT = 0.004          # 0.4% take-profit limit
SL_PCT = 0.008          # 0.8% stop-loss
TRAIL_ACTIVATE = 0.005  # 0.5% → trail stop to breakeven

def get_conn():
    return psycopg2.connect(**DB)

def load_bars(symbol, since="2024-01-01"):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT time, open, high, low, close, volume
        FROM moex_prices_5m
        WHERE symbol = %s AND time >= %s AND volume > 0
        ORDER BY time
    """, (symbol, since))
    rows = cur.fetchall()
    conn.close()
    return rows

def resample_h4(rows):
    """Resample 5m bars to H4. MOEX H4: midnight UTC + 4h chunks."""
    if not rows:
        return []
    h4 = {}
    for t, o, h, l, c, v in rows:
        # H4 bucket: truncate to hour, then floor to 4h
        h4_key = t.replace(minute=0, second=0, microsecond=0)
        h4_key = h4_key - timedelta(hours=h4_key.hour % 4)
        if h4_key not in h4:
            h4[h4_key] = [t, o, h, l, c, v]
        else:
            prev = h4[h4_key]
            h4[h4_key] = [prev[0], prev[1], max(prev[2], h), min(prev[3], l), c, prev[5] + v]
    return sorted((vals for vals in h4.values()), key=lambda x: x[0])

def compute_h4_features(h4_bars):
    """Add indicators: vol_ratio, range_pct, close_pos, rolling_median_vol."""
    if len(h4_bars) < H4_WINDOW + 5:
        return []
    
    data = []
    for i, (t, o, h, l, c, v) in enumerate(h4_bars):
        d = {"time": t, "open": o, "high": h, "low": l, "close": c, "volume": v,
             "range_pct": (h - l) / l * 100 if l else 0}
        
        if i >= H4_WINDOW:
            window = h4_bars[i - H4_WINDOW:i]
            vols = [w[5] for w in window]
            med_vol = np.median(vols) if vols else 1
            d["vol_ratio"] = v / max(med_vol, 1)
            
            ranges = [(w[2] - w[3]) / w[3] * 100 for w in window if w[3] > 0]
            d["avg_range_pct"] = np.mean(ranges) if ranges else 0
            
            # Close position in range: 0 (low) to 1 (high)
            d["close_pos"] = (c - l) / (h - l) if h != l else 0.5
        else:
            d["vol_ratio"] = 0
            d["avg_range_pct"] = 0
            d["close_pos"] = 0.5
        
        data.append(d)
    return data

def find_signals(data):
    """Volume Climax strategy — realistic exchange simulation.
    
    Entry: next H4 bar open +0.1% slippage (market order at open)
    TP:    limit order at 0.4% from entry
    SL:    stop-loss at 0.8% 
    Trail: after 0.5% in our favor → trail SL to breakeven +0.1%
    Max hold: 2 H4 bars → exit at close if TP/SL not hit
    
    Also keeps touch/close metrics for comparison.
    """
    sigs = []
    for i, d in enumerate(data):
        if d["vol_ratio"] <= 2 or d["range_pct"] <= d.get("avg_range_pct", 0):
            continue
        
        is_red = d["close"] < d["open"]
        is_bear_climax = is_red and d["close_pos"] <= 0.35
        is_green = d["close"] > d["open"]
        is_bull_climax = is_green and d["close_pos"] >= 0.65
        
        if not is_bear_climax and not is_bull_climax:
            continue
        
        if i + 1 + TARGET_BARS >= len(data):
            continue
        
        entry_bar = data[i+1]
        entry = entry_bar["open"] * (1 + ENTRY_SLIPPAGE)
        hold_bars = [data[i+1+k] for k in range(TARGET_BARS)]
        
        # Legacy metrics for comparison
        if is_bear_climax:
            best_close = max(f["close"] for f in hold_bars)
            best_touch = max(f["high"] for f in hold_bars)
            close_win = best_close >= entry * 1.002
            touch_win = best_touch >= entry * 1.002
            ret_close = (best_close - entry) / entry * 100 if close_win else (min(f["low"] for f in hold_bars) - entry) / entry * 100
            ret_touch = (best_touch - entry) / entry * 100
        else:
            worst_close = min(f["close"] for f in hold_bars)
            worst_touch = min(f["low"] for f in hold_bars)
            close_win = worst_close <= entry * 0.998
            touch_win = worst_touch <= entry * 0.998
            ret_close = (entry - worst_close) / entry * 100 if close_win else (entry - max(f["high"] for f in hold_bars)) / entry * 100
            ret_touch = (entry - worst_touch) / entry * 100
        
        # ── Realistic execution model ──
        if is_bear_climax:
            tp = entry * (1 + TP_PCT)
            sl = entry * (1 - SL_PCT)
            trail_be = entry * 1.001  # breakeven stop
            trailed = False
            real_exit = None
            real_reason = "timeout"
            
            # Simulate bar by bar
            trail_sl = sl  # current protective stop
            for bar in hold_bars:
                # Check TP first
                if bar["high"] >= tp:
                    real_exit = tp
                    real_reason = "tp"
                    break
                # Check SL
                if bar["low"] <= trail_sl:
                    real_exit = trail_sl
                    real_reason = "sl"
                    break
                # Trail to breakeven if price moved enough
                if not trailed and bar["high"] >= entry * (1 + TRAIL_ACTIVATE):
                    trail_sl = trail_be
                    trailed = True
            
            if real_exit is None:
                # Exit at close of last bar
                real_exit = hold_bars[-1]["close"]
                real_reason = "expiry"
            
            real_ret = (real_exit - entry) / entry * 100
            real_win = real_ret > 0
            
            sigs.append({
                "time": str(d["time"].date()), "dir": "LONG",
                "entry": entry,
                "tp": tp, "sl": sl,
                "real_ret": round(real_ret, 2),
                "real_win": real_win,
                "real_reason": real_reason,
                "close_win": close_win, "touch_win": touch_win,
                "ret_close": round(ret_close, 2),
                "ret_touch": round(ret_touch, 2),
                "vol_ratio": round(d["vol_ratio"], 1),
            })
        else:
            tp = entry * (1 - TP_PCT)
            sl = entry * (1 + SL_PCT)
            trail_be = entry * 0.999
            trailed = False
            real_exit = None
            real_reason = "timeout"
            
            trail_sl = sl
            for bar in hold_bars:
                if bar["low"] <= tp:
                    real_exit = tp
                    real_reason = "tp"
                    break
                if bar["high"] >= trail_sl:
                    real_exit = trail_sl
                    real_reason = "sl"
                    break
                if not trailed and bar["low"] <= entry * (1 - TRAIL_ACTIVATE):
                    trail_sl = trail_be
                    trailed = True
            
            if real_exit is None:
                real_exit = hold_bars[-1]["close"]
                real_reason = "expiry"
            
            real_ret = (entry - real_exit) / entry * 100
            real_win = real_ret > 0
            
            sigs.append({
                "time": str(d["time"].date()), "dir": "SHORT",
                "entry": entry,
                "tp": tp, "sl": sl,
                "real_ret": round(real_ret, 2),
                "real_win": real_win,
                "real_reason": real_reason,
                "close_win": close_win, "touch_win": touch_win,
                "ret_close": round(ret_close, 2),
                "ret_touch": round(ret_touch, 2),
                "vol_ratio": round(d["vol_ratio"], 1),
            })
    return sigs

def compute_equity(sigs):
    """Compute cumulative equity curves: realistic (TP/SL), touch, close, GO-based."""
    if not sigs:
        return {"real": [], "touch": [], "close": [], "go": []}
    cum_real = cum_touch = cum_close = cum_go = 0
    curve_real = curve_touch = curve_close = curve_go = []
    for s in sigs:
        cum_real += s["real_ret"] / 100.0
        cum_touch += s["ret_touch"] / 100.0
        cum_close += s["ret_close"] / 100.0
        cum_go += s["ret_go"] / 100.0
        curve_real.append({"date": s["time"], "equity": round(cum_real * 100, 2)})
        curve_touch.append({"date": s["time"], "equity": round(cum_touch * 100, 2)})
        curve_close.append({"date": s["time"], "equity": round(cum_close * 100, 2)})
        curve_go.append({"date": s["time"], "equity": round(cum_go * 100, 2)})
    return {"real": curve_real, "touch": curve_touch, "close": curve_close, "go": curve_go}

def compute_stats(sigs):
    """Compute trading stats. Primary = realistic (TP/SL), secondary = touch/close."""
    if not sigs:
        return {"signals": 0}
    
    n = len(sigs)
    real_ret = [s["real_ret"] for s in sigs]
    go_ret = [s["ret_go"] for s in sigs]  # % of GO instead of notional
    touch_ret = [s["ret_touch"] if s["dir"]=="LONG" else -s["ret_touch"] for s in sigs]
    close_pnls = [s["ret_close"] if s["dir"]=="LONG" else -s["ret_close"] for s in sigs]
    
    # Realistic (primary) — notional-based
    real_wins = sum(1 for s in sigs if s["real_win"])
    real_total = sum(real_ret)
    real_avg = np.mean(real_ret) if real_ret else 0
    real_gp = sum(p for p in real_ret if p > 0)
    real_gl = abs(sum(p for p in real_ret if p < 0))
    real_pf = real_gp / max(real_gl, 0.001)
    real_cum = np.cumsum(real_ret) if real_ret else [0]
    real_peak = np.maximum.accumulate(real_cum)
    real_dd = real_cum - real_peak
    real_max_dd = min(real_dd) if len(real_dd) > 0 else 0
    
    # GO-based (with leverage)
    go_total = sum(go_ret)
    go_avg = np.mean(go_ret) if go_ret else 0
    go_cum = np.cumsum(go_ret) if go_ret else [0]
    go_peak = np.maximum.accumulate(go_cum)
    go_dd = go_cum - go_peak
    go_max_dd = min(go_dd) if len(go_dd) > 0 else 0
    go_gp = sum(p for p in go_ret if p > 0)
    go_gl = abs(sum(p for p in go_ret if p < 0))
    go_pf = go_gp / max(go_gl, 0.001)
    
    # Touch (best case)
    touch_wins = sum(1 for s in sigs if s["touch_win"])
    touch_total = sum(touch_ret)
    touch_cum = np.cumsum(touch_ret) if touch_ret else [0]
    touch_peak = np.maximum.accumulate(touch_cum)
    touch_dd = touch_cum - touch_peak
    touch_max_dd = min(touch_dd) if len(touch_dd) > 0 else 0
    
    # Close (realistic exit but by close)
    close_wins = sum(1 for s in sigs if s["close_win"])
    close_total = sum(close_pnls)
    close_cum = np.cumsum(close_pnls) if close_pnls else [0]
    close_peak = np.maximum.accumulate(close_cum)
    close_dd = close_cum - close_peak
    close_max_dd = min(close_dd) if len(close_dd) > 0 else 0
    
    # Direction breakdown (realistic)
    long_sigs = [s for s in sigs if s["dir"] == "LONG"]
    short_sigs = [s for s in sigs if s["dir"] == "SHORT"]
    long_wr = sum(1 for s in long_sigs if s["real_win"]) / max(len(long_sigs), 1) * 100
    short_wr = sum(1 for s in short_sigs if s["real_win"]) / max(len(short_sigs), 1) * 100
    
    # Exit reason breakdown
    tp_cnt = sum(1 for s in sigs if s.get("real_reason") == "tp")
    sl_cnt = sum(1 for s in sigs if s.get("real_reason") == "sl")
    exp_cnt = sum(1 for s in sigs if s.get("real_reason") == "expiry")
    
    return {
        "signals": n,
        # Realistic (TP/SL model)
        "real_wins": real_wins, "real_losses": n - real_wins,
        "real_wr": round(real_wins / max(n, 1) * 100, 1),
        "real_total_pnl": round(real_total, 2),
        "real_avg_ret": round(real_avg, 2),
        "real_pf": round(real_pf, 2),
        "real_max_dd": round(real_max_dd, 2),
        # GO-based (with leverage)
        "go_total_pnl": round(go_total, 2),
        "go_avg_ret": round(go_avg, 2),
        "go_pf": round(go_pf, 2),
        "go_max_dd": round(go_max_dd, 2),
        # Touch (secondary)
        "touch_wr": round(touch_wins / max(n, 1) * 100, 1),
        "touch_total_pnl": round(touch_total, 2),
        "touch_max_dd": round(touch_max_dd, 2),
        # Close (tertiary)
        "close_wr": round(close_wins / max(n, 1) * 100, 1),
        "close_total_pnl": round(close_total, 2),
        "close_max_dd": round(close_max_dd, 2),
        # Direction
        "long_wr": round(long_wr, 1), "short_wr": round(short_wr, 1),
        "long_count": len(long_sigs), "short_count": len(short_sigs),
        # Exit reasons
        "tp_count": tp_cnt, "sl_count": sl_cnt, "expiry_count": exp_cnt,
        "all_real_ret": real_ret,
    }

def generate_analysis(stats, sigs):
    """Generate human-readable analysis text for a ticker."""
    if not sigs or stats["signals"] == 0:
        return {"verdict": "neutral", "short": "Недостаточно сигналов.", "detail": ""}
    
    wr = stats["real_wr"]
    pf = stats["real_pf"]
    dd = stats["real_max_dd"]
    avg = stats["real_avg_ret"]
    total = stats["real_total_pnl"]
    go_total = stats["go_total_pnl"]
    go_avg = stats["go_avg_ret"]
    go_pf = stats["go_pf"]
    go_dd = stats["go_max_dd"]
    twr = stats["touch_wr"]
    cwr = stats["close_wr"]
    tp_cnt = stats["tp_count"]
    sl_cnt = stats["sl_count"]
    exp_cnt = stats["expiry_count"]
    
    lw = stats["long_wr"]
    sw = stats["short_wr"]
    lc = stats["long_count"]
    sc = stats["short_count"]
    n = stats["signals"]
    
    if wr >= 55:
        verdict = "strong_buy"
        verdict_label = "🟢 Рабочий"
    elif wr >= 48:
        verdict = "buy"
        verdict_label = "🟡 Перспективный"
    elif wr >= 42:
        verdict = "neutral"
        verdict_label = "⚪ Нейтральный"
    else:
        verdict = "sell"
        verdict_label = "🔴 Слабый"
    
    short_parts = []
    short_parts.append(f"Real WR {wr:.0f}%")
    short_parts.append(f"TP {tp_cnt}/{sl_cnt}/{exp_cnt}")
    short_parts.append(f"средняя {avg:+.1f}%")
    if pf >= 1.2:
        short_parts.append(f"PF {pf:.1f}")
    if dd < -10:
        short_parts.append(f"⚠️ DD {dd:.0f}%")
    
    short = f"{verdict_label} · {' · '.join(short_parts)}"
    
    lines = []
    
    # Model explanation
    lines.append(f"📐 Модель: вход по open +0.1%, TP {TP_PCT*100:.1f}%, SL {SL_PCT*100:.1f}%, "
                f"трейлинг после {TRAIL_ACTIVATE*100:.1f}%, макс {TARGET_BARS} бара")
    
    # Leverage info
    lev = sigs[0].get("lev", 5.0) if sigs else 5.0
    if go_total != 0 and go_pf > 0:
        lines.append(f"💰 С плечом {lev:.1f}x: GO Σ {go_total:+.0f}% · средняя {go_avg:+.1f}% · PF {go_pf:.2f} · DD {go_dd:.0f}%")
    else:
        lines.append(f"💰 Без плеча: Σ {total:+.0f}% ({avg:+.2f}%/сделку) · PF {pf:.2f}")
    
    # Exit breakdown
    tp_pct = tp_cnt / max(n, 1) * 100
    sl_pct = sl_cnt / max(n, 1) * 100
    exp_pct = exp_cnt / max(n, 1) * 100
    lines.append(f"Выходы: TP {tp_cnt} ({tp_pct:.0f}%) · SL {sl_cnt} ({sl_pct:.0f}%) · по времени {exp_cnt} ({exp_pct:.0f}%)")
    
    # Comparisons
    lines.append(f"Сравнение: Real {wr:.0f}% vs Touch {twr:.0f}% vs Close {cwr:.0f}%")
    
    # Long vs Short
    if lc >= 5 and sc >= 5 and abs(lw - sw) >= 8:
        better = "лонги" if lw > sw else "шорты"
        lines.append(f"Преимущество {better}: {lw:.0f}% vs {sw:.0f}%")
    
    # PF / quality
    if pf >= 1.3:
        lines.append(f"PF {pf:.2f} — есть запас прибыли над убытками")
    elif pf >= 1.0:
        lines.append(f"PF {pf:.2f} — на грани безубытка")
    else:
        lines.append(f"PF {pf:.2f} — убытки превышают прибыль")
    
    if dd < -30:
        lines.append(f"⚠️ Просадка {dd:.0f}% — нужен стоп-лосс жёстче")
    elif dd < -10:
        lines.append(f"Просадка {dd:.0f}% — терпимо с контролем риска")
    else:
        lines.append(f"Просадка {dd:.0f}% — хорошо")
    
    # Avg ret analysis
    rets = stats.get("all_real_ret", [])
    if rets:
        avg_win = np.mean([r for r in rets if r > 0]) if any(r > 0 for r in rets) else 0
        avg_loss = abs(np.mean([r for r in rets if r < 0])) if any(r < 0 for r in rets) else 0
        if avg_win > 0 and avg_loss > 0:
            lines.append(f"Средняя прибыль {avg_win:+.2f}% / убыток {avg_loss:+.2f}% (ratio {avg_win/max(avg_loss,0.001):.1f})")
    
    if total > 0:
        lines.append(f"Σ {total:+.0f}% за {n} сделок")
    
    # Recommendation
    if wr >= 50 and pf >= 1.2 and dd > -20:
        lines.append(f"\n💡 Кандидат для тестовой торговли. Контролировать риск.")
    elif wr >= 45 and pf >= 1.0:
        lines.append(f"\n💡 Можно пробовать с фильтром (тренд/OI). Требует калибровки.")
    else:
        lines.append(f"\n💡 Не рекомендуется для самостоятельной торговли")
    
    return {
        "verdict": verdict,
        "short": short,
        "detail": "\n".join(lines),
    }

def process_ticker(symbol, name):
    """Full pipeline for one ticker."""
    rows = load_bars(symbol)
    if not rows:
        return None
    
    h4 = resample_h4(rows)
    if len(h4) < H4_WINDOW + TARGET_BARS + 10:
        return None
    
    features = compute_h4_features(h4)
    sigs = find_signals(features)
    if not sigs:
        return None
    
    # Add GO-based returns (real PnL as % of margin, not % of notional)
    go_info = GO_DATA.get(symbol, {})
    go_rub = go_info.get("go_rub", 0)
    # Compute leverage from first entry price / GO
    if go_rub > 0 and sigs:
        last_close = abs(sigs[-1]["entry"])
        lev = max(last_close / go_rub, 0.5)  # cap minimum at 0.5x
    else:
        lev = DEFAULT_LEV
    for s in sigs:
        s["lev"] = round(lev, 2)
        s["ret_go"] = round(s["real_ret"] * lev, 2)
    
    equity = compute_equity(sigs)
    stats = compute_stats(sigs)
    
    # Price series for chart
    prices = [{"time": str(f["time"].date()), "close": f["close"],
               "open": f["open"], "high": f["high"], "low": f["low"]}
              for f in features]
    
    analysis = generate_analysis(stats, sigs)
    return {
        "symbol": symbol, "name": name,
        "stats": stats, "sigs": sigs,
        "equity": equity, "prices": prices,
        "analysis": analysis,
        # Summary for sorting/filtering
        "wr": stats["real_wr"],
        "total_pnl": stats["real_total_pnl"],
        "profit_factor": stats["real_pf"],
        "avg_ret": stats["real_avg_ret"],
        "signals": stats["signals"],
        "lev": lev,
    }

def load_all():
    """Process all champion tickers."""
    results = []
    for sym, name in CHAMPIONS:
        r = process_ticker(sym, name)
        if r and r["stats"]["signals"] >= 10:  # minimum signal threshold
            results.append(r)
    # Sort by close WR descending
    results.sort(key=lambda x: x["wr"], reverse=True)
    return results

# ── HTML Template ──

HTML = r"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>MOEX Volume Climax — Equity Dashboard</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Courier New',monospace;background:#0d1117;color:#c9d1d9;padding:15px}
h1{color:#58a6ff;font-size:1.3em;margin-bottom:5px}
h2{color:#f0883e;font-size:1.1em;margin:15px 0 8px}
.sub{color:#8b949e;font-size:0.85em;margin-bottom:15px}
.dash-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:8px;margin-bottom:20px}
.card{background:#161b22;border:1px solid #30363d;border-radius:6px;padding:10px;cursor:pointer;transition:border-color .15s}
.card:hover{border-color:#58a6ff}
.card.sel{border-color:#f0883e}
.card h3{font-size:0.95em;color:#c9d1d9}
.card .sym{color:#58a6ff;font-weight:bold}
.card .stat{font-size:0.85em;color:#8b949e;margin-top:3px}
.card .wr{font-weight:bold}
.card .wr.high{color:#3fb950}
.card .wr.mid{color:#d29922}
.card .wr.low{color:#f85149}
.card .pnl{font-weight:bold}
.card .pnl.pos{color:#3fb950}
.card .pnl.neg{color:#f85149}
#main-chart{width:100%;height:400px;background:#0d1117;border:1px solid #30363d;border-radius:6px;margin-bottom:10px}
#eq-chart{width:100%;height:200px;background:#0d1117;border:1px solid #30363d;border-radius:6px;margin-bottom:10px}
.stats-row{display:flex;gap:15px;flex-wrap:wrap;margin-bottom:15px;font-size:0.85em}
.stat-box{background:#161b22;border:1px solid #30363d;border-radius:4px;padding:6px 12px;min-width:80px;text-align:center}
.stat-box .label{color:#8b949e;font-size:0.8em}
.stat-box .val{font-weight:bold;color:#c9d1d9;margin-top:2px}
.stat-box .val.pos{color:#3fb950}
.stat-box .val.neg{color:#f85149}
.sig-table{width:100%;border-collapse:collapse;font-size:0.8em;margin-bottom:20px}
.sig-table th{background:#161b22;color:#8b949e;padding:4px 8px;text-align:center;border:1px solid #30363d}
.sig-table td{padding:3px 8px;text-align:center;border:1px solid #30363d}
.sig-table tr.w td{color:#3fb950}
.sig-table tr.l td{color:#f85149}
.filter-bar{margin:10px 0;display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.filter-bar label{color:#8b949e;font-size:0.85em}
.filter-bar select{padding:3px 6px;background:#161b22;color:#c9d1d9;border:1px solid #30363d;border-radius:4px;font-family:inherit;font-size:0.85em}
.filter-bar input{padding:3px 6px;background:#161b22;color:#c9d1d9;border:1px solid #30363d;border-radius:4px;font-family:inherit;font-size:0.85em;width:60px}
.summary-table{width:100%;border-collapse:collapse;font-size:0.85em;margin-bottom:20px}
.summary-table th{background:#161b22;color:#8b949e;padding:4px 8px;text-align:center;border:1px solid #30363d;cursor:pointer;user-select:none}
.summary-table th:hover{background:#21262d}
.summary-table td{padding:3px 8px;text-align:center;border:1px solid #30363d}
.summary-table tr:hover td{background:#161b22}
.sorted::after{content:'▲';margin-left:4px;color:#58a6ff}
.sorted.desc::after{content:'▼'}
.analysis-card{background:#161b22;border:1px solid #30363d;border-radius:6px;padding:12px;margin-bottom:10px}
.analysis-card.strong_buy{border-color:#3fb950}
.analysis-card.buy{border-color:#d29922}
.analysis-card.neutral{border-color:#8b949e}
.analysis-card.sell{border-color:#f85149}
.analysis-short{font-size:0.9em;font-weight:bold;margin-bottom:8px;padding-bottom:8px;border-bottom:1px solid #30363d;white-space:pre-wrap}
.analysis-detail{font-size:0.8em;color:#c9d1d9;line-height:1.6;white-space:pre-wrap}
.analysis-detail .rec{color:#58a6ff;font-size:1.1em}
.edu-toggle{display:inline-block;background:#21262d;color:#8b949e;border:1px solid #30363d;border-radius:4px;padding:3px 10px;cursor:pointer;font-size:0.85em;margin-left:10px}
.edu-toggle:hover{border-color:#58a6ff;color:#c9d1d9}
.edu-panel{display:none;background:#0d1117;border:1px solid #30363d;border-radius:6px;padding:15px;margin:15px 0;font-size:0.85em;line-height:1.7}
.edu-panel.open{display:block}
.edu-panel h3{color:#58a6ff;font-size:1.1em;margin-bottom:10px}
.edu-panel h4{color:#f0883e;font-size:0.95em;margin:12px 0 6px}
.edu-panel p{color:#c9d1d9;margin-bottom:8px}
.edu-panel code{background:#161b22;color:#58a6ff;padding:1px 5px;border-radius:3px;font-size:0.95em}
.edu-panel .formula{background:#161b22;border:1px solid #30363d;border-radius:4px;padding:8px 12px;margin:8px 0;font-family:inherit;color:#f0883e}
.edu-panel .example{background:#161b22;border-left:3px solid #3fb950;border-radius:4px;padding:8px 12px;margin:8px 0}
.edu-panel table{width:100%;border-collapse:collapse;margin:8px 0}
.edu-panel th{background:#161b22;color:#8b949e;padding:4px 8px;border:1px solid #30363d;text-align:center;font-size:0.85em}
.edu-panel td{padding:3px 8px;border:1px solid #30363d;text-align:center;font-size:0.85em}
@media(max-width:900px){
  .dash-grid{grid-template-columns:repeat(auto-fill,minmax(200px,1fr))}
  .stats-row{gap:8px}
  .stat-box{padding:4px 8px;min-width:60px}
}
</style></head><body>
<h1>📊 MOEX Volume Climax — Equity Dashboard</h1>
<p class="sub" id="sub-info">Loading data... <span class="edu-toggle" id="eduBtn" onclick="toggleEdu()">📖 О расчётах GO</span></p>

<div class="filter-bar">
  <label>🔥 Sort: </label>
  <select id="sortBy" onchange="sortGrid()">
    <option value="wr">Win Rate</option>
    <option value="total_pnl">Total PnL</option>
    <option value="signals">Signal Count</option>
    <option value="profit_factor">Profit Factor</option>
    <option value="avg_ret">Avg Return</option>
  </select>
  <label>⬆️</label>
  <select id="sortDir" onchange="sortGrid()">
    <option value="desc">Desc</option>
    <option value="asc">Asc</option>
  </select>
  <label>🔍 Min WR: </label>
  <input type="number" id="minWR" value="45" min="0" max="100" onchange="sortGrid()">
  <label>Min signals: </label>
  <input type="number" id="minSig" value="20" min="0" onchange="sortGrid()">
</div>

<div id="summary-div"></div>
<div id="grid-div" class="dash-grid"></div>

<div id="detail-div" style="display:none">
  <h2 id="detail-title"></h2>
  <div id="analysis-card" class="analysis-card">
    <div class="analysis-short" id="analysis-short"></div>
    <div class="analysis-detail" id="analysis-detail"></div>
  </div>
  <div class="stats-row" id="detail-stats"></div>
  <canvas id="main-chart"></canvas>
  <canvas id="eq-chart"></canvas>
  <div id="detail-table"></div>
</div>

<!-- ─── Educational panel: GO Equity Calculation ─── -->
<div class="edu-panel" id="eduPanel">
  <h3>📘 Как считаются доходности на ГО (гарантийное обеспечение)</h3>
  
  <h4>Проблема</h4>
  <p>Фьючерсы на MOEX торгуются с плечом. Один контракт требует лишь часть полной стоимости — <strong>гарантийное обеспечение (ГО)</strong>. 
  Доходность в % от цены контракта (notional) не отражает реальную эффективность капитала трейдера.</p>
  
  <h4>Формула</h4>
  <div class="formula">ret_GO = ret_notional × leverage</div>
  
  <p>Где:</p>
  <p>• <code>ret_notional</code> — процент изменения цены контракта (наша Real Return)</p>
  <p>• <code>leverage</code> = стоимость контракта / ГО (кредитное плечо биржи)</p>
  
  <h4>Пример: CC (Cocoa C)</h4>
  <div class="example">
    <b>Параметры:</b> цена ~20 000 RUB, ГО = 473 RUB, плечо = 6.4x<br>
    <b>Сигнал:</b> TP 0.4% от entry → notional return = +0.4%<br>
    <b>На ГО:</b> 0.4% × 6.4 = <b>+2.6%</b> от вложенного капитала<br>
    <b>При WR 76%:</b> 10 сделок → ~7.6× +2.6% и ~2.4× −5.1% (SL 0.8% × 6.4)
  </div>

  <h4>Откуда берутся цифры плеча</h4>
  <p>ГО каждого фьючерса публикуется на <a href="https://www.moex.com/ru/contracts/forts/" style="color:#58a6ff" target="_blank">moex.com</a> в разделе FORTS. 
  Мы используем <strong>initial margin</strong> ближайшего фьючерса на 1 контракт. 
  Плечо считается как <code>цена_контракта / ГО</code> и меняется с ценой базового актива.</p>

  <h4>Ключевые расхождения с notional-подходом</h4>
  <table>
    <tr><th>Метрика</th><th>Notional (обычный)</th><th>GO (с плечом)</th><th>Разница</th></tr>
    <tr><td>Средняя сделка (CC)</td><td>+0.32%</td><td>+2.0%</td><td>× 6.4</td></tr>
    <tr><td>ΣPnL (CC)</td><td>+48%</td><td>+305%</td><td>× 6.4</td></tr>
    <tr><td>Просадка (CC)</td><td>−13.3%</td><td>−85%</td><td>× 6.4</td></tr>
  </table>
  <p>Просадка масштабируется <b>так же</b>, как и прибыль — плечо работает в обе стороны. 
  Именно поэтому в дашборде отображаются оба показателя: notional (для сравнения моделей) и GO (для реальной оценки риска/прибыли).</p>
  
  <h4>Зачем это трейдеру</h4>
  <p>• Реалистичная оценка <b>доходности на капитал</b> (ROI на ГО, а не на полную стоимость)<br>
     • Понимание <b>реального риска</b>: просадка 10% notional = 50-80% на ГО при плече 5-8x<br>
     • Сравнение инструментов: CC с WR 76% и плечом 6.4x даёт <b>+305%</b> на ГО vs NG с WR 70% и плечом 3.5x даёт +129%<br>
     • Оценка drawdown-риска для расчёта капитала под стратегию</p>
  <p style="margin-top:10px;color:#8b949e;font-size:0.8em;border-top:1px solid #30363d;padding-top:8px">
  ⚠️ Все расчёты — для образовательных целей. Прошлые результаты не гарантируют будущую доходность.
  </p>
</div>

<script>
// DATA will be injected here
const DATA = __DATA__;

function fmtPnl(v){return v>0?'+'+v.toFixed(1):v.toFixed(1)}
function wrClass(w){return w>=50?'high':w>=42?'mid':'low'}
function pnlClass(v){return v>=0?'pos':'neg'}

function toggleEdu(){
  const p = document.getElementById('eduPanel');
  const b = document.getElementById('eduBtn');
  p.classList.toggle('open');
  b.textContent = p.classList.contains('open') ? '📕 Скрыть GO' : '📖 О расчётах GO';
}

let selected = null;
let sortCol = 'wr';
let sortDir = 'desc';

function renderSummary(all){
  const totalSig = all.reduce((s,t)=>s+t.stats.signals,0);
  const totalW = all.reduce((s,t)=>s+t.stats.real_wins,0);
  const totalWR = totalSig>0?(totalW/totalSig*100).toFixed(1):'0';
  const totalPnL = all.reduce((s,t)=>s+t.stats.real_total_pnl,0);
  const wr50 = all.filter(t=>t.stats.real_wr>=50).length;
  document.getElementById('sub-info').textContent =
    `${all.length} tickers · ${totalSig} signals · ${totalWR}% real WR · ${fmtPnl(totalPnL)}% ΣPnL · ${wr50} rw≥50%`;
}

function renderGrid(all){
  const grid = document.getElementById('grid-div');
  const minWR = parseFloat(document.getElementById('minWR').value)||0;
  const minSig = parseInt(document.getElementById('minSig').value)||0;
  const filtered = all.filter(t=>t.stats.close_wr>=minWR && t.stats.signals>=minSig);
  const sb = document.getElementById('sortBy').value;
  const sd = document.getElementById('sortDir').value;
  const rev = sd==='desc'?-1:1;
  filtered.sort((a,b)=>{
    const va = a[sb]||a.stats[sb]||0, vb = b[sb]||b.stats[sb]||0;
    return (va-vb)*rev;
  });
  grid.innerHTML = filtered.map(t=>{
    const sel = selected===t.symbol?' sel':'';
    const s = t.stats;
    return `<div class="card${sel}" onclick="selectTicker('${t.symbol}')">
      <h3><span class="sym">${t.symbol}</span> — ${t.name}</h3>
      <div class="stat">Signals: <b>${s.signals}</b> · R-WR: <b class="wr ${wrClass(s.real_wr)}">${s.real_wr}%</b> · T-WR: <b>${s.touch_wr}%</b> · C-WR: <b>${s.close_wr}%</b></div>
      <div class="stat">TP${s.tp_count}/SL${s.sl_count}/Exp${s.expiry_count} · PF ${s.real_pf} · DD ${s.real_max_dd}% · Σ ${fmtPnl(s.real_total_pnl)}%</div>
    </div>`;
  }).join('');
}

function sortGrid(){
  if(!DATA)return;
  renderGrid(DATA);
  if(selected) showDetail(selected);
}

function selectTicker(sym){
  selected = sym;
  renderGrid(DATA);
  showDetail(sym);
}

function showDetail(sym){
  const t = DATA.find(x=>x.symbol===sym);
  if(!t)return;
  document.getElementById('detail-div').style.display='block';
  document.getElementById('detail-title').textContent = t.symbol+' — '+t.name;
  
  const s = t.stats;
  document.getElementById('detail-stats').innerHTML = [
    ['Signals', s.signals, ''],
    ['R-Wins', s.real_wins, ''],
    ['R-WR', s.real_wr+'%', wrClass(s.real_wr)],
    ['T-WR', s.touch_wr+'%', ''],
    ['C-WR', s.close_wr+'%', ''],
    ['Avg Ret', fmtPnl(s.real_avg_ret)+'%', pnlClass(s.real_avg_ret)],
    ['ΣPnL', fmtPnl(s.real_total_pnl)+'%', pnlClass(s.real_total_pnl)],
    ['PF', s.real_pf, s.real_pf>=1.2?'pos':'neg'],
    ['DD', s.real_max_dd+'%', 'neg'],
    ['TP', s.tp_count, s.tp_count>s.sl_count?'pos':'neg'],
    ['SL', s.sl_count, 'neg'],
    ['Exp', s.expiry_count, ''],
  ].map(x=>`<div class="stat-box"><div class="label">${x[0]}</div><div class="val ${x[2]}">${x[1]}</div></div>`).join('');
  
  // Analysis text
  if(t.analysis){
    const ac = document.getElementById('analysis-card');
    ac.className = 'analysis-card ' + t.analysis.verdict;
    document.getElementById('analysis-short').textContent = t.analysis.short;
    document.getElementById('analysis-detail').textContent = t.analysis.detail;
  }
  
  // Price chart
  drawPriceChart(t);
  drawEquityChart(t);
  renderTradeTable(t);
}

function downsample(arr, n){
  if(!arr||arr.length<=n)return arr||[];
  const step = Math.floor(arr.length/n);
  const res = [];
  for(let i=0;i<arr.length;i+=step)res.push(arr[i]);
  if(res[res.length-1]!==arr[arr.length-1])res.push(arr[arr.length-1]);
  return res;
}

function drawPriceChart(t){
  const canvas = document.getElementById('main-chart');
  const rect = canvas.parentElement.getBoundingClientRect();
  canvas.width=Math.min(rect.width-2,1200);
  canvas.height=400;
  const ctx=canvas.getContext('2d');
  const W=canvas.width,H=canvas.height,PAD={t:30,b:30,l:60,r:20};
  
  const prices = t.prices;
  const sigs = t.sigs;
  const closes = prices.map(p=>p.close);
  const maxP = Math.max(...closes)*1.02;
  const minP = Math.min(...closes)*0.98;
  const rangeP = maxP-minP;
  const xStep = (W-PAD.l-PAD.r)/Math.max(prices.length-1,1);
  
  function x(i){return PAD.l+i*xStep}
  function y(v){return PAD.t+(1-(v-minP)/rangeP)*(H-PAD.t-PAD.b)}
  
  // Grid
  ctx.strokeStyle='#21262d'; ctx.lineWidth=1;
  for(let i=0;i<=4;i++){
    const yy = PAD.t+(H-PAD.t-PAD.b)*i/4;
    ctx.beginPath();ctx.moveTo(PAD.l,yy);ctx.lineTo(W-PAD.r,yy);ctx.stroke();
    const val = maxP-(maxP-minP)*i/4;
    ctx.fillStyle='#8b949e';ctx.font='11px Courier New';
    ctx.textAlign='right';ctx.fillText(val.toFixed(val>100?0:2),PAD.l-5,yy+4);
  }
  
  // Price line
  ctx.strokeStyle='#c9d1d9'; ctx.lineWidth=1;
  ctx.beginPath();
  for(let i=0;i<prices.length;i++){
    i===0?ctx.moveTo(x(i),y(prices[i].close)):ctx.lineTo(x(i),y(prices[i].close));
  }
  ctx.stroke();
  
  // Signals
  const sigMap = {};
  sigs.forEach(s=>{sigMap[s.time]=s});
  for(let i=0;i<prices.length;i++){
    const s = sigMap[prices[i].time];
    if(!s)continue;
    const cx=x(i),cy=y(prices[i].close);
    const sz = 8;
    ctx.fillStyle=s.win?'rgba(63,185,80,0.8)':'rgba(248,81,73,0.8)';
    ctx.strokeStyle='#fff';ctx.lineWidth=1;
    ctx.beginPath();
    if(s.dir==='LONG'){
      ctx.moveTo(cx,cy-sz);ctx.lineTo(cx-sz,cy+sz);ctx.lineTo(cx+sz,cy+sz);ctx.closePath();
    } else {
      ctx.moveTo(cx,cy+sz);ctx.lineTo(cx-sz,cy-sz);ctx.lineTo(cx+sz,cy-sz);ctx.closePath();
    }
    ctx.fill();ctx.stroke();
  }
  
  // Labels
  ctx.fillStyle='#8b949e';ctx.font='11px Courier New';ctx.textAlign='center';
  const nLabels = Math.min(8, prices.length);
  const step = Math.max(1,Math.floor(prices.length/nLabels));
  for(let i=0;i<prices.length;i+=step){
    ctx.fillText(prices[i].time, x(i), H-5);
  }
}

function drawEquityChart(t){
  const canvas = document.getElementById('eq-chart');
  const rect = canvas.parentElement.getBoundingClientRect();
  canvas.width=Math.min(rect.width-2,1200);
  canvas.height=200;
  const ctx=canvas.getContext('2d');
  const W=canvas.width,H=canvas.height,PAD={t:20,b:25,l:60,r:20};
  
  const eqReal = t.equity.real||[];
  const eqTouch = t.equity.touch||[];
  const eqClose = t.equity.close||[];
  if(eqReal.length<2){ctx.fillStyle='#8b949e';ctx.font='12px Courier New';ctx.textAlign='center';ctx.fillText('No equity data',W/2,H/2);return}
  
  const valsR = eqReal.map(e=>e.equity);
  const valsT = eqTouch.map(e=>e.equity);
  const valsC = eqClose.map(e=>e.equity);
  const allV = [...valsR,...valsT,...valsC,0];
  const maxV = Math.max(...allV)*1.1;
  const minV = Math.min(...allV)*1.1;
  const rangeV = maxV-minV||1;
  const xStep = (W-PAD.l-PAD.r)/Math.max(valsR.length-1,1);
  
  function x(i){return PAD.l+i*xStep}
  function y(v){return PAD.t+(1-(v-minV)/rangeV)*(H-PAD.t-PAD.b)}
  
  // Zero line
  const zy = y(0);
  ctx.strokeStyle='#30363d';ctx.lineWidth=1;ctx.setLineDash([4,4]);
  ctx.beginPath();ctx.moveTo(PAD.l,zy);ctx.lineTo(W-PAD.r,zy);ctx.stroke();ctx.setLineDash([]);
  
  // Touch (ghost)
  if(valsT.length>=2){
    ctx.strokeStyle='rgba(88,166,255,0.2)';ctx.lineWidth=1;
    ctx.beginPath();for(let i=0;i<valsT.length;i++){i===0?ctx.moveTo(x(i),y(valsT[i])):ctx.lineTo(x(i),y(valsT[i]));}
    ctx.stroke();
  }
  // Close (ghost)
  if(valsC.length>=2){
    ctx.strokeStyle='rgba(210,153,34,0.2)';ctx.lineWidth=1;
    ctx.beginPath();for(let i=0;i<valsC.length;i++){i===0?ctx.moveTo(x(i),y(valsC[i])):ctx.lineTo(x(i),y(valsC[i]));}
    ctx.stroke();
  }
  
  // Realistic fill
  const lastR = valsR[valsR.length-1];
  ctx.beginPath();
  ctx.moveTo(PAD.l,zy);
  for(let i=0;i<valsR.length;i++){i===0?ctx.lineTo(x(i),y(valsR[i])):ctx.lineTo(x(i),y(valsR[i]));}
  ctx.lineTo(PAD.l+(valsR.length-1)*xStep,zy);ctx.closePath();
  const grad = ctx.createLinearGradient(0,PAD.t,0,H-PAD.b);
  grad.addColorStop(0,lastR>=0?'rgba(63,185,80,0.25)':'rgba(248,81,73,0.25)');
  grad.addColorStop(1,lastR>=0?'rgba(63,185,80,0.02)':'rgba(248,81,73,0.02)');
  ctx.fillStyle=grad;ctx.fill();
  
  // Realistic line
  ctx.strokeStyle=lastR>=0?'#3fb950':'#f85149';ctx.lineWidth=2;
  ctx.beginPath();for(let i=0;i<valsR.length;i++){i===0?ctx.moveTo(x(i),y(valsR[i])):ctx.lineTo(x(i),y(valsR[i]));}
  ctx.stroke();
  
  // Legend
  ctx.fillStyle='rgba(88,166,255,0.4)';ctx.font='9px Courier New';ctx.textAlign='left';
  ctx.fillText('touch',W-PAD.r-120,PAD.t+10);
  ctx.fillStyle='rgba(210,153,34,0.4)';ctx.fillText('close',W-PAD.r-120,PAD.t+22);
  ctx.fillStyle=lastR>=0?'rgba(63,185,80,0.7)':'rgba(248,81,73,0.7)';ctx.fillText('real',W-PAD.r-120,PAD.t+34);
  
  // Y axis
  ctx.fillStyle='#8b949e';ctx.font='10px Courier New';ctx.textAlign='right';
  for(let i=0;i<=3;i++){const yy=PAD.t+(H-PAD.t-PAD.b)*i/3;ctx.fillText((maxV-(maxV-minV)*i/3).toFixed(1)+'%',PAD.l-5,yy+3);}
  // Date labels
  ctx.textAlign='center';
  const lStep=Math.max(1,Math.floor(valsR.length/6));
  for(let i=0;i<valsR.length;i+=lStep){const d=eqReal[i].date;if(d)ctx.fillText(d.substring(5),x(i),H-3);}
}

function renderTradeTable(t){
  const div = document.getElementById('detail-table');
  const sigs = t.sigs;
  if(!sigs||!sigs.length){div.innerHTML='<p>No signals</p>';return}
  
  let html = '<table class="sig-table"><tr><th>Date</th><th>Dir</th><th>Entry</th><th>R-Ret%</th><th>Exit</th><th>T-Ret%</th><th>C-Ret%</th></tr>';
  sigs.forEach(s=>{
    const cls = s.real_win?'w':'l';
    const arrow = s.dir==='LONG'?'▲':'▼';
    const reason = s.real_reason||'';
    const entryVal = typeof s.entry==='number'?s.entry.toFixed(s.entry>100?0:4):s.entry;
    html += `<tr class="${cls}"><td>${s.time}</td><td>${arrow}</td><td>${entryVal}</td><td>${s.real_ret.toFixed(2)}%</td><td>${reason}</td><td>${(s.dir==='LONG'?'+':'')+s.ret_touch.toFixed(2)}%</td><td>${(s.dir==='LONG'?'+':'')+s.ret_close.toFixed(2)}%</td></tr>`;
  });
  html += '</table>';
  div.innerHTML = html;
}

// Summary table
function renderSummaryTable(all){
  const div = document.getElementById('summary-div');
  const cols = ['sym','name','signals','real_wr','real_total_pnl','real_pf','real_max_dd','real_avg_ret','touch_wr','close_wr'];
  const colLabels = ['Sym','Name','Sig','R-WR%','ΣPnL%','PF','DD%','Avg%','T-WR%','C-WR%'];
  
  const render = (data)=>{
    let h = '<table class="summary-table"><tr>';
    const cd = sortDir==='desc'?' desc':'';
    cols.forEach((c,i)=>{
      h += `<th class="${sortCol===c?'sorted'+cd:''}" onclick="sortTable('${c}')">${colLabels[i]}</th>`;
    });
    h += '</tr>';
    data.forEach(t=>{
      const s=t.stats;
      h += `<tr onclick="selectTicker('${t.symbol}')" style="cursor:pointer">
        <td style="color:#58a6ff;font-weight:bold">${t.symbol}</td>
        <td style="color:#8b949e;font-size:0.85em">${t.name}</td>
        <td>${s.signals}</td>
        <td class="wr ${wrClass(s.real_wr)}">${s.real_wr}%</td>
        <td class="pnl ${pnlClass(s.real_total_pnl)}">${fmtPnl(s.real_total_pnl)}%</td>
        <td>${s.real_pf}</td>
        <td style="color:#f85149">${s.real_max_dd}%</td>
        <td class="pnl ${pnlClass(s.real_avg_ret)}">${fmtPnl(s.real_avg_ret)}%</td>
        <td class="wr ${wrClass(s.touch_wr)}">${s.touch_wr}%</td>
        <td class="wr ${wrClass(s.close_wr)}">${s.close_wr}%</td>
      </tr>`;
    });
    h += '</table>';
    div.innerHTML = h;
  };
  
  window.sortTable = function(col){
    if(sortCol===col)sortDir=sortDir==='desc'?'asc':'desc';
    else{sortCol=col;sortDir='desc'}
    const rev = sortDir==='desc'?-1:1;
    const sorted = [...DATA].sort((a,b)=>{
      const va = a.stats[col]!==undefined?a.stats[col]:a[col]||0;
      const vb = b.stats[col]!==undefined?b.stats[col]:b[col]||0;
      return (va-vb)*rev;
    });
    render(sorted);
    if(selected)showDetail(selected);
  };
  
  render(DATA);
}

// Init
document.addEventListener('DOMContentLoaded', ()=>{
  renderSummary(DATA);
  renderSummaryTable(DATA);
  renderGrid(DATA);
  if(DATA.length>0)selectTicker(DATA[0].symbol);
});
</script>
</body></html>"""

class Handler(http.server.BaseHTTPRequestHandler):
    data = None
    
    def do_GET(self):
        if self.path == '/':
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            html = HTML.replace('__DATA__', json.dumps(self.data, default=str))
            self.wfile.write(html.encode('utf-8'))
        elif self.path == '/api/data':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.end_headers()
            self.wfile.write(json.dumps(self.data, default=str).encode('utf-8'))
        else:
            self.send_response(404)
            self.end_headers()
    
    def log_message(self, fmt, *args):
        pass

def main():
    print("Loading MOEX data...", flush=True)
    data = load_all()
    print(f"Loaded {len(data)} tickers:", flush=True)
    for d in data[:10]:
        s = d['stats']
        print(f"  {d['symbol']:4s} {s['signals']:4d} sig  R-WR {s['real_wr']:5.1f}%  GO-PnL {s['go_total_pnl']:+7.1f}%  Not-PnL {s['real_total_pnl']:+7.1f}%  PF {s['real_pf']:.2f}  {d['lev']:.1f}x  TP/SL/Exp {s['tp_count']}/{s['sl_count']}/{s['expiry_count']}", flush=True)
    if len(data) > 10:
        print(f"  ... and {len(data)-10} more", flush=True)
    
    Handler.data = data
    
    server = http.server.ThreadingHTTPServer(('0.0.0.0', PORT), Handler, bind_and_activate=False)
    server.allow_reuse_address = True
    server.server_bind()
    server.server_activate()
    print(f"\nDashboard: http://10.0.0.60:{PORT}/", flush=True)
    print(f"           http://localhost:{PORT}/", flush=True)
    server.serve_forever()

if __name__ == '__main__':
    main()
