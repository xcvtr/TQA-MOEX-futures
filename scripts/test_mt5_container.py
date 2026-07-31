import MetaTrader5 as mt5
from datetime import datetime

print('Initializing...', flush=True)
ok = mt5.initialize(path='C:/Program Files/MetaTrader 5/terminal64.exe')
print(f'init ok={ok}', flush=True)

if ok:
    ti = mt5.terminal_info()
    print(f'Terminal: {ti.name}', flush=True)
    for sym in ['ALLFUTSi','ALLFUTGOLD','MOEXMM','ALLFUTROSN','ALLFUTNG']:
        rates = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M1, 0, 3)
        if rates and len(rates) > 0:
            print(f'{sym}: OK last={datetime.fromtimestamp(rates[-1][0])}', flush=True)
        else:
            print(f'{sym}: NO DATA', flush=True)
    mt5.shutdown()
else:
    print(f'Error: {mt5.last_error()}', flush=True)
