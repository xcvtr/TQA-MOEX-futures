#!/usr/bin/env python3
"""Load AlgoPack v2 orderstats (put/cancel orders) into ClickHouse.
Works like algopack_load_v2.py but for orderstats.
Usage: TABLE=orderstats T=eyJ... python3 scripts/orderstats_load.py
"""
import os, sys, time, json
from datetime import datetime, timedelta
import urllib.request

T = os.environ.get("T", "")
TABLE = "orderstats"
CH_HOST = os.environ.get("CH_HOST", "10.0.0.64")
CH_DB = "moex_algopack_v2"
API_BASE = "https://apim.moex.com/iss/datashop/algopack"

def fetch_day(day):
    url = API_BASE + "/eq/" + TABLE + ".json?date=" + day + "&limit=100000"
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + T})
    try:
        resp = urllib.request.urlopen(req, timeout=120)
        d = json.loads(resp.read())
    except Exception as e:
        return (0, str(e))

    cn = [c.lower() for c in d["data"]["columns"]]
    rows = d["data"]["data"]
    if not rows:
        return (0, None)

    total = 0
    BATCH = 1000
    for start in range(0, len(rows), BATCH):
        batch = rows[start:start + BATCH]
        vals = []
        for row in batch:
            vs = []
            for i, v in enumerate(row):
                if v is None:
                    vs.append("NULL")
                elif isinstance(v, (int, float)):
                    vs.append(str(v))
                else:
                    vs.append(chr(39) + str(v).replace(chr(39), chr(39) * 2) + chr(39))
            vals.append("(" + ", ".join(vs) + ")")
        sql = "INSERT INTO %s.%s (%s) VALUES\n%s" % (CH_DB, TABLE, ", ".join(cn), "\n".join(vals))
        try:
            u = "http://" + CH_HOST + ":8123/"
            urllib.request.urlopen(u, data=sql.encode("utf-8"), timeout=120).read()
            total += len(batch)
        except Exception as e:
            return (total, "CH: " + str(e))
    return (total, None)

def main():
    last = "2020-01-01"
    try:
        u = "http://%s:8123/?query=SELECT+max(tradedate)+FROM+%s.%s" % (CH_HOST, CH_DB, TABLE)
        cl = urllib.request.urlopen(u, timeout=10).read().decode().strip()
        if cl > "2020-01-01":
            last = cl
    except:
        pass

    s = datetime.strptime(last, "%Y-%m-%d")
    e = datetime.now() - timedelta(days=1)
    ad = []
    d = s
    while d <= e:
        ad.append(d.strftime("%Y-%m-%d"))
        d += timedelta(1)
    n = len(ad)
    print("%s: %d days (%s to %s)" % (TABLE, n, ad[0], ad[-1]))
    sys.stdout.flush()

    total = 0
    errs = 0
    rt = time.time()
    for i, day in enumerate(ad):
        nr, er = fetch_day(day)
        if nr > 0:
            total += nr
        if er:
            errs += 1
            if errs <= 3:
                print("  %s: ERR %s" % (day, er))
                sys.stdout.flush()
        if time.time() - rt > 60:
            pct = (i + 1) * 100 // n
            print("  [%d%%] %d/%d days, %d rows, %d errors" % (pct, i+1, n, total, errs))
            sys.stdout.flush()
            rt = time.time()
    print()
    print("TABLE %s DONE: %d rows, %d errors" % (TABLE, total, errs))

if __name__ == "__main__":
    main()
