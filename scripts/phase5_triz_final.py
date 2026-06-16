#!/usr/bin/env python3
"""
Phase 5.3: Финальный ТРИЗ-портфель с Collar хеджем.
- Правильный MTM equity (cash + mark-to-market открытых позиций)
- Si SHORT как хедж (collar — продажа колла)
- Adaptive Kelly по скользящему окну
- Max DD circuit breaker (15% → стоп)
- Score-based entry с порогом
"""
import json, sys, os
from datetime import datetime, timedelta
from collections import defaultdict

import numpy as np
import pandas as pd
import clickhouse_connect

INITIAL_CAPITAL = 100_000  # 100K RUB как реально
MAX_DD_STOP = 0.15         # 15% → стоп всей торговли
KELLY_WINDOW = 50          # скользящее окно для Kelly

PORTFOLIO = {
    'core': [  # LONG
        ('GL', 'vod', 'L', 21, 2, 1.0),
        ('RN', 'vou', 'L', 5, 5, 1.0),
        ('HY', 'vou', 'L', 5, 5, 1.0),
        ('NM', 'vod', 'L', 21, 3, 1.0),
        ('AF', 'sm',  'L', 21, 2, 1.0),
    ],
    'hedge': [  # SHORT
        ('BR', 'vyf', 'S', 13, 5, 1.0),
        ('SV', 'vod', 'S', 5,  5, 1.0),
        ('SF', 'vod', 'S', 8,  3, 0.3),  # SF — пониженный вес
    ],
}

# Si как collar-хедж (SHORT при открытии LONG позиций)
COLLAR_SYMBOL = 'Si'
COLLAR_DIRECTION = 'S'  # SHORT Si = хедж против падения рынка
COLLAR_HEDGE_RATIO = 0.3  # 30% от капитала в хедже


def rz(series, window=20):
    mean = series.rolling(window, min_periods=window).mean()
    std = series.rolling(window, min_periods=window).std()
    return (series - mean) / std.clip(lower=1e-10)


def calc_atr(df, period=14):
    prev = df['close'].shift(1)
    tr = pd.concat([
        df['high'] - df['low'],
        (df['high'] - prev).abs(),
        (df['low'] - prev).abs()
    ], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=period).mean().bfill().fillna(0)


def compute_signal(df, pattern, direction):
    """Compute score [0,1] for a pattern"""
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
    
    d['atr14'] = calc_atr(d)
    d['atr_pct'] = d['atr14'] / d['close'].clip(lower=1) * 100
    
    dm = 1 if direction == 'L' else -1
    
    if pattern == 'vod':  # vol↑ OI↓ → SHORT
        vs = np.clip((d['vr'] - 1.5) / 3.0, 0, 1)
        if has_oi:
            oima = d['oi_r'].rolling(20).mean()
            os_ = np.clip((oima - d['oi_r']) / oima.clip(lower=0.1), 0, 1)
        else:
            os_ = 0.5
        d['raw'] = vs * 0.6 + os_ * 0.4
        
    elif pattern == 'vou':  # vol↑ OI↑ → LONG
        vs = np.clip((d['vr'] - 1.5) / 3.0, 0, 1)
        if has_oi:
            oima = d['oi_r'].rolling(20).mean()
            os_ = np.clip((d['oi_r'] - oima) / oima.clip(lower=0.1), 0, 1)
        else:
            os_ = 0.5
        d['raw'] = vs * 0.6 + os_ * 0.4
        
    elif pattern == 'sm':  # Smart Money
        if has_oi:
            ys = np.clip(abs(d['yz']) / 3.0, 0, 1)
            fs = np.clip(abs(d['fz']) / 3.0, 0, 1)
            d['raw'] = ys * 0.7 + fs * 0.3
        else:
            d['raw'] = np.clip((d['vr'] - 1.5) / 3.0, 0, 1)
            
    elif pattern == 'vyf':  # Vol Yur Flow
        vs = np.clip((d['vr'] - 2.0) / 4.0, 0, 1)
        if has_oi:
            yn = d['yur_net'].fillna(0)
            ys = np.clip(yn / max(yn.std(), 1) * dm, 0, 1)
        else:
            ys = np.clip((d['close'] - d['close'].shift(1)) / d['close'].shift(1).clip(lower=1) * 50, 0, 1)
        d['raw'] = vs * 0.5 + ys * 0.5
    else:
        d['raw'] = np.clip((d['vr'] - 2.5) / 5.0, 0, 1)
    
    # ATR фильтр
    af = np.clip(1 - (d['atr_pct'] - 0.3) / 3.0, 0, 1)
    d['score'] = np.clip(d['raw'] * af * np.clip(1 + d['vz']/5, 0.5, 1.5), 0, 1)
    return d


def load_data(ch, symbols, start='2024-01-01', end='2026-04-30'):
    data = {}
    for sym in symbols:
        q = f"""
            SELECT p.time, p.open, p.high, p.low, p.close, p.volume,
                   o.fiz_buy, o.fiz_sell, o.yur_buy, o.yur_sell
            FROM moex.prices_5m p
            LEFT JOIN moex.prices_5m_oi o ON p.time=o.time AND p.symbol=o.symbol
            WHERE p.symbol='{sym}' AND p.time>='{start}' AND p.time<='{end}'
            ORDER BY p.time
        """
        try:
            r = ch.query(q)
            if not r.result_rows: continue
            cols = ['time','open','high','low','close','volume',
                    'fiz_buy','fiz_sell','yur_buy','yur_sell']
            df = pd.DataFrame(r.result_rows, columns=cols)
            df['time'] = pd.to_datetime(df['time'])
            df.set_index('time', inplace=True)
            data[sym] = df
        except Exception as e:
            print(f"  ✗ {sym}: {e}")
    return data


def load_go(ch, symbols):
    """Загрузить ГО из securities или bar_level_sim"""
    from bar_level_sim import TICKER_CONFIGS
    go_map = {}
    for sym in symbols:
        go_map[sym] = TICKER_CONFIGS.get(sym, {}).get('go', 5000)
        try:
            r = ch.query(f"SELECT go FROM moex.securities WHERE symbol='{sym}' AND exchange='MOEX'")
            if r.result_rows:
                go_map[sym] = r.result_rows[0][0]
        except:
            pass
    return go_map


def run_triz_portfolio(data, go_map):
    """Финальная симуляция с правильным MTM"""
    
    # Предвычисление сигналов — используем векторизованный подход
    print("Предвычисление сигналов...")
    signals = {}
    for sym, df in data.items():
        sym_signals = {}
        seen = set()
        # OHE prep — один раз на тикер
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
                
                if pat == 'vod':
                    vs = np.clip((d['vr'] - 1.5) / 3.0, 0, 1)
                    if has_oi:
                        os_ = np.clip((d['oima'] - d['oi_r']) / d['oima'].clip(lower=0.1), 0, 1)
                    else:
                        os_ = 0.5
                    raw = vs * 0.6 + os_ * 0.4
                elif pat == 'vou':
                    vs = np.clip((d['vr'] - 1.5) / 3.0, 0, 1)
                    if has_oi:
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
    print(f"Сигналы вычислены для {len(signals)} тикеров")
    
    # Коллар сигнал (Si SHORT)
    collar_df = None
    if COLLAR_SYMBOL in data:
        collar_df = compute_signal(data[COLLAR_SYMBOL], 'vou', 'S')
    
    # ─── Симуляция ───
    cash = INITIAL_CAPITAL
    peak = INITIAL_CAPITAL
    max_dd = 0
    circuit_breaker = False
    
    # Kelly история
    kelly_hist = defaultdict(lambda: {'w': 0, 'l': 0, 'pnl': []})
    
    positions = {}  # {sym: {...}}
    all_trades = []
    eq_curve = [(datetime(2024,1,3), cash, 0)]
    
    # Собираем все временные метки
    all_ts = sorted({t for df in data.values() for t in df.index})
    print(f"Всего баров: {len(all_ts)}")
    
    for idx, ts in enumerate(all_ts):
        if circuit_breaker:
            break
        
        if idx % 50000 == 0:
            print(f"  {idx}/{len(all_ts)} баров, cash={cash:,.0f}, {len(positions)} позиций")
        
        # === ВЫХОДЫ ===
        to_close = []
        for sym, pos in positions.items():
            if ts not in data.get(pos['real_sym'], pd.DataFrame()).index:
                continue
            bar = data[pos['real_sym']].loc[ts]
            exit_price = None
            reason = ''
            
            # Стоп
            if pos['dir'] == 'L' and bar['low'] <= pos['stop']:
                exit_price = pos['stop']; reason = 'stop'
            elif pos['dir'] == 'S' and bar['high'] >= pos['stop']:
                exit_price = pos['stop']; reason = 'stop'
            
            # Time exit
            if exit_price is None and pos['bars_held'] >= pos['hold']:
                exit_price = bar['close']; reason = 'time'
            
            # Score fade exit
            if exit_price is None and 'pattern' in pos:
                # Используем предвычисленные сигналы
                sig_key = f"{pos['pattern']}_{pos['dir']}"
                if pos['real_sym'] in signals and sig_key in signals[pos['real_sym']]:
                    df_sig, _, _, _ = signals[pos['real_sym']][sig_key]
                    if ts in df_sig.index:
                        score = df_sig.loc[ts, 'score']
                        if score < 0.15:
                            exit_price = bar['close']; reason = 'fade'
            
            if exit_price is not None:
                dm = 1 if pos['dir'] == 'L' else -1
                pnl_pct = dm * (exit_price - pos['entry']) / pos['entry']
                pnl_rub = pnl_pct * pos['go'] * pos['contracts']
                cash += pnl_rub
                
                all_trades.append({
                    'sym': pos['real_sym'],
                    'entry': pos['entry_ts'],
                    'exit': ts,
                    'dir': pos['dir'],
                    'pnl_rub': pnl_rub,
                    'pnl_pct': pnl_pct * 100,
                    'reason': reason,
                })
                
                if pnl_rub > 0:
                    kelly_hist[sym]['w'] += 1
                else:
                    kelly_hist[sym]['l'] += 1
                kelly_hist[sym]['pnl'].append(pnl_rub)
                if len(kelly_hist[sym]['pnl']) > KELLY_WINDOW:
                    kelly_hist[sym]['pnl'].pop(0)
                
                to_close.append(sym)
        
        for sym in to_close:
            del positions[sym]
        
        # === MTM Equity ===
        mtm_pnl = 0
        for sym, pos in list(positions.items()):
            if ts in data.get(pos['real_sym'], pd.DataFrame()).index:
                bar = data[pos['real_sym']].loc[ts]
                dm = 1 if pos['dir'] == 'L' else -1
                mtm_pnl += dm * (bar['close'] - pos['entry']) / pos['entry'] * pos['go'] * pos['contracts']
        
        total_eq = cash + mtm_pnl
        if total_eq > peak:
            peak = total_eq
        dd = (peak - total_eq) / peak
        if dd > max_dd:
            max_dd = dd
        
        if dd > MAX_DD_STOP:
            print(f"  ⛔ Circuit breaker at {ts}: DD={dd*100:.1f}%")
            circuit_breaker = True
            continue
        
        # === ВХОДЫ (только в рабочие часы) ===
        if ts.hour < 7 or ts.hour >= 23:
            continue
        
        locked_go = sum(p['go'] * p['contracts'] for p in positions.values())
        avail = cash - locked_go
        if avail <= 0:
            continue
        
        entries = []
        
        # Core (LONG)
        for sym, pat, d_, hold, atr_m, weight in PORTFOLIO['core']:
            if sym in positions or sym not in data: continue
            if sym not in signals: continue
            sig_key = f"{pat}_{d_}"
            if sig_key not in signals[sym]: continue
            
            df_sig, _, _, _ = signals[sym][sig_key]
            if ts not in df_sig.index: continue
            bar = df_sig.loc[ts]
            
            score = bar.get('score', 0)
            if score < 0.4 or np.isnan(score): continue
            
            go = go_map.get(sym, 5000)
            
            # Kelly
            kh = kelly_hist[sym]
            kelly = 0.08
            if kh['w'] + kh['l'] >= 10:
                wr = kh['w'] / max(kh['w'] + kh['l'], 1)
                avg_pnl_w = max(sum(p for p in kh['pnl'] if p>0) / max(kh['w'],1), 1)
                avg_pnl_l = max(abs(sum(p for p in kh['pnl'] if p<0) / max(kh['l'],1)), 1)
                rr = avg_pnl_w / avg_pnl_l if avg_pnl_l > 0 else 1.5
                k = wr - (1-wr) / max(rr, 0.5)
                kelly = max(0.03, min(k, 0.20))
            
            pct = min(kelly * score * weight, 0.15)
            max_rub = avail * pct
            contracts = max(1, int(max_rub / go))
            
            if contracts == 0: continue
            atr_v = bar.get('atr14', 0)
            if atr_v == 0: continue
            
            ep = bar['close']
            stop = ep - atr_v * atr_m if d_ == 'L' else ep + atr_v * atr_m
            
            entries.append((sym, pat, d_, hold, contracts, ep, stop, go, score, 'core'))
        
        # Hedge (SHORT)
        for sym, pat, d_, hold, atr_m, weight in PORTFOLIO['hedge']:
            if sym in positions or sym not in data: continue
            if sym not in signals: continue
            sig_key = f"{pat}_{d_}"
            if sig_key not in signals[sym]: continue
            
            df_sig, _, _, _ = signals[sym][sig_key]
            if ts not in df_sig.index: continue
            bar = df_sig.loc[ts]
            
            score = bar.get('score', 0)
            if score < 0.3 or np.isnan(score): continue
            
            go = go_map.get(sym, 5000)
            kh = kelly_hist[sym]
            kelly = 0.05
            if kh['w'] + kh['l'] >= 10:
                wr = kh['w'] / max(kh['w'] + kh['l'], 1)
                k = wr - (1-wr) / 1.2
                kelly = max(0.03, min(k, 0.15))
            
            pct = min(kelly * score * weight, 0.10)
            max_rub = avail * pct
            contracts = max(1, int(max_rub / go))
            
            if contracts == 0: continue
            atr_v = bar.get('atr14', 0)
            if atr_v == 0: continue
            
            ep = bar['close']
            stop = ep + atr_v * atr_m if d_ == 'S' else ep - atr_v * atr_m
            
            entries.append((sym, pat, d_, hold, contracts, ep, stop, go, score, 'hedge'))
        
        # Сортируем по score
        entries.sort(key=lambda e: e[8], reverse=True)
        
        for ent in entries[:3]:  # макс 3 входа на бар
            sym, pat, d_, hold, contracts, ep, stop, go, score, role = ent
            cost = contracts * go
            if cost > avail: continue
            
            positions[sym] = {
                'real_sym': sym,
                'dir': d_,
                'hold': hold,
                'atr_m': None,
                'entry': ep,
                'stop': stop,
                'contracts': contracts,
                'go': go,
                'bars_held': 0,
                'entry_ts': ts,
                'pattern': pat,
            }
            avail -= cost
        
        # === Коллар: Si SHORT (автоматический хедж) ===
        if COLLAR_SYMBOL not in positions and COLLAR_SYMBOL in data:
            if ts in data[COLLAR_SYMBOL].index:
                total_long_go = sum(
                    p['go'] * p['contracts'] for p in positions.values()
                    if p.get('dir') == 'L'
                )
                if total_long_go > 0:
                    go_si = go_map.get(COLLAR_SYMBOL, 1000)
                    hedge_size = int(total_long_go * COLLAR_HEDGE_RATIO / go_si)
                    if hedge_size > 0:
                        cost = hedge_size * go_si
                        if cost <= avail:
                            # Входим SHORT по Si
                            bar = data[COLLAR_SYMBOL].loc[ts]
                            ep = bar['close']
                            stop = ep + bar.get('atr14', ep*0.01) * 3
                            positions[COLLAR_SYMBOL] = {
                                'real_sym': COLLAR_SYMBOL,
                                'dir': 'S',
                                'hold': 48,  # 4 часа
                                'atr_m': None,
                                'entry': ep,
                                'stop': stop,
                                'contracts': hedge_size,
                                'go': go_si,
                                'bars_held': 0,
                                'entry_ts': ts,
                                'pattern': 'collar',
                            }
                            avail -= cost
        
        # Equity log (раз в день)
        if ts.hour == 18 and ts.minute == 45:
            eq_curve.append((ts, total_eq, dd*100))
    
    # Close all at end
    for sym, pos in list(positions.items()):
        if pos['real_sym'] in data and data[pos['real_sym']].index[-1] in data[pos['real_sym']].index:
            last_bar = data[pos['real_sym']].iloc[-1]
            dm = 1 if pos['dir'] == 'L' else -1
            pnl_pct = dm * (last_bar['close'] - pos['entry']) / pos['entry']
            pnl_rub = pnl_pct * pos['go'] * pos['contracts']
            cash += pnl_rub
            all_trades.append({
                'sym': pos['real_sym'], 'dir': pos['dir'],
                'pnl_rub': pnl_rub, 'reason': 'end_of_data',
            })
    
    # Stats
    total_r = (cash - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100
    wins = sum(1 for t in all_trades if t['pnl_rub'] > 0)
    total_t = len(all_trades)
    wr = wins / total_t * 100 if total_t > 0 else 0
    
    days = max((all_ts[-1] - all_ts[0]).days, 1) if all_ts else 365
    years = days / 365.25
    ann_r = (cash / INITIAL_CAPITAL) ** (1 / max(years, 0.1)) - 1
    
    calmar = (ann_r * 100) / (max_dd * 100) if max_dd > 0 else 0
    
    print(f"\n{'='*60}")
    print(f"ФИНАЛЬНЫЙ ТРИЗ-ПОРТФЕЛЬ 5m")
    print(f"{'='*60}")
    print(f"Начальный капитал: {INITIAL_CAPITAL:,.0f} ₽")
    print(f"Конечный капитал:  {cash:,.0f} ₽")
    print(f"Доходность:        {total_r:+.1f}%")
    print(f"Годовая:           {ann_r*100:+.1f}%")
    print(f"Max DD:            {max_dd*100:.1f}%")
    print(f"Calmar:            {calmar:.2f}")
    print(f"Win Rate:          {wr:.1f}% ({wins}/{total_t})")
    print(f"Период:            {all_ts[0]} - {all_ts[-1]} ({days}д)")
    print(f"Circuit breaker:   {'✅ Сработал' if circuit_breaker else '❌ Не сработал'}")
    
    # Per-symbol stats
    sym_stats = defaultdict(lambda: {'pnl':0, 'w':0, 'l':0, 'n':0})
    for t in all_trades:
        s = t.get('sym','?')
        sym_stats[s]['pnl'] += t['pnl_rub']
        sym_stats[s]['n'] += 1
        if t['pnl_rub'] > 0: sym_stats[s]['w'] += 1
        else: sym_stats[s]['l'] += 1
    
    print(f"\nПо тикерам:")
    for s, st in sorted(sym_stats.items(), key=lambda x: x[1]['pnl'], reverse=True):
        wr_s = st['w']/st['n']*100 if st['n']>0 else 0
        print(f"  {s}: {st['pnl']:+,.0f} ₽ WR={wr_s:.0f}% ({st['n']} тр) L/S={'L' if st['pnl']>0 else 'S'}")
    
    return {
        'capital': cash,
        'return_pct': total_r,
        'annual_return': ann_r * 100,
        'max_dd_pct': max_dd * 100,
        'calmar': calmar,
        'wr': wr,
        'n_trades': total_t,
        'circuit_breaker': circuit_breaker,
        'trades': all_trades,
        'equity_curve': [(t.strftime('%Y-%m-%d'), round(e,2), round(d,2)) for t,e,d in eq_curve],
        'sym_stats': {s: st for s, st in sym_stats.items()},
    }


if __name__ == '__main__':
    print("=== Phase 5.3: Финальный ТРИЗ-портфель + Collar ===")
    
    all_symbols = set()
    for lst in [PORTFOLIO['core'], PORTFOLIO['hedge']]:
        all_symbols.update(c[0] for c in lst)
    all_symbols.add(COLLAR_SYMBOL)
    print(f"Тикеры: {sorted(all_symbols)}")
    
    ch = clickhouse_connect.get_client(host='127.0.0.1', port=8123)
    
    print("Загрузка данных...")
    data = load_data(ch, list(all_symbols))
    print(f"Загружено {len(data)}/{len(all_symbols)} тикеров")
    
    print("Загрузка ГО...")
    go_map = load_go(ch, list(all_symbols))
    for s, g in go_map.items():
        print(f"  {s}: GO={g}")
    
    result = run_triz_portfolio(data, go_map)
    
    os.makedirs('reports/phase5_triz', exist_ok=True)
    with open('reports/phase5_triz/final_result.json', 'w') as f:
        json.dump({
            'config': 'core+hedge+collar',
            'result': {k: v for k, v in result.items() if k != 'trades' and k != 'equity_curve'},
        }, f, indent=2, ensure_ascii=False)
    
    print(f"\nРезультат: reports/phase5_triz/final_result.json")
