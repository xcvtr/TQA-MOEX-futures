#!/usr/bin/env python3
"""Map Alor tickers to MOEX ISS futures GO data."""
import sys, json, urllib.request

resp = urllib.request.urlopen('https://iss.moex.com/iss/engines/futures/markets/forts/securities.json?iss.meta=off&limit=200')
data = json.loads(resp.read().decode())
cols = data['securities']['columns']
rows = data['securities']['data']
idx = {c:i for i,c in enumerate(cols)}

interests = ['CH','W4','OJ','DX','BM','BR','NR','SV','SS','IB','NG','CC','SN','GZ','VB','PD','HY','SE','LK','GD','RI','GL','SR','NM',
             'ME','GK','SP','MM','UC','SF','HS','AL','MG','CE','RN']

found = {}
for r in rows:
    secid = r[idx['SECID']]
    for prefix_len in range(6, 1, -1):
        base = secid[:prefix_len]
        if base in interests:
            go = r[idx['INITIALMARGIN']]
            price = r[idx['PREVPRICE']]
            name = r[idx['SHORTNAME']]
            if go and price and float(go) > 0 and float(price) > 0:
                if base not in found:
                    found[base] = {'secid': secid, 'name': name, 'go': float(go), 'price': float(price)}
            break

existing_go = {
    'SS':2.0,'W4':9.2,'VB':5.7,'GD':12.1,'SR':5.8,'SV':4.8,'GZ':5.7,'PD':4.6,
    'LK':4.9,'GL':8.7,'RI':6.6,'NG':3.5,'CC':6.4,'CH':7.8,'IB':3.5,'NM':5.8,
    'SN':4.9,'BR':3.8,'NR':4.9,'HY':4.9,'OJ':5.9,'SE':1.4,'DX':5.0,'BM':5.0
}

print(f"{'Ticker':>8} {'ISS SecID':>12} {'Name':>25} {'GO':>10} {'Price':>10} {'Lev(ISS)':>8} {'Lev_OLD':>8}")
print('-'*85)
for base in sorted(interests):
    if base in found:
        f = found[base]
        lev_iss = f['price'] / f['go']
        lev_old = existing_go.get(base, None)
        flag = ' ★' if base not in existing_go else ''
        old_str = f"{lev_old}x" if lev_old else '?'
        print(f"{base:>8} {f['secid']:>12} {f['name']:>25} {f['go']:>10.2f} {f['price']:>10.2f} {lev_iss:>7.2f}x {old_str:>8}{flag}")
    else:
        print(f"{base:>8} {'NOT FOUND':>12}")
