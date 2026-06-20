#!/usr/bin/env python3
"""
Walk-forward оптимизация BASE v2.
Скользящее окно: 6 мес train → 3 мес test.
На каждом train-окне — grid search по score, bars, stop, lev.
Лучшие параметры тестируются на следующем test-окне (out-of-sample).
Сбор OOS equity кривой + метрик.

АУДИТ: сравниваем walk-forward (адаптивные параметры) vs фиксированные BASE v2.
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
import clickhouse_connect
from bar_level_sim import TICKER_CONFIGS
from config import CH_HOST, CH_PORT, CH_DB

INITIAL_CAPITAL = 100_000
SYMBOLS = ['GL', 'HS', 'HY', 'RN', 'NM', 'AF']  # DX исключён — слабый

# Walk-forward параметры
TRAIN_M = 6
TEST_M = 3
START = pd.Timestamp('2023-07-01')  # первые 6 мес на预热
END = pd.Timestamp('2026-05-01')

# Grid search параметры (ограниченная сетка для скорости)
SCORES = [0.05, 0.10, 0.15, 0.20, 0.25, 0.30]
BARS = [5, 8, 10, 13, 16]
STOPS = [0.8, 1.0, 1.5, 2.0]
LEVS = [0.25, 0.35, 0.50]

# Фиксированные BASE v2 для сравнения
BEST_FIXED = {'sc': 0.10, 'bl': 8, 'sa': 1.0, 'lv': 0.50}
BASE_OLD = {'sc': 0.25, 'bl': 13, 'sa': 2.0, 'lv': 0.25}


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


def load_data(sym, start='2023-01-01', end='2026-05-01'):
    ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)
    q = f"""
        SELECT p.time, p.open, p.high, p.low, p.close, p.volume,
               o.fiz_buy, o.fiz_sell, o.yur_buy, o.yur_sell, o.total_oi
        FROM moex.prices_5m p
        LEFT JOIN moex.prices_5m_oi o ON p.time = o.time AND p.symbol = o.symbol
        WHERE p.symbol='{sym}' AND p.time>='{start}' AND p.time<='{end}'
        ORDER BY p.time
    """
    r = ch.query(q)
    cols = ['time', 'open', 'high', 'low', 'close', 'volume',
            'fiz_buy', 'fiz_sell', 'yur_buy', 'yur_sell', 'total_oi']
    df = pd.DataFrame(r.result_rows, columns=cols)
    df['time'] = pd.to_datetime(df['time']).dt.tz_localize(None)
    df.set_index('time', inplace=True)
    return df


def load_accounts(sym, start='2023-01-01', end='2026-05-01'):
    ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)
    q = f"""
        SELECT time, clgroup, buy_accounts, sell_accounts
        FROM moex.openinterest
        WHERE symbol='{sym}' AND time>='{start}' AND time<='{end}'
        ORDER BY time, clgroup
    """
    r = ch.query(q)
    rows = r.result_rows
    if not rows:
        return pd.DataFrame()
    recs = [{'time': r[0], 'clg': r[1], 'buy_a': r[2], 'sell_a': r[3]} for r in rows]
    df = pd.DataFrame(recs)
    df['time'] = pd.to_datetime(df['time']).dt.tz_localize(None)
    fiz = df[df['clg'] == 0][['time', 'buy_a', 'sell_a']].rename(
        columns={'buy_a': 'fiz_buy_a', 'sell_a': 'fiz_sell_a'})
    yur = df[df['clg'] == 1][['time', 'buy_a', 'sell_a']].rename(
        columns={'buy_a': 'yur_buy_a', 'sell_a': 'yur_sell_a'})
    merged = pd.merge(fiz, yur, on='time', how='outer').fillna(0)
    merged.set_index('time', inplace=True)
    return merged


def precompute_base(df, acc_df=None):
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

    vs = np.clip((d['vr'] - 1.5) / 3.0, 0, 1)
    os_ = np.clip((d['oima'] - d['oi_r']) / d['oima'].clip(lower=0.1), 0, 1)
    raw = vs * 0.6 + os_ * 0.4
    af = np.clip(1 - (d['atr_pct'] - 0.3) / 3.0, 0, 1)
    score = np.clip(raw * af * np.clip(1 + d['vz'] / 5, 0.5, 1.5), 0, 1)
    d['score'] = score

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


def grid_search_on_window(d, start, end, sym):
    """Однопроходный grid search на данном окне данных.
    Возвращает лучшие параметры по Calmar."""
    mask = (d.index >= start) & (d.index < end)
    dd = d[mask].copy()
    if len(dd) < 1000:
        return None

    params = []
    for sc in SCORES:
        for bl in BARS:
            for sa in STOPS:
                for lv in LEVS:
                    params.append({'sc': sc, 'bl': bl, 'sa': sa, 'lv': lv})
    n = len(params)

    # Состояния
    cash = np.full(n, INITIAL_CAPITAL, dtype=float)
    peak = np.full(n, INITIAL_CAPITAL, dtype=float)
    max_dd = np.zeros(n, dtype=float)
    trades = np.zeros(n, dtype=int)
    wins = np.zeros(n, dtype=int)
    pos_active = np.zeros(n, dtype=bool)
    pos_entry = np.zeros(n, dtype=float)
    pos_stop = np.zeros(n, dtype=float)
    pos_bars_left = np.zeros(n, dtype=int)
    pos_contracts = np.zeros(n, dtype=int)
    pos_go_val = TICKER_CONFIGS.get(sym, {}).get('go', 5000)

    score_arr = dd['score_conf'].values
    close_arr = dd['close'].values.astype(float)
    low_arr = dd['low'].values.astype(float)
    high_arr = dd['high'].values.astype(float)
    atr_arr = dd['atr14'].values
    hour_arr = np.array([ts.hour if hasattr(ts, 'hour') else pd.Timestamp(ts).hour
                         for ts in dd.index])

    sc_thr_arr = np.array([p['sc'] for p in params], dtype=float)
    bl_arr = np.array([p['bl'] for p in params], dtype=int)
    sa_arr = np.array([p['sa'] for p in params], dtype=float)
    lv_arr = np.array([p['lv'] for p in params], dtype=float)
    go_arr = np.full(n, pos_go_val, dtype=float)

    for i in range(1, len(dd)):
        h = hour_arr[i]
        if h < 7 or h >= 23:
            continue

        clos = close_arr[i]
        lowv = low_arr[i]
        highv = high_arr[i]
        atrv = atr_arr[i]
        scv = score_arr[i]

        active = pos_active.copy()
        if active.any():
            pos_bars_left -= 1
            hit_low_mask = (lowv <= pos_stop) & active
            hit_high_mask = (highv >= pos_stop) & active
            for j in np.where(active)[0]:
                hit = False
                ep = clos
                if hit_low_mask[j]:
                    hit = True; ep = pos_stop[j]
                elif hit_high_mask[j]:
                    hit = True; ep = pos_stop[j]
                elif pos_bars_left[j] <= 0:
                    hit = True
                if hit:
                    pp = (ep - pos_entry[j]) / pos_entry[j]
                    pr = pp * go_arr[j] * pos_contracts[j]
                    cash[j] += pr
                    trades[j] += 1
                    if pr > 0: wins[j] += 1
                    pos_active[j] = False

        for j in range(n):
            if pos_active[j]:
                mtm = (clos - pos_entry[j]) / pos_entry[j] * go_arr[j] * pos_contracts[j]
                teq = cash[j] + mtm
            else:
                teq = cash[j]
            if teq > peak[j]: peak[j] = teq
            ddv = (peak[j] - teq) / peak[j] if peak[j] > 0 else 0
            if ddv > max_dd[j]: max_dd[j] = ddv

        if np.isnan(scv) or scv == 0:
            continue
        inactive = ~pos_active
        if not inactive.any():
            continue

        ok = (sc_thr_arr <= scv) & inactive
        for j in np.where(ok)[0]:
            max_rub = cash[j] * lv_arr[j]
            ctr = max(1, int(max_rub / pos_go_val))
            stop_p = clos - atrv * sa_arr[j]
            pos_active[j] = True
            pos_entry[j] = clos
            pos_stop[j] = stop_p
            pos_bars_left[j] = bl_arr[j]
            pos_contracts[j] = ctr

    last_c = close_arr[-1] if len(dd) > 0 else 0
    for j in range(n):
        if pos_active[j]:
            pp = (last_c - pos_entry[j]) / pos_entry[j]
            pr = pp * go_arr[j] * pos_contracts[j]
            cash[j] += pr
            trades[j] += 1
            if pr > 0: wins[j] += 1

    # Best by Calmar
    best_idx = -1
    best_calmar = -999
    for j in range(n):
        if trades[j] < 50:
            continue
        tr = (cash[j] - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100
        calmar = tr / 100 / max(max_dd[j], 0.001) if max_dd[j] > 0 else tr * 10
        if calmar > best_calmar:
            best_calmar = calmar
            best_idx = j

    if best_idx < 0:
        return None
    return params[best_idx]


def simulate_equity(params, d, start, end, sym):
    """Симуляция с записью equity кривой"""
    mask = (d.index >= start) & (d.index < end)
    dd = d[mask].copy()
    if len(dd) == 0:
        return None

    cash = INITIAL_CAPITAL
    peak = INITIAL_CAPITAL
    max_dd = 0
    trades = 0
    wins = 0
    go = TICKER_CONFIGS.get(sym, {}).get('go', 5000)
    pos = None
    equities = []

    for i in range(1, len(dd)):
        bar = dd.iloc[i]
        ts = bar.name
        h = ts.hour if hasattr(ts, 'hour') else pd.Timestamp(ts).hour
        if h < 7 or h >= 23:
            continue
        if pos is not None:
            pos['bars_left'] -= 1
            hit = False; ep = bar['close']
            if pos['dir'] == 'L' and bar['low'] <= pos['stop']:
                hit = True; ep = pos['stop']
            elif pos['dir'] == 'S' and bar['high'] >= pos['stop']:
                hit = True; ep = pos['stop']
            elif pos['bars_left'] <= 0:
                hit = True
            if hit:
                pp = (ep - pos['entry']) / pos['entry']
                pr = pp * pos['go'] * pos['contracts']
                cash += pr; trades += 1
                if pr > 0: wins += 1
                pos = None
        if pos is not None:
            mtm = (bar['close'] - pos['entry']) / pos['entry'] * pos['go'] * pos['contracts']
            teq = cash + mtm
        else:
            teq = cash
        if teq > peak: peak = teq
        ddv = (peak - teq) / peak if peak > 0 else 0
        if ddv > max_dd: max_dd = ddv
        equities.append(teq)
        if pos is not None:
            continue
        score = float(bar['score_conf'])
        if np.isnan(score) or score < params['sc']:
            continue
        max_rub = cash * params['lv']
        contracts = max(1, int(max_rub / go))
        atrv = float(bar.get('atr14', 1))
        stop_p = float(bar['close']) - atrv * params['sa']
        pos = {'dir': 'L', 'entry': float(bar['close']), 'stop': stop_p,
               'bars_left': params['bl'], 'go': go, 'contracts': contracts}

    if pos is not None:
        lb = dd.iloc[-1]
        pp = (lb['close'] - pos['entry']) / pos['entry']
        pr = pp * pos['go'] * pos['contracts']
        cash += pr; trades += 1
        if pr > 0: wins += 1
        mtm = 0
        teq = cash
        equities[-1] = teq

    tr = (cash - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100
    days = (end - start).days
    years = max(days / 365.25, 0.1)
    cagr = ((cash / INITIAL_CAPITAL) ** (1 / years) - 1) * 100 if cash > 0 else -100
    calmar = tr / 100 / max(max_dd, 0.001) if max_dd > 0 else tr * 10
    return {
        'ret': round(tr, 2), 'cagr': round(cagr, 2),
        'dd': round(max_dd * 100, 2), 'calmar': round(calmar, 2),
        'wr': round(wins / trades * 100, 2) if trades > 0 else 0,
        'trades': trades, 'equity': equities,
    }


def main():
    print("=" * 90)
    print("WALK-FORWARD ОПТИМИЗАЦИЯ BASE v2")
    print(f"Окно: {TRAIN_M}m train → {TEST_M}m test")
    print(f"Сетка: scores{SCORES}, bars{BARS}, stops{STOPS}, levs{LEVS}")
    print(f"Тикеры: {SYMBOLS}")
    print("=" * 90, flush=True)

    # Загрузка данных
    loaded = {}
    print("\nЗагрузка данных...")
    for sym in SYMBOLS:
        t0 = time.time()
        df = load_data(sym)
        acc = load_accounts(sym)
        d = precompute_base(df, acc)
        loaded[sym] = d
        print(f"  {sym}: {len(d)} баров за {time.time()-t0:.1f}s", flush=True)

    # Walk-forward окна
    windows = []
    t = START
    while t + pd.DateOffset(months=TRAIN_M + TEST_M) <= END:
        train_end = t + pd.DateOffset(months=TRAIN_M)
        test_end = train_end + pd.DateOffset(months=TEST_M)
        windows.append((t, train_end, test_end))
        t = train_end  # скользящее, без перекрытия train

    print(f"\nОкон: {len(windows)}")
    print(f"Период: {windows[0][0].date()} — {windows[-1][2].date()}")
    print()

    # Для каждого тикера — walk-forward
    all_results = {}
    for sym in SYMBOLS:
        print(f"\n{'='*80}")
        print(f"  {sym} — Walk-forward")
        print(f"{'='*80}", flush=True)
        d = loaded[sym]

        # Equity кривые (OOS)
        wf_equities = []
        wf_timestamps = []
        wf_trades = []
        wf_params_log = []

        for train_start, train_end, test_end in windows:
            # Получаем данные для train (с запасом для индикаторов)
            train_start_padded = train_start - pd.DateOffset(months=3)

            # Grid search на train
            best_params = grid_search_on_window(d, train_start_padded, train_end, sym)
            if best_params is None:
                print(f"  ⚠️ {train_start.date()}-{train_end.date()}: нет параметров", flush=True)
                continue

            # Тест на test
            result = simulate_equity(best_params, d, train_end, test_end, sym)
            if result is None or result['trades'] < 5:
                print(f"  ⚠️ {train_start.date()}: test дал <5 сделок", flush=True)
                continue

            eq = result.pop('equity', [])
            wf_timestamps.extend(d.loc[train_end:test_end].index[:len(eq)].tolist())
            wf_equities.extend(eq)
            wf_trades.append(result['trades'])

            log_entry = f"  {train_start.date()}–{train_end.date()} → test {train_end.date()}–{test_end.date()}: "
            log_entry += f"score>{best_params['sc']:.2f}, bars={best_params['bl']}, "
            log_entry += f"stop={best_params['sa']:.1f}A, lev={best_params['lv']:.2f} → "
            log_entry += f"Ret={result['ret']:.1f}%, DD={result['dd']:.1f}%, Calmar={result['calmar']:.1f}, сделок={result['trades']}"
            wf_params_log.append(log_entry)
            print(log_entry, flush=True)

        # --- АУДИТ: WF vs фиксированные ---
        if len(wf_equities) > 0:
            wf_final = wf_equities[-1]
            wf_ret = (wf_final - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100
            wf_peak = max(wf_equities)
            wf_max_dd = max((wf_peak - e) / wf_peak for e in wf_equities) if wf_peak > 0 else 0
            wf_calmar = wf_ret / 100 / max(wf_max_dd, 0.001) if wf_max_dd > 0 else wf_ret * 10
            wf_total_trades = sum(wf_trades)

            # Фиксированные BASE v2 на том же периоде
            test_start = windows[0][1]
            test_end_final = windows[-1][2]
            best_fixed_result = simulate_equity(BEST_FIXED, d, test_start, test_end_final, sym)
            base_old_result = simulate_equity(BASE_OLD, d, test_start, test_end_final, sym)

            print(f"\n  ─── АУДИТ {sym} ───")
            print(f"  {train_start.date()} – {test_end_final.date()}")
            print(f"  WF (адаптивный):     Ret={wf_ret:.1f}%, DD={wf_max_dd*100:.1f}%, "
                  f"Calmar={wf_calmar:.1f}, сделок={wf_total_trades}")
            if best_fixed_result:
                print(f"  BASE v2 (фикс):      Ret={best_fixed_result['ret']:.1f}%, "
                      f"DD={best_fixed_result['dd']:.1f}%, "
                      f"Calmar={best_fixed_result['calmar']:.1f}, сделок={best_fixed_result['trades']}")
            if base_old_result:
                print(f"  BASE old (фикс):     Ret={base_old_result['ret']:.1f}%, "
                      f"DD={base_old_result['dd']:.1f}%, "
                      f"Calmar={base_old_result['calmar']:.1f}, сделок={base_old_result['trades']}")

            # Вердикт
            if best_fixed_result:
                wf_better = wf_calmar > best_fixed_result['calmar']
                print(f"  WF {'🟢 лучше' if wf_better else '🔴 хуже'} фиксированного BASE v2 "
                      f"(Δ={wf_calmar - best_fixed_result['calmar']:+.1f})")
            print(f"  Параметры по окнам:")
            for entry in wf_params_log[-5:]:  # последние 5
                print(entry)

            all_results[sym] = {
                'wf_ret': wf_ret, 'wf_dd': wf_max_dd * 100,
                'wf_calmar': wf_calmar, 'wf_trades': wf_total_trades,
                'fixed_ret': best_fixed_result['ret'] if best_fixed_result else None,
                'fixed_dd': best_fixed_result['dd'] if best_fixed_result else None,
                'fixed_calmar': best_fixed_result['calmar'] if best_fixed_result else None,
            }
        print()

    # === Финальная сводка ===
    print(f"\n{'='*90}")
    print(f"ФИНАЛЬНАЯ СВОДКА: WF vs BASE v2 (фикс)")
    print(f"{'='*90}")
    print(f"{'Тикер':<6} {'WF Ret':>7} {'DD':>5} {'Calm':>7} {'Tr':>5} | "
          f"{'V2 Ret':>7} {'DD':>5} {'Calm':>7} {'Tr':>5} | ΔCalm")
    print('-' * 75)

    wf_wins = 0
    for sym in SYMBOLS:
        r = all_results.get(sym)
        if r is None:
            continue
        dc = r['wf_calmar'] - (r['fixed_calmar'] or 0)
        icon = '🟢' if dc > 0.3 else ('🔴' if dc < -0.3 else '➡️')
        print(f"{sym:<6} {r['wf_ret']:>6.1f}% {r['wf_dd']:>5.1f}% "
              f"{r['wf_calmar']:>7.1f} {r['wf_trades']:>5} | "
              f"{r['fixed_ret']:>6.1f}% {r['fixed_dd']:>5.1f}% "
              f"{r['fixed_calmar']:>7.1f} {0:>5} | {dc:+5.1f} {icon}")
        if dc > 0.3:
            wf_wins += 1

    print(f"\nWF победил на {wf_wins}/{len(SYMBOLS)} тикерах ({wf_wins/len(SYMBOLS)*100:.0f}%)")
    if wf_wins >= len(SYMBOLS) / 2:
        print(f"✅ Walk-forward имеет смысл — адаптивные параметры лучше фикса")
    else:
        print(f"✅ Фиксированные BASE v2 оптимальны — WF не даёт преимущества")


if __name__ == '__main__':
    main()
