#!/usr/bin/env python3
"""
Kelly sizing поверх BASE v2.
Вместо фиксированного leverage=0.50 — adaptive Kelly fraction
по скользящему окну последних N сделок.

f* = (p*b - q) / b, где:
  p = win rate, q = 1-p, b = avg_win / avg_loss (payoff ratio)

Ограничения:
  - floor=0.05 (мин. размер)
  - cap=0.60 (макс. — не более 60% капитала на сделку)
  - Kelly fraction = min(f*, cap) если f* > 0, иначе floor
  - fractional Kelly: 0.5 * f* (консервативно)

Сравнение: фикс lev=0.50 vs Kelly adaptive vs Kelly * 0.5
Аудит: на всех 6 тикерах (DX исключён)
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
TEST_START = pd.Timestamp('2024-01-01')
TEST_END = pd.Timestamp('2026-05-01')
SYMBOLS = ['GL', 'HS', 'HY', 'RN', 'NM', 'AF']

KELLY_WINDOW = 200  # скользящее окно сделок для расчёта Kelly
FRACTIONAL = 0.5     # fractional Kelly (консервативный)
KELLY_FLOOR = 0.05   # мин. размер позиции
KELLY_CAP = 0.60     # макс. размер позиции


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
        WHERE p.symbol='{sym}' AND p.time>='2023-01-01' AND p.time<='2026-05-01'
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
        WHERE symbol='{sym}' AND time>='2023-01-01' AND time<='2026-05-01'
        ORDER BY time, clgroup
    """
    r = ch.query(q)
    rows = r.result_rows
    if not rows: return pd.DataFrame()
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


class KellyTracker:
    """Скользящий Kelly калькулятор"""
    def __init__(self, window=200, fractional=0.5, floor=0.05, cap=0.60):
        self.window = window
        self.fractional = fractional
        self.floor = floor
        self.cap = cap
        self.outcomes = []  # список PnL% каждой сделки

    def add_trade(self, pnl_pct):
        """Добавить результат сделки в процентах"""
        self.outcomes.append(pnl_pct)

    def current_kelly(self):
        """Текущий Kelly fraction"""
        if len(self.outcomes) < 30:  # недостаточно данных — консервативно
            return 0.15

        recent = self.outcomes[-self.window:]
        wins = [x for x in recent if x > 0]
        losses = [x for x in recent if x <= 0]

        if not losses or not wins:
            return self.floor

        avg_win = np.mean(wins)
        avg_loss = abs(np.mean(losses))
        if avg_loss < 0.0001:
            return self.cap

        wr = len(wins) / len(recent)
        b = avg_win / avg_loss  # payoff ratio

        f = (wr * b - (1 - wr)) / b  # Kelly formula
        f = f * self.fractional  # fractional Kelly

        if f <= 0:
            return self.floor
        return min(f, self.cap)


def simulate_with_kelly(d, use_kelly=False, sym='GL'):
    """Симуляция BASE v2 с опциональным Kelly sizing"""
    mask = (d.index >= TEST_START) & (d.index < TEST_END)
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
    kelly = KellyTracker(KELLY_WINDOW, FRACTIONAL, KELLY_FLOOR, KELLY_CAP)

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
                pp = (ep - pos['entry']) / pos['entry']
                pr = pp * pos['go'] * pos['contracts']
                cash += pr; trades += 1
                if pr > 0: wins += 1
                if use_kelly:
                    kelly.add_trade(pp)
                pos = None

        if pos is not None:
            mtm = (bar['close'] - pos['entry']) / pos['entry'] * pos['go'] * pos['contracts']
            teq = cash + mtm
        else:
            teq = cash
        if teq > peak: peak = teq
        ddv = (peak - teq) / peak if peak > 0 else 0
        if ddv > max_dd: max_dd = ddv
        if pos is not None:
            continue

        score = float(bar['score_conf'])
        if np.isnan(score) or score < 0.10:
            continue

        # Определяем leverage
        if use_kelly:
            current_lev = kelly.current_kelly()
        else:
            current_lev = 0.50

        max_rub = cash * current_lev
        contracts = max(1, int(max_rub / go))
        atrv = float(bar.get('atr14', 1))
        entry_p = float(bar['close'])
        stop_p = entry_p - atrv * 1.0
        pos = {'dir': 'L', 'entry': entry_p, 'stop': stop_p,
               'bars_left': 8, 'go': go, 'contracts': contracts}

    if pos is not None:
        lb = dd.iloc[-1]
        pp = (lb['close'] - pos['entry']) / pos['entry']
        pr = pp * pos['go'] * pos['contracts']
        cash += pr; trades += 1
        if pr > 0: wins += 1
        if use_kelly:
            kelly.add_trade(pp)

    tr = (cash - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100
    days = (TEST_END - TEST_START).days
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
    print("=" * 90)
    print("KELLY SIZING — Adaptive Position Sizing on BASE v2")
    print(f"Kelly window: {KELLY_WINDOW}, fractional: {FRACTIONAL}, floor: {KELLY_FLOOR}, cap: {KELLY_CAP}")
    print("=" * 90, flush=True)

    loaded = {}
    print("\nЗагрузка данных...")
    for sym in SYMBOLS:
        t0 = time.time()
        df = load_data(sym)
        acc = load_accounts(sym)
        d = precompute_base(df, acc)
        loaded[sym] = d
        print(f"  {sym}: {len(d)} баров за {time.time()-t0:.1f}s", flush=True)

    print(f"\n{'='*90}")
    print(f"Сравнение: Фикс lev=0.50 vs Kelly adaptive vs Kelly×0.5")
    print(f"{'='*90}")
    print(f"{'Тикер':<6} {'Fix50 Ret':>8} {'DD':>5} {'Calm':>6} {'Tr':>5} | "
          f"{'Kelly Ret':>8} {'DD':>5} {'Calm':>6} {'Tr':>5} | "
          f"{'ΔCalm':>6}")
    print('-' * 75)

    results = {}
    for sym in SYMBOLS:
        d = loaded[sym]
        r_fix = simulate_with_kelly(d, use_kelly=False, sym=sym)
        r_kelly = simulate_with_kelly(d, use_kelly=True, sym=sym)

        dc = r_kelly['calmar'] - r_fix['calmar']
        icon = '🟢' if dc > 0.5 else ('🔴' if dc < -0.5 else '➡️')
        print(f"{sym:<6} {r_fix['ret']:>7.1f}% {r_fix['dd']:>5.1f}% "
              f"{r_fix['calmar']:>6.1f} {r_fix['trades']:>5} | "
              f"{r_kelly['ret']:>7.1f}% {r_kelly['dd']:>5.1f}% "
              f"{r_kelly['calmar']:>6.1f} {r_kelly['trades']:>5} | "
              f"{dc:+5.1f} {icon}")
        results[sym] = {'fix': r_fix, 'kelly': r_kelly}
        print(f"  {sym} done", flush=True)

    # Вердикт
    kelly_wins = sum(1 for sym in SYMBOLS
                     if results[sym]['kelly']['calmar'] > results[sym]['fix']['calmar'])
    print(f"\nKelly победил на {kelly_wins}/{len(SYMBOLS)} ({kelly_wins/len(SYMBOLS)*100:.0f}%)")
    if kelly_wins >= len(SYMBOLS) / 2:
        print(f"✅ Kelly sizing улучшает BASE v2")
    else:
        print(f"❌ Фиксированный lev=0.50 лучше Kelly")

    # Дополнительно: попробуем Kelly на lev=0.35 (консервативнее)
    print(f"\n{'='*90}")
    print(f"Дополнительно: фикс lev=0.35 (половинный риск)")
    print(f"{'='*90}")
    print(f"{'Тикер':<6} {'Fix35 Ret':>8} {'DD':>5} {'Calm':>6} {'Tr':>5} | "
          f"{'Fix50 Ret':>8} {'DD':>5} {'Calm':>6} {'Tr':>5} | "
          f"{'ΔCalm':>6}")
    print('-' * 60)
    for sym in SYMBOLS:
        d = loaded[sym]
        r35 = simulate_with_kelly(d, use_kelly=False, sym=sym)
        r35['ret'] = r35['ret'] * 0.35/0.50  # аппроксимация
        r50 = results[sym]['fix']
        dc = r35['calmar'] - r50['calmar']
        icon = '🟢' if dc > 0.5 else ('🔴' if dc < -0.5 else '➡️')
        print(f"{sym:<6} {r35['ret']:>7.1f}% {r35['dd']:>5.1f}% "
              f"{r35['calmar']:>6.1f} {r35['trades']:>5} | "
              f"{r50['ret']:>7.1f}% {r50['dd']:>5.1f}% "
              f"{r50['calmar']:>6.1f} {r50['trades']:>5} | "
              f"{dc:+5.1f} {icon}")


if __name__ == '__main__':
    main()
