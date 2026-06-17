#!/usr/bin/env python3
"""
H1 portfolio simulation with slippage sweep.
Loads 5m data from pickle, resamples to H1, computes signals,
runs simulation with 4 slippage settings.
"""

import json, os, sys, pickle
from datetime import datetime, timedelta
from collections import defaultdict

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from bar_level_sim import TICKER_CONFIGS

INITIAL_CAPITAL = 100_000

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

TEST_END = '2026-04-30'

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

def resample_to_h1(d):
    """Resample 5m data to H1 with proper OHLCV+OI aggregation."""
    ohlc = {
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum',
        'fiz_buy': 'last',
        'fiz_sell': 'last',
        'yur_buy': 'last',
        'yur_sell': 'last',
    }
    return d.resample('1h').agg(ohlc)

def precompute_signals(data, symbols):
    signals = {}
    for sym in symbols:
        if sym not in data:
            continue
        d = data[sym].copy()
        d['volume'] = d['volume'].astype(float)
        d['vma20'] = d['volume'].rolling(20).mean().fillna(d['volume'])
        d['vr'] = d['volume'] / d['vma20'].clip(lower=1)
        d['vz'] = rz(d['volume'], 20)
        has_oi = 'fiz_buy' in d.columns
        if has_oi:
            d['fiz_net'] = d['fiz_buy'].fillna(0) - d['fiz_sell'].fillna(0)
            d['yur_net'] = d['yur_buy'].fillna(0) - d['yur_sell'].fillna(0)
            d['fz'] = rz(d['fiz_net'], 20)
            d['yz'] = rz(d['yur_net'], 20)
            d['oi_r'] = (d['yur_buy'] + d['yur_sell']).fillna(0) / (d['fiz_buy'] + d['fiz_sell'] + 1).fillna(0)
            d['oima'] = d['oi_r'].rolling(20).mean()
        d['atr14'] = calc_atr(d)
        d['atr_pct'] = d['atr14'] / d['close'].clip(lower=1) * 100

        sym_sigs = {}
        seen = set()
        for lst in PORTFOLIO.values():
            for c in lst:
                sn, pat, di, hold, atm = c[0], c[1], c[2], c[3], c[4]
                if sn != sym:
                    continue
                k = f"{pat}_{di}"
                if k in seen:
                    continue
                seen.add(k)
                dm = 1 if di == 'L' else -1
                if pat in ('vod', 'vou'):
                    vs = np.clip((d['vr'] - 1.5) / 3.0, 0, 1)
                    if has_oi:
                        if pat == 'vod':
                            os_ = np.clip((d['oima'] - d['oi_r']) / d['oima'].clip(lower=0.1), 0, 1)
                        else:
                            os_ = np.clip((d['oi_r'] - d['oima']) / d['oima'].clip(lower=0.1), 0, 1)
                    else:
                        os_ = 0.5
                    raw = vs * 0.6 + os_ * 0.4
                elif pat == 'sm':
                    if has_oi:
                        raw = np.clip(abs(d['yz']) / 3.0, 0, 1) * 0.7 + np.clip(abs(d['fz']) / 3.0, 0, 1) * 0.3
                    else:
                        raw = np.clip((d['vr'] - 1.5) / 3.0, 0, 1)
                elif pat == 'vyf':
                    vs = np.clip((d['vr'] - 2.0) / 4.0, 0, 1)
                    if has_oi:
                        ys = np.clip(d['yur_net'].fillna(0) / max(d['yur_net'].std(), 1) * dm, 0, 1)
                    else:
                        ys = np.clip((d['close'] - d['close'].shift(1)) / d['close'].shift(1).clip(lower=1) * 50, 0, 1)
                    raw = vs * 0.5 + ys * 0.5
                else:
                    raw = np.clip((d['vr'] - 2.5) / 5.0, 0, 1)
                af = np.clip(1 - (d['atr_pct'] - 0.3) / 3.0, 0, 1)
                score = np.clip(raw * af * np.clip(1 + d['vz'] / 5, 0.5, 1.5), 0, 1)
                dout = d.copy()
                dout['score'] = score
                sym_sigs[k] = (dout, di, hold, atm)
        signals[sym] = sym_sigs
    return signals


def simulate_period(data, signals, start, end, kelly_min, kelly_max, label, slippage=0.0):
    """
    Simulate with slippage.
    slippage: fractional cost (0.0 = none, 0.0001 = 0.01%)
    LONG: entry = close * (1 + slip), exit = close * (1 - slip), stop same
    SHORT: entry = close * (1 - slip), exit = close * (1 + slip), stop same
    """
    cash = INITIAL_CAPITAL
    peak = INITIAL_CAPITAL
    max_dd = 0
    kelly_hist = defaultdict(lambda: {'w': 0, 'l': 0, 'pnl': []})
    positions = {}
    all_trades = []

    all_ts = []
    for sym in data:
        for t in data[sym].index:
            t_naive = t.to_pydatetime().replace(tzinfo=None) if hasattr(t, 'tz') and t.tz else t
            if isinstance(t_naive, pd.Timestamp):
                t_naive = t_naive.to_pydatetime().replace(tzinfo=None)
            start_n = start
            end_n = end
            if start_n <= t_naive <= end_n:
                all_ts.append(t)
    all_ts = sorted(set(all_ts))

    for idx, ts in enumerate(all_ts):
        # Exits
        to_close = []
        for sym, pos in list(positions.items()):
            rs = pos.get('real_sym', sym)
            if rs not in data or ts not in data[rs].index:
                continue
            bar = data[rs].loc[ts]
            ep = None
            r = ''

            # Stop loss with slippage
            if pos['dir'] == 'L' and bar['low'] <= pos['stop']:
                # Stop is below entry; when hit, we exit at stop with slippage (pay more on exit for LONG)
                exit_price = pos['stop'] * (1 - slippage)
                ep = exit_price
                r = 'stop'
            elif pos['dir'] == 'S' and bar['high'] >= pos['stop']:
                exit_price = pos['stop'] * (1 + slippage)
                ep = exit_price
                r = 'stop'

            # Time stop
            if ep is None and pos.get('bars_held', 0) >= pos.get('hold', 40):
                ep = bar['close'] * (1 - slippage) if pos['dir'] == 'L' else bar['close'] * (1 + slippage)
                r = 'time'

            # Fade exit
            if ep is None and 'pattern' in pos:
                sk = f"{pos['pattern']}_{pos['dir']}"
                if rs in signals and sk in signals[rs]:
                    dfsig, _, _, _ = signals[rs][sk]
                    if ts in dfsig.index and float(dfsig.loc[ts, 'score']) < 0.10:
                        ep = bar['close'] * (1 - slippage) if pos['dir'] == 'L' else bar['close'] * (1 + slippage)
                        r = 'fade'

            if ep is not None:
                dm = 1 if pos['dir'] == 'L' else -1
                pp = dm * (ep - pos['entry']) / pos['entry']
                pr = pp * pos['go'] * pos['contracts']
                cash += pr
                all_trades.append({'sym': rs, 'dir': pos['dir'], 'pnl_rub': pr, 'reason': r})
                if pr > 0:
                    kelly_hist[rs]['w'] += 1
                else:
                    kelly_hist[rs]['l'] += 1
                kelly_hist[rs]['pnl'].append(pr)
                if len(kelly_hist[rs]['pnl']) > 50:
                    kelly_hist[rs]['pnl'].pop(0)
                to_close.append(sym)

        for s in to_close:
            del positions[s]

        # MTM
        mtm = 0
        for sym, pos in list(positions.items()):
            rs = pos.get('real_sym', sym)
            if rs in data and ts in data[rs].index:
                bar = data[rs].loc[ts]
                dm = 1 if pos['dir'] == 'L' else -1
                mtm += dm * (bar['close'] - pos['entry']) / pos['entry'] * pos['go'] * pos['contracts']

        teq = cash + mtm
        if teq > peak:
            peak = teq
        ddv = (peak - teq) / peak if peak > 0 else 0
        if ddv > max_dd:
            max_dd = ddv

        # Market hours check (7:00-23:00 IRKT)
        if ts.hour < 7 or ts.hour >= 23:
            continue

        locked = sum(p['go'] * p.get('contracts', 0) for p in positions.values())
        avail = cash - locked
        if avail <= 0:
            continue

        entries = []
        for lst_name, lst in PORTFOLIO.items():
            for sym, pat, di, hold, atm, w in lst:
                if sym in positions or sym not in data:
                    continue
                if sym not in signals:
                    continue
                sk = f"{pat}_{di}"
                if sk not in signals[sym]:
                    continue
                dfsig, _, _, _ = signals[sym][sk]
                if ts not in dfsig.index:
                    continue
                bs = dfsig.loc[ts]
                score = float(bs.get('score', 0))
                if np.isnan(score) or score < (0.25 if di == 'L' else 0.20):
                    continue
                go = TICKER_CONFIGS.get(sym, {}).get('go', 5000)
                kh = kelly_hist[sym]
                kelly = kelly_min
                if kh['w'] + kh['l'] >= 10:
                    wr_ = kh['w'] / max(kh['w'] + kh['l'], 1)
                    aw = max(sum(p for p in kh['pnl'] if p > 0) / max(kh['w'], 1), 1)
                    al = max(abs(sum(p for p in kh['pnl'] if p < 0) / max(kh['l'], 1)), 1)
                    rr = aw / al if al > 0 else 1.5
                    k = wr_ - (1 - wr_) / max(rr, 0.5)
                    kelly = max(kelly_min, min(k, kelly_max))
                pct = min(kelly * score * w, 0.35)
                mr = avail * pct
                ct = max(1, int(mr / go))
                if ct == 0:
                    continue
                atrv = float(bs.get('atr14', 0))
                if atrv == 0 or np.isnan(atrv):
                    continue
                ep = float(bs['close'])
                # Entry with slippage
                if di == 'L':
                    entry_price = ep * (1 + slippage)
                    stop = ep - atrv * atm  # stop for LONG is below entry
                else:
                    entry_price = ep * (1 - slippage)
                    stop = ep + atrv * atm  # stop for SHORT is above entry

                entries.append((sym, pat, di, hold, ct, entry_price, stop, go, score, lst_name))

        entries.sort(key=lambda e: e[8], reverse=True)
        for ent in entries[:5]:
            sym, pat, di, hold, ct, entry_price, stop, go, score, role = ent
            cost = ct * go
            if cost > avail:
                continue
            positions[sym] = {
                'real_sym': sym, 'dir': di, 'hold': hold,
                'entry': entry_price, 'stop': stop,
                'contracts': ct, 'go': go, 'bars_held': 0,
                'entry_ts': ts, 'pattern': pat
            }
            avail -= cost

    # Close remaining
    for sym, pos in list(positions.items()):
        rs = pos.get('real_sym', sym)
        if rs in data:
            lb = data[rs].iloc[-1]
            dm = 1 if pos['dir'] == 'L' else -1
            exit_price = lb['close'] * (1 - slippage) if pos['dir'] == 'L' else lb['close'] * (1 + slippage)
            pp = dm * (exit_price - pos['entry']) / pos['entry']
            pr = pp * pos['go'] * pos['contracts']
            cash += pr
            all_trades.append({'sym': rs, 'dir': pos['dir'], 'pnl_rub': pr, 'reason': 'eod'})

    tr = (cash - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100
    wins = sum(1 for t in all_trades if t.get('pnl_rub', 0) > 0)
    total_t = len(all_trades)
    wr_ = wins / total_t * 100 if total_t > 0 else 0

    if all_ts:
        days = (all_ts[-1] - all_ts[0]).days
        years = max(days / 365.25, 0.1)
    else:
        days = 0
        years = 0.1

    ann = (cash / INITIAL_CAPITAL) ** (1 / max(years, 0.1)) - 1
    cal = ann / max_dd if max_dd > 0 else 0

    # Trades per day
    trading_days = len(set(
        t.to_pydatetime().date() if hasattr(t, 'date') else t.date()
        for t in all_ts
    ))
    tpd = total_t / max(trading_days, 1)

    sym_stats = defaultdict(lambda: {'pnl': 0, 'w': 0, 'l': 0, 'n': 0})
    for t in all_trades:
        s = t.get('sym', '?')
        sym_stats[s]['pnl'] += t.get('pnl_rub', 0)
        sym_stats[s]['n'] += 1
        if t.get('pnl_rub', 0) > 0:
            sym_stats[s]['w'] += 1

    print(f"\n{'=' * 50}")
    print(f"{label}")
    print(f"{'=' * 50}")
    print(f"Capital: {INITIAL_CAPITAL:,.0f} → {cash:,.0f} ₽")
    print(f"Return:  {tr:+.1f}%  ({ann * 100:+.1f}%/год)")
    print(f"Max DD:  {max_dd * 100:.1f}%")
    print(f"Calmar:  {cal:.2f}")
    print(f"WR:      {wr_:.1f}% ({wins}/{total_t})")
    print(f"Trades/day: {tpd:.2f}")
    print(f"Trading days: {trading_days}")
    print(f"Период:  {days} дней")

    for s, st in sorted(sym_stats.items(), key=lambda x: x[1]['pnl'], reverse=True):
        ws = st['w'] / st['n'] * 100 if st['n'] > 0 else 0
        print(f"  {s}: {st['pnl']:+,.0f} ₽ WR={ws:.0f}% ({st['n']} тр)")

    return {
        'capital': cash,
        'return_pct': tr,
        'annual_return': ann * 100,
        'max_dd_pct': max_dd * 100,
        'calmar': cal,
        'wr': wr_,
        'n_trades': total_t,
        'trades_per_day': tpd,
        'trading_days': trading_days,
    }


if __name__ == '__main__':
    all_symbols = set()
    for lst in PORTFOLIO.values():
        for c in lst:
            all_symbols.add(c[0])
    all_symbols = sorted(all_symbols)
    print(f"=== H1 Slippage Sweep ===")
    print(f"Тикеры: {all_symbols}")
    print(f"Период: 2025-01-01 до {TEST_END}")

    # 1. Load pickle
    print("\nЗагрузка pickle...")
    pkl_path = os.path.join(os.path.dirname(__file__), '..', '.tf_sweep_data.pkl')
    with open(pkl_path, 'rb') as f:
        data_5m = pickle.load(f)
    print(f"Загружено {len(data_5m)} тикеров (5m)")

    # 2. Resample 5m → H1
    print("\nРесемплинг на H1...")
    data_h1 = {}
    for sym in all_symbols:
        if sym in data_5m:
            d = data_5m[sym]
            # Ensure sorted index
            d = d.sort_index()
            dh = resample_to_h1(d)
            # Drop NaN rows (e.g. overnight gaps)
            dh = dh.dropna(subset=['open', 'high', 'low', 'close'])
            data_h1[sym] = dh
            print(f"  ✓ {sym}: {len(d)} bars 5m → {len(dh)} bars H1")

    # 3. Precompute signals
    print("\nПредвычисление сигналов (H1)...")
    signals_h1 = precompute_signals(data_h1, all_symbols)
    print(f"Сигналы: {len(signals_h1)} тикеров")

    # 4. Run simulations
    test_start = datetime.strptime('2025-01-01', '%Y-%m-%d')
    test_end_dt = datetime.strptime(TEST_END, '%Y-%m-%d') + timedelta(days=1)

    slippage_levels = [
        (0.0, '0% (original)'),
        (0.0001, '0.01%'),
        (0.0002, '0.02%'),
        (0.0005, '0.05%'),
    ]

    results = {}
    for slip, label in slippage_levels:
        label_full = f"H1 TEST Kelly 40-150% slippage {label}"
        result = simulate_period(
            data_h1, signals_h1,
            test_start, test_end_dt,
            kelly_min=0.40, kelly_max=1.50,
            label=label_full,
            slippage=slip,
        )
        results[label] = result

    # 5. Save results
    out_dir = os.path.join(os.path.dirname(__file__), '..', 'reports', 'tf_sweep')
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, 'h1_slippage_results.json')
    with open(out_path, 'w') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\nСохранено: {out_path}")

    # 6. Comparison table
    print(f"\n{'=' * 90}")
    print(f"СРАВНЕНИЕ H1 СЛИППЕДЖ")
    print(f"{'=' * 90}")
    header = f"{'Параметр':25}"
    for label, _ in slippage_levels:
        header += f"{'':>5} {label:>18}"
    print(header)
    print("-" * 90)

    metrics = [
        ('return_pct', 'Доходность %'),
        ('annual_return', 'Доходность год %'),
        ('max_dd_pct', 'Max DD %'),
        ('calmar', 'Calmar'),
        ('wr', 'WR %'),
        ('n_trades', 'Сделок'),
        ('trades_per_day', 'Сд/день'),
        ('trading_days', 'Торг.дней'),
    ]

    for key, metric_name in metrics:
        row = f"{metric_name:25}"
        for label, _ in slippage_levels:
            val = results[label][key]
            if isinstance(val, float):
                row += f"{'':>5} {val:>18.2f}"
            else:
                row += f"{'':>5} {val:>18}"
        print(row)

    # Trade reduction
    print(f"\n{'=' * 90}")
    print(f"АНАЛИЗ: влияние slippage")
    print(f"{'=' * 90}")
    base_ret = results['0% (original)']['annual_return']
    for label, slip_pct in slippage_levels[1:]:
        r = results[label]
        ret_hit = r['annual_return']
        hit_pct = (ret_hit / base_ret * 100) if base_ret != 0 else 0
        cal = r['calmar']
        print(f"  {label:20}: return={ret_hit:+.1f}% ({hit_pct:.0f}% от base)  Calmar={cal:.2f}  trades/day={r['trades_per_day']:.1f}")

    slip_002 = results['0.02%']
    slip_000 = results['0% (original)']
    ret_retention = (slip_002['annual_return'] / slip_000['annual_return'] * 100) if slip_000['annual_return'] != 0 else 0
    print(f"\nВЫВОД:")
    print(f"  Trades/day на H1: {slip_000['trades_per_day']:.1f} (vs ~116 на 5m)")
    print(f"  Возврат при 0.02% slippage: {ret_retention:.1f}% от оригинального")
    print(f"  Calmar при 0.02%: {slip_002['calmar']:.2f}")
    if ret_retention > 50:
        print(f"  ✅ Стратегия ВЫДЕРЖИВАЕТ 0.02% slippage (реалистичный для MOEX)")
    else:
        print(f"  ❌ Стратегия НЕ ВЫДЕРЖИВАЕТ 0.02% slippage")
