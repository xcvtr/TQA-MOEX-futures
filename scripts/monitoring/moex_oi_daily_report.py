#!/usr/bin/env python3 -u
"""Ежедневный отчёт по OI paper trader (TQA-MOEX-futures).
Запуск: cron 0 5 * * 1-5 (05:00 IRK, после закрытия вечерней сессии 04:50).
Вывод: компактный статус → доставляется в чат origin.
Если сегодня выходной/нет торгов — тихий вывод (одна строка).
"""
import subprocess, sys, datetime

def sh(cmd):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
    return r.stdout.strip()

# ── 1. PG state ──
state = sh("psql -h 10.0.0.60 -U postgres -d moex -t -A -c \"SELECT equity, peak FROM futures.paper_state ORDER BY updated_at DESC LIMIT 1\"")
if not state:
    print("⚠️ PG paper_state пуст — папер не запускался?")
    sys.exit(0)
eq, peak = state.split('|')
eq, peak = float(eq), float(peak)
dd = (peak - eq) / peak * 100 if peak else 0

# ── 2. Открытые позиции (из positions_json) ──
pos_count_sql = sh("psql -h 10.0.0.60 -U postgres -d moex -t -A -c \"SELECT positions_json FROM futures.paper_state ORDER BY updated_at DESC LIMIT 1\"")
try:
    import json as _j
    _pj = _j.loads(pos_count_sql) if pos_count_sql and pos_count_sql != "None" else []
    if not isinstance(_pj, list):
        _pj = list(_pj.values()) if isinstance(_pj, dict) else []
    n_pos = sum(1 for x in _pj if isinstance(x, dict) and not x.get("closed"))
except Exception:
    n_pos = 0

# ── 3. Сделки за сегодня (и вчера для сравнения) ──
today = datetime.date.today()
trades_today = sh(f"psql -h 10.0.0.60 -U postgres -d moex -t -A -c \"SELECT count(*), COALESCE(round(sum(pnl_rub)),0) FROM futures.paper_trades WHERE exit_time::date = '{today}'\"")
yesterday = today - datetime.timedelta(days=1)
trades_yes = sh(f"psql -h 10.0.0.60 -U postgres -d moex -t -A -c \"SELECT count(*), COALESCE(round(sum(pnl_rub)),0) FROM futures.paper_trades WHERE exit_time::date = '{yesterday}'\"")
try:
    nt, pt = trades_today.split('|')
except:
    nt, pt = 0, 0
try:
    ny, py = trades_yes.split('|')
except:
    ny, py = 0, 0
nt, pt, ny, py = int(nt or 0), int(pt or 0), int(ny or 0), int(py or 0)

# ── 4. Свежесть данных ──
fresh = sh("clickhouse-client --host 10.0.0.60 --database moex --query \"SELECT ticker, toDate(max(bt)) FROM moex.futoi WHERE ticker IN ('BR','NG','SV') GROUP BY ticker FORMAT TSVRaw\"")
futoi_ok = all(str(today) in line for line in fresh.splitlines()) if fresh else False

# Свежесть mt5_continuous (цены для исполнения)
fresh_mt5 = sh("clickhouse-client --host 10.0.0.60 --database moex --query \"SELECT max(toUnixTimestamp(now()) - bt) FROM (SELECT max(toUnixTimestamp(bt)) bt FROM moex.mt5_continuous WHERE ticker IN ('BR','NG','SV') GROUP BY ticker) t\"")
try:
    mt5_age_min = int(float(fresh_mt5)) // 60 if fresh_mt5 else 999
except Exception:
    mt5_age_min = 999
mt5_ok = mt5_age_min <= 15

# ── 4b. Корректность открытых позиций ──
def pos_check():
    pos_json = sh("psql -h 10.0.0.60 -U postgres -d moex -t -A -c \"SELECT positions_json FROM futures.paper_state ORDER BY updated_at DESC LIMIT 1\"")
    if not pos_json or pos_json == "None":
        return []
    try:
        import json as _json
        _pos = _json.loads(pos_json)
        if not isinstance(_pos, list):
            _pos = list(_pos.values()) if isinstance(_pos, dict) else []
        out = []
        for p in _pos:
            if not isinstance(p, dict) or p.get("closed"):
                continue
            tk = p.get("ticker", "?")
            d = p.get("direction", "?")
            ep = float(p.get("entry_price", 0))
            ct = int(p.get("contracts", 0))
            et_raw = p.get("entry_time", "")
            et = et_raw[:16].replace("T", " ")
            # entry_time в UTC → unix
            try:
                import datetime as _dt
                et_dt = _dt.datetime.fromisoformat(et_raw.replace("Z", "+00:00"))
                et_ts = int(et_dt.timestamp())
            except Exception:
                et_ts = 0
            q = ("SELECT round(abs(prc - %s)/prc*100, 2) FROM moex.mt5_continuous "
                 "WHERE ticker='%s' AND abs(toUnixTimestamp(bt) - %s) < 300 "
                 "ORDER BY abs(toUnixTimestamp(bt) - %s) LIMIT 1") % (ep, tk, et_ts, et_ts)
            chk = sh("clickhouse-client --host 10.0.0.60 --database moex --query \"" + q + "\"")
            dev = f"{float(chk):.2f}%" if chk else "n/a"
            out.append(f"  {tk} {d.upper()} x{ct} @ {ep} ({et} МСК) — цена vs рынок: {dev}")
        return out
    except Exception:
        return []

pos_check_lines = pos_check()

# ── 5. Cron жив? ──
cron_ok = 'run_moex_paper_trader' in sh("crontab -l 2>/dev/null | grep run_moex_paper_trader")

# bridge tz-fix в контейнере?
bridge_fix = "ok" if sh("docker exec mt5-finam grep -c 'r\\[.time.\\] - 3\\*3600' /app/engine/mt5_moex_bridge.py 2>/dev/null | grep -q '1' && echo yes") else "нет"
bridge_ok = (bridge_fix == "ok")

# ── Вывод ──
print(f"📊 OI папер {today}")
print(f"Eq={eq:,.0f}₽ | peak={peak:,.0f}₽ | DD={dd:.1f}% | позиций={n_pos}")
print(f"Сделки: сегодня {nt} ({pt:+,.0f}₽) | вчера {ny} ({py:+,.0f}₽)")
print(f"futoi: {'✅ свежие' if futoi_ok else '⚠️ СТАРЫЕ (см. watchdog)'}")
print(f"mt5_continuous: {'✅ свежие' if mt5_ok else f'⚠️ возраст {mt5_age_min} мин'}")
print(f"bridge tz-fix: {'✅' if bridge_ok else '❌ НЕТ (mt5_continuous поедет на +3ч)'}")
if pos_check_lines:
    print("Позиции:")
    for l in pos_check_lines:
        print(l)
print(f"cron: {'✅' if cron_ok else '❌ НЕТ'}")
if futoi_ok and mt5_ok and cron_ok and n_pos == 0 and int(nt or 0) == 0:
    print("---")
    print("Спокойный день: сигналов нет (|day_net| < 4%). Норма для нечастых сделок.")
