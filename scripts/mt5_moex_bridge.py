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

# MOEX tickers and their MT5 symbol prefix (FINAM format)
# FINAM uses quarter codes: SiU4, MMU4, GZU4, etc. (U=Sep, Z=Dec, H=Mar, M=Jun)
ALLFUT_PREFIXES = {
    # Stock futures (ALLFUT — FINAM continuous contracts)
    'Si': 'ALLFUTSi', 'BR': 'ALLFUTBR',
    'GOLD': 'ALLFUTGOLD', 'SILV': 'ALLFUTSILV', 'NG': 'ALLFUTNG',
    'LKOH': 'ALLFUTLKOH', 'SBRF': 'ALLFUTSBRF', 'VTBR': 'ALLFUTVTBR',
    'TATN': 'ALLFUTTATN', 'GAZR': 'ALLFUTGAZR', 'ROSN': 'ALLFUTROSN',
    'AFLT': 'ALLFUTAFLT', 'MTSI': 'ALLFUTMTSI', 'RTKM': 'ALLFUTRTKM',
    'SNGP': 'ALLFUTSNGP', 'SNGR': 'ALLFUTSNGR', 'SBPR': 'ALLFUTSBPR',
    'TRNF': 'ALLFUTTRNF', 'MGNT': 'ALLFUTMGNT', 'FEES': 'ALLFUTFEES',
    'HYDR': 'ALLFUTHYDR', 'MIX': 'ALLFUTMIX', 'RTSI': 'ALLFUTRTSI',
    'ED': 'ALLFUTED', 'Eu': 'ALLFUTEu', 'CNY': 'ALLFUTCNY',
    'GZ': 'ALLFUTGZ',
    # International
    'ES': 'ALLFUTES', 'SPYF': 'ALLFUTSPYF', 'NASD': 'ALLFUTNASD',
    'HANG': 'ALLFUTHANG', 'BTC': 'ALLFUTBTC', 'ETH': 'ALLFUTETH',
}

MOEX_PREFIXES = {
    'Si': 'Si',   # USD/RUB
    'MM': 'MM',   # Moscow Exchange Index
    'GZ': 'GZ',   # Natural Gas
    'BR': 'BR',   # Brent Oil
    'SV': 'SV',   # Sberbank
    'CR': 'CR',   # Crude Oil
    'GD': 'GD',   # Gold
    'RN': 'RN',   # Rosneft
    'NG': 'NG',   # Norilsk Nickel
    'Eu': 'Eu',   # Euro/RUB
    'ED': 'ED',   # Edinaya (RTS)
    # Perpetual (no quarter codes)
    'CNYRUBF': '__PERP__',  # CNY/RUB
    'X5': '__PERP__',       # X5 Retail
    'GLDRUBF': '__PERP__',  # Gold/RUB
    'EURRUBF': '__PERP__',  # EUR/RUB
    'IMOEXF': '__PERP__',   # MOEX Index
    'USDRUBF': '__PERP__',  # USD/RUB
}


def find_active_symbol(mt5, prefix, all_syms):
    """Find a symbol by prefix, preferring actively traded contracts.
    Contract priority: U4 (Sep) > Z4 (Dec) > H4 (Mar) > M4 (Jun) for Jul 2026.
    Perpetual symbols (__PERP__ prefix) match exact name."""
    if prefix == '__PERP__':
        return None  # handled in main loop
    # Priority order for month codes
    priority = {'U': 0, 'Z': 1, 'H': 2, 'M': 3, 'N': 4, 'Q': 5, 'F': 6, 'G': 7, 'J': 8, 'K': 9, 'V': 10}
    candidates = [(s.name, s) for s in all_syms 
                  if s.name.startswith(prefix) 
                  and len(s.name) <= len(prefix) + 3
                  and s.visible]
    def sort_key(item):
        name = item[0]
        code = name[len(prefix):]
        month = code[0] if len(code) > 0 else 'Z'
        year = code[1] if len(code) > 1 else '4'
        return (priority.get(month, 99), year)
    candidates.sort(key=sort_key)
    if candidates:
        return candidates[0][0]
    return None

def write_bars(ch, ticker, rates):
    """Write M1 bars to CH mt5_continuous."""
    if rates is None or len(rates) == 0:
        return 0
    data = []
    for r in rates:
        data.append([
            ticker,
            datetime.fromtimestamp(r['time']),  # datetime object, not string
            float(r['open']),
            float(r['high']),
            float(r['low']),
            float(r['close']),
            int(r['tick_volume']) if r['tick_volume'] else 0,
            int(r['tick_volume']) if r['tick_volume'] else 0,
        ])
    
    # CH insert via JSONEachRow — use list of lists format
    ch.insert('moex.mt5_continuous', data, column_names=['ticker','bt','opn','hi','lo','prc','vol','tick_vol'])
    return len(data)


def main():
    import MetaTrader5 as mt5
    
    loop_mode = '--loop' in sys.argv
    
    while True:
        now = datetime.now()
        print(f"\n=== MT5 MOEX Bridge == {now.isoformat()}", flush=True)
        
        # Initialize MT5 — connect to same-prefix terminal (env inherited from finam)
        # First try the MOEX terminal path, then the FINAM US path, then auto-detect
        init_ok = mt5.initialize(path='C:/Program Files/MetaTrader 5/terminal64.exe')
        if not init_ok:
            init_ok = mt5.initialize(path='C:/Program Files/MetaTrader 5 FINAM/terminal64.exe')
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
        
        # Discover available symbols and find active MOEX contracts
        all_syms = mt5.symbols_get()
        if all_syms:
            available = [s.name for s in all_syms]
            print(f"   Available symbols: {len(available)} total", flush=True)
        
        # Auto-detect active symbols for each ticker
        # 🚨 ФИКС: используем ALLFUT-континуумы, НЕ квартальные коды!
        # MOEX_PREFIXES ('SV'→'SV') находил АКЦИЮ Сбербанка (370₽) вместо
        # фьючерса SILV (67₽) — мусор в mt5_continuous. ALLFUT — правильные.
        # Маппинг ALLFUT-имя → канонический тикер CH (SILV→SV, GOLD→GD, CNY→CR)
        ALLFUT_TO_TICKER = {
            'ALLFUTSILV': 'SV', 'ALLFUTGOLD': 'GD', 'ALLFUTCNY': 'CR',
            'ALLFUTEu': 'Eu', 'ALLFUTGZ': 'GZ',
        }
        active_symbols = {}
        for ticker, sym in ALLFUT_PREFIXES.items():
            if not mt5.symbol_info(sym):
                print(f"   {ticker} → not found", flush=True)
                continue
            ch_ticker = ALLFUT_TO_TICKER.get(sym, ticker)
            active_symbols[ch_ticker] = sym
            print(f"   {ch_ticker} → {sym}", flush=True)
        
        if not active_symbols:
            print("❌ No MOEX symbols found!", flush=True)
            mt5.shutdown()
            time.sleep(60)
            continue
        
        # Connect to CH
        import clickhouse_connect as cc
        ch = cc.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)
        
        total = 0
        symbols_ok = 0
        for ticker, mt5_sym in active_symbols.items():
            rates = mt5.copy_rates_from_pos(mt5_sym, mt5.TIMEFRAME_M1, 0, 5)
            
            if rates is None or (hasattr(rates, '__len__') and len(rates) == 0):
                print(f"   {ticker} ({mt5_sym}): no data", flush=True)
                continue
            
            try:
                n = write_bars(ch, ticker, rates)
                total += n
                symbols_ok += 1
            except Exception as e:
                print(f"   ⚠ {ticker} ({mt5_sym}): write error: {e}", flush=True)
        
        ch.close()
        print(f"   ✅ {symbols_ok}/{len(active_symbols)} symbols, {total} bars", flush=True)
        
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
