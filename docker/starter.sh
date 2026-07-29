#!/bin/bash
# FINAM MT5 Starter — runs inside container
# Launches terminal + bridge in same shell (shared wineserver)
set -e

export WINEPREFIX=/root/.mt5
export DISPLAY=:5
export PYTHONIOENCODING=utf-8

MT5_DIR="$WINEPREFIX/drive_c/Program Files/MetaTrader 5"
LOG_DIR="/app/logs"
mkdir -p "$LOG_DIR"

echo "$(date): Starting FINAM MT5 terminal..."

# Kill stale wineservers
wineserver -k 2>/dev/null || true
sleep 2

# Start terminal in background
wine "$MT5_DIR/terminal64.exe" >> "$LOG_DIR/terminal.log" 2>&1 &
TERM_PID=$!
echo "$(date): Terminal PID $TERM_PID"

# Wait for terminal to initialize
echo "$(date): Waiting 30s for terminal..."
sleep 30

# Check if terminal is alive
if ! kill -0 $TERM_PID 2>/dev/null; then
    echo "$(date): Terminal died! Check terminal.log"
    cat "$LOG_DIR/terminal.log" | tail -10
    echo "$(date): Restarting in 10s..."
    sleep 10
    exit 1
fi

echo "$(date): Starting DOM API server..."
python3 /app/engine/dom_api.py --port 8808 &
echo "$(date): DOM API PID $!"
sleep 2

echo "$(date): Terminal alive. Starting MOEX bridge..."

# Start MOEX bridge (same wineserver)
wine "C:/Python311/python.exe" -u /app/engine/mt5_moex_bridge.py --loop >> "$LOG_DIR/bridge_moex.log" 2>&1 &
BRIDGE_PID=$!
echo "$(date): MOEX Bridge PID $BRIDGE_PID"

# Write PIDs
echo "$TERM_PID" > /app/term.pid
echo "$BRIDGE_PID" > /app/bridge_moex.pid

echo "$(date): All services started. Monitoring..."

# Keep alive — restart if needed
while true; do
    if ! kill -0 $TERM_PID 2>/dev/null; then
        echo "$(date): Terminal died! Restarting container..."
        exit 1
    fi
    if ! kill -0 $BRIDGE_PID 2>/dev/null; then
        echo "$(date): MOEX Bridge died! Restarting..."
        wineserver -k 2>/dev/null || true
        sleep 2
        wine "$MT5_DIR/terminal64.exe" >> "$LOG_DIR/terminal.log" 2>&1 &
        TERM_PID=$!
        sleep 30
        wine "C:/Python311/python.exe" -u /app/engine/mt5_moex_bridge.py --loop >> "$LOG_DIR/bridge_moex.log" 2>&1 &
        BRIDGE_PID=$!
        echo "$TERM_PID" > /app/term.pid
        echo "$BRIDGE_PID" > /app/bridge_moex.pid
    fi
    sleep 10
done
