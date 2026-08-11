#!/usr/bin/env python3
"""
moex_trade_audit.py — событийный анализ сделок TQA-MOEX-futures (OI папер).
Каждые 20 мин (cron 0,20,40):
- ✅ ВХОД: новые позиции (появились в positions_json)
- ✅ ВЫХОД: закрытые позиции за окно (paper_trades)
- 🚨 ПРОБЛЕМЫ: неверные позиции → force_close (папер закроет при следующем тике) + алерт
- 🛑 ПАУЗА: DD > 20% или 3+ проблемы → .moex_pause_flag (папер блокирует новые входы)
- 🤖 REVIEW: неоднозначное → .moex_review_pending (LLM-агент разберёт)
stdout ПУСТ когда ничего не произошло (тишина). Дедуп по state-файлу.
"""
import sys, os, json, subprocess
from datetime import datetime, timezone, timedelta

PG = dict(host='10.0.0.60', dbname='moex', user='postgres')
STATE_FILE = os.path.expanduser('~/.hermes/scripts/.moex_audit_state.json')
PAUSE_FILE = os.path.expanduser('~/.hermes/scripts/.moex_pause_flag')
REVIEW_FILE = os.path.expanduser('~/.hermes/scripts/.moex_review_pending')

PORTFOLIO = {'BR', 'NG', 'SV'}          # live OI тикеры
RISKS = {'BR': 0.15, 'NG': 0.10, 'SV': 0.05}
GO = {'BR': 27606, 'NG': 6093, 'SV': 10971}  # ПГО (актуальные)
PAUSE_DD = 20.0                          # DD > 20% → пауза
PAUSE_HOURS = 6                          # авто-снятие паузы через 6ч
STALE_PCT = 1.5                          # вход по ушедшей цене >1.5% → force_close

import psycopg2
import clickhouse_connect

def get_pg():
    return psycopg2.connect(**PG)

def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {'seen_ids': [], 'seen_exits': []}

def save_state(st):
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(st, f)
    except Exception:
        pass

def main():
    lines = []
    alerts = []
    now = datetime.now(timezone.utc)
    since = now - timedelta(minutes=25)
    st = load_state()
    seen_ids = set(list(st.get('seen_ids', []))[-200:])
    seen_exits = set(list(st.get('seen_exits', []))[-200:])

    conn = get_pg()
    cur = conn.cursor()

    # ── 0. АВТО-ВОЗОБНОВЛЕНИЕ: пауза старше лимита → снять ──
    resumed = False
    try:
        if os.path.exists(PAUSE_FILE):
            age_h = (now - datetime.fromtimestamp(os.path.getmtime(PAUSE_FILE), tz=timezone.utc)).total_seconds() / 3600
            if age_h >= PAUSE_HOURS:
                os.remove(PAUSE_FILE)
                resumed = True
    except Exception:
        pass
    if resumed:
        lines.append(f"▶️ ВОЗОБНОВЛЕНИЕ: аудит-пауза снята (старше {PAUSE_HOURS}ч)")

    # ── 1. Состояние папера ──
    cur.execute("SELECT equity, peak, mtm_equity, mtm_peak, positions_json, updated_at FROM futures.paper_state ORDER BY updated_at DESC LIMIT 1")
    row = cur.fetchone()
    if not row:
        # нет state — тихо (папер не запускался)
        conn.close()
        return
    equity = float(row[0] or 0)
    peak = float(row[1] or 0)
    mtm_eq = float(row[2] or 0)
    mtm_peak = float(row[3] or 0)
    positions = json.loads(row[4]) if row[4] else []
    if not isinstance(positions, list):
        positions = list(positions.values()) if isinstance(positions, dict) else []
    open_pos = [p for p in positions if isinstance(p, dict) and not p.get('closed')]

    # ── 2. Новые позиции (входы) ──
    for p in open_pos:
        pid = p.get('id')
        if pid in seen_ids:
            continue
        seen_ids.add(pid)
        tk = p.get('ticker', '?')
        d = p.get('direction', '?')
        ct = int(p.get('contracts', p.get('base_contracts', 1)))
        ep = float(p.get('entry_price', 0))
        lines.append(f"✅ ВХОД {tk} {d.upper()} x{ct} @ {ep}")

    # ── 3. Закрытые сделки за окно (выходы) ──
    cur.execute("SELECT id, ticker, direction, entry_price, exit_price, pnl_rub, exit_reason, exit_time FROM futures.paper_trades WHERE exit_time > %s ORDER BY exit_time", (since,))
    for tid, tk, d, ep, xp, pnl, reason, xt in cur.fetchall():
        key = str(tid)
        if key in seen_exits:
            continue
        seen_exits.add(key)
        lines.append(f"✅ ВЫХОД {tk} {d.upper()} PnL={float(pnl or 0):+,.0f}₽ reason={reason}")

    # ── 4. ПРОБЛЕМЫ: автозакрытие неверных позиций ──
    auto_close = []
    pos_by_tk = {}
    for p in open_pos:
        tk = p.get('ticker', '')
        pos_by_tk.setdefault(tk, []).append(p)

    for p in open_pos:
        tk = p.get('ticker', '')
        if tk and tk not in PORTFOLIO:
            auto_close.append((p, f"тикер {tk} вне портфеля (BR/NG/SV) — legacy/мусор"))
            alerts.append(f"🚨 ЗАКРЫВАЮ {tk}: вне портфеля!")

    # Лот вне формулы (risk × min(eq, cap) / GO)
    ch = clickhouse_connect.get_client(host='10.0.0.60', port=8123, database='moex')
    for p in open_pos:
        tk = p.get('ticker', '')
        if tk not in GO:
            continue
        ct = int(p.get('contracts', p.get('base_contracts', 1)))
        risk = RISKS.get(tk, 0.1)
        cap_eq = min(equity, 2_000_000) if equity > 0 else equity
        expect_ct = max(1, int(cap_eq * risk / GO[tk]))
        if ct > expect_ct * 3:  # в 3× больше расчётного — явная ошибка
            auto_close.append((p, f"лот {ct} >> расчётного {expect_ct} (risk {risk:.0%})"))
            alerts.append(f"🚨 ЗАКРЫВАЮ {tk}: лот {ct} вне формулы (ожидалось ~{expect_ct})!")

    # Stale вход: entry далеко от текущей цены
    for p in open_pos:
        tk = p.get('ticker', '')
        ep = float(p.get('entry_price', 0) or 0)
        if not tk or ep <= 0:
            continue
        try:
            r = ch.query(f"SELECT prc FROM moex.mt5_continuous WHERE ticker='{tk}' ORDER BY bt DESC LIMIT 1").result_rows
            if r:
                last_px = float(r[0][0])
                diff = abs(last_px - ep) / ep * 100
                if diff > STALE_PCT:
                    auto_close.append((p, f"stale вход: entry {ep}, сейчас {last_px} ({diff:.1f}%)"))
                    alerts.append(f"🚨 ЗАКРЫВАЮ {tk}: вход по ушедшей цене ({diff:.1f}%)!")
        except Exception:
            pass
    ch.close()

    if auto_close:
        # Ставим force_close на позиции (папер закроет при следующем тике)
        seen_tk = set()
        for p, reason in auto_close:
            tk = p.get('ticker', '?')
            if tk in seen_tk:
                continue
            seen_tk.add(tk)
            p['force_close'] = True
            lines.append(f"🛑 force_close: {tk} ({reason})")
        try:
            cur.execute("UPDATE futures.paper_state SET positions_json=%s, updated_at=NOW() WHERE positions_json IS NOT NULL",
                        (json.dumps(positions),))
            conn.commit()
        except Exception as e:
            print(f"force_close update fail: {e}", file=sys.stderr)

    # ── 5. ЗАЩИТНАЯ ПАУЗА: DD > 20% или 3+ проблемы ──
    dd_pct = (peak - equity) / peak * 100 if peak > 0 else 0
    mtm_dd = (mtm_peak - mtm_eq) / mtm_peak * 100 if mtm_peak > 0 else 0
    paused = False
    if dd_pct > PAUSE_DD:
        paused = True
        alerts.append(f"🛑 ПАУЗА: DD {dd_pct:.1f}% > {PAUSE_DD:.0f}% (eq {equity:,.0f}₽, peak {peak:,.0f}₽)")
    elif mtm_dd > PAUSE_DD:
        paused = True
        alerts.append(f"🛑 ПАУЗА: MTM DD {mtm_dd:.1f}% > {PAUSE_DD:.0f}%")
    if len(alerts) >= 3 and not paused:
        paused = True
        alerts.append(f"🛑 ПАУЗА: {len(alerts)} проблемы подряд — входы заблокированы на {PAUSE_HOURS}ч")
    if paused:
        try:
            with open(PAUSE_FILE, 'w') as f:
                f.write(now.isoformat())
        except Exception:
            pass

    # ── 6. REVIEW-ФЛАГ для LLM-агента (неоднозначное) ──
    non_auto = [a for a in alerts if 'ЗАКРЫВАЮ' not in a and 'ПАУЗА' not in a]
    if non_auto and not paused:
        try:
            with open(REVIEW_FILE, 'w') as f:
                f.write(f"{now.isoformat()}\n" + "\n".join(non_auto) +
                        f"\nEq {equity:,.0f}₽, позиции: {[p.get('ticker') for p in open_pos] or 'нет'}")
        except Exception:
            pass

    save_state({'seen_ids': list(seen_ids)[-200:], 'seen_exits': list(seen_exits)[-200:]})
    conn.close()

    # ── Вывод ──
    if not lines and not alerts:
        return  # тишина

    out = []
    if lines:
        out.append("🔍 MOEX OI сделки:")
        out.extend(lines)
    if alerts:
        out.append("")
        out.extend(alerts)
    out.append(f"\nEq {equity:,.0f}₽ | DD {dd_pct:.1f}% | MTM DD {mtm_dd:.1f}% | открыто {len(open_pos)}: {', '.join(sorted({p.get('ticker') for p in open_pos})) or 'нет'}")
    text = "\n".join(out)
    print(text)

    # Звонки/email ТОЛЬКО при проблемах
    if alerts:
        try:
            subprocess.run(['python3', '/home/user/.hermes/scripts/send_alert_email.py',
                            'TQA-MOEX: проблема со сделкой', text], timeout=60, capture_output=True)
            subprocess.run(['python3', '/home/user/.hermes/scripts/send_voice_alert.py',
                            'Внимание, проблема со сделкой MOEX. ' + alerts[0][:150]], timeout=120, capture_output=True)
            subprocess.run(['/home/user/venvs/tqa/main/bin/python3', '/home/user/.hermes/scripts/matrix_call.py',
                            'Внимание, проблема со сделкой MOEX. ' + alerts[0][:150]], timeout=120, capture_output=True)
        except Exception as e:
            print(f"alert fail: {e}", file=sys.stderr)

if __name__ == '__main__':
    main()
