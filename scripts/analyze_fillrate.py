#!/usr/bin/env python3
"""Analyze why fill rate is 0.2%. Check RN signals vs portfolio constraints."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd, psycopg2
from datetime import datetime, timedelta, timezone

from trading_bot.new_strategies import detect_oi_divergence_signals
from scripts.bar_level_sim import TICKER_CONFIGS, TICKER_PRIORITY, TICKER_PRIORITY_WEIGHT, MAX_WEIGHT

DB = dict(host='127.0.0.1', port=5432, dbname='moex', user='postgres', password='')

def _zs(vals, w=20):
    out = [0.0]*len(vals)
    for i in range(w, len(vals)):
        chunk = vals[i-w:i]
        mu = sum(chunk)/w
        sd = (sum((x-mu)**2 for x in chunk)/w)**0.5
        out[i] = (vals[i]-mu)/sd if sd>0 else 0
    return out

since = datetime.now(timezone.utc)-timedelta(days=365)

# Get whale tickers and GO
conn = psycopg2.connect(**DB)
cur = conn.cursor()
cur.execute("SELECT symbol, AVG((yur_buy::float+yur_sell::float)/NULLIF(total_oi::float,0)) as ys FROM moex_prices_5m_oi WHERE time>=%s AND total_oi>0 GROUP BY symbol", (since,))
whale = sorted([r[0] for r in cur.fetchall() if r[1] > 0.50 and r[0] in TICKER_CONFIGS])

print("=== Whale Ticker GO Analysis ===")
print(f"{'Ticker':>8} {'Priority':>8} {'Weight':>8} {'GO':>8}")
total_margin_budget = 200000 * 0.20  # 200K * total_margin_limit
print(f"Total margin budget (200K * 0.20): {total_margin_budget}")
print()

total_go = 0
for sym in whale:
    cfg = TICKER_CONFIGS.get(sym, {})
    go = cfg.get('go', 0)
    prio = TICKER_PRIORITY.get(sym, 99)
    wgt = TICKER_PRIORITY_WEIGHT.get(sym, 1.0)
    max_risk = 200000 * 0.10 * (wgt / MAX_WEIGHT)
    max_contracts = int(max_risk // go) if go > 0 else 0
    print(f"{sym:>8} {prio:>8} {wgt:>8.1f} {go:>8}  max_contracts={max_contracts} max_risk={max_risk:.0f}")
    total_go += go

# Now check RN specifically - one RN position takes how much?
rn_go = TICKER_CONFIGS.get('RN', {}).get('go', 0)
print(f"\n=== RN Capacity ===")
print(f"RN GO: {rn_go}")
print(f"With 200K, margin_usage=0.10, weight={TICKER_PRIORITY_WEIGHT.get('RN',1.0)}:")
max_risk_rn = 200000 * 0.10 * (TICKER_PRIORITY_WEIGHT.get('RN', 1.0) / MAX_WEIGHT)
print(f"  max_risk = 200K * 0.10 * {TICKER_PRIORITY_WEIGHT.get('RN',1.0)}/3.0 = {max_risk_rn:.0f}")
contracts = int(max_risk_rn // rn_go) if rn_go > 0 else 0
locked_go = contracts * rn_go
print(f"  contracts = {contracts}, locked_go = {locked_go}")
print(f"  Total margin used by RN alone: {locked_go}")
print(f"  Remaining margin budget: {total_margin_budget - locked_go}")

# What if margin_usage=0.30 and total_margin_limit=0.50?
print(f"\n=== Loose Constraints ===")
total_budget_loose = 200000 * 0.50
print(f"Total margin budget (200K * 0.50): {total_budget_loose}")
max_risk_loose = 200000 * 0.30 * (TICKER_PRIORITY_WEIGHT.get('RN', 1.0) / MAX_WEIGHT)
print(f"RN max_risk (margin=0.30): {max_risk_loose:.0f}")
contracts_loose = int(max_risk_loose // rn_go) if rn_go > 0 else 0
print(f"RN contracts: {contracts_loose}, locked_go: {contracts_loose * rn_go}")
print(f"Can hold ~{total_budget_loose // rn_go} RN positions simultaneously")
# How many concurrent if avg GO = 5000?
avg_go = 5000
print(f"With avg GO={avg_go}: ~{int(total_budget_loose // avg_go)} concurrent positions")

# Now load YD signals for ALL whale tickers and check fill rate
print(f"\n=== YD Signal Fill Rate Analysis ===")
print(f"Whale tickers: {len(whale)}")

# For each ticker, count signals and check why they don't fill
for sym in whale:
    cur = conn.cursor()
    cur.execute("SELECT time,open,high,low,close,volume FROM moex_prices_5m WHERE symbol=%s AND time>=%s ORDER BY time", (sym, since))
    ohlcv = [{'time':r[0],'open':float(r[1]),'high':float(r[2]),'low':float(r[3]),'close':float(r[4]),'volume':float(r[5])} for r in cur]
    cur.close()
    if len(ohlcv)<100: continue
    cur = conn.cursor()
    cur.execute("SELECT time,fiz_buy,fiz_sell,yur_buy,yur_sell,total_oi FROM moex_prices_5m_oi WHERE symbol=%s AND time>=%s ORDER BY time", (sym, since))
    oi = [{'time':r[0],'fiz_buy':float(r[1]),'fiz_sell':float(r[2]),'yur_buy':float(r[3]),'yur_sell':float(r[4]),'total_oi':float(r[5])} for r in cur]
    cur.close()
    om = {r['time'].strftime('%Y-%m-%d %H:%M'):r for r in oi}
    merged=[]
    for r in ohlcv:
        o=om.get(r['time'].strftime('%Y-%m-%d %H:%M'))
        if o is None: continue
        merged.append({**r,'total_oi':o['total_oi'],'fiz_buy':o['fiz_buy'],'fiz_sell':o['fiz_sell'],'yur_buy':o['yur_buy'],'yur_sell':o['yur_sell'],'symbol':sym})
    if len(merged)<100: continue
    # Resample
    df=pd.DataFrame(merged)
    df['time']=pd.to_datetime(df['time'])
    df.set_index('time',inplace=True)
    agg_dict={'open':'first','high':'max','low':'min','close':'last','volume':'sum','total_oi':'last','fiz_buy':'last','fiz_sell':'last','yur_buy':'last','yur_sell':'last'}
    res=df.resample('1h').agg(agg_dict).dropna(subset=['close'])
    rows=[{'time':idx,'open':float(r['open']),'high':float(r['high']),'low':float(r['low']),'close':float(r['close']),'volume':float(r['volume']),'total_oi':float(r['total_oi']),'fiz_buy':float(r['fiz_buy']),'fiz_sell':float(r['fiz_sell']),'yur_buy':float(r['yur_buy']),'yur_sell':float(r['yur_sell']),'symbol':sym} for idx,r in res.iterrows()]
    if len(rows)<50: continue
    
    n=len(rows)
    yur_net=[(r['yur_buy']-r['yur_sell'])/max(r['yur_buy']+r['yur_sell'],1) for r in rows]
    yz=_zs(yur_net,20)
    chg=[0.0]*n
    for i in range(1,n): chg[i]=(rows[i]['close']-rows[i-1]['close'])/rows[i-1]['close']*100
    pz=_zs(chg,20)
    
    n_sigs=0
    total_ret=0
    n_profitable=0
    n_long=0
    n_short=0
    for i in range(25,n-6):
        if yz[i]>1.0 and pz[i]<-1.0:
            direction='LONG'
            n_long+=1
        elif yz[i]<-1.0 and pz[i]>1.0:
            direction='SHORT'
            n_short+=1
        else: continue
        n_sigs+=1
        entry=rows[i+1]['open']
        exit_p=rows[i+6]['close']
        ret=(exit_p-entry)/entry*100 if direction=='LONG' else (entry-exit_p)/entry*100
        total_ret+=ret
        if ret>0: n_profitable+=1
    
    if n_sigs>0:
        wr=n_profitable/n_sigs*100
        avg_ret=total_ret/n_sigs
        go=TICKER_CONFIGS.get(sym,{}).get('go',0)
        print(f"{sym:>8} sigs={n_sigs:>4} L={n_long:>3} S={n_short:>3} WR={wr:.0f}% avg={avg_ret:>+6.2f}% GO={go:>5}")

conn.close()