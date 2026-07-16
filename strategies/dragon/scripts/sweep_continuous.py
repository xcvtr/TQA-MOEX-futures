#!/usr/bin/env python3 -u
"""Sweep dragon на MT5 continuous M1 данных — moex.mt5_continuous."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import clickhouse_connect as cc
from dragon.prod.engine import check_signal
from dragon.scripts.backtest_m1 import build_m5_bars, SPECS

CH = dict(host='10.0.0.60', port=8123, database='moex')
TC = 4

CONT_TICKERS = ['BR', 'Si', 'CR', 'GD', 'GZ', 'MM', 'NG', 'RN', 'SV']


def load_cont(ticker, days=365):
    cutoff = '2025-07-16' if days >= 365 else '2026-01-16'
    ch = cc.get_client(**CH)
    rows = ch.query(f"""
        SELECT bt, opn, hi, lo, prc
        FROM moex.mt5_continuous
        WHERE ticker = '{ticker}'
          AND bt >= '{cutoff}'
        ORDER BY bt
    """).result_rows
    ch.close()
    bars = []
    for r in rows:
        ts = r[0]
        if ts.weekday() >= 5: continue
        h, m = ts.hour, ts.minute
        if h < 15 or h > 23 or (h == 23 and m > 45): continue
        bars.append({'ts': ts, 'opn': float(r[1]), 'hi': float(r[2]), 'lo': float(r[3]), 'prc': float(r[4])})
    return bars


def bt(ticker, bars, contracts=2, impulse=0.3, retrace_max=70, hump=0.1, lookback=100,
       trail_act=0.008, trail_trail=0.01, sl_pct=0.01, to_m1=60):
    if ticker not in SPECS:
        return []
    s = SPECS[ticker]
    ms, sp = s['ms'], s['sp']
    dp = {'impulse_pct': impulse, 'retrace_max_pct': retrace_max,
          'hump_extension': hump, 'lookback': lookback}
    trades, open_pos = [], None
    m5_cache = []  # cached M5 bars
    for i in range(30, len(bars)):
        # Update M5 cache on every 5th bar
        if i % 5 == 0:
            gs = i - 5
            group = bars[gs:gs+5]
            if len(group) >= 3:
                m5_cache.append({
                    'opn': group[0]['opn'],
                    'hi': max(b['hi'] for b in group),
                    'lo': min(b['lo'] for b in group),
                    'prc': group[-1]['prc'],
                })
        bar = bars[i]
        if open_pos:
            ep = open_pos['ep']
            exit_p = None
            sl = ep * (1 - sl_pct) if open_pos['dir'] == 'long' else ep * (1 + sl_pct)
            if (open_pos['dir'] == 'long' and bar['lo'] <= sl) or \
               (open_pos['dir'] == 'short' and bar['hi'] >= sl):
                exit_p = sl
            if not exit_p and i % 5 == 4:
                if not open_pos.get('tr'):
                    if (open_pos['dir'] == 'long' and bar['hi'] >= ep * (1 + trail_act)) or \
                       (open_pos['dir'] == 'short' and bar['lo'] <= ep * (1 - trail_act)):
                        open_pos['tr'] = True
                        open_pos['tl'] = bar['hi'] * (1 - trail_trail) if open_pos['dir'] == 'long' else bar['lo'] * (1 + trail_trail)
                if open_pos.get('tr'):
                    if (open_pos['dir'] == 'long' and bar['lo'] <= open_pos['tl']) or \
                       (open_pos['dir'] == 'short' and bar['hi'] >= open_pos['tl']):
                        exit_p = open_pos['tl']
            if not exit_p and i - open_pos['bi'] >= to_m1:
                exit_p = bar['prc']
            if exit_p is not None:
                raw = (exit_p - ep) / ms * sp - TC
                pnl = (raw if open_pos['dir'] == 'long' else -raw) * contracts
                trades.append(pnl)
                open_pos = None
        if i % 5 == 0 and not open_pos:
            if len(m5_cache) < 6: continue
            m5_slice = m5_cache[-(lookback+10):]
            sig = check_signal({'prc': m5_slice[-1]['prc'], 'bars_list': m5_slice}, ticker, dp)
            if sig:
                open_pos = {'bi': i, 'ep': sig['entry_price'], 'dir': sig['direction'],
                            'tr': False, 'tl': None}
    return trades


if __name__ == '__main__':
    print(f'\n🐉 Dragon MT5 Continuous Sweep — 1 год, ×2 контракта')
    print(f'  Параметры: SL=1.0% trail_act=0.8% trail_trail=1.0%')
    print(f'  Данные: moex.mt5_continuous (Indicative Continuous MT5 FINAM)')
    print(f'{">"*70}')
    
    all_trades = []
    for t in CONT_TICKERS:
        bars = load_cont(t, 365)
        trades = bt(t, bars, 2)
        n = len(trades)
        if n == 0:
            print(f'  {t:4s} ({len(bars):>6d} bars): 0 сделок', flush=True)
            continue
        wins = [p for p in trades if p > 0]
        losses = [p for p in trades if p <= 0]
        total = sum(trades)
        wr = len(wins)/n*100
        tp = sum(wins)
        tn = sum(abs(p) for p in losses)
        pf = tp/tn if tn else float('inf')
        aw = tp/len(wins) if wins else 0
        al = tn/len(losses) if losses else 0
        cap = 200000
        peak = cap
        mdd = 0
        for p in trades:
            cap += p
            peak = max(peak, cap)
            mdd = max(mdd, (peak - cap)/peak*100)
        ret = (cap - 200000)/200000*100
        calmar = ret/mdd if mdd > 0 else 0
        print(f'  {t:4s} ({len(bars):>6d} bars): n={n:4d} wr={wr:5.1f}% pnl={total:+8.0f} pf={pf:.2f} mdd={mdd:.2f}% calmar={calmar:.1f} aw={aw:+6.0f} al={al:+6.0f}', flush=True)
        all_trades.extend(trades)
    
    if all_trades:
        print(f'\n{"="*70}')
        wins = [p for p in all_trades if p > 0]
        losses = [p for p in all_trades if p <= 0]
        total = sum(all_trades)
        n = len(all_trades)
        wr = len(wins)/n*100
        tp = sum(wins)
        tn = sum(abs(p) for p in losses)
        pf = tp/tn if tn else float('inf')
        aw = tp/len(wins) if wins else 0
        al = tn/len(losses) if losses else 0
        cap = 200000
        peak = cap
        mdd = 0
        for p in all_trades:
            cap += p
            peak = max(peak, cap)
            mdd = max(mdd, (peak - cap)/peak*100)
        ret = (cap - 200000)/200000*100
        print(f'ПОРТФЕЛЬ ({len(CONT_TICKERS)} тикеров, {n} сделок)')
        print(f'  Капитал: 200,000 -> {cap:,.0f}₽ ({ret:+.2f}%)')
        print(f'  WR: {wr:.1f}% | PF: {pf:.2f} | MDD: {mdd:.2f}% | Calmar: {ret/mdd if mdd>0 else 0:.1f}')
        print(f'  AvgWin: {aw:+.0f}₽ | AvgLoss: {al:+.0f}₽')
