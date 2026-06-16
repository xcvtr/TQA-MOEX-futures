#!/usr/bin/env python3
"""
MOEX Full Price History Loader v4

Fetches daily history for all futures contracts via ISS API.
For each date, picks the contract with highest volume.
Writes to ClickHouse (moex.prices).
"""

import sys, os, json, time
from datetime import datetime, date
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import MOEX_OI_TICKERS, CH_HOST, CH_PORT, CH_DB

import requests
import clickhouse_connect

TICKER_SET = set(MOEX_OI_TICKERS)
REQUEST_TIMEOUT = 30

# futoi ticker → MOEX futures ASSETCODE
TICKER_TO_ASSET = {
    "Si": "Si", "BR": "BR", "ED": "ED", "Eu": "Eu",
    "GD": "GOLD", "GZ": "GAZR", "LK": "LKOH", "SR": "SBRF", "VB": "VTBR",
    "RN": "ROSN", "AF": "AFLT", "AL": "ALRS", "SN": "SNGR", "SP": "SPBE",
    "TT": "T", "HY": "HYDR", "NM": "NLMK", "MG": "MAGN", "MM": "MOEX",
    "ME": "MTLR", "VI": "RVI", "SV": "SILV", "NG": "NG",
    "PD": "PLD", "PT": "PLT", "RI": "RTS",
    "CNYRUBF": "CNYRUBTOM", "USDRUBF": "USDRUBTOM", "EURRUBF": "EURRUBTOM",
    "GLDRUBF": "GLDRUBTOM", "IMOEXF": "IMOEX", "GAZPF": "GAZPF",
    "SBERF": "SBERF", "GL": "GL", "X5": "X5", "YD": "YDEX",
    "BM": "BRM", "CC": "COCOA", "CE": "COPPER", "CH": "CHINA",
    "DX": "DAX", "FF": "FNI", "GK": "GMKN", "HS": "HANG",
    "IB": "IBIT", "KC": "COFFEE", "MC": "MXI", "MY": "MY",
    "NA": "NASD", "NR": "NICKEL", "OJ": "ORANGE", "RB": "RUBBER",
    "RL": "RURAL", "RM": "RTSM", "SE": "SGZH", "SS": "SMLT",
    "W4": "WHEAT", "UC": "UCNY", "AU": "AUDU",
    "TN": "T", "SF": "SFIN",
}
ASSET_TO_TICKER = {v: k for k, v in TICKER_TO_ASSET.items()}


def get_ch():
    return clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)


def get_all_secids() -> dict:
    url = "https://iss.moex.com/iss/engines/futures/markets/forts/securities.json?iss.meta=off&iss.only=securities"
    resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=REQUEST_TIMEOUT)
    data = resp.json()
    cols = data["securities"]["columns"]
    idx = {c: i for i, c in enumerate(cols)}
    contracts = defaultdict(list)
    for row in data["securities"]["data"]:
        asset = row[idx["ASSETCODE"]] or ""
        if asset in ASSET_TO_TICKER:
            contracts[asset].append({
                "secid": row[idx["SECID"]],
                "shortname": row[idx["SHORTNAME"]],
                "last_trade": row[idx["LASTTRADEDATE"]] or "",
            })
    return contracts


def fetch_all_pages(secid: str, from_date: str, till_date: str) -> list[dict]:
    all_rows = []
    start = 0
    while True:
        url = f"https://iss.moex.com/iss/history/engines/futures/markets/forts/securities/{secid}.json?from={from_date}&till={till_date}&iss.meta=off&iss.only=history,history.cursor&start={start}"
        for attempt in range(3):
            try:
                resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=REQUEST_TIMEOUT)
                if resp.status_code != 200:
                    time.sleep(1); continue
                data = resp.json()
                cols = data["history"]["columns"]
                rows = data["history"]["data"]
                cursor = data.get("history.cursor", {}).get("data", [[0, 0, 100]])[0]
                total = cursor[1] if len(cursor) > 1 else 0
                all_rows.extend(dict(zip(cols, row)) for row in rows if any(row))
                start += 100
                if start >= total:
                    return all_rows
                break
            except:
                time.sleep(1); break
        else:
            break
    return all_rows


def save_batch(ch, records: list) -> int:
    if not records:
        return 0
    ch.insert(
        "moex.prices",
        records,
        column_names=["symbol", "time", "open", "high", "low",
                       "last", "volume", "open_interest", "settle_price"],
    )
    return len(records)


def main():
    print(f"=== MOEX Price History v4 [{datetime.now():%Y-%m-%d %H:%M:%S}] === (ClickHouse)")
    ch = get_ch()
    contracts = get_all_secids()
    print(f"Assets: {len(contracts)}")
    total = 0

    for asset in sorted(contracts.keys()):
        ticker = ASSET_TO_TICKER[asset]
        clist = sorted(contracts[asset], key=lambda x: x["last_trade"] or "")
        print(f"\n{ticker:10s} (asset={asset}): {len(clist)} contracts")

        # Fetch ALL history for ALL contracts of this ticker
        daily_pool = defaultdict(list)  # date → [(volume, oi, record)]
        for c in clist:
            rows = fetch_all_pages(c["secid"], "2023-01-01", "2026-05-15")
            for r in rows:
                td = r.get("TRADEDATE", "")
                if not td:
                    continue
                try:
                    vol = int(r["VOLUME"]) if r.get("VOLUME") not in (None, "") else 0
                    oi = int(r["OPENPOSITION"]) if r.get("OPENPOSITION") not in (None, "") else 0
                    open_ = float(r["OPEN"]) if r.get("OPEN") not in (None, "") else None
                    high = float(r["HIGH"]) if r.get("HIGH") not in (None, "") else None
                    low = float(r["LOW"]) if r.get("LOW") not in (None, "") else None
                    close = float(r["CLOSE"]) if r.get("CLOSE") not in (None, "") else None
                    settle = float(r["SETTLEPRICE"]) if r.get("SETTLEPRICE") not in (None, "") else None
                except:
                    continue
                daily_pool[td].append((vol, oi, (open_, high, low, close, vol, oi, settle)))

        # For each date, pick best contract (highest volume, fallback to OI)
        records = []
        for td in sorted(daily_pool.keys()):
            pool = daily_pool[td]
            # Sort by volume desc, then OI desc
            pool.sort(key=lambda x: (x[0] or 0, x[1] or 0), reverse=True)
            _, _, vals = pool[0]
            open_, high, low, close, vol, oi, settle = vals
            records.append((ticker, f"{td}T23:50:00", open_, high, low, close, vol, oi, settle))

        if records:
            n = save_batch(ch, records)
            total += n
            print(f"  {len(records)} days, {n} saved to CH")

        time.sleep(0.15)

    print(f"\n=== Done: {total} records ===")


if __name__ == "__main__":
    main()
