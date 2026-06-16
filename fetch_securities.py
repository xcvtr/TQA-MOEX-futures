#!/usr/bin/env python3
"""
MOEX Futures Securities Collector.
Собирает ГО (INITIALMARGIN), лотность, шаг цены для всех 64+ тикеров.
Сохраняет в ClickHouse + JSON-снапшот.
"""
import sys, os, json, requests
from datetime import datetime, date

sys.path.insert(0, '/home/user/projects/TQA-MOEX')
from config import CH_HOST, CH_PORT, CH_DB
import clickhouse_connect

ISS_URL = "https://iss.moex.com/iss/engines/futures/markets/forts/securities.json"
HEADERS = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36'}
SNAPSHOT_PATH = '/home/user/projects/TQA-MOEX/securities_snapshot.json'

TICKERS = [
    'AF','AL','AU','BM','BR','CC','CE','CH','CNYRUBF','CR','DX','ED','Eu',
    'EURRUBF','FF','GAZPF','GD','GK','GL','GLDRUBF','GZ','HS','HY','IB',
    'IMOEXF','KC','LK','MC','ME','MG','MM','MN','MX','MY','NA','NG','NM',
    'NR','OJ','PD','PT','RB','RI','RL','RM','RN','SBERF','SE','SF','Si',
    'SN','SP','SR','SS','SV','TN','TT','UC','USDRUBF','VB','VI','W4','X5','YD'
]

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
    'ALRS': 'AL', 'SBRF': 'SR', 'SMLT': 'SS', 'VTBR': 'VB', 'WHEAT': 'W4',
    'SI': 'Si', 'IMOEX': 'IMOEXF', 'MXI': 'MX', 'SILV': 'SV', 'LKOH': 'LK',
    'GOLD': 'GD', 'COCOA': 'CC', 'SGZH': 'SE', 'GMKN': 'NM', 'PLD': 'PD',
    'PLT': 'PT', 'RTS': 'RI', 'RENI': 'RN',
    'CNYRUBTOM': 'CNYRUBF', 'USDRUBTOM': 'USDRUBF', 'EURRUBTOM': 'EURRUBF',
    'GLDRUBTOM': 'GLDRUBF',
}


def get_ch():
    return clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)


def fetch_securities():
    cols = None
    all_data = []
    params = {'iss.meta': 'off', 'iss.only': 'securities', 'limit': 700}
    r = requests.get(ISS_URL, headers=HEADERS, params=params, timeout=30)
    if r.status_code == 200:
        d = r.json()
        cols = d['securities']['columns']
        all_data = d['securities']['data']
    return cols, all_data


def build_go_map(cols, all_data):
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

        if ticker not in moex_contracts or moex_contracts[ticker].get('prevopenposition', 0) < prevopenpos:
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


def save_to_ch(ch, contracts):
    rows = []
    for t, c in contracts.items():
        rows.append((t, c['secid'], c['shortname'], c['go_rub'], c['lot'],
                      c['stepprice'], c['minstep'], c['leverage'],
                      c['prevsettle'], c['prevprice'], c['lasttrade'],
                      c['prevopenposition'], datetime.fromisoformat(c['updated'])))
    # Delete + re-insert for securities (small table, fully replaced)
    ch.query("TRUNCATE TABLE IF EXISTS moex.securities")
    ch.insert(
        "moex.securities",
        rows,
        column_names=["ticker", "secid", "shortname", "go_rub", "lot",
                       "stepprice", "minstep", "leverage",
                       "prevsettle", "prevprice", "lasttrade",
                       "prevopenposition", "updated"],
    )
    return len(rows)


def main():
    print(f"[{datetime.now():%H:%M:%S}] Fetching securities...")
    cols, all_data = fetch_securities()
    print(f"  {len(all_data)} контрактов получено")
    print(f"  Строю маппинг...")
    contracts = build_go_map(cols, all_data)
    print(f"  Найдено {len(contracts)} наших тикеров")

    # Show new/changed vs current CH data
    ch = get_ch()
    db_contracts = {}
    try:
        rows = ch.query("SELECT ticker, go_rub, leverage FROM moex.securities").result_rows
        for r in rows:
            db_contracts[r[0]] = {'go': r[1], 'lev': r[2]}
    except:
        pass

    for t, c in sorted(contracts.items()):
        old = db_contracts.get(t)
        old_go = old['go'] if old else 0
        if not old or old_go != c['go_rub'] or old.get('lev', 0) != c['leverage']:
            print(f"  {'🆕' if not old else '🔄'} {t:6s} → {c['shortname']:20s} ГО={c['go_rub']:>8.0f} плечо={c['leverage']:.1f}x")

    # Save snapshot
    snapshot = {t: c for t, c in sorted(contracts.items())}
    with open(SNAPSHOT_PATH, 'w') as f:
        json.dump(snapshot, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n  ✅ Снапшот: {SNAPSHOT_PATH}")

    # Save to CH
    n = save_to_ch(ch, contracts)
    print(f"  ✅ ClickHouse: moex.securities — {n} записей")


if __name__ == '__main__':
    main()
