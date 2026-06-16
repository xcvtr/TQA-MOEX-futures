#!/usr/bin/env python3
"""
Phase 5: Портфельный 5m тест — 10+ тикеров с WFA-подтверждёнными конфигами.
Bar-level OHLCV симуляция с ГО, ATR-стопом, реинвестом, Kelly sizing.
"""
import json
import sys
import os
from datetime import datetime, timedelta
from collections import defaultdict

import numpy as np
import pandas as pd
import clickhouse_connect

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bar_level_sim import BarLevelPortfolio, TICKER_CONFIGS, TICKER_PRIORITY

# ─── Конфиги портфеля (из phase2_fullscan.json, топ-10 уникальных тикеров по Calmar OOS) ───
PORTFOLIO_CONFIGS = {
    'SF': {'pattern': 'vod', 'direction': 'S', 'hold': 21, 'atr_mult': 2},
    'GL': {'pattern': 'vod', 'direction': 'L', 'hold': 21, 'atr_mult': 2},
    'AL': {'pattern': 'vou', 'direction': 'L', 'hold': 21, 'atr_mult': 2},
    'NG': {'pattern': 'vou', 'direction': 'L', 'hold': 5,  'atr_mult': 5},
    'RN': {'pattern': 'vou', 'direction': 'L', 'hold': 8,  'atr_mult': 3},
    'BR': {'pattern': 'vyf', 'direction': 'S', 'hold': 13, 'atr_mult': 5},
    'SV': {'pattern': 'vod', 'direction': 'S', 'hold': 5,  'atr_mult': 5},
    'AF': {'pattern': 'sm',  'direction': 'L', 'hold': 21, 'atr_mult': 2},
    'HY': {'pattern': 'vou', 'direction': 'L', 'hold': 5,  'atr_mult': 5},
    'NM': {'pattern': 'vod', 'direction': 'L', 'hold': 21, 'atr_mult': 3},
    'SR': {'pattern': 'sm',  'direction': 'L', 'hold': 8,  'atr_mult': 5},
    'Si': {'pattern': 'vyf', 'direction': 'L', 'hold': 13, 'atr_mult': 2},
}

INITIAL_CAPITAL = 100_000  # 100K RUB
MIN_CAPITAL = 50_000       # стоп если капитал упал ниже
MAX_LEVERAGE = 3.0          # макс плечо по портфелю

# ─── Паттерны ───
PATTERN_FUNCS = {}

def _rolling_zscore(series, window):
    """Rolling z-score с гарантией что не делим на 0"""
    mean = series.rolling(window, min_periods=window).mean()
    std = series.rolling(window, min_periods=window).std()
    z = (series - mean) / std.clip(lower=1e-10)
    return z.fillna(0)

def compute_patterns(df, pattern_name):
    """Вычислить сигналы для паттерна"""
    df = df.copy()
    
    # Базовые признаки
    df['volume'] = df['volume'].astype(float)
    df['vol_ma'] = df['volume'].rolling(20, min_periods=20).mean().fillna(df['volume'])
    df['vol_z'] = (df['volume'] - df['vol_ma']) / df['vol_ma'].clip(lower=1e-10)
    df['vol_ratio'] = df['volume'] / df['vol_ma'].clip(lower=1e-10)
    
    # OI признаки (если есть)
    has_oi = all(c in df.columns for c in ['fiz_buy', 'fiz_sell', 'yur_buy', 'yur_sell'])
    
    if has_oi:
        df['fiz_net'] = df['fiz_buy'].fillna(0) - df['fiz_sell'].fillna(0)
        df['yur_net'] = df['yur_buy'].fillna(0) - df['yur_sell'].fillna(0)
        df['fiz_z'] = _rolling_zscore(df['fiz_net'], 20)
        df['yur_z'] = _rolling_zscore(df['yur_net'], 20)
        df['oi_ratio'] = (df['yur_buy'].fillna(0) + df['yur_sell'].fillna(0)) / \
                         (df['fiz_buy'].fillna(0) + df['fiz_sell'].fillna(0) + 1)
    
    # ATR
    df['prev_close'] = df['close'].shift(1)
    df['atr_raw'] = np.maximum(
        df['high'] - df['low'],
        np.maximum(
            (df['high'] - df['prev_close']).abs(),
            (df['low'] - df['prev_close']).abs()
        ))
    df['atr'] = df['atr_raw'].rolling(14, min_periods=14).mean().bfill().fillna(0)
    df['atr_pct'] = df['atr'] / df['close'].clip(lower=1) * 100
    
    # Сигналы
    df['signal'] = 0
    
    if pattern_name == 'vou':   # Volume OI Up
        # Объём растёт, OI растёт
        vol_up = df['vol_ratio'] > 1.5
        oi_up = df['oi_ratio'] > df['oi_ratio'].rolling(20).mean() if has_oi else True
        df.loc[vol_up & oi_up, 'signal'] = 1
        
    elif pattern_name == 'vod':  # Volume OI Down
        vol_up = df['vol_ratio'] > 1.5
        oi_down = df['oi_ratio'] < df['oi_ratio'].rolling(20).mean() if has_oi else True
        df.loc[vol_up & oi_down, 'signal'] = 1
        
    elif pattern_name == 'sm':   # Smart Money
        if has_oi:
            sm_cond = (df['yur_z'] > 1.5) & (df['fiz_z'] < -1.0)
            df.loc[sm_cond, 'signal'] = 1
        else:
            df['signal'] = 0
            
    elif pattern_name == 'vyf':  # Volume Yur Flow
        if has_oi:
            vol_up = df['vol_ratio'] > 2.0
            yur_bull = df['yur_net'] > 0
            df.loc[vol_up & yur_bull, 'signal'] = 1
        else:
            vol_up = df['vol_ratio'] > 2.5
            up_close = df['close'] > df['close'].shift(1)
            df.loc[vol_up & up_close, 'signal'] = 1
    
    else:  # fev — Fallo Extremo Volume
        vol_extreme = df['vol_z'] > 3.0
        df.loc[vol_extreme, 'signal'] = 1
    
    return df


def fetch_data_for_tickers(ch, tickers, start='2024-01-01', end='2026-04-30'):
    """Загрузить OHLCV+OI данные для всех тикеров из ClickHouse"""
    data = {}
    
    for sym in tickers:
        if sym not in TICKER_CONFIGS:
            print(f"  ⚠ {sym}: нет конфига (GO/minstep), пропускаю")
            continue
        
        cfg = TICKER_CONFIGS[sym]
        
        query = f"""
            SELECT p.time, p.open, p.high, p.low, p.close, p.volume,
                   o.fiz_buy, o.fiz_sell, o.yur_buy, o.yur_sell
            FROM moex.prices_5m p
            LEFT JOIN moex.prices_5m_oi o 
                ON p.time = o.time AND p.symbol = o.symbol
            WHERE p.symbol = '{sym}'
              AND p.time >= '{start}'
              AND p.time <= '{end}'
            ORDER BY p.time
        """
        
        try:
            result = ch.query(query)
            rows = result.result_rows
            if not rows:
                print(f"  ⚠ {sym}: нет данных")
                continue
            
            cols = ['time', 'open', 'high', 'low', 'close', 'volume',
                    'fiz_buy', 'fiz_sell', 'yur_buy', 'yur_sell']
            df = pd.DataFrame(rows, columns=cols)
            df['time'] = pd.to_datetime(df['time'])
            df.set_index('time', inplace=True)
            
            # Тикер информация
            df.attrs['go'] = cfg.get('go', 5000)
            df.attrs['minstep'] = cfg.get('minstep', 0.01)
            df.attrs['tick_rub'] = cfg.get('tick_rub', 1.0)
            
            data[sym] = df
            print(f"  ✓ {sym}: {len(df)} bars, GO={df.attrs['go']}")
        except Exception as e:
            print(f"  ✗ {sym}: ошибка: {e}")
    
    return data


def simulate_portfolio(data, configs, initial_capital=100000):
    """Простая последовательная симуляция портфеля с GO, ATR-стопом, реинвестом"""
    
    # Для каждого тикера генерируем сигналы и сделки
    all_trades = {}
    
    for sym, cfg in configs.items():
        if sym not in data:
            continue
        
        df = data[sym].copy()
        go = df.attrs['go']
        minstep = df.attrs['minstep']
        tick_rub = df.attrs['tick_rub']
        
        # Вычисляем паттерны
        df = compute_patterns(df, cfg['pattern'])
        
        # Генерируем сигналы
        direction = cfg['direction'].upper()
        hold = cfg['hold']
        atr_mult = cfg['atr_mult']
        
        # Сигнал: direction L → сигнал=1 лонг, S → сигнал=-1 шорт
        signal_col = df['signal'].copy()
        if direction == 'S':
            signal_col = -signal_col
        
        # Сделки
        trades = []
        in_position = False
        entry_bar = 0
        entry_price = 0
        atr_at_entry = 0
        bars_held = 0
        stop_price = 0
        
        for i in range(len(df)):
            if in_position:
                bars_held += 1
                
                # Проверка стопа
                if direction == 'L':
                    hit_stop = df['low'].iloc[i] <= stop_price
                else:
                    hit_stop = df['high'].iloc[i] >= stop_price
                
                exit_signal = (bars_held >= hold) or hit_stop or \
                              (direction == 'L' and signal_col.iloc[i] < 0) or \
                              (direction == 'S' and signal_col.iloc[i] > 0)
                
                if exit_signal:
                    exit_price = stop_price if hit_stop else df['close'].iloc[i]
                    exit_time = df.index[i]
                    
                    # PnL
                    if direction == 'L':
                        pnl_pct = (exit_price - entry_price) / entry_price
                    else:
                        pnl_pct = (entry_price - exit_price) / entry_price
                    
                    pnl_rub = pnl_pct * go  # возврат на ГО
                    
                    trades.append({
                        'entry_time': df.index[entry_bar],
                        'exit_time': exit_time,
                        'entry_price': entry_price,
                        'exit_price': exit_price,
                        'direction': direction,
                        'pnl_pct': pnl_pct,
                        'pnl_rub': pnl_rub,
                        'bars_held': bars_held,
                        'hit_stop': hit_stop,
                    })
                    
                    in_position = False
                    bars_held = 0
                    
            if not in_position:
                if direction == 'L' and signal_col.iloc[i] == 1:
                    in_position = True
                    entry_bar = i
                    entry_price = df['close'].iloc[i]
                    bars_held = 0
                    atr_val = df['atr'].iloc[i]
                    stop_price = entry_price - atr_val * atr_mult
                    
                elif direction == 'S' and signal_col.iloc[i] == -1:
                    in_position = True
                    entry_bar = i
                    entry_price = df['close'].iloc[i]
                    bars_held = 0
                    atr_val = df['atr'].iloc[i]
                    stop_price = entry_price + atr_val * atr_mult
        
        all_trades[sym] = trades
        print(f"  {sym} ({cfg['pattern']}_{direction} h={hold} a={atr_mult}): "
              f"{len(trades)} сделок")
    
    # ─── Симуляция портфеля ───
    capital = initial_capital
    equity_curve = [capital]
    daily_pnl = []
    
    # Собираем все сделки с временными метками
    all_trades_timeline = []
    for sym, trades in all_trades.items():
        for t in trades:
            t['symbol'] = sym
            all_trades_timeline.append(t)
    
    all_trades_timeline.sort(key=lambda t: t['entry_time'])
    
    # Симуляция: входим когда есть капитал
    active_positions = {}  # {symbol: {entry_pnl_pct, entry_capital}}
    portfolio_pnl_log = []
    
    # Группируем сделки по дню для расчёта ежедневного PnL
    from collections import defaultdict
    daily_pnls = defaultdict(list)
    
    capital_used = 0
    
    for t in all_trades_timeline:
        sym = t['symbol']
        go = data[sym].attrs['go']
        
        # Сколько контрактов можем купить
        max_contracts = max(1, int((capital - capital_used) * 0.2 / go))
        
        if max_contracts == 0:
            continue
        
        contracts = max_contracts
        
        # PnL в рублях с учётом контрактов
        entry_go_cost = contracts * go
        pnl_total_rub = t['pnl_rub'] * contracts
        
        # Записываем дневной PnL
        exit_day = t['exit_time'].strftime('%Y-%m-%d')
        daily_pnls[exit_day].append({
            'symbol': sym,
            'pnl_rub': pnl_total_rub,
            'direction': t['direction'],
            'entry_price': t['entry_price'],
            'exit_price': t['exit_price'],
        })
    
    # Рассчитываем капитал по дням
    sorted_days = sorted(daily_pnls.keys())
    for day in sorted_days:
        day_pnl = sum(d['pnl_rub'] for d in daily_pnls[day])
        capital += day_pnl
        equity_curve.append(capital)
        daily_pnl.append({'day': day, 'pnl': day_pnl, 'capital': capital})
    
    # Метрики
    total_return = (capital - initial_capital) / initial_capital * 100
    
    # Max DD
    peak = initial_capital
    max_dd = 0
    for v in equity_curve:
        if v > peak:
            peak = v
        dd = (peak - v) / peak
        if dd > max_dd:
            max_dd = dd
    
    # Win rate по всем сделкам (одна сделка = один контракт)
    total_trades = 0
    wins = 0
    for day_data in daily_pnls.values():
        for d in day_data:
            total_trades += 1
            if d['pnl_rub'] > 0:
                wins += 1
    
    wr = wins / total_trades * 100 if total_trades > 0 else 0
    calmar = (total_return / 100) / max_dd if max_dd > 0 else 0
    
    print(f"\n{'='*60}")
    print(f"РЕЗУЛЬТАТЫ ПОРТФЕЛЯ 5m — {len(configs)} тикеров")
    print(f"{'='*60}")
    print(f"Начальный капитал: {initial_capital:,.0f} ₽")
    print(f"Конечный капитал:  {capital:,.0f} ₽")
    print(f"Доходность:         {total_return:+.1f}%")
    print(f"Макс. просадка:     {max_dd*100:.1f}%")
    print(f"Calmar:             {calmar:.2f}")
    print(f"Win rate:           {wr:.1f}%")
    print(f"Всего сделок:       {total_trades}")
    print(f"Период:             {min(t['entry_time'] for t in all_trades_timeline).strftime('%Y-%m-%d')} - {max(t['entry_time'] for t in all_trades_timeline).strftime('%Y-%m-%d')}" if all_trades_timeline else "")
    
    # Годовая доходность
    if all_trades_timeline:
        start_t = min(t['entry_time'] for t in all_trades_timeline)
        end_t = max(t['exit_time'] for t in all_trades_timeline)
        days = (end_t - start_t).days
        years = max(days / 365.25, 0.1)
        annual_return = (capital / initial_capital) ** (1 / years) - 1
        print(f"Годовая доходность: {annual_return*100:+.1f}%")
    
    print(f"\nЛучшие/худшие сделки по тикерам:")
    sym_stats = defaultdict(lambda: {'total': 0, 'wins': 0, 'pnl': 0})
    for day_data in daily_pnls.values():
        for d in day_data:
            sym_stats[d['symbol']]['total'] += 1
            sym_stats[d['symbol']]['pnl'] += d['pnl_rub']
            if d['pnl_rub'] > 0:
                sym_stats[d['symbol']]['wins'] += 1
    
    for sym, stats in sorted(sym_stats.items(), key=lambda x: x[1]['pnl'], reverse=True):
        wr_s = stats['wins'] / stats['total'] * 100 if stats['total'] > 0 else 0
        print(f"  {sym}: {stats['pnl']:+,.0f} ₽, WR={wr_s:.0f}%, {stats['total']} сделок")
    
    return {
        'capital': capital,
        'return_pct': total_return,
        'max_dd': max_dd * 100,
        'calmar': calmar,
        'wr': wr,
        'total_trades': total_trades,
        'annual_return': annual_return * 100 if all_trades_timeline else 0,
        'equity_curve': equity_curve,
        'daily_pnl': daily_pnl,
        'sym_stats': dict(sym_stats),
    }


if __name__ == '__main__':
    print("=== Phase 5: Портфельный 5m тест ===")
    print(f"Тикеров в портфеле: {len(PORTFOLIO_CONFIGS)}")
    print(f"Конфигурация:")
    for sym, cfg in PORTFOLIO_CONFIGS.items():
        print(f"  {sym}: {cfg['pattern']}_{cfg['direction']} h={cfg['hold']} a={cfg['atr_mult']}")
    
    # Подключаемся к CH
    print("\nПодключение к ClickHouse...")
    ch = clickhouse_connect.get_client(host='127.0.0.1', port=8123)
    
    # Загружаем данные
    print("\nЗагрузка данных...")
    data = fetch_data_for_tickers(ch, list(PORTFOLIO_CONFIGS.keys()))
    
    print(f"\nЗагружено {len(data)}/{len(PORTFOLIO_CONFIGS)} тикеров")
    
    # Запускаем портфельный тест
    result = simulate_portfolio(data, PORTFOLIO_CONFIGS, INITIAL_CAPITAL)
    
    # Сохраняем результат
    output = {
        'config': {k: v for k, v in PORTFOLIO_CONFIGS.items() if k in data},
        'result': {
            'capital': result['capital'],
            'return_pct': result['return_pct'],
            'max_dd': result['max_dd'],
            'calmar': result['calmar'],
            'wr': result['wr'],
            'total_trades': result['total_trades'],
            'annual_return': result['annual_return'],
        }
    }
    with open('reports/phase5_portfolio_5m_result.json', 'w') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    
    print(f"\nРезультат сохранён в reports/phase5_portfolio_5m_result.json")
