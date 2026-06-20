#!/usr/bin/env python3
"""Портфельный тест E6 — ОДИН cash на все 6 тикеров. Как в реальности."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
import clickhouse_connect
from scripts.bar_level_sim import TICKER_CONFIGS
from config import CH_HOST, CH_PORT, CH_DB

INITIAL_CAPITAL = 100_000
TEST_START = pd.Timestamp('2025-01-01')
TEST_END = pd.Timestamp('2026-05-01')
SYMBOLS = ['GL', 'HS', 'HY', 'RN', 'NM', 'AF']

LOT_PCT = 0.50
BARS_LEFT = 4
STOP_ATR = 1.0
SCORE_THRESH = 0.10

def rz(s, w=20):
    m = s.rolling(w, min_periods=w).mean()
    std = s.rolling(w, min_periods=w).std()
    return (s - m) / std.clip(lower=1e-10)

def calc_atr(df, p=14):
    prev = df['close'].shift(1)
    tr = pd.concat([
        df['high'] - df['low'],
        (df['high'] - prev).abs(),
        (df['low'] - prev).abs()
    ], axis=1).max(axis=1)
    return tr.rolling(p, min_periods=p).mean().bfill().fillna(0)

def load_data(sym):
    ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)
    q = f"""
        SELECT p.time, p.open, p.high, p.low, p.close, p.volume,
               o.fiz_buy, o.fiz_sell, o.yur_buy, o.yur_sell, o.total_oi
        FROM moex.prices_5m p
        LEFT JOIN moex.prices_5m_oi o ON p.time = o.time AND p.symbol = o.symbol
        WHERE p.symbol='{sym}' AND p.time>='2023-01-01' AND p.time<='2026-04-30'
        ORDER BY p.time
    """
    r = ch.query(q)
    cols = ['time','open','high','low','close','volume',
            'fiz_buy','fiz_sell','yur_buy','yur_sell','total_oi']
    df = pd.DataFrame(r.result_rows, columns=cols)
    df['time'] = pd.to_datetime(df['time']).dt.tz_localize(None)
    df.set_index('time', inplace=True)
    return df

def resample_to_15m(df):
    ohlc = df['close'].resample('15min').ohlc()
    vol = df['volume'].resample('15min').sum()
    rez = ohlc.copy()
    rez['volume'] = vol
    for col in ['fiz_buy','fiz_sell','yur_buy','yur_sell','total_oi']:
        rez[col] = df[col].resample('15min').last().fillna(0)
    return rez.dropna()

def precompute_base(df):
    d = df.copy()
    d['volume'] = d['volume'].astype(float)
    d['vma20'] = d['volume'].rolling(20).mean().fillna(d['volume'])
    d['vr'] = d['volume'] / d['vma20'].clip(lower=1)
    d['vz'] = rz(d['volume'], 20).shift(1)
    d['fiz_net'] = d['fiz_buy'].fillna(0) - d['fiz_sell'].fillna(0)
    d['yur_net'] = d['yur_buy'].fillna(0) - d['yur_sell'].fillna(0)
    d['oi_r'] = (d['yur_buy']+d['yur_sell']).fillna(0) / (d['fiz_buy']+d['fiz_sell']+1).fillna(0)
    d['oima'] = d['oi_r'].rolling(20).mean()
    d['atr14'] = calc_atr(d)
    d['atr_pct'] = d['atr14'] / d['close'].clip(lower=1) * 100
    d['oi_accel'] = d['oi_r'].diff().rolling(5).mean()
    d['fiz_yur_delta'] = (d['fiz_net'] - d['yur_net']).abs() / (d['fiz_net'].abs() + d['yur_net'].abs() + 1)
    vs = np.clip((d['vr'] - 1.5) / 3.0, 0, 1)
    os_ = np.clip((d['oima'] - d['oi_r']) / d['oima'].clip(lower=0.1), 0, 1)
    raw = vs*0.3 + os_*0.7 + d['oi_accel']*0.5 + d['fiz_yur_delta']*0.3
    af = np.clip(1 - (d['atr_pct'] - 0.3) / 3.0, 0, 1)
    score = np.clip(raw * af * np.clip(1 + d['vz'] / 5, 0.5, 1.5), 0, 1)
    d['score'] = score
    return d

def main():
    print("Загрузка данных...", flush=True)
    data = {}
    for sym in SYMBOLS:
        print(f"  {sym}...", end=' ', flush=True)
        df = load_data(sym)
        d15 = resample_to_15m(df)
        d = precompute_base(d15)
        data[sym] = d
        print(f"{len(d)} баров", flush=True)

    # Создаём единый таймлайнер — все бары всех тикеров, сортированные по времени
    print("Сборка единого таймлайнера...", flush=True)
    # Собираем данные в numpy-структуры для скорости
    print("Сборка единого таймлайнера...", flush=True)
    all_records = []
    for sym in SYMBOLS:
        d = data[sym]
        mask = (d.index >= TEST_START) & (d.index < TEST_END)
        dd = d[mask]
        times = dd.index.values
        for i in range(len(dd)):
            all_records.append({
                'time': times[i], 'sym': sym,
                'open': dd.iloc[i]['open'],
                'high': dd.iloc[i]['high'],
                'low': dd.iloc[i]['low'],
                'close': dd.iloc[i]['close'],
                'score': dd.iloc[i]['score'],
                'atr14': dd.iloc[i]['atr14'],
            })
    timeline = pd.DataFrame(all_records)
    timeline = timeline.sort_values(['time', 'sym']).reset_index(drop=True)
    print(f"  Всего баров: {len(timeline)}", flush=True)

    # Строим индекс: unique_time -> dict[sym] -> row
    print("  Индексирование...", flush=True)
    go_map = {sym: TICKER_CONFIGS.get(sym, {}).get('go', 5000) for sym in SYMBOLS}

    bar_index = {}  # time -> {sym: (close, low, high, score, atr14, hour)}
    for _, row in timeline.iterrows():
        ts = row['time']
        if ts not in bar_index:
            bar_index[ts] = {}
        h = ts.hour if hasattr(ts, 'hour') else pd.Timestamp(ts).hour
        bar_index[ts][row['sym']] = (
            float(row['close']), float(row['low']), float(row['high']),
            float(row['score']), float(row['atr14']), h
        )

    unique_times = sorted(bar_index.keys())
    print(f"  Уникальных таймштампов: {len(unique_times)}", flush=True)

    # === ОДИН simulate на всём портфеле ===
    cash = float(INITIAL_CAPITAL)
    positions = {}  # {sym: {entry, stop, bars_left, contracts, go}}
    peak = INITIAL_CAPITAL
    max_dd = 0.0
    total_trades = 0
    total_wins = 0
    equity_records = []

    for ts in unique_times:
        bars = bar_index[ts]

        # === 1. Стопы ===
        for sym in list(positions.keys()):
            if sym not in bars:
                continue
            close, low, high, _, _, _ = bars[sym]
            pos = positions[sym]
            pos['bars_left'] -= 1
            hit = False
            ep = close
            if pos['dir'] == 'L' and low <= pos['stop']:
                hit = True
                ep = pos['stop']
            elif pos['dir'] == 'S' and high >= pos['stop']:
                hit = True
                ep = pos['stop']
            elif pos['bars_left'] <= 0:
                hit = True

            if hit:
                dm = 1 if pos['dir'] == 'L' else -1
                pp = dm * (ep - pos['entry']) / pos['entry']
                pr = pp * pos['go'] * pos['contracts']
                cash += pr + pos['go'] * pos['contracts']
                total_trades += 1
                if pr > 0:
                    total_wins += 1
                del positions[sym]

        # === 2. Новые сигналы ===
        for sym in SYMBOLS:
            if sym in positions or sym not in bars:
                continue
            close, low, high, score, atr14, h = bars[sym]
            if h < 7 or h >= 23:
                continue
            if np.isnan(score) or score < SCORE_THRESH:
                continue

            go = go_map[sym]
            max_rub = cash * LOT_PCT
            contracts = int(max_rub / go)
            if contracts < 1:
                continue
            needed = go * contracts
            if cash < needed:
                continue

            atrv = atr14 if not np.isnan(atr14) else 1.0
            stop = close - atrv * STOP_ATR
            cash -= needed
            positions[sym] = {
                'dir': 'L', 'entry': close, 'stop': stop,
                'bars_left': BARS_LEFT, 'go': go, 'contracts': contracts,
            }

        # === 3. MTM + equity ===
        mtm_total = 0.0
        for sym, pos in positions.items():
            if sym in bars:
                close = bars[sym][0]
                dm = 1 if pos['dir'] == 'L' else -1
                mtm = dm * (close - pos['entry']) / pos['entry'] * pos['go'] * pos['contracts']
                mtm_total += mtm

        equity = cash + mtm_total + sum(p['go'] * p['contracts'] for p in positions.values())
        if equity > peak:
            peak = equity
        ddv = (peak - equity) / peak if peak > 0 else 0
        if ddv > max_dd:
            max_dd = ddv
        equity_records.append({'time': ts, 'equity': equity, 'open_positions': len(positions)})

    # Закрываем оставшиеся позиции
    for sym, pos in list(positions.items()):
        last_bar = timeline[timeline['sym'] == sym].iloc[-1]
        dm = 1 if pos['dir'] == 'L' else -1
        pp = dm * (float(last_bar['close']) - pos['entry']) / pos['entry']
        pr = pp * pos['go'] * pos['contracts']
        cash += pr + pos['go'] * pos['contracts']
        total_trades += 1
        if pr > 0:
            total_wins += 1

    # === Итог ===
    final_equity = cash
    port_return = (final_equity - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100
    days = (unique_times[-1] - unique_times[0]).total_seconds() / 86400
    years = max(days / 365.25, 0.1)
    port_cagr = ((final_equity / INITIAL_CAPITAL) ** (1/years) - 1) * 100 if final_equity > 0 else -100
    port_calmar = port_return / 100 / max(max_dd, 0.001)
    wr = total_wins / total_trades * 100 if total_trades > 0 else 0

    print(f"\n{'='*60}")
    print(f"  ПОРТФЕЛЬ E6 — ОДИН СЧЁТ")
    print(f"{'='*60}")
    print(f"  Параметры: M15 · lot={LOT_PCT*100:.0f}% · bars={BARS_LEFT} · stop={STOP_ATR}ATR · score>{SCORE_THRESH}")
    print(f"  {'='*60}")
    print(f"  Начальный капитал: {INITIAL_CAPITAL:>10,.0f} ₽")
    print(f"  Конечный капитал:  {final_equity:>10,.0f} ₽")
    print(f"  Доходность:        {port_return:>10.1f}%")
    print(f"  CAGR:              {port_cagr:>10.1f}%")
    print(f"  MAX DD:            {max_dd*100:>10.1f}%")
    print(f"  Calmar:            {port_calmar:>10.1f}")
    print(f"  Сделок:            {total_trades:>10}")
    print(f"  WR:                {wr:>10.1f}%")
    print(f"  Период:            {str(unique_times[0])[:10]} — {str(unique_times[-1])[:10]} ({days:.0f} дн)")

    # Сохраняем equity
    eq_df = pd.DataFrame(equity_records)
    eq_df.to_json('reports/equity_e6_unified.json', orient='records', date_format='iso')
    print(f"\n  Equity сохранена: reports/equity_e6_unified.json")

    # Статистика по позициям
    max_concurrent = max(r['open_positions'] for r in equity_records)
    avg_concurrent = sum(r['open_positions'] for r in equity_records) / len(equity_records)
    print(f"  Макс. одновременных позиций: {max_concurrent}")
    print(f"  Среднее кол-во позиций:      {avg_concurrent:.1f}")

if __name__ == '__main__':
    main()
