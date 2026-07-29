#!/bin/bash
# System crontab wrapper for MOEX futures paper trader v6 (DOM execution)
# Запуск: */5 15-23,0-4 * * 1-5 /home/user/projects/TQA-MOEX-futures/scripts/cron_paper_v6.sh
set -euo pipefail
cd /home/user/projects/TQA-MOEX-futures
exec python3 run_paper_trader.py --state-key portfolio_v6 --broker dom >> /tmp/paper_trader_v6.log 2>&1
