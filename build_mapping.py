#!/usr/bin/env python3
"""Build mapping: our ticker → MOEX futures ASSETCODE."""
import json, requests

resp = requests.get(
    "https://iss.moex.com/iss/engines/futures/markets/forts/securities.json?iss.meta=off&iss.only=securities",
    headers={"User-Agent": "Mozilla/5.0"}, timeout=30
)
data = resp.json()
cols = data["securities"]["columns"]
idx = {c: i for i, c in enumerate(cols)}

our_tickers = [
    "AF","AL","AU","BM","BR","CC","CE","CH","CNYRUBF","CR","DX","ED",
    "EURRUBF","Eu","FF","GAZPF","GD","GK","GL","GLDRUBF","GZ","HS",
    "HY","IB","IMOEXF","KC","LK","MC","ME","MG","MM","MN","MX","MY",
    "NA","NG","NM","NR","OJ","PD","PT","RB","RI","RL","RM","RN",
    "SBERF","SE","SF","Si","SN","SP","SR","SS","SV","TN","TT","UC",
    "USDRUBF","VB","VI","W4","X5","YD",
]

# Build known ASSETCODE for each ticker
# SHORTNAME format: "Si-6.26" or "SBRF-6.26" or "CNYRUBF"
# ASSETCODE: actual asset code in MOEX
known_assets = {}
for row in data["securities"]["data"]:
    secid = row[idx["SECID"]]
    short = row[idx["SHORTNAME"]] or ""
    asset = row[idx["ASSETCODE"]] or ""
    
    # Extract prefix before "-"
    prefix = short.split("-")[0] if "-" in short else short
    
    for t in our_tickers:
        # Match if prefix starts with our ticker (case-insensitive)
        if prefix.upper() == t.upper():
            if t not in known_assets:
                known_assets[t] = (asset, secid, short)
            break

print("# Manual mapping needed for unmatched:")
for t in our_tickers:
    if t in known_assets:
        a, s, sh = known_assets[t]
        print(f"{t:10s} → asset={a:15s} secid={s:12s} name={sh}")
    else:
        print(f"{t:10s} → NO MATCH (needs manual mapping)")
