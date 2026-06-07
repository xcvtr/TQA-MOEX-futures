#!/usr/bin/env python3
"""Complete pattern scan across ALL MOEX tickers - saves to file."""
import psycopg2, sys
from collections import defaultdict
DB = dict(host='10.0.0.64', port=5432, dbname='moex', user='postgres', password='postgres')

def zs(v, w=20):
    r=[0.0]*len(v)
    for i in range(w,len(v)):
        c=v[i-w:i]; m=sum(c)/w;s=(sum((x-m)**2 for x in c)/w)**0.5
        r[i]=(v[i]-m)/s if s>0 else 0.0
    return r

def profile(evs, label):
    if not evs:
        return ""
    n=len(evs)
    if n < 30:
        return ""
    avg_max=sum(e['max_ret'] for e in evs)/n
    avg_min=sum(e['min_ret'] for e in evs)/n
    avg_last=sum(e['last_ret'] for e in evs)/n
    bullish=sum(1 for e in evs if e['skew']=='BULL')/n*100
    bearish=sum(1 for e in evs if e['skew']=='BEAR')/n*100
    up_close=sum(1 for e in evs if e['last_ret']>0)/n*100
    avg_retrace=sum(e['retrace'] for e in evs)/n
    # Asymmetry: upside potential vs downside risk
    asymmetry = (avg_max / abs(avg_min)) if avg_min != 0 else 0
    
    return (f"  {label:25s} n={n:5d} | max={avg_max:+.3f}% min={avg_min:+.3f}% last={avg_last:+.3f}% "
            f"| up_close={up_close:5.1f}% | BULL={bullish:4.1f}% BEAR={bearish:4.1f}% "
            f"| asym={asymmetry:.2f} retrace={avg_retrace:.2f}")

conn = psycopg2.connect(**DB)
cur = conn.cursor()
cur.execute("SELECT DISTINCT symbol FROM moex_prices_5m_oi ORDER BY symbol")
all_symbols = [r[0] for r in cur.fetchall()]
cur.close()

lines = []
lines.append(f"PATTERN SCAN: Volume Surge + FIZ/YUR Divergence — ALL {len(all_symbols)} TICKERS")
lines.append(f"{'='*80}")
lines.append("")

for sym in all_symbols:
    cur = conn.cursor()
    cur.execute("""
        SELECT oi.time, oi.fiz_buy, oi.fiz_sell, oi.yur_buy, oi.yur_sell, p.close, p.volume
        FROM moex_prices_5m_oi oi
        JOIN moex_prices_5m p ON p.symbol=oi.symbol AND p.time=oi.time
        WHERE oi.symbol=%s ORDER BY oi.time
    """, (sym,))
    rows = cur.fetchall()
    cur.close()
    
    if len(rows) < 500:
        continue
    
    vol=[r[6] or 0 for r in rows]
    cl=[r[5] or 0 for r in rows]
    fn=[r[1]-r[2] for r in rows]
    yn=[r[3]-r[4] for r in rows]
    
    vz=zs(vol,20); fz=zs(fn,20); yz=zs(yn,20)
    
    events=[]
    for i in range(21, len(rows)-25):
        if vz[i] < 2.0: continue
        fzi, yzi = fz[i], yz[i]
        if fzi * yzi >= 0: continue
        if abs(fzi) < 0.5 or abs(yzi) < 0.5: continue
        
        future_cl = cl[i+1:i+25]
        if not future_cl or cl[i]==0: continue
        
        rets=[(x-cl[i])/cl[i]*100 for x in future_cl]
        max_r=max(rets); min_r=min(rets)
        last_r=rets[-1]; avg_r=sum(rets)/len(rets)
        half_r=(max_r-min_r)/2
        mid=(max_r+min_r)/2
        skew='BULL' if avg_r > mid else 'BEAR' if avg_r < mid else 'FLAT'
        retrace=abs(last_r-avg_r)/half_r if half_r>0 else 0
        
        events.append({
            'type':'FIZ_LONG_YUR_SHORT' if fzi>0 and yzi<0 else 'FIZ_SHORT_YUR_LONG',
            'fiz_z':round(fzi,1),'yur_z':round(yzi,1),'vol_z':round(vz[i],1),
            'max_ret':round(max_r,3),'min_ret':round(min_r,3),
            'last_ret':round(last_r,3),'avg_ret':round(avg_r,3),
            'half_range':round(half_r,3),'skew':skew,'retrace':round(retrace,2)
        })
    
    if len(events) < 50:
        continue
    
    long_ev=[e for e in events if e['type']=='FIZ_SHORT_YUR_LONG']
    short_ev=[e for e in events if e['type']=='FIZ_LONG_YUR_SHORT']
    
    lines.append(f"── {sym} ({len(events)} events, {len(rows)} bars) ──")
    
    pl = profile(long_ev, "FIZ_SHORT_YUR_LONG")
    if pl: lines.append(pl)
    ps = profile(short_ev, "FIZ_LONG_YUR_SHORT")
    if ps: lines.append(ps)
    lines.append("")

conn.close()

# Classification
print("\n".join(lines))

# Summary classification
print("\n\n" + "="*80)
print("КЛАССИФИКАЦИЯ ТИКЕРОВ")
print("="*80)

# Parse results and classify
current_sym = None
ticker_data = {}
for line in lines:
    if line.startswith("──") and "events" in line:
        current_sym = line.split()[1]
        ticker_data[current_sym] = {'long': None, 'short': None, 'events': int(line.split('(')[1].split()[0])}
    elif current_sym and 'FIZ_SHORT_YUR_LONG' in line:
        parts = line.split('|')
        if len(parts) >= 4:
            stats = parts[0].split()[-1]  # n=1234
            n = int(stats.split('=')[1])
            max_r = float(parts[1].split()[0].split('=')[1])
            min_r = float(parts[1].split()[1].split('=')[1])
            last_r = float(parts[1].split()[2].split('=')[1])
            up_close = float(parts[2].split('=')[1])
            ticker_data[current_sym]['long'] = {'n': n, 'max': max_r, 'min': min_r, 'last': last_r, 'up_close': up_close}
    elif current_sym and 'FIZ_LONG_YUR_SHORT' in line:
        parts = line.split('|')
        if len(parts) >= 4:
            stats = parts[0].split()[-1]
            n = int(stats.split('=')[1])
            max_r = float(parts[1].split()[0].split('=')[1])
            min_r = float(parts[1].split()[1].split('=')[1])
            last_r = float(parts[1].split()[2].split('=')[1])
            up_close = float(parts[2].split('=')[1])
            ticker_data[current_sym]['short'] = {'n': n, 'max': max_r, 'min': min_r, 'last': last_r, 'up_close': up_close}

print(f"{'Тикер':6s} {'Сигн':>5s} {'max':>7s} {'min':>7s} {'last':>7s} {'up%':>5s} {'Тип':>10s}")
print("-" * 55)

for sym in sorted(ticker_data.keys()):
    d = ticker_data[sym]
    for side, label in [('long', 'LONG'), ('short', 'SHORT')]:
        if d[side] is None:
            continue
        sd = d[side]
        # Classify
        typ = 'DEAD'
        if abs(sd['last']) > 0.05: typ = 'DIRECT'
        if sd['up_close'] > 55: typ = 'BULL'
        if sd['up_close'] < 45: typ = 'BEAR'
        if abs(sd['max']) > 1.0: typ = 'BIGMOVE'
        if abs(sd['max']) < 0.3 and abs(sd['min']) < 0.3: typ = 'FLAT'
        if sd['max'] > abs(sd['min'])*1.5: typ = 'UPSIDE'
        if abs(sd['min']) > sd['max']*1.5: typ = 'DOWNSIDE'
        
        print(f"{sym:6s} {side:5s} {sd['n']:>5d} {sd['max']:>+6.3f}% {sd['min']:>+6.3f}% {sd['last']:>+6.3f}% {sd['up_close']:>4.1f}% {typ:>10s}")

# Save
with open('/tmp/pattern_results.txt', 'w') as f:
    f.write('\n'.join(lines))
    f.write('\n\n')
    f.write('='*80 + '\n')
    f.write('CLASSIFICATION\n' + '='*80 + '\n')
    for sym in sorted(ticker_data.keys()):
        d = ticker_data[sym]
        for side, label in [('long', 'LONG'), ('short', 'SHORT')]:
            if d[side] is None: continue
            sd = d[side]
            f.write(f"{sym:6s} {label:5s} n={sd['n']:5d} max={sd['max']:+.3f} min={sd['min']:+.3f} last={sd['last']:+.3f} up={sd['up_close']:.1f}%\n")
