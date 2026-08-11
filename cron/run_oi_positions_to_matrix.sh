#!/usr/bin/env bash
# Отображение позиций OI папера в канал monitor MOEX (!CwXBuPBmDSGprggUtR)
# Скрипт сам шлёт статус в Matrix, молчит если позиций нет (no-agent cron, пустой stdout = тихо)
cd /home/user/projects/TQA-MOEX-futures
exec /home/user/projects/TQA-MOEX-futures/.venv/bin/python3 scripts/monitoring/oi_positions_to_matrix.py 2>&1
