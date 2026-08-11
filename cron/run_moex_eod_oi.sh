#!/usr/bin/env bash
set -euo pipefail

cd /home/user/projects/TQA-MOEX-futures
# CH_HOST=10.0.0.60 — живая реплика (10.0.0.64 openinterest в readonly: zookeeper metadata lost, 11.08)
export MOEX_CH_HOST=10.0.0.60
exec /home/user/venvs/tqa-moex-futures/bin/python3 load_eod_oi.py "$@"
