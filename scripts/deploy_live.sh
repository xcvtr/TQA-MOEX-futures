#!/bin/bash
# Deploy live — копирует проверенные файлы в strategies/common/live/
# Запускать после тестирования изменений
set -euo pipefail

cd "$(dirname "$0")/.."

echo "=== Deploying live ==="

# Основные файлы paper trader
cp strategies/common/paper_trader.py strategies/common/live/
cp strategies/common/paper_trader_v6.py strategies/common/live/
cp strategies/common/broker.py strategies/common/live/
cp strategies/common/broker_dom.py strategies/common/live/
cp strategies/common/executor.py strategies/common/live/
cp strategies/common/backtest.py strategies/common/live/
cp strategies/common/engine.py strategies/common/live/engine/

# Entry point
cp run_paper_trader.py strategies/common/live/

# Cron wrapper указывает на live
echo "#!/bin/bash
cd /home/user/projects/TQA-MOEX-futures
export PATH=/home/user/.hermes/hermes-agent/venv/bin:/usr/bin:/bin
python3 run_paper_trader.py --state-key portfolio_v5 >> /tmp/paper_trader.log 2>&1" > scripts/cron_paper_v5.sh

echo "=== Done ==="
echo "Live files: strategies/common/live/"
ls -la strategies/common/live/
