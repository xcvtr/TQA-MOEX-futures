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
DEAL_FILE = os.path.expanduser('~/.hermes/scripts/.moex_deal_pending')  # флаг для глубокого ИИ-отчёта

PORTFOLIO = {'BR', 'NG', 'SV', 'TATN', 'SNGP'}  # fallback; в main() переопределяется из PG futures.portfolio (enabled)
RISKS = {'BR': 0.15, 'NG': 0.10, 'SV': 0.05, 'TATN': 0.35, 'SNGP': 0.35}  # mean_reversion: 0.35 из params
GO = {'BR': 27606, 'NG': 6093, 'SV': 10971, 'TATN': 17305, 'SNGP': 8284}  # ПГО (актуальные; TATN/SNGP из ticker_specs)
PAUSE_DD = 20.0                          # DD > 20% → пауза
PAUSE_HOURS = 6                          # авто-снятие паузы через 6ч
STALE_PCT = 5.0                          # вход по ушедшей цене >5% → force_close
                                          # (было 1.5% — слишком жёстко для mean_reversion:
                                          #  волатильные TATN/SNGP двигаются 1-2% за час — это
                                          #  НЕ ошибка входа, а нормальное движение)

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

def write_deal_flag(deal_info):
    """Пишет флаг сделки для LLM-агента (глубокий отчёт при входе/выходе).
    Накапливает до 5 последних сделок в JSON-списке."""
    try:
        pending = []
        if os.path.exists(DEAL_FILE):
            try:
                with open(DEAL_FILE) as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        pending = data
            except Exception:
                pending = []
        pending.append(deal_info)
        pending = pending[-5:]
        with open(DEAL_FILE, 'w') as f:
            json.dump(pending, f, ensure_ascii=False)
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

    # Живой портфель — из PG (enabled), НЕ хардкод: 19.08 в live добавлены mean_reversion (TATN/SNGP),
    # а хардкод {'BR','NG','SV'} force-закрывал их как «вне портфеля» (баг 21.08, аудит_close ×3).
    portfolio = set(PORTFOLIO)
    try:
        cur.execute("SELECT DISTINCT ticker FROM futures.portfolio WHERE enabled")
        live = {r[0] for r in cur.fetchall()}
        if live:
            portfolio = live
    except Exception:
        pass

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
        # Доп. проверка входа: day_net (дневная дельта КАК ПАПЕР) + цена vs рынок
        detail = ''
        et_raw = p.get('entry_time', '')
        try:
            et_dt = datetime.fromisoformat(et_raw.replace('Z', '+00:00'))
            et_ts = int(et_dt.timestamp())
            ch = clickhouse_connect.get_client(host='10.0.0.60', port=8123, database='moex')
            # Дневная дельта физлиц (как папер fetch_day_net):
            # day_start = первая запись IRK-дня (граница 07:00 UTC), day_net = (тек.−старт)/total
            DAY_SEC = 86400
            rows = ch.query(f"SELECT toUnixTimestamp(toDateTime(bt)), buy_fiz, sell_fiz, buy_yur, sell_yur FROM moex.futoi WHERE ticker='{tk}' AND bt >= toDateTime({et_ts - 72*3600}) ORDER BY bt").result_rows
            if rows:
                # День входа (граница 07:00 UTC)
                cur_day = int((et_ts - 7 * 3600) // DAY_SEC)
                day_rows = [r for r in rows if int((r[0] - 7 * 3600) // DAY_SEC) == cur_day]
                if len(day_rows) >= 2:
                    day_start_net = int(day_rows[0][1]) - int(day_rows[0][2])
                    # строка, ближайшая к моменту входа
                    best = min(day_rows, key=lambda r: abs(r[0] - et_ts))
                    cur_net = int(best[1]) - int(best[2])
                    total = int(best[1]) + int(best[2]) + int(best[3]) + int(best[4])
                    if total > 0:
                        dn = (cur_net - day_start_net) / total * 100
                        detail += f" day_net={dn:+.2f}%"
            # цена в момент входа vs текущая
            r2 = ch.query(f"SELECT prc FROM moex.mt5_continuous WHERE ticker='{tk}' AND abs(toUnixTimestamp(bt) - {et_ts}) < 300 ORDER BY abs(toUnixTimestamp(bt) - {et_ts}) LIMIT 1").result_rows
            r3 = ch.query(f"SELECT prc FROM moex.mt5_continuous WHERE ticker='{tk}' ORDER BY bt DESC LIMIT 1").result_rows
            if r2 and r3:
                px_enter = float(r2[0][0])
                px_now = float(r3[0][0])
                dev = abs(px_now - px_enter) / px_enter * 100
                detail += f" px_enter={px_enter} px_now={px_now} (Δ{dev:.2f}%)"
            ch.close()
        except Exception:
            pass
        # Собираем детали для глубокого ИИ-отчёта
        deal_info = {'type': 'ВХОД', 'ticker': tk, 'direction': d.upper(),
                     'contracts': ct, 'entry_price': ep, 'detail': detail.strip(),
                     'ts': now.isoformat()}
        write_deal_flag(deal_info)
        lines.append(f"✅ ВХОД {tk} {d.upper()} x{ct} @ {ep}{detail}")

    # ── 3. Закрытые сделки за окно (выходы) ──
    cur.execute("SELECT id, ticker, direction, entry_price, exit_price, pnl_rub, exit_reason, exit_time FROM futures.paper_trades WHERE exit_time > %s ORDER BY exit_time", (since,))
    for tid, tk, d, ep, xp, pnl, reason, xt in cur.fetchall():
        key = str(tid)
        if key in seen_exits:
            continue
        seen_exits.add(key)
        # Детали для глубокого ИИ-отчёта (выход)
        deal_info = {'type': 'ВЫХОД', 'ticker': tk, 'direction': d.upper(),
                     'entry_price': float(ep or 0), 'exit_price': float(xp or 0),
                     'pnl_rub': float(pnl or 0), 'reason': reason, 'ts': now.isoformat()}
        write_deal_flag(deal_info)
        lines.append(f"✅ ВЫХОД {tk} {d.upper()} PnL={float(pnl or 0):+,.0f}₽ reason={reason}")

    # ── 4. ПРОБЛЕМЫ: автозакрытие неверных позиций ──
    auto_close = []
    pos_by_tk = {}
    for p in open_pos:
        tk = p.get('ticker', '')
        pos_by_tk.setdefault(tk, []).append(p)

    for p in open_pos:
        tk = p.get('ticker', '')
        if tk and tk not in portfolio:
            auto_close.append((p, f"тикер {tk} вне портфеля (live: {sorted(portfolio)}) — legacy/мусор"))
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

    # Stale вход: entry далеко от цены В МОМЕНТ ВХОДА (только для свежих позиций < 1ч)
    # НЕ сравнивать с ТЕКУЩЕЙ ценой — для старых позиций цена закономерно ушла (это PnL, не ошибка)
    STALE_AGE_H = 1.0  # проверять только позиции младше 1 часа
    for p in open_pos:
        tk = p.get('ticker', '')
        ep = float(p.get('entry_price', 0) or 0)
        et_raw = p.get('entry_time', '')
        if not tk or ep <= 0 or not et_raw:
            continue
        try:
            et_dt = datetime.fromisoformat(et_raw.replace('Z', '+00:00'))
            age_h = (now - et_dt).total_seconds() / 3600
        except Exception:
            continue
        if age_h > STALE_AGE_H:
            continue  # старая позиция — цена ушла закономерно, это не stale вход
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

    # Дедуп force_close: если force_close уже стоит на позиции — не дублировать алерт
    already_closing = {p.get('ticker') for p in open_pos if p.get('force_close')}
    auto_close = [(p, reason) for p, reason in auto_close if p.get('ticker') not in already_closing]

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
    # Глубокий отчёт по сделкам делает LLM-агент (флаг DEAL_FILE). Скрипт шлёт в stdout
    # ТОЛЬКО алерты проблем (force_close/пауза) — они дублируются в канал через cron deliver.
    if not alerts:
        return  # тишина (сделки → DEAL_FILE для агента)

    out = []
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
