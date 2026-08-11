#!/usr/bin/env bash
# moex_trade_audit.py — событийный анализ сделок OI папера.
# stdout → в канал monitor MOEX options (cron deliver matrix:...).
cd /home/user/projects/TQA-MOEX-futures
exec /home/user/projects/TQA-MOEX-futures/.venv/bin/python3 scripts/monitoring/moex_trade_audit.py 2>&1
