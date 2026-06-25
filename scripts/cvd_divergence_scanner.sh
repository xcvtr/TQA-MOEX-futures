#!/bin/bash
# CVD divergence paper trader — wrapper for cron
set -e
cd /home/user/projects/TQA-MOEX-futures
source .env
exec /home/user/projects/TQA-MOEX-futures/.venv/bin/python3 scripts/cvd_divergence_paper_trader.py "$@"
