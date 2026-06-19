#!/usr/bin/env python3
"""Phase 5 — сравнение: рыночные ордера vs лимитные + market fallback vs лимитные + fallback + Alor комиссии."""

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

# Трейтмент ликвидности — вероятности заполнения лимитного ордера
LIQUID_TICKERS = ['GL', 'Si', 'SR', 'RN', 'BR', 'AL']  # 90%
MID_TICKERS = ['AF', 'YD', 'SN', 'SV', 'NG']            # 70%
THIN_TICKERS = ['NM', 'HY', 'SF']                        # 50%

FILL_PROB = {}
for t in LIQUID_TICKERS: FILL_PROB[t] = 0.90
for t in MID_TICKERS:    FILL_PROB[t] = 0.70
for t in THIN_TICKERS:   FILL_PROB[t] = 0.50

# Комиссия Alor: 0.5₽/контракт для тейкер-части
ALOR_COMMISSION_PER_CONTRACT = 0.50

# Score thresholds
SCORE_THRESHOLD_LONG = 0.25
SCORE_THRESHOLD_SHORT = 0.20

def rz(s, w=20):
    m=s.rolling(w,min_periods=w).mean(); std=s.rolling(w,min_periods=w).std()
    return (s-m)/std.clip(lower=1e-10)

def calc_atr(df,p=14):
    prev=df['close'].shift(1)
    tr=pd.concat([df['high']-df['low'],(df['high']-prev).abs(),(df['low']-prev).abs()],axis=1).max(axis=1)
    return tr.rolling(p,min_periods=p).mean().bfill().fillna(0)

def precompute_signals_5m(data_5m, symbols):
    """Same as phase5_walkforward.py precompute_signals."""
    signals = {}
    for sym in symbols:
        if sym not in data_5m: continue
        d = data_5m[sym].copy()
        d['volume']=d['volume'].astype(float)
        d['vma20']=d['volume'].rolling(20).mean().fillna(d['volume'])
        d['vr']=d['volume']/d['vma20'].clip(lower=1); d['vz']=rz(d['volume'],20)
        has_oi='fiz_buy' in d.columns
        if has_oi:
            d['fiz_net']=d['fiz_buy'].fillna(0)-d['fiz_sell'].fillna(0)
            d['yur_net']=d['yur_buy'].fillna(0)-d['yur_sell'].fillna(0)
            d['fz']=rz(d['fiz_net'],20); d['yz']=rz(d['yur_net'],20)
            d['oi_r']=(d['yur_buy']+d['yur_sell']).fillna(0)/(d['fiz_buy']+d['fiz_sell']+1).fillna(0)
            d['oima']=d['oi_r'].rolling(20).mean()
        d['atr14']=calc_atr(d); d['atr_pct']=d['atr14']/d['close'].clip(lower=1)*100
        
        sym_sigs={}
        seen=set()
        for lst in PORTFOLIO.values():
            for c in lst:
                sn,pat,di,hold,atm=c[0],c[1],c[2],c[3],c[4]
                if sn!=sym: continue
                k=f"{pat}_{di}"
                if k in seen: continue
                seen.add(k)
                dm=1 if di=='L' else -1
                if pat in ('vod','vou'):
                    vs=np.clip((d['vr']-1.5)/3.0,0,1)
                    if has_oi:
                        os_=np.clip((d['oima']-d['oi_r'])/d['oima'].clip(lower=0.1),0,1) if pat=='vod' else np.clip((d['oi_r']-d['oima'])/d['oima'].clip(lower=0.1),0,1)
                    else: os_=0.5
                    raw=vs*0.6+os_*0.4
                elif pat=='sm':
                    if has_oi: raw=np.clip(abs(d['yz'])/3.0,0,1)*0.7+np.clip(abs(d['fz'])/3.0,0,1)*0.3
                    else: raw=np.clip((d['vr']-1.5)/3.0,0,1)
                elif pat=='vyf':
                    vs=np.clip((d['vr']-2.0)/4.0,0,1)
                    if has_oi: ys=np.clip(d['yur_net'].fillna(0)/max(d['yur_net'].std(),1)*dm,0,1)
                    else: ys=np.clip((d['close']-d['close'].shift(1))/d['close'].shift(1).clip(lower=1)*50,0,1)
                    raw=vs*0.5+ys*0.5
                else: raw=np.clip((d['vr']-2.5)/5.0,0,1)
                af=np.clip(1-(d['atr_pct']-0.3)/3.0,0,1)
                score=np.clip(raw*af*np.clip(1+d['vz']/5,0.5,1.5),0,1)
                dout=d.copy(); dout['score']=score
                sym_sigs[k]=(dout,di,hold,atm)
        signals[sym]=sym_sigs
    return signals


def simulate(scenario, data_5m, signals_all, all_ts, rng=None):
    """
    scenario: 'market' | 'limit_fallback' | 'limit_fallback_commission'
    all_ts: sorted list of timestamps for test period
    """
    if rng is None:
        rng = np.random.default_rng(42)

    slip = 0.0001  # 0.01% slippage для рыночных ордеров
    use_limit = (scenario != 'market')
    use_commission = (scenario == 'limit_fallback_commission')

    cash = float(INITIAL_CAPITAL)
    peak = float(INITIAL_CAPITAL)
    max_dd = 0.0
    kelly_hist = defaultdict(lambda: {'w':0,'l':0,'pnl':[]})
    positions = {}  # sym -> pos
    pending = {}    # sym -> {ent info} for limit orders waiting to fill
    all_trades = []
    total_commission = 0.0
    
    t0 = time.time()
    
    for idx, ts in enumerate(all_ts):
        if idx % 10000 == 0:
            elapsed = time.time() - t0
            print(f"  bar {idx}/{len(all_ts)} ({elapsed:.0f}s)", flush=True)
        
        # ─── Step 1: Process pending limit orders (entry at ts from previous bar's limit) ───
        # Pending orders were set at bar t-1 to execute at close[t-1].
        # At bar t, we check if they filled (probabilistically) and if not, fallback to market at close[t].
        pending_filled = []
        pending_fallback = []
        
        for sym, ent in list(pending.items()):
            rs = ent['real_sym']
            if rs not in data_5m or ts not in data_5m[rs].index:
                # No data for this bar — carry over
                continue
            
            bar = data_5m[rs].loc[ts]
            di = ent['dir']
            
            # Check if limit order was filled (probabilistic, based on liquidity)
            fill_p = FILL_PROB.get(sym, 0.5)
            filled = rng.random() < fill_p
            
            if filled:
                # Limit fill at close[t-1] = ent['limit_price']
                ep = ent['limit_price']
                pending_filled.append((sym, ent, ep, 'limit'))
            else:
                # Fallback: market order at close[t] with slippage
                ep = float(bar['close'])
                if di == 'L':
                    ep = ep * (1 + slip)
                else:
                    ep = ep * (1 - slip)
                
                # Check if signal is still alive at bar t
                sk = f"{ent['pattern']}_{di}"
                signal_alive = True
                if rs in signals_all and sk in signals_all[rs]:
                    dfsig,_,_,_ = signals_all[rs][sk]
                    if ts in dfsig.index:
                        score = float(dfsig.loc[ts, 'score'])
                        thresh = SCORE_THRESHOLD_LONG if di == 'L' else SCORE_THRESHOLD_SHORT
                        if np.isnan(score) or score < thresh:
                            signal_alive = False
                
                if signal_alive:
                    pending_fallback.append((sym, ent, ep))
                # else: signal died, skip entirely
        
        # Clear pending
        pending.clear()
        
        # Process filled limit orders
        for sym, ent, ep, reason in pending_filled:
            go = ent['go']
            ct = ent['contracts']
            cost = ct * go
            
            # Re-check available capital
            locked = sum(p['go']*p.get('contracts',0) for p in positions.values())
            avail = cash - locked
            if cost > avail:
                continue
            
            stop = ent['stop']
            di = ent['dir']
            
            positions[sym] = {
                'real_sym': sym, 'dir': di, 'hold': ent['hold'],
                'entry': ep, 'stop': stop,
                'contracts': ct, 'go': go, 'bars_held': 0,
                'entry_ts': ts, 'pattern': ent['pattern'],
                'entry_method': 'limit'
            }
        
        # Process market fallback orders
        for sym, ent, ep in pending_fallback:
            go = ent['go']
            ct = ent['contracts']
            cost = ct * go
            di = ent['dir']
            
            locked = sum(p['go']*p.get('contracts',0) for p in positions.values())
            avail = cash - locked
            if cost > avail:
                continue
            
            stop = ent['stop']
            
            # Apply commission for taker (market) part if enabled
            if use_commission:
                comm = ct * ALOR_COMMISSION_PER_CONTRACT
                total_commission += comm
                # Commission is deducted from position entry
            
            positions[sym] = {
                'real_sym': sym, 'dir': di, 'hold': ent['hold'],
                'entry': ep, 'stop': stop,
                'contracts': ct, 'go': go, 'bars_held': 0,
                'entry_ts': ts, 'pattern': ent['pattern'],
                'entry_method': 'market_fallback',
                'commission_paid': comm if use_commission else 0.0
            }
        
        # ─── Step 2: Exits ───
        to_close = []
        for sym, pos in list(positions.items()):
            rs = pos.get('real_sym', sym)
            if rs not in data_5m or ts not in data_5m[rs].index: continue
            bar = data_5m[rs].loc[ts]
            ep = None; reason = ''
            
            # Stop-loss: ATR × atm — рыночный, slippage 0.01%
            if pos['dir']=='L' and bar['low']<=pos['stop']:
                ep = pos['stop'] * (1 - slip)
                reason='stop'
            elif pos['dir']=='S' and bar['high']>=pos['stop']:
                ep = pos['stop'] * (1 + slip)
                reason='stop'
            
            # Time-stop: hold_bars — по close, slippage 0.01%
            if ep is None and pos.get('bars_held',0) >= pos.get('hold', 40):
                ep = float(bar['close'])
                if slip > 0:
                    ep = ep * (1 - slip) if pos['dir']=='L' else ep * (1 + slip)
                reason='time'
            # Score fade exit = УБРАТЬ (как уже решили)
            
            if ep is not None:
                dm = 1 if pos['dir']=='L' else -1
                pp = dm*(ep-pos['entry'])/pos['entry']
                pr = pp*pos['go']*pos['contracts']
                
                # Deduct commission on exit if applicable (taker on exit too)
                if use_commission and 'commission_paid' in pos:
                    # Exit is also a taker order in fallback case
                    exit_comm = pos['contracts'] * ALOR_COMMISSION_PER_CONTRACT
                    pr -= exit_comm
                    total_commission += exit_comm
                elif use_commission:
                    # Market entry (scenario 1) — both entry and exit are taker
                    entry_comm = pos['contracts'] * ALOR_COMMISSION_PER_CONTRACT
                    exit_comm = pos['contracts'] * ALOR_COMMISSION_PER_CONTRACT
                    pr -= (entry_comm + exit_comm)
                    total_commission += (entry_comm + exit_comm)
                
                cash += pr
                all_trades.append({'sym':rs,'dir':pos['dir'],'pnl_rub':pr,'reason':reason,
                                   'entry_method': pos.get('entry_method', 'direct_market')})
                if pr>0: kelly_hist[rs]['w']+=1
                else: kelly_hist[rs]['l']+=1
                kelly_hist[rs]['pnl'].append(pr)
                if len(kelly_hist[rs]['pnl'])>50: kelly_hist[rs]['pnl'].pop(0)
                to_close.append(sym)
        for s in to_close: del positions[s]
        to_close.clear()
        
        # ─── Step 3: MTM ───
        mtm=0
        for sym, pos in positions.items():
            rs = pos.get('real_sym', sym)
            if rs in data_5m and ts in data_5m[rs].index:
                bar = data_5m[rs].loc[ts]
                dm = 1 if pos['dir']=='L' else -1
                mtm += dm*(float(bar['close'])-pos['entry'])/pos['entry']*pos['go']*pos['contracts']
        teq = cash + mtm
        if teq > peak: peak = teq
        ddv = (peak - teq) / peak if peak > 0 else 0
        if ddv > max_dd: max_dd = ddv
        
        # ─── Step 4: Entries (skip if outside market hours) ───
        if ts.hour < 7 or ts.hour >= 23: continue
        locked = sum(p['go']*p.get('contracts',0) for p in positions.values())
        avail = cash - locked
        if avail <= 0: continue
        
        entries = []
        for lst_name, lst in PORTFOLIO.items():
            for sym, pat, di, hold, atm, w in lst:
                if sym in positions or sym not in data_5m: continue
                if sym not in signals_all: continue
                sk = f"{pat}_{di}"
                if sk not in signals_all[sym]: continue
                dfsig,_,_,_ = signals_all[sym][sk]
                if ts not in dfsig.index: continue
                bs = dfsig.loc[ts]
                score = float(bs.get('score', 0))
                if np.isnan(score): continue
                thresh = SCORE_THRESHOLD_LONG if di=='L' else SCORE_THRESHOLD_SHORT
                if score < thresh: continue
                
                go = TICKER_CONFIGS.get(sym, {}).get('go', 5000)
                kh = kelly_hist[sym]
                kelly = 0.40
                if kh['w']+kh['l'] >= 10:
                    wr_ = kh['w']/max(kh['w']+kh['l'],1)
                    aw = max(sum(p for p in kh['pnl'] if p>0)/max(kh['w'],1),1)
                    al = max(abs(sum(p for p in kh['pnl'] if p<0)/max(kh['l'],1)),1)
                    rr = aw/al if al>0 else 1.5
                    k_ = wr_ - (1-wr_)/max(rr, 0.5)
                    kelly = max(0.40, min(k_, 1.50))
                pct = min(kelly*score*w, 0.35)
                mr = avail * pct
                ct = max(1, int(mr/go))
                if ct == 0: continue
                atrv = float(bs.get('atr14', 0))
                if atrv == 0 or np.isnan(atrv): continue
                close_p = float(bs['close'])
                
                if use_limit:
                    # Limit order at close[t]
                    limit_price = close_p
                    stop_price = limit_price - atrv*atm if di=='L' else limit_price + atrv*atm
                    
                    # For limit orders, we just record the pending intent
                    # No capital is locked yet; we'll check on the next bar
                    if sym not in pending:
                        pending[sym] = {
                            'real_sym': sym, 'pattern': pat, 'dir': di,
                            'hold': hold, 'contracts': ct, 'go': go,
                            'stop': stop_price, 'limit_price': limit_price,
                            'entry_ts': ts,
                        }
                else:
                    # Direct market entry with slippage at close[t]
                    ep = close_p
                    if di == 'L':
                        ep = ep * (1 + slip)
                    else:
                        ep = ep * (1 - slip)
                    cost = ct * go
                    if cost > avail: continue
                    stop = ep - atrv*atm if di=='L' else ep + atrv*atm
                    entries.append((sym, pat, di, hold, ct, ep, stop, go, score, lst_name))
        
        if not use_limit:
            # Process direct market entries
            entries.sort(key=lambda e: e[8], reverse=True)
            for ent in entries[:5]:
                sym, pat, di, hold, ct, ep, stop, go, score, role = ent
                cost = ct * go
                if cost > avail: continue
                positions[sym] = {
                    'real_sym':sym, 'dir':di, 'hold':hold, 'entry':ep, 'stop':stop,
                    'contracts':ct, 'go':go, 'bars_held':0, 'entry_ts':ts,
                    'pattern':pat, 'entry_method': 'direct_market'
                }
                avail -= cost
    
    # ─── Close remaining positions ───
    for sym, pos in list(positions.items()):
        rs = pos.get('real_sym', sym)
        if rs in data_5m:
            lb = data_5m[rs].iloc[-1]
            dm = 1 if pos['dir']=='L' else -1
            ep_close = float(lb['close'])
            if slip > 0:
                ep_close = ep_close * (1 - slip) if pos['dir']=='L' else ep_close * (1 + slip)
            pp = dm*(ep_close-pos['entry'])/pos['entry']
            pr = pp*pos['go']*pos['contracts']
            if use_commission:
                comm = pos['contracts'] * ALOR_COMMISSION_PER_CONTRACT
                pr -= comm
                total_commission += comm
            cash += pr
            all_trades.append({'sym':rs, 'dir':pos['dir'], 'pnl_rub':pr, 'reason':'eod',
                               'entry_method': pos.get('entry_method', 'direct_market')})
    
    # Also close pending limit orders at the end (unfilled)
    for sym, ent in list(pending.items()):
        rs = ent['real_sym']
        # These never got filled, just discard
    
    # ─── Calculate metrics ───
    tr = (cash - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100
    wins = sum(1 for t in all_trades if t.get('pnl_rub',0) > 0)
    total_t = len(all_trades)
    days = max((all_ts[-1]-all_ts[0]).days, 1)
    years = max(days/365.25, 0.1)
    ann = (cash/INITIAL_CAPITAL) ** (1/years) - 1
    cal = ann / max_dd if max_dd > 0 else 0
    sigs_per_day = total_t / days
    
    # Per-symbol stats
    sym_stats = defaultdict(lambda: {'pnl':0,'w':0,'l':0,'n':0})
    for t in all_trades:
        s = t.get('sym','?')
        sym_stats[s]['pnl'] += t.get('pnl_rub',0)
        sym_stats[s]['n'] += 1
        if t.get('pnl_rub',0) > 0:
            sym_stats[s]['w'] += 1
        else:
            sym_stats[s]['l'] += 1
    
    return {
        'capital': cash,
        'return_pct': tr,
        'annual_return': ann * 100,
        'max_dd_pct': max_dd * 100,
        'calmar': cal,
        'wr': wins/total_t*100 if total_t else 0,
        'n_trades': total_t,
        'trades_per_day': round(sigs_per_day, 1),
        'total_commission': round(total_commission, 0),
        'days': days,
        'time_s': round(time.time()-t0, 1),
        'sym_stats': dict(sym_stats),
    }


if __name__ == '__main__':
    all_symbols = sorted(set(c[0] for lst in PORTFOLIO.values() for c in lst))
    
    print("Loading data...", flush=True)
    with open('.tf_sweep_data.pkl', 'rb') as f:
        data_5m = pickle.load(f)
    
    print("Precomputing 5m signals...", flush=True)
    t0_all = time.time()
    signals_all = precompute_signals_5m(data_5m, all_symbols)
    print(f"  Done in {time.time()-t0_all:.0f}s", flush=True)
    
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
    
    # ─── Run all 3 scenarios ───
    rng_seed = 42
    scenarios = [
        ('market', 'Рыночные ордера + 0.01% slippage'),
        ('limit_fallback', 'Лимитные + market fallback'),
        ('limit_fallback_commission', 'Лимитные + market fallback + комиссия Alor'),
    ]
    
    results = []
    for sc_key, sc_label in scenarios:
        print(f"\n{'='*60}", flush=True)
        print(f"СЦЕНАРИЙ: {sc_label}", flush=True)
        print(f"{'='*60}", flush=True)
        
        rng = np.random.default_rng(rng_seed)
        res = simulate(sc_key, data_5m, signals_all, all_ts, rng)
        res['scenario'] = sc_key
        res['scenario_label'] = sc_label
        
        # Print results
        print(f"\n  Capital: {INITIAL_CAPITAL:,.0f} → {res['capital']:,.0f} ₽", flush=True)
        print(f"  Return:  {res['return_pct']:+.1f}%  ({res['annual_return']:+.1f}%/год)", flush=True)
        print(f"  Max DD:  {res['max_dd_pct']:.1f}%", flush=True)
        print(f"  Calmar:  {res['calmar']:.2f}", flush=True)
        print(f"  Trades:  {res['n_trades']}  ({res['trades_per_day']:.1f}/day)", flush=True)
        print(f"  WR:      {res['wr']:.1f}%", flush=True)
        if res['total_commission'] > 0:
            print(f"  Comm:    {res['total_commission']:+,.0f} ₽", flush=True)
        
        # Per-symbol
        for s in sorted(res['sym_stats'].keys(), key=lambda x: res['sym_stats'][x]['pnl'], reverse=True):
            st = res['sym_stats'][s]
            ws = st['w']/st['n']*100 if st['n']>0 else 0
            print(f"    {s}: {st['pnl']:+,.0f} ₽  WR={ws:.0f}% ({st['n']} тр)", flush=True)
        
        results.append(res)
    
    # ─── Comparison table ───
    print(f"\n\n{'='*80}")
    print(f"{'СРАВНЕНИЕ СЦЕНАРИЕВ':^80}")
    print(f"{'='*80}")
    
    header = f"{'Сценарий':40} {'Return':>9} {'Ann%':>8} {'DD':>7} {'Calmar':>8} {'Trades':>7} {'/day':>6} {'WR':>6}"
    print(header)
    print(f"{'─'*80}")
    
    for r in results:
        label = r['scenario_label']
        line = f"{label:40} {r['return_pct']:>+8.1f}% {r['annual_return']:>7.1f}% {r['max_dd_pct']:>6.1f}% {r['calmar']:>8.2f} {r['n_trades']:>7} {r['trades_per_day']:>6.1f} {r['wr']:>5.1f}%"
        print(line)
    
    print(f"{'─'*80}")
    
    # Also per scenario detail
    for r in results:
        print(f"\n{r['scenario_label']} — per symbol:")
        for s in sorted(r['sym_stats'].keys(), key=lambda x: r['sym_stats'][x]['pnl'], reverse=True):
            st = r['sym_stats'][s]
            ws = st['w']/st['n']*100 if st['n']>0 else 0
            print(f"  {s}: {st['pnl']:+,.0f} ₽  WR={ws:.0f}% ({st['n']} тр)")
    
    # ─── Save ───
    os.makedirs('reports/tf_sweep', exist_ok=True)
    out_path = 'reports/tf_sweep/limit_order_results.json'
    
    # Clean sym_stats for JSON
    results_clean = []
    for r in results:
        rc = {k: v for k, v in r.items() if k != 'sym_stats'}
        rc['sym_stats'] = {k: dict(v) for k, v in r['sym_stats'].items()}
        results_clean.append(rc)
    
    with open(out_path, 'w') as f:
        json.dump(results_clean, f, indent=2, ensure_ascii=False)
    print(f"\nСохранено: {out_path}")
    print(f"\nDone in {time.time()-t0_all:.0f}s total")
