#!/usr/bin/env python3
"""
Get economic calendar from MT5 via Wine Python + MetaTrader5 API.
Outputs JSON with calendar events.
"""
import json
import sys
from datetime import datetime, timezone

try:
    import MetaTrader5 as mt5
except ImportError:
    print(json.dumps({"error": "MetaTrader5 not installed"}))
    sys.exit(1)

# Initialize MT5 connection
if not mt5.initialize():
    err = mt5.last_error()
    print(json.dumps({"error": f"MT5 init failed: {err}"}))
    sys.exit(1)

print(f"MT5 initialized, terminal info: {mt5.terminal_info().name}", file=sys.stderr)

# Get calendar
try:
    calendars = mt5.calendar_get()
    print(f"Got {len(calendars) if calendars else 0} calendar events", file=sys.stderr)
except Exception as e:
    print(json.dumps({"error": f"calendar_get failed: {e}"}), file=sys.stderr)
    mt5.shutdown()
    sys.exit(1)

if not calendars:
    print(json.dumps({"events": [], "count": 0}))
    mt5.shutdown()
    sys.exit(0)

events = []
for cal in calendars:
    ev = {
        "event_id": cal.event_id,
        "event_time": cal.time.isoformat() if hasattr(cal.time, 'isoformat') else str(cal.time),
        "country": cal.country if hasattr(cal, 'country') else "",
        "name": cal.name if hasattr(cal, 'name') else cal.event,
        "importance": cal.importance if hasattr(cal, 'importance') else 0,
        "actual_value": str(cal.actual_value) if hasattr(cal, 'actual_value') and cal.actual_value is not None else None,
        "forecast_value": str(cal.forecast_value) if hasattr(cal, 'forecast_value') and cal.forecast_value is not None else None,
        "prev_value": str(cal.prev_value) if hasattr(cal, 'prev_value') and cal.prev_value is not None else None,
    }
    
    # Handle different attribute names across MT5 versions
    for attr in ['event_code', 'event_type', 'sector', 'frequency']:
        if hasattr(cal, attr):
            ev[attr] = getattr(cal, attr)
    
    events.append(ev)

mt5.shutdown()

print(json.dumps({"events": events, "count": len(events)}, ensure_ascii=False))
