#!/usr/bin/env python3 -u
"""Update PG go with KSUR PGO from MOEX XML + FINAM XLS.

Usage: python3 update_go_ksur_pgo.py
Runs: curl MOEX go.xml + parse FINAM XLS → UPDATE futures.ticker_specs
"""

import re, subprocess, psycopg2, openpyxl

# ── 1. Parse MOEX go.xml ──
result = subprocess.run(['curl', '-s', 'https://www.moex.com/export/derivatives/go.aspx'],
                       capture_output=True, text=True, timeout=15)
content = result.stdout

from datetime import datetime, date

today = date(2026, 7, 26)

moex = {}
for item in re.findall(r'<item[^>]*/>', content):
    c = re.search(r'code="([^"]+)"', item)
    if not c: continue
    code = c.group(1)
    prefix = code.split('-')[0]
    im = float(re.search(r'initial_margin="([\d.]+)"', item).group(1))
    med = float(re.search(r'buy_deposit_medium_risk="([\d.]+)"', item).group(1))
    dd_str = re.search(r'delivery_date="([\d\s]+)"', item)
    dd = None
    if dd_str:
        try:
            dd = datetime.strptime(dd_str.group(1).strip(), '%Y%m%d').date()
        except: pass
    moex.setdefault(prefix, []).append((code, im, med, dd))

# ── 2. Parse FINAM XLS ──
XLS = '/home/user/.hermes/document_cache/doc_a708352f5ef1_instrumenty__edp_2026-07-25.xlsx'
wb = openpyxl.load_workbook(XLS, data_only=True)
ws = wb['Фьючерсы ФОРТС с пониженным ГО']

# (code → (kpur_rate, ksur_rate))
xls = {}
for row in range(7, ws.max_row + 1):
    code = ws.cell(row=row, column=11).value  # KSUR code
    kpur = ws.cell(row=row, column=3).value   # KPUR rate
    ksur = ws.cell(row=row, column=12).value  # KSUR rate
    if code and kpur is not None and ksur is not None:
        xls[str(code).strip()] = (float(kpur), float(ksur))

# ── 3. Ticker mapping ──
MAP = {
    'Si': ('MTSI', 'USD_FUT'),
    'GZ': ('GAZR', 'GAZP_FUT'),
    'GD': ('GOLD', 'GLDRU'),
    'MM': ('MXI', 'MIX'),
    'NG': ('NG', 'NGASRU'),
    'RN': ('ROSN', 'ROSN_FUT'),
    'CR': ('CNY', 'CNY_FUT'),
    # BR not in XLS → manual
    'BR': ('BR', None),
}

conn = psycopg2.connect(host='10.0.0.60', port=5432, dbname='moex', user='postgres')
cur = conn.cursor()

print(f'{"Ticker":5s} {"MoexPrefix":12s} {"Front":14s} {"im":>8s} {"med":>8s} {"kpur":>6s} {"ksur":>6s} {"go_new":>8s} {"go_old":>8s}')
print('-' * 85)

for ticker, (mpref, xkey) in MAP.items():
    # MOEX data: pick contract with delivery >30 days from today, closest to expiry
    cts = moex.get(mpref, [])
    if not cts:
        print(f'{ticker:5s} {mpref:12s} NO MOEX DATA'); continue
    # Filter: delivery_date >= today+30d, then sort by delivery_date
    valid = [c for c in cts if c[3] and (c[3] - today).days >= 30]
    if not valid:
        # Fallback: take the one with farthest delivery
        valid = sorted(cts, key=lambda x: x[3] if x[3] else date.max, reverse=True)
    else:
        valid.sort(key=lambda x: x[3])
    code, im, med, dd = valid[0]

    # XLS rate
    if xkey is None:
         print(f'{ticker:5s} {mpref:12s} {code:14s} {im:>8.0f} {med:>8.0f} SKIP (no XLS)'); continue

    xd = None
    for xk, xv in xls.items():
        if xkey.upper() in xk.upper() or xk.upper() in xkey.upper():
            xd = xv; break
    if not xd:
        print(f'{ticker:5s} {mpref:12s} {code:14s} {im:>8.0f} {med:>8.0f} NO XLS RATE'); continue

    kpur_rate, ksur_rate = xd
    go_new = round(im * ksur_rate / kpur_rate, 0)

    cur.execute("SELECT go FROM futures.ticker_specs WHERE ticker = %s", (ticker,))
    row = cur.fetchone()
    go_old = float(row[0]) if row else 0

    arrow = '🟢' if abs(go_new - go_old) / go_old < 0.05 else '🔴'
    print(f'{ticker:5s} {mpref:12s} {code:14s} {im:>8.0f} {med:>8.0f} {kpur_rate:>5.3f} {ksur_rate:>5.3f} {go_new:>8.0f} {go_old:>8.0f}  {arrow}')

    cur.execute("UPDATE futures.ticker_specs SET go = %s WHERE ticker = %s", (go_new, ticker))

conn.commit()
cur.close(); conn.close()
print('\n✅ PG updated.')
