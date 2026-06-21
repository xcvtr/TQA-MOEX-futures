#!/usr/bin/env python3
"""Портфель: joint_score = yur_net_z*0.5 + oi_spread_z*0.5. Multi-CPU."""
import sys, os, itertools, multiprocessing as mp
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
COMMISSION = 4
N_JOBS = 64

def rz(s, w=20):
    m = s.rolling(w, min_periods=w).mean()
    std = s.rolling(w, min_periods=w).std()
    return (s - m) / std.clip(lower=1e-10)

def calc_atr(df, p=14):
    prev = df['close'].shift(1)
    tr = pd.concat([
        df['high']-df['low'],
        (df['high']-prev).abs(),
        (df['low']-prev).abs()
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
        rez[col] = df[col].resample(f'{minutes}min').last().ffill().fillna(0)
    return rez.dropna()

def precompute(df):
    d = df.copy()
    d['volume'] = d['volume'].astype(float)
    d['yur_net'] = d['yur_buy'].fillna(0) - d['yur_sell'].fillna(0)
    d['fiz_net'] = d['fiz_buy'].fillna(0) - d['fiz_sell'].fillna(0)
    d['yur_net_z'] = rz(d['yur_net'], 20).shift(1)
    d['oi_r'] = (d['yur_buy'].fillna(0) + d['yur_sell'].fillna(0)) / (d['fiz_buy'].fillna(0) + d['fiz_sell'].fillna(0) + 1)
    d['oima'] = d['oi_r'].rolling(20).mean()
    d['oi_spread'] = (d['oima'] - d['oi_r']) / d['oima'].clip(lower=0.1)
    d['oi_spread_z'] = rz(d['oi_spread'], 20).shift(1)
    d['atr14'] = calc_atr(d)
    d['vr'] = d['volume'] / d['volume'].rolling(20).mean().clip(lower=1)
    d['score'] = np.clip(d['yur_net_z'] * 0.5 + d['oi_spread_z'] * 0.5, -3, 3) / 3
    return d

def simulate(args):
    bar_index, unique_times, go_map, lot_pct, bars_left, stop_atr, score_thresh = args
    cash = float(INITIAL_CAPITAL)
    positions = {}
    peak = INITIAL_CAPITAL
    max_dd = 0.0
    total_trades = 0
    total_commission = 0.0

    for ts in unique_times:
        bars = bar_index[ts]
        for sym in list(positions.keys()):
            if sym not in bars: continue
            close, low, high = bars[sym][0], bars[sym][1], bars[sym][2]
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
                comm = pos['contracts'] * COMMISSION
                cash += pr + pos['go'] * pos['contracts'] - comm
                total_trades += 1
                total_commission += comm
                del positions[sym]
        for sym in SYMBOLS:
            if sym in positions or sym not in bars: continue
            close, low, high, score, atr14, h, vr, yn, oz = bars[sym]
            if h < 7 or h >= 23: continue
            if np.isnan(score): continue
            if vr < 0.5: continue
            if abs(score) < score_thresh: continue
            entry_dir = 'L' if score > score_thresh else 'S'
            go = go_map[sym]
            max_rub = cash * lot_pct
            contracts = int(max_rub / go)
            if contracts < 1: continue
            needed = go * contracts
            if cash < needed: continue
            atrv = atr14 if not np.isnan(atr14) else 1.0
            stop = close - atrv * stop_atr if entry_dir == 'L' else close + atrv * stop_atr
            cash -= needed
            positions[sym] = {
                'dir': entry_dir, 'entry': close, 'stop': stop,
                'bars_left': bars_left, 'go': go, 'contracts': contracts,
            }
        mtm_total = 0.0
        for sym, pos in positions.items():
            if sym in bars:
                c = bars[sym][0]
                dm = 1 if pos['dir'] == 'L' else -1
                mtm = dm * (c - pos['entry']) / pos['entry'] * pos['go'] * pos['contracts']
                mtm_total += mtm
        equity = cash + mtm_total + sum(p['go'] * p['contracts'] for p in positions.values())
        if equity > peak: peak = equity
        ddv = (peak - equity) / peak if peak > 0 else 0
        if ddv > max_dd: max_dd = ddv

    for sym, pos in list(positions.items()):
        ep = pos['entry']
        dm = 1 if pos['dir'] == 'L' else -1
        pp = dm * (ep - pos['entry']) / pos['entry']
        pr = pp * pos['go'] * pos['contracts']
        comm = pos['contracts'] * COMMISSION
        cash += pr + pos['go'] * pos['contracts'] - comm
        total_trades += 1
        total_commission += comm

    port_ret = (cash - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100
    days = (unique_times[-1] - unique_times[0]).total_seconds() / 86400
    years = max(days / 365.25, 0.1)
    cagr = ((cash / INITIAL_CAPITAL) ** (1/years) - 1) * 100 if cash > 0 else -100
    calmar = port_ret / 100 / max(max_dd, 0.001) if max_dd > 0 else port_ret * 10
    return {'ret': round(port_ret,1), 'cagr': round(cagr,1), 'dd': round(max_dd*100,1),
            'calmar': round(calmar,1), 'trades': total_trades, 'comm': round(total_commission)}

def build_bar_index(data, tf_name):
    """Собрать bar_index один раз. Возвращает (bar_index, unique_times, go_map)."""
    bar_index = {}
    for sym in SYMBOLS:
        d = data[sym]
        mask = (d.index >= TEST_START) & (d.index < TEST_END)
        dd = d[mask]
        for i in range(len(dd)):
            ts = dd.index[i]
            if ts not in bar_index: bar_index[ts] = {}
            h = ts.hour if hasattr(ts,'hour') else pd.Timestamp(ts).hour
            bar_index[ts][sym] = (
                float(dd.iloc[i]['close']), float(dd.iloc[i]['low']),
                float(dd.iloc[i]['high']), float(dd.iloc[i]['score']),
                float(dd.iloc[i]['atr14']), h,
                float(dd.iloc[i]['vr']), float(dd.iloc[i]['yur_net_z']),
                float(dd.iloc[i]['oi_spread_z']),
            )
    unique_times = sorted(bar_index.keys())
    return bar_index, unique_times

def main():
    print("Loading data...")
    raw = {}
    for sym in SYMBOLS:
        print(f"  {sym}...", end=' ', flush=True)
        raw[sym] = load_data(sym)
        print(f"{len(raw[sym])} bars")

    go_map = {sym: TICKER_CONFIGS.get(sym, {}).get('go', 5000) for sym in SYMBOLS}

    for tf_name, tf_min in [('H1',60), ('H4',240), ('H8',480), ('D1',1440)]:
        print(f"\n{'='*60}")
        print(f"  TF: {tf_name}")
        print(f"{'='*60}")

        data = {}
        for sym in SYMBOLS:
            data[sym] = precompute(resample_to_m(raw[sym], tf_min))

        n_bars = min(len(v) for v in data.values())
        print(f"  Bars: {n_bars}")
        print(f"  Building bar_index...", flush=True)

        bar_index, unique_times = build_bar_index(data, tf_name)
        print(f"  bar_index: {len(bar_index)} timestamps, {len(unique_times)} unique", flush=True)

        # Готовим аргументы
        params = []
        for thresh in [0.3, 0.5, 0.8, 1.0, 1.2, 1.5, 2.0, 3.0]:
            for lot in [0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50]:
                for bars_left in [5, 8, 13, 21, 34, 55]:
                    for stop in [0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0]:
                        params.append((bar_index, unique_times, go_map, lot, bars_left, stop, thresh))

        total = len(params)
        print(f"  Params: {total} combos, {N_JOBS} workers", flush=True)

        results = []
        with mp.Pool(N_JOBS) as pool:
            for i, r in enumerate(pool.imap_unordered(simulate, params, chunksize=32), 1):
                if r['cagr'] > 0 and r['dd'] < 20 and r['trades'] >= 20:
                    print(f"  ✅ th={r['calmar']:.1f}|ret={r['ret']:>6.1f}% CAGR={r['cagr']:>6.1f}% DD={r['dd']:.1f}% "
                          f"Calmar={r['calmar']:>6.1f} Tr={r['trades']:>4} Comm={r['comm']:>6,}", flush=True)
                    results.append(r)
                if i % 500 == 0:
                    print(f"  [{tf_name}] {i}/{total} combos done...", flush=True)

        print(f"\n  [{tf_name}] Done. {len(results)} passed filter.\n")

if __name__ == '__main__':
    main()
