#!/usr/bin/env python3
"""
Slippage impact analysis for Phase 5 on 5m data.
Runs simulate_period with 4 slippage levels, keeping all other params identical.
"""
import json, os, pickle, sys
from datetime import datetime, timedelta
from collections import defaultdict

import numpy as np
import pandas as pd

# ── Project root ──
PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT, 'scripts'))

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

TEST_START_STR = '2025-01-01'
TEST_END_STR = '2026-04-30'


def rz(s, w=20):
    m = s.rolling(w, min_periods=w).mean()
    std = s.rolling(w, min_periods=w).std()
    return (s - m) / std.clip(lower=1e-10)


def calc_atr(df, p=14):
    prev = df['close'].shift(1)
    tr = pd.concat([
        df['high'] - df['low'],
        (df['high'] - prev).abs(),
        (df['low'] - prev).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(p, min_periods=p).mean().bfill().fillna(0)


def precompute_signals(data, symbols):
    """Identical to phase5_walkforward.py version."""
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
    Same as phase5_walkforward.py but with slippage on execution.
    slippage: fraction (e.g. 0.0001 = 0.01%)
    
    Slippage rules:
    - Entry LONG:  close * (1 + slip)
    - Entry SHORT: close * (1 - slip)
    - Exit/stop LONG:  close * (1 - slip)
    - Exit/stop SHORT: close * (1 + slip)
    - Signals & indicators use original close (no slippage)
    """
    cash = INITIAL_CAPITAL
    peak = INITIAL_CAPITAL
    max_dd = 0
    kelly_hist = defaultdict(lambda: {'w': 0, 'l': 0, 'pnl': []})
    positions = {}
    all_trades = []
    total_slippage_cost = 0.0

    # Collect all timestamps
    all_ts = []
    for sym in data:
        for t in data[sym].index:
            t_naive = t.to_pydatetime().replace(tzinfo=None) if hasattr(t, 'tz') and t.tz else t
            if isinstance(t_naive, pd.Timestamp):
                t_naive = t_naive.to_pydatetime().replace(tzinfo=None)
            if start <= t_naive <= end:
                all_ts.append(t)
    all_ts = sorted(set(all_ts))

    for idx, ts in enumerate(all_ts):
        # ── Exits ──
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
                ep = pos['stop'] * (1 - slippage)  # exit LONG: apply slip
                r = 'stop'
            elif pos['dir'] == 'S' and bar['high'] >= pos['stop']:
                ep = pos['stop'] * (1 + slippage)  # exit SHORT: apply slip
                r = 'stop'
            
            # Time exit
            if ep is None and pos.get('bars_held', 0) >= pos.get('hold', 40):
                ep = bar['close']
                if pos['dir'] == 'L':
                    ep = ep * (1 - slippage)
                else:
                    ep = ep * (1 + slippage)
                r = 'time'
            
            # Fade exit
            if ep is None and 'pattern' in pos:
                sk = f"{pos['pattern']}_{pos['dir']}"
                if rs in signals and sk in signals[rs]:
                    dfsig, _, _, _ = signals[rs][sk]
                    if ts in dfsig.index and float(dfsig.loc[ts, 'score']) < 0.10:
                        ep = bar['close']
                        if pos['dir'] == 'L':
                            ep = ep * (1 - slippage)
                        else:
                            ep = ep * (1 + slippage)
                        r = 'fade'
            
            if ep is not None:
                dm = 1 if pos['dir'] == 'L' else -1
                pp = dm * (ep - pos['entry']) / pos['entry']
                pr = pp * pos['go'] * pos['contracts']
                cash += pr
                # slippage cost = difference between ideal close and actual exit (absolute)
                ideal_ep = bar['close']
                slip_amount = abs(ep - ideal_ep) / ideal_ep * pos['go'] * pos['contracts']
                total_slippage_cost += slip_amount
                
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

        # ── MTM ──
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

        # ── Entries (only in trading hours) ──
        # Convert to naive for hour check
        if hasattr(ts, 'hour'):
            h = ts.hour
        elif hasattr(ts, 'to_pydatetime'):
            h = ts.to_pydatetime().hour
        else:
            h = 0
        if h < 7 or h >= 23:
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
                
                # Apply slippage to entry price
                if di == 'L':
                    entry_price = ep * (1 + slippage)
                    stop = ep - atrv * atm
                else:
                    entry_price = ep * (1 - slippage)
                    stop = ep + atrv * atm
                
                entries.append((sym, pat, di, hold, ct, entry_price, stop, go, score, lst_name))
        
        entries.sort(key=lambda e: e[8], reverse=True)
        for ent in entries[:5]:
            sym, pat, di, hold, ct, ep, stop, go, score, role = ent
            cost = ct * go
            if cost > avail:
                continue
            # Record slippage cost for entry
            ideal_ep = float(data[sym].loc[ts, 'close'])
            slip_entry = abs(ep - ideal_ep) / ideal_ep * go * ct
            total_slippage_cost += slip_entry
            
            positions[sym] = {
                'real_sym': sym, 'dir': di, 'hold': hold, 'entry': ep,
                'stop': stop, 'contracts': ct, 'go': go, 'bars_held': 0,
                'entry_ts': ts, 'pattern': pat,
            }
            avail -= cost

    # ── Close remaining (EOD) ──
    for sym, pos in list(positions.items()):
        rs = pos.get('real_sym', sym)
        if rs in data:
            lb = data[rs].iloc[-1]
            dm = 1 if pos['dir'] == 'L' else -1
            exit_price = lb['close']
            if pos['dir'] == 'L':
                exit_price = exit_price * (1 - slippage)
            else:
                exit_price = exit_price * (1 + slippage)
            pp = dm * (exit_price - pos['entry']) / pos['entry']
            pr = pp * pos['go'] * pos['contracts']
            cash += pr
            # Slippage cost for final exit
            ideal_ep = lb['close']
            slip_amount = abs(exit_price - ideal_ep) / ideal_ep * pos['go'] * pos['contracts']
            total_slippage_cost += slip_amount
            all_trades.append({'sym': rs, 'dir': pos['dir'], 'pnl_rub': pr, 'reason': 'eod'})

    # ── Stats ──
    tr = (cash - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100
    wins = sum(1 for t in all_trades if t.get('pnl_rub', 0) > 0)
    total_t = len(all_trades)
    wr = wins / total_t * 100 if total_t > 0 else 0

    if all_ts:
        t0 = all_ts[0]
        t1 = all_ts[-1]
        if hasattr(t0, 'to_pydatetime'):
            t0_dt = t0.to_pydatetime().replace(tzinfo=None)
            t1_dt = t1.to_pydatetime().replace(tzinfo=None)
        else:
            t0_dt = t0 if not hasattr(t0, 'tz') else t0.replace(tzinfo=None)
            t1_dt = t1 if not hasattr(t1, 'tz') else t1.replace(tzinfo=None)
        days = (t1_dt - t0_dt).days
    else:
        days = 0
    years = max(days / 365.25, 0.1)
    ann = (cash / INITIAL_CAPITAL) ** (1 / max(years, 0.1)) - 1
    cal = ann / max_dd if max_dd > 0 else 0

    sym_stats = defaultdict(lambda: {'pnl': 0, 'w': 0, 'l': 0, 'n': 0})
    for t in all_trades:
        s = t.get('sym', '?')
        sym_stats[s]['pnl'] += t.get('pnl_rub', 0)
        sym_stats[s]['n'] += 1
        if t.get('pnl_rub', 0) > 0:
            sym_stats[s]['w'] += 1

    print(f"\n{'='*55}")
    print(f"{label}")
    print(f"Slippage: {slippage*100:.3f}% per trade side")
    print(f"{'='*55}")
    print(f"Capital: {INITIAL_CAPITAL:,.0f} → {cash:,.0f} ₽")
    print(f"Return:  {tr:+.2f}%  ({ann*100:+.2f}%/год)")
    print(f"Max DD:  {max_dd*100:.2f}%")
    print(f"Calmar:  {cal:.2f}")
    print(f"WR:      {wr:.1f}% ({wins}/{total_t})")
    print(f"Total slippage cost: {total_slippage_cost:+,.0f} ₽")
    print(f"Период: {days} дней")

    for s, st in sorted(sym_stats.items(), key=lambda x: x[1]['pnl'], reverse=True):
        ws = st['w'] / st['n'] * 100 if st['n'] > 0 else 0
        print(f"  {s}: {st['pnl']:+,.0f} ₽ WR={ws:.0f}% ({st['n']} тр)")

    return {
        'capital': cash,
        'return_pct': tr,
        'annual_return': ann * 100,
        'max_dd_pct': max_dd * 100,
        'calmar': cal,
        'wr': wr,
        'n_trades': total_t,
        'total_slippage_cost': total_slippage_cost,
    }


if __name__ == '__main__':
    all_symbols = set()
    for lst in PORTFOLIO.values():
        all_symbols.update(c[0] for c in lst)
    print(f"=== Phase 5 Slippage Impact Analysis (5m) ===")
    print(f"Тикеры: {sorted(all_symbols)}")
    print(f"Период: {TEST_START_STR} → {TEST_END_STR}")
    print(f"Kelly: 40-150%")

    # Load pickle data
    pkl_path = os.path.join(PROJECT, '.tf_sweep_data.pkl')
    print(f"\nЗагрузка pickle: {pkl_path}")
    with open(pkl_path, 'rb') as f:
        data_all = pickle.load(f)
    print(f"Загружено {len(data_all)} тикеров")

    # Precompute signals (once)
    print("\nПредвычисление сигналов...")
    signals_all = precompute_signals(data_all, list(all_symbols))
    print(f"Сигналы: {len(signals_all)} тикеров")

    test_start = datetime.strptime(TEST_START_STR, '%Y-%m-%d')
    test_end = datetime.strptime(TEST_END_STR, '%Y-%m-%d') + timedelta(days=1)

    # Slippage variants
    slippage_levels = [0.0, 0.0001, 0.0002, 0.0005]
    labels = ['0% (original)', '0.01%', '0.02%', '0.05%']

    results = {}
    for slip, lab in zip(slippage_levels, labels):
        print(f"\n{'#'*60}")
        print(f"Запуск: slippage {lab}")
        print(f"{'#'*60}")
        res = simulate_period(
            data_all, signals_all, test_start, test_end,
            kelly_min=0.40, kelly_max=1.50,
            label=f"TEST 2025-2026 (OOS, Kelly 40-150%, slippage {lab})",
            slippage=slip,
        )
        key = lab.replace('%', 'pct').replace(' ', '_')
        results[key] = res

    # Save
    out_dir = os.path.join(PROJECT, 'reports', 'tf_sweep')
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, '5m_slippage_results.json')
    with open(out_path, 'w') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\nСохранено: {out_path}")

    # Comparison table
    print(f"\n{'='*70}")
    print(f"СРАВНЕНИЕ ВЛИЯНИЯ SLIPPAGE НА 5m")
    print(f"{'='*70}")
    header = f"{'Параметр':25}"
    for lab in labels:
        header += f" {lab:>14}"
    print(header)
    print('-' * (25 + 15 * len(labels)))

    metrics = [
        ('return_pct', 'Return, %', '{:>13.2f}%'),
        ('annual_return', 'Годовая, %', '{:>13.2f}%'),
        ('max_dd_pct', 'Max DD, %', '{:>13.2f}%'),
        ('calmar', 'Calmar', '{:>13.2f}'),
        ('wr', 'WR, %', '{:>13.1f}%'),
        ('n_trades', 'Сделок', '{:>13}'),
        ('total_slippage_cost', 'Slip cost, ₽', '{:>13,.0f}'),
    ]

    for key, display, fmt in metrics:
        row = f"{display:25}"
        for lab in labels:
            k = lab.replace('%', 'pct').replace(' ', '_')
            val = results[k].get(key, 0)
            row += fmt.format(val)
        print(row)

    print(f"\nВывод: Signals и индикаторы считаются на оригинальных close (без slippage).")
    print(f"Slippage применяется ТОЛЬКО на entry/exit исполнение.")
