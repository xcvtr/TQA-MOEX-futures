#!/usr/bin/env python3
"""
restore_trades_v5.py — Пересчёт сделок paper trader с реалистичной симуляцией исполнения.

Использует lib_cvd_divergence для всех общих функций.

Реалистичная модель:
1. Вход — лимитный ордер по entry_price
2. Проверка касания: для LONG low сигнального 5м бара <= entry_price
                      для SHORT high сигнального 5м бара >= entry_price
3. Если не коснулось — сделка не исполнилась (PnL = 0, пропускаем)
4. Выход — на следующем 5м баре по close (рыночный)
5. Slippage: 0.5 тика на вход + 1.0 тик на выход = 1.5 тика round-trip
6. PnL: pnl_rub = pnl_ticks * tick_cost - 1.5 * tick_cost

Источник данных: CSV файлы {symbol}_1m_full.csv (загружены fetch_ohlc_20260626.py)
"""
import os, sys, re
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
import clickhouse_connect

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib_cvd_divergence import (
    TICK, TICK_COST, GO, SYMBOLS, INITIAL_CAPITAL,
    SLIPPAGE_IN_TICKS, SLIPPAGE_OUT_TICKS, ROUND_TRIP_TICKS,
    resample_to_5m, deduplicate_1m,
    calc_entry_price, check_touch, calc_pnl_rub, simulate_trade,
    find_5m_bar,
)

# ── Пути ──────────────────────────────────────────────────────────────────
DATA_DIR = os.path.join(os.path.expanduser("~"), ".hermes", "data", "cvd_paper")
LOG_FILE = os.path.join(DATA_DIR, "trades.log")

# ── ClickHouse ──────────────────────────────────────────────────────────────
CH_HOST = os.environ.get('MOEX_CH_HOST', '10.0.0.64')
CH = None

def get_ch():
    global CH
    if CH is None:
        CH = clickhouse_connect.get_client(host=CH_HOST, database='moex')
    return CH


# ═══════════════════════════════════════════════════════════════════════════
#  1. ЗАГРУЗКА 1М ДАННЫХ С HIGH/LOW
# ═══════════════════════════════════════════════════════════════════════════

def load_1m_full_ohlc(symbol, day_str):
    """Загрузить 1m данные с high/low из CSV и ресемплировать в 5м.
    
    CSV имеет колонки: time, open, high, low, close, vol_b, vol_s
    Использует lib_cvd_divergence.deduplicate_1m() для фильтрации
    мульти-потоковых данных (TOD/TOM/серии).
    
    Возвращает 5m DataFrame.
    """
    csv_path = os.path.join(DATA_DIR, f"{symbol}_1m_full.csv")
    if not os.path.exists(csv_path):
        print(f"  ⚠️  No CSV for {symbol}: {csv_path}")
        return pd.DataFrame()
    
    df = pd.read_csv(csv_path)
    df['time'] = pd.to_datetime(df['time'])
    
    # Фильтр за нужный день
    day_start = pd.Timestamp(day_str)
    day_end = day_start + pd.Timedelta(days=1)
    df = df[(df['time'] >= day_start) & (df['time'] < day_end)]
    
    if df.empty:
        return df
    
    # Используем библиотечную дедупликацию + ресемпл
    # deduplicate=True включает фильтрацию мульти-потоков
    return resample_to_5m(df, deduplicate=True)


# ═══════════════════════════════════════════════════════════════════════════
#  2. ПАРСИНГ ЛОГА СДЕЛОК
# ═══════════════════════════════════════════════════════════════════════════

def parse_dt_fast(ts_str):
    """Быстрый парсинг datetime из строки."""
    ts_str = ts_str.strip()
    if '.' in ts_str:
        ts_str = ts_str.split('.')[0]
    return datetime.strptime(ts_str, '%Y-%m-%d %H:%M:%S')


def parse_trades_from_log(day_str):
    """Парсим лог, извлекаем entry/exit пары только для указанного дня."""
    if not os.path.exists(LOG_FILE):
        print(f"❌ Log file not found: {LOG_FILE}")
        return []
    
    entry_re = re.compile(
        r'\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] TRADE ENTRY: (\S+) (long|short) @ ([\d.]+) \(([\d\- :]+)\)'
    )
    exit_re = re.compile(
        r'\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] TRADE EXIT: (\S+) @ ([\d.]+) pnl=([+-][\d.]+)'
    )
    
    raw_entries = []
    raw_exits = []
    catchup_time = None
    
    with open(LOG_FILE) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            
            if 'CATCHUP' in line:
                match = re.match(r'\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\].*CATCHUP', line)
                if match and catchup_time is None:
                    catchup_time = datetime.strptime(match.group(1), '%Y-%m-%d %H:%M:%S')
                continue
            
            if catchup_time:
                match = re.match(r'\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]', line)
                if match:
                    log_dt = datetime.strptime(match.group(1), '%Y-%m-%d %H:%M:%S')
                    if log_dt >= catchup_time:
                        continue
            
            m = entry_re.match(line)
            if m:
                log_time_str = m.group(1)
                symbol = m.group(2)
                direction = m.group(3)
                price = float(m.group(4))
                entry_time_str = m.group(5).strip()
                log_dt = datetime.strptime(log_time_str, '%Y-%m-%d %H:%M:%S')
                raw_entries.append((log_dt, symbol, direction, price, entry_time_str))
                continue
            
            m = exit_re.match(line)
            if m:
                log_time_str = m.group(1)
                symbol = m.group(2)
                price = float(m.group(3))
                pnl_str = m.group(4)
                log_dt = datetime.strptime(log_time_str, '%Y-%m-%d %H:%M:%S')
                raw_exits.append((log_dt, symbol, price, pnl_str))
    
    # Дедикация entry
    seen_entry = {}
    deduped_entries = []
    for e in raw_entries:
        key = (e[1], e[4])
        if key not in seen_entry:
            seen_entry[key] = len(deduped_entries)
            deduped_entries.append(e)
    
    # Дедикация exit
    seen_exit = set()
    deduped_exits = []
    for x in raw_exits:
        key = (x[1], x[2], x[0])
        if key not in seen_exit:
            seen_exit.add(key)
            deduped_exits.append(x)
    
    exit_first = {}
    for x in deduped_exits:
        key = (x[1], x[2])
        if key not in exit_first:
            exit_first[key] = x
    deduped_exits = sorted(exit_first.values(), key=lambda x: x[0])
    
    # Фильтр за день
    day_dt = datetime.strptime(day_str, '%Y-%m-%d')
    next_day = day_dt + timedelta(days=1)
    
    filtered_entries = [e for e in deduped_entries 
                        if day_dt <= parse_dt_fast(e[4]) < next_day]
    
    # Matching
    used_exits = set()
    trades = []
    
    for e_log_dt, symbol, direction, price, entry_time_str in filtered_entries:
        candidates = [
            (i, x) for i, x in enumerate(deduped_exits)
            if x[1] == symbol and x[0] >= e_log_dt and i not in used_exits
        ]
        candidates.sort(key=lambda c: c[1][0])
        
        if not candidates:
            continue
        
        exit_idx, exit_data = candidates[0]
        used_exits.add(exit_idx)
        
        _, _, exit_price, pnl_str = exit_data
        direction_int = 1 if direction == 'long' else -1
        
        trades.append({
            'symbol': symbol,
            'direction': direction,
            'direction_int': direction_int,
            'entry_price': price,
            'exit_price': exit_price,
            'entry_time_str': entry_time_str,
            'exit_log_time': exit_data[0],
        })
    
    return trades


# ═══════════════════════════════════════════════════════════════════════════
#  3. РАСЧЁТ PnL С ПРОВЕРКОЙ КАСАНИЯ
# ═══════════════════════════════════════════════════════════════════════════

def calc_realistic_pnl(trade, bars_5m):
    """Рассчитать PnL сделки с реалистичной симуляцией.
    
    Использует lib_cvd_divergence.simulate_trade() для проверки касания и расчёта.
    
    Returns: (pnl_rub, executed, touch, reason_str)
    """
    symbol = trade['symbol']
    entry_price = trade['entry_price']
    direction = trade['direction_int']
    entry_time_dt = parse_dt_fast(trade['entry_time_str'])
    
    # 1. Находим сигнальный 5м бар
    sig_idx = find_5m_bar(bars_5m, entry_time_dt)
    if sig_idx is None:
        return 0.0, False, False, f"Signal bar not found for entry_time={entry_time_dt}"
    
    signal_bar = bars_5m.iloc[sig_idx]
    
    # 2. Следующий бар для выхода
    next_bar = bars_5m.iloc[sig_idx + 1] if sig_idx + 1 < len(bars_5m) else bars_5m.iloc[-1]
    
    # 3. Полная симуляция через библиотеку
    result = simulate_trade(
        signal_bar, next_bar, entry_price, direction, symbol,
        slippage_in_ticks=SLIPPAGE_IN_TICKS,
        slippage_out_ticks=SLIPPAGE_OUT_TICKS,
    )
    
    return result['pnl_rub'], result['executed'], result['executed'], result['reason']


# ═══════════════════════════════════════════════════════════════════════════
#  4. ЗАПИСЬ В CLICKHOUSE
# ═══════════════════════════════════════════════════════════════════════════

def ensure_tables():
    ch = get_ch()
    ch.command("""
        CREATE TABLE IF NOT EXISTS moex.strategy_paper_trades (
            id Int32,
            ticker String,
            direction String,
            entry_price Float64,
            exit_price Nullable(Float64),
            entry_time DateTime,
            exit_time Nullable(DateTime),
            pnl_rub Nullable(Float64),
            signal_type String DEFAULT 'cvd_divergence',
            status String DEFAULT 'open',
            strategy String DEFAULT 'cvd_divergence',
            created_at DateTime DEFAULT now(),
            updated_at DateTime DEFAULT now()
        ) ENGINE = MergeTree()
        ORDER BY (ticker, entry_time)
    """)
    ch.command("""
        CREATE TABLE IF NOT EXISTS moex.strategy_portfolio_state (
            strategy String,
            capital Float64 DEFAULT 100000.0,
            peak_capital Float64 DEFAULT 100000.0,
            lots Int32 DEFAULT 1,
            updated_at DateTime DEFAULT now()
        ) ENGINE = MergeTree()
        ORDER BY (strategy, updated_at)
    """)
    print("  ✅ Tables ensured")


def clear_tables():
    ch = get_ch()
    ch.command("TRUNCATE TABLE IF EXISTS moex.strategy_paper_trades")
    ch.command("TRUNCATE TABLE IF EXISTS moex.strategy_portfolio_state")
    print("  ✅ Tables cleared")


# ═══════════════════════════════════════════════════════════════════════════
#  5. MAIN
# ═══════════════════════════════════════════════════════════════════════════

def main():
    day_str = '2026-06-26'
    
    print("=" * 60)
    print("restore_trades_v5.py — Реалистичная симуляция с проверкой касания")
    print(f"  (использует lib_cvd_divergence)")
    print("=" * 60)
    
    # ── 1. Парсим лог ────────────────────────────────────────────────────
    print(f"\n📋 Parsing log for {day_str}...")
    trades = parse_trades_from_log(day_str)
    print(f"  Parsed {len(trades)} trades")
    
    if not trades:
        print("❌ No trades parsed")
        return
    
    # ── 2. Загружаем 1м данные с high/low и ресемплим в 5м ──────────────
    print(f"\n📊 Loading 1m OHLC data and resampling to 5m...")
    df_5m_by_symbol = {}
    for symbol in SYMBOLS:
        df_5m = load_1m_full_ohlc(symbol, day_str)
        if df_5m.empty:
            print(f"  ⚠️  {symbol}: no 5m data for {day_str}")
            continue
        df_5m_by_symbol[symbol] = df_5m
        print(f"  {symbol}: {len(df_5m)} 5m bars")
        if not df_5m.empty:
            print(f"    Range: {df_5m.iloc[0]['time']} .. {df_5m.iloc[-1]['time']}")
    
    # ── 3. Пересчитываем каждую сделку ──────────────────────────────────
    print(f"\n💰 Recalculating PnL with realistic execution (touch-check)...")
    results = []
    
    for trade in trades:
        symbol = trade['symbol']
        df_5m = df_5m_by_symbol.get(symbol)
        
        if df_5m is None or df_5m.empty:
            print(f"  ⚠️  {symbol}: no 5m data, skipping")
            continue
        
        pnl_rub, executed, touch, reason = calc_realistic_pnl(trade, df_5m)
        trade['pnl_rub'] = pnl_rub
        trade['executed'] = executed
        trade['touch'] = touch
        trade['reason'] = reason
        results.append(trade)
        
        entry_dt = parse_dt_fast(trade['entry_time_str'])
        sig_idx = find_5m_bar(df_5m, entry_dt)
        bar_info = ""
        if sig_idx is not None:
            bar = df_5m.iloc[sig_idx]
            bar_info = f" | sig_bar: H={bar['high']:.4f} L={bar['low']:.4f} C={bar['close']:.4f}"
        print(f"  {reason}{bar_info}")
    
    # ── 4. Агрегация ────────────────────────────────────────────────────
    executed_trades = [t for t in results if t['executed']]
    skipped_trades = [t for t in results if not t['executed']]
    
    print(f"\n{'='*60}")
    print(f"📊 ИТОГИ СИМУЛЯЦИИ")
    print(f"{'='*60}")
    print(f"  Всего сделок: {len(results)}")
    print(f"  Исполнилось:  {len(executed_trades)}")
    print(f"  Не исполнилось (no touch): {len(skipped_trades)}")
    
    if skipped_trades:
        print(f"\n  ❌ Сделки, не исполнившиеся из-за отсутствия касания:")
        for t in skipped_trades:
            entry_dt = parse_dt_fast(t['entry_time_str'])
            print(f"    {t['symbol']:4s} {t['direction']:5s} entry={t['entry_price']:.4f} time={entry_dt}")
    
    if executed_trades:
        total_pnl = sum(t['pnl_rub'] for t in executed_trades)
        print(f"\n  PnL по символам:")
        symbol_pnl = {}
        for t in executed_trades:
            sym = t['symbol']
            if sym not in symbol_pnl:
                symbol_pnl[sym] = {'count': 0, 'pnl': 0.0}
            symbol_pnl[sym]['count'] += 1
            symbol_pnl[sym]['pnl'] += t['pnl_rub']
        
        for sym in sorted(symbol_pnl.keys()):
            info = symbol_pnl[sym]
            print(f"    {sym:4s}: {info['count']:2d} trades, PnL = {info['pnl']:>+10.2f} ₽")
        
        print(f"\n  Total PnL: {total_pnl:>+10.2f} ₽")
        final_capital = INITIAL_CAPITAL + total_pnl
        print(f"  Начальный капитал: {INITIAL_CAPITAL:>10,.2f} ₽")
        print(f"  Финальный капитал: {final_capital:>10,.2f} ₽")
    
    print(f"\n  Сравнение с предыдущим (restore_trades_v4):")
    print(f"    Старый финальный капитал: 134,473.25 ₽ (Total PnL: +34,473.25)")
    if executed_trades:
        print(f"    Новый финальный капитал:  {final_capital:>10,.2f} ₽")
    
    # ── 5. Запись в ClickHouse ──────────────────────────────────────────
    print(f"\n💾 Writing to ClickHouse...")
    ensure_tables()
    clear_tables()
    
    ch = get_ch()
    capital = INITIAL_CAPITAL
    peak_capital = capital
    trade_id = 0
    
    for trade in results:
        if not trade['executed']:
            continue
        
        trade_id += 1
        symbol = trade['symbol']
        direction = trade['direction']
        entry_price = trade['entry_price']
        entry_time_dt = parse_dt_fast(trade['entry_time_str'])
        pnl_rub = trade['pnl_rub']
        
        # Exit price и exit time из 5м данных
        df_5m = df_5m_by_symbol[symbol]
        sig_idx = find_5m_bar(df_5m, entry_time_dt)
        if sig_idx is not None and sig_idx + 1 < len(df_5m):
            exit_bar = df_5m.iloc[sig_idx + 1]
            exit_price = float(exit_bar['close'])
            exit_time = exit_bar['time'].to_pydatetime()
        else:
            exit_price = trade['exit_price']
            exit_time = trade['exit_log_time']
        
        ch.command(f"""
            INSERT INTO moex.strategy_paper_trades 
            (id, ticker, direction, entry_price, exit_price, entry_time, exit_time, pnl_rub, signal_type, status, strategy)
            VALUES ({trade_id}, '{symbol}', '{direction}', {entry_price:.6f}, {exit_price:.6f}, 
                    '{entry_time_dt.strftime('%Y-%m-%d %H:%M:%S')}', '{exit_time.strftime('%Y-%m-%d %H:%M:%S')}', 
                    {pnl_rub:.2f}, 'cvd_divergence', 'closed', 'cvd_divergence')
        """)
        
        capital += pnl_rub
        peak_capital = max(peak_capital, capital)
        
        ch.command(f"""
            INSERT INTO moex.strategy_portfolio_state (strategy, capital, peak_capital, lots, updated_at)
            VALUES ('cvd_divergence', {capital:.2f}, {peak_capital:.2f}, 1, '{exit_time.strftime('%Y-%m-%d %H:%M:%S')}')
        """)
    
    # ── 6. Финальная верификация ────────────────────────────────────────
    print(f"\n✅ Восстановление завершено")
    print(f"   Сделок в БД: {trade_id}")
    print(f"   Начальный капитал: {INITIAL_CAPITAL:,.2f}")
    print(f"   Финальный капитал: {capital:,.2f}")
    print(f"   Общий PnL: {capital - INITIAL_CAPITAL:+,.2f}")
    
    r = ch.query("SELECT count() FROM moex.strategy_paper_trades")
    trade_cnt = r.result_rows[0][0]
    r = ch.query("SELECT count() FROM moex.strategy_portfolio_state")
    snap_cnt = r.result_rows[0][0]
    r = ch.query("SELECT coalesce(sum(pnl_rub), 0) FROM moex.strategy_paper_trades WHERE status='closed'")
    total_pnl_db = float(r.result_rows[0][0])
    
    print(f"\n🔍 Verification:")
    print(f"   Trades in DB: {trade_cnt}")
    print(f"   Snapshots in DB: {snap_cnt}")
    print(f"   Total PnL in DB: {total_pnl_db:+,.2f}")
    
    print(f"\n   Все сделки в БД:")
    r = ch.query("""
        SELECT id, ticker, direction, entry_price, exit_price, 
               entry_time, exit_time, pnl_rub, status
        FROM moex.strategy_paper_trades
        ORDER BY entry_time
    """)
    for row in r.result_rows:
        print(f"     {row[0]:3d} | {row[1]:4s} {row[2]:5s} | "
              f"entry={row[3]:>10.4f} exit={row[4]:>10.4f} | "
              f"pnl={row[7]:>+8.2f} | {row[8]}")
    
    print(f"\n   PnL по символам:")
    r = ch.query("""
        SELECT ticker, count(), sum(pnl_rub), avg(pnl_rub), 
               min(pnl_rub), max(pnl_rub)
        FROM moex.strategy_paper_trades
        WHERE status='closed'
        GROUP BY ticker
        ORDER BY ticker
    """)
    for row in r.result_rows:
        print(f"     {row[0]:4s}: {row[1]:3d} trades, pnl={row[2]:>+8.2f}, "
              f"avg={row[3]:>+7.2f}, min={row[4]:>+8.2f}, max={row[5]:>+8.2f}")


if __name__ == '__main__':
    main()
