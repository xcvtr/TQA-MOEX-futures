#!/usr/bin/env bash
set -euo pipefail

cd /home/user/projects/TQA-MOEX-futures
exec /home/user/venvs/tqa-moex-futures/bin/python3 loader.py
