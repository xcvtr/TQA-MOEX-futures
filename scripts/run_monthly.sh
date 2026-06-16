#!/bin/bash
cd /home/user/projects/TQA-MOEX
rm -f reports/phase5_monthly_pnl/run.log
~/venvs/tqa/main/bin/python3 -u scripts/phase5_monthly_pnl.py > reports/phase5_monthly_pnl/run.log 2>&1
echo "EXIT_CODE: $?" >> reports/phase5_monthly_pnl/run.log