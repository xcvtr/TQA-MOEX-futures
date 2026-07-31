#!/usr/bin/env python3 -u
"""Load MOEX M1 bars from MT5 FINAM via MQL5 export → CH moex.mt5_continuous."""
import subprocess, json, sys, os, time
from datetime import datetime
import clickhouse_connect as cc

WINEPREFIX = '/home/user/.wine-finam'
MT5_DIR = f'{WINEPREFIX}/drive_c/Program Files/MetaTrader 5'
SCRIPT = 'MQL5/Scripts/ohlcv_export_moex.ex5'

# 1. Run MQL5 script on FINAM terminal (headless)
print('Starting FINAM terminal for export...', flush=True)
proc = subprocess.Popen(
    ['wine', f'{MT5_DIR}/terminal64.exe', '/script:' + SCRIPT, '/portable'],
    cwd=MT5_DIR, env={**os.environ, 'WINEPREFIX': WINEPREFIX, 'DISPLAY': ':99'},
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
)

# Wait for export
print('Waiting 30s for export...', flush=True)
time.sleep(30)

# Kill terminal
proc.terminate()
time.sleep(2)
try: proc.kill()
except: pass
print('Terminal killed', flush=True)

# 2. Find JSON in FILE_COMMON (wine)
common_dir = os.path.expanduser(f'{WINEPREFIX}/drive_c/users/{os.getenv("USER","root")}/AppData/Roaming/MetaQuotes/Terminal/Common')
json_path = os.path.join(common_dir, 'mt5_moex_ohlcv.json')

if not os.path.exists(json_path):
    # Try alternate path
    json_path = os.path.expanduser(f'{WINEPREFIX}/drive_c/users/root/AppData/Roaming/MetaQuotes/Terminal/Common/mt5_moex_ohlcv.json')

if not os.path.exists(json_path):
    print(f'JSON not found (tried {json_path})', flush=True)
    # Search
    import glob
    files = glob.glob(f'{WINEPREFIX}/drive_c/users/*/AppData/**/mt5_moex_ohlcv.json', recursive=True)
    if files:
        json_path = files[0]
    else:
        print('JSON not found anywhere!', flush=True)
        sys.exit(1)

print(f'Found JSON: {json_path} ({os.path.getsize(json_path)} bytes)', flush=True)

# 3. Parse and load to CH
with open(json_path) as f:
    data = json.load(f)

ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')
total = 0

for sym_data in data.get('symbols', []):
    symbol = sym_data['s']
    # Map MT5 symbol → ticker
    ticker_map = {'ALLFUTSi':'Si','ALLFUTGOLD':'GD','MOEXMM':'MM',
                  'ALLFUTROSN':'RN','ALLFUTNG':'NG','ALLFUTBR':'BR'}
    ticker = ticker_map.get(symbol, symbol)
    
    bars = sym_data.get('b', [])
    if not bars:
        print(f'{symbol}: no bars', flush=True)
        continue
    
    batch = []
    for b in bars:
        ts = datetime.fromtimestamp(b['t'])
        batch.append((ticker, ts, float(b['o']), float(b['h']), float(b['l']),
                      float(b['c']), int(b.get('v',0)), 0))
    
    if batch:
        ch.insert('moex.mt5_continuous', batch,
                  column_names=['ticker','bt','opn','hi','lo','prc','vol','tick_vol'])
        total += len(batch)
        print(f'{ticker}: {len(batch)} bars ({batch[0][1]} -> {batch[-1][1]})', flush=True)

ch.close()
print(f'Done: {total} bars loaded', flush=True)

# Cleanup
os.remove(json_path)
print('JSON cleaned up', flush=True)
