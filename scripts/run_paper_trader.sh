#!/usr/bin/env bash
set -euo pipefail
cd /home/user/projects/TQA-MOEX-futures
export PYTHONPATH=/home/user/projects/TQA-MOEX-futures
exec python3 strategies/common/paper_trader.py >> /tmp/paper_trader.log 2>&1
