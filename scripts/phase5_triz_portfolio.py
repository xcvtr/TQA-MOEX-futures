#!/usr/bin/env python3
"""
Phase 5.2: Портфельный 5m тест — ТРИЗ-режим.
Score-based sizing, Kelly, LONG/SHORT пары, adaptive стоп.
Фильтр: только конфиги с OOS WR >= 48%.
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

# ─── Конфиги портфеля — только проверенные ───
PORTFOLIO_CONFIGS = {
    # LONG паттерны с WR >= 48% (из WFA OOS)
    'GL': {'pattern': 'vod', 'direction': 'L', 'hold': 21, 'atr_mult': 2, 'weight': 1.0},
    'RN': {'pattern': 'vou', 'direction': 'L', 'hold': 5,  'atr_mult': 5, 'weight': 1.0},
    'HY': {'pattern': 'vou', 'direction': 'L', 'hold': 5,  'atr_mult': 5, 'weight': 1.0},
    'NM': {'pattern': 'vod', 'direction': 'L', 'hold': 21, 'atr_mult': 3, 'weight': 1.0},
    
    # SHORT паттерны
    'SF': {'pattern': 'vod', 'direction': 'S', 'hold': 8,  'atr_mult': 3, 'weight': 0.5},  # SF — risky, половина веса
    'BR': {'pattern': 'vyf', 'direction': 'S', 'hold': 13, 'atr_mult': 5, 'weight': 1.0},
    'SV': {'pattern': 'vod', 'direction': 'S', 'hold': 5,  'atr_mult': 5, 'weight': 1.0},
    
    # Mixed direction — ставим по направлению
    'AF': {'pattern': 'sm',  'direction': 'L', 'hold': 21, 'atr_mult': 2, 'weight': 1.0},
}

INITIAL_CAPITAL = 1_000_000  # 1M RUB для реалистичного плеча
MIN_POSITION_PCT = 0.05     # мин 5% капитала на позицию
MAX_POSITION_PCT = 0.25     # макс 25% на позицию
MAX_TOTAL_LEVERAGE = 3.0    # макс плечо


def _rolling_zscore(series, window):
    mean = series.rolling(window, min_periods=window).mean()
    std = series.rolling(window, min_periods=window).std()
    z = (series - mean) / std.clip(lower=1e-10)
    return z.fillna(0)


def compute_signal_score(df, pattern_name, direction):
    """Вычислить силу сигнала от 0 до 1 (не бинарно)"""
    df = df.copy()
    df['volume'] = df['volume'].astype(float)
    df['vol_ma'] = df['volume'].rolling(20, min_periods=20).mean().fillna(df['volume'])
    df['vol_ratio'] = df['volume'] / df['vol_ma'].clip(lower=1e-10)
    df['vol_z'] = (df['vol_ratio'] - 1) / 0.5  # z-score вола
    
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
    
    # Score от 0 до 1 — вероятность что паттерн сработал
    df['score'] = 0.0
    
    dir_mult = 1 if direction == 'L' else -1
    
    if pattern_name == 'vod':  # Volume OI Down
        # SHORT-сигнал: объём↑ + OI↓
        vol_score = np.clip((df['vol_ratio'] - 1.5) / 2.0, 0, 1)
        
        if has_oi:
            oi_ma = df['oi_ratio'].rolling(20).mean()
            oi_score = np.clip((oi_ma - df['oi_ratio']) / oi_ma.clip(lower=1e-10) * 2, 0, 1)
        else:
            oi_score = 0.5
        
        df['raw_score'] = vol_score * 0.6 + oi_score * 0.4
    
    elif pattern_name == 'vou':  # Volume OI Up
        vol_score = np.clip((df['vol_ratio'] - 1.5) / 2.0, 0, 1)
        
        if has_oi:
            oi_ma = df['oi_ratio'].rolling(20).mean()
            oi_score = np.clip((df['oi_ratio'] - oi_ma) / oi_ma.clip(lower=1e-10) * 2, 0, 1)
        else:
            oi_score = 0.5
        
        df['raw_score'] = vol_score * 0.6 + oi_score * 0.4
    
    elif pattern_name == 'sm':  # Smart Money
        if has_oi:
            yur_strength = np.clip(abs(df['yur_z']) / 3.0, 0, 1)
            fiz_opposite = np.clip(abs(df['fiz_z']) / 3.0, 0, 1) * (-1 if direction == 'L' else 1)
            df['raw_score'] = yur_strength * 0.7 + np.clip(fiz_opposite, 0, 1) * 0.3
        else:
            df['raw_score'] = np.clip((df['vol_ratio'] - 1.5) / 2.0, 0, 1)
    
    elif pattern_name == 'vyf':  # Volume Yur Flow
        vol_score = np.clip((df['vol_ratio'] - 2.0) / 3.0, 0, 1)
        if has_oi:
            yur_net = df['yur_net'].fillna(0)
            yur_score = np.clip(yur_net / max(yur_net.std(), 1) * dir_mult, 0, 1)
        else:
            yur_score = np.clip((df['close'] - df['close'].shift(1)) / df['close'].shift(1).clip(lower=1) * 100, 0, 1)
        df['raw_score'] = vol_score * 0.5 + yur_score * 0.5
    
    else:
        df['raw_score'] = np.clip((df['vol_ratio'] - 2.5) / 5.0, 0, 1)
    
    # ATR-фильтр: не входить при экстремальной волатильности
    atr_filter = np.clip(1 - (df['atr_pct'] - 0.5) / 2.0, 0, 1)
    df['score'] = df['raw_score'] * atr_filter * np.clip(1 + df['vol_z'] / 5.0, 0.5, 1.5)
    df['score'] = np.clip(df['score'], 0, 1)
    
    return df


def fetch_data_for_tickers(ch, tickers, start='2024-01-01', end='2026-04-30'):
    data = {}
    for sym in tickers:
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
            data[sym] = df
        except Exception as e:
            print(f"  ✗ {sym}: {e}")
    return data


def simulate_triz_portfolio(data, configs, initial_capital=1_000_000):
    """
    ТРИЗ-портфель:
    1. Score-based sizing — вес на основе силы сигнала
    2. Kelly — размер позиции по вероятности выигрыша
    3. Диверсификация — макс 25% на 1 тикер
    4. LONG/SHORT — естественный хедж
    5. Rollover — входим снова после закрытия
    """
    
    for sym, cfg in configs.items():
        if sym not in data:
            continue
        df = data[sym]
        df_out = compute_signal_score(df, cfg['pattern'], cfg['direction'])
        data[sym] = df_out
    
    # ─── Симуляция ───
    capital = initial_capital
    equity_points = [{'time': datetime(2024, 1, 3), 'equity': capital}]
    
    # Для каждого тикера — своя очередь сигналов
    # Но капитал общий
    total_trades = 0
    wins = 0
    losses = 0
    total_pnl = 0
    peak_equity = capital
    max_dd = 0
    
    # Проходим по времени последовательно
    all_times = set()
    for sym, df in data.items():
        for t in df.index:
            all_times.add(t)
    
    all_times = sorted(all_times)
    
    # Активные позиции: {sym: {entry_price, entry_time, atr_at_entry, bars_held, direction, hold, atr_mult, contracts, go}}
    positions = {}
    positions_go_locked = 0  # сумма ГО в активных позициях
    
    # Kelly: на основе исторической WR каждого тикера (запускаем катящееся окно)
    sym_history = defaultdict(lambda: {'wins': 0, 'losses': 0, 'total': 0})
    
    # Рабочий капитал
    working_capital = initial_capital
    
    for current_time in all_times:
        # 1. Проверяем выход из позиций
        exited_symbols = set()
        for sym, pos in list(positions.items()):
            pos['bars_held'] += 1
            bar = data[sym].loc[current_time] if current_time in data[sym].index else None
            if bar is None:
                continue
            
            hit_stop = False
            if pos['direction'] == 'L':
                if bar['low'] <= pos['stop_price']:
                    hit_stop = True
                    exit_price = pos['stop_price']
            else:
                if bar['high'] >= pos['stop_price']:
                    hit_stop = True
                    exit_price = pos['stop_price']
            
            time_exit = pos['bars_held'] >= pos['hold']
            
            if hit_stop or time_exit:
                if not hit_stop:
                    exit_price = bar['close']
                
                # Считаем PnL
                if pos['direction'] == 'L':
                    pnl_pct = (exit_price - pos['entry_price']) / pos['entry_price']
                else:
                    pnl_pct = (pos['entry_price'] - exit_price) / pos['entry_price']
                
                pnl_rub = pnl_pct * pos['go'] * pos['contracts']
                
                working_capital += pnl_rub
                positions_go_locked -= pos['go'] * pos['contracts']
                
                total_trades += 1
                total_pnl += pnl_rub
                if pnl_rub > 0:
                    wins += 1
                    sym_history[sym]['wins'] += 1
                else:
                    losses += 1
                    sym_history[sym]['losses'] += 1
                sym_history[sym]['total'] += 1
                
                exited_symbols.add(sym)
                del positions[sym]
        
        # 2. Проверяем вход в новые позиции
        # Сортируем по score от всех тикеров
        candidates = []
        available_capital = working_capital - positions_go_locked
        
        for sym, df in data.items():
            if sym in positions:
                continue
            if current_time not in df.index:
                continue
            bar = df.loc[current_time]
            
            score = bar.get('score', 0)
            if score < 0.3:  # порог входа
                continue
            
            cfg = configs[sym]
            
            # Kelly fraction
            hist = sym_history[sym]
            if hist['total'] >= 10:
                wr_hist = hist['wins'] / max(hist['total'], 1)
                # Kelly = WR - (1-WR)/RR, где RR ~ 1.2 (средний по MOEX)
                kelly = wr_hist - (1 - wr_hist) / 1.2
                kelly = max(0.05, min(kelly, 0.25))  # clamp 5%-25%
            else:
                kelly = 0.1  # default 10%
            
            # Размер позиции = kelly * score * weight * available_capital
            weight = cfg.get('weight', 1.0)
            position_pct = kelly * score * weight
            position_pct = max(MIN_POSITION_PCT, min(position_pct, MAX_POSITION_PCT))
            
            # Проверка: макс все позиции не больше MAX_TOTAL_LEVERAGE * capital
            max_total_go = working_capital * MAX_TOTAL_LEVERAGE * 0.3
            available_for_new = max_total_go - positions_go_locked
            
            if available_for_new <= 0:
                continue
            
            max_position_rub = available_capital * position_pct
            max_position_rub = min(max_position_rub, available_for_new)
            
            # GO
            go = 1000  # примерно
            # Найдём реальный GO из bar_level_sim если есть
            try:
                from bar_level_sim import TICKER_CONFIGS
                go = TICKER_CONFIGS.get(sym, {}).get('go', 5000)
            except:
                pass
            
            contracts = max(1, int(max_position_rub / go))
            if contracts == 0:
                continue
            
            # ATR-стоп
            atr_val = bar.get('atr', 0)
            if atr_val == 0:
                continue
            
            atr_mult = cfg.get('atr_mult', 2)
            entry_price = bar['close']
            
            if cfg['direction'] == 'L':
                stop_price = entry_price - atr_val * atr_mult
            else:
                stop_price = entry_price + atr_val * atr_mult
            
            candidates.append({
                'symbol': sym,
                'score': score,
                'contracts': contracts,
                'entry_price': entry_price,
                'stop_price': stop_price,
                'direction': cfg['direction'],
                'hold': cfg['hold'],
                'atr_mult': atr_mult,
                'go': go,
            })
        
        # Входим только если есть свободный капитал
        candidates.sort(key=lambda c: c['score'], reverse=True)
        for cand in candidates:
            if sym in positions:
                continue
            cost = cand['contracts'] * cand['go']
            available = working_capital - positions_go_locked
            
            if cost > available:
                # Пробуем 1 контракт
                cand['contracts'] = 1
                cost = cand['go']
            
            if cost > available:
                continue
            
            positions[cand['symbol']] = {
                'entry_price': cand['entry_price'],
                'entry_time': current_time,
                'stop_price': cand['stop_price'],
                'bars_held': 0,
                'direction': cand['direction'],
                'hold': cand['hold'],
                'atr_mult': cand['atr_mult'],
                'contracts': cand['contracts'],
                'go': cand['go'],
            }
            positions_go_locked += cost
        
        # Запись equity
        current_equity = working_capital + sum(
            pos['go'] * pos['contracts'] for pos in positions.values()
        )  # не совсем equity, но для отслеживания
        
        if current_time.hour == 18 and current_time.minute == 45:  # раз в день
            equity_points.append({'time': current_time, 'equity': working_capital})
            
            if working_capital > peak_equity:
                peak_equity = working_capital
            dd = (peak_equity - working_capital) / peak_equity * 100
            if dd > max_dd:
                max_dd = dd
    
    # Итоги
    total_return = (working_capital - initial_capital) / initial_capital * 100
    wr = wins / total_trades * 100 if total_trades > 0 else 0
    calmar = total_return / max_dd / 100 if max_dd > 0 else 0
    
    print(f"\n{'='*60}")
    print(f"ТРИЗ-ПОРТФЕЛЬ 5m — {len(configs)} тикеров")
    print(f"{'='*60}")
    print(f"Начальный капитал: {initial_capital:,.0f} ₽")
    print(f"Конечный капитал:  {working_capital:,.0f} ₽")
    print(f"Доходность:         {total_return:+.1f}%")
    print(f"Макс. просадка:     {max_dd:.1f}%")
    print(f"Calmar:             {calmar:.2f}")
    print(f"Win rate:           {wr:.1f}%")
    print(f"Всего сделок:       {total_trades} (W:{wins} L:{losses})")
    
    days = (max(all_times) - min(all_times)).days if all_times else 365
    years = max(days / 365.25, 0.1)
    annual_return = (working_capital / initial_capital) ** (1 / years) - 1
    print(f"Годовая доходность: {annual_return*100:+.1f}%")
    print(f"Период: {min(all_times)} - {max(all_times)} ({days} дней)")
    
    return {
        'capital': working_capital,
        'return_pct': total_return,
        'max_dd': max_dd,
        'calmar': calmar,
        'wr': wr,
        'total_trades': total_trades,
        'annual_return': annual_return * 100,
    }


if __name__ == '__main__':
    print("=== Phase 5.2: ТРИЗ-портфель 5m ===")
    print(f"Тикеров: {len(PORTFOLIO_CONFIGS)}")
    
    ch = clickhouse_connect.get_client(host='127.0.0.1', port=8123)
    
    print("\nЗагрузка данных...")
    data = fetch_data_for_tickers(ch, list(PORTFOLIO_CONFIGS.keys()))
    
    # Запускаем тест
    result = simulate_triz_portfolio(data, PORTFOLIO_CONFIGS, INITIAL_CAPITAL)
    
    # Сохраняем
    report_dir = 'reports/phase5_triz'
    os.makedirs(report_dir, exist_ok=True)
    
    with open(f'{report_dir}/result.json', 'w') as f:
        json.dump({
            'config': {k: v for k, v in PORTFOLIO_CONFIGS.items() if k in data},
            'result': result,
        }, f, indent=2, ensure_ascii=False)
    
    print(f"\nРезультат: reports/phase5_triz/result.json")
