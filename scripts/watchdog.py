#!/usr/bin/env python3
"""Watchdog: проверяет MT5 FINAM bridge и paper trader, рестартует при падении."""
import subprocess, os, sys
from datetime import datetime

LOG = os.path.expanduser("~/.hermes/cron/output/watchdog-dragon.log")
MT5_PATH = "/home/user/.wine/drive_c/Program Files/MetaTrader 5 FINAM/terminal64.exe"
TRADE_HOURS = range(15, 24)  # IRK, 15:00-23:59 = MOEX основная + вечерняя

def log(msg):
    with open(LOG, 'a') as f:
        f.write(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n")
    print(msg, flush=True)

def pg_query(q):
    try:
        r = subprocess.run(['psql', '-h', '10.0.0.60', '-U', 'postgres', '-d', 'moex', '-At', '-c', q],
                          capture_output=True, text=True, timeout=10)
        return r.stdout.strip()
    except:
        return ""

def is_market_hours():
    h = datetime.now().hour
    return h in TRADE_HOURS

alerts = []

# 1. MT5 terminal alive
term_count = subprocess.run(['pgrep', '-f', 'MetaTrader 5 FINAM/terminal64'], capture_output=True).stdout.count(b'\n')
if term_count == 0 and is_market_hours():
    alerts.append("⚠️ MT5 FINAM terminal DOWN — restarting")
    log(alerts[-1])
    subprocess.Popen(['xvfb-run', '--auto-servernum', '--server-args=-screen 0 1024x768x24',
                      'wine', MT5_PATH, '/portable'], stderr=subprocess.DEVNULL)
elif term_count == 0:
    log("⏸️ MT5 terminal down (market closed, skipping)")
else:
    log(f"✅ MT5 terminal OK ({term_count} procs)")

# 2. MT5 bridge — check paper.state age
state_age = pg_query("SELECT EXTRACT(EPOCH FROM NOW() - updated_at)/60 FROM paper.state WHERE strategy='dragon'")
state_age = float(state_age) if state_age else 999

if state_age > 15 and is_market_hours():
    alerts.append(f"⚠️ Paper trader stale: {state_age:.0f}m — running now")
    log(alerts[-1])
    subprocess.run(['python3', '/home/user/projects/TQA-MOEX-futures/scripts/paper_dragon.py'],
                  capture_output=True, timeout=60, cwd='/home/user/projects/TQA-MOEX-futures')
elif state_age > 15:
    log(f"⏸️ Paper trader stale ({state_age:.0f}m, market closed)")
else:
    log(f"✅ Paper trader OK ({state_age:.0f}m ago)")

# 3. Check equity
equity = pg_query("SELECT equity::text FROM paper.state WHERE strategy='dragon'")
if equity and float(equity) <= 0 and is_market_hours():
    alerts.append(f"🚨 EQUITY ZERO! Check account!")
    log(alerts[-1])

# 4. Dashboard alive on 8087
dash = subprocess.run(['pgrep', '-f', 'dashboard.py.*8087'], capture_output=True).stdout.count(b'\n')
if dash == 0:
    alerts.append("⚠️ Dashboard down — restarting")
    log(alerts[-1])
    subprocess.Popen(['/home/user/venvs/TQA-crypto/bin/python3',
                      '/home/user/projects/TQA-MOEX-futures/scripts/dashboard.py'],
                     cwd='/home/user/projects/TQA-MOEX-futures', stderr=subprocess.DEVNULL)
else:
    log(f"✅ Dashboard OK")

if alerts:
    log(f"🔔 {len(alerts)} alert(s)")
else:
    log("✅ All systems OK")
