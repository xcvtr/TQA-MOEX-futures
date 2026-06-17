#!/usr/bin/env python3
"""Phase 5 full portfolio run — EMA40 vol, EMA12 score, threshold 0.50/0.45, no fade, re-entry delay, 0.01% slippage."""

import sys, os, pickle, time, json
from datetime import datetime, timedelta
from collections import defaultdict

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from scripts.bar_level_sim import TICKER_CONFIGS

INITIAL_CAPITAL = 100_000
TEST_END = '2026-04-30'

PORTFOLIO = {
    'core': [
        ('GL','vod','L',24,2,1.0), ('RN','vou','L',48,5,1.0),
        ('AL','vou','L',48,2,1.0), ('HY','vou','L',48,5,1.0),
        ('NM','vod','L',24,3,1.0), ('AF','sm','L',48,2,1.0),
        ('SR','sm','L',24,5,1.0),  ('Si','vyf','L',48,2,1.0),
        ('SN','vou','L',48,5,1.0), ('YD','vod','L',24,5,1.0),
    ],
    'hedge': [
        ('BR','vyf','S',48,5,1.0), ('SV','vod','S',24,5,1.0),
        ('SF','vod','S',48,3,1.0), ('NG','vyf','S',48,5,1.0),
    ],
}

def rz(s, w=20):
    m=s.rolling(w,min_periods=w).mean(); std=s.rolling(w,min_periods=w).std()
    return (s-m)/std.clip(lower=1e-10)

def ema(s, w):
    return s.ewm(span=w, adjust=False, min_periods=w).mean().bfill().fillna(s)

def calc_atr(df,p=14):
    prev=df['close'].shift(1)
    tr=pd.concat([df['high']-df['low'],(df['high']-prev).abs(),(df['low']-prev).abs()],axis=1).max(axis=1)
    return tr.rolling(p,min_periods=p).mean().bfill().fillna(0)

def precompute_signals_5m(data_5m, symbols):
    """Precompute signals with EMA40 volume, EMA12 score smoothing."""
    signals = {}
    for sym in symbols:
        if sym not in data_5m: continue
        d = data_5m[sym].copy()
        d['volume'] = d['volume'].astype(float)
        # Change 1: EMA40 instead of SMA20 for volume
        d['vema40'] = ema(d['volume'], 40)
        d['vr'] = d['volume'] / d['vema40'].clip(lower=1)
        d['vz'] = rz(d['volume'], 20)
        has_oi = 'fiz_buy' in d.columns
        if has_oi:
            d['fiz_net'] = d['fiz_buy'].fillna(0) - d['fiz_sell'].fillna(0)
            d['yur_net'] = d['yur_buy'].fillna(0) - d['yur_sell'].fillna(0)
            d['fz'] = rz(d['fiz_net'], 20); d['yz'] = rz(d['yur_net'], 20)
            d['oi_r'] = (d['yur_buy']+d['yur_sell']).fillna(0) / (d['fiz_buy']+d['fiz_sell']+1).fillna(0)
            d['oima'] = d['oi_r'].rolling(20).mean()
        d['atr14'] = calc_atr(d); d['atr_pct'] = d['atr14']/d['close'].clip(lower=1)*100
        
        sym_sigs = {}
        seen = set()
        for lst in PORTFOLIO.values():
            for c in lst:
                sn, pat, di, hold, atm = c[0], c[1], c[2], c[3], c[4]
                if sn != sym: continue
                k = f"{pat}_{di}"
                if k in seen: continue
                seen.add(k)
                dm = 1 if di == 'L' else -1
                if pat in ('vod', 'vou'):
                    vs = np.clip((d['vr'] - 1.5) / 3.0, 0, 1)
                    if has_oi:
                        os_ = np.clip((d['oima'] - d['oi_r']) / d['oima'].clip(lower=0.1), 0, 1) if pat == 'vod' else np.clip((d['oi_r'] - d['oima']) / d['oima'].clip(lower=0.1), 0, 1)
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
                # Raw score (without EMA12 yet)
                score_raw = np.clip(raw * af * np.clip(1 + d['vz'] / 5, 0.5, 1.5), 0, 1)
                # Change 2: Apply EMA(12) to score
                score = ema(score_raw, 12)
                dout = d.copy(); dout['score'] = score
                sym_sigs[k] = (dout, di, hold, atm)
        signals[sym] = sym_sigs
    return signals


if __name__ == '__main__':
    all_symbols = sorted(set(c[0] for lst in PORTFOLIO.values() for c in lst))
    
    print("Loading data...", flush=True)
    with open('.tf_sweep_data.pkl', 'rb') as f:
        data_5m = pickle.load(f)
    
    print("Precomputing 5m signals (EMA40 vol, EMA12 score)...", flush=True)
    t0 = time.time()
    signals_all = precompute_signals_5m(data_5m, all_symbols)
    print(f"  Done in {time.time()-t0:.0f}s", flush=True)
    
    # Build all_ts for test period
    test_start = datetime.strptime('2025-01-01', '%Y-%m-%d')
    test_end_dt = datetime.strptime(TEST_END, '%Y-%m-%d') + timedelta(days=1)
    
    print("Building all_ts...", flush=True)
    all_ts = []
    for sym in data_5m:
        for t in data_5m[sym].index:
            t_naive = t.to_pydatetime().replace(tzinfo=None) if hasattr(t, 'tz') and t.tz else t
            if isinstance(t_naive, pd.Timestamp):
                t_naive = t_naive.to_pydatetime().replace(tzinfo=None)
            if test_start <= t_naive <= test_end_dt:
                all_ts.append(t)
    all_ts = sorted(set(all_ts))
    print(f"  {len(all_ts)} bars", flush=True)
    
    slip = 0.0001  # 0.01%
    slip_name = f"{slip*100:.2f}%"
    print(f"\n--- Slippage: {slip_name} ---", flush=True)
    t1 = time.time()
    
    cash = float(INITIAL_CAPITAL)
    peak = float(INITIAL_CAPITAL)
    max_dd = 0.0
    kelly_hist = defaultdict(lambda: {'w': 0, 'l': 0, 'pnl': []})
    positions = {}
    all_trades = []
    total_slippage_cost = 0.0
    
    # Change 6: Re-entry delay dictionary
    reentry_block = {}  # sym -> bar index when unblocked
    
    for idx, ts in enumerate(all_ts):
        if idx % 10000 == 0:
            print(f"  bar {idx}/{len(all_ts)} ({time.time()-t1:.0f}s)", flush=True)
        
        # --- Exits ---
        to_close = []
        for sym, pos in list(positions.items()):
            rs = pos.get('real_sym', sym)
            if rs not in data_5m or ts not in data_5m[rs].index: continue
            bar = data_5m[rs].loc[ts]
            ep = None; reason = ''
            
            # Stop loss
            if pos['dir'] == 'L' and bar['low'] <= pos['stop']:
                ep = pos['stop'] * (1 - slip)
                reason = 'stop'
            elif pos['dir'] == 'S' and bar['high'] >= pos['stop']:
                ep = pos['stop'] * (1 + slip)
                reason = 'stop'
            
            # Time stop (hold_bars)
            if ep is None and pos.get('bars_held', 0) >= pos.get('hold', 40):
                ep = bar['close']
                if slip > 0:
                    ep = ep * (1 - slip) if pos['dir'] == 'L' else ep * (1 + slip)
                reason = 'time'
            
            # Change 5: REMOVED fade exit (score < 0.10 block)
            
            if ep is not None:
                dm = 1 if pos['dir'] == 'L' else -1
                pp = dm * (ep - pos['entry']) / pos['entry']
                pr = pp * pos['go'] * pos['contracts']
                cash += pr
                all_trades.append({'sym': rs, 'dir': pos['dir'], 'pnl_rub': pr, 'reason': reason})
                if pr > 0:
                    kelly_hist[rs]['w'] += 1
                else:
                    kelly_hist[rs]['l'] += 1
                kelly_hist[rs]['pnl'].append(pr)
                if len(kelly_hist[rs]['pnl']) > 50:
                    kelly_hist[rs]['pnl'].pop(0)
                to_close.append(sym)
                
                # Change 6: Set re-entry block for 24 bars
                reentry_block[rs] = idx + 24  # blocked until index idx+24
        
        for s in to_close:
            del positions[s]
        
        # --- MTM ---
        mtm = 0
        for sym, pos in positions.items():
            rs = pos.get('real_sym', sym)
            if rs in data_5m and ts in data_5m[rs].index:
                bar = data_5m[rs].loc[ts]
                dm = 1 if pos['dir'] == 'L' else -1
                mtm += dm * (bar['close'] - pos['entry']) / pos['entry'] * pos['go'] * pos['contracts']
        teq = cash + mtm
        if teq > peak:
            peak = teq
        ddv = (peak - teq) / peak if peak > 0 else 0
        if ddv > max_dd:
            max_dd = ddv
        
        # Increment bars_held
        for sym, pos in positions.items():
            pos['bars_held'] = pos.get('bars_held', 0) + 1
        
        # Only entries during market hours
        if ts.hour < 7 or ts.hour >= 23:
            continue
        
        locked = sum(p['go'] * p.get('contracts', 0) for p in positions.values())
        avail = cash - locked
        if avail <= 0:
            continue
        
        # --- Entries ---
        entries = []
        for lst_name, lst in PORTFOLIO.items():
            for sym, pat, di, hold, atm, w in lst:
                if sym in positions or sym not in data_5m: continue
                if sym not in signals_all: continue
                sk = f"{pat}_{di}"
                if sk not in signals_all[sym]: continue
                dfsig, _, _, _ = signals_all[sym][sk]
                if ts not in dfsig.index: continue
                bs = dfsig.loc[ts]
                score = float(bs.get('score', 0))
                if np.isnan(score):
                    continue
                
                # Change 3: Higher thresholds 0.50 / 0.45
                if di == 'L' and score < 0.50:
                    continue
                if di == 'S' and score < 0.45:
                    continue
                
                # Change 6: Check re-entry block
                if sym in reentry_block and idx < reentry_block[sym]:
                    continue
                
                go = TICKER_CONFIGS.get(sym, {}).get('go', 5000)
                kh = kelly_hist[sym]
                kelly = 0.40  # Change 8: Kelly 40-150%
                if kh['w'] + kh['l'] >= 10:
                    wr_ = kh['w'] / max(kh['w'] + kh['l'], 1)
                    aw = max(sum(p for p in kh['pnl'] if p > 0) / max(kh['w'], 1), 1)
                    al = max(abs(sum(p for p in kh['pnl'] if p < 0) / max(kh['l'], 1)), 1)
                    rr = aw / al if al > 0 else 1.5
                    k = wr_ - (1 - wr_) / max(rr, 0.5)
                    kelly = max(0.40, min(k, 1.50))
                
                pct = min(kelly * score * w, 0.35)
                mr = avail * pct
                ct = max(1, int(mr / go))
                if ct == 0:
                    continue
                atrv = float(bs.get('atr14', 0))
                if atrv == 0 or np.isnan(atrv):
                    continue
                ep_c = float(bs['close'])
                # Change 7: 0.01% slippage on entry
                if slip > 0:
                    ep_c = ep_c * (1 + slip) if di == 'L' else ep_c * (1 - slip)
                total_slippage_cost += ct * go * slip
                stop = ep_c - atrv * atm if di == 'L' else ep_c + atrv * atm
                entries.append((sym, pat, di, hold, ct, ep_c, stop, go, score, lst_name))
        
        entries.sort(key=lambda e: e[8], reverse=True)
        for ent in entries[:5]:
            sym, pat, di, hold, ct, ep_c, stop, go, score, role = ent
            cost = ct * go
            if cost > avail:
                continue
            positions[sym] = {
                'real_sym': sym, 'dir': di, 'hold': hold, 'entry': ep_c, 'stop': stop,
                'contracts': ct, 'go': go, 'bars_held': 0, 'entry_ts': ts, 'pattern': pat
            }
            avail -= cost
    
    # Close remaining positions
    for sym, pos in list(positions.items()):
        rs = pos.get('real_sym', sym)
        if rs in data_5m:
            lb = data_5m[rs].iloc[-1]
            dm = 1 if pos['dir'] == 'L' else -1
            ep_close = lb['close']
            if slip > 0:
                ep_close = ep_close * (1 - slip) if pos['dir'] == 'L' else ep_close * (1 + slip)
            pp = dm * (ep_close - pos['entry']) / pos['entry']
            pr = pp * pos['go'] * pos['contracts']
            cash += pr
            all_trades.append({'sym': rs, 'dir': pos['dir'], 'pnl_rub': pr, 'reason': 'eod'})
    
    tr = (cash - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100
    wins = sum(1 for t in all_trades if t.get('pnl_rub', 0) > 0)
    total_t = len(all_trades)
    days = max((all_ts[-1] - all_ts[0]).days, 1)
    years = max(days / 365.25, 0.1)
    ann = (cash / INITIAL_CAPITAL) ** (1 / years) - 1
    cal = ann / max_dd if max_dd > 0 else 0
    sigs_per_day = total_t / days
    
    print(f"\n{'='*60}", flush=True)
    print(f"RESULTS — EMA40, EMA12 score, thresh 0.50/0.45, hold 24-48, no fade, re-entry 24h", flush=True)
    print(f"{'='*60}", flush=True)
    print(f"  Capital: {INITIAL_CAPITAL:,.0f} → {cash:,.0f} ₽", flush=True)
    print(f"  Return:  {tr:+.1f}%  ({ann*100:+.1f}%/год)", flush=True)
    print(f"  Max DD:  {max_dd*100:.1f}%", flush=True)
    print(f"  Calmar:  {cal:.2f}", flush=True)
    print(f"  Trades:  {total_t}  ({sigs_per_day:.1f}/day)", flush=True)
    if total_t:
        print(f"  WR:      {wins/total_t*100:.1f}% ({wins}/{total_t})", flush=True)
    print(f"  Slip cost: {total_slippage_cost:+,.0f}", flush=True)
    print(f"  Days:    {days} ({years*365.25:.0f} days used)", flush=True)
    
    # Per-symbol breakdown
    print(f"\n  {'Symbol':8} {'PnL':>10} {'Trades':>8} {'Wins':>6} {'WR':>6} {'AvgPnL':>10}", flush=True)
    print(f"  {'─'*48}", flush=True)
    sym_stats = defaultdict(lambda: {'pnl': 0, 'w': 0, 'n': 0})
    for t in all_trades:
        s = t.get('sym', '?')
        sym_stats[s]['pnl'] += t.get('pnl_rub', 0)
        sym_stats[s]['n'] += 1
        if t.get('pnl_rub', 0) > 0:
            sym_stats[s]['w'] += 1
    for s, st in sorted(sym_stats.items(), key=lambda x: x[1]['pnl'], reverse=True):
        ws = st['w'] / st['n'] * 100 if st['n'] > 0 else 0
        avg = st['pnl'] / st['n'] if st['n'] > 0 else 0
        print(f"  {s:8} {st['pnl']:>+10,.0f} {st['n']:>8} {st['w']:>6} {ws:>5.0f}% {avg:>+10,.0f}", flush=True)
    
    # Save results
    result = {
        'config': {
            'volume_ema': 40,
            'score_ema': 12,
            'entry_threshold_long': 0.50,
            'entry_threshold_short': 0.45,
            'slippage': slip,
            'kelly_min': 0.40,
            'kelly_max': 1.50,
            'fade_exit': False,
            'reentry_delay_bars': 24,
            'test_start': '2025-01-01',
            'test_end': TEST_END,
            'market_hours': '7:00-23:00',
        },
        'results': {
            'capital': cash,
            'return_pct': tr,
            'annual_return': ann * 100,
            'max_dd_pct': max_dd * 100,
            'calmar': cal,
            'wr': wins / total_t * 100 if total_t else 0,
            'n_trades': total_t,
            'trades_per_day': round(sigs_per_day, 1),
            'total_slippage_cost': round(total_slippage_cost, 0),
            'days': days,
            'time_s': round(time.time() - t1, 1),
        },
        'per_symbol': {
            s: {'pnl_rub': st['pnl'], 'trades': st['n'], 'wins': st['w'],
                'wr_pct': round(st['w'] / st['n'] * 100, 1) if st['n'] else 0,
                'avg_pnl': round(st['pnl'] / st['n'], 0) if st['n'] else 0}
            for s, st in sorted(sym_stats.items(), key=lambda x: x[1]['pnl'], reverse=True)
        },
        'exit_reasons': {},
    }
    
    # Exit reason breakdown
    reason_counts = defaultdict(int)
    for t in all_trades:
        reason_counts[t.get('reason', '?')] += 1
    result['exit_reasons'] = dict(reason_counts)
    
    os.makedirs('reports/tf_sweep', exist_ok=True)
    with open('reports/tf_sweep/ema_portfolio.json', 'w') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"\nSaved: reports/tf_sweep/ema_portfolio.json", flush=True)
