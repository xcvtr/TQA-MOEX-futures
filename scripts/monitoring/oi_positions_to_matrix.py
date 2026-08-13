#!/usr/bin/env python3 -u
"""Отображение открытых позиций OI папера в канал monitor MOEX options (!WDeUiNFGfJyHeAzGLW).

Cron: каждые 30 мин в торговые часы (15:00-23:00, 0:00-4:00 IRK будни):
  */30 15-23,0-4 * * 1-5

Поведение:
- Если есть открытые позиции — отправляет компактный статус (Eq, DD, позиции с UPnL)
- Если позиций нет — ТИХО (ничего не шлёт, stdout пуст) — не спамит канал
- При ошибке доступа — одна строка в stderr (не шлёт в канал)

Формат (в канале):
📊 OI папер 11.08 17:30
Eq=200 000₽ | DD=0.0% | MTM=-77₽ | позиций=1
🟢 NG LONG x1 @ 2.791 | UPnL -77₽ | 2.2ч из 72ч

Отправка: Matrix через curl (MATRIX_ACCESS_TOKEN из ~/.hermes/.env).
"""
import json, os, sys, datetime, subprocess

ROOM_ID = '!WDeUiNFGfJyHeAzGLW:matrix.local'  # monitor MOEX options
PG_HOST = '10.0.0.60'
STATE_TBL = 'futures.paper_state'  # текущий OI папер (без суффикса)

def sh(cmd, timeout=30):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()
    except Exception:
        return ''

def load_env():
    """Читает MATRIX_ACCESS_TOKEN и MATRIX_HOMESERVER из ~/.hermes/.env."""
    env_path = os.path.expanduser('~/.hermes/.env')
    out = {}
    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                k, v = line.split('=', 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    except Exception as e:
        print(f"⚠️ .env read error: {e}", file=sys.stderr)
    return out

def send_matrix(env, body):
    token = env.get('MATRIX_ACCESS_TOKEN', '')
    host = env.get('MATRIX_HOMESERVER', 'http://127.0.0.1:8008')
    if not token:
        print("⚠️ MATRIX_ACCESS_TOKEN не найден", file=sys.stderr)
        return False
    # Markdown → HTML для рендера жирного/курсива в Element
    import html as _html
    try:
        import markdown as _md
        formatted = _md.markdown(body, extensions=["fenced_code", "tables"])
        formatted = formatted.replace("<h1>", "<strong>").replace("</h1>", "</strong>")
        formatted = formatted.replace("<h2>", "<strong>").replace("</h2>", "</strong>")
        formatted = formatted.replace("<h3>", "<strong>").replace("</h3>", "</strong>")
        has_md = formatted != _html.escape(body)
    except Exception:
        formatted = None
        has_md = False
    # экранирование для JSON
    def _jesc(s):
        return s.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')
    if has_md and formatted:
        payload = '{"msgtype":"m.text","body":"%s","format":"org.matrix.custom.html","formatted_body":"%s"}' % (
            _jesc(body), _jesc(formatted))
    else:
        payload = '{"msgtype":"m.text","body":"%s"}' % _jesc(body)
    r = subprocess.run(
        ['curl', '-s', '-X', 'POST',
         '-H', f'Authorization: Bearer {token}',
         '-H', 'Content-Type: application/json',
         '-d', payload,
         f'{host}/_matrix/client/v3/rooms/{ROOM_ID}/send/m.room.message'],
        capture_output=True, text=True, timeout=30)
    ok = 'event_id' in r.stdout
    if not ok:
        print(f"⚠️ Matrix send failed: {r.stdout[:200]}", file=sys.stderr)
    return ok

def get_state():
    q = (f"SELECT equity, peak, mtm_equity, mtm_peak, positions_json, updated_at "
         f"FROM {STATE_TBL} ORDER BY updated_at DESC LIMIT 1")
    row = sh(f"psql -h {PG_HOST} -U postgres -d moex -t -A -c \"{q}\"")
    if not row:
        return None
    parts = row.split('|')
    if len(parts) < 6:
        return None
    try:
        eq = float(parts[0])
        peak = float(parts[1])
        mtm_eq = float(parts[2] or 0)
        mtm_peak = float(parts[3] or 0)
        pos_raw = parts[4]
        ts = parts[5]
        pos = json.loads(pos_raw) if pos_raw and pos_raw != 'None' else []
        if not isinstance(pos, list):
            pos = list(pos.values()) if isinstance(pos, dict) else []
        return {'eq': eq, 'peak': peak, 'mtm_eq': mtm_eq, 'mtm_peak': mtm_peak,
                'pos': pos, 'ts': ts}
    except Exception as e:
        print(f"⚠️ state parse error: {e}", file=sys.stderr)
        return None

SPECS_CACHE = {}
FEES_CACHE = {}

def get_spec(tk):
    """(ms, sp) тикера из PG ticker_specs (с кэшем)."""
    if tk not in SPECS_CACHE:
        try:
            r = sh(f"psql -h {PG_HOST} -U postgres -d moex -t -A -c \"SELECT min_step, step_price FROM futures.ticker_specs WHERE ticker='{tk}'\"")
            parts = r.split('|') if r else []
            ms = float(parts[0]) if len(parts) > 0 and parts[0] else 0.01
            sp = float(parts[1]) if len(parts) > 1 and parts[1] else 1.0
            SPECS_CACHE[tk] = (ms, sp)
        except Exception:
            SPECS_CACHE[tk] = (0.01, 1.0)
    return SPECS_CACHE[tk]

def fee2(tk):
    """Round-trip комиссия на лот (fee×2) из PG ticker_specs."""
    if tk not in FEES_CACHE:
        try:
            r = sh(f"psql -h {PG_HOST} -U postgres -d moex -t -A -c \"SELECT COALESCE(fee_entry,4.0) FROM futures.ticker_specs WHERE ticker='{tk}'\"")
            FEES_CACHE[tk] = float(r) * 2 if r else 8.0
        except Exception:
            FEES_CACHE[tk] = 8.0
    return FEES_CACHE[tk]

def get_last_price(tk):
    """Последняя цена из mt5_continuous."""
    try:
        r = sh(f"clickhouse-client --host 10.0.0.60 --database moex --query \"SELECT prc FROM moex.mt5_continuous WHERE ticker='{tk}' ORDER BY bt DESC LIMIT 1\"")
        return float(r) if r else None
    except Exception:
        return None

def fmt_pos(p):
    tk = p.get('ticker', '?')
    d = p.get('direction', '?')
    ct = int(p.get('contracts', p.get('base_contracts', 1)))
    ep = float(p.get('entry_price', 0))
    # UPnL открытой позиции: p['pnl']=0 до закрытия — считаем по текущей цене (MTM)
    pnl = float(p.get('pnl', 0))
    if pnl == 0:
        px = get_last_price(tk)
        if px and ep > 0:
            ms, sp = get_spec(tk)
            if d == 'long':
                pnl = (px - ep) / ms * sp * ct - fee2(tk) * ct
            else:
                pnl = (ep - px) / ms * sp * ct - fee2(tk) * ct
    hold_h = p.get('max_hold_h', 72)
    et_raw = p.get('entry_time', '')
    age_h = 0.0
    try:
        et_dt = datetime.datetime.fromisoformat(et_raw.replace('Z', '+00:00'))
        age_h = (datetime.datetime.now(datetime.timezone.utc) - et_dt).total_seconds() / 3600
    except Exception:
        pass
    arrow = '🟢' if d == 'long' else '🔴'
    pnl_s = f"{pnl:+,.0f}₽"
    return f"{arrow} {tk} {d.upper()} x{ct} @ {ep} | UPnL {pnl_s} | {age_h:.1f}ч из {hold_h}ч"

def main():
    env = load_env()
    st = get_state()
    if not st:
        print("⚠️ PG state пуст — папер не запускался?", file=sys.stderr)
        return 0

    open_pos = [p for p in st['pos'] if isinstance(p, dict) and not p.get('closed')]
    if not open_pos:
        # нет позиций — тихо, не спамим канал
        return 0

    dd = (st['peak'] - st['eq']) / st['peak'] * 100 if st['peak'] else 0
    mtm_dd = (st['mtm_peak'] - st['mtm_eq']) / st['mtm_peak'] * 100 if st['mtm_peak'] else 0
    ts_local = datetime.datetime.now().strftime('%d.%m %H:%M')

    lines = [f"📊 OI папер {ts_local}"]
    lines.append(f"Eq={st['eq']:,.0f}₽ | DD={dd:.1f}% | MTM DD={mtm_dd:.1f}% | позиций={len(open_pos)}")
    for p in open_pos:
        lines.append(fmt_pos(p))
    body = '\n'.join(lines)

    send_matrix(env, body)
    return 0

if __name__ == '__main__':
    sys.exit(main())
