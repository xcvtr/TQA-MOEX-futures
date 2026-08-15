#!/bin/bash
# Мониторинг свежести live-данных: bars_1m + futoi_iss.
# Если данные старые (рынок открыт, а данных нет) — алерт в Matrix.
# Крон: */5 15-23,0-4 * * 1-5 (торговые часы), тихо если ок.

LOG=/tmp/data_freshness_watchdog.log
STATE=/tmp/data_freshness_state

# Торговые часы МСК: 10:00-18:45 (будни) = данные ДОЛЖНЫ обновляться
H=$(TZ=Europe/Moscow date +%H); M=$(TZ=Europe/Moscow date +%M); DOW=$(date +%u)
if [ "$DOW" -gt 5 ]; then exit 0; fi  # выходные — не проверяем
if [ "$H" -lt 10 ] || { [ "$H" -eq 10 ] && [ "$M" -lt 5 ]; } || { [ "$H" -eq 18 ] && [ "$M" -gt 45 ]; } || [ "$H" -gt 18 ]; then
    exit 0  # вне торговых часов
fi

# 1. bars_1m свежесть (max age минут)
BR_AGE=$(psql -h 10.0.0.60 -U postgres -d moex -t -c "SELECT round(EXTRACT(EPOCH FROM (now()-max(bt)))/60) FROM futures.bars_1m WHERE ticker='BR'" 2>/dev/null | tr -d ' ')
# 2. futoi_iss свежесть
FUTOI_AGE=$(psql -h 10.0.0.60 -U postgres -d moex -t -c "SELECT round(EXTRACT(EPOCH FROM (now()-max(bt)))/60) FROM futures.futoi_iss WHERE ticker='BR'" 2>/dev/null | tr -d ' ')

PROBLEM=""
[ -n "$BR_AGE" ] && [ "$BR_AGE" -gt 10 ] 2>/dev/null && PROBLEM="bars_1m: ${BR_AGE} мин"
[ -n "$FUTOI_AGE" ] && [ "$FUTOI_AGE" -gt 12 ] 2>/dev/null && PROBLEM="$PROBLEM futoi_iss: ${FUTOI_AGE} мин"

if [ -n "$PROBLEM" ]; then
    echo "$(date): ⚠️ данные старые: $PROBLEM" >> $LOG
    # Анти-спам: 1 алерт в 30 мин
    if [ ! -f $STATE ] || [ $(( $(date +%s) - $(cat $STATE 2>/dev/null || echo 0) )) -gt 1800 ]; then
        date +%s > $STATE
        if command -v hermes >/dev/null 2>&1; then
            echo "⚠️ Данные устарели: $PROBLEM (рынок открыт!)" | hermes send --target matrix 2>/dev/null || true
        fi
        echo "$(date): алерт отправлен" >> $LOG
    fi
fi
# Тихо, если всё ок
