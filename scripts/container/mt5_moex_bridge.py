#!/usr/bin/env python3
"""
MT5 MOEX Bridge — reads M1 bars from FINAM MT5 terminal, writes to CH.
Runs under Wine with WINEPREFIX=~/.wine-finam

Usage (Wine):
  WINEPREFIX=~/.wine-finam wine C:/Python311/python.exe Z:/home/user/projects/TQA-MOEX-futures/scripts/mt5_moex_bridge.py --loop

Writes to: moex.mt5_continuous (CH)
"""
import sys, os, time, json
from datetime import datetime, timezone

CH_HOST = '10.0.0.60'
CH_PORT = 8123
CH_DB = 'moex'

# MOEX tickers and their MT5 symbol prefix (FINAM format)
# FINAM uses quarter codes: SiU4, MMU4, GZU4, etc. (U=Sep, Z=Dec, H=Mar, M=Jun)
# MOEX_PREFIXES: тикеры читаются из PG futures.portfolio (enabled=true) —
# НЕ хардкод. Маппинг тикер → MT5 символ: точный, ALLFUT+ticker, или префикс.
MOEX_PREFIXES = {}

import psycopg2 as _pg

def _load_portfolio_tickers():
    """Тикеры из futures.portfolio (enabled=true). Падение → пусто."""
    try:
        conn = _pg.connect(host='10.0.0.60', port=5432, dbname='moex',
                           user='postgres', connect_timeout=5)
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT ticker FROM futures.portfolio WHERE enabled = true")
        rows = [r[0] for r in cur.fetchall()]
        cur.close(); conn.close()
        return {t: t for t in rows}  # prefix == ticker, find_active_symbol найдёт
    except Exception as e:
        print(f"   WARN load portfolio tickers: {e}", flush=True)
        return {}

def _refresh_moex_prefixes():
    MOEX_PREFIXES.clear()
    MOEX_PREFIXES.update(_load_portfolio_tickers())
    print(f"   Tickers from PG portfolio: {list(MOEX_PREFIXES.keys())}", flush=True)

# initial load
_refresh_moex_prefixes()


def find_active_symbol(mt5, prefix, all_syms):
    """Find a symbol by prefix, prefer pinned from PG, then pick most recent data."""
    import psycopg2
    
    # Try pinned symbol from PG
    try:
        conn = psycopg2.connect(host='10.0.0.60', port=5432, dbname='moex', user='postgres', connect_timeout=2)
        cur = conn.cursor()
        cur.execute("SELECT symbol FROM futures.active_symbols WHERE prefix = %s", (prefix,))
        r = cur.fetchone()
        if r:
            sym = r[0]
            info = mt5.symbol_info(sym)
            # pinned: достаточно что есть бары и стакан (ALLFUT-символы могут быть trade_mode=0)
            mt5.symbol_select(sym, True)
            test = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M1, 0, 1)
            if test is not None and len(test) > 0:
                mt5.market_book_add(sym)
                import time as _t; _t.sleep(2)
                bk = mt5.market_book_get(sym)
                if bk is not None and len(bk) > 0:
                    cur.close(); conn.close()
                    return sym
        cur.close(); conn.close()
    except: pass
    
    # Find best by data recency
    candidates = []
    # Точное совпадение / ALLFUT+ticker / префикс
    for s in all_syms:
        n = s.name
        if n == prefix or n == 'ALLFUT' + prefix or n.startswith(prefix):
            candidates.append((n, s))
        elif len(n) <= len(prefix) + 3 and n.startswith(prefix[:len(n)]):
            candidates.append((n, s))
    best_sym = None; best_time = 0; best_exp = None
    for sym_name, _ in candidates:
        mt5.symbol_select(sym_name, True)
        test = mt5.copy_rates_from_pos(sym_name, mt5.TIMEFRAME_M1, 0, 1)
        if test is not None and len(test) > 0:
            ts = test[0][0]
            if ts > best_time:
                best_time = ts; best_sym = sym_name
                info = mt5.symbol_info(sym_name)
                best_exp = info.expiration_time if info and info.expiration_time > 0 else None
    
    # Pin to PG
    if best_sym:
        try:
            conn = psycopg2.connect(host='10.0.0.60', port=5432, dbname='moex', user='postgres', connect_timeout=2)
            cur = conn.cursor()
            exp_dt = datetime.fromtimestamp(best_exp) if best_exp else None
            last_dt = datetime.fromtimestamp(best_time) if best_time else None
            cur.execute("""
                INSERT INTO futures.active_symbols (prefix, symbol, updated_at, expiration_time, last_bar_time)
                VALUES (%s, %s, now(), %s, %s)
                ON CONFLICT (prefix) DO UPDATE SET symbol = %s, updated_at = now(), expiration_time = %s, last_bar_time = %s
            """, (prefix, best_sym, exp_dt, last_dt, best_sym, exp_dt, last_dt))
            conn.commit()
            cur.close(); conn.close()
        except: pass
        return best_sym
    
    # Fallback: any visible
    for sym_name, _ in candidates:
        info = mt5.symbol_info(sym_name)
        if info and info.visible:
            return sym_name
    return None

def write_bars(ch, ticker, rates):
    """Write M1 bars to CH mt5_continuous + PG bars_1m.
    Время: FINAM отдаёт МСК as unix → конвертируем в aware UTC (правильный момент).
    Хранение: unix (момент). Отображение: МСК (PG timezone=Europe/Moscow)."""
    if rates is None or len(rates) == 0:
        return 0
    data = []
    for r in rates:
        # FINAM time = МСК-часы как unix. МСК = UTC+3 → вычесть 3ч для UTC-момента.
        ts_utc = datetime.fromtimestamp(r['time'] - 3*3600, tz=timezone.utc)
        data.append([
            ticker,
            ts_utc,
            float(r['open']),
            float(r['high']),
            float(r['low']),
            float(r['close']),
            int(r['tick_volume']) if r['tick_volume'] else 0,
            int(r['tick_volume']) if r['tick_volume'] else 0,
        ])
    
    # CH insert via JSONEachRow — use list of lists format
    ch.insert('moex.mt5_continuous', data, column_names=['ticker','bt','opn','hi','lo','prc','vol','tick_vol'])

    # ── PG dual-write: futures.bars_1m (для live-папера, последние 14 дней + autopurge) ──
    try:
        import psycopg2
        conn = psycopg2.connect(host='10.0.0.60', port=5432, dbname='moex', user='postgres', connect_timeout=3)
        cur = conn.cursor()
        for row in data:
            cur.execute(
                "INSERT INTO futures.bars_1m (ticker,bt,opn,hi,lo,prc,vol) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (ticker,bt) DO NOTHING",
                (row[0], row[1], row[2], row[3], row[4], row[5], row[6]))
        # autopurge: старше 14 дней — удаляем
        cur.execute("DELETE FROM futures.bars_1m WHERE bt < now() - INTERVAL '14 days'")
        conn.commit()
        cur.close(); conn.close()
    except Exception as e:
        print(f"   ⚠ PG bars_1m write error: {e}", flush=True)
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
        
        # Refresh tickers from PG portfolio (добавили SBRF/SPYF → подхватятся)
        _refresh_moex_prefixes()
        # Auto-detect active symbols for each ticker
        active_symbols = {}
        for ticker, prefix in MOEX_PREFIXES.items():
            found = find_active_symbol(mt5, prefix, all_syms)
            if found:
                active_symbols[ticker] = found
                print(f"   {ticker} → {found}", flush=True)
            else:
                print(f"   {ticker} → not found", flush=True)
        
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
            rates = mt5.copy_rates_from_pos(mt5_sym, mt5.TIMEFRAME_M1, 0, 100)
            
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
        
        # ——— Collect DOM from fresh connection ———
        _collect_dom()
        
        mt5.shutdown()
        
        if not loop_mode:
            break
        
        # Sleep until next full minute
        now = datetime.now()
        sleep_sec = 60 - now.second
        print(f"   😴 sleeping {sleep_sec}s...", flush=True)
        time.sleep(sleep_sec)



def _collect_dom():
    """Collect DOM from a fresh MT5 connection."""
    import MetaTrader5 as mt5_dom
    import time
    if not mt5_dom.initialize():
        return
    time.sleep(2)  # let terminal settle
    # Find active symbols
    all_syms = mt5_dom.symbols_get()
    if not all_syms:
        mt5_dom.shutdown()
        return
    _refresh_moex_prefixes()
    for ticker, prefix in MOEX_PREFIXES.items():
        sym = find_active_symbol(mt5_dom, prefix, all_syms)
        if not sym:
            continue
        mt5_dom.symbol_select(sym, True)
        mt5_dom.market_book_add(sym)
        time.sleep(2)  # wait for subscription
        book = mt5_dom.market_book_get(sym)
        if not book or len(book) == 0:
            print(f"   [dom] {sym}: no book ({mt5_dom.last_error()})", flush=True)
            continue
        _dom_send_to_pg(sym, book)
        print(f"   [dom] {sym}: {len(book)} rows", flush=True)
    mt5_dom.shutdown()

def _dom_send_to_pg(sym, book):
    """Send DOM snapshot to API."""
    import json, urllib.request, time
    bids = [[round(b.price,4),int(b.volume)] for b in book if b.type==1][:10]
    asks = [[round(b.price,4),int(b.volume)] for b in book if b.type==2][:10]
    if not bids and not asks:
        return
    data = json.dumps({"sym":sym,"time_msc":int(time.time()*1000),
                       "bids":bids,"asks":asks}).encode()
    try:
        req = urllib.request.Request("http://127.0.0.1:8808/api/dom",
            data=data, headers={"Content-Type":"application/json"})
        urllib.request.urlopen(req, timeout=3)
    except Exception as e:
        print(f"   [dom] API error: {e}", flush=True)

if __name__ == '__main__':
    sys.stdout = open(sys.stdout.fileno(), 'w', encoding='utf-8', buffering=1)
    main()
