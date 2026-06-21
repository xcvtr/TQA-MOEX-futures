#!/usr/bin/env python3
"""Stress test: grid параметров с комиссиями 4₽/сделку."""
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
COMMISSION_PER_CONTRACT = 4  # round-trip

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

def resample_to_m(df, minutes):
    ohlc = df['close'].resample(f'{minutes}min').ohlc()
    vol = df['volume'].resample(f'{minutes}min').sum()
    rez = ohlc.copy()
    rez['volume'] = vol
    for col in ['fiz_buy','fiz_sell','yur_buy','yur_sell','total_oi']:
        rez[col] = df[col].resample(f'{minutes}min').last().fillna(0)
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

def run_sim(data, lot_pct, bars_left, stop_atr, score_thresh, slippage_pct=0.0):
    """Один simulate на всём портфеле. Возвращает метрики."""
    # Собираем timeline
    go_map = {sym: TICKER_CONFIGS.get(sym, {}).get('go', 5000) for sym in SYMBOLS}
    bar_index = {}
    for sym in SYMBOLS:
        d = data[sym]
        mask = (d.index >= TEST_START) & (d.index < TEST_END)
        dd = d[mask]
        for i in range(len(dd)):
            ts = dd.index[i]
            if ts not in bar_index:
                bar_index[ts] = {}
            h = ts.hour if hasattr(ts, 'hour') else pd.Timestamp(ts).hour
            bar_index[ts][sym] = (float(dd.iloc[i]['close']), float(dd.iloc[i]['low']),
                                  float(dd.iloc[i]['high']), float(dd.iloc[i]['score']),
                                  float(dd.iloc[i]['atr14']), h)
    unique_times = sorted(bar_index.keys())

    cash = float(INITIAL_CAPITAL)
    positions = {}
    peak = INITIAL_CAPITAL
    max_dd = 0.0
    total_trades = 0
    total_commission = 0.0

    for ts in unique_times:
        bars = bar_index[ts]
        # Стопы
        for sym in list(positions.keys()):
            if sym not in bars:
                continue
            close, low, high, _, _, _ = bars[sym]
            pos = positions[sym]
            pos['bars_left'] -= 1
            hit = False
            ep = close
            if pos['dir'] == 'L' and low <= pos['stop']:
                hit = True; ep = pos['stop']
            elif pos['dir'] == 'S' and high >= pos['stop']:
                hit = True; ep = pos['stop']
            elif pos['bars_left'] <= 0:
                hit = True
            if hit:
                dm = 1 if pos['dir'] == 'L' else -1
                pp = dm * (ep - pos['entry']) / pos['entry']
                pr = pp * pos['go'] * pos['contracts']
                comm = pos['contracts'] * COMMISSION_PER_CONTRACT
                cash += pr + pos['go'] * pos['contracts'] - comm
                total_trades += 1
                total_commission += comm
                del positions[sym]
        # Новые сигналы
        for sym in SYMBOLS:
            if sym in positions or sym not in bars:
                continue
            close, low, high, score, atr14, h = bars[sym]
            if h < 7 or h >= 23:
                continue
            if np.isnan(score) or score < score_thresh:
                continue
            go = go_map[sym]
            max_rub = cash * lot_pct
            contracts = int(max_rub / go)
            if contracts < 1:
                continue
            needed = go * contracts
            if cash < needed:
                continue
            atrv = atr14 if not np.isnan(atr14) else 1.0
            stop = close - atrv * stop_atr
            cash -= needed
            positions[sym] = {
                'dir': 'L', 'entry': close, 'stop': stop,
                'bars_left': bars_left, 'go': go, 'contracts': contracts,
            }
        # Equity
        mtm_total = 0.0
        for sym, pos in positions.items():
            if sym in bars:
                c = bars[sym][0]
                dm = 1 if pos['dir'] == 'L' else -1
                mtm = dm * (c - pos['entry']) / pos['entry'] * pos['go'] * pos['contracts']
                mtm_total += mtm
        equity = cash + mtm_total + sum(p['go'] * p['contracts'] for p in positions.values())
        if equity > peak:
            peak = equity
        ddv = (peak - equity) / peak if peak > 0 else 0
        if ddv > max_dd:
            max_dd = ddv

    # Закрытие остатков
    for sym, pos in list(positions.items()):
        last_bar = data[sym]
        mask = (last_bar.index >= TEST_START) & (last_bar.index < TEST_END)
        dd = last_bar[mask]
        last = dd.iloc[-1]
        dm = 1 if pos['dir'] == 'L' else -1
        pp = dm * (float(last['close']) - pos['entry']) / pos['entry']
        pr = pp * pos['go'] * pos['contracts']
        comm = pos['contracts'] * COMMISSION_PER_CONTRACT
        cash += pr + pos['go'] * pos['contracts'] - comm
        total_trades += 1
        total_commission += comm

    final_equity = cash
    port_ret = (final_equity - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100
    days = (unique_times[-1] - unique_times[0]).total_seconds() / 86400
    years = max(days / 365.25, 0.1)
    cagr = ((final_equity / INITIAL_CAPITAL) ** (1/years) - 1) * 100 if final_equity > 0 else -100
    calmar = port_ret / 100 / max(max_dd, 0.001) if max_dd > 0 else port_ret * 10
    wr = (total_trades - total_trades * 0) / total_trades * 100  # placeholder

    return {
        'final': round(final_equity),
        'ret_pct': round(port_ret, 1),
        'cagr_pct': round(cagr, 1),
        'dd_pct': round(max_dd * 100, 1),
        'calmar': round(calmar, 1),
        'trades': total_trades,
        'commission': round(total_commission),
    }

def main():
    print("Загрузка данных...", flush=True)
    data = {}
    for sym in SYMBOLS:
        print(f"  {sym}...", end=' ', flush=True)
        df = load_data(sym)
        d = precompute_base(resample_to_m(df, 15))
        data[sym] = d
        print(f"{len(d)} баров", flush=True)

    # Grid
    configs = []
    for lot in [0.25, 0.50]:
        for bars in [4, 8, 13]:
            for stop in [1.0, 2.0]:
                for score in [0.10, 0.25]:
                    configs.append((lot, bars, stop, score))

    # Добавляем консервативные варианты
    configs.extend([
        (0.15, 8, 2.0, 0.25),
        (0.15, 13, 2.0, 0.25),
        (0.20, 8, 1.5, 0.20),
        (0.25, 8, 2.0, 0.15),
    ])

    print(f"\nПрогон {len(configs)} конфигов с комиссиями...", flush=True)
    results = []
    for lot, bars, stop, score in configs:
        r = run_sim(data, lot, bars, stop, score)
        results.append((lot, bars, stop, score, r))
        print(f"  lot={lot:.2f} bars={bars:2d} stop={stop:.1f} score={score:.2f}"
              f" → Ret={r['ret_pct']:>7.1f}% CAGR={r['cagr_pct']:>7.1f}%"
              f" DD={r['dd_pct']:.1f}% Calmar={r['calmar']:>7.1f} Tr={r['trades']:>5} Comm={r['commission']:>6,}",
              flush=True)

    # Сортировка по Calmar
    results.sort(key=lambda x: x[4]['calmar'], reverse=True)

    print(f"\n{'='*100}")
    print(f"  ТОП-10 ПО CALMAR (с комиссиями 4₽/сделку)")
    print(f"{'='*100}")
    print(f"  {'#':<3} {'Lot':<5} {'Bars':<5} {'Stop':<6} {'Score':<7} {'Ret%':>8} {'CAGR%':>8} {'DD%':>6} {'Calmar':>8} {'Trades':>7} {'Comm':>8}")
    print(f"  {'-'*80}")
    for i, (lot, bars, stop, score, r) in enumerate(results[:10]):
        print(f"  {i+1:<3} {lot:<5.2f} {bars:<5} {stop:<6.1f} {score:<7.2f} {r['ret_pct']:>7.1f}% {r['cagr_pct']:>7.1f}% {r['dd_pct']:>5.1f}% {r['calmar']:>7.1f} {r['trades']:>5} {r['commission']:>7,}")

    # Baseline E6 для сравнения
    prev_e6 = results[[i for i,(l,b,s,sc,r) in enumerate(results)
                       if (l,b,s,sc)==(0.50,4,1.0,0.10)][0]]
    print(f"\n  Baseline E6 (lot=0.50 bars=4 stop=1.0 score=0.10): Ret={prev_e6[4]['ret_pct']}%, CAGR={prev_e6[4]['cagr_pct']}%, DD={prev_e6[4]['dd_pct']}%, Calmar={prev_e6[4]['calmar']}")

if __name__ == '__main__':
    main()
