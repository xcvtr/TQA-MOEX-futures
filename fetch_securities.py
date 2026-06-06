#!/usr/bin/env python3
"""
MOEX Futures Securities Collector.
Собирает ГО (INITIALMARGIN), лотность, шаг цены для всех 64+ тикеров.
Сохраняет в PostgreSQL + JSON-снапшот.
"""
import sys, os, json, requests
from datetime import datetime, date

sys.path.insert(0, '/home/user/projects/TQA-MOEX')
from config import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD
import psycopg2
from psycopg2.extras import execute_values

ISS_URL = "https://iss.moex.com/iss/engines/futures/markets/forts/securities.json"
HEADERS = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36'}
SNAPSHOT_PATH = '/home/user/projects/TQA-MOEX/securities_snapshot.json'

# Наши 64 тикера + их asset коды
TICKERS = [
    'AF','AL','AU','BM','BR','CC','CE','CH','CNYRUBF','CR','DX','ED','Eu',
    'EURRUBF','FF','GAZPF','GD','GK','GL','GLDRUBF','GZ','HS','HY','IB',
    'IMOEXF','KC','LK','MC','ME','MG','MM','MN','MX','MY','NA','NG','NM',
    'NR','OJ','PD','PT','RB','RI','RL','RM','RN','SBERF','SE','SF','Si',
    'SN','SP','SR','SS','SV','TN','TT','UC','USDRUBF','VB','VI','W4','X5','YD'
]

# Маппинг asset code → ticker для securities matching
ASSET_TO_TICKER = {
    'AF': 'AF', 'AFRICA': 'AF', 'ALRS': 'AL', 'AU': 'AU', 'BR': 'BR', 'CC': 'CC',
    'CE': 'CE', 'CH': 'COCOA', 'CR': 'CR', 'DX': 'DX', 'ED': 'ED',
    'Eu': 'Eu', 'EU': 'Eu', 'FF': 'FF', 'GD': 'GD', 'GK': 'GK',
    'GL': 'GL', 'GZ': 'GZ', 'HS': 'HS', 'HY': 'HY', 'IB': 'IB',
    'KC': 'KC', 'LK': 'LK', 'MC': 'MC', 'ME': 'ME', 'MG': 'MG',
    'MM': 'MM', 'MN': 'MN', 'MX': 'MX', 'MY': 'MY', 'NA': 'NA',
    'NG': 'NG', 'NM': 'NM', 'NR': 'NR', 'OJ': 'OJ', 'PD': 'PD',
    'PT': 'PT', 'RB': 'RB', 'RI': 'RI', 'RL': 'RL', 'RM': 'RM',
    'RN': 'RN', 'SE': 'SE', 'SF': 'SF', 'Si': 'Si', 'SN': 'SN',
    'SP': 'SP', 'SR': 'SR', 'SS': 'SS', 'SV': 'SV', 'TN': 'TN',
    'TT': 'TT', 'UC': 'UC', 'VB': 'VB', 'VI': 'VI', 'W4': 'W4',
    'X5': 'X5', 'YD': 'YD',
    # ISS asset codes
    'ALRS': 'AL', 'SBRF': 'SR', 'SMLT': 'SS', 'VTBR': 'VB', 'WHEAT': 'W4',
    'SI': 'Si', 'IMOEX': 'IMOEXF', 'MXI': 'MX', 'SILV': 'SV', 'LKOH': 'LK',
    'GOLD': 'GD', 'COCOA': 'CC', 'SGZH': 'SE', 'GMKN': 'NM', 'PLD': 'PD',
    'PLT': 'PT', 'RTS': 'RI', 'RENI': 'RN',
    'CNYRUBTOM': 'CNYRUBF', 'USDRUBTOM': 'USDRUBF', 'EURRUBTOM': 'EURRUBF',
    'GLDRUBTOM': 'GLDRUBF',
}


def fetch_securities():
    cols = None
    all_data = []
    
    # Запрашиваем все сразу — 564 контракта помещаются в один запрос
    params = {'iss.meta': 'off', 'iss.only': 'securities', 'limit': 700}
    r = requests.get(ISS_URL, headers=HEADERS, params=params, timeout=30)
    if r.status_code == 200:
        d = r.json()
        cols = d['securities']['columns']
        all_data = d['securities']['data']
    
    return cols, all_data

def build_go_map(cols, all_data):
    """Построить карту: ticker → {GO, lot, step, minstep, prevsettle, prevprice, secid, shortname}"""
    idx = {c: i for i, c in enumerate(cols)}
    moex_contracts = {}
    
    for row in all_data:
        asset = row[idx['ASSETCODE']] if idx['ASSETCODE'] < len(row) else ''
        if not asset:
            continue
        ticker = ASSET_TO_TICKER.get(asset.upper())
        if not ticker:
            continue
        
        secid = row[idx['SECID']]
        shortname = row[idx['SHORTNAME']] if idx['SHORTNAME'] < len(row) else ''
        
        def safe_float(v):
            try: return float(v) if v else 0
            except: return 0
        
        go = safe_float(row[idx['INITIALMARGIN']] if idx['INITIALMARGIN'] < len(row) else 0)
        lot = safe_float(row[idx['LOTVOLUME']] if idx['LOTVOLUME'] < len(row) else 0)
        stepprice = safe_float(row[idx['STEPPRICE']] if idx['STEPPRICE'] < len(row) else 0)
        minstep = safe_float(row[idx['MINSTEP']] if idx['MINSTEP'] < len(row) else 0)
        prevsettle = safe_float(row[idx['PREVSETTLEPRICE']] if idx['PREVSETTLEPRICE'] < len(row) else 0)
        prevprice = safe_float(row[idx['PREVPRICE']] if idx['PREVPRICE'] < len(row) else 0)
        lasttrade = str(row[idx['LASTTRADEDATE']] if idx['LASTTRADEDATE'] < len(row) else '')
        prevopenpos = safe_float(row[idx['PREVOPENPOSITION']] if idx['PREVOPENPOSITION'] < len(row) else 0)
        
        if ticker not in moex_contracts or moex_contracts[ticker].get('prevopenpos', 0) < prevopenpos:
            # Leverage: contract_value = (prevsettle * stepprice / minstep) if minstep > 0 else 0
            if minstep > 0 and stepprice > 0:
                contract_val = prevsettle * stepprice / minstep
                lev = contract_val / go if go > 0 else 5.0
            else:
                contract_val = 0
                lev = 5.0 if go > 0 else 5.0
            
            moex_contracts[ticker] = {
                'ticker': ticker,
                'secid': secid,
                'shortname': shortname,
                'go_rub': go,
                'lot': lot,
                'stepprice': stepprice,
                'minstep': minstep,
                'leverage': round(lev, 1),
                'prevsettle': prevsettle,
                'prevprice': prevprice,
                'lasttrade': lasttrade,
                'prevopenposition': prevopenpos,
                'updated': datetime.now().isoformat(),
            }
    
    return moex_contracts

def save_to_db(conn, contracts):
    """Сохранить в PostgreSQL таблицу moex_securities."""
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS moex_securities (
                ticker VARCHAR(20) PRIMARY KEY,
                secid VARCHAR(20),
                shortname VARCHAR(50),
                go_rub DOUBLE PRECISION,
                lot DOUBLE PRECISION,
                stepprice DOUBLE PRECISION,
                minstep DOUBLE PRECISION,
                leverage DOUBLE PRECISION,
                prevsettle DOUBLE PRECISION,
                prevprice DOUBLE PRECISION,
                lasttrade VARCHAR(20),
                prevopenposition DOUBLE PRECISION,
                updated TIMESTAMP
            )
        """)
        
        rows = []
        for t, c in contracts.items():
            rows.append((t, c['secid'], c['shortname'], c['go_rub'], c['lot'],
                        c['stepprice'], c['minstep'], c['leverage'],
                        c['prevsettle'], c['prevprice'], c['lasttrade'],
                        c['prevopenposition'], datetime.fromisoformat(c['updated'])))
        
        execute_values(cur, """
            INSERT INTO moex_securities AS tgt
            (ticker, secid, shortname, go_rub, lot, stepprice, minstep,
             leverage, prevsettle, prevprice, lasttrade, prevopenposition, updated)
            VALUES %s
            ON CONFLICT (ticker) DO UPDATE SET
                secid = EXCLUDED.secid,
                shortname = EXCLUDED.shortname,
                go_rub = EXCLUDED.go_rub,
                lot = EXCLUDED.lot,
                stepprice = EXCLUDED.stepprice,
                minstep = EXCLUDED.minstep,
                leverage = EXCLUDED.leverage,
                prevsettle = EXCLUDED.prevsettle,
                prevprice = EXCLUDED.prevprice,
                lasttrade = EXCLUDED.lasttrade,
                prevopenposition = EXCLUDED.prevopenposition,
                updated = EXCLUDED.updated
        """, rows)
        conn.commit()
        return len(rows)

def main():
    print(f"[{datetime.now():%H:%M:%S}] Fetching securities...")
    cols, all_data = fetch_securities()
    print(f"  {len(all_data)} контрактов получено")
    
    print(f"  Строю маппинг...")
    contracts = build_go_map(cols, all_data)
    print(f"  Найдено {len(contracts)} наших тикеров")
    
    # Show new/changed
    db_contracts = {}
    try:
        conn = psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD)
        cur = conn.cursor()
        cur.execute("SELECT ticker, go_rub, leverage FROM moex_securities")
        for r in cur.fetchall():
            db_contracts[r[0]] = {'go': r[1], 'lev': r[2]}
        conn.close()
    except:
        pass
    
    for t, c in sorted(contracts.items()):
        old = db_contracts.get(t)
        old_go = old['go'] if old else 0
        if not old or old_go != c['go_rub'] or old.get('lev', 0) != c['leverage']:
            print(f"  {'🆕' if not old else '🔄'} {t:6s} → {c['shortname']:20s} ГО={c['go_rub']:>8.0f} плечо={c['leverage']:.1f}x")
        elif old:
            pass  # unchanged
    
    # Save snapshot
    snapshot = {t: c for t, c in sorted(contracts.items())}
    with open(SNAPSHOT_PATH, 'w') as f:
        json.dump(snapshot, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n  ✅ Снапшот: {SNAPSHOT_PATH}")
    
    # Save to DB
    conn = psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD)
    n = save_to_db(conn, contracts)
    conn.close()
    print(f"  ✅ PostgreSQL: moex_securities — {n} записей")

if __name__ == '__main__':
    main()
