#!/bin/bash
cd /home/user/projects/TQA-MOEX-futures
export PATH=/home/user/.hermes/hermes-agent/venv/bin:/usr/bin:/bin
python3 run_paper_trader.py --state-key portfolio_v5 >> /tmp/paper_trader.log 2>&1
