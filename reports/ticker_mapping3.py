#!/usr/bin/env python3
"""Phase 3: Final comprehensive mapping with correct column names."""

import clickhouse_connect
from collections import defaultdict

HOST = '10.0.0.60'
DB = 'moex'

client = clickhouse_connect.get_client(host=HOST, database=DB)

def q(sql, label):
    print(f"[{label}] ...", end=" ", flush=True)
    try:
        rows = client.query(sql).result_rows
        print(f"{len(rows)} rows")
        return rows
    except Exception as e:
        print(f"ERROR: {e}")
        return []

# Helper
def base_ticker(secid):
    if len(secid) > 2:
        return secid[:-2]
    return secid

# ---------------------------------------------------------------------------
# 1. tradestats_fo base tickers (P80)
# ---------------------------------------------------------------------------
ts_rows = q("SELECT DISTINCT secid FROM tradestats_fo", "tradestats_fo secids")
ts_secids = sorted(set(r[0] for r in ts_rows))
ts_bases = sorted(set(base_ticker(s) for s in ts_secids))
print(f"  -> {len(ts_bases)} base tickers")

# ---------------------------------------------------------------------------
# 2. prices_5m symbols
# ---------------------------------------------------------------------------
p5m_rows = q("SELECT DISTINCT symbol FROM prices_5m", "prices_5m symbols")
p5m_symbols = sorted(set(r[0] for r in p5m_rows))
print(f"  -> {len(p5m_symbols)} symbols")

# ---------------------------------------------------------------------------
# 3. prices_5m_oi symbols
# ---------------------------------------------------------------------------
p5moi_rows = q("SELECT DISTINCT symbol FROM prices_5m_oi", "prices_5m_oi symbols")
p5moi_symbols = sorted(set(r[0] for r in p5moi_rows))
print(f"  -> {len(p5moi_symbols)} symbols")

# ---------------------------------------------------------------------------
# 4. supercandles_fo tickers
# ---------------------------------------------------------------------------
sc_t_rows = q("SELECT DISTINCT ticker FROM supercandles_fo", "supercandles_fo tickers")
sc_tickers = sorted(set(r[0] for r in sc_t_rows))
print(f"  -> {len(sc_tickers)} tickers")

# Also get secids (for direct matching with tradestats_fo secids)
sc_sec_rows = q("SELECT DISTINCT secid FROM supercandles_fo", "supercandles_fo secids")
sc_secids = sorted(set(r[0] for r in sc_sec_rows))
# Need to also match contract-level: supercandles_fo secids like 'AFH0' -> base 'AF'
sc_bases_from_secid = sorted(set(base_ticker(s) for s in sc_secids))
print(f"  -> {len(sc_bases_from_secid)} bases from secid")

# ---------------------------------------------------------------------------
# 5. futoi tickers
# ---------------------------------------------------------------------------
futoi_rows = q("SELECT DISTINCT ticker FROM futoi", "futoi tickers")
futoi_tickers = sorted(set(r[0] for r in futoi_rows))
print(f"  -> {len(futoi_tickers)} tickers")

# ---------------------------------------------------------------------------
# Build the mapping
# ---------------------------------------------------------------------------
print()
print("=" * 130)
print(f"{'P80 Base':<12} {'p5m':<8} {'p5m_oi':<8} {'sc_ticker':<10} {'sc_secid':<8} {'futoi':<8} {'ts_has_oi':<10}| {'Match type'}")
print("=" * 130)

def any_secid_starts_with(secids, base):
    for s in secids:
        if s.startswith(base):
            return True
    return False

matched_super = 0
matched_p5m = 0
matched_p5moi = 0
matched_futoi = 0
matched_any = 0
unmatched = []

for base in ts_bases:
    in_p5m = base in p5m_symbols
    in_p5moi = base in p5moi_symbols
    in_sc_ticker = base in sc_tickers
    in_sc_secid = any_secid_starts_with(sc_secids, base)
    in_futoi = base in futoi_tickers
    
    # Determine match type
    matches = []
    if in_p5m: matches.append("p5m")
    if in_p5moi: matches.append("p5moi")
    if in_sc_ticker: matches.append("sc_tkr")
    if in_sc_secid: matches.append("sc_secid")
    if in_futoi: matches.append("futoi")
    match_type = "+".join(matches) if matches else "NONE"
    
    p5m_s = "YES" if in_p5m else "."
    p5moi_s = "YES" if in_p5moi else "."
    sc_t_s = "YES" if in_sc_ticker else "."
    sc_s_s = "YES" if in_sc_secid else "."
    futoi_s = "YES" if in_futoi else "."
    
    if in_p5m: matched_p5m += 1
    if in_p5moi: matched_p5moi += 1
    if in_sc_ticker: matched_super += 1
    if in_futoi: matched_futoi += 1
    
    if in_p5m or in_p5moi or in_sc_ticker or in_sc_secid or in_futoi:
        matched_any += 1
    else:
        unmatched.append(base)
    
    print(f"{base:<12} {p5m_s:<8} {p5moi_s:<8} {sc_t_s:<10} {sc_s_s:<8} {futoi_s:<8} {'YES':<10}| {match_type}")

print("=" * 130)

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print()
print("=" * 60)
print("SUMMARY")
print("=" * 60)
print(f"Total P80 base tickers from tradestats_fo:  {len(ts_bases)}")
print()
print(f"prices_5m symbols ({len(p5m_symbols)}):          {matched_p5m} matched P80 bases")
print(f"prices_5m_oi symbols ({len(p5moi_symbols)}):       {matched_p5moi} matched P80 bases")
print(f"supercandles_fo tickers ({len(sc_tickers)}):     {matched_super} matched P80 bases")
print(f"futoi tickers ({len(futoi_tickers)}):             {matched_futoi} matched P80 bases")
print()
print(f"Matched in at least one source:              {matched_any} / {len(ts_bases)}")
print(f"UNMATCHED in any source:                     {len(unmatched)}")
if unmatched:
    print(f"  Unmatched: {', '.join(unmatched)}")

# Also show prices_5m_oi symbols that are NOT in P80 base tickers
extra_p5moi = [s for s in p5moi_symbols if s not in ts_bases]
if extra_p5moi:
    print(f"\nprices_5m_oi symbols NOT in P80 bases ({len(extra_p5moi)}): {', '.join(extra_p5moi)}")

# Show which P80 bases only have OI from tradestats_fo itself (no other source)
ts_only = [b for b in ts_bases if not (b in p5m_symbols or b in p5moi_symbols or b in sc_tickers or any_secid_starts_with(sc_secids, b) or b in futoi_tickers)]
if ts_only:
    print(f"\nP80 bases ONLY in tradestats_fo (no other table): {len(ts_only)}")
    print(f"  {', '.join(ts_only)}")

# ---------------------------------------------------------------------------
# Check: Can we map tradestats_fo secids directly to supercandles_fo secids?
# ---------------------------------------------------------------------------
print()
print("=" * 60)
print("SECID-LEVEL OVERLAP CHECK")
print("=" * 60)
# Check if tradestats_fo secids appear directly in supercandles_fo
direct_overlap = [s for s in ts_secids if s in sc_secids]
print(f"tradestats_fo secids that appear DIRECTLY in supercandles_fo: {len(direct_overlap)}")
if direct_overlap:
    print(f"  Samples: {', '.join(direct_overlap[:20])}")

# Check if we can map via contract column in prices_5m
p5m_contract_rows = q("SELECT DISTINCT contract, symbol FROM prices_5m LIMIT 50", "prices_5m contract samples")
print("\nSample contract->symbol mappings:")
for r in p5m_contract_rows[:20]:
    print(f"  {r[0]:30s} -> {r[1]}")

# ---------------------------------------------------------------------------
# Check what P80 "universe" actually looks like vs P80 filter
# ---------------------------------------------------------------------------
print()
print("=" * 60)
print("P80 SIGNAL FILTER — What the CVD script actually uses")
print("=" * 60)
# The P80 script queries tradestats_fo for volatility and signal filtering.
# Let's see roughly how many distinct secids per base ticker exist
print("Sample secids per base ticker from tradestats_fo:")
from collections import Counter
base_counts = Counter(base_ticker(s) for s in ts_secids)
for base, count in sorted(base_counts.items(), key=lambda x: -x[1])[:30]:
    print(f"  {base:<12} {count} secids (e.g. {', '.join([s for s in ts_secids if s.startswith(base)][:4])})")

print(f"\nBase tickers with < 12 secids (likely fewer expiry series):")
for base, count in sorted(base_counts.items()):
    if count < 12:
        print(f"  {base:<12} {count} secids")

client.close()
print("\nDone.")
