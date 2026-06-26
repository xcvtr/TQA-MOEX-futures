#!/usr/bin/env bash
# CVD Divergence Paper Trader — обёртка для AlgoPack лоадера
# Запускается из системного cron.
# Основной запуск: /home/user/projects/TQA-MOEX-futures/scripts/cvd_paper_trader.sh

cd /home/user/projects/TQA-MOEX-futures || exit 1
mkdir -p logs

TS=$(date '+%Y-%m-%d %H:%M:%S')
echo "[$TS] === CVD Paper Trader run ===" >> logs/cvd_paper_trader.log

python3 scripts/cvd_divergence_paper_trader.py >> logs/cvd_paper_trader.log 2>&1

EXIT_CODE=$?
echo "[$TS] Exit: $EXIT_CODE" >> logs/cvd_paper_trader.log
exit $EXIT_CODE
