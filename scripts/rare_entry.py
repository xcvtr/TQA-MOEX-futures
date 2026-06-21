#!/usr/bin/env python3
"""Rare entry strategy: hard filters instead of score weights.
   Grid search: TF × (ADX,VR,OI) thresholds × dir × params. Top-20 by Calmar.
   Step 5: relax thresholds if strict yields 0 results."""
import sys, os, itertools, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
import clickhouse_connect
from scripts.bar_level_sim import TICKER_CONFIGS
from config import CH_HOST, CH_PORT, CH_DB
from scripts.stress_test_full import (
    load_data, resample_to_m, calc_atr, calc_adx, rz,
    build_bar_index, simulate, SYMBOLS, INITIAL_CAPITAL, TEST_START, TEST_END,
    COMMISSION, SLIPPAGE, MIN_TRADES, MAX_DD_PCT
)

TIMEFRAMES = [('H1', 60), ('H4', 240)]
PARAM_LOT = [0.10, 0.15, 0.20, 0.25, 0.50]
PARAM_BARS = [8, 13, 21, 34]
PARAM_STOP = [1.0, 1.5, 2.0, 3.0]

ADX_THRESH = [20, 25]
VR_THRESH = [1.5, 2.0]
DIR_MODES = ['B', 'L']

OI_FILTERS = [
    ('accel_z', lambda d: d['oi_accel_z'] > 1.5),
    ('r_z',     lambda d: d['oi_r_z'] > 2.0),
    ('fyd',     lambda d: d['fiz_yur_delta'] > 0.8),
    ('any',     lambda d: (d['oi_accel_z'] > 1.5) | (d['oi_r_z'] > 2.0) | (d['fiz_yur_delta'] > 0.8)),
    ('any_low', lambda d: (d['oi_accel_z'] > 1.0) | (d['oi_r_z'] > 1.5) | (d['fiz_yur_delta'] > 0.6)),
]


def precompute_rare(df):
    d = df.copy()
    d['volume'] = d['volume'].astype(float)
    d['vma20'] = d['volume'].rolling(20).mean().fillna(d['volume'])
    d['vr'] = d['volume'] / d['vma20'].clip(lower=1)
    d['fiz_net'] = d['fiz_buy'].fillna(0) - d['fiz_sell'].fillna(0)
    d['yur_net'] = d['yur_buy'].fillna(0) - d['yur_sell'].fillna(0)
    d['oi_r'] = (d['yur_buy']+d['yur_sell']).fillna(0) / (d['fiz_buy']+d['fiz_sell']+1).fillna(0)
    d['oi_r_z'] = rz(d['oi_r'], 20)
    d['oi_accel'] = d['oi_r'].diff().rolling(5).mean()
    d['oi_accel_z'] = rz(d['oi_accel'].fillna(0), 20)
    d['fiz_yur_delta'] = (d['fiz_net'] - d['yur_net']).abs() / (d['fiz_net'].abs() + d['yur_net'].abs() + 1)
    d['atr14'] = calc_atr(d)
    d['adx'] = calc_adx(d)
    return d


def compute_rare_score(d, oi_filter, adx_min, vr_min, dir_mode):
    vr_ok = d['vr'] > vr_min
    adx_ok = d['adx'] > adx_min
    oi_ok = oi_filter(d)
    if dir_mode == 'L':
        score = pd.Series(0.0, index=d.index)
        score[vr_ok & adx_ok & oi_ok & (d['yur_net'] > 0)] = 1.0
    elif dir_mode == 'S':
        score = pd.Series(0.0, index=d.index)
        score[vr_ok & adx_ok & oi_ok & (d['yur_net'] < 0)] = -1.0
    else:
        long_cond = vr_ok & adx_ok & oi_ok & (d['yur_net'] > 0)
        short_cond = vr_ok & adx_ok & oi_ok & (d['yur_net'] < 0)
        score = pd.Series(0.0, index=d.index)
        score[long_cond] = 1.0
        score[short_cond] = -1.0
    return score


def per_symbol_breakdown(data, params):
    print(f"\n  Per-symbol breakdown for top config:", flush=True)
    print(f"  TF={params['tf']} OI={params['oi']} ADX>{params['adx']} VR>{params['vr']} "
          f"dir={params['dir']} lot={params['lot']:.2f} bars={params['bars']} stop={params['stop']:.1f}", flush=True)
    for sym in SYMBOLS:
        sym_data = {sym: data[sym]}
        bi, ut, gm, lc = build_bar_index(sym_data)
        r = simulate(bi, ut, gm, lc, params['lot'], params['bars'], params['stop'],
                     score_thresh=0.5, dir_mode='B', adx_min=0, atr_pct_min=0)
        print(f"    {sym}: Ret={r['ret_pct']:>7.1f}% CAGR={r['cagr_pct']:>7.1f}% "
              f"DD={r['dd_pct']:.1f}% Calmar={r['calmar']:>7.1f} Tr={r['trades']:>4}", flush=True)


def main():
    strict_label = (f"ADX∈{ADX_THRESH} VR∈{VR_THRESH} OI∈{[n for n, _ in OI_FILTERS]} "
                    f"dir∈{DIR_MODES}")
    print("=" * 80, flush=True)
    print("  RARE ENTRY GRID — hard filters", flush=True)
    print(f"  {strict_label}", flush=True)
    print(f"  Period: {TEST_START.date()} — {TEST_END.date()}", flush=True)
    print(f"  Commission: {COMMISSION}₽/trade · Slippage: {SLIPPAGE*100:.1f}%", flush=True)
    print(f"  min_trades={MIN_TRADES} · max_dd={MAX_DD_PCT}%", flush=True)
    print("=" * 80, flush=True)

    t0 = time.time()
    print("\nLoading raw data...", flush=True)
    raw = {}
    for sym in SYMBOLS:
        print(f"  {sym}...", end=' ', flush=True)
        raw[sym] = load_data(sym)
        print(f"{len(raw[sym])} bars", flush=True)

    all_results = []

    for tf_name, tf_min in TIMEFRAMES:
        print(f"\n{'─' * 80}", flush=True)
        print(f"  TIMEFRAME: {tf_name} ({tf_min}min)", flush=True)
        print(f"{'─' * 80}", flush=True)

        data = {}
        for sym in SYMBOLS:
            d = resample_to_m(raw[sym], tf_min)
            data[sym] = precompute_rare(d)

        n_bars = min(len(v) for v in data.values()) if data else 0
        print(f"  Symbols prepared, min bars: {n_bars}", flush=True)

        for oi_name, oi_filter in OI_FILTERS:
            for adx_min in ADX_THRESH:
                for vr_min in VR_THRESH:
                    for dir_mode in DIR_MODES:
                        t_m = time.time()
                        label = f"{oi_name}_a{adx_min}_v{vr_min:.1f}_{dir_mode}"
                        for sym in SYMBOLS:
                            data[sym]['score'] = compute_rare_score(
                                data[sym], oi_filter, adx_min, vr_min, dir_mode
                            )

                        bar_index, unique_times, go_map, last_close = build_bar_index(data)
                        n_signals = sum(
                            1 for ts in unique_times for sym in SYMBOLS
                            if sym in bar_index[ts] and abs(bar_index[ts][sym][3]) > 0.5
                        )
                        print(f"  {label:30s} — {len(unique_times):5d} ts, ~{n_signals:4d} signals", end='', flush=True)

                        combo_count = 0
                        for lot, bars, stop in itertools.product(PARAM_LOT, PARAM_BARS, PARAM_STOP):
                            r = simulate(bar_index, unique_times, go_map, last_close,
                                         lot, bars, stop, score_thresh=0.5,
                                         dir_mode='B', adx_min=0, atr_pct_min=0)
                            if r['cagr_pct'] > 0 and r['dd_pct'] < MAX_DD_PCT and r['trades'] >= MIN_TRADES:
                                all_results.append((tf_name, oi_name, adx_min, vr_min, dir_mode,
                                                    lot, bars, stop, r))
                                combo_count += 1
                        dt = time.time() - t_m
                        print(f" — {combo_count} ok in {dt:.0f}s", flush=True)

    all_results.sort(key=lambda x: x[8]['calmar'], reverse=True)

    print(f"\n{'=' * 130}", flush=True)
    print(f"  TOP-20 BY CALMAR (commission {COMMISSION}₽, slippage {SLIPPAGE*100:.1f}%)", flush=True)
    print(f"{'=' * 130}", flush=True)
    header = (f"  {'#':<3} {'TF':<4} {'OI':<9} {'ADX':<4} {'VR':<4} {'Dir':<4}"
              f" {'Lot':<5} {'Bars':<5} {'Stop':<6}"
              f" {'Ret%':>8} {'CAGR%':>8} {'DD%':>6} {'Calmar':>8} {'Trades':>5}")
    print(header, flush=True)
    print(f"  {'─' * 100}")
    for i, (tf, oi, adx, vr, dm, lot, bars, stop, r) in enumerate(all_results[:20]):
        print(f"  {i+1:<3} {tf:<4} {oi:<9} {adx:<4} {vr:<4.1f} {dm:<4} "
              f"{lot:<5.2f} {bars:<5} {stop:<6.1f} "
              f"{r['ret_pct']:>7.1f}% {r['cagr_pct']:>7.1f}% "
              f"{r['dd_pct']:>5.1f}% {r['calmar']:>7.1f} {r['trades']:>5}", flush=True)

    print(f"\n  Total successful configs: {len(all_results)}", flush=True)
    print(f"  Total time: {time.time() - t0:.0f}s", flush=True)

    top5 = all_results[:5]
    if top5:
        print(f"\n{'=' * 130}", flush=True)
        print(f"  PER-SYMBOL BREAKDOWN FOR TOP-5", flush=True)
        print(f"{'=' * 130}", flush=True)
        for rank, (tf, oi, adx, vr, dm, lot, bars, stop, r) in enumerate(top5):
            params = dict(tf=tf, oi=oi, adx=adx, vr=vr, dir=dm,
                          lot=lot, bars=bars, stop=stop)
            per_symbol_breakdown(data, params)


if __name__ == '__main__':
    main()
