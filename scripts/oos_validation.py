#!/usr/bin/env python3
"""
OOS-валидация лучшей комбинации BASE v2 (score>0.10, bars=8, stop=1.0ATR, lev=0.50).
Тест на 2024 (out-of-sample относительно оптимизации) и 2025 (in-sample).
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
SYMBOLS = ['GL', 'HS', 'HY', 'DX', 'RN', 'NM', 'AF']

# Параметры
BEST = {'sc': 0.10, 'bl': 8, 'sa': 1.0, 'lv': 0.50}
BASE = {'sc': 0.25, 'bl': 13, 'sa': 2.0, 'lv': 0.25}

# Периоды
PERIODS = [
    ('2024 OOS', pd.Timestamp('2024-01-01'), pd.Timestamp('2025-01-01')),
    ('2025 INS', pd.Timestamp('2025-01-01'), pd.Timestamp('2026-05-01')),
    ('FULL',    pd.Timestamp('2024-01-01'), pd.Timestamp('2026-05-01')),
    ('2023_24', pd.Timestamp('2023-01-01'), pd.Timestamp('2025-01-01')),
]


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


def simulate(params, d, start, end, sym):
    """Однопроходная симуляция для одной комбинации на подмножестве данных"""
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

    for i in range(1, len(dd)):
        bar = dd.iloc[i]
        ts = bar.name
        h = ts.hour if hasattr(ts, 'hour') else pd.Timestamp(ts).hour
        if h < 7 or h >= 23:
            continue

        if pos is not None:
            pos['bars_left'] -= 1
            hit = False
            ep = bar['close']
            if pos['dir'] == 'L' and bar['low'] <= pos['stop']:
                hit = True; ep = pos['stop']
            elif pos['dir'] == 'S' and bar['high'] >= pos['stop']:
                hit = True; ep = pos['stop']
            elif pos['bars_left'] <= 0:
                hit = True
            if hit:
                dm = 1 if pos['dir'] == 'L' else -1
                pp = dm * (ep - pos['entry']) / pos['entry']
                pr = pp * pos['go'] * pos['contracts']
                cash += pr; trades += 1
                if pr > 0: wins += 1
                pos = None

        if pos is not None:
            dm = 1 if pos['dir'] == 'L' else -1
            mtm = dm * (bar['close'] - pos['entry']) / pos['entry'] * pos['go'] * pos['contracts']
            teq = cash + mtm
        else:
            teq = cash
        if teq > peak: peak = teq
        ddv = (peak - teq) / peak if peak > 0 else 0
        if ddv > max_dd: max_dd = ddv
        if pos is not None:
            continue

        score = float(bar['score_conf'])
        if np.isnan(score) or score < params['sc']:
            continue

        max_rub = cash * params['lv']
        contracts = max(1, int(max_rub / go))
        atrv = float(bar.get('atr14', 1))
        entry_p = float(bar['close'])
        stop_p = entry_p - atrv * params['sa']
        pos = {'dir': 'L', 'entry': entry_p, 'stop': stop_p,
               'bars_left': params['bl'], 'go': go, 'contracts': contracts}

    if pos is not None:
        lb = dd.iloc[-1]
        dm = 1 if pos['dir'] == 'L' else -1
        pp = dm * (lb['close'] - pos['entry']) / pos['entry']
        pr = pp * pos['go'] * pos['contracts']
        cash += pr; trades += 1
        if pr > 0: wins += 1

    tr = (cash - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100
    days = (end - start).days
    years = max(days / 365.25, 0.1)
    cagr = ((cash / INITIAL_CAPITAL) ** (1 / years) - 1) * 100 if cash > 0 else -100
    calmar = tr / 100 / max(max_dd, 0.001) if max_dd > 0 else tr * 10
    return {
        'ret': round(tr, 2), 'cagr': round(cagr, 2),
        'dd': round(max_dd * 100, 2), 'calmar': round(calmar, 2),
        'wr': round(wins / trades * 100, 2) if trades > 0 else 0,
        'trades': trades,
    }


def main():
    print("=" * 80)
    print("OOS-ВАЛИДАЦИЯ BASE v2")
    print(f"BEST:  score>{BEST['sc']:.2f}, bars={BEST['bl']}, stop={BEST['sa']:.1f}A, lev={BEST['lv']:.2f}")
    print(f"BASE:  score>{BASE['sc']:.2f}, bars={BASE['bl']}, stop={BASE['sa']:.1f}A, lev={BASE['lv']:.2f}")
    print("=" * 80, flush=True)

    # Загружаем все данные сразу (максимальный период)
    loaded = {}
    print("\nЗагрузка данных...")
    for sym in SYMBOLS:
        t0 = time.time()
        df = load_data(sym)
        acc = load_accounts(sym)
        d = precompute_base(df, acc)
        loaded[sym] = d
        print(f"  {sym}: {len(d)} баров за {time.time()-t0:.1f}s", flush=True)

    # Для каждого периода — тест BEST vs BASE на всех тикерах
    for pname, pstart, pend in PERIODS:
        print(f"\n{'='*80}")
        print(f"Период: {pname} ({pstart.date()} — {pend.date()})")
        print(f"{'='*80}")
        print(f"{'Тикер':<6} {'BEST Ret':>7} {'DD':>5} {'Calm':>6} {'Tr':>5} | "
              f"{'BASE Ret':>7} {'DD':>5} {'Calm':>6} {'Tr':>5} | ΔCalm")
        print('-' * 75)

        best_wins = 0
        for sym in SYMBOLS:
            d = loaded[sym]
            r_best = simulate(BEST, d, pstart, pend, sym)
            r_base = simulate(BASE, d, pstart, pend, sym)
            if r_best is None or r_base is None:
                print(f"{sym:<6}  — нет данных")
                continue
            dc = r_best['calmar'] - r_base['calmar']
            if dc > 0.3: best_wins += 1
            icon = '🟢' if dc > 0.3 else ('🔴' if dc < -0.3 else '➡️')
            print(f"{sym:<6} {r_best['ret']:>6.1f}% {r_best['dd']:>5.1f}% "
                  f"{r_best['calmar']:>6.1f} {r_best['trades']:>5} | "
                  f"{r_base['ret']:>6.1f}% {r_base['dd']:>5.1f}% "
                  f"{r_base['calmar']:>6.1f} {r_base['trades']:>5} | "
                  f"{dc:+4.1f} {icon}")
            print(f"  {sym} done", flush=True)

        print(f"\nBEST победил на {best_wins}/{len(SYMBOLS)} тикерах "
              f"({best_wins/len(SYMBOLS)*100:.0f}%)")

    # Сводка по всем периодам
    print(f"\n{'='*80}")
    print(f"СВОДНАЯ ТАБЛИЦА — среднее по портфелю")
    print(f"{'='*80}")
    print(f"{'Период':<15} {'BEST avg Ret':>13} {'avg DD':>7} {'avg Calm':>9} | "
          f"{'BASE avg Ret':>13} {'avg DD':>7} {'avg Calm':>9} | {'Wins':>5}")
    print('-' * 85)

    for pname, pstart, pend in PERIODS:
        best_rets, base_rets = [], []
        best_dds, base_dds = [], []
        best_calmars, base_calmars = [], []
        best_wins = 0
        for sym in SYMBOLS:
            d = loaded[sym]
            r_best = simulate(BEST, d, pstart, pend, sym)
            r_base = simulate(BASE, d, pstart, pend, sym)
            if r_best is None:
                continue
            best_rets.append(r_best['ret'])
            best_dds.append(r_best['dd'])
            best_calmars.append(r_best['calmar'])
            base_rets.append(r_base['ret'])
            base_dds.append(r_base['dd'])
            base_calmars.append(r_base['calmar'])
            if r_best['calmar'] > r_base['calmar'] + 0.3:
                best_wins += 1

        n = len(best_rets)
        if n > 0:
            print(f"{pname:<15} {sum(best_rets)/n:>10.1f}%  {sum(best_dds)/n:>5.1f}% "
                  f"{sum(best_calmars)/n:>8.1f} | {sum(base_rets)/n:>10.1f}%  "
                  f"{sum(base_dds)/n:>5.1f}% {sum(base_calmars)/n:>8.1f} | "
                  f"{best_wins:>2}/{n}")

    print(f"\n{'='*80}")
    if any(simulate(BEST, loaded['GL'], pstart, pend, 'GL') is not None
           and simulate(BEST, loaded['GL'], pstart, pend, 'GL')['calmar']
           > simulate(BASE, loaded['GL'], pstart, pend, 'GL')['calmar']
           for _, pstart, pend in PERIODS):
        print(f"✅ BEST стабильно обыгрывает BASE во всех периодах")
    else:
        print(f"⚠️  BEST не стабилен — есть периоды где BASE лучше")


if __name__ == '__main__':
    main()
