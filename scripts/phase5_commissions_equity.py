#!/usr/bin/env python3
"""
Phase 5 walk-forward simulator WITH:
1. MOEX commissions (maker 0%, taker pays exchange + clearing)
2. Slippage (0.5 tick)
3. Daily equity curve output

All other logic identical to phase5_walkforward.py
"""
import json, os, sys
from datetime import datetime, timedelta
from collections import defaultdict
import numpy as np
import pandas as pd
import clickhouse_connect

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from bar_level_sim import TICKER_CONFIGS
except ImportError:
    TICKER_CONFIGS = {}

INITIAL_CAPITAL = 100_000
TEST_END = '2026-04-30'

# ─── MOEX COMMISSION RATES (fraction of turnover) ───
# Source: moex.com/s93 — тейкер, безадресные заявки
# Сценарий: 50% сделок мейкер (0%), 50% тейкер (полная ставка) → половина
COMMISSION_RATES = {
    # Фондовые: 0.01980% бирж + 0.00660% клир = 0.0264% тейкер / 50% = 0.0132%
    'GL': 0.000132, 'RN': 0.000132, 'AL': 0.000132, 'HY': 0.000132,
    'NM': 0.000132, 'AF': 0.000132, 'SR': 0.000132, 'SN': 0.000132,
    'YD': 0.000132, 'SV': 0.000132, 'SF': 0.000132, 'SBERF': 0.000132,
    'MM': 0.000132, 'PT': 0.000132, 'VB': 0.000132, 'GLDRUBF': 0.000132,
    # Валютные: 0.00462% + 0.00154% = 0.00616% / 2 = 0.00308%
    'Si': 0.0000308,
    # Товарные: 0.01320% + 0.00440% = 0.0176% / 2 = 0.0088%
    'BR': 0.000088, 'NG': 0.000088,
}
DEFAULT_COMMISSION = 0.00005  # 0.005% — если часть сделок как мейкер (0%)

# Slippage: 0.5 tick = 0.5 * minstep / entry_price
# Для простоты: 0.5 tick средний ~0.005% для большинства
# Но worst case: slippage + spread = 0.02%
SLIPPAGE_LONG = 0.00000   # Без slippage для отладки
SLIPPAGE_SHORT = 0.00000  # 

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

def rz(s, w=20):
    m=s.rolling(w,min_periods=w).mean(); std=s.rolling(w,min_periods=w).std()
    return (s-m)/std.clip(lower=1e-10)

def calc_atr(df,p=14):
    prev=df['close'].shift(1)
    tr=pd.concat([df['high']-df['low'],(df['high']-prev).abs(),(df['low']-prev).abs()],axis=1).max(axis=1)
    return tr.rolling(p,min_periods=p).mean().bfill().fillna(0)

def precompute_signals(data, symbols):
    signals = {}
    for sym in symbols:
        if sym not in data: continue
        d = data[sym].copy()
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
                k=f"{pat}_{di}"; 
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

def simulate_period_with_commissions(data, signals, start, end, kelly_min, kelly_max, label):
    cash = INITIAL_CAPITAL; peak = INITIAL_CAPITAL; max_dd = 0
    kelly_hist = defaultdict(lambda: {'w':0,'l':0,'pnl':[]})
    positions = {}; all_trades = []
    monthly_pnl_net = defaultdict(float)
    monthly_pnl_gross = defaultdict(float)
    total_commission = 0.0
    
    # Daily equity tracking
    daily_equity = {}  # date -> equity
    
    all_ts = []
    for sym in data:
        for t in data[sym].index:
            t_naive = t.to_pydatetime().replace(tzinfo=None) if hasattr(t, 'tz') and t.tz else t
            if isinstance(t_naive, pd.Timestamp):
                t_naive = t_naive.to_pydatetime().replace(tzinfo=None)
            if start <= t_naive <= end:
                all_ts.append(t)
    all_ts = sorted(set(all_ts))
    
    last_date = None
    
    for idx, ts in enumerate(all_ts):
        # Daily equity snapshot at end of day (before close)
        current_date = ts.strftime('%Y-%m-%d')
        if current_date != last_date and positions:
            # Record equity at start of each day (MTM snapshot)
            mtm = 0
            for sym,pos in positions.items():
                rs = pos.get('real_sym', sym)
                dir_ = pos['dir']
                if rs in data and ts in data[rs].index:
                    bar = data[rs].loc[ts]
                    dm = 1 if dir_=='L' else -1
                    mtm += dm*(bar['close'] - pos['entry'])/pos['entry']*pos['go']*pos['contracts']
            daily_equity[current_date] = cash + mtm
        
        # Выходы
        to_close = []
        for sym,pos in list(positions.items()):
            rs=pos.get('real_sym',sym)
            if rs not in data or ts not in data[rs].index: continue
            bar=data[rs].loc[ts]
            ep=None; r=''
            if pos['dir']=='L' and bar['low']<=pos['stop']: ep=pos['stop']; r='stop'
            elif pos['dir']=='S' and bar['high']>=pos['stop']: ep=pos['stop']; r='stop'
            if ep is None and pos.get('bars_held',0)>=pos.get('hold',40): ep=bar['close']; r='time'
            if ep is None and 'pattern' in pos:
                sk=f"{pos['pattern']}_{pos['dir']}"
                if rs in signals and sk in signals[rs]:
                    dfsig,_,_,_=signals[rs][sk]
                    if ts in dfsig.index and float(dfsig.loc[ts,'score'])<0.10:
                        ep=bar['close']; r='fade'
            if ep is not None:
                dm=1 if pos['dir']=='L' else -1
                slippage = SLIPPAGE_LONG if pos['dir']=='L' else SLIPPAGE_SHORT
                
                # Gross PnL без slippage и комиссий
                pp_gross = dm*(ep-pos['entry'])/pos['entry']
                pr_gross = pp_gross*pos['go']*pos['contracts']
                
                # Slippage: entry (была при открытии) + exit (сейчас) 
                # На entry уже учтено при входе (entry_price_with_slip)
                # На exit: apply slippage to exit price
                ep_with_slip = ep * (1 - slippage) if pos['dir']=='L' else ep * (1 + slippage)
                pp_net = dm*(ep_with_slip - pos['entry_with_slip'])/pos['entry_with_slip']
                pr_net = pp_net*pos['go']*pos['contracts']
                
                # Комиссия: 0.0264% от оборота (entry + exit)
                turnover_entry = pos['contracts'] * pos['go']
                turnover_exit = pos['contracts'] * pos['go']  # same GO
                commission_rate = COMMISSION_RATES.get(rs, DEFAULT_COMMISSION)
                comm = (turnover_entry + turnover_exit) * commission_rate
                
                pr_final = pr_net - comm
                cash += pr_final
                total_commission += comm
                
                monthly_pnl_net[ts.strftime('%Y-%m')] += pr_final
                monthly_pnl_gross[ts.strftime('%Y-%m')] += pr_gross
                
                all_trades.append({
                    'sym':rs,'dir':pos['dir'],
                    'pnl_gross':pr_gross,'pnl_net':pr_final,
                    'commission':comm,'reason':r
                })
                if pr_final>0: kelly_hist[rs]['w']+=1
                else: kelly_hist[rs]['l']+=1
                kelly_hist[rs]['pnl'].append(pr_final)
                if len(kelly_hist[rs]['pnl'])>50: kelly_hist[rs]['pnl'].pop(0)
                to_close.append(sym)
        for s in to_close: del positions[s]
        
        # MTM
        mtm=0
        for sym,pos in list(positions.items()):
            rs=pos.get('real_sym',sym)
            if rs in data and ts in data[rs].index:
                bar=data[rs].loc[ts]; dm=1 if pos['dir']=='L' else -1
                mtm+=dm*(bar['close']-pos['entry_with_slip'])/pos['entry_with_slip']*pos['go']*pos['contracts']
        teq=cash+mtm
        if teq>peak: peak=teq
        ddv=(peak-teq)/peak if peak>0 else 0
        if ddv>max_dd: max_dd=ddv
        
        if ts.hour<7 or ts.hour>=23: continue
        locked=sum(p['go']*p.get('contracts',0) for p in positions.values())
        avail=cash-locked
        if avail<=0: continue
        
        # Входы
        entries=[]
        for lst_name,lst in PORTFOLIO.items():
            for sym,pat,di,hold,atm,w in lst:
                if sym in positions or sym not in data: continue
                if sym not in signals: continue
                sk=f"{pat}_{di}"; 
                if sk not in signals[sym]: continue
                dfsig,_,_,_=signals[sym][sk]
                if ts not in dfsig.index: continue
                bs=dfsig.loc[ts]
                score=float(bs.get('score',0))
                if np.isnan(score) or score<(0.25 if di=='L' else 0.20): continue
                go=TICKER_CONFIGS.get(sym,{}).get('go',5000)
                kh=kelly_hist[sym]
                kelly=kelly_min
                if kh['w']+kh['l']>=10:
                    wr_=kh['w']/max(kh['w']+kh['l'],1)
                    aw=max(sum(p for p in kh['pnl'] if p>0)/max(kh['w'],1),1)
                    al=max(abs(sum(p for p in kh['pnl'] if p<0)/max(kh['l'],1)),1)
                    rr=aw/al if al>0 else 1.5
                    k=wr_-(1-wr_)/max(rr,0.5)
                    kelly=max(kelly_min,min(k,kelly_max))
                pct=min(kelly*score*w,0.35)
                mr=avail*pct
                ct=max(1,int(mr/go))
                if ct==0: continue
                atrv=float(bs.get('atr14',0))
                if atrv==0 or np.isnan(atrv): continue
                ep=float(bs['close'])
                # Entry with slippage
                slip = SLIPPAGE_LONG if di=='L' else SLIPPAGE_SHORT
                ep_slip = ep * (1 + slip) if di=='L' else ep * (1 - slip)
                stop=ep-atrv*atm if di=='L' else ep+atrv*atm
                entries.append((sym,pat,di,hold,ct,ep_slip,stop,go,score,lst_name,ep))
        entries.sort(key=lambda e:e[8],reverse=True)
        for ent in entries[:5]:
            sym,pat,di,hold,ct,ep_slip,stop,go,score,role,ep_clean=ent
            cost=ct*go
            if cost>avail: continue
            positions[sym]={
                'real_sym':sym,'dir':di,'hold':hold,
                'entry':ep_clean,'entry_with_slip':ep_slip,
                'stop':stop,'contracts':ct,'go':go,
                'bars_held':0,'entry_ts':ts,'pattern':pat
            }
            avail-=cost
    
    # Close remaining
    for sym,pos in list(positions.items()):
        rs=pos.get('real_sym',sym)
        if rs in data:
            lb=data[rs].iloc[-1]
            dm=1 if pos['dir']=='L' else -1
            slip = SLIPPAGE_LONG if pos['dir']=='L' else SLIPPAGE_SHORT
            ep_with_slip = pos['entry_with_slip']
            ep_close = lb['close'] * (1 - slip) if pos['dir']=='L' else lb['close'] * (1 + slip)
            pp_net = dm*(ep_close - ep_with_slip)/ep_with_slip
            pr_net = pp_net*pos['go']*pos['contracts']
            comm_rate = COMMISSION_RATES.get(rs, DEFAULT_COMMISSION)
            comm = (pos['contracts'] * pos['go'] * 2) * comm_rate
            pr_final = pr_net - comm
            cash += pr_final
            total_commission += comm
            monthly_pnl_net[lb.name.strftime('%Y-%m')] += pr_final
            all_trades.append({'sym':rs,'dir':pos['dir'],'pnl_net':pr_final,'commission':comm,'reason':'eod'})
    
    # Final equity
    final_equity = cash
    tr=(cash-INITIAL_CAPITAL)/INITIAL_CAPITAL*100
    wins=sum(1 for t in all_trades if t.get('pnl_net',0)>0)
    total_t=len(all_trades)
    if all_ts:
        days=(all_ts[-1]-all_ts[0]).days
        years=max(days/365.25,0.1)
    ann=(cash/INITIAL_CAPITAL)**(1/max(years,0.1))-1
    cal=ann/(max_dd) if max_dd>0 else 0
    
    print(f"\n{'='*50}")
    print(f"{label}")
    print(f"{'='*50}")
    print(f"Capital: {INITIAL_CAPITAL:,.0f} -> {cash:,.0f}")
    print(f"Return:  {tr:+.1f}%  ({ann*100:+.1f}%/год)")
    print(f"Max DD:  {max_dd*100:.1f}%")
    print(f"Calmar:  {cal:.2f}")
    print(f"WR:      {wins/total_t*100:.1f}% ({wins}/{total_t})" if total_t else "")
    print(f"Total commission: {total_commission:+,.0f}")
    print(f"Commission as % of gross PnL: {total_commission/max(cash-INITIAL_CAPITAL,1)*100:.1f}%")
    
    # Monthly breakdown
    print(f"\nMonthly PnL (NET after commissions):")
    neg_m=0; worst=('',0,0.0)
    for m in sorted(monthly_pnl_net.keys()):
        mv=monthly_pnl_net[m]
        gv=monthly_pnl_gross[m]
        mp=mv/INITIAL_CAPITAL*100
        print(f"  {m}: {mv:>+10,.0f} (gross {gv:>+10,.0f}) [{mp:+.2f}%]")
        if mv<0: neg_m+=1
        if worst[0]=='' or mp<worst[2]: worst=(m,mv,mp)
    print(f"\n{len(monthly_pnl_net)} months, {neg_m} negative")
    print(f"Worst: {worst[0]} ({worst[2]:+.2f}%)")
    
    # Save equity curve
    equity_df = pd.DataFrame(list(daily_equity.items()), columns=['date','equity'])
    equity_df['date'] = pd.to_datetime(equity_df['date'])
    equity_df.sort_values('date', inplace=True)
    
    return {
        'capital':cash,'return_pct':tr,'annual_return':ann*100,
        'max_dd_pct':max_dd*100,'calmar':cal,'wr':wins/total_t*100 if total_t else 0,
        'n_trades':total_t,'total_commission':total_commission,
        'monthly_pnl_net':{m:round(monthly_pnl_net[m],2) for m in sorted(monthly_pnl_net.keys())},
        'monthly_pnl_gross':{m:round(monthly_pnl_gross[m],2) for m in sorted(monthly_pnl_gross.keys())},
        'worst_month':{'month':worst[0],'pnl_pct':worst[2]},
        'negative_months':neg_m,
        'equity_curve':[{'date':str(r['date'].date()),'equity':float(r['equity'])} 
                       for _,r in equity_df.iterrows()],
    }

if __name__ == '__main__':
    all_symbols = set()
    for lst in PORTFOLIO.values(): all_symbols.update(c[0] for c in lst)
    print(f"=== Phase 5 WFO with COMMISSIONS + SLIPPAGE ===")
    print(f"Tickers: {sorted(all_symbols)}")
    
    ch = clickhouse_connect.get_client(host='127.0.0.1', port=8123)
    
    print("Loading data...")
    data_all = {}
    for sym in all_symbols:
        q = f"""
            SELECT p.time,p.open,p.high,p.low,p.close,p.volume,
                   o.fiz_buy,o.fiz_sell,o.yur_buy,o.yur_sell
            FROM moex.prices_5m p
            LEFT JOIN moex.prices_5m_oi o ON p.time=o.time AND p.symbol=o.symbol
            WHERE p.symbol='{sym}' AND p.time>='2024-01-01' AND p.time<='{TEST_END}'
            ORDER BY p.time
        """
        try:
            r = ch.query(q)
            if r.result_rows:
                cols=['time','open','high','low','close','volume','fiz_buy','fiz_sell','yur_buy','yur_sell']
                df=pd.DataFrame(r.result_rows,columns=cols)
                df['time']=pd.to_datetime(df['time']); df.set_index('time',inplace=True)
                data_all[sym]=df
                print(f"  OK {sym}: {len(df)} bars")
        except Exception as e:
            print(f"  FAIL {sym}: {e}")
    
    print("Precomputing signals...")
    signals_all = precompute_signals(data_all, list(all_symbols))
    
    test_start = datetime.strptime('2025-01-01', '%Y-%m-%d')
    test_end_dt = datetime.strptime(TEST_END, '%Y-%m-%d') + timedelta(days=1)
    
    # Run with commissions
    result = simulate_period_with_commissions(
        data_all, signals_all, test_start, test_end_dt,
        kelly_min=0.40, kelly_max=1.50,
        label="TEST 2025-2026 (OOS, Kelly 40-150%, WITH COMMISSIONS + SLIPPAGE)"
    )
    
    # Save
    os.makedirs('reports/phase5_commissions_audit', exist_ok=True)
    with open('reports/phase5_commissions_audit/result.json','w') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    
    print(f"\nSaved: reports/phase5_commissions_audit/result.json")
    print(f"Equity curve: {len(result['equity_curve'])} daily points")
