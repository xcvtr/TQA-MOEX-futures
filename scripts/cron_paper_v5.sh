#!/bin/bash
# System crontab wrapper for MOEX futures paper trader (portfolio_v5)
# Установка: crontab -e → добавить строку внизу

cd /home/user/projects/TQA-MOEX-futures
export PATH=/home/user/.venv/bin:/usr/bin:/bin

# Запуск paper trader
/home/user/projects/TQA-MOEX-futures/.venv/bin/python3 \
  run_paper_trader.py --state-key portfolio_v5 --stdout \
  >> /tmp/paper_trader.log 2>&1

# Проверка: если не запускается больше 5 минут — перезапустить
# (pidfile not needed — cron сам управляет)
