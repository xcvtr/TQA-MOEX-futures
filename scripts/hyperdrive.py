#!/usr/bin/env python3
"""
HyperDrive: 7 experiments for 100%+ CAGR.
Прогоняет 7 экспериментов на 6 тикерах с L+S и новым score (oi_accel + fiz_yur_delta).
Результаты: таблица в stdout + JSON в reports/hyperdrive_results.json.
"""
import sys, os, json, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

from scripts.portfolio_sweep_enhancements import (
    load_data, load_accounts, precompute_base, calc_atr, calc_adx, rz,
    INITIAL_CAPITAL, TEST_START, TEST_END
)
from scripts.bar_level_sim import TICKER_CONFIGS

SYMBOLS = ['GL', 'HS', 'HY', 'RN', 'NM', 'AF']


def simulate_param(df, score_col, start, end, sym=None, name="",
                   lot_pct=0.25, bars_left=13, stop_atr=2.0,
                   use_tod=False, tod_ranges=None):
    mask = (df.index >= start) & (df.index < end)
    d = df[mask].copy()
    if len(d) == 0:
        return {'name': name, 'return_pct': 0, 'max_dd_pct': 0, 'calmar': 0, 'trades': 0}

    cash = INITIAL_CAPITAL
    peak = INITIAL_CAPITAL
    max_dd = 0
    trades = 0
    wins = 0

    if tod_ranges is None:
        tod_ranges = [(7, 24)]

    pos = None
    for i in range(1, len(d)):
        bar = d.iloc[i]
        ts = bar.name
        h = ts.hour if hasattr(ts, 'hour') else pd.Timestamp(ts).hour
        if h < 7 or h >= 23:
            continue

        if pos is not None:
            pos['bars_left'] -= 1
            hit = False
            ep = bar['close']
            if pos['dir'] == 'L' and bar['low'] <= pos['stop']:
                hit = True
                ep = pos['stop']
            elif pos['dir'] == 'S' and bar['high'] >= pos['stop']:
                hit = True
                ep = pos['stop']
            elif pos['bars_left'] <= 0:
                hit = True
            if hit:
                dm = 1 if pos['dir'] == 'L' else -1
                pp = dm * (ep - pos['entry']) / pos['entry']
                pr = pp * pos['go'] * pos['contracts']
                cash += pr
                trades += 1
                if pr > 0:
                    wins += 1
                pos = None

        if pos is not None:
            dm = 1 if pos['dir'] == 'L' else -1
            mtm = dm * (bar['close'] - pos['entry']) / pos['entry'] * pos['go'] * pos['contracts']
            teq = cash + mtm
        else:
            teq = cash
        if teq > peak:
            peak = teq
        ddv = (peak - teq) / peak if peak > 0 else 0
        if ddv > max_dd:
            max_dd = ddv
        if pos is not None:
            continue

        score = float(bar[score_col])
        if np.isnan(score) or score < 0.25:
            continue

        if use_tod:
            ok = False
            for lo, hi in tod_ranges:
                if lo <= h < hi:
                    ok = True
                    break
            if not ok:
                continue

        go = TICKER_CONFIGS.get(sym, {}).get('go', 5000)
        max_rub = cash * lot_pct
        contracts = max(1, int(max_rub / go))
        atrv = float(bar.get('atr14', 1))
        ep = float(bar['close'])
        stop = ep - atrv * stop_atr
        pos = {'dir': 'L', 'entry': ep, 'stop': stop,
               'bars_left': bars_left, 'go': go, 'contracts': contracts}

    if pos is not None:
        lb = d.iloc[-1]
        dm = 1 if pos['dir'] == 'L' else -1
        pp = dm * (lb['close'] - pos['entry']) / pos['entry']
        pr = pp * pos['go'] * pos['contracts']
        cash += pr
        trades += 1
        if pr > 0:
            wins += 1

    tr = (cash - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100
    days = (end - start).days
    years = max(days / 365.25, 0.1)
    cagr = ((cash / INITIAL_CAPITAL) ** (1 / years) - 1) * 100 if cash > 0 else -100
    calmar = tr / 100 / max(max_dd, 0.001) if max_dd > 0 else tr * 10

    return {
        'name': name, 'capital': round(cash, 2),
        'return_pct': round(tr, 2), 'cagr_pct': round(cagr, 2),
        'max_dd_pct': round(max_dd * 100, 2), 'calmar': round(calmar, 2),
        'wr_pct': round(wins / trades * 100, 2) if trades > 0 else 0,
        'trades': trades,
    }


def resample_to_15m(df):
    ohlc = df['close'].resample('15min').ohlc()
    vol = df['volume'].resample('15min').sum()
    rez = ohlc.copy()
    rez['volume'] = vol
    for col in ['open', 'high', 'low']:
        rez[col] = df[col].resample('15min').last()
    for col in ['fiz_buy', 'fiz_sell', 'yur_buy', 'yur_sell', 'total_oi']:
        rez[col] = df[col].resample('15min').last().fillna(0)
    return rez.dropna()


def precompute_15m(df):
    d = df.copy()
    d['volume'] = d['volume'].astype(float)
    d['vma20'] = d['volume'].rolling(20).mean().fillna(d['volume'])
    d['vr'] = d['volume'] / d['vma20'].clip(lower=1)
    d['vz'] = rz(d['volume'], 20)
    d['fiz_net'] = d['fiz_buy'].fillna(0) - d['fiz_sell'].fillna(0)
    d['yur_net'] = d['yur_buy'].fillna(0) - d['yur_sell'].fillna(0)
    d['fz'] = rz(d['fiz_net'], 20)
    d['yz'] = rz(d['yur_net'], 20)
    d['oi_r'] = (d['yur_buy'] + d['yur_sell']).fillna(0) / (d['fiz_buy'] + d['fiz_sell'] + 1).fillna(0)
    d['oima'] = d['oi_r'].rolling(20).mean()
    d['atr14'] = calc_atr(d)
    d['atr_pct'] = d['atr14'] / d['close'].clip(lower=1) * 100
    d['adx14'] = calc_adx(d)

    vs = np.clip((d['vr'] - 1.5) / 3.0, 0, 1)
    os_ = np.clip((d['oima'] - d['oi_r']) / d['oima'].clip(lower=0.1), 0, 1)
    raw = vs * 0.6 + os_ * 0.4
    af = np.clip(1 - (d['atr_pct'] - 0.3) / 3.0, 0, 1)
    score = np.clip(raw * af * np.clip(1 + d['vz'] / 5, 0.5, 1.5), 0, 1)
    d['score'] = score
    d['score_conf'] = d['score']
    return d


def precompute_new_score(df, acc_df=None):
    d = df.copy()
    d['volume'] = d['volume'].astype(float)
    d['vma20'] = d['volume'].rolling(20).mean().fillna(d['volume'])
    d['vr'] = d['volume'] / d['vma20'].clip(lower=1)
    d['vz'] = rz(d['volume'], 20)
    d['fiz_net'] = d['fiz_buy'].fillna(0) - d['fiz_sell'].fillna(0)
    d['yur_net'] = d['yur_buy'].fillna(0) - d['yur_sell'].fillna(0)
    d['fz'] = rz(d['fiz_net'], 20)
    d['yz'] = rz(d['yur_net'], 20)
    d['oi_r'] = (d['yur_buy'] + d['yur_sell']).fillna(0) / (d['fiz_buy'] + d['fiz_sell'] + 1).fillna(0)
    d['oima'] = d['oi_r'].rolling(20).mean()
    d['atr14'] = calc_atr(d)
    d['atr_pct'] = d['atr14'] / d['close'].clip(lower=1) * 100
    d['adx14'] = calc_adx(d)

    d['oi_accel'] = d['oi_r'].diff().rolling(5).mean()
    d['fiz_yur_delta'] = (d['fiz_net'] - d['yur_net']).abs() / (d['fiz_net'].abs() + d['yur_net'].abs() + 1)

    vs = np.clip((d['vr'] - 1.5) / 3.0, 0, 1)
    os_ = np.clip((d['oima'] - d['oi_r']) / d['oima'].clip(lower=0.1), 0, 1)
    af = np.clip(1 - (d['atr_pct'] - 0.3) / 3.0, 0, 1)
    score = af * (vs * 0.3 + os_ * 0.7 + d['oi_accel'] * 0.5 + d['fiz_yur_delta'] * 0.3)
    d['score'] = np.clip(score, 0, 1)

    if acc_df is not None and len(acc_df) > 0:
        d = d.join(acc_df, how='left').fillna(0)
        d['fiz_vol_pa'] = d['fiz_net'].abs() / (d['fiz_buy_a'] + d['fiz_sell_a'] + 1)
        d['yur_a_change'] = d['yur_buy_a'] - d['yur_sell_a']
        d['yur_a_z'] = rz(d['yur_a_change'], 20)
        d['conc'] = np.clip(d['fiz_vol_pa'] / 1000.0, 0, 1)
        d['yur_conf'] = np.clip(d['yur_a_z'] / 2.0, 0, 1)
        d['score_conf'] = np.clip(d['score'] * (1 + d['conc'] * 0.5 + d['yur_conf'] * 0.3), 0, 1)
    else:
        d['score_conf'] = d['score']

    return d


def build_supercandles(df, volume_col='volume'):
    vol_target = df[volume_col].rolling(20).mean().mean() * 3
    bars = []
    current = None
    for i in range(len(df)):
        row = df.iloc[i]
        if current is None:
            current = {
                'time': row.name, 'open': row['open'], 'high': row['high'],
                'low': row['low'], 'close': row['close'], 'volume': row[volume_col],
                'fiz_buy': row.get('fiz_buy', 0), 'fiz_sell': row.get('fiz_sell', 0),
                'yur_buy': row.get('yur_buy', 0), 'yur_sell': row.get('yur_sell', 0),
                'total_oi': row.get('total_oi', 0),
            }
        else:
            current['high'] = max(current['high'], row['high'])
            current['low'] = min(current['low'], row['low'])
            current['close'] = row['close']
            current['volume'] += row[volume_col]
            current['fiz_buy'] += row.get('fiz_buy', 0)
            current['fiz_sell'] += row.get('fiz_sell', 0)
            current['yur_buy'] += row.get('yur_buy', 0)
            current['yur_sell'] += row.get('yur_sell', 0)
            current['total_oi'] = row.get('total_oi', current['total_oi'])
        if current['volume'] >= vol_target:
            bars.append(current)
            current = None
    if current is not None:
        bars.append(current)
    rez = pd.DataFrame(bars)
    if len(rez) > 0:
        rez.set_index('time', inplace=True)
    return rez


def run_symbol(sym, all_cfgs):
    print(f"  Loading {sym}...", end=' ', flush=True)
    t0 = time.time()
    df = load_data(sym)
    acc_df = load_accounts(sym)
    print(f"{len(df)} bars in {time.time()-t0:.1f}s", flush=True)

    # Precompute base data once
    d_base = precompute_base(df, acc_df)

    results = {}
    for cfg in all_cfgs:
        name = cfg['name']
        print(f"    {name}...", end=' ', flush=True)
        t1 = time.time()

        if cfg.get('timeframe') == 'M15':
            df_15m = resample_to_15m(df)
            d = precompute_15m(df_15m)
        elif cfg.get('timeframe') == 'supercandle':
            df_sc = build_supercandles(df)
            d = precompute_15m(df_sc)
        elif cfg.get('new_score'):
            d = precompute_new_score(df, acc_df)
        else:
            d = d_base

        lot_pct = cfg.get('lot_pct', 0.25)
        bars_left = cfg.get('bars_left', 13)
        stop_atr = cfg.get('stop_atr', 2.0)
        score_col = cfg.get('score_col', 'score_conf')

        res = simulate_param(d, score_col, TEST_START, TEST_END,
                             sym=sym, name=name,
                             lot_pct=lot_pct, bars_left=bars_left,
                             stop_atr=stop_atr)

        print(f"+{res['return_pct']:.1f}% DD={res['max_dd_pct']:.1f}% "
              f"Calmar={res['calmar']:.2f} Trades={res['trades']} "
              f"({time.time()-t1:.1f}s)", flush=True)
        results[name] = res

    return results


def print_separator(char='=', width=110):
    print(f"\n{char * width}")


def main():
    print("=" * 90)
    print("  HyperDrive: 7 experiments + baseline")
    print("=" * 90)
    print(f"  Symbols: {SYMBOLS}")
    print(f"  Period: {TEST_START.date()} - {TEST_END.date()}")
    print(f"  Capital: {INITIAL_CAPITAL:,}")
    print()

    exp_configs = [
        {'name': 'BASE_lot25_bars13',       'lot_pct': 0.25, 'bars_left': 13, 'stop_atr': 2.0},
        {'name': 'E1_lot50',                'lot_pct': 0.50, 'bars_left': 13, 'stop_atr': 2.0},
        {'name': 'E2_bars4',                'lot_pct': 0.25, 'bars_left': 4,  'stop_atr': 2.0},
        {'name': 'E3_bars6',                'lot_pct': 0.25, 'bars_left': 6,  'stop_atr': 2.0},
        {'name': 'E4_M15',                  'timeframe': 'M15',        'lot_pct': 0.25, 'bars_left': 3,  'stop_atr': 1.0},
        {'name': 'E5_supercandle',          'timeframe': 'supercandle', 'lot_pct': 0.25, 'bars_left': 3,  'stop_atr': 1.0},
        {'name': 'E6_M15_lot50_bars4',      'timeframe': 'M15',        'lot_pct': 0.50, 'bars_left': 4,  'stop_atr': 1.0},
        {'name': 'E7_newscore_lot50_bars4', 'new_score': True,                        'lot_pct': 0.50, 'bars_left': 4,  'stop_atr': 1.0},
    ]
    exp_names = [e['name'] for e in exp_configs]

    all_results = {}
    for sym in SYMBOLS:
        all_results[sym] = run_symbol(sym, exp_configs)

    # Per-experiment table
    for exp_name in exp_names:
        print_separator('─')
        print(f"  {exp_name}")
        print_separator('─')
        print(f"  {'Ticker':<6} | {'Return%':>8} | {'CAGR%':>7} | {'DD%':>6} | {'Calmar':>7} | {'WR%':>6} | {'Trades':>7}")
        print(f"  {'-' * 60}")
        for sym in SYMBOLS:
            r = all_results[sym].get(exp_name, {})
            print(f"  {sym:<6} | {r.get('return_pct', 0):>7.1f}% | {r.get('cagr_pct', 0):>6.1f}% | "
                  f"{r.get('max_dd_pct', 0):>5.1f}% | {r.get('calmar', 0):>7.2f} | "
                  f"{r.get('wr_pct', 0):>5.1f}% | {r.get('trades', 0):>7}")

    # Portfolio average
    print_separator()
    print(f"{'PORTFOLIO AVERAGE (equal weight)':^110}")
    print_separator()
    print(f"  {'Experiment':<24} | {'Avg Ret%':>8} | {'Avg CAGR%':>9} | {'Avg DD%':>7} | {'Avg Calmar':>10} | {'Avg WR%':>8} | {'Σ Trades':>8}")
    print(f"  {'-' * 86}")

    portfolio_rows = []
    for exp_name in exp_names:
        rets = [all_results[sym][exp_name]['return_pct'] for sym in SYMBOLS]
        cagrs = [all_results[sym][exp_name]['cagr_pct'] for sym in SYMBOLS]
        dds = [all_results[sym][exp_name]['max_dd_pct'] for sym in SYMBOLS]
        calms = [all_results[sym][exp_name]['calmar'] for sym in SYMBOLS]
        wrs = [all_results[sym][exp_name]['wr_pct'] for sym in SYMBOLS]
        trades = [all_results[sym][exp_name]['trades'] for sym in SYMBOLS]

        avg_ret = sum(rets) / len(rets)
        avg_cagr = sum(cagrs) / len(cagrs)
        avg_dd = sum(dds) / len(dds)
        avg_calmar = sum(calms) / len(calms)
        avg_wr = sum(wrs) / len(wrs)
        total_trades = sum(trades)

        print(f"  {exp_name:<24} | {avg_ret:>7.1f}% | {avg_cagr:>8.1f}% | {avg_dd:>6.1f}% | "
              f"{avg_calmar:>10.2f} | {avg_wr:>7.1f}% | {total_trades:>8}")
        portfolio_rows.append({
            'experiment': exp_name,
            'avg_return_pct': round(avg_ret, 2),
            'avg_cagr_pct': round(avg_cagr, 2),
            'avg_dd_pct': round(avg_dd, 2),
            'avg_calmar': round(avg_calmar, 2),
            'avg_wr_pct': round(avg_wr, 2),
            'total_trades': total_trades,
        })

    # Save JSON
    os.makedirs('reports', exist_ok=True)
    output = {
        'metadata': {
            'symbols': SYMBOLS,
            'period': f"{TEST_START.date()} - {TEST_END.date()}",
            'initial_capital': INITIAL_CAPITAL,
            'description': 'HyperDrive experiments — see .hermes/plans/2026-06-13-hyperdrive-experiments.md',
        },
        'per_symbol': {
            sym: {k: dict(v) for k, v in sv.items()}
            for sym, sv in all_results.items()
        },
        'portfolio_avg': portfolio_rows,
    }
    out_path = 'reports/hyperdrive_results.json'
    with open(out_path, 'w') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"\n  Results saved to {out_path}")

    # Winner
    print_separator()
    best = max(portfolio_rows, key=lambda x: x['avg_calmar'])
    rank = sorted(portfolio_rows, key=lambda x: -x['avg_calmar'])
    print(f"  Ranking by Calmar:")
    for i, r in enumerate(rank, 1):
        medal = {1: '🥇', 2: '🥈', 3: '🥉'}.get(i, '  ')
        print(f"  {medal} #{i} {r['experiment']:<24} Calmar={r['avg_calmar']:.2f}  "
              f"Ret={r['avg_return_pct']:.1f}%  DD={r['avg_dd_pct']:.1f}%")
    print()


if __name__ == '__main__':
    main()
