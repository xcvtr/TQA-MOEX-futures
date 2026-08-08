#!/usr/bin/env python3
"""OI watchdog для TQA-MOEX-futures.

Проверяет живость OI-стратегии и шлёт email при проблемах:
1. futoi (CH) не обновляется (застрял loader) — в будни 10-18 MSK
2. mt5_continuous (CH) не обновляется
3. paper trader OI не делает сделок N торговых дней подряд (баг или затишье)
4. Критический DD

Крон: */30 17-23,0-2 * * 1-5 (каждые 30 мин в будни в торговые часы IRK)
Email: monitoring-0@ya.ru → m4slayer@ya.ru (Яндекс SMTP, как TQA-CRYPTO watchdog)
"""
import os
import sys
import json
import smtplib
from datetime import datetime, timezone, date, timedelta
from email.message import EmailMessage
from pathlib import Path

import clickhouse_connect as cc
import psycopg2

CH_HOST, CH_PORT, CH_DB = '10.0.0.60', 8123, 'moex'
PG = dict(host='10.0.0.60', port=5432, dbname='moex', user='postgres', connect_timeout=5)


def pg_conn():
    return psycopg2.connect(host=PG['host'], port=PG['port'], dbname=PG['dbname'],
                            user=PG['user'], connect_timeout=PG['connect_timeout'])

SMTP_HOST, SMTP_PORT = 'smtp.yandex.ru', 587
SENDER, RECIPIENT = 'monitoring-0@ya.ru', 'm4slayer@ya.ru'

STATE_FILE = '/tmp/oi_watchdog_state.json'
FUTOI_STALE_MIN = 15      # futoi max(bt) старше 15 мин в торговые часы
MT5_STALE_MIN = 15        # mt5_continuous max(bt) старше 15 мин
NO_TRADES_DAYS = 3        # 0 сделок OI N торговых дней подряд → алерт
DD_ALERT_PCT = 25.0       # критический DD (в % от peak)
MAX_ALERTS_PER_DAY = 2    # не спамить


def _load_pass():
    pwd = os.environ.get('SMTP_PASSWORD') or os.environ.get('WATCHDOG_SMTP_PASS')
    if pwd:
        return pwd
    env = Path.home() / '.hermes' / '.env'
    if env.exists():
        for line in env.read_text().strip().split('\n'):
            if line.startswith('SMTP_PASSWORD='):
                return line.split('=', 1)[1].strip()
    return ''


def send_alert(subject, body):
    pwd = _load_pass()
    if not pwd:
        print('  ⚠️ SMTP_PASSWORD not configured, alert not sent')
        return False
    try:
        msg = EmailMessage()
        msg['Subject'] = f'[TQA-MOEX-FUTURES] {subject}'
        msg['From'] = SENDER
        msg['To'] = RECIPIENT
        msg.set_content(body)
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as s:
            s.starttls()
            s.login(SENDER, pwd)
            s.send_message(msg)
        print(f'  📧 Alert sent to {RECIPIENT}')
        return True
    except Exception as e:
        print(f'  ❌ Email failed: {e}')
        return False


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(st):
    with open(STATE_FILE, 'w') as f:
        json.dump(st, f)


def trading_hours_now():
    """Будни 10:00-18:45 MSK (= 15:00-23:45 IRK) — время, когда данные ДОЛЖНЫ обновляться."""
    now = datetime.now(timezone(timedelta(hours=8)))
    if now.weekday() >= 5:
        return False
    h, m = now.hour, now.minute
    # IRK = MSK+5: 15:00 IRK = 10:00 MSK, 23:45 IRK = 18:45 MSK
    return (h == 15 and m >= 0) or (15 < h < 23) or (h == 23 and m <= 45)


def check():
    issues = []
    info = {}
    now = datetime.now(timezone(timedelta(hours=8)))

    # 1. futoi свежесть
    # ВАЖНО: futoi bt хранится в MSK, цены mt5_continuous — в IRK (+5ч от MSK).
    # Для сравнения с now (IRK) прибавляем 5 часов к MSK-времени futoi.
    try:
        ch = cc.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)
        r = ch.query("SELECT max(bt) FROM moex.futoi").result_rows
        ch.close()
        last = r[0][0]
        if last:
            last = last.replace(tzinfo=timezone(timedelta(hours=5)))  # MSK → IRK
            age = (now - last).total_seconds() / 60
            info['futoi_age_min'] = round(age, 1)
            if trading_hours_now() and age > FUTOI_STALE_MIN:
                issues.append(f'⏱ futoi stale: {age:.0f} мин (последний {last})')
        else:
            issues.append('❌ futoi пуст')
    except Exception as e:
        issues.append(f'❌ futoi check error: {e}')

    # 2. mt5_continuous свежесть (mt5_continuous bt уже IRK)
    try:
        ch = cc.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)
        r = ch.query("SELECT max(bt) FROM moex.mt5_continuous WHERE ticker='BR'").result_rows
        ch.close()
        last = r[0][0]
        if last:
            last = last.replace(tzinfo=timezone(timedelta(hours=8)))  # IRK
            age = (now - last).total_seconds() / 60
            info['mt5_age_min'] = round(age, 1)
            if trading_hours_now() and age > MT5_STALE_MIN:
                issues.append(f'⏱ mt5_continuous stale: {age:.0f} мин')
    except Exception as e:
        issues.append(f'❌ mt5 check error: {e}')

    # 3. Сделки OI за последние N торговых дней
    # ВАЖНО: общий фреймворк пишет ВСЕ сделки в futures.paper_trades (strategy='oi'),
    # а НЕ в paper_trades_oi (старая таблица, пустая).
    try:
        conn = pg_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT count(*), coalesce(max(entry_time), NULL)
            FROM futures.paper_trades
            WHERE strategy = 'oi' AND entry_time >= now() - interval '7 days'
        """)
        cnt, last_trade = cur.fetchone()
        info['trades_7d'] = cnt
        info['last_trade'] = str(last_trade) if last_trade else None
        if cnt == 0:
            # сколько торговых дней прошло с последней сделки
            cur.execute("SELECT max(entry_time) FROM futures.paper_trades WHERE strategy = 'oi'")
            last_any = cur.fetchone()[0]
            if last_any is None:
                # таблица вообще пуста — алертим только в торговые часы буднего дня
                if trading_hours_now():
                    issues.append('🕳 Нет НИ ОДНОЙ сделки OI (paper_trades пуст)')
            else:
                days_since = (date.today() - last_any.date()).days
                if days_since >= NO_TRADES_DAYS:
                    issues.append(f'🕳 Нет сделок OI {days_since} дн (последняя {last_any.date()})')

        # 4. DD по paper_state (общий фреймворк), не paper_state_oi
        try:
            cur.execute("SELECT equity, peak FROM futures.paper_state ORDER BY id DESC LIMIT 1")
            row = cur.fetchone()
            if row and row[0] and row[1]:
                eq, peak = float(row[0]), float(row[1])
                dd = (eq - peak) / peak * 100 if peak > 0 else 0
                info['dd_pct'] = round(dd, 1)
                if dd < -DD_ALERT_PCT:
                    issues.append(f'🚨 DD: {dd:.1f}% (eq={eq:,.0f}, peak={peak:,.0f})')
        except Exception:
            pass
        conn.close()
    except Exception as e:
        issues.append(f'❌ PG check error: {e}')

    return issues, info


def main():
    st = load_state()
    today = date.today().isoformat()
    issues, info = check()

    if not issues:
        # сброс счётчика алертов
        if st.get('date') != today:
            st = {'date': today, 'alerts': 0}
        save_state(st)
        print(f"✅ OI здоров: {info}")
        return

    # Ограничение частоты
    if st.get('date') != today:
        st = {'date': today, 'alerts': 0}
    if st.get('alerts', 0) >= MAX_ALERTS_PER_DAY:
        print(f"⚠️ Проблемы (лимит алертов): {issues}")
        return

    body = "🔍 OI Watchdog (TQA-MOEX-futures)\n" + "\n".join(f"  {i}" for i in issues)
    body += f"\n\ninfo: {info}"
    body += "\n\nКрон: paper trader */5, futoi loader */5, ГО 30 6"
    ok = send_alert('OI: проблемы', body)
    if ok:
        st['alerts'] = st.get('alerts', 0) + 1
        save_state(st)
    print(f"⚠️ Проблемы: {issues}")


if __name__ == '__main__':
    main()
