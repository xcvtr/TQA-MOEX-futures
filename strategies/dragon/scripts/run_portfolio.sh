#!/bin/bash
cd /home/user/projects/TQA-MOEX-futures
exec python3 -u strategies/dragon/scripts/portfolio_run.py > /tmp/portfolio_v5_slip.txt 2>&1