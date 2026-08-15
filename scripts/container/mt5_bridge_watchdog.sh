#!/bin/bash
# Watchdog моста MT5-FINAM: если wine-процесс моста умер — перезапустить + алерт.
# Крон: */5 * * * * (тихо, если всё ок)
# Критично: контейнер НЕ пересоздаём (теряется логин FINAM) — только рестарт процесса.

LOG=/tmp/mt5_bridge_watchdog.log
STATE=/tmp/mt5_bridge_watchdog_state  # хранит время последнего алерта (анти-спам)

# Проверяем, что мост жив (wine-процесс mt5_moex_bridge.py)
ALIVE=$(docker exec mt5-finam bash -c 'ps aux | grep -c "[m]t5_moex_bridge.py --loop"' 2>/dev/null)

if [ "$ALIVE" = "0" ] || [ -z "$ALIVE" ]; then
    echo "$(date): ⚠️ мост МЁРТВ (alive=$ALIVE) — перезапуск" >> $LOG
    # Анти-спам: не чаще 1 алерта в 30 мин
    if [ ! -f $STATE ] || [ $(( $(date +%s) - $(cat $STATE 2>/dev/null || echo 0) )) -gt 1800 ]; then
        date +%s > $STATE
        # Запуск моста заново (wine)
        docker exec -d mt5-finam bash -c 'WINEPREFIX=/root/.mt5 nohup wine C:/Python311/python.exe -u /app/engine/mt5_moex_bridge.py --loop >> /tmp/bridge_moex.log 2>&1' 2>/dev/null
        # Алерт в Matrix (через Hermes send_message — если доступен)
        if command -v hermes >/dev/null 2>&1; then
            echo "🚨 MT5-FINAM мост упал $(date '+%H:%M') — перезапущен watchdog'ом" | hermes send --target matrix 2>/dev/null || true
        fi
        echo "$(date): мост перезапущен + алерт отправлен" >> $LOG
    else
        echo "$(date): мост мёртв, алерт уже был <30мин назад — только рестарт" >> $LOG
        docker exec -d mt5-finam bash -c 'WINEPREFIX=/root/.mt5 nohup wine C:/Python311/python.exe -u /app/engine/mt5_moex_bridge.py --loop >> /tmp/bridge_moex.log 2>&1' 2>/dev/null
    fi
fi
# Тихо, если всё ок
