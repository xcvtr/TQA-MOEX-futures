#!/usr/bin/env python3
"""
Полный портфельный тест 3 вариантов на всех 7 тикерах.
BASE (conf) vs RAW_OI vs TOD_full_no_lunch.
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
SYMBOLS = ['GL', 'HS', 'HY', 'DX', 'RN', 'NM', 'AF']


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


def calc_adx(df, p=14):
    high = df['high'].values.astype(float)
    low = df['low'].values.astype(float)
    close = df['close'].values.astype(float)
    n = len(df)
    tr = np.zeros(n)
    plus_dm = np.zeros(n)
    minus_dm = np.zeros(n)
    for i in range(1, n):
        tr[i] = max(high[i] - low[i], abs(high[i] - close[i-1]), abs(low[i] - close[i-1]))
        up = high[i] - high[i-1]
        down = low[i-1] - low[i]
        plus_dm[i] = up if up > down and up > 0 else 0
        minus_dm[i] = down if down > up and down > 0 else 0
    atr = pd.Series(tr).rolling(p).mean().values
    pdi = pd.Series(plus_dm).rolling(p).mean().values / atr * 100
    ndi = pd.Series(minus_dm).rolling(p).mean().values / atr * 100
    dx = np.abs(pdi - ndi) / (pdi + ndi + 1e-10) * 100
    adx = pd.Series(dx).rolling(p).mean().values
    return adx


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
    d['adx14'] = calc_adx(d)

    # Score (vod_L)
    vs = np.clip((d['vr'] - 1.5) / 3.0, 0, 1)
    os_ = np.clip((d['oima'] - d['oi_r']) / d['oima'].clip(lower=0.1), 0, 1)
    raw = vs * 0.6 + os_ * 0.4
    af = np.clip(1 - (d['atr_pct'] - 0.3) / 3.0, 0, 1)
    score = np.clip(raw * af * np.clip(1 + d['vz'] / 5, 0.5, 1.5), 0, 1)
    d['score'] = score

    # Raw OI ratio
    yur_vol = (d['yur_buy'] + d['yur_sell']).clip(lower=1)
    d['oi_ratio'] = d['yur_net'] / yur_vol
    d['oi_ratio_z'] = rz(d['oi_ratio'], 20)

    # Conf score from accounts
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


def simulate(df, score_col, start, end, use_tod=False, tod_ranges=None,
             use_raw_oi=False, name="BASE", sym=None):
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

        # Exit
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

        # Time-of-day filter
        if use_tod:
            ok = False
            for lo, hi in tod_ranges:
                if lo <= h < hi:
                    ok = True
                    break
            if not ok:
                continue

        # Raw OI ratio
        if use_raw_oi:
            oi_r = float(bar.get('oi_ratio_z', 0))
            if np.isnan(oi_r):
                continue
            if abs(oi_r) < 1.5:
                continue

        go = TICKER_CONFIGS.get(sym, {}).get('go', 5000)
        max_rub = cash * 0.25
        contracts = max(1, int(max_rub / go))
        atrv = float(bar.get('atr14', 1))
        ep = float(bar['close'])
        stop = ep - atrv * 2
        pos = {'dir': 'L', 'entry': ep, 'stop': stop,
               'bars_left': 13, 'go': go, 'contracts': contracts}

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
        'filtered': 0,
    }


def process_symbol(sym):
    print(f"\n{'='*60}")
    print(f"  {sym}...")
    print(f"{'='*60}")
    t0 = time.time()
    df = load_data(sym)
    print(f"    баров: {len(df)} за {time.time()-t0:.1f}s", flush=True)
    acc_df = load_accounts(sym)
    d = precompute_base(df, acc_df)

    base = simulate(d, 'score_conf', TEST_START, TEST_END, name="BASE", sym=sym)
    print(f"    BASE: +{base['return_pct']:.1f}%, DD={base['max_dd_pct']:.1f}%, "
          f"Calmar={base['calmar']:.1f}, сделок={base['trades']}", flush=True)

    raw_oi = simulate(d, 'score', TEST_START, TEST_END, use_raw_oi=True, name="RAW_OI", sym=sym)
    print(f"    RAW_OI: +{raw_oi['return_pct']:.1f}%, DD={raw_oi['max_dd_pct']:.1f}%, "
          f"Calmar={raw_oi['calmar']:.1f}, сделок={raw_oi['trades']}", flush=True)

    tod = simulate(d, 'score_conf', TEST_START, TEST_END,
                   use_tod=True, tod_ranges=[(10, 14), (15, 20)],
                   name="TOD_full", sym=sym)
    print(f"    TOD_full: +{tod['return_pct']:.1f}%, DD={tod['max_dd_pct']:.1f}%, "
          f"Calmar={tod['calmar']:.1f}, сделок={tod['trades']}", flush=True)

    return {'sym': sym, 'base': base, 'raw_oi': raw_oi, 'tod': tod}


def main():
    results = []
    for sym in SYMBOLS:
        results.append(process_symbol(sym))

    # Сводная таблица
    print(f"\n\n{'='*100}")
    print(f"{'Сводка по портфелю':^100}")
    print(f"{'='*100}")
    header = f"{'Тикер':<6} | {'BASE Ret':>8} {'DD':>5} {'Calm':>6} {'Tr':>5} | {'RAW_OI Ret':>8} {'DD':>5} {'Calm':>6} {'Tr':>5} | {'TOD Ret':>8} {'DD':>5} {'Calm':>6} {'Tr':>5}"
    print(header)
    print('-' * len(header))
    for r in results:
        b, ro, td = r['base'], r['raw_oi'], r['tod']
        print(f"{r['sym']:<6} | {b['return_pct']:>7.1f}% {b['max_dd_pct']:>5.1f}% {b['calmar']:>6.1f} {b['trades']:>5} "
              f"| {ro['return_pct']:>7.1f}% {ro['max_dd_pct']:>5.1f}% {ro['calmar']:>6.1f} {ro['trades']:>5} "
              f"| {td['return_pct']:>7.1f}% {td['max_dd_pct']:>5.1f}% {td['calmar']:>6.1f} {td['trades']:>5}")

    # Итоговый вердикт
    print(f"\n\n{'='*100}")
    print(f"{'Вердикт':^100}")
    print(f"{'='*100}")
    base_ok = sum(1 for r in results if r['base']['calmar'] >= 3)
    print(f"Тикеров где BASE Calmar>=3: {base_ok}/{len(results)}")

    best_count = {'BASE': 0, 'RAW_OI': 0, 'TOD': 0}
    best_calmar = {'BASE': 0, 'RAW_OI': 0, 'TOD': 0}
    for r in results:
        b, ro, td = r['base']['calmar'], r['raw_oi']['calmar'], r['tod']['calmar']
        best = max([('BASE', b), ('RAW_OI', ro), ('TOD', td)], key=lambda x: x[1])
        best_count[best[0]] += 1
        for k in best_calmar:
            best_calmar[k] += (r['base']['calmar'] if k == 'BASE'
                               else r['raw_oi']['calmar'] if k == 'RAW_OI'
                               else r['tod']['calmar'])

    print(f"Побед по Calmar: BASE={best_count['BASE']}, RAW_OI={best_count['RAW_OI']}, TOD={best_count['TOD']}")
    print(f"Средний Calmar: BASE={best_calmar['BASE']/len(results):.1f}, "
          f"RAW_OI={best_calmar['RAW_OI']/len(results):.1f}, "
          f"TOD={best_calmar['TOD']/len(results):.1f}")

    # Суммарно по портфелю (равновесный)
    print(f"\n{'='*100}")
    print(f"{'Суммарный портфельный результат (среднее по тикерам)':^100}")
    print(f"{'='*100}")
    avg_base_ret = sum(r['base']['return_pct'] for r in results) / len(results)
    avg_ro_ret = sum(r['raw_oi']['return_pct'] for r in results) / len(results)
    avg_tod_ret = sum(r['tod']['return_pct'] for r in results) / len(results)
    print(f"  BASE avg return: {avg_base_ret:.1f}%")
    print(f"  RAW_OI avg return: {avg_ro_ret:.1f}%")
    print(f"  TOD_full avg return: {avg_tod_ret:.1f}%")

    wins_base = sum(1 for r in results if r['base']['calmar'] >= r['raw_oi']['calmar']
                    and r['base']['calmar'] >= r['tod']['calmar'])
    print(f"\n  BASE лучший на {wins_base}/{len(results)} тикерах")
    print(f"  {'✅ BASE остаётся базой' if wins_base >= len(results)/2 else '❌ BASE не доминирует'}")


if __name__ == '__main__':
    main()
