#!/bin/bash
PYTHON=/home/user/venvs/TQA-crypto/bin/python3
cd /home/user/projects/TQA-MOEX-futures
mkdir -p logs
TS=$(date '+%Y-%m-%d %H:%M:%S')
echo "[$TS] === CVD Paper Trader run ===" >> logs/cvd_paper_trader.log
PYTHONPATH=. $PYTHON -u strategies/cvd/paper_trader.py >> logs/cvd_paper_trader.log 2>&1
EXIT_CODE=$?
echo "[$TS] Exit: $EXIT_CODE" >> logs/cvd_paper_trader.log
exit $EXIT_CODE
