#!/usr/bin/env python3
"""Grid search stress test: all TF × params × weights × dir × hour с комиссиями 4₽, slippage 0.1%, SHORT."""
import sys, os, itertools, time
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
SLIPPAGE = 0.001

TIMEFRAMES = [('H1',60), ('H4',240), ('D1',1440)]

WEIGHT_PRESETS = [
    ('base',     (0.3, 0.7, 0.5, 0.3, 0.2)),
    ('accel',    (0.2, 0.3, 0.5, 0.4, 0.15)),
    ('bal',      (0.25, 0.25, 0.25, 0.25, 0.15)),
]

DIR_MODES = ['L', 'B']
HOUR_MODES = ['all']

PARAM_LOT = [0.10, 0.15, 0.20, 0.25]
PARAM_BARS = [8, 13, 21, 34]
PARAM_STOP = [1.5, 2.0, 3.0]
PARAM_SCORE = [0.15, 0.20, 0.25, 0.30]
ADX_MIN = 20
ATR_PCT_MIN = 0.1

MIN_TRADES = 50
MAX_DD_PCT = 20


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


def calc_adx(df, p=14):
    high, low, close = df['high'], df['low'], df['close']
    up_move = high - high.shift(1)
    down_move = low.shift(1) - low
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    tr = pd.concat([high-low, (high-close.shift(1)).abs(), (low-close.shift(1)).abs()], axis=1).max(axis=1)
    atr = tr.rolling(p, min_periods=p).mean()
    plus_di = 100 * pd.Series(plus_dm, index=df.index).rolling(p, min_periods=p).mean() / atr.clip(lower=1e-10)
    minus_di = 100 * pd.Series(minus_dm, index=df.index).rolling(p, min_periods=p).mean() / atr.clip(lower=1e-10)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).clip(lower=1e-10)
    adx = dx.rolling(p, min_periods=p).mean()
    return adx.bfill().fillna(0)


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
    rez['open'] = df['open'].resample(f'{minutes}min').first()
    rez['high'] = df['high'].resample(f'{minutes}min').max()
    rez['low'] = df['low'].resample(f'{minutes}min').min()
    for col in ['fiz_buy','fiz_sell','yur_buy','yur_sell','total_oi']:
        rez[col] = df[col].resample(f'{minutes}min').last().fillna(0)
    return rez.dropna()


def precompute_base(df, weights):
    vs_w, os_w, oi_w, fyd_w, vz_w = weights
    d = df.copy()
    d['volume'] = d['volume'].astype(float)
    d['vma20'] = d['volume'].rolling(20).mean().fillna(d['volume'])
    d['vr'] = d['volume'] / d['vma20'].clip(lower=1)
    d['vz_raw'] = rz(d['volume'], 20).shift(1)
    d['vz'] = d['vz_raw']
    d['fiz_net'] = d['fiz_buy'].fillna(0) - d['fiz_sell'].fillna(0)
    d['yur_net'] = d['yur_buy'].fillna(0) - d['yur_sell'].fillna(0)
    d['oi_r'] = (d['yur_buy']+d['yur_sell']).fillna(0) / (d['fiz_buy']+d['fiz_sell']+1).fillna(0)
    d['oima'] = d['oi_r'].rolling(20).mean()
    d['atr14'] = calc_atr(d)
    d['atr_pct'] = d['atr14'] / d['close'].clip(lower=1) * 100
    d['oi_accel'] = d['oi_r'].diff().rolling(5).mean()
    d['fiz_yur_delta'] = (d['fiz_net'] - d['yur_net']).abs() / (d['fiz_net'].abs() + d['yur_net'].abs() + 1)
    d['adx'] = calc_adx(d)
    d['_vs'] = np.clip((d['vr']-1.5)/3.0, 0, 1)
    d['_os'] = np.clip((d['oima']-d['oi_r'])/d['oima'].clip(lower=0.1), 0, 1)
    d['_af'] = np.clip(1 - (d['atr_pct']-0.3)/3.0, 0, 1)
    raw = d['_vs']*vs_w + d['_os']*os_w + d['oi_accel']*oi_w + d['fiz_yur_delta']*fyd_w
    score = np.clip(raw * d['_af'] * np.clip(1 + d['vz']*vz_w, 0.5, 1.5), 0, 1)
    d['score'] = score
    return d


def recompute_score(d, weights):
    vs_w, os_w, oi_w, fyd_w, vz_w = weights
    raw = d['_vs']*vs_w + d['_os']*os_w + d['oi_accel']*oi_w + d['fiz_yur_delta']*fyd_w
    score = np.clip(raw * d['_af'] * np.clip(1 + d['vz']*vz_w, 0.5, 1.5), 0, 1)
    return score


def build_bar_index(data):
    go_map = {sym: TICKER_CONFIGS.get(sym, {}).get('go', 5000) for sym in SYMBOLS}
    bar_index = {}
    last_close = {sym: 0.0 for sym in SYMBOLS}
    for sym in SYMBOLS:
        d = data[sym]
        mask = (d.index >= TEST_START) & (d.index < TEST_END)
        dd = d[mask]
        for i in range(len(dd)):
            ts = dd.index[i]
            if ts not in bar_index:
                bar_index[ts] = {}
            h = ts.hour if hasattr(ts, 'hour') else pd.Timestamp(ts).hour
            close_val = float(dd.iloc[i]['close'])
            bar_index[ts][sym] = (close_val, float(dd.iloc[i]['low']),
                                  float(dd.iloc[i]['high']), float(dd.iloc[i]['score']),
                                  float(dd.iloc[i]['atr14']), h, float(dd.iloc[i]['adx']))
            last_close[sym] = close_val
    unique_times = sorted(bar_index.keys())
    return bar_index, unique_times, go_map, last_close


def simulate(bar_index, unique_times, go_map, last_close, lot_pct, bars_left, stop_atr,
             score_thresh, slippage_pct=SLIPPAGE, dir_mode='L', hour_mode='all',
             adx_min=0, atr_pct_min=0):
    cash = float(INITIAL_CAPITAL)
    positions = {}
    peak = INITIAL_CAPITAL
    max_dd = 0.0
    total_trades = 0
    total_commission = 0.0

    for ts in unique_times:
        bars = bar_index[ts]
        for sym in list(positions.keys()):
            if sym not in bars:
                continue
            close, low, high, _, _, _, _ = bars[sym]
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
            if sym in positions or sym not in bars:
                continue
            close, low, high, score, atr14, h, adx_val = bars[sym]
            if hour_mode == 'morning' and not (10 <= h < 14):
                continue
            if hour_mode == 'day' and not (15 <= h < 20):
                continue
            if h < 7 or h >= 23:
                continue
            if np.isnan(score):
                continue
            if adx_min > 0 and adx_val < adx_min:
                continue
            if atr_pct_min > 0 and (atr14 / close * 100) < atr_pct_min:
                continue
            if dir_mode == 'L':
                if score < score_thresh:
                    continue
                entry_dir = 'L'
                use_score = score
            elif dir_mode == 'S':
                if -score > score_thresh:
                    entry_dir = 'S'
                    use_score = -score
                else:
                    continue
            elif dir_mode == 'B':
                if abs(score) < score_thresh:
                    continue
                entry_dir = 'L' if score > 0 else 'S'
                use_score = abs(score)
            go = go_map[sym]
            max_rub = cash * lot_pct
            contracts = int(max_rub / go)
            if contracts < 1:
                continue
            needed = go * contracts
            if cash < needed:
                continue
            atrv = atr14 if not np.isnan(atr14) else 1.0
            if entry_dir == 'L':
                entry_price = close * (1 + slippage_pct)
                stop = entry_price - atrv * stop_atr
            else:
                entry_price = close * (1 - slippage_pct)
                stop = entry_price + atrv * stop_atr
            cash -= needed
            positions[sym] = {
                'dir': entry_dir, 'entry': entry_price, 'stop': stop,
                'bars_left': bars_left, 'go': go, 'contracts': contracts,
            }

        mtm_total = 0.0
        for sym, pos in positions.items():
            if sym in bars:
                c = bars[sym][0]
                dm = 1 if pos['dir'] == 'L' else -1
                mtm = dm * (c - pos['entry']) / pos['entry'] * pos['go'] * pos['contracts']
                mtm_total += mtm
        equity = cash + mtm_total + sum(p['go']*p['contracts'] for p in positions.values())
        if equity > peak:
            peak = equity
        ddv = (peak - equity) / peak if peak > 0 else 0
        if ddv > max_dd:
            max_dd = ddv

    for sym, pos in list(positions.items()):
        ep = last_close.get(sym, pos['entry'])
        dm = 1 if pos['dir'] == 'L' else -1
        pp = dm * (ep - pos['entry']) / pos['entry']
        pr = pp * pos['go'] * pos['contracts']
        comm = pos['contracts'] * COMMISSION
        cash += pr + pos['go'] * pos['contracts'] - comm
        total_trades += 1
        total_commission += comm

    final_equity = cash
    port_ret = (final_equity - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100
    days = (unique_times[-1] - unique_times[0]).total_seconds() / 86400
    years = max(days / 365.25, 0.1)
    cagr = ((final_equity / INITIAL_CAPITAL)**(1/years)-1)*100 if final_equity > 0 else -100
    calmar = port_ret/100/max(max_dd, 0.001) if max_dd > 0 else port_ret*10

    return {
        'final': round(final_equity),
        'ret_pct': round(port_ret, 1),
        'cagr_pct': round(cagr, 1),
        'dd_pct': round(max_dd*100, 1),
        'calmar': round(calmar, 1),
        'trades': total_trades,
        'commission': round(total_commission),
    }


def per_symbol_breakdown(data, params):
    """Run simulate per-symbol individually and return results."""
    print(f"\n  Per-symbol breakdown for top config:", flush=True)
    print(f"  TF={params['tf']} weights={params['w']} dir={params['dir']} "
          f"lot={params['lot']:.2f} bars={params['bars']} stop={params['stop']:.1f} "
          f"score={params['score']:.2f}", flush=True)
    results = {}
    for sym in SYMBOLS:
        sym_data = {sym: data[sym]}
        bi, ut, gm, lc = build_bar_index(sym_data)
        r = simulate(bi, ut, gm, lc, params['lot'], params['bars'], params['stop'],
                     params['score'], dir_mode=params['dir'], hour_mode=params['hour'],
                     adx_min=params.get('adx_min', ADX_MIN))
        results[sym] = r
        print(f"    {sym}: Ret={r['ret_pct']:>7.1f}% CAGR={r['cagr_pct']:>7.1f}% "
              f"DD={r['dd_pct']:.1f}% Calmar={r['calmar']:>7.1f} Tr={r['trades']:>4}", flush=True)
    return results


def main():
    print("="*80, flush=True)
    print("  STRESS TEST FULL — grid search: TF × weights × dir × params", flush=True)
    print(f"  Период: {TEST_START.date()} — {TEST_END.date()}", flush=True)
    print(f"  Комиссия: {COMMISSION}₽/сд · Slippage: {SLIPPAGE*100:.1f}%", flush=True)
    print(f"  ADX > {ADX_MIN} · ATR% > {ATR_PCT_MIN} · min_trades={MIN_TRADES} · max_dd={MAX_DD_PCT}%", flush=True)
    print("="*80, flush=True)

    t0 = time.time()
    print("\nLoading raw data...", flush=True)
    raw = {}
    for sym in SYMBOLS:
        print(f"  {sym}...", end=' ', flush=True)
        raw[sym] = load_data(sym)
        print(f"{len(raw[sym])} bars", flush=True)

    all_results = []

    for tf_name, tf_min in TIMEFRAMES:
        print(f"\n{'─'*80}", flush=True)
        print(f"  TIMEFRAME: {tf_name} ({tf_min}min)", flush=True)
        print(f"{'─'*80}", flush=True)
        data = {}
        for sym in SYMBOLS:
            d = resample_to_m(raw[sym], tf_min)
            data[sym] = precompute_base(d, (0.3, 0.7, 0.5, 0.3, 0.2))
        n_bars = min(len(v) for v in data.values()) if data else 0
        print(f"  Symbols prepared, min bars: {n_bars}", flush=True)

        for w_name, weights in WEIGHT_PRESETS:
            t_w = time.time()
            for sym in SYMBOLS:
                data[sym]['score'] = recompute_score(data[sym], weights)
            bar_index, unique_times, go_map, last_close = build_bar_index(data)
            print(f"  Weights '{w_name}' {weights} — {len(unique_times)} timestamps", flush=True)

            for dir_mode in DIR_MODES:
                for hour_mode in HOUR_MODES:
                    combo_count = len(PARAM_LOT)*len(PARAM_BARS)*len(PARAM_STOP)*len(PARAM_SCORE)
                    for lot, bars, stop, score_th in itertools.product(
                        PARAM_LOT, PARAM_BARS, PARAM_STOP, PARAM_SCORE
                    ):
                        r = simulate(bar_index, unique_times, go_map, last_close, lot, bars, stop,
                                     score_th, dir_mode=dir_mode, hour_mode=hour_mode,
                                     adx_min=ADX_MIN, atr_pct_min=ATR_PCT_MIN)
                        if r['cagr_pct'] > 0 and r['dd_pct'] < MAX_DD_PCT and r['trades'] >= MIN_TRADES:
                            all_results.append((tf_name, w_name, dir_mode, hour_mode,
                                                lot, bars, stop, score_th, r))
                            r_str = (f"  ✓ {tf_name:3s} {w_name:6s} {dir_mode} {hour_mode:8s}"
                                     f" lot={lot:.2f} bars={bars:2d} stop={stop:.1f} score={score_th:.2f}"
                                     f" → Ret={r['ret_pct']:>7.1f}% CAGR={r['cagr_pct']:>7.1f}%"
                                     f" DD={r['dd_pct']:.1f}% Calmar={r['calmar']:>7.1f}"
                                     f" Tr={r['trades']:>4}")
                            print(r_str, flush=True)
            dt = time.time() - t_w
            print(f"  └─ weights '{w_name}' done in {dt:.0f}s", flush=True)

    all_results.sort(key=lambda x: x[8]['calmar'], reverse=True)

    print(f"\n{'='*120}", flush=True)
    print(f"  ТОП-20 ПО CALMAR (с комиссиями {COMMISSION}₽, slippage {SLIPPAGE*100:.1f}%)")
    print(f"{'='*120}", flush=True)
    header = (f"  {'#':<3} {'TF':<4} {'W':<7} {'Dir':<4} {'Hour':<8} {'Lot':<5} {'Bars':<5}"
              f" {'Stop':<6} {'Score':<7} {'Ret%':>8} {'CAGR%':>8} {'DD%':>6}"
              f" {'Calmar':>8} {'Trades':>5} {'Comm':>8}")
    print(header)
    print(f"  {'─'*110}")
    for i, (tf, wn, dm, hm, lot, bars, stop, score_th, r) in enumerate(all_results[:20]):
        print(f"  {i+1:<3} {tf:<4} {wn:<7} {dm:<4} {hm:<8} {lot:<5.2f} {bars:<5} "
              f"{stop:<6.1f} {score_th:<7.2f} {r['ret_pct']:>7.1f}% {r['cagr_pct']:>7.1f}% "
              f"{r['dd_pct']:>5.1f}% {r['calmar']:>7.1f} {r['trades']:>5} {r['commission']:>7,}")

    print(f"\n  Всего успешных конфигов (CAGR>0, DD<{MAX_DD_PCT}%, trades>={MIN_TRADES}): {len(all_results)}", flush=True)
    print(f"  Общее время: {time.time()-t0:.0f}s", flush=True)

    top5 = all_results[:5]
    if top5:
        print(f"\n{'='*120}", flush=True)
        print(f"  PER-SYMBOL BREAKDOWN FOR TOP-5")
        print(f"{'='*120}", flush=True)
        for rank, (tf, wn, dm, hm, lot, bars, stop, score_th, r) in enumerate(top5):
            params = dict(tf=tf, w=wn, dir=dm, hour=hm,
                         lot=lot, bars=bars, stop=stop, score=score_th, adx_min=ADX_MIN)
            per_symbol_breakdown(data, params)


if __name__ == '__main__':
    main()
