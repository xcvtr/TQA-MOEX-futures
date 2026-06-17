#!/usr/bin/env python3
"""
Per-ticker grid search параметров для Phase 5 стратегии на 5m.
Для каждого из 14 тикеров портфеля подбирает оптимальные параметры
(volume_ema, score_ema, threshold, hold_bars) по метрике return/max_dd на OOS.
"""
import sys, os, json, time, pickle
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bar_level_sim import TICKER_CONFIGS

# ─── Портфель Phase 5 ───
PORTFOLIO = {
    'core': [
        ('GL','vod','L',21,2,1.0), ('RN','vou','L',5,5,1.0),
        ('AL','vou','L',21,2,1.0), ('HY','vou','L',5,5,1.0),
        ('NM','vod','L',21,3,1.0), ('AF','sm','L',21,2,1.0),
        ('SR','sm','L',8,5,1.0),   ('Si','vyf','L',13,2,1.0),
        ('SN','vou','L',5,5,1.0),  ('YD','vod','L',13,5,1.0),
    ],
    'hedge': [
        ('BR','vyf','S',13,5,1.0), ('SV','vod','S',5,5,1.0),
        ('SF','vod','S',8,3,1.0),  ('NG','vyf','S',5,5,1.0),
    ],
}

INITIAL_CAPITAL = 100_000
TEST_START = '2025-01-01'
TEST_END = '2026-04-30'

# ─── Grid параметров ───
VOLUME_EMA_VALS = [20, 30, 40, 60, 80]
SCORE_EMA_VALS = [0, 6, 12, 20]
THRESHOLD_VALS = [0.30, 0.40, 0.50, 0.60]
HOLD_BARS_VALS = [24, 48, 72, 96]

def rz(s, w=20):
    """Rolling z-score."""
    m = s.rolling(w, min_periods=w).mean()
    std = s.rolling(w, min_periods=w).std()
    return (s - m) / std.clip(lower=1e-10)

def calc_atr(df, p=14):
    """ATR с shift(1) для prev close."""
    prev = df['close'].shift(1)
    tr = pd.concat([
        df['high'] - df['low'],
        (df['high'] - prev).abs(),
        (df['low'] - prev).abs()
    ], axis=1).max(axis=1)
    return tr.rolling(p, min_periods=p).mean().bfill().fillna(0)

def compute_score(df, pattern, direction, volume_ema, score_ema):
    """
    Вычислить score для одного тикера и одного паттерна.
    df: DataFrame с колонками OHLCV+OI
    Возвращает Series score (0-1).
    """
    d = df.copy()
    dm = 1 if direction == 'L' else -1

    # Volume ratio
    d['vol_ema'] = d['volume'].ewm(span=volume_ema, adjust=False).mean()
    d['vr'] = d['volume'] / d['vol_ema'].clip(lower=1e-10)

    # Volume score
    # Для vod/vou/vyf: vs = clip((vr - 1.5) / 3.0, 0, 1)
    # Для sm: иначе (пока тоже самое, как в tf_sweep_fast.py)
    if pattern == 'sm':
        # Для sm vs не используется напрямую — raw считается по yz/fz
        vs = np.clip((d['vr'] - 1.5) / 3.0, 0, 1)
    else:
        vs = np.clip((d['vr'] - 1.5) / 3.0, 0, 1)

    # Volume z-score
    d['vz'] = rz(d['volume'], 20)  # всегда 20

    # OI ratio и score
    has_oi = all(c in d.columns for c in ['fiz_buy', 'fiz_sell', 'yur_buy', 'yur_sell'])
    if has_oi:
        d['oi_r'] = (d['yur_buy'].fillna(0) + d['yur_sell'].fillna(0)) / \
                     (d['fiz_buy'].fillna(0) + d['fiz_sell'].fillna(0) + 1)
        # oi_r_ema всегда 20
        d['oi_r_ema'] = d['oi_r'].ewm(span=20, adjust=False).mean()

    # ATR
    d['atr14'] = calc_atr(d)
    d['atr_pct'] = d['atr14'] / d['close'].clip(lower=1) * 100

    # ─── Raw score ───
    if pattern in ('vod', 'vou'):
        if has_oi:
            if pattern == 'vod':
                os_ = np.clip((d['oi_r_ema'] - d['oi_r']) / d['oi_r_ema'].clip(lower=0.1), 0, 1)
            else:  # vou
                os_ = np.clip((d['oi_r'] - d['oi_r_ema']) / d['oi_r_ema'].clip(lower=0.1), 0, 1)
        else:
            os_ = 0.5
        raw = vs * 0.6 + os_ * 0.4

    elif pattern == 'sm':
        if has_oi:
            d['fiz_net'] = d['fiz_buy'].fillna(0) - d['fiz_sell'].fillna(0)
            d['yur_net'] = d['yur_buy'].fillna(0) - d['yur_sell'].fillna(0)
            d['fz'] = rz(d['fiz_net'], 20)
            d['yz'] = rz(d['yur_net'], 20)
            raw = np.clip(abs(d['yz']) / 3.0, 0, 1) * 0.7 + np.clip(abs(d['fz']) / 3.0, 0, 1) * 0.3
        else:
            raw = np.clip((d['vr'] - 1.5) / 3.0, 0, 1)

    elif pattern == 'vyf':
        vs_vyf = np.clip((d['vr'] - 2.0) / 4.0, 0, 1)
        if has_oi:
            d['yur_net'] = d['yur_buy'].fillna(0) - d['yur_sell'].fillna(0)
            ys = np.clip(d['yur_net'].fillna(0) / max(d['yur_net'].std(), 1) * dm, 0, 1)
        else:
            ys = np.clip((d['close'] - d['close'].shift(1)) / d['close'].shift(1).clip(lower=1) * 50, 0, 1)
        raw = vs_vyf * 0.5 + ys * 0.5

    else:
        raw = np.clip((d['vr'] - 2.5) / 5.0, 0, 1)

    # ATR factor
    af = np.clip(1 - (d['atr_pct'] - 0.3) / 3.0, 0, 1)

    # Score
    score = np.clip(raw * af * np.clip(1 + d['vz'] / 5, 0.5, 1.5), 0, 1)

    # EMA smoothing on score
    if score_ema > 0:
        score = score.ewm(span=score_ema, adjust=False).mean()

    return score.clip(0, 1)


def simulate_one_ticker(df, score_series, direction, threshold, hold_bars, atr_mult, go):
    """
    Изолированная симуляция одного тикера.
    Начальный капитал = 100,000.
    Kelly = 40-150%.
    Вход по score >= threshold на баре t, цена close[t].
    Выход: stop по ATR или time-stop по hold_bars.
    NO score fade exit, NO slippage, NO re-entry delay.
    Возвращает dict с метриками.
    """
    d = df.copy()
    d['score'] = score_series

    # Маска тестового периода
    mask = (d.index >= TEST_START) & (d.index <= TEST_END)
    d_test = d[mask].copy()

    if len(d_test) < 10:
        return None

    # ATR для стопов
    if 'atr14' not in d_test.columns:
        d_test['atr14'] = calc_atr(d_test)
    # Stop distance
    stop_dist = d_test['atr14'] * atr_mult
    stop_dist = stop_dist.fillna(0)

    # Bars in test period
    cash = float(INITIAL_CAPITAL)
    peak = cash
    max_dd = 0.0

    # Kelly history
    kelly_hist = {'w': 0, 'l': 0, 'pnl': []}

    trades = []
    in_position = False
    entry_price = 0.0
    entry_bar_idx = -1
    direction_mult = 1 if direction == 'L' else -1
    entry_score = 0.0

    total_bars = len(d_test)

    for i in range(total_bars):
        bar = d_test.iloc[i]
        sc = float(bar['score'])
        cp = float(bar['close'])

        # ── Check if in position ──
        if in_position:
            bars_held = i - entry_bar_idx

            # ATR stop check
            stop_hit = False
            if direction == 'L':
                stop_price = entry_price - stop_dist.iloc[entry_bar_idx]
                if bar['low'] <= stop_price:
                    exit_price = stop_price
                    stop_hit = True
            else:  # Short
                stop_price = entry_price + stop_dist.iloc[entry_bar_idx]
                if bar['high'] >= stop_price:
                    exit_price = stop_price
                    stop_hit = True

            # Time stop
            if not stop_hit and bars_held >= hold_bars:
                exit_price = cp
                stop_hit = True

            if stop_hit:
                ret = direction_mult * (exit_price - entry_price) / max(entry_price, 1e-10)
                pnl_rub = ret * go * contracts
                cash += pnl_rub

                trades.append({
                    'entry_time': d_test.index[entry_bar_idx],
                    'exit_time': d_test.index[i],
                    'direction': direction,
                    'entry': entry_price,
                    'exit': exit_price,
                    'pnl_rub': pnl_rub,
                    'ret_pct': ret * 100,
                    'reason': 'stop' if stop_hit and bars_held < hold_bars else 'time',
                    'score': entry_score,
                })

                if pnl_rub > 0:
                    kelly_hist['w'] += 1
                else:
                    kelly_hist['l'] += 1
                kelly_hist['pnl'].append(pnl_rub)
                if len(kelly_hist['pnl']) > 50:
                    kelly_hist['pnl'].pop(0)

                in_position = False
                entry_bar_idx = -1

        # ── Equity curve update ──
        current_equity = cash
        if in_position:
            mtm = direction_mult * (cp - entry_price) / max(entry_price, 1e-10) * go * contracts
            current_equity = cash + mtm

        if current_equity > peak:
            peak = current_equity
        dd = (peak - current_equity) / max(peak, 1e-10)
        if dd > max_dd:
            max_dd = dd

        # ── Entry logic ──
        if not in_position and not pd.isna(sc) and sc >= threshold:
            # Kelly sizing
            kelly_base = 0.40  # min
            if kelly_hist['w'] + kelly_hist['l'] >= 10:
                wr = kelly_hist['w'] / max(kelly_hist['w'] + kelly_hist['l'], 1)
                avg_win = max(sum(p for p in kelly_hist['pnl'] if p > 0) / max(kelly_hist['w'], 1), 1)
                avg_loss = max(abs(sum(p for p in kelly_hist['pnl'] if p < 0) / max(kelly_hist['l'], 1)), 1)
                rr = avg_win / max(avg_loss, 0.5)
                k = wr - (1 - wr) / max(rr, 0.5)
                kelly_base = max(0.40, min(k, 1.50))
            else:
                kelly_base = 0.40

            pct = min(kelly_base * sc, 0.35)
            mr = cash * pct
            contracts = max(1, int(mr / go))

            if contracts > 0:
                in_position = True
                entry_price = cp
                entry_bar_idx = i
                entry_score = sc

    # ── Close any remaining position at end of period ──
    if in_position and total_bars > 0:
        last_bar = d_test.iloc[-1]
        cp = float(last_bar['close'])
        ret = direction_mult * (cp - entry_price) / max(entry_price, 1e-10)
        pnl_rub = ret * go * contracts
        cash += pnl_rub
        trades.append({
            'entry_time': d_test.index[entry_bar_idx],
            'exit_time': d_test.index[-1],
            'direction': direction,
            'entry': entry_price,
            'exit': cp,
            'pnl_rub': pnl_rub,
            'ret_pct': ret * 100,
            'reason': 'eod',
            'score': entry_score,
        })

    # ── Metrics ──
    n_trades = len(trades)
    if n_trades < 10:
        return None

    return_pct = (cash - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100
    max_dd_pct = max_dd * 100
    calmar = return_pct / max_dd_pct if max_dd_pct > 0 else 0.0

    # Trading days in test period
    test_dates = d_test.index.normalize().unique()
    trading_days = len(test_dates)
    trades_per_day = n_trades / max(trading_days, 1)

    return {
        'return_pct': round(return_pct, 1),
        'max_dd_pct': round(max_dd_pct, 1),
        'calmar': round(calmar, 2),
        'n_trades': n_trades,
        'trades_per_day': round(trades_per_day, 2),
        'final_capital': round(cash, 0),
    }


def get_config(symbol, pattern, direction):
    """Получить hold и atr_mult из портфеля для тикера+паттерна."""
    for lst in PORTFOLIO.values():
        for c in lst:
            if c[0] == symbol and c[1] == pattern and c[2] == direction:
                return c[3], c[4]  # hold, atr_mult
    return 21, 2.0  # default


def grid_search_ticker(data_5m, symbol):
    """Grid search для одного тикера. Возвращает TOP 5 комбинаций."""
    if symbol not in data_5m:
        print(f"  ⚠ {symbol}: нет данных в cache")
        return []

    # Найти паттерн и направление для этого тикера
    symbol_info = None
    for lst_name, lst in PORTFOLIO.items():
        for c in lst:
            if c[0] == symbol:
                symbol_info = (c[1], c[2], c[3], c[4], lst_name)  # pattern, direction, hold, atr_mult, role
                break
        if symbol_info:
            break

    if not symbol_info:
        print(f"  ⚠ {symbol}: нет в портфеле")
        return []

    pattern, direction, port_hold, atr_mult, role = symbol_info
    df = data_5m[symbol].copy()
    go = TICKER_CONFIGS.get(symbol, {}).get('go', 5000)

    print(f"\n  {symbol}: pattern={pattern}, direction={direction}, role={role}, "
          f"atr_mult={atr_mult}, go={go}, bars={len(df)}")

    results = []
    total_combo = len(VOLUME_EMA_VALS) * len(SCORE_EMA_VALS) * len(THRESHOLD_VALS) * len(HOLD_BARS_VALS)
    combo_idx = 0

    for vol_ema in VOLUME_EMA_VALS:
        for sc_ema in SCORE_EMA_VALS:
            for th in THRESHOLD_VALS:
                for hold in HOLD_BARS_VALS:
                    combo_idx += 1
                    if combo_idx % 20 == 0 or combo_idx == total_combo:
                        print(f"    [{combo_idx}/{total_combo}] vol_ema={vol_ema} score_ema={sc_ema} th={th} hold={hold}...")

                    # Compute score
                    score_series = compute_score(df, pattern, direction, vol_ema, sc_ema)

                    # Simulate
                    sim_result = simulate_one_ticker(
                        df, score_series, direction, th, hold, atr_mult, go
                    )

                    if sim_result is None:
                        continue

                    results.append({
                        'volume_ema': vol_ema,
                        'score_ema': sc_ema,
                        'threshold': th,
                        'hold_bars': hold,
                        **sim_result,
                    })

    if not results:
        return []

    # Sort by metric = return_pct / max_dd_pct (Calmar-like)
    # Higher calmar = better
    results.sort(key=lambda r: r['calmar'], reverse=True)

    top5 = results[:5]
    return top5


def main():
    print("=" * 70)
    print("Per-Ticker Grid Search — Phase 5 Strategy on 5m")
    print(f"Period: {TEST_START} to {TEST_END}")
    print(f"Grid: vol_ema={VOLUME_EMA_VALS}")
    print(f"      score_ema={SCORE_EMA_VALS}")
    print(f"      threshold={THRESHOLD_VALS}")
    print(f"      hold_bars={HOLD_BARS_VALS}")
    print(f"Metric: return_pct / max_dd_pct (min 10 trades)")
    print("=" * 70)

    # Load data
    print("\nLoading data from pickle...")
    with open('.tf_sweep_data.pkl', 'rb') as f:
        data_5m = pickle.load(f)
    print(f"Loaded {len(data_5m)} tickers")

    all_symbols = set()
    for lst in PORTFOLIO.values():
        for c in lst:
            all_symbols.add(c[0])
    all_symbols = sorted(all_symbols)
    print(f"Symbols to optimize: {all_symbols}")

    overall_results = {}
    t_start = time.time()

    for sym in all_symbols:
        print(f"\n{'=' * 60}")
        print(f"=== {sym} ===")
        print(f"{'=' * 60}")
        t0 = time.time()

        info = None
        for lst_name, lst in PORTFOLIO.items():
            for c in lst:
                if c[0] == sym:
                    info = (c[1], c[2], c[3], c[4], lst_name)
                    break
            if info:
                break

        if info:
            pat, di, hold, atm, role = info
            print(f"  ({pat}, {di}, hold={hold}, atm={atm}, role={role})")

        top5 = grid_search_ticker(data_5m, sym)

        elapsed = time.time() - t0

        if not top5:
            print(f"  ✗ No valid parameter combinations (min 10 trades)")
            overall_results[sym] = {
                'pattern': pat if info else '?',
                'direction': di if info else '?',
                'role': role if info else '?',
                'atr_mult': atm if info else '?',
                'portfolio_hold': hold if info else '?',
                'top_results': [],
                'time_s': round(elapsed, 1),
            }
            continue

        print(f"\n  TOP 5 combinations (time: {elapsed:.1f}s):")
        for i, r in enumerate(top5):
            print(f"    {i+1}. vol_ema={r['volume_ema']} score_ema={r['score_ema']} "
                  f"th={r['threshold']:.2f} hold={r['hold_bars']} → "
                  f"ret={r['return_pct']:+.1f}% DD={r['max_dd_pct']:.1f}% "
                  f"Calmar={r['calmar']:.2f} trades={r['n_trades']} ({r['trades_per_day']:.1f}/day)")

        overall_results[sym] = {
            'pattern': pat if info else '?',
            'direction': di if info else '?',
            'role': role if info else '?',
            'atr_mult': atm if info else '?',
            'portfolio_hold': hold if info else '?',
            'top_results': top5,
            'time_s': round(elapsed, 1),
        }

    total_time = time.time() - t_start

    # ─── Print summary ───
    print(f"\n\n{'=' * 70}")
    print("SUMMARY — Best per-ticker parameters (5m)")
    print(f"{'=' * 70}")
    print(f"{'Ticker':6} {'Best params':40} {'Ret':>8} {'DD':>7} {'Calmar':>8} {'Trades':>7} {'/day':>6}")
    print(f"{'─' * 70}")

    for sym in all_symbols:
        if sym not in overall_results:
            continue
        o = overall_results[sym]
        if not o['top_results']:
            print(f"{sym:6} {'NO VALID RESULTS':40} {'—':>8} {'—':>7} {'—':>8} {'—':>7} {'—':>6}")
            continue
        best = o['top_results'][0]
        pat, di = o['pattern'], o['direction']
        print(f"{sym:6} vol_ema={best['volume_ema']} sc_ema={best['score_ema']} "
              f"th={best['threshold']:.2f} hold={best['hold_bars']}"
              f"{'':5}"
              f"{best['return_pct']:>+8.1f}% "
              f"{best['max_dd_pct']:>6.1f}% "
              f"{best['calmar']:>8.2f} "
              f"{best['n_trades']:>7} "
              f"{best['trades_per_day']:>5.1f}")

    print(f"{'─' * 70}")
    print(f"Total time: {total_time:.0f}s ({total_time/60:.1f} min)")

    # ─── Save ───
    # Prepare clean JSON output
    json_output = {}
    for sym, o in overall_results.items():
        json_output[sym] = {
            'pattern': o['pattern'],
            'direction': o['direction'],
            'role': o['role'],
            'atr_mult': o['atr_mult'],
            'portfolio_hold': o['portfolio_hold'],
            'top_results': o['top_results'],
        }

    out_dir = 'reports/tf_sweep'
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, 'per_ticker_params.json')
    with open(out_path, 'w') as f:
        json.dump(json_output, f, indent=2, ensure_ascii=False)
    print(f"\nSaved: {out_path}")


if __name__ == '__main__':
    main()
