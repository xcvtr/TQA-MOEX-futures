import psycopg2, requests, json

r = requests.get('https://iss.moex.com/iss/engines/futures/markets/forts/securities.json?limit=500', timeout=15)
data = r.json()
cols = data['securities']['columns']
idxs = {c:i for i,c in enumerate(cols)}

# All our known tickers
all_tickers = {'FV','OZ','TI','AS','VI','DL','S0','PS','Si','FN','TN','SS','W4','WU','GZ','IP','RB','CR','BR','NG','GL','GD','ED','Eu','SR','VB','AF','AL','RN','SN','TT','SP','NM','HY','ME','GK','MG','LK','MM','MY','MX','RI','PT','PD','SV','CC','CE','CH','DX','FF','HS','IB','KC','MC','NA','NR','OJ','RM','SE','SF','UC','X5','YD','AU','BM','CL','ML','YN'}

conn = psycopg2.connect(host='10.0.0.60', dbname='moex', user='user')
cur = conn.cursor()

# Get existing tickers in BG to avoid duplicates
cur.execute("SELECT ticker FROM shared.ticker_specs")
existing = set(r[0] for r in cur.fetchall() or [])

specs = {}
for row in data['securities']['data']:
    secid = row[idxs['SECID']]
    base = secid[:2] if len(secid) >= 2 else secid
    asset = row[idxs['ASSETCODE']] or ''
    # Check if base is in our list
    if base in all_tickers and base not in specs:
        specs[base] = {
            'secid': secid,
            'name': (row[idxs['SHORTNAME']] or ''),
            'asset': asset,
            'lot': int(row[idxs['LOTVOLUME']]) if row[idxs['LOTVOLUME']] else 1,
            'min_step': float(row[idxs['MINSTEP']]) if row[idxs['MINSTEP']] else 0.01,
            'step_price': float(row[idxs['STEPPRICE']]) if row[idxs['STEPPRICE']] else 1.0,
            'decimals': int(row[idxs['DECIMALS']]) if row[idxs['DECIMALS']] else 0,
            'go': float(row[idxs['INITIALMARGIN']]) if row[idxs['INITIALMARGIN']] else 0,
        }

# Also special mappings
special = {
    'Si': 'Si-12.26', 'GL': 'GL-12.26', 'GD': 'GOLD-12.26',
    'Eu': 'Eu-12.26', 'SR': 'SBRF-12.26', 'BR': 'BR-12.26',
    'NG': 'NG-12.26', 'CR': 'CNY-3.27'
}
for base, secid_find in special.items():
    if base in specs:
        continue
    for row in data['securities']['data']:
        if row[idxs['SECID']] == secid_find:
            specs[base] = {
                'secid': row[idxs['SECID']],
                'name': (row[idxs['SHORTNAME']] or ''),
                'asset': row[idxs['ASSETCODE']] or base,
                'lot': int(row[idxs['LOTVOLUME']]) if row[idxs['LOTVOLUME']] else 1,
                'min_step': float(row[idxs['MINSTEP']]) if row[idxs['MINSTEP']] else 0.01,
                'step_price': float(row[idxs['STEPPRICE']]) if row[idxs['STEPPRICE']] else 1.0,
                'decimals': int(row[idxs['DECIMALS']]) if row[idxs['DECIMALS']] else 0,
                'go': float(row[idxs['INITIALMARGIN']]) if row[idxs['INITIALMARGIN']] else 0,
            }
            break

# Handle FV/OZ/TI - not in ISS
for base in ['FV','OZ','TI']:
    if base not in specs:
        specs[base] = {'secid': base+'XX', 'name': base+'-future', 'asset': base, 'lot': 1, 'min_step': 0.01, 'step_price': 1.0, 'decimals': 0, 'go': 1000}

n = 0
for t, s in sorted(specs.items()):
    cur.execute("""
        INSERT INTO shared.ticker_specs (ticker, sec_id, short_name, asset_code, lot_volume, min_step, step_price, decimals, go)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (ticker) DO UPDATE SET
            sec_id=EXCLUDED.sec_id, short_name=EXCLUDED.short_name,
            asset_code=EXCLUDED.asset_code, lot_volume=EXCLUDED.lot_volume,
            min_step=EXCLUDED.min_step, step_price=EXCLUDED.step_price,
            decimals=EXCLUDED.decimals, go=EXCLUDED.go, updated_at=NOW()
    """, (t, s['secid'], s['name'], s['asset'],
          s['lot'], s['min_step'], s['step_price'], s['decimals'], s['go']))
    n += 1

conn.commit()
cur.close()
conn.close()
print(f"Inserted/updated {n} tickers in ticker_specs")
