#!/usr/bin/env python3
"""Update futures.ticker_specs.go from MOEX XML with KSUR PGO formula.

GO = initial_margin * rate_ksur / rate_kpur
Rates from FINAM XLS (фиксированы, обновляются при появлении нового XLS).
"""
import sys, subprocess, re

URL = 'https://www.moex.com/export/derivatives/go.aspx'
PG = 'postgresql://postgres@10.0.0.60/moex'

# KSUR PGO rates (from instrumenty__edp_2026-07-25.xlsx)
# {MOEX_prefix: (kpur_rate, ksur_rate)}
RATES = {
    'MTSI': (0.06, 0.1164),   # Si
    'BR':   (None, None),      # BR — нет ставки, пропускаем
    'GAZR': (0.15, 0.2775),   # GZ
    'GOLD': (0.08, 0.1536),   # GD
    'MXI':  (0.075, 0.1444),  # MM
    'NG':   (0.20, 0.36),      # NG
    'ROSN': (0.15, 0.2775),   # RN
    'CNY':  (0.045, 0.088),   # CR
}

# Short names → ticker mapping (based on go.xml symbol format)
# go.xml uses codes like MTU6, NGU6 etc — identify by code prefix
PREFIX_MAP = {
    'MTSI': 'Si', 'BR': 'BR', 'GAZR': 'GZ', 'GOLD': 'GD',
    'MXI': 'MM', 'NG': 'NG', 'ROSN': 'RN', 'CNY': 'CR',
}

import urllib.request
try:
    req = urllib.request.Request(URL, headers={'User-Agent': 'Mozilla/5.0'})
    raw = urllib.request.urlopen(req, timeout=30).read()
    content = raw.decode('windows-1251')
except Exception as e:
    print(f'Download failed: {e}')
    sys.exit(1)

# Parse contract items, find front-month (>30d from today)
from datetime import datetime, date
today = date.today()

contracts = {}  # prefix → [(code, im, delivery_date)]
for item in re.findall(r'<item[^>]*/>', content):
    c = re.search(r'code="([^"]+)"', item)
    if not c: continue
    code = c.group(1)
    prefix = code.split('-')[0]
    im = float(re.search(r'initial_margin="([\d.]+)"', item).group(1))
    med = re.search(r'buy_deposit_medium_risk="([\d.]+)"', item)
    medium = float(med.group(1)) if med else im
    dd_str = re.search(r'delivery_date="([\d\s]+)"', item)
    dd = None
    if dd_str:
        try: dd = datetime.strptime(dd_str.group(1).strip(), '%Y%m%d').date()
        except: pass
    contracts.setdefault(prefix, []).append((code, im, medium, dd))

updates = []
for prefix, ticker in PREFIX_MAP.items():
    rates = RATES.get(prefix)
    if not rates or rates[0] is None:
        print(f'{ticker}: SKIP (no KSUR rate)')
        continue
    
    cts = contracts.get(prefix, [])
    if not cts:
        print(f'{ticker}: SKIP (no contract in XML)')
        continue
    
    # Find front-month (>30d to delivery)
    valid = [c for c in cts if c[3] and (c[3] - today).days >= 30]
    if not valid:
        valid = sorted(cts, key=lambda x: x[3] if x[3] else date.max, reverse=True)
    else:
        valid.sort(key=lambda x: x[3])
    code, im, medium, dd = valid[0]

    # ПГО (пониженное ГО для КВАЛ):
    # ГО_ПГО = medium (КСУР) × kpur/ksur  — понижение ~0.5×
    rate_kpur, rate_ksur = rates
    go = round(medium * rate_kpur / rate_ksur, 0)

    r = subprocess.run(['psql', PG, '-c',
        f"UPDATE futures.ticker_specs SET go={go:.0f}, updated_at=NOW() WHERE ticker='{ticker}'"],
        capture_output=True, text=True)
    ok = 'UPDATE 1' in r.stdout
    ok_str = 'OK' if ok else 'FAIL'
    print(f'{ticker}: {ok_str} -> GO={go:.0f} (contract={code}, im={im:.0f}, medium={medium:.0f}, kpur/ksur={rate_kpur/rate_ksur:.3f})')

print(f'\nUpdated {len(updates)} tickers ({len(raw)} bytes)')
