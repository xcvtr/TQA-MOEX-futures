#!/usr/bin/env python3
"""MOEX Volume Climax Equity Dashboard — Canvas-based, no CDN."""

import os, sys, json, math, http.server, socket
from datetime import datetime, timedelta, timezone
import psycopg2
import numpy as np

DB = dict(host="10.0.0.60", port=5432, dbname="moex", user="postgres", password=os.environ.get("MOEX_DB_PASSWORD", "***"))
PORT = 5056

# --- Champion tickers from H4 ranking (WR >= 53%) ---
CHAMPIONS = [
    ("CH", "Cocoa"), ("W4", "Wheat"), ("OJ", "Orange Juice"),
    ("DX", "Dollar Index"), ("BM", "Butter"), ("BR", "Brent"),
    ("NR", "Natural Rubber"), ("SV", "Silver"), ("SS", "Sugar"),
    ("IB", "I-Bonds"), ("NG", "Natural Gas"), ("CC", "Cocoa C"),
    ("SN", "Tin"), ("GZ", "Gold Z"), ("VB", "VTB"),
    ("PD", "Palladium"), ("HY", "Hryvnia"), ("SE", "Soybean"),
    ("LK", "Lukoil"), ("GD", "Gold"), ("RI", "RTS Index"),
    ("GL", "Gold L"), ("SR", "Sberbank"), ("NM", "Norilsk"),
]

H4_WINDOW = 20  # rolling median window
TARGET_BARS = 4  # forward look

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
    """Volume Climax strategy signals."""
    sigs = []
    for i, d in enumerate(data):
        if d["vol_ratio"] <= 2 or d["range_pct"] <= d.get("avg_range_pct", 0):
            continue
        
        # Bullish: sell-climax (red bar, close near low)
        is_red = d["close"] < d["open"]
        is_bear_climax = is_red and d["close_pos"] <= 0.35
        # Bearish: buy-climax (green bar, close near high)
        is_green = d["close"] > d["open"]
        is_bull_climax = is_green and d["close_pos"] >= 0.65
        
        if not is_bear_climax and not is_bull_climax:
            continue
        
        # Forward check: best/worst of next TARGET_BARS
        future = data[i+1:i+1+TARGET_BARS]
        if len(future) < TARGET_BARS:
            continue
        
        if is_bear_climax:
            # Expect price to go UP
            best_fwd = max(f["high"] for f in future)
            entry = d["close"]
            win = best_fwd >= entry * 1.002
            ret_pct = (best_fwd - entry) / entry * 100
            sigs.append({
                "time": str(d["time"].date()), "dir": "LONG",
                "entry": entry, "target": best_fwd,
                "ret_pct": round(ret_pct, 2), "win": win,
                "vol_ratio": round(d["vol_ratio"], 1),
                "range_pct": round(d["range_pct"], 2),
                "close_pos": round(d["close_pos"], 2),
            })
        elif is_bull_climax:
            # Expect price to go DOWN
            worst_fwd = min(f["low"] for f in future)
            entry = d["close"]
            win = worst_fwd <= entry * 0.998
            ret_pct = (worst_fwd - entry) / entry * 100
            sigs.append({
                "time": str(d["time"].date()), "dir": "SHORT",
                "entry": entry, "target": worst_fwd,
                "ret_pct": round(ret_pct, 2), "win": win,
                "vol_ratio": round(d["vol_ratio"], 1),
                "range_pct": round(d["range_pct"], 2),
                "close_pos": round(d["close_pos"], 2),
            })
    return sigs

def compute_equity(sigs):
    """Compute cumulative equity curve from signals."""
    if not sigs:
        return []
    cum = 0
    curve = []
    for s in sigs:
        ret = s["ret_pct"] / 100.0
        if s["dir"] == "LONG":
            cum += ret
        else:
            cum += -ret  # short: positive ret_pct means price went down
        curve.append({"date": s["time"], "equity": round(cum * 100, 2)})
    return curve

def compute_stats(sigs):
    """Compute trading stats."""
    if not sigs:
        return {"signals": 0, "wins": 0, "wr": 0, "avg_ret": 0, "total_pnl": 0,
                "profit_factor": 0, "max_drawdown": 0, "long_wr": 0, "short_wr": 0}
    
    wins = sum(1 for s in sigs if s["win"])
    losses = len(sigs) - wins
    wr = wins / len(sigs) * 100
    
    # PnL
    pnls = []
    for s in sigs:
        pnl = s["ret_pct"] if s["dir"] == "LONG" else -s["ret_pct"]
        pnls.append(pnl)
    total_pnl = sum(pnls)
    avg_ret = np.mean(pnls) if pnls else 0
    
    gross_profit = sum(p for p in pnls if p > 0)
    gross_loss = abs(sum(p for p in pnls if p < 0))
    profit_factor = gross_profit / max(gross_loss, 0.001)
    
    # Drawdown
    cum = np.cumsum(pnls) if pnls else [0]
    peak = np.maximum.accumulate(cum) if len(cum) > 0 else [0]
    dd = cum - peak
    max_dd = min(dd) if len(dd) > 0 else 0
    
    # Long/short WR
    long_sigs = [s for s in sigs if s["dir"] == "LONG"]
    short_sigs = [s for s in sigs if s["dir"] == "SHORT"]
    long_wr = sum(1 for s in long_sigs if s["win"]) / max(len(long_sigs), 1) * 100
    short_wr = sum(1 for s in short_sigs if s["win"]) / max(len(short_sigs), 1) * 100
    
    return {
        "signals": len(sigs), "wins": wins, "losses": losses,
        "wr": round(wr, 1), "avg_ret": round(avg_ret, 2),
        "total_pnl": round(total_pnl, 2),
        "profit_factor": round(profit_factor, 2),
        "max_drawdown": round(max_dd, 2),
        "long_wr": round(long_wr, 1),
        "short_wr": round(short_wr, 1),
        "long_count": len(long_sigs),
        "short_count": len(short_sigs),
    }

def generate_analysis(stats, sigs):
    """Generate human-readable analysis text for a ticker."""
    if not sigs or stats["signals"] == 0:
        return {"verdict": "neutral", "short": "Недостаточно сигналов.", "detail": ""}
    
    wr = stats["wr"]
    pf = stats["profit_factor"]
    dd = stats["max_drawdown"]
    avg = stats["avg_ret"]
    total = stats["total_pnl"]
    lw = stats["long_wr"]
    sw = stats["short_wr"]
    lc = stats["long_count"]
    sc = stats["short_count"]
    n = stats["signals"]
    
    # Verdict
    if wr >= 80:
        verdict = "strong_buy"
        verdict_label = "🟢 Сильный сигнал"
    elif wr >= 65:
        verdict = "buy"
        verdict_label = "🟡 Позитивный"
    elif wr >= 50:
        verdict = "neutral"
        verdict_label = "⚪ Нейтральный"
    else:
        verdict = "sell"
        verdict_label = "🔴 Слабый"
    
    # Short description
    short_parts = []
    
    if avg > 1.5:
        short_parts.append(f"средняя доходность {avg:+.1f}% на сделку")
    elif avg > 0.5:
        short_parts.append(f"средняя доходность {avg:+.1f}%")
    else:
        short_parts.append(f"средняя доходность {avg:+.1f}%")
    
    short_parts.append(f"{wr:.0f}% побед ({n} сигналов)")
    
    if pf >= 3:
        short_parts.append(f"PF {pf:.0f}")
    elif pf >= 1.5:
        short_parts.append(f"PF {pf:.1f}")
    
    if dd < -5:
        short_parts.append(f"просадка {dd:.1f}%")
    
    short = f"{verdict_label} · {' · '.join(short_parts)}"
    
    # Detail analysis
    lines = []
    
    # Long vs Short
    if lc >= 5 and sc >= 5:
        diff = lw - sw
        if abs(diff) >= 15:
            better = "лонги" if diff > 0 else "шорты"
            lines.append(f"Асимметрия: {better} значительно сильнее (лонги {lw:.0f}% vs шорты {sw:.0f}%). "
                        f"Рекомендуется фильтровать сигналы в пользу {better}.")
        elif abs(diff) >= 5:
            better = "лонги" if diff > 0 else "шорты"
            lines.append(f"Небольшое преимущество {better}: {lw:.0f}% vs {sw:.0f}%.")
        else:
            lines.append(f"Симметричная стратегия: лонги {lw:.0f}% ({lc} сигн.) и шорты {sw:.0f}% ({sc} сигн.) — примерно равны.")
    elif lc >= 5:
        lines.append(f"Доминируют лонги ({lc} сигн., {lw:.0f}% WR). Шортов недостаточно для анализа ({sc}).")
    elif sc >= 5:
        lines.append(f"Доминируют шорты ({sc} сигн., {sw:.0f}% WR). Лонгов недостаточно ({lc}).")
    
    # Drawdown
    if dd < -20:
        lines.append(f"⚠️ Высокая просадка {dd:.1f}% — стратегия может быть рискованной. "
                    f"Рекомендуется стоп-лосс или уменьшение размера позиции.")
    elif dd < -5:
        lines.append(f"Просадка {dd:.1f}% — умеренная, контролируемая.")
    else:
        lines.append(f"Минимальная просадка {dd:.1f}% — отличная стабильность.")
    
    # Profit factor
    if pf >= 10:
        lines.append(f"Исключительный PF {pf:.0f}: на каждый рубль убытка приходится {pf:.0f} прибыли. "
                    f"Стратегия крайне устойчива.")
    elif pf >= 3:
        lines.append(f"Хороший PF {pf:.1f}: убыточные сделки не перевешивают прибыльные.")
    elif pf >= 1.5:
        lines.append(f"Приемлемый PF {pf:.1f} — работает, но запас прочности невелик.")
    else:
        lines.append(f"Слабый PF {pf:.2f}: убытки почти равны прибыли. Нужна калибровка.")
    
    # Signal density
    period_days = 365 * 2  # approximate since 2024
    sig_per_month = n / max(period_days / 30, 1)
    if sig_per_month >= 15:
        lines.append(f"Высокая частота сигналов (~{sig_per_month:.0f}/мес). Достаточно для активной торговли.")
    elif sig_per_month >= 5:
        lines.append(f"Умеренная частота (~{sig_per_month:.0f}/мес). ")
    else:
        lines.append(f"Низкая частота (~{sig_per_month:.0f}/мес). Требуется терпение.")
    
    # Total PnL
    if total > 200:
        lines.append(f"Суммарная доходность {total:+.0f}% — выдающийся результат на исторических данных.")
    elif total > 50:
        lines.append(f"Суммарная доходность {total:+.0f}% — хороший результат.")
    
    # Win rate quality
    if wr >= 90:
        lines.append(f"WR {wr:.0f}% — экстра-класс. Система практически не ошибается на этом таймфрейме.")
    elif wr >= 75:
        lines.append(f"WR {wr:.0f}% — отличный показатель. Стратегия уверенно предсказывает движение.")
    elif wr >= 60:
        lines.append(f"WR {wr:.0f}% — уверенный результат выше случайного.")
    
    # Signal direction imbalance
    if lc > 0 and sc > 0:
        ratio = max(lc, sc) / min(lc, sc) if min(lc, sc) > 0 else 0
        if ratio >= 3:
            dominant = "лонговые" if lc > sc else "короткие"
            lines.append(f"Дисбаланс направлений: {dominant} сигналов в {ratio:.0f}x больше. "
                        f"Проверь, нет ли систематического смещения в Volume Climax на этом инструменте.")
    
    # Recommendation
    if wr >= 75 and pf >= 2 and dd > -10:
        lines.append(f"\n💡 Рекомендация: сильный кандидат для торговли. "
                    f"Использовать Volume Climax как основной вход с фильтром по направлению.")
    elif wr >= 60 and pf >= 1.5:
        lines.append(f"\n💡 Рекомендация: можно торговать, но с осторожностью. "
                    f"Добавить дополнительный фильтр (тренд, OI).")
    else:
        lines.append(f"\n💡 Рекомендация: только в комбинации с другими сигналами. Самостоятельно недостаточно надёжен.")
    
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
    }

def load_all():
    """Process all champion tickers."""
    results = []
    for sym, name in CHAMPIONS:
        r = process_ticker(sym, name)
        if r and r["stats"]["signals"] >= 10:  # minimum signal threshold
            results.append(r)
    # Sort by WR descending
    results.sort(key=lambda x: x["stats"]["wr"], reverse=True)
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
@media(max-width:900px){
  .dash-grid{grid-template-columns:repeat(auto-fill,minmax(200px,1fr))}
  .stats-row{gap:8px}
  .stat-box{padding:4px 8px;min-width:60px}
}
</style></head><body>
<h1>📊 MOEX Volume Climax — Equity Dashboard</h1>
<p class="sub" id="sub-info">Loading data...</p>

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
  <input type="number" id="minWR" value="50" min="0" max="100" onchange="sortGrid()">
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

<script>
// DATA will be injected here
const DATA = __DATA__;

function fmtPnl(v){return v>0?'+'+v.toFixed(1):v.toFixed(1)}
function wrClass(w){return w>=65?'high':w>=53?'mid':'low'}
function pnlClass(v){return v>=0?'pos':'neg'}

let selected = null;
let sortCol = 'wr';
let sortDir = 'desc';

function renderSummary(all){
  const totalSig = all.reduce((s,t)=>s+t.stats.signals,0);
  const totalW = all.reduce((s,t)=>s+t.stats.wins,0);
  const totalWR = totalSig>0?(totalW/totalSig*100).toFixed(1):'0';
  const totalPnL = all.reduce((s,t)=>s+t.stats.total_pnl,0);
  const wr50 = all.filter(t=>t.stats.wr>=50).length;
  document.getElementById('sub-info').textContent =
    `${all.length} tickers · ${totalSig} signals · ${totalWR}% WR · ${fmtPnl(totalPnL)}% ΣPnL · ${wr50} wr≥50%`;
}

function renderGrid(all){
  const grid = document.getElementById('grid-div');
  const minWR = parseFloat(document.getElementById('minWR').value)||0;
  const minSig = parseInt(document.getElementById('minSig').value)||0;
  const filtered = all.filter(t=>t.stats.wr>=minWR && t.stats.signals>=minSig);
  const sb = document.getElementById('sortBy').value;
  const sd = document.getElementById('sortDir').value;
  const rev = sd==='desc'?-1:1;
  filtered.sort((a,b)=>{
    const va = a.stats[sb], vb = b.stats[sb];
    return (va-vb)*rev;
  });
  grid.innerHTML = filtered.map(t=>{
    const sel = selected===t.symbol?' sel':'';
    return `<div class="card${sel}" onclick="selectTicker('${t.symbol}')">
      <h3><span class="sym">${t.symbol}</span> — ${t.name}</h3>
      <div class="stat">Signals: <b>${t.stats.signals}</b> · WR: <b class="wr ${wrClass(t.stats.wr)}">${t.stats.wr}%</b> · ΣPnL: <b class="pnl ${pnlClass(t.stats.total_pnl)}">${fmtPnl(t.stats.total_pnl)}%</b></div>
      <div class="stat">L: ${t.stats.long_wr}%(${t.stats.long_count}) · S: ${t.stats.short_wr}%(${t.stats.short_count}) · PF: ${t.stats.profit_factor} · DD: ${t.stats.max_drawdown}%</div>
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
    ['Wins', s.wins, ''],
    ['WR', s.wr+'%', wrClass(s.wr)],
    ['Avg Ret', fmtPnl(s.avg_ret)+'%', pnlClass(s.avg_ret)],
    ['ΣPnL', fmtPnl(s.total_pnl)+'%', pnlClass(s.total_pnl)],
    ['PF', s.profit_factor, s.profit_factor>=1.5?'pos':'neg'],
    ['Max DD', s.max_drawdown+'%', 'neg'],
    ['L WR', s.long_wr+'%', wrClass(s.long_wr)],
    ['S WR', s.short_wr+'%', wrClass(s.short_wr)],
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
  
  const eq = t.equity;
  if(!eq||eq.length<2){ctx.fillStyle='#8b949e';ctx.font='12px Courier New';ctx.textAlign='center';ctx.fillText('No equity data',W/2,H/2);return}
  
  const vals = eq.map(e=>e.equity);
  const maxV = Math.max(...vals,0)*1.1;
  const minV = Math.min(...vals,0)*1.1;
  const rangeV = maxV-minV||1;
  const xStep = (W-PAD.l-PAD.r)/Math.max(vals.length-1,1);
  
  function x(i){return PAD.l+i*xStep}
  function y(v){return PAD.t+(1-(v-minV)/rangeV)*(H-PAD.t-PAD.b)}
  
  // Zero line
  const zy = y(0);
  ctx.strokeStyle='#30363d';ctx.lineWidth=1;ctx.setLineDash([4,4]);
  ctx.beginPath();ctx.moveTo(PAD.l,zy);ctx.lineTo(W-PAD.r,zy);ctx.stroke();ctx.setLineDash([]);
  
  // Fill
  ctx.beginPath();
  ctx.moveTo(PAD.l,zy);
  for(let i=0;i<vals.length;i++){
    i===0?ctx.lineTo(x(i),y(vals[i])):ctx.lineTo(x(i),y(vals[i]));
  }
  ctx.lineTo(PAD.l+(vals.length-1)*xStep,zy);ctx.closePath();
  const grad = ctx.createLinearGradient(0,PAD.t,0,H-PAD.b);
  const lastV = vals[vals.length-1];
  grad.addColorStop(0,lastV>=0?'rgba(63,185,80,0.3)':'rgba(248,81,73,0.3)');
  grad.addColorStop(1,lastV>=0?'rgba(63,185,80,0.02)':'rgba(248,81,73,0.02)');
  ctx.fillStyle=grad;ctx.fill();
  
  // Line
  ctx.strokeStyle=lastV>=0?'#3fb950':'#f85149';ctx.lineWidth=2;
  ctx.beginPath();
  for(let i=0;i<vals.length;i++){
    i===0?ctx.moveTo(x(i),y(vals[i])):ctx.lineTo(x(i),y(vals[i]));
  }
  ctx.stroke();
  
  // Y axis
  ctx.fillStyle='#8b949e';ctx.font='10px Courier New';ctx.textAlign='right';
  for(let i=0;i<=3;i++){
    const yy = PAD.t+(H-PAD.t-PAD.b)*i/3;
    const v = maxV-(maxV-minV)*i/3;
    ctx.fillText(v.toFixed(1)+'%',PAD.l-5,yy+3);
  }
  
  // Date labels
  ctx.textAlign='center';
  const nLabels = Math.min(6, vals.length);
  const lStep = Math.max(1,Math.floor(vals.length/nLabels));
  for(let i=0;i<vals.length;i+=lStep){
    const d = eq[i].date;
    if(d)ctx.fillText(d.substring(5), x(i), H-3);
  }
}

function renderTradeTable(t){
  const div = document.getElementById('detail-table');
  const sigs = t.sigs;
  if(!sigs||!sigs.length){div.innerHTML='<p>No signals</p>';return}
  
  let html = '<table class="sig-table"><tr><th>Date</th><th>Dir</th><th>Entry</th><th>Ret%</th><th>VolR</th><th>Range%</th></tr>';
  sigs.forEach(s=>{
    const cls = s.win?'w':'l';
    const arrow = s.dir==='LONG'?'▲':'▼';
    const icon = s.win?'✅':'❌';
    html += `<tr class="${cls}"><td>${s.time}</td><td>${arrow} ${s.dir}</td><td>${s.entry.toFixed(s.entry>100?0:4)}</td><td>${(s.dir==='LONG'?'+':'')+s.ret_pct.toFixed(2)}%</td><td>${s.vol_ratio}</td><td>${s.range_pct}%</td></tr>`;
  });
  html += '</table>';
  div.innerHTML = html;
}

// Summary table
function renderSummaryTable(all){
  const div = document.getElementById('summary-div');
  const cols = ['sym','name','signals','wr','total_pnl','profit_factor','max_drawdown','avg_ret','long_wr','short_wr'];
  const colLabels = ['Sym','Name','Sig','WR%','ΣPnL%','PF','DD%','Avg%','L%','S%'];
  
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
        <td class="wr ${wrClass(s.wr)}">${s.wr}%</td>
        <td class="pnl ${pnlClass(s.total_pnl)}">${fmtPnl(s.total_pnl)}%</td>
        <td>${s.profit_factor}</td>
        <td style="color:#f85149">${s.max_drawdown}%</td>
        <td class="pnl ${pnlClass(s.avg_ret)}">${fmtPnl(s.avg_ret)}%</td>
        <td>${s.long_wr}%</td>
        <td>${s.short_wr}%</td>
      </tr>`;
    });
    h += '</table>';
    div.innerHTML = h;
  };
  
  window.sortTable = function(col){
    if(sortCol===col)sortDir=sortDir==='desc'?'asc':'desc';
    else{sortCol=col;sortDir='desc'}
    const rev = sortDir==='desc'?-1:1;
    const sorted = [...DATA].sort((a,b)=>((a.stats[col]||0)-(b.stats[col]||0))*rev);
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
        print(f"  {d['symbol']:4s} {s['signals']:4d} sig  WR {s['wr']:5.1f}%  ΣPnL {s['total_pnl']:+.1f}%  PF {s['profit_factor']:.2f}", flush=True)
    if len(data) > 10:
        print(f"  ... and {len(data)-10} more", flush=True)
    
    Handler.data = data
    
    server = http.server.ThreadingHTTPServer(('0.0.0.0', PORT), Handler)
    server.allow_reuse_address = True
    print(f"\nDashboard: http://10.0.0.60:{PORT}/", flush=True)
    print(f"           http://localhost:{PORT}/", flush=True)
    server.serve_forever()

if __name__ == '__main__':
    main()
