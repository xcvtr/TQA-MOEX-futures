#!/usr/bin/env python3
"""
Честный тест пирамидинга на BR (Brent).
OHLCV + ГО + ATR*1.0 + bar-level + пирамида 3x.
Параметры: V1, W=40, T=2.0, hold=10 (из BR pre-recovery OOS).
БЕЗ look-ahead: вход по open следующего бара, стоп по high/low.
"""
import sys, os, json
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import clickhouse_connect
import numpy as np
import pandas as pd
from config import CH_HOST, CH_PORT, CH_DB

COMMISSION = 2.0
CAPITAL = 200000.0
PYRAMID_MAX = 3
PYRAMID_SAME_DIR = True

# Параметры BR из pre-recovery OOS
TICKER = 'BR'
GO = 17228
LOT = 10
WINDOW = 40
THRESHOLD = 2.0
HOLD_BARS = 10
ATR_MULT = 1.0
MIN_STOP_PCT = 0.01
MU = 0.50  # доля капитала под ГО


def get_ch():
    return clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)


def load_data(ch, ticker, start='2024-01-01', end='2026-06-01'):
    query = f"""
    SELECT p.time, p.open, p.high, p.low, p.close, p.volume,
           o.fiz_buy, o.fiz_sell, o.yur_buy, o.yur_sell, o.total_oi
    FROM moex.prices_5m_oi AS o
    INNER JOIN moex.prices_5m AS p ON p.symbol = o.symbol AND p.time = o.time
    WHERE o.symbol = {{t:String}} AND p.symbol = {{t:String}}
      AND p.time >= {{start:String}} AND p.time < {{end:String}}
    ORDER BY p.time
    """
    params = {"t": ticker, "start": start, "end": end}
    rows = ch.query(query, parameters=params).result_rows
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=[
        "time","open","high","low","close","volume",
        "fiz_buy","fiz_sell","yur_buy","yur_sell","total_oi"
    ])
    return df


def calc_atr(df, p=14):
    prev = df['close'].shift(1)
    tr = pd.concat([
        df['high']-df['low'], (df['high']-prev).abs(), (df['low']-prev).abs()
    ], axis=1).max(axis=1)
    return tr.rolling(p, min_periods=p).mean().bfill().fillna(0)


def compute_signal(df, w=WINDOW, t=THRESHOLD):
    """V1: fiz_net z-score"""
    d = df.copy()
    d['fiz_net'] = d['fiz_buy'].fillna(0) - d['fiz_sell'].fillna(0)
    d['yur_net'] = d['yur_buy'].fillna(0) - d['yur_sell'].fillna(0)

    mu = d['fiz_net'].rolling(w, min_periods=w).mean()
    std = d['fiz_net'].rolling(w, min_periods=w).std().clip(lower=1e-10)
    d['fiz_z'] = (d['fiz_net'] - mu) / std

    d['atr14'] = calc_atr(d)
    d['atr_pct'] = d['atr14'] / d['close'].clip(lower=1) * 100

    long_sig = d['fiz_z'] > t
    short_sig = d['fiz_z'] < -t

    return d, long_sig.values, short_sig.values


def run_test():
    ch = get_ch()
    print(f"Загрузка {TICKER} данных...")
    df = load_data(ch, TICKER, '2024-01-01', '2026-06-01')
    if df is None or len(df) < 5000:
        print("НЕТ ДАННЫХ")
        return

    print(f"Баров: {len(df)} ({df['time'].min()} → {df['time'].max()})")

    print("Вычисление сигналов...")
    d, long_sig, short_sig = compute_signal(df)
    opens = d['open'].values
    highs = d['high'].values
    lows = d['low'].values
    closes = d['close'].values
    times = d['time'].values
    atr_pct = d['atr_pct'].values

    print("Запуск симуляции с пирамидой...")

    # === BASE (без пирамиды) ===
    balance_base = CAPITAL
    peak_base = CAPITAL
    max_dd_base = 0.0
    trades_base = 0
    wins_base = 0

    open_pos = None  # {entry, stop, direction, bars_left}
    prev_close = None

    for i in range(WINDOW, len(df)):
        ts = times[i]
        # Проверка времени (MOEX: 7:00-23:45)
        if hasattr(ts, 'hour'):
            h = ts.hour
        else:
            h = pd.Timestamp(ts).hour
        if h < 7 or h >= 23:
            continue

        open_p = opens[i]
        high_p = highs[i]
        low_p = lows[i]
        close_p = closes[i]
        atr_p = atr_pct[i]

        if open_pos is not None:
            dir_m = 1 if open_pos['dir'] == 'L' else -1
            # Проверка стопа
            hit = False
            exit_price = close_p
            if open_pos['dir'] == 'L' and low_p <= open_pos['stop']:
                hit = True
                exit_price = open_pos['stop']
            elif open_pos['dir'] == 'S' and high_p >= open_pos['stop']:
                hit = True
                exit_price = open_pos['stop']
            elif open_pos['bars_left'] <= 0:
                hit = True
                # exit_price = close_p

            if hit:
                pnl_pct = (exit_price / open_pos['entry'] - 1) * dir_m
                pnl_rub = pnl_pct * open_pos['go_locked']
                balance_base += pnl_rub
                trades_base += 1
                if pnl_rub > 0:
                    wins_base += 1
                open_pos = None

        if open_pos is None:
            # Проверка сигнала (вход по OPEN следующего бара)
            if long_sig[i-1]:
                dir_ = 'L'
            elif short_sig[i-1]:
                dir_ = 'S'
            else:
                continue

            stop_pct = max(atr_p / 100 * ATR_MULT, MIN_STOP_PCT)
            stop_price = open_p * (1 - stop_pct) if dir_ == 'L' else open_p * (1 + stop_pct)

            # Размер позиции
            go_locked = balance_base * MU
            contracts = max(1, int(go_locked / GO))
            go_locked_actual = contracts * GO

            open_pos = {
                'entry': open_p,
                'stop': stop_price,
                'dir': dir_,
                'bars_left': HOLD_BARS,
                'go_locked': go_locked_actual,
            }

        else:
            open_pos['bars_left'] -= 1

    # === PYRAMID (с пирамидой) ===
    balance_pyr = CAPITAL
    peak_pyr = CAPITAL
    max_dd_pyr = 0.0
    trades_pyr = 0
    wins_pyr = 0
    all_trades = []

    positions = []  # список позиций [{entry, stop, dir, bars_left, go_locked}]

    for i in range(WINDOW, len(df)):
        ts = times[i]
        if hasattr(ts, 'hour'):
            h = ts.hour
        else:
            h = pd.Timestamp(ts).hour
        if h < 7 or h >= 23:
            continue

        open_p = opens[i]
        high_p = highs[i]
        low_p = lows[i]
        close_p = closes[i]
        atr_p = atr_pct[i]

        # === ВЫХОДЫ ===
        to_remove = []
        for pos in positions:
            dir_m = 1 if pos['dir'] == 'L' else -1
            hit = False
            exit_price = close_p

            if pos['dir'] == 'L' and low_p <= pos['stop']:
                hit = True
                exit_price = pos['stop']
            elif pos['dir'] == 'S' and high_p >= pos['stop']:
                hit = True
                exit_price = pos['stop']
            elif pos['bars_left'] <= 0:
                hit = True

            if hit:
                pnl_pct = (exit_price / pos['entry'] - 1) * dir_m
                pnl_rub = pnl_pct * pos['go_locked']
                balance_pyr += pnl_rub
                trades_pyr += 1
                if pnl_rub > 0: wins_pyr += 1
                all_trades.append({
                    'entry_ts': str(pos['entry_ts']),
                    'exit_ts': str(ts),
                    'dir': pos['dir'],
                    'pnl_pct': round(pnl_pct * 100, 2),
                    'pnl_rub': round(pnl_rub, 2),
                    'reason': 'stop' if hit and exit_price != close_p else 'time',
                    'entry': round(pos['entry'], 2),
                    'exit': round(exit_price, 2),
                })
                to_remove.append(pos)

        for p in to_remove:
            positions.remove(p)

        # === MTM EQUITY ===
        mtm_pnl = 0
        for pos in positions:
            dir_m = 1 if pos['dir'] == 'L' else -1
            mtm_pnl += dir_m * (close_p / pos['entry'] - 1) * pos['go_locked']

        total_eq = balance_pyr + mtm_pnl
        if total_eq > peak_pyr:
            peak_pyr = total_eq
        dd = (peak_pyr - total_eq) / peak_pyr
        if dd > max_dd_pyr:
            max_dd_pyr = dd

        # === ВХОДЫ ===
        locked_go = sum(p['go_locked'] for p in positions)
        avail = balance_pyr - locked_go
        if avail <= 0:
            continue

        if long_sig[i-1]:
            dir_ = 'L'
        elif short_sig[i-1]:
            dir_ = 'S'
        else:
            continue

        # Проверка пирамиды
        if len(positions) >= PYRAMID_MAX:
            continue
        if PYRAMID_SAME_DIR and positions:
            first_dir = positions[0]['dir']
            if dir_ != first_dir:
                continue

        stop_pct = max(atr_p / 100 * ATR_MULT, MIN_STOP_PCT)
        stop_price = open_p * (1 - stop_pct) if dir_ == 'L' else open_p * (1 + stop_pct)

        # Размер на одну позицию пирамиды
        capital_per_pos = avail * MU / PYRAMID_MAX
        contracts = max(1, int(capital_per_pos / GO))
        go_locked_actual = contracts * GO

        # Не тратить больше чем доступно
        if go_locked_actual > avail:
            go_locked_actual = int(avail / GO) * GO
            if go_locked_actual < GO:
                continue

        positions.append({
            'entry': open_p,
            'stop': stop_price,
            'dir': dir_,
            'bars_left': HOLD_BARS,
            'go_locked': go_locked_actual,
            'entry_ts': ts,
        })

    # === РЕЗУЛЬТАТЫ ===
    print("\n" + "="*65)
    print(f"{'МЕТРИКА':<30} {'BASE':>15} {'PYRAMID':>15}")
    print("="*65)

    base_return = (balance_base - CAPITAL) / CAPITAL * 100
    pyr_return = (balance_pyr - CAPITAL) / CAPITAL * 100

    # CAGR
    days = (pd.Timestamp(times[-1]) - pd.Timestamp(times[WINDOW])).days
    years = max(days / 365.25, 0.1)
    base_cagr = ((balance_base / CAPITAL) ** (1/years) - 1) * 100 if balance_base > 0 else -100
    pyr_cagr = ((balance_pyr / CAPITAL) ** (1/years) - 1) * 100 if balance_pyr > 0 else -100

    base_calmar = base_return / 100 / max(max_dd_base, 0.001)
    pyr_calmar = pyr_return / 100 / max(max_dd_pyr, 0.001)
    base_wr = (wins_base / trades_base * 100) if trades_base > 0 else 0
    pyr_wr = (wins_pyr / trades_pyr * 100) if trades_pyr > 0 else 0

    print(f"{'Финальный капитал':<30} {f'{balance_base:,.0f} ₽':>15} {f'{balance_pyr:,.0f} ₽':>15}")
    print(f"{'Доходность':<30} {f'+{base_return:,.1f}%':>15} {f'+{pyr_return:,.1f}%':>15}")
    print(f"{'CAGR':<30} {f'{base_cagr:,.1f}%':>15} {f'{pyr_cagr:,.1f}%':>15}")
    print(f"{'Max DD':<30} {f'{max_dd_base*100:.1f}%':>15} {f'{max_dd_pyr*100:.1f}%':>15}")
    print(f"{'Calmar':<30} {f'{base_calmar:,.1f}':>15} {f'{pyr_calmar:,.1f}':>15}")
    print(f"{'WR':<30} {f'{base_wr:.1f}%':>15} {f'{pyr_wr:.1f}%':>15}")
    print(f"{'Сделок':<30} {f'{trades_base}':>15} {f'{trades_pyr}':>15}")
    print(f"{'Период (дней)':<30} {f'{days}':>15} {f'{days}':>15}")
    print(f"{'Пирамида макс':<30} {f'1':>15} {f'{PYRAMID_MAX}':>15}")

    # Анализ пирамиды — сколько сделок в каскаде
    if all_trades:
        pyr_wins = [t for t in all_trades if t['pnl_rub'] > 0]
        pyr_losses = [t for t in all_trades if t['pnl_rub'] <= 0]
        avg_win = np.mean([t['pnl_rub'] for t in pyr_wins]) if pyr_wins else 0
        avg_loss = abs(np.mean([t['pnl_rub'] for t in pyr_losses])) if pyr_losses else 0
        rr = avg_win / max(avg_loss, 1)

        print(f"\n--- Детали пирамиды ---")
        print(f"Всего сделок в каскаде: {len(all_trades)}")
        print(f"Средний выигрыш: {avg_win:,.0f} ₽")
        print(f"Средний проигрыш: {avg_loss:,.0f} ₽")
        print(f"RR (avg_win/avg_loss): {rr:.2f}")
        print(f"Стоп-выходов: {len([t for t in all_trades if t['reason']=='stop'])}")
        print(f"Time-выходов: {len([t for t in all_trades if t['reason']=='time'])}")

    # Мультипликатор
    if base_return > 0 and pyr_return > 0:
        mult = pyr_return / base_return
        dd_mult = (max_dd_pyr * 100) / max(max_dd_base * 100, 0.1)
        print(f"\n--- Мультипликаторы ---")
        print(f"Доходность: ×{mult:.1f} ({base_return:.1f}% → {pyr_return:.1f}%)")
        print(f"DD: ×{dd_mult:.1f} ({max_dd_base*100:.1f}% → {max_dd_pyr*100:.1f}%)")

    # Сохранение
    result = {
        'ticker': TICKER,
        'period': f'{times[WINDOW]} to {times[-1]}',
        'days': days,
        'pyramid_max': PYRAMID_MAX,
        'base': {
            'final_capital': round(balance_base, 2),
            'return_pct': round(base_return, 2),
            'cagr_pct': round(base_cagr, 2),
            'max_dd_pct': round(max_dd_base * 100, 2),
            'calmar': round(base_calmar, 2),
            'wr_pct': round(base_wr, 2),
            'trades': trades_base,
        },
        'pyramid': {
            'final_capital': round(balance_pyr, 2),
            'return_pct': round(pyr_return, 2),
            'cagr_pct': round(pyr_cagr, 2),
            'max_dd_pct': round(max_dd_pyr * 100, 2),
            'calmar': round(pyr_calmar, 2),
            'wr_pct': round(pyr_wr, 2),
            'trades': trades_pyr,
        },
        'multipliers': {
            'return_x': round(pyr_return / max(base_return, 0.1), 2),
            'dd_x': round(max_dd_pyr / max(max_dd_base, 0.001), 2),
            'calmar_x': round(pyr_calmar / max(base_calmar, 0.1), 2),
        },
    }

    out_dir = "reports/pyramid_test"
    os.makedirs(out_dir, exist_ok=True)
    with open(f"{out_dir}/result_br_honest.json", 'w') as f:
        json.dump(result, f, indent=2, default=str)

    print(f"\nРезультат сохранён в {out_dir}/result_br_honest.json")

    # Вывод ключевых выводов
    print("\n" + "="*65)
    print("ВЫВОДЫ:")
    print("="*65)
    if pyr_return > base_return * 1.5:
        print(f"✅ Пирамидинг даёт МУЛЬТИПЛИКАТОР ×{mult:.1f} по доходности")
        print(f"   DD выросла в ×{dd_mult:.1f} ({max_dd_base*100:.1f}% → {max_dd_pyr*100:.1f}%)")
        print(f"   Calmar: {base_calmar:.1f} → {pyr_calmar:.1f}")
    else:
        print("❌ Пирамидинг НЕ ДАЁТ значимого улучшения на этом тикере")
    print("="*65)


if __name__ == '__main__':
    run_test()
