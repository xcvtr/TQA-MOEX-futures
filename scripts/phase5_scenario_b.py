#!/usr/bin/env python3
"""
Scenario B: Daily OI-паттерны + 5m stacked confirmation.
5 паттернов × 5 тикеров = портфель с DD до 20%.
Основан на references/daily-oi-patterns-and-stacked-confirmation.md
"""
import json, sys, os
from datetime import datetime, timedelta
from collections import defaultdict

import numpy as np
import pandas as pd
import clickhouse_connect

from bar_level_sim import TICKER_CONFIGS

INITIAL_CAPITAL = 100_000

# CBR dates
CBR_DATES = [
    '2024-02-16','2024-03-22','2024-04-26','2024-06-07','2024-07-26',
    '2024-09-13','2024-10-25','2024-12-20','2025-02-14','2025-03-21',
    '2025-04-25','2025-06-13','2025-07-25','2025-09-12','2025-10-24',
    '2025-12-19','2026-02-14','2026-03-21','2026-04-25',
]
CBR_DATES_SET = set(CBR_DATES)

def is_cbr_day(d):
    """Проверить, находится ли дата в ±2 дня от заседания ЦБ"""
    for cbr in CBR_DATES:
        cd = datetime.strptime(cbr, '%Y-%m-%d').date()
        if abs((d - cd).days) <= 2:
            return True
    return False

def rz(s, w=20):
    m = s.rolling(w, min_periods=w).mean()
    std = s.rolling(w, min_periods=w).std()
    return (s - m) / std.clip(lower=1e-10)

def calc_atr(df, p=14):
    prev = df['close'].shift(1)
    tr = pd.concat([df['high']-df['low'], (df['high']-prev).abs(), (df['low']-prev).abs()], axis=1).max(axis=1)
    return tr.rolling(p, min_periods=p).mean().bfill().fillna(0)


PORTFOLIO_TICKERS = ['RI', 'GL', 'USDRUBF', 'AF', 'BR']


def compute_daily_signals(m5_data, sym):
    """Resample 5m→daily и вычислить все 5 паттернов. Возвращает DataFrame с сигналами."""
    if sym not in m5_data:
        return None
    df5 = m5_data[sym]
    
    # Resample to daily
    daily = pd.DataFrame()
    daily['open'] = df5['open'].resample('D').first()
    daily['high'] = df5['high'].resample('D').max()
    daily['low'] = df5['low'].resample('D').min()
    daily['close'] = df5['close'].resample('D').last()
    daily['volume'] = df5['volume'].resample('D').sum()
    daily['fiz_buy'] = df5['fiz_buy'].fillna(0).resample('D').sum()
    daily['fiz_sell'] = df5['fiz_sell'].fillna(0).resample('D').sum()
    daily['yur_buy'] = df5['yur_buy'].fillna(0).resample('D').sum()
    daily['yur_sell'] = df5['yur_sell'].fillna(0).resample('D').sum()
    
    daily['fiz_net'] = daily['fiz_buy'] - daily['fiz_sell']
    daily['yur_net'] = daily['yur_buy'] - daily['yur_sell']
    daily['oi'] = daily['fiz_buy'] + daily['fiz_sell'] + daily['yur_buy'] + daily['yur_sell']
    daily['fiz_net_change'] = daily['fiz_net'].diff()
    
    # Volume z-score
    daily['vol_z'] = rz(daily['volume'], 20)
    daily['oi_z'] = rz(daily['oi'], 20)
    daily['yur_buy_pct'] = daily['yur_buy'] / daily['oi'].clip(lower=1)
    daily['fiz_net_pct'] = daily['fiz_net'] / daily['oi'].clip(lower=1)
    
    # Базовые признаки
    daily['vol_up'] = daily['vol_z'] > 1.0
    daily['oi_up'] = daily['oi_z'] > 0.5
    daily['oi_down'] = daily['oi_z'] < -0.5
    daily['yb_up'] = daily['yur_buy'] > daily['yur_buy'].shift(1)
    daily['yb_down'] = daily['yur_buy'] < daily['yur_buy'].shift(1)
    daily['fn_down'] = daily['fiz_net'] < daily['fiz_net'].shift(1)
    daily['fn_up'] = daily['fiz_net'] > daily['fiz_net'].shift(1)
    daily['fiz_extreme'] = abs(daily['fiz_net_change']) > daily['fiz_net_change'].std() * 5
    
    # ATR
    daily['atr14'] = calc_atr(daily)
    
    # CBR filter
    daily['cbr_exclude'] = daily.index.map(lambda d: is_cbr_day(d.date()))
    
    # 5m stacked confirmation: последние 3 5m бара дня
    # Перебираем 5m бары, группируем по дате
    stacked = {}
    for date, grp in df5.groupby(df5.index.date):
        last3 = grp.iloc[-3:]
        # fiz_net z-score на последних 3 барах
        fiz_net_3 = last3['fiz_buy'].fillna(0) - last3['fiz_sell'].fillna(0)
        if len(fiz_net_3) > 0:
            mean_f = fiz_net_3.mean()
            std_f = max(fiz_net_3.std(), 1)
            stacked[date] = {
                'fiz_z_last3': mean_f / std_f,
                'vol_z_last3': (last3['volume'].sum() / last3['volume'].mean()) if last3['volume'].mean() > 0 else 0,
            }
    
    # 5 паттернов
    patterns = {}
    
    # 1. vol_up_oi_up_yb_up → LONG
    cond1 = daily['vol_up'] & daily['oi_up'] & daily['yb_up']
    patterns['vol_up_oi_up_yb_up'] = cond1
    
    # 2. vol_up_oi_down → SHORT
    cond2 = daily['vol_up'] & daily['oi_down']
    patterns['vol_up_oi_down'] = cond2
    
    # 3. smart_money → LONG: vol↑ + yur_buy↑ + fiz_net↓
    cond3 = daily['vol_up'] & daily['yb_up'] & daily['fn_down']
    patterns['smart_money'] = cond3
    
    # 4. vol_up_yb_down_fiz_up → SHORT
    cond4 = daily['vol_up'] & daily['yb_down'] & daily['fn_up']
    patterns['vol_up_yb_down_fiz_up'] = cond4
    
    # 5. fiz_extreme_vol_up → SHORT
    cond5 = daily['vol_up'] & daily['fiz_extreme']
    patterns['fiz_extreme_vol_up'] = cond5
    
    # Score for each pattern (0-1)
    for name, cond in patterns.items():
        score_col = f'score_{name}'
        daily[score_col] = 0.0
        
        # Base score from vol_z strength
        base_score = np.clip((daily['vol_z'] - 1.0) / 3.0, 0, 1)
        daily[score_col] = base_score * cond.astype(float)
        
        # Boost by stacked confirmation
        for date_key, stack in stacked.items():
            dt = pd.Timestamp(date_key)
            if dt in daily.index and cond.loc[dt]:
                boost = np.clip(stack.get('fiz_z_last3', 0) / 3.0, 0, 0.5)
                daily.loc[dt, score_col] = daily.loc[dt, score_col] + boost
        
        daily[score_col] = np.clip(daily[score_col], 0, 1)
        
        # CBR filter
        daily.loc[daily['cbr_exclude'], score_col] = 0
    
    # Для stacked confirmation отдельный столбец
    daily['stacked_z'] = 0.0
    for date_key, stack in stacked.items():
        dt = pd.Timestamp(date_key)
        if dt in daily.index:
            daily.loc[dt, 'stacked_z'] = stack.get('fiz_z_last3', 0)
    
    daily['atr14'] = calc_atr(daily)
    
    return daily


def run_scenario_b(m5_data):
    print("Resampling and computing daily signals...")
    daily_data = {}
    for sym in PORTFOLIO_TICKERS:
        d = compute_daily_signals(m5_data, sym)
        if d is not None and len(d) > 100:
            daily_data[sym] = d
            print(f"  {sym}: {len(d)} days")
    
    # ─── Симуляция ───
    cash = INITIAL_CAPITAL
    peak = INITIAL_CAPITAL
    max_dd = 0
    
    kelly_hist = defaultdict(lambda: {'w': 0, 'l': 0, 'pnl': []})
    positions = {}
    all_trades = []
    
    all_dates = sorted({d for df in daily_data.values() for d in df.index})
    print(f"Всего дней: {len(all_dates)}")
    
    for idx, ts in enumerate(all_dates):
        if idx % 200 == 0:
            print(f"  Day {idx}/{len(all_dates)} cash={cash:,.0f} pos={len(positions)}")
        
        # === ВЫХОДЫ ===
        to_close = []
        for sym, pos in list(positions.items()):
            if sym not in daily_data or ts not in daily_data[sym].index:
                continue
            bar = daily_data[sym].loc[ts]
            exit_price = None
            reason = ''
            
            if pos['dir'] == 'L' and bar['low'] <= pos['stop']:
                exit_price = pos['stop']; reason = 'stop'
            elif pos['dir'] == 'S' and bar['high'] >= pos['stop']:
                exit_price = pos['stop']; reason = 'stop'
            
            if exit_price is None and pos.get('bars_held', 0) >= pos.get('hold', 5):
                exit_price = bar['close']; reason = 'time'
            
            if exit_price is not None:
                dm = 1 if pos['dir'] == 'L' else -1
                pnl_pct = dm * (exit_price - pos['entry']) / pos['entry']
                pnl_rub = pnl_pct * pos['go'] * pos['contracts']
                cash += pnl_rub
                
                all_trades.append({
                    'sym': sym, 'dir': pos['dir'], 'pnl_rub': pnl_rub,
                    'reason': reason, 'entry': pos.get('entry_ts', ts), 'exit': ts,
                })
                
                if pnl_rub > 0: kelly_hist[sym]['w'] += 1
                else: kelly_hist[sym]['l'] += 1
                kelly_hist[sym]['pnl'].append(pnl_rub)
                to_close.append(sym)
        
        for s in to_close:
            del positions[s]
        
        # MTM
        mtm_pnl = 0
        for sym, pos in list(positions.items()):
            if sym in daily_data and ts in daily_data[sym].index:
                bar = daily_data[sym].loc[ts]
                dm = 1 if pos['dir'] == 'L' else -1
                mtm_pnl += dm * (bar['close'] - pos['entry']) / pos['entry'] * pos['go'] * pos['contracts']
        
        total_eq = cash + mtm_pnl
        if total_eq > peak: peak = total_eq
        dd_pct = (peak - total_eq) / peak if peak > 0 else 0
        if dd_pct > max_dd: max_dd = dd_pct
        
        # === ВХОДЫ ===
        locked_go = sum(p['go'] * p.get('contracts', 0) for p in positions.values())
        avail = cash - locked_go
        if avail <= 0: continue
        
        entries = []
        
        # Pattern-ticker mapping
        ptn_map = {
            # (ticker, pattern) → (direction, hold, weight)
            ('RI', 'smart_money'): ('L', 5, 1.0),
            ('GL', 'vol_up_oi_up_yb_up'): ('L', 3, 1.0),
            ('USDRUBF', 'vol_up_oi_down'): ('S', 3, 1.0),
            ('USDRUBF', 'vol_up_yb_down_fiz_up'): ('S', 2, 1.0),
            ('AF', 'vol_up_oi_down'): ('S', 5, 1.0),
            ('BR', 'vol_up_yb_down_fiz_up'): ('S', 3, 1.0),
            ('BR', 'fiz_extreme_vol_up'): ('S', 4, 1.0),
            ('RI', 'vol_up_oi_up_yb_up'): ('L', 3, 1.0),
            ('GL', 'smart_money'): ('L', 3, 1.0),
        }
        
        for sym, pat in ptn_map:
            dir_, hold, weight = ptn_map[(sym, pat)]
            
            if sym in positions or sym not in daily_data: continue
            if ts not in daily_data[sym].index: continue
            
            bar = daily_data[sym].loc[ts]
            score_col = f'score_{pat}'
            
            if score_col not in bar.index:
                continue
            score = float(bar[score_col])
            if np.isnan(score) or score < 0.05: continue  # очень низкий порог
            
            go = TICKER_CONFIGS.get(sym, {}).get('go', 5000)
            
            # Kelly
            kh = kelly_hist[sym]
            kelly = 0.05
            if kh['w'] + kh['l'] >= 5:
                wr = kh['w'] / max(kh['w'] + kh['l'], 1)
                avg_w = max(sum(p for p in kh['pnl'] if p>0) / max(kh['w'],1), 1)
                avg_l = max(abs(sum(p for p in kh['pnl'] if p<0) / max(kh['l'],1)), 1)
                rr = avg_w / avg_l if avg_l > 0 else 1.5
                k = wr - (1-wr) / max(rr, 0.5)
                kelly = max(0.05, min(k, 0.30))
            
            pct = min(kelly * score * weight, 0.30)
            max_rub = avail * pct
            contracts = max(1, int(max_rub / go))
            
            if contracts == 0: continue
            atr_v = float(bar.get('atr14', 0))
            if atr_v == 0 or np.isnan(atr_v): continue
            
            ep = float(bar['close'])
            stop = ep - atr_v * 2 if dir_ == 'L' else ep + atr_v * 2
            
            entries.append((sym, pat, dir_, hold, contracts, ep, stop, go, score))
        
        entries.sort(key=lambda e: e[8], reverse=True)
        
        for ent in entries[:3]:
            sym, pat, dir_, hold, contracts, ep, stop, go, score = ent
            cost = contracts * go
            if cost > avail: continue
            
            positions[sym] = {
                'real_sym': sym, 'dir': dir_, 'hold': hold,
                'entry': ep, 'stop': stop, 'contracts': contracts,
                'go': go, 'bars_held': 0, 'entry_ts': ts, 'pattern': pat,
            }
            avail -= cost
    
    # Close остатки
    for sym, pos in list(positions.items()):
        if sym in daily_data:
            last_bar = daily_data[sym].iloc[-1]
            dm = 1 if pos['dir'] == 'L' else -1
            pnl_pct = dm * (last_bar['close'] - pos['entry']) / pos['entry']
            pnl_rub = pnl_pct * pos['go'] * pos['contracts']
            cash += pnl_rub
            all_trades.append({'sym': sym, 'dir': pos['dir'], 'pnl_rub': pnl_rub, 'reason': 'eod'})
    
    total_r = (cash - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100
    wins = sum(1 for t in all_trades if t.get('pnl_rub',0) > 0)
    total_t = len(all_trades)
    wr = wins / total_t * 100 if total_t > 0 else 0
    
    years = max(len(all_dates)/365.25, 0.1)
    ann_r = (cash / INITIAL_CAPITAL) ** (1 / max(years, 0.1)) - 1
    calmar = (ann_r * 100) / (max_dd * 100) if max_dd > 0 else 0
    
    print(f"\n{'='*60}")
    print(f"СЦЕНАРИЙ B: Daily OI-паттерны")
    print(f"{'='*60}")
    print(f"Capital: {INITIAL_CAPITAL:,.0f} → {cash:,.0f} ₽")
    print(f"Return:  {total_r:+.1f}%  ({ann_r*100:+.1f}%/год)")
    print(f"Max DD:  {max_dd*100:.1f}%")
    print(f"Calmar:  {calmar:.2f}")
    print(f"WR:      {wr:.1f}% ({wins}/{total_t})")
    
    sym_stats = defaultdict(lambda: {'pnl':0, 'w':0, 'l':0, 'n':0})
    for t in all_trades:
        s = t.get('sym','?')
        sym_stats[s]['pnl'] += t.get('pnl_rub',0)
        sym_stats[s]['n'] += 1
        if t.get('pnl_rub',0) > 0: sym_stats[s]['w'] += 1
    
    print(f"\nПо тикерам:")
    for s, st in sorted(sym_stats.items(), key=lambda x: x[1]['pnl'], reverse=True):
        wr_s = st['w']/st['n']*100 if st['n']>0 else 0
        print(f"  {s}: {st['pnl']:+,.0f} ₽ WR={wr_s:.0f}% ({st['n']} тр)")
    
    return {
        'capital': cash, 'return_pct': total_r,
        'annual_return': ann_r * 100, 'max_dd_pct': max_dd * 100,
        'calmar': calmar, 'wr': wr, 'n_trades': total_t,
        'sym_stats': {s: st for s, st in sym_stats.items()},
    }


if __name__ == '__main__':
    print(f"=== Scenario B: Daily OI Patterns ===")
    print(f"Tickers: {PORTFOLIO_TICKERS}")
    
    ch = clickhouse_connect.get_client(host='127.0.0.1', port=8123)
    
    print("Loading 5m data for daily resampling...")
    m5_data = {}
    for sym in PORTFOLIO_TICKERS:
        q = f"""
            SELECT p.time,p.open,p.high,p.low,p.close,p.volume,
                   o.fiz_buy,o.fiz_sell,o.yur_buy,o.yur_sell
            FROM moex.prices_5m p
            LEFT JOIN moex.prices_5m_oi o ON p.time=o.time AND p.symbol=o.symbol
            WHERE p.symbol='{sym}' AND p.time>='2020-01-01' AND p.time<='2026-04-30'
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
                m5_data[sym] = df
                print(f"  ✓ {sym}: {len(df)} 5m bars")
        except Exception as e:
            print(f"  ✗ {sym}: {e}")
    
    print(f"\nLoaded {len(m5_data)}/{len(PORTFOLIO_TICKERS)} tickers")
    
    result = run_scenario_b(m5_data)
    
    os.makedirs('reports/phase5_scenario_b', exist_ok=True)
    with open('reports/phase5_scenario_b/result.json', 'w') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"\nSaved: reports/phase5_scenario_b/result.json")
