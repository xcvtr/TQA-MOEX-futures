#!/usr/bin/env python3
"""Fix OI in supercandles_fo using clickhouse-client via subprocess."""
import subprocess
import sys

CH = ["clickhouse-client", "-h", "10.0.0.60", "-q"]

def q(sql):
    r = subprocess.run(CH + [sql], capture_output=True, text=True, timeout=30)
    if r.returncode:
        print(f"  ERROR: {r.stderr[:200]}", file=sys.stderr)
        return []
    return [line.split('\t') for line in r.stdout.strip().split('\n') if line.strip()]

# Получаем все тикеры
tickers = [r[0] for r in q("SELECT DISTINCT ticker FROM moex.supercandles_fo ORDER BY ticker")]
total = len(tickers)
print(f"Tickers: {total}")

for i, ticker in enumerate(tickers):
    # Получаем первый secid для этого ticker
    secid_rows = q(f"SELECT secid FROM moex.supercandles_fo WHERE ticker = '{ticker}' LIMIT 1")
    if not secid_rows:
        print(f"[{i+1}/{total}] {ticker}: no secid in supercandles")
        continue
    
    sid = secid_rows[0][0]
    t_len = len(ticker)
    
    # Определяем условие поиска в tradestats_fo
    if sid == ticker:
        secid_cond = f"secid = '{ticker}'"
    else:
        secid_cond = f"substring(secid, 1, {t_len}) = '{ticker}'"
    
    # Получаем дневные OI
    oi_rows = q(f"""
        SELECT 
            toString(tradedate),
            toString(argMin(oi_close, tradetime)),
            toString(argMax(oi_close, tradetime)),
            toString(max(oi_high)),
            toString(min(oi_low)),
            toString(argMin(oi_open, tradetime))
        FROM moex.tradestats_fo
        WHERE {secid_cond}
          AND tradedate >= '2025-01-01'
          AND substring(tradetime, 1, 2) >= '10'
        GROUP BY tradedate
        ORDER BY tradedate
        FORMAT TabSeparated
    """)
    
    if not oi_rows:
        print(f"[{i+1}/{total}] {ticker}: no OI data")
        continue
    
    n = 0
    for row in oi_rows:
        if len(row) < 6:
            continue
        dt, oi_first_s, oi_last_s, oi_high_s, oi_low_s, oi_open_s = row
        
        try:
            oi_first = int(float(oi_first_s)) if oi_first_s else 0
            oi_last = int(float(oi_last_s)) if oi_last_s else 0
            oi_high = int(float(oi_high_s)) if oi_high_s else 0
            oi_low = int(float(oi_low_s)) if oi_low_s else 0
            oi_open = int(float(oi_open_s)) if oi_open_s else 0
        except (ValueError, TypeError):
            continue
        
        if oi_first == 0 and oi_last == 0:
            continue
        
        oi_change = oi_last - oi_open
        if oi_high == 0: oi_high = max(oi_open, oi_last)
        if oi_low == 0: oi_low = min(oi_open, oi_last)
        
        r = subprocess.run(CH + [f"""
            ALTER TABLE moex.supercandles_fo
            UPDATE 
                oi_open = {oi_open},
                oi_high = {oi_high},
                oi_low = {oi_low},
                oi_close = {oi_last},
                oi_change = {oi_change}
            WHERE ticker = '{ticker}'
              AND tradedate = '{dt}'
        """], capture_output=True, text=True, timeout=30)
        
        if r.returncode:
            print(f"  UPDATE ERROR [{ticker} {dt}]: {r.stderr[:100]}")
            continue
        n += 1
    
    print(f"[{i+1}/{total}] {ticker}: {n} days updated (from {len(oi_rows)} found)")

print("\n=== Verification ===")
for t in ['Si', 'GL', 'BR', 'NG', 'CR', 'GD', 'SR', 'AF', 'RI', 'PD', 'PT']:
    rows = q(f"""
        SELECT 
            toString(min(oi_change)),
            toString(max(oi_change)),
            toString(avg(oi_change)),
            toString(count())
        FROM moex.supercandles_fo 
        WHERE ticker = '{t}' AND oi_change != 0
    """)
    if rows and rows[0]:
        r = rows[0]
        print(f"  {t}: min={r[0]}, max={r[1]}, avg={float(r[2]):.1f}, non-zero={r[3]}")

print("\nDone!")
