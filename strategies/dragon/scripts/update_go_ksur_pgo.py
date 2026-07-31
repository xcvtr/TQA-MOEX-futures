#!/usr/bin/env python3 -u
"""Update PG futures.ticker_specs.go with KSUR PGO rates from XLS + MOEX go.xml

GO = initial_margin * rate_ksur_pgo / (initial_margin_percent / 100)
"""
import re, psycopg2, subprocess, openpyxl

# 1. Parse MOEX go.xml
result = subprocess.run(['curl', '-s', 'https://www.moex.com/export/derivatives/go.aspx'],
                       capture_output=True, text=True, timeout=15)
content = result.stdout

moex_data = {}
for item in re.findall(r'<item[^>]*/>', content):
    c = re.search(r'code="([^"]+)"', item)
    if not c: continue
    code = c.group(1)
    prefix = code.split('-')[0]
    im = float(re.search(r'initial_margin="([\d.]+)"', item).group(1))
    im_pct = float(re.search(r'initial_margin_percent="([\d.]+)"', item).group(1))
    med = float(re.search(r'buy_deposit_medium_risk="([\d.]+)"', item).group(1))
    if prefix not in moex_data:
        moex_data[prefix] = []
    moex_data[prefix].append((code, im, im_pct, med))

# 2. Parse XLS KSUR rates
wb = openpyxl.load_workbook(
    '/home/user/.hermes/document_cache/doc_a708352f5ef1_instrumenty__edp_2026-07-25.xlsx',
    data_only=True)
ws = wb['Фьючерсы ФОРТС с пониженным ГО']

xls_rates = {}
for row_idx in range(7, ws.max_row + 1):
    code_val = ws.cell(row=row_idx, column=11).value
    rate = ws.cell(row=row_idx, column=12).value
    if code_val and rate is not None:
        xls_rates[code_val.strip()] = float(rate)

# 3. Compute and update
TICKER_MAP = {
    'Si': ('MTSI', 'USD_FUT'),     # XLS code for Si
    'BR': ('BR', None),             # BR not in XLS ПГО → skip
    'GZ': ('GAZR', 'GAZP_FUT'),    # XLS code for GAZR
    'GD': ('GOLD', 'GLDRU'),       # XLS code for GLDRUBF (Gold)
    'MM': ('MXI', 'MIX'),          # XLS code for MXI
    'NG': ('NG', 'NGASRU'),        # XLS code for NG
    'RN': ('ROSN', 'ROSN_FUT'),    # XLS code for ROSN
    'CR': ('CNY', 'CNY_FUT'),      # XLS code for CNY
}

PG = dict(host='10.0.0.60', port=5432, dbname='moex', user='postgres')
conn = psycopg2.connect(**PG)
cur = conn.cursor()

hdr = f'{"Ticker":5s} {"Front":15s} {"im":>8s} {"im_pct":>7s} {"med":>10s} {"rate":>8s} {"GO_new":>10s} {"GO_old":>10s}'
print(hdr)
print('-' * 85)

for ticker, (moex_prefix, xls_key) in TICKER_MAP.items():
    contracts = moex_data.get(moex_prefix, [])
    if not contracts:
        print(f'{ticker:5s} NO MOEX DATA')
        continue

    def sort_key(c):
        p = c[0].split('-')[1].split('.')
        return (int(p[1]), int(p[0]))
    contracts.sort(key=sort_key)
    c_name, im, im_pct, med = contracts[0]

    rate_kpur = None
    rate_ksur = None
    if xls_key is None:
        print(f'{ticker:5s} {c_name:15s} SKIP (no XLS rate)')
        continue
    for xc, xr in xls_rates.items():
        if xls_key.upper() in xc.upper() or xc.upper() in xls_key.upper():
            rate_ksur = xr
            break

    if rate_ksur is None:
        print(f'{ticker:5s} {c_name:15s} NO XLS RATE (key={xls_key})')
        continue

    # Also get КПУР rate from XLS (same row, col 3)
    rate_kpur = rate_ksur  # fallback
    for row_idx in range(7, ws.max_row + 1):
        code_val = ws.cell(row=row_idx, column=11).value
        if code_val and xls_key.upper() in str(code_val).strip().upper():
            kpur_cell = ws.cell(row=row_idx, column=3).value
            if kpur_cell is not None:
                rate_kpur = float(kpur_cell)
                break

    # GO = im * rate_ksur / rate_kpur
    go_new = round(im * rate_ksur / rate_kpur, 0)

    cur.execute("SELECT go FROM futures.ticker_specs WHERE ticker = %s", (ticker,))
    row = cur.fetchone()
    go_old = float(row[0]) if row else 0

    change = ((go_new - go_old) / go_old * 100) if go_old else 0
    arrow = 'UP' if go_new > go_old else 'DN' if go_new < go_old else '--'

    print(f'{ticker:5s} {c_name:15s} {im:>8.2f} {im_pct:>6.2f}% {med:>10.2f} {rate_ksur:>7.4f} {go_new:>10.0f} {go_old:>10.0f}  {arrow} {change:+.1f}%')

    cur.execute("UPDATE futures.ticker_specs SET go = %s WHERE ticker = %s", (go_new, ticker))

conn.commit()
cur.close()
conn.close()
print('\nDone! PG updated.')
