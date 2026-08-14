#!/bin/bash
# Применяет фиксы к mt5_moex_bridge.py в контейнере (если ещё не применены):
# 1. TZ-fix: MT5 отдаёт МСК-время как unix → вычитать 3ч при записи в CH
# 2. Тикеры из PG futures.portfolio (enabled=true) вместо жёсткого MOEX_PREFIXES
#    (иначе SBRF/SPYF не грузятся; вызывается при каждом пересоздании контейнера)
FILE=/app/engine/mt5_moex_bridge.py
TZ_OK=$(docker exec mt5-finam grep -cE "r\['time'\] - 3\*3600|timezone\.utc" "$FILE" 2>/dev/null)
PG_OK=$(docker exec mt5-finam grep -c "load_portfolio_tickers" "$FILE" 2>/dev/null)

if [ "$TZ_OK" -gt 0 ] && [ "$PG_OK" -gt 0 ]; then
    echo "$(date): bridge фиксы уже применены (tz=$TZ_OK, portfolio=$PG_OK)"
    exit 0
fi

echo "$(date): применяю фиксы к bridge (tz=$TZ_OK, portfolio=$PG_OK)..."
docker exec mt5-finam python3 - << 'PYEOF'
path = '/app/engine/mt5_moex_bridge.py'
src = open(path).read()
changed = []

# 1. TZ-fix
if "r['time'] - 3*3600" not in src:
    old = "datetime.fromtimestamp(r['time']),  # datetime object, not string"
    new = "datetime.fromtimestamp(r['time'] - 3*3600),  # FINAM отдаёт МСК как unix → UTC"
    if old in src:
        src = src.replace(old, new)
        changed.append('tz-fix')
    else:
        # fallback: любое fromtimestamp(r['time'])
        import re
        src2 = re.sub(r"datetime\.fromtimestamp\(r\['time'\]\)",
                      "datetime.fromtimestamp(r['time'] - 3*3600)", src)
        if src2 != src:
            src = src2
            changed.append('tz-fix(alt)')

# 2. Тикеры из PG portfolio
if 'load_portfolio_tickers' not in src:
    import re
    m = re.search(r'MOEX_PREFIXES\s*=\s*\{.*?\n\}', src, re.S)
    if m:
        new_code = '''# MOEX_PREFIXES: тикеры читаются из PG futures.portfolio (enabled=true) —
# НЕ хардкод. Маппинг тикер → MT5 символ: точный, ALLFUT+ticker, или префикс.
MOEX_PREFIXES = {}

import psycopg2 as _pg

def _load_portfolio_tickers():
    """Тикеры из futures.portfolio (enabled=true). Падение → пусто."""
    try:
        conn = _pg.connect(host='10.0.0.60', port=5432, dbname='moex',
                           user='postgres', connect_timeout=5)
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT ticker FROM futures.portfolio WHERE enabled = true")
        rows = [r[0] for r in cur.fetchall()]
        cur.close(); conn.close()
        return {t: t for t in rows}
    except Exception as e:
        print(f"   WARN load portfolio tickers: {e}", flush=True)
        return {}

def _refresh_moex_prefixes():
    MOEX_PREFIXES.clear()
    MOEX_PREFIXES.update(_load_portfolio_tickers())
    print(f"   Tickers from PG portfolio: {list(MOEX_PREFIXES.keys())}", flush=True)

_refresh_moex_prefixes()'''
        src = src[:m.start()] + new_code + src[m.end():]
        # refresh в main() и _collect_dom()
        src = src.replace(
            "        # Auto-detect active symbols for each ticker\n        active_symbols = {}",
            "        # Refresh tickers from PG portfolio\n        _refresh_moex_prefixes()\n        # Auto-detect active symbols for each ticker\n        active_symbols = {}")
        src = src.replace(
            "    for ticker, prefix in MOEX_PREFIXES.items():\n        sym = find_active_symbol(mt5_dom, prefix, all_syms)",
            "    _refresh_moex_prefixes()\n    for ticker, prefix in MOEX_PREFIXES.items():\n        sym = find_active_symbol(mt5_dom, prefix, all_syms)")
        changed.append('portfolio-tickers')

open(path, 'w').write(src)
print('applied:', changed if changed else 'nothing (уже было)')
PYEOF

# Перезапуск bridge, если что-то менялось
if docker exec mt5-finam bash -c 'ps aux | grep "mt5_moex_bridge.py --loop" | grep -v grep | wc -l' | grep -q "[1-9]"; then
    echo "$(date): перезапускаю bridge..."
    docker exec mt5-finam bash -c 'pkill -f "mt5_moex_bridge.py --loop"; sleep 2; cd /tmp && nohup wine C:/Python311/python.exe -u /app/engine/mt5_moex_bridge.py --loop >> /tmp/bridge_moex.log 2>&1 &'
fi
