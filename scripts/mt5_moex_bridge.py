#!/usr/bin/env python3
"""
MT5 MOEX Bridge — reads M1 bars from FINAM MT5 terminal, writes to CH.
Runs under Wine with WINEPREFIX=~/.wine-finam

Usage (Wine):
  WINEPREFIX=~/.wine-finam wine C:/Python311/python.exe Z:/home/user/projects/TQA-MOEX-futures/scripts/mt5_moex_bridge.py --loop

Writes to: moex.mt5_continuous (CH)
"""
import sys, os, time, json
from datetime import datetime

CH_HOST = '10.0.0.60'
CH_PORT = 8123
CH_DB = 'moex'

# MOEX tickers and their MT5 symbol names (FINAM)
# FINAM usually uses standard symbol names or *-RM suffix
MOEX_SYMBOLS = {
    'MM': 'MM-3.27',
    'GZ': 'GZ-3.27',
    'NG': 'NG-1.27',
    'BR': 'BR-1.27',
    'SV': 'SV-3.27',
    'CR': 'CR-3.27',
    'GD': 'GD-3.27',
    'RN': 'RN-3.27',
    'Si': 'Si-3.27',
}

# Alternative: try plain symbols too
FALLBACK_SYMBOLS = ['MM', 'GZ', 'NG', 'BR', 'SV', 'CR', 'GD', 'RN', 'Si']

def write_bars(ch, ticker, rates):
    """Write M1 bars to CH mt5_continuous."""
    if not rates:
        return 0
    data = []
    for r in rates:
        data.append({
            'ticker': ticker,
            'bt': datetime.fromtimestamp(r.time).strftime('%Y-%m-%d %H:%M:%S'),
            'opn': r.open,
            'hi': r.high,
            'lo': r.low,
            'prc': r.close,
            'vol': int(r.tick_volume) if r.tick_volume else 0,
        })
    
    # CH insert via JSONEachRow
    ch.insert('moex.mt5_continuous', data, column_names=['ticker','bt','opn','hi','lo','prc','vol'])
    return len(data)


def main():
    import MetaTrader5 as mt5
    
    loop_mode = '--loop' in sys.argv
    
    while True:
        now = datetime.now()
        print(f"\n=== MT5 MOEX Bridge == {now.isoformat()}", flush=True)
        
        # Initialize MT5 — try MOEX FINAM path, then FINAM old, then default
        moex_path = 'C:/Program Files/MetaTrader 5 MOEX/terminal64.exe'
        finam_old = 'C:/Program Files/MetaTrader 5 FINAM/terminal64.exe'
        init_ok = mt5.initialize(path=moex_path)
        if not init_ok:
            init_ok = mt5.initialize(path=finam_old)
        if not init_ok:
            init_ok = mt5.initialize(path='C:/Program Files/MetaTrader 5/terminal64.exe')
        if not init_ok:
            init_ok = mt5.initialize()  # try without path
        if not init_ok:
            print(f"❌ MT5 init failed: {mt5.last_error()}", flush=True)
            time.sleep(30)
            continue
        
        term = mt5.terminal_info()
        if not term or not term.connected:
            print("❌ Terminal not connected", flush=True)
            mt5.shutdown()
            time.sleep(30)
            continue
        
        print(f"   Terminal: {term.name}  Connected: {term.connected}", flush=True)
        
        # Discover available symbols
        all_syms = mt5.symbols_get()
        if all_syms:
            available = [s.name for s in all_syms]
            print(f"   Available symbols: {len(available)} total", flush=True)
            # Find MOEX-like symbols
            moex_found = [s for s in available if any(x in s.upper() for x in ['SI','MM','GZ','BR','NG','SV','CR','GD','RN'])]
            print(f"   MOEX-like: {moex_found[:15]}", flush=True)
        
        # Enable all symbols
        for sym in list(MOEX_SYMBOLS.values()) + FALLBACK_SYMBOLS:
            try:
                mt5.symbol_select(sym, True)
            except:
                pass
        
        # Connect to CH
        import clickhouse_connect as cc
        ch = cc.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)
        
        total = 0
        symbols_ok = 0
        for ticker, mt5_sym in MOEX_SYMBOLS.items():
            rates = mt5.copy_rates_from_pos(mt5_sym, mt5.TIMEFRAME_M1, 0, 5)
            if rates is None or len(rates) == 0:
                # Try fallback symbol
                for fb in FALLBACK_SYMBOLS:
                    if mt5_sym == fb:
                        continue
                    rates = mt5.copy_rates_from_pos(fb, mt5.TIMEFRAME_M1, 0, 5)
                    if rates and len(rates) > 0:
                        mt5_sym = fb
                        break
            
            if rates is None or len(rates) == 0:
                continue
            
            try:
                n = write_bars(ch, ticker, rates)
                total += n
                symbols_ok += 1
            except Exception as e:
                print(f"   ⚠ {ticker} ({mt5_sym}): write error: {e}", flush=True)
        
        ch.close()
        print(f"   ✅ {symbols_ok}/{len(MOEX_SYMBOLS)} symbols, {total} bars", flush=True)
        
        mt5.shutdown()
        
        if not loop_mode:
            break
        
        # Sleep until next full minute
        now = datetime.now()
        sleep_sec = 60 - now.second
        print(f"   😴 sleeping {sleep_sec}s...", flush=True)
        time.sleep(sleep_sec)


if __name__ == '__main__':
    sys.stdout = open(sys.stdout.fileno(), 'w', encoding='utf-8', buffering=1)
    main()
