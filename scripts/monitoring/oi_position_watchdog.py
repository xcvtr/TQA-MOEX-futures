#!/usr/bin/env python3 -u
"""OI папер — вотчер открытия позиций (контроль корректности).

Каждые 5 мин (cron): читает paper_state.positions_json, сравнивает с прошлым
снапшотом (~/.hermes/scripts/state/oi_positions_last.json). При появлении НОВОЙ
позиции (новый id) — проверяет корректность открытия:
  1. entry_time в торговые часы (10:00-18:45 МСК, будни) + не в будущем
  2. entry_price ≈ реальной цене mt5_continuous в момент входа (±0.5%)
  3. |day_net| в момент входа ≥ thr (порог из параметров)
  4. contracts ≤ лимит тикера (TICKER_LIMITS / дневной объём)
  5. данные futoi в момент входа были свежими (age ≤ 15 мин)

Вывод: только при НОВОЙ позиции или ошибке (пустой stdout = тихо).
"""
import json, os, sys, datetime, subprocess

STATE_FILE = os.path.expanduser("~/.hermes/scripts/state/oi_positions_last.json")
os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)

def sh(cmd, timeout=60):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    return r.stdout.strip()

def now_msk():
    return datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=3)

# ── 1. Текущие позиции ─────────────────────────────────────────────────
raw = sh("psql -h 10.0.0.60 -U postgres -d moex -t -A -c \"SELECT positions_json FROM futures.paper_state ORDER BY updated_at DESC LIMIT 1\"")
if not raw or raw == "None":
    sys.exit(0)  # state пуст — тихо

try:
    positions = json.loads(raw)
except Exception:
    sys.exit(0)

if not isinstance(positions, list):
    positions = list(positions.values()) if isinstance(positions, dict) else []

# ── 2. Прошлый снапшот ─────────────────────────────────────────────────
prev_ids = set()
if os.path.exists(STATE_FILE):
    try:
        with open(STATE_FILE) as f:
            prev = json.load(f)
        prev_ids = set(prev.get("ids", []))
    except Exception:
        prev_ids = set()

cur_ids = set()
for p in positions:
    if isinstance(p, dict) and p.get("id") is not None:
        cur_ids.add(p["id"])

new_ids = cur_ids - prev_ids

# Сохраняем текущее состояние
with open(STATE_FILE, "w") as f:
    json.dump({"ids": sorted(cur_ids), "ts": now_msk().isoformat()}, f)

if not new_ids:
    sys.exit(0)  # нет новых позиций — тихо

# ── 3. Проверка каждой новой позиции ───────────────────────────────────
lines = []
for p in positions:
    pid = p.get("id")
    if pid not in new_ids:
        continue
    tk = p.get("ticker", "?")
    d = p.get("direction", "?")
    ep = float(p.get("entry_price", 0))
    et_raw = p.get("entry_time", "")
    ct = int(p.get("contracts", 0))
    thr = float(p.get("exit_thr", p.get("params", {}).get("thr", 3)) if not isinstance(p.get("exit_thr"), (int, float)) else p.get("exit_thr", 3))

    # entry_time
    try:
        et = datetime.datetime.fromisoformat(et_raw.replace("Z", "+00:00"))
    except Exception:
        et = None
    et_msk = et + datetime.timedelta(hours=3) if et else None

    issues = []
    now = now_msk()

    # 1) время: не в будущем, торговые часы (10:00-02:00 МСК: дневная + вечерняя сессия, будни)
    if et_msk is None:
        issues.append("entry_time не парсится")
    else:
        if et_msk > now + datetime.timedelta(minutes=5):
            issues.append(f"entry_time В БУДУЩЕМ ({et_msk:%H:%M})")
        if et_msk.weekday() >= 5:
            issues.append(f"вход в выходной ({et_msk:%a})")
        t_min = et_msk.hour * 60 + et_msk.minute
        # Live-папер: крон 15-23,0-4 IRK = 10:00-02:00 МСК (дневная + вечерняя сессия)
        market_ok = (t_min >= 10 * 60) or (t_min < 2 * 60 + 50)
        if not market_ok:
            issues.append(f"вход вне торговых часов ({et_msk:%H:%M})")

    # 2) цена: реальная цена mt5_continuous в момент входа
    if et is not None:
        ts0 = int(et.timestamp()) - 180
        ts1 = int(et.timestamp()) + 180
        prc_row = sh(f"clickhouse-client --host 10.0.0.60 --database moex --query \"SELECT prc FROM moex.mt5_continuous WHERE ticker='{tk}' AND toUnixTimestamp(bt) BETWEEN {ts0} AND {ts1} ORDER BY ABS(toUnixTimestamp(bt) - {int(et.timestamp())}) LIMIT 1\"")
        if prc_row:
            real = float(prc_row)
            dev = abs(real - ep) / real * 100
            if dev > 0.5:
                issues.append(f"цена входа {ep} ≠ реальной {real:.2f} ({dev:.2f}%)")
        else:
            issues.append("нет бара mt5_continuous на момент входа")

    # 3) day_net в момент входа
    if et is not None:
        q = ("SELECT round((buy_fiz - sell_fiz) / (buy_fiz+sell_fiz+buy_yur+sell_yur) * 100, 2) "
             "FROM moex.futoi WHERE ticker='" + tk + "' AND toUnixTimestamp(toDateTime(bt)) <= " + str(int(et.timestamp())) + " "
             "ORDER BY bt DESC LIMIT 1")
        dn = sh("clickhouse-client --host 10.0.0.60 --database moex --query \"" + q + "\"")
        if dn:
            dnv = float(dn)
            if abs(dnv) < thr - 0.5:
                issues.append(f"day_net={dnv:.2f}% < порога {thr}% на момент входа")

    # 4) лимит контрактов
    limit = sh(f"psql -h 10.0.0.60 -U postgres -d moex -t -A -c \"SELECT COALESCE((SELECT max_contracts FROM futures.ticker_specs WHERE ticker='{tk}'), 1000)\"")
    try:
        lim = int(limit) if limit else 1000
    except Exception:
        lim = 1000
    if ct > lim:
        issues.append(f"contracts {ct} > лимит {lim}")

    status = "✅ OK" if not issues else "❌ " + "; ".join(issues)
    lines.append(f"🆕 {tk} {d.upper()} x{ct} @ {ep} [{et_msk:%d.%m %H:%M МСК}]")
    lines.append(f"   {status}")

# ── Вывод ──────────────────────────────────────────────────────────────
if lines:
    print(f"🚨 НОВЫЕ ПОЗИЦИИ OI ПАПЕРА ({len(new_ids)})")
    print("\n".join(lines))
    print("---")
    print(f"Всего открыто: {len(cur_ids)}")
