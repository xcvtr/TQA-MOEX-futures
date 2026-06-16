#!/usr/bin/env python3
"""
Phase 5.A: Агрессивный портфель — DD target 15-20%, агрессивный Kelly.
Основан на phase5_triz_final.py с модификациями:
- Kelly 10-40% (вместо 3-20%)
- Score порог 0.25/0.2 (вместо 0.4/0.3)
- Max позиция 35% (вместо 15%)
- Больше тикеров (10 лучших core + 5 hedge)
- Без Collar
- Без Circuit Breaker
"""
import json, sys, os
from datetime import datetime, timedelta
from collections import defaultdict

import numpy as np
import pandas as pd
import clickhouse_connect

from bar_level_sim import TICKER_CONFIGS

INITIAL_CAPITAL = 100_000
KELLY_WINDOW = 50

# АГРЕССИВНЫЕ настройки
KELLY_MIN = 0.40
KELLY_MAX = 1.50
SCORE_THRESHOLD_LONG = 0.25
SCORE_THRESHOLD_SHORT = 0.20
MAX_POSITION_PCT = 0.35
MAX_ENTRIES_PER_BAR = 5

# Расширенный портфель (из phase2_fullscan, топ по Calmar OOS)
PORTFOLIO = {
    'core': [  # LONG
        ('GL', 'vod', 'L', 21, 2, 1.0),
        ('RN', 'vou', 'L', 5, 5, 1.0),
        ('AL', 'vou', 'L', 21, 2, 1.0),
        ('HY', 'vou', 'L', 5,  5, 1.0),
        ('NM', 'vod', 'L', 21, 3, 1.0),
        ('AF', 'sm',  'L', 21, 2, 1.0),
        ('SR', 'sm',  'L', 8,  5, 1.0),
        ('Si', 'vyf', 'L', 13, 2, 1.0),
        ('SN', 'vou', 'L', 5,  5, 1.0),
        ('YD', 'vod', 'L', 13, 5, 1.0),
    ],
    'hedge': [  # SHORT
        ('BR', 'vyf', 'S', 13, 5, 1.0),
        ('SV', 'vod', 'S', 5,  5, 1.0),
        ('SF', 'vod', 'S', 8,  3, 1.0),
        ('NG', 'vou', 'S', 5,  5, 1.0),   # vou_S = SHORT on NG
    ],
}


def rz(s, w=20):
    m = s.rolling(w, min_periods=w).mean()
    std = s.rolling(w, min_periods=w).std()
    return (s - m) / std.clip(lower=1e-10)


def calc_atr(df, p=14):
    prev = df['close'].shift(1)
    tr = pd.concat([
        df['high']-df['low'], (df['high']-prev).abs(), (df['low']-prev).abs()
    ], axis=1).max(axis=1)
    return tr.rolling(p, min_periods=p).mean().bfill().fillna(0)


def run_scenario_a(data, go_map):
    print("Предвычисление сигналов...")
    signals = {}
    for sym, df in data.items():
        sym_signals = {}
        seen = set()
        d = df.copy()
        d['volume'] = d['volume'].astype(float)
        d['vma20'] = d['volume'].rolling(20).mean().fillna(d['volume'])
        d['vr'] = d['volume'] / d['vma20'].clip(lower=1)
        d['vz'] = rz(d['volume'], 20)
        
        has_oi = all(c in d.columns for c in ['fiz_buy','fiz_sell','yur_buy','yur_sell'])
        if has_oi:
            d['fiz_net'] = d['fiz_buy'].fillna(0) - d['fiz_sell'].fillna(0)
            d['yur_net'] = d['yur_buy'].fillna(0) - d['yur_sell'].fillna(0)
            d['fz'] = rz(d['fiz_net'], 20)
            d['yz'] = rz(d['yur_net'], 20)
            d['oi_r'] = (d['yur_buy']+d['yur_sell']).fillna(0) / (d['fiz_buy']+d['fiz_sell']+1).fillna(0)
            d['oima'] = d['oi_r'].rolling(20).mean()
        
        d['atr14'] = calc_atr(d)
        d['atr_pct'] = d['atr14'] / d['close'].clip(lower=1) * 100
        
        for lst in PORTFOLIO.values():
            for c in lst:
                sym_name, pat, dir_, hold, atr_m = c[0], c[1], c[2], c[3], c[4]
                if sym_name != sym: continue
                key = f"{pat}_{dir_}"
                if key in seen: continue
                seen.add(key)
                
                dm = 1 if dir_ == 'L' else -1
                
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
                        ys = np.clip(abs(d['yz']) / 3.0, 0, 1)
                        fs = np.clip(abs(d['fz']) / 3.0, 0, 1)
                        raw = ys * 0.7 + fs * 0.3
                    else:
                        raw = np.clip((d['vr'] - 1.5) / 3.0, 0, 1)
                elif pat == 'vyf':
                    vs = np.clip((d['vr'] - 2.0) / 4.0, 0, 1)
                    if has_oi:
                        yn = d['yur_net'].fillna(0)
                        ys = np.clip(yn / max(yn.std(), 1) * dm, 0, 1)
                    else:
                        ys = np.clip((d['close'] - d['close'].shift(1)) / d['close'].shift(1).clip(lower=1) * 50, 0, 1)
                    raw = vs * 0.5 + ys * 0.5
                else:
                    raw = np.clip((d['vr'] - 2.5) / 5.0, 0, 1)
                
                af = np.clip(1 - (d['atr_pct'] - 0.3) / 3.0, 0, 1)
                score = np.clip(raw * af * np.clip(1 + d['vz']/5, 0.5, 1.5), 0, 1)
                d_out = d.copy()
                d_out['score'] = score
                sym_signals[key] = (d_out, dir_, hold, atr_m)
        
        signals[sym] = sym_signals
    print(f"Сигналы: {len(signals)} тикеров")

    # ─── Симуляция ───
    cash = INITIAL_CAPITAL
    peak = INITIAL_CAPITAL
    max_dd = 0
    
    kelly_hist = defaultdict(lambda: {'w': 0, 'l': 0, 'pnl': []})
    positions = {}
    all_trades = []
    
    all_ts = sorted({t for df in data.values() for t in df.index})
    print(f"Всего баров: {len(all_ts)}")
    
    for idx, ts in enumerate(all_ts):
        if idx % 50000 == 0:
            print(f"  {idx}/{len(all_ts)} cash={cash:,.0f} pos={len(positions)}")
        
        # === ВЫХОДЫ ===
        to_close = []
        for sym, pos in list(positions.items()):
            real_sym = pos.get('real_sym', sym)
            if real_sym not in data or ts not in data[real_sym].index:
                continue
            bar = data[real_sym].loc[ts]
            exit_price = None
            reason = ''
            
            if pos['dir'] == 'L' and bar['low'] <= pos['stop']:
                exit_price = pos['stop']; reason = 'stop'
            elif pos['dir'] == 'S' and bar['high'] >= pos['stop']:
                exit_price = pos['stop']; reason = 'stop'
            
            if exit_price is None and pos.get('bars_held', 0) >= pos.get('hold', 40):
                exit_price = bar['close']; reason = 'time'
            
            # Score fade
            if exit_price is None and 'pattern' in pos:
                sig_key = f"{pos['pattern']}_{pos['dir']}"
                if real_sym in signals and sig_key in signals[real_sym]:
                    df_sig, _, _, _ = signals[real_sym][sig_key]
                    if ts in df_sig.index:
                        score = float(df_sig.loc[ts, 'score'])
                        if score < 0.10:
                            exit_price = bar['close']; reason = 'fade'
            
            if exit_price is not None:
                dm = 1 if pos['dir'] == 'L' else -1
                pnl_pct = dm * (exit_price - pos['entry']) / pos['entry']
                pnl_rub = pnl_pct * pos['go'] * pos['contracts']
                cash += pnl_rub
                
                all_trades.append({
                    'sym': real_sym, 'dir': pos['dir'],
                    'pnl_rub': pnl_rub, 'reason': reason,
                    'entry': pos.get('entry_ts', ts),
                    'exit': ts,
                })
                
                if pnl_rub > 0:
                    kelly_hist[real_sym]['w'] += 1
                else:
                    kelly_hist[real_sym]['l'] += 1
                kelly_hist[real_sym]['pnl'].append(pnl_rub)
                if len(kelly_hist[real_sym]['pnl']) > KELLY_WINDOW:
                    kelly_hist[real_sym]['pnl'].pop(0)
                
                to_close.append(sym)
        
        for s in to_close:
            del positions[s]
        
        # MTM equity
        mtm_pnl = 0
        for sym, pos in list(positions.items()):
            rs = pos.get('real_sym', sym)
            if rs in data and ts in data[rs].index:
                bar = data[rs].loc[ts]
                dm = 1 if pos['dir'] == 'L' else -1
                mtm_pnl += dm * (bar['close'] - pos['entry']) / pos['entry'] * pos['go'] * pos['contracts']
        
        total_eq = cash + mtm_pnl
        if total_eq > peak: peak = total_eq
        dd = (peak - total_eq) / peak
        if dd > max_dd: max_dd = dd
        
        # ⛔ НЕТ circuit breaker
        
        # === ВХОДЫ ===
        if ts.hour < 7 or ts.hour >= 23:
            continue
        
        locked_go = sum(p['go'] * p.get('contracts', 0) for p in positions.values())
        avail = cash - locked_go
        if avail <= 0: continue
        
        entries = []
        
        for lst_name, lst in PORTFOLIO.items():
            for sym, pat, d_, hold, atr_m, weight in lst:
                if sym in positions or sym not in data: continue
                if sym not in signals: continue
                sig_key = f"{pat}_{d_}"
                if sig_key not in signals[sym]: continue
                
                df_sig, _, _, _ = signals[sym][sig_key]
                if ts not in df_sig.index: continue
                bar_sig = df_sig.loc[ts]
                
                score = float(bar_sig.get('score', 0))
                if np.isnan(score) or score < (SCORE_THRESHOLD_LONG if d_ == 'L' else SCORE_THRESHOLD_SHORT):
                    continue
                
                go = go_map.get(sym, 5000)
                
                # Агрессивный Kelly
                kh = kelly_hist[sym]
                kelly = KELLY_MIN
                if kh['w'] + kh['l'] >= 10:
                    wr = kh['w'] / max(kh['w'] + kh['l'], 1)
                    avg_w = max(sum(p for p in kh['pnl'] if p>0) / max(kh['w'],1), 1)
                    avg_l = max(abs(sum(p for p in kh['pnl'] if p<0) / max(kh['l'],1)), 1)
                    rr = avg_w / avg_l if avg_l > 0 else 1.5
                    k = wr - (1-wr) / max(rr, 0.5)
                    kelly = max(KELLY_MIN, min(k, KELLY_MAX))
                
                pct = min(kelly * score * weight, MAX_POSITION_PCT)
                max_rub = avail * pct
                contracts = max(1, int(max_rub / go))
                
                if contracts == 0: continue
                atr_v = float(bar_sig.get('atr14', 0))
                if atr_v == 0 or np.isnan(atr_v): continue
                
                ep = float(bar_sig['close'])
                stop = ep - atr_v * atr_m if d_ == 'L' else ep + atr_v * atr_m
                
                entries.append((sym, pat, d_, hold, contracts, ep, stop, go, score, lst_name))
        
        entries.sort(key=lambda e: e[8], reverse=True)
        
        for ent in entries[:MAX_ENTRIES_PER_BAR]:
            sym, pat, d_, hold, contracts, ep, stop, go, score, role = ent
            cost = contracts * go
            if cost > avail: continue
            
            positions[sym] = {
                'real_sym': sym, 'dir': d_, 'hold': hold,
                'entry': ep, 'stop': stop, 'contracts': contracts,
                'go': go, 'bars_held': 0, 'entry_ts': ts, 'pattern': pat,
            }
            avail -= cost
    
    # Close остатки
    for sym, pos in list(positions.items()):
        rs = pos.get('real_sym', sym)
        if rs in data:
            last_bar = data[rs].iloc[-1]
            dm = 1 if pos['dir'] == 'L' else -1
            pnl_pct = dm * (last_bar['close'] - pos['entry']) / pos['entry']
            pnl_rub = pnl_pct * pos['go'] * pos['contracts']
            cash += pnl_rub
            all_trades.append({'sym': rs, 'dir': pos['dir'], 'pnl_rub': pnl_rub, 'reason': 'eod'})
    
    # Итоги
    total_r = (cash - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100
    wins = sum(1 for t in all_trades if t.get('pnl_rub',0) > 0)
    total_t = len(all_trades)
    wr = wins / total_t * 100 if total_t > 0 else 0
    
    days = max((all_ts[-1] - all_ts[0]).days, 1) if all_ts else 365
    years = days / 365.25
    ann_r = (cash / INITIAL_CAPITAL) ** (1 / max(years, 0.1)) - 1
    calmar = (ann_r * 100) / (max_dd * 100) if max_dd > 0 else 0
    
    print(f"\n{'='*60}")
    print(f"СЦЕНАРИЙ A: Агрессивный портфель (DD target 15-20%)")
    print(f"{'='*60}")
    print(f"Capital: {INITIAL_CAPITAL:,.0f} → {cash:,.0f} ₽")
    print(f"Return:  {total_r:+.1f}%  ({ann_r*100:+.1f}%/год)")
    print(f"Max DD:  {max_dd*100:.1f}%")
    print(f"Calmar:  {calmar:.2f}")
    print(f"WR:      {wr:.1f}% ({wins}/{total_t})")
    print(f"Period:  {days} дней")
    
    sym_stats = defaultdict(lambda: {'pnl':0, 'w':0, 'l':0, 'n':0})
    for t in all_trades:
        s = t.get('sym','?')
        sym_stats[s]['pnl'] += t.get('pnl_rub',0)
        sym_stats[s]['n'] += 1
        if t.get('pnl_rub',0) > 0: sym_stats[s]['w'] += 1
        else: sym_stats[s]['l'] += 1
    
    print(f"\nПо тикерам:")
    for s, st in sorted(sym_stats.items(), key=lambda x: x[1]['pnl'], reverse=True):
        wr_s = st['w']/st['n']*100 if st['n']>0 else 0
        print(f"  {s}: {st['pnl']:+,.0f} ₽ WR={wr_s:.0f}% ({st['n']} тр)")
    
    result = {
        'capital': cash, 'return_pct': total_r,
        'annual_return': ann_r * 100, 'max_dd_pct': max_dd * 100,
        'calmar': calmar, 'wr': wr, 'n_trades': total_t,
        'sym_stats': {s: st for s, st in sym_stats.items()},
    }
    return result


if __name__ == '__main__':
    print("=== Scenario A: Aggressive DD 15-20% ===")
    all_symbols = set()
    for lst in PORTFOLIO.values():
        all_symbols.update(c[0] for c in lst)
    print(f"Тикеры ({len(all_symbols)}): {sorted(all_symbols)}")
    
    ch = clickhouse_connect.get_client(host='127.0.0.1', port=8123)
    
    print("Загрузка данных...")
    data = {}
    for sym in all_symbols:
        q = f"""
            SELECT p.time,p.open,p.high,p.low,p.close,p.volume,
                   o.fiz_buy,o.fiz_sell,o.yur_buy,o.yur_sell
            FROM moex.prices_5m p
            LEFT JOIN moex.prices_5m_oi o ON p.time=o.time AND p.symbol=o.symbol
            WHERE p.symbol='{sym}' AND p.time>='2024-01-01' AND p.time<='2026-04-30'
            ORDER BY p.time
        """
        try:
            r = ch.query(q)
            if r.result_rows:
                cols = ['time','open','high','low','close','volume',
                        'fiz_buy','fiz_sell','yur_buy','yur_sell']
                df = pd.DataFrame(r.result_rows, columns=cols)
                df['time'] = pd.to_datetime(df['time'])
                df.set_index('time', inplace=True)
                data[sym] = df
                print(f"  ✓ {sym}: {len(df)} bars")
        except Exception as e:
            print(f"  ✗ {sym}: {e}")
    
    print(f"\nЗагружено {len(data)}/{len(all_symbols)} тикеров")
    
    # GO
    go_map = {}
    for sym in all_symbols:
        go_map[sym] = TICKER_CONFIGS.get(sym, {}).get('go', 5000)
    
    result = run_scenario_a(data, go_map)
    
    os.makedirs('reports/phase5_scenario_a', exist_ok=True)
    with open('reports/phase5_scenario_a/result.json', 'w') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"\nСохранено: reports/phase5_scenario_a/result.json")
