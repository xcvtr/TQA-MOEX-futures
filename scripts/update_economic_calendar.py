#!/home/user/venvs/tqa/main/bin/python
"""
Investing.com Economic Calendar Importer.
Парсит данные из __NEXT_DATA__ (cur bypasses Cloudflare) и сохраняет в PostgreSQL.
Запуск: python scripts/update_economic_calendar.py
Добавляет новые события, обновляет existing по (event_code, event_time, country_code).
"""
import json, re, sys, time
from datetime import datetime, timezone, timedelta
import requests
import psycopg2
import psycopg2.extras  # for execute_values

DB_HOST = '10.0.0.64'
DB_NAME = 'forex'
DB_USER = 'postgres'
DB_PASS = 'postgres'

# ─── Currency → country_code mapping ───
CURRENCY_TO_COUNTRY = {
    'USD': 'US', 'EUR': 'EU', 'GBP': 'GB', 'JPY': 'JP',
    'AUD': 'AU', 'CAD': 'CA', 'CHF': 'CH', 'NZD': 'NZ',
    'CNY': 'CN', 'INR': 'IN', 'BRL': 'BR', 'ZAR': 'ZA',
    'MXN': 'MX', 'SGD': 'SG', 'HKD': 'HK', 'KRW': 'KR',
    'NOK': 'NO', 'SEK': 'SE', 'TRY': 'TR', 'PLN': 'PL',
    'RUB': 'RU', 'CZK': 'CZ', 'HUF': 'HU', 'ILS': 'IL',
    'CLP': 'CL', 'COP': 'CO', 'PHP': 'PH', 'MYR': 'MY',
    'IDR': 'ID', 'THB': 'TH', 'TWD': 'TW', 'ARS': 'AR',
    'NGN': 'NG', 'EGP': 'EG', 'PKR': 'PK', 'BDT': 'BD',
    'VND': 'VN', 'KES': 'KE', 'DZD': 'DZ', 'MAD': 'MA',
    'AED': 'AE', 'SAR': 'SA', 'QAR': 'QA', 'OMR': 'OM',
    'BHD': 'BH', 'KWD': 'KW', 'JOD': 'JO', 'LBP': 'LB',
    'GHS': 'GH', 'TZS': 'TZ', 'UGX': 'UG', 'ZMW': 'ZM',
    'XAU': 'XL',  # Gold
    'XAG': 'XL',  # Silver
    'XPD': 'XL',  # Palladium
    'XPT': 'XL',  # Platinum
    'BTC': 'XX',  # Bitcoin
    'ETH': 'XX',  # Ethereum
    'Oil': 'XX',  # Oil
    'NG': 'XX',   # Natural Gas
}

# ─── Importance mapping ───
IMPORTANCE_MAP = {'1': 3, '2': 2, '3': 1}  # 3 stars = importance 3, 1 star = importance 1


def fetch_page(date_start: str, date_end: str) -> dict | None:
    """Fetch investing.com calendar page and extract __NEXT_DATA__ JSON."""
    url = f"https://www.investing.com/economic-calendar/?dateFrom={date_start}&dateTo={date_end}"
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9,ru;q=0.8',
        'Referer': 'https://www.investing.com/',
    }
    
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    
    # Extract __NEXT_DATA__ JSON
    match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', resp.text, re.DOTALL)
    if not match:
        print("ERROR: __NEXT_DATA__ not found in page")
        return None
    
    return json.loads(match.group(1))


def extract_events(next_data: dict) -> list[dict]:
    """Extract economic calendar events from Next.js page data."""
    events = []
    
    # Navigate: props -> pageProps -> calendarEventsByDate
    try:
        calendar = next_data['props']['pageProps']['calendarEventsByDate']
    except KeyError:
        print("ERROR: calendarEventsByDate not found in __NEXT_DATA__")
        print(f"Available keys: {list(next_data.get('props', {}).get('pageProps', {}).keys())}")
        return []
    
    for date_str, day_events in calendar.items():
        for ev in day_events:
            try:
                event_time = ev.get('time') or ev.get('actual_time')
                if not event_time:
                    continue
                
                # Parse time
                dt = datetime.fromisoformat(event_time.replace('Z', '+00:00'))
                
                currency = ev.get('currency', '')
                country = CURRENCY_TO_COUNTRY.get(currency, currency)
                
                # Importance
                imp_str = ev.get('importance', '1')
                importance = IMPORTANCE_MAP.get(imp_str, 2)
                
                # Generate a stable event_code
                event_name = ev.get('event', '').strip()
                event_id = ev.get('eventId', 0)
                event_code = ev.get('event_code') or f"investing-{event_id}"
                
                # Values
                actual = ev.get('actual')
                forecast = ev.get('forecast')
                previous = ev.get('previous')
                
                def parse_num(v):
                    if v is None or v == '':
                        return None
                    try:
                        # Remove non-numeric except . and -
                        cleaned = re.sub(r'[^0-9.\-]', '', str(v))
                        return float(cleaned) if cleaned else None
                    except (ValueError, TypeError):
                        return None
                
                events.append({
                    'event_time': dt,
                    'country_code': country,
                    'importance': importance,
                    'event_code': event_code,
                    'name': event_name,
                    'actual_value': parse_num(actual),
                    'forecast_value': parse_num(forecast),
                    'prev_value': parse_num(previous),
                    'source_url': f"https://www.investing.com/economic-calendar/",
                })
            except Exception as e:
                print(f"  Skipping event: {e}")
                continue
    
    return events


def upsert_events(conn, events: list[dict]) -> tuple[int, int]:
    """Insert or update events in PostgreSQL. Returns (inserted, updated)."""
    if not events:
        return (0, 0)
    
    inserted = 0
    updated = 0
    
    with conn.cursor() as cur:
        for ev in events:
            # Try to find existing event by (event_code, event_time, country_code)
            cur.execute("""
                SELECT id FROM economic_calendar 
                WHERE event_code = %s 
                  AND event_time = %s 
                  AND country_code = %s
            """, (ev['event_code'], ev['event_time'], ev['country_code']))
            
            existing = cur.fetchone()
            
            if existing:
                # Update
                cur.execute("""
                    UPDATE economic_calendar 
                    SET importance = %s,
                        name = %s,
                        actual_value = COALESCE(%s, actual_value),
                        forecast_value = COALESCE(%s, forecast_value),
                        prev_value = COALESCE(%s, prev_value),
                        source_url = %s
                    WHERE id = %s
                """, (
                    ev['importance'], ev['name'],
                    ev['actual_value'], ev['forecast_value'], ev['prev_value'],
                    ev['source_url'],
                    existing[0]
                ))
                updated += 1
            else:
                # Insert
                cur.execute("""
                    INSERT INTO economic_calendar 
                        (event_time, country_code, importance, event_code, name,
                         actual_value, forecast_value, prev_value, source_url)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    ev['event_time'], ev['country_code'], ev['importance'],
                    ev['event_code'], ev['name'],
                    ev['actual_value'], ev['forecast_value'], ev['prev_value'],
                    ev['source_url']
                ))
                inserted += 1
        
        conn.commit()
    
    return (inserted, updated)


def main():
    conn = psycopg2.connect(host=DB_HOST, dbname=DB_NAME, user=DB_USER, password=DB_PASS)
    
    # Determine date range: from last event in DB + 1 day, to today+2 weeks
    with conn.cursor() as cur:
        cur.execute("SELECT max(event_time) FROM economic_calendar")
        last_date = cur.fetchone()[0]
    
    if last_date is None:
        start_date = datetime(2026, 1, 1, tzinfo=timezone.utc)
    else:
        start_date = last_date - timedelta(days=1)  # overlap for safety
    
    end_date = datetime.now(timezone.utc) + timedelta(days=14)
    
    date_from = start_date.strftime('%Y-%m-%d')
    date_to = end_date.strftime('%Y-%m-%d')
    
    print(f"Fetching calendar: {date_from} → {date_to}")
    
    # Fetch in monthly chunks to avoid large pages
    current = start_date
    total_inserted = 0
    total_updated = 0
    
    while current < end_date:
        chunk_end = min(current + timedelta(days=60), end_date)
        cf = current.strftime('%Y-%m-%d')
        ct = chunk_end.strftime('%Y-%m-%d')
        
        print(f"  Chunk: {cf} → {ct}...", end=' ', flush=True)
        
        try:
            next_data = fetch_page(cf, ct)
            if not next_data:
                print("FAILED (no data)")
                current = chunk_end
                continue
            
            events = extract_events(next_data)
            if not events:
                print(f"no events")
                current = chunk_end
                continue
            
            ins, upd = upsert_events(conn, events)
            total_inserted += ins
            total_updated += upd
            print(f"{ins} new, {upd} updated")
            
        except Exception as e:
            print(f"ERROR: {e}")
        
        current = chunk_end
        time.sleep(1)  # rate limit
    
    print(f"\nDone! Total: {total_inserted} inserted, {total_updated} updated")
    print(f"Date range: {date_from} → {date_to}")
    
    # Stats
    with conn.cursor() as cur:
        cur.execute("""
            SELECT country_code, count(*) 
            FROM economic_calendar 
            WHERE event_time >= %s 
            GROUP BY country_code 
            ORDER BY count(*) DESC
        """, (date_from,))
        stats = cur.fetchall()
        print(f"\nNew events by country:")
        for cc, cnt in stats:
            print(f"  {cc}: {cnt}")
    
    conn.close()


if __name__ == '__main__':
    main()
