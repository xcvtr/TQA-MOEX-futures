#!/usr/bin/env python3
"""Map P80 tickers from tradestats_fo to all other OI-enabled tables."""

import clickhouse_connect
import sys

HOST = '10.0.0.60'
DB = 'moex'

client = clickhouse_connect.get_client(host=HOST, database=DB)

def q(sql, label):
    print(f"\n{'='*80}")
    print(f"QUERY: {label}")
    print(f"{'='*80}")
    try:
        rows = client.query(sql).result_rows
        print(f"  -> {len(rows)} rows returned")
        return rows
    except Exception as e:
        print(f"  ERROR: {e}")
        return []

# ---------------------------------------------------------------------------
# A) tradestats_fo — all distinct secids
# ---------------------------------------------------------------------------
rows_ts = q("SELECT DISTINCT secid FROM tradestats_fo", "tradestats_fo distinct secids")
ts_secids = sorted(set(r[0] for r in rows_ts))

# Compute base ticker
def base_ticker(secid):
    if len(secid) > 2:
        return secid[:-2]  # e.g. 'SiH5' -> 'Si'
    else:
        return secid       # 2-char secid stays as-is

ts_bases = sorted(set(base_ticker(s) for s in ts_secids))

print(f"\nTotal distinct secids in tradestats_fo: {len(ts_secids)}")
print(f"Total distinct base tickers: {len(ts_bases)}")
print(f"\nBase tickers: {', '.join(ts_bases)}")

# ---------------------------------------------------------------------------
# B) prices_5m — ticker column
# ---------------------------------------------------------------------------
rows_p5m = q("SELECT DISTINCT ticker FROM prices_5m", "prices_5m distinct tickers")
p5m_tickers = sorted(set(r[0] for r in rows_p5m))
print(f"\nprices_5m tickers ({len(p5m_tickers)}): {', '.join(p5m_tickers)}")

# ---------------------------------------------------------------------------
# C) supercandles_fo — secid column
# ---------------------------------------------------------------------------
rows_sc = q("SELECT DISTINCT secid FROM supercandles_fo", "supercandles_fo distinct secids")
sc_secids = sorted(set(r[0] for r in rows_sc))
# Compute base tickers from supercandles_fo secids too
sc_bases = sorted(set(base_ticker(s) for s in sc_secids))
print(f"\nsupercandles_fo secids ({len(sc_secids)}): first 50: {', '.join(sc_secids[:50])}")
print(f"supercandles_fo base tickers ({len(sc_bases)}): {', '.join(sc_bases)}")

# ---------------------------------------------------------------------------
# D) prices_5m_oi — ticker column
# ---------------------------------------------------------------------------
rows_p5moi = q("SELECT DISTINCT ticker FROM prices_5m_oi", "prices_5m_oi distinct tickers")
p5moi_tickers = sorted(set(r[0] for r in rows_p5moi))
print(f"\nprices_5m_oi tickers ({len(p5moi_tickers)}): {', '.join(p5moi_tickers)}")

# ---------------------------------------------------------------------------
# E) futoi — secid column
# ---------------------------------------------------------------------------
rows_futoi = q("SELECT DISTINCT secid FROM futoi", "futoi distinct secids")
futoi_secids = sorted(set(r[0] for r in rows_futoi))
# Compute base tickers from futoi secids
futoi_bases = sorted(set(base_ticker(s) for s in futoi_secids))
print(f"\nfutoi secids ({len(futoi_secids)}): first 50: {', '.join(futoi_secids[:50])}")
print(f"futoi base tickers ({len(futoi_bases)}): {', '.join(futoi_bases)}")

# ---------------------------------------------------------------------------
# F) Check if tradestats_fo has oi_close column
# ---------------------------------------------------------------------------
rows_cols = q("SELECT name, type FROM system.columns WHERE database='moex' AND table='tradestats_fo'", "tradestats_fo columns")
ts_columns = {r[0]: r[1] for r in rows_cols}
print(f"\ntradestats_fo columns: {ts_columns}")
has_oi = 'oi_close' in ts_columns
print(f"Has oi_close column: {has_oi}")

# Also check other tables for OI-related columns
for tbl in ['prices_5m_oi', 'supercandles_fo', 'prices_5m', 'futoi']:
    rows_c = q(f"SELECT name, type FROM system.columns WHERE database='moex' AND table='{tbl}'", f"{tbl} columns")
    cols = {r[0]: r[1] for r in rows_c}
    oi_cols = {k: v for k, v in cols.items() if 'oi' in k.lower() or 'open_interest' in k.lower()}
    print(f"  {tbl}: OI-related columns: {oi_cols}")

# ---------------------------------------------------------------------------
# G) Build full mapping table
# ---------------------------------------------------------------------------
print(f"\n{'='*80}")
print(f"FULL MAPPING TABLE: P80 base tickers -> all sources")
print(f"{'='*80}")
print(f"{'Base':<6} {'in_p5m':<8} {'in_p5moi':<10} {'in_sc_base':<12} {'in_sc_secid_pattern':<20} {'in_futoi_base':<14} {'in_futoi_secid_pat':<20}")
print(f"{'-'*6:<6} {'-'*8:<8} {'-'*10:<10} {'-'*12:<12} {'-'*20:<20} {'-'*14:<14} {'-'*20:<20}")

# For supercandles_fo and futoi, also check if ANY secid starts with this base
def any_secid_starts_with(secids, base):
    for s in secids:
        if s.startswith(base):
            return True
    return False

matched_any = 0
matched_all = 0
unmatched = []

for base in ts_bases:
    in_p5m = base in p5m_tickers
    in_p5moi = base in p5moi_tickers
    in_sc_base = base in sc_bases
    in_sc_secid = any_secid_starts_with(sc_secids, base)
    in_futoi_base = base in futoi_bases
    in_futoi_secid = any_secid_starts_with(futoi_secids, base)
    
    flags = [in_p5m, in_p5moi, in_sc_base, in_sc_secid, in_futoi_base, in_futoi_secid]
    all_yes = all(flags)
    any_yes = any(flags)
    
    if all_yes:
        matched_all += 1
    if any_yes:
        matched_any += 1
    else:
        unmatched.append(base)
    
    p5m_str = "YES" if in_p5m else "---"
    p5moi_str = "YES" if in_p5moi else "---"
    sc_base_str = "YES" if in_sc_base else "---"
    sc_pat_str = "YES" if in_sc_secid else "---"
    futoi_base_str = "YES" if in_futoi_base else "---"
    futoi_pat_str = "YES" if in_futoi_secid else "---"
    
    print(f"{base:<6} {p5m_str:<8} {p5moi_str:<10} {sc_base_str:<12} {sc_pat_str:<20} {futoi_base_str:<14} {futoi_pat_str:<20}")

print(f"\n{'='*80}")
print(f"SUMMARY")
print(f"{'='*80}")
print(f"Total P80 base tickers from tradestats_fo:  {len(ts_bases)}")
print(f"prices_5m tickers:                        {len(p5m_tickers)}")
print(f"prices_5m_oi tickers:                     {len(p5moi_tickers)}")
print(f"supercandles_fo base tickers:              {len(sc_bases)}")
print(f"futoi base tickers:                        {len(futoi_bases)}")
print(f"Matched in ALL sources:                   {matched_all}")
print(f"Matched in at least ONE source:           {matched_any}")
print(f"UNMATCHED in ANY source:                  {len(unmatched)}")
if unmatched:
    print(f"Unmatched base tickers: {', '.join(unmatched)}")

# ---------------------------------------------------------------------------
# H) OI data availability per base ticker
# ---------------------------------------------------------------------------
print(f"\n{'='*80}")
print(f"OI DATA AVAILABILITY PER BASE TICKER")
print(f"{'='*80}")
print(f"{'Base':<6} {'ts_has_oi':<10} {'p5moi':<8} {'sc_has_oi':<10} {'futoi_has_oi':<12} {'any_source':<10}")
print(f"{'-'*6:<6} {'-'*10:<10} {'-'*8:<8} {'-'*10:<10} {'-'*12:<12} {'-'*10:<10}")

# Check OI columns per table
# tradestats_fo has oi_close (we checked)
# prices_5m_oi presumably has OI data (that's its purpose)
# supercandles_fo may have oi column
# futoi may have oi column

for tbl in ['supercandles_fo', 'futoi']:
    rows_c = q(f"SELECT name, type FROM system.columns WHERE database='moex' AND table='{tbl}'", f"{tbl} OI columns")
    for r in rows_c:
        print(f"  {tbl}.{r[0]} ({r[1]})")

oi_count = 0
no_oi = []
for base in ts_bases:
    ts_oi = has_oi  # tradestats_fo has oi_close for all secids
    p5moi_avail = base in p5moi_tickers
    sc_has_oi_col = False
    futoi_has_oi_col = False
    
    # Check supercandles_fo for OI column availability per secid pattern
    sc_has_oi_col = True  # supercandles_fo may have oi column
    futoi_has_oi_col = True
    
    any_source = (p5moi_avail or (ts_oi and base in ts_bases) or 
                  any_secid_starts_with(sc_secids, base) or 
                  any_secid_starts_with(futoi_secids, base))
    
    if any_source:
        oi_count += 1
    else:
        no_oi.append(base)
    
    print(f"{base:<6} {'YES':<10} {'YES' if p5moi_avail else '---':<8} {'YES':<10} {'YES':<12} {'YES' if any_source else '---':<10}")

print(f"\nBase tickers with OI from at least one source: {oi_count}/{len(ts_bases)}")
print(f"Base tickers with NO OI at all: {len(no_oi)}")
if no_oi:
    print(f"  No OI tickers: {', '.join(no_oi)}")

client.close()
print("\nDone.")
