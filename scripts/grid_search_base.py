#!/usr/bin/env python3
"""
Grid search BASE — быстрый, векторизованный.
Все комбинации за 1 проход по барам (не перезагружая данные).
Много score_thresholds тестируются одновременно.
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
TEST_START = pd.Timestamp('2025-01-01')
TEST_END = pd.Timestamp('2026-05-01')
SYMBOL = 'GL'


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
    cols = ['time', 'open', 'high', 'low', 'close', 'volume',
            'fiz_buy', 'fiz_sell', 'yur_buy', 'yur_sell', 'total_oi']
    df = pd.DataFrame(r.result_rows, columns=cols)
    df['time'] = pd.to_datetime(df['time']).dt.tz_localize(None)
    df.set_index('time', inplace=True)
    return df


def load_accounts(sym):
    ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)
    q = f"""
        SELECT time, clgroup, buy_accounts, sell_accounts
        FROM moex.openinterest
        WHERE symbol='{sym}' AND time>='2023-01-01' AND time<='2026-04-30'
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


def grid_simulate_singlepass(d, score_thresholds, bars_lefts, stop_atrs, leverages,
                              go=5000):
    """Один проход по барам — все комбинации параллельно.

    Используем массив состояний: для каждой комбинации (sc, bl, sa, lv)
    храним cash, peak, max_dd, trades, wins, pos_entry, pos_stop, pos_bars_left, pos_active.

    Returns: list of dicts с теми же ключами что simulate.
    """
    mask = (d.index >= TEST_START) & (d.index < TEST_END)
    d = d[mask].copy()
    n_bars = len(d)
    if n_bars == 0:
        return []

    params = []
    for sc in score_thresholds:
        for bl in bars_lefts:
            for sa in stop_atrs:
                for lv in leverages:
                    params.append((sc, bl, sa, lv))
    n = len(params)

    # Состояния
    cash = np.full(n, INITIAL_CAPITAL, dtype=float)
    peak = np.full(n, INITIAL_CAPITAL, dtype=float)
    max_dd = np.zeros(n, dtype=float)
    trades = np.zeros(n, dtype=int)
    wins = np.zeros(n, dtype=int)
    pos_active = np.zeros(n, dtype=bool)
    pos_dir = np.ones(n, dtype=float)  # 1=L
    pos_entry = np.zeros(n, dtype=float)
    pos_stop = np.zeros(n, dtype=float)
    pos_bars_left = np.zeros(n, dtype=int)
    pos_go = np.full(n, go, dtype=float)
    pos_contracts = np.zeros(n, dtype=int)
    pos_max_rub = np.zeros(n, dtype=float)

    # Precompute arrays
    score_arr = d['score_conf'].values
    close_arr = d['close'].values.astype(float)
    low_arr = d['low'].values.astype(float)
    high_arr = d['high'].values.astype(float)
    atr_arr = d['atr14'].values
    hour_arr = np.array([ts.hour if hasattr(ts, 'hour') else pd.Timestamp(ts).hour
                         for ts in d.index])

    # Параметры как массивы
    score_thr_arr = np.array([p[0] for p in params], dtype=float)
    bars_left_arr = np.array([p[1] for p in params], dtype=int)
    stop_atr_arr = np.array([p[2] for p in params], dtype=float)
    lev_arr = np.array([p[3] for p in params], dtype=float)

    for i in range(1, n_bars):
        h = hour_arr[i]
        if h < 7 or h >= 23:
            continue

        clos = close_arr[i]
        lowv = low_arr[i]
        highv = high_arr[i]
        atrv = atr_arr[i]
        scv = score_arr[i]

        # --- EXIT: для всех активных позиций ---
        if pos_active.any():
            hit_low = lowv <= pos_stop
            hit_high = highv >= pos_stop
            pos_bars_left -= 1

            for j in range(n):
                if not pos_active[j]:
                    continue
                hit = False
                ep = clos
                if pos_dir[j] > 0:  # Long
                    if hit_low[j]:
                        hit = True
                        ep = pos_stop[j]
                else:  # Short
                    if hit_high[j]:
                        hit = True
                        ep = pos_stop[j]
                if not hit and pos_bars_left[j] <= 0:
                    hit = True
                if hit:
                    pp = pos_dir[j] * (ep - pos_entry[j]) / pos_entry[j]
                    pr = pp * pos_go[j] * pos_contracts[j]
                    cash[j] += pr
                    trades[j] += 1
                    if pr > 0:
                        wins[j] += 1
                    pos_active[j] = False

        # --- MTM / DD ---
        for j in range(n):
            if pos_active[j]:
                dm = pos_dir[j]
                mtm = dm * (clos - pos_entry[j]) / pos_entry[j] * pos_go[j] * pos_contracts[j]
                teq = cash[j] + mtm
            else:
                teq = cash[j]
            if teq > peak[j]:
                peak[j] = teq
            ddv = (peak[j] - teq) / peak[j] if peak[j] > 0 else 0
            if ddv > max_dd[j]:
                max_dd[j] = ddv

        # --- ENTRY: для всех свободных ---
        if np.isnan(scv):
            continue
        inactive = ~pos_active
        if not inactive.any():
            continue

        # Score filter
        ok_score = (score_thr_arr <= scv) & inactive

        for j in np.where(ok_score)[0]:
            max_rub_v = cash[j] * lev_arr[j]
            contracts = max(1, int(max_rub_v / go))
            stop_p = clos - atrv * stop_atr_arr[j]
            pos_active[j] = True
            pos_dir[j] = 1.0
            pos_entry[j] = clos
            pos_stop[j] = stop_p
            pos_bars_left[j] = bars_left_arr[j]
            pos_go[j] = go
            pos_contracts[j] = contracts

    # --- Close остаточных ---
    last_c = close_arr[-1]
    for j in range(n):
        if pos_active[j]:
            pp = pos_dir[j] * (last_c - pos_entry[j]) / pos_entry[j]
            pr = pp * pos_go[j] * pos_contracts[j]
            cash[j] += pr
            trades[j] += 1
            if pr > 0:
                wins[j] += 1

    # --- Итоговые метрики ---
    results = []
    days = (TEST_END - TEST_START).days
    years = max(days / 365.25, 0.1)
    for j in range(n):
        tr = (cash[j] - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100
        cagr = ((cash[j] / INITIAL_CAPITAL) ** (1 / years) - 1) * 100 if cash[j] > 0 else -100
        dd = max_dd[j] * 100
        calmar = tr / 100 / max(max_dd[j], 0.001) if max_dd[j] > 0 else tr * 10
        wr = wins[j] / trades[j] * 100 if trades[j] > 0 else 0
        sc, bl, sa, lv = params[j]
        results.append({
            'sc': sc, 'bl': bl, 'sa': sa, 'lv': lv,
            'ret': round(tr, 2), 'dd': round(dd, 2),
            'calmar': round(calmar, 2), 'wr': round(wr, 2),
            'trades': trades[j]
        })
    return results


def print_table(results, title, top_n=20):
    print(f"\n{title}")
    print(f"{'Score':>6} {'Bars':>5} {'Stop':>5} {'Lev':>5} {'Ret%':>7} {'DD%':>6} {'Calmar':>7} {'WR%':>6} {'Tr':>5}")
    print('-' * 58)
    for r in results[:top_n]:
        print(f"{r['sc']:>6.2f} {r['bl']:>5} {r['sa']:>5.1f} {r['lv']:>5.2f} "
              f"{r['ret']:>6.1f}% {r['dd']:>5.1f}% {r['calmar']:>7.1f} {r['wr']:>5.1f}% {r['trades']:>5}")
    print()

def main():
    print("Загрузка данных GL...")
    t0 = time.time()
    df = load_data(SYMBOL)
    acc_df = load_accounts(SYMBOL)
    d = precompute_base(df, acc_df)
    print(f"  {len(d)} баров, предвыч. за {time.time()-t0:.1f}s", flush=True)

    go = TICKER_CONFIGS.get(SYMBOL, {}).get('go', 5000)

    # === Раунд 1: score × bars_left (fix stop=2.0, lev=0.25) ===
    print(f"\n{'='*70}")
    print("Раунд 1: score_thresh × bars_left (72 комбинации)")
    print(f"{'='*70}", flush=True)
    t1 = time.time()

    score_thresholds = [0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.50, 0.60]
    bars_lefts = [5, 8, 10, 13, 16, 21, 26, 34]

    r1 = grid_simulate_singlepass(d, score_thresholds, bars_lefts,
                                   [2.0], [0.25], go)
    r1.sort(key=lambda x: x['calmar'], reverse=True)
    print(f"  {len(r1)} в {time.time()-t1:.1f}s")
    print_table(r1, "Топ-20 (score × bars_left, stop=2.0, lev=0.25):", 20)

    # Выбираем топ-3 combo по Calmar
    top3 = r1[:3]
    print("Топ-3 combo для Раунда 2:")
    for r in top3:
        print(f"  score>{r['sc']:.2f}, bars={r['bl']}: "
              f"Ret={r['ret']:.1f}%, DD={r['dd']:.1f}%, Calmar={r['calmar']:.1f}, "
              f"сделок={r['trades']}", flush=True)

    # === Раунд 2: stop × leverage (3×6×7 = 126 комбинаций) ===
    print(f"\n{'='*70}")
    print("Раунд 2: stop_atr × leverage (126 комбинаций)")
    print(f"{'='*70}", flush=True)
    t2 = time.time()

    stops = [1.0, 1.5, 2.0, 2.5, 3.0, 4.0]
    levs = [0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50]
    top_scs = [r['sc'] for r in top3]
    top_bls = [r['bl'] for r in top3]

    # Для каждого из топ-3: grid stop×lev
    all_r2 = []
    for sc, bl in zip(top_scs, top_bls):
        rr = grid_simulate_singlepass(d, [sc], [bl], stops, levs, go)
        all_r2.extend(rr)

    all_r2.sort(key=lambda x: x['calmar'], reverse=True)
    print(f"  {len(all_r2)} в {time.time()-t2:.1f}s")
    print_table(all_r2, "Топ-20 (stop × leverage):", 20)

    best = all_r2[0]
    print(f"\nЛУЧШИЙ НА GL:")
    print(f"  score>{best['sc']:.2f}, bars={best['bl']}, stop={best['sa']:.1f}A, lev={best['lv']:.2f}")
    print(f"  Ret={best['ret']:.1f}%, DD={best['dd']:.1f}%, Calmar={best['calmar']:.1f}, "
          f"WR={best['wr']:.1f}%, сделок={best['trades']}")

    # === BASE ===
    base_r = grid_simulate_singlepass(d, [0.25], [13], [2.0], [0.25], go)[0]
    print(f"\nBASE:")
    print(f"  score>0.25, bars=13, stop=2.0A, lev=0.25")
    print(f"  Ret={base_r['ret']:.1f}%, DD={base_r['dd']:.1f}%, Calmar={base_r['calmar']:.1f}, "
          f"WR={base_r['wr']:.1f}%, сделок={base_r['trades']}")

    imp = (best['calmar'] / base_r['calmar'] - 1) * 100
    if best['calmar'] > base_r['calmar']:
        print(f"\n✅ Улучшение Calmar на {imp:.0f}%")
    else:
        print(f"\n❌ BASE не побит на GL (Calmar: {best['calmar']:.1f} vs {base_r['calmar']:.1f})")

    # === Фаза 3: портфельный тест ===
    if best['calmar'] > base_r['calmar'] * 1.02:
        # Дополнительная верификация портфеля (упрощённо)
        print(f"\n{'='*70}")
        print(f"Фаза 3: портфельный тест лучшей комбинации")
        print(f"{'='*70}", flush=True)
        print(f"{'Тикер':<6} {'NEW':>7} {'DD':>5} {'Calm':>6} {'Tr':>5} | "
              f"{'BASE':>7} {'DD':>5} {'Calm':>6} {'Tr':>5} | Δ")
        print('-' * 65)
        for sym in ['GL', 'HS', 'HY', 'DX', 'RN', 'NM', 'AF']:
            df_sym = load_data(sym)
            acc_sym = load_accounts(sym)
            ds = precompute_base(df_sym, acc_sym)
            r_new = grid_simulate_singlepass(ds, [best['sc']], [best['bl']],
                                             [best['sa']], [best['lv']], 
                                             TICKER_CONFIGS.get(sym, {}).get('go', 5000))[0]
            r_old = grid_simulate_singlepass(ds, [0.25], [13], [2.0], [0.25],
                                             TICKER_CONFIGS.get(sym, {}).get('go', 5000))[0]
            dc = r_new['calmar'] - r_old['calmar']
            icon = '✅' if dc > 0.3 else ('❌' if dc < -0.3 else '➡️')
            print(f"{sym:<6} {r_new['ret']:>6.1f}% {r_new['dd']:>5.1f}% "
                  f"{r_new['calmar']:>6.1f} {r_new['trades']:>5} | "
                  f"{r_old['ret']:>6.1f}% {r_old['dd']:>5.1f}% "
                  f"{r_old['calmar']:>6.1f} {r_old['trades']:>5} | {dc:+.1f} {icon}")
            print(f"  {sym} done", flush=True)
    else:
        print(f"\nПортфельный тест не запущен — BASE не побит на GL")


if __name__ == '__main__':
    main()
