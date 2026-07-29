#!/bin/bash
cd /home/user/projects/TQA-MOEX-futures
export PATH=/home/user/.hermes/hermes-agent/venv/bin:/usr/bin:/bin
python3 run_paper_trader.py --state-key portfolio_v6 --broker dom >> /tmp/paper_trader_v6.log 2>&1
