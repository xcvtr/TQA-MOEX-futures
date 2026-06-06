#!/usr/bin/env python3
"""
Cron entry point for trading bot.
Runs: scans tickers, checks exits, opens positions, sends alerts.

Usage:
    python -m trading_bot.cron_scanner              # full cycle
    python -m trading_bot.cron_scanner --healthcheck # just check DB
"""
import sys, os, json
from datetime import datetime

from . import SCAN_SYMBOLS, DEFAULT_CONFIG, TICKERS, DB_CREDENTIALS
from .engine import detect_signals
from .scanner import load_data, scan_all, format_signal
from .tracker import load_positions, check_exits, open_position, get_stats
from .alerts import send_alert, format_signal_alert, format_position_update, format_stats


def healthcheck() -> dict:
    """Check DB connectivity and module health."""
    result = {'status': 'ok', 'db': False, 'modules': True}
    try:
        import psycopg2
        conn = psycopg2.connect(**DB_CREDENTIALS)
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.close()
        conn.close()
        result['db'] = True
    except Exception as e:
        result['status'] = 'critical'
        result['db_error'] = str(e)
    return result


def main() -> str:
    """Full scan cycle. Returns alert string if any signals found."""
    alerts = []
    now = datetime.now()

    # 1. Load open positions
    positions = load_positions()
    alerts.append(f"[{now:%H:%M}] Открыто: {sum(1 for p in positions if p['status']=='open')}")

    # 2. Build configs and scan
    configs = {}
    for sym in SCAN_SYMBOLS:
        cfg = TICKERS.get(sym, {})
        if cfg.get('enabled', True):
            configs[sym] = {**DEFAULT_CONFIG, **cfg}

    signals = scan_all(configs)

    # Filter: only signals from last 30 minutes (recent, not historical)
    from datetime import timedelta
    cutoff = now - timedelta(minutes=30)
    signals = [s for s in signals if s.get('time', '')[:16] >= cutoff.strftime('%Y-%m-%dT%H:%M')]

    # 3. Check exits (horizon/stop)
    # Convert signals to format tracker expects
    signal_dicts = []
    for sig in signals:
        signal_dicts.append({
            'ticker': sig['ticker'],
            'symbol': sig['ticker'],
            'direction': sig['direction'],
            'entry': sig['entry'],
            'close': sig['entry'],
        })

    try:
        closed = check_exits(signal_dicts)
        for c in closed:
            alert = format_position_update(c)
            alerts.append(alert)
            send_alert(alert, 'close')
    except Exception as e:
        alerts.append(f"⚠ check_exits error: {e}")

    # 4. Open new positions for signals not already held
    active_symbols = {p['symbol'] for p in positions if p['status'] == 'open'}
    opened = 0
    for sig in signals:
        tk = sig['ticker']
        if tk in active_symbols:
            continue
        # Check if we already have a signal for this ticker
        cfg = TICKERS.get(tk, {})
        label = cfg.get('label', tk)
        go = cfg.get('go', 5000)
        horizon = cfg.get('horizon', 12)

        try:
            pos = open_position(
                symbol=tk,
                direction=sig['direction'],
                entry_price=sig['entry'],
                signal_time=sig['time'],
                horizon=horizon,
            )
            opened += 1
            alert = format_signal_alert(sig, label)
            alerts.append(alert)
            send_alert(alert, 'signal')
        except Exception as e:
            alerts.append(f"⚠ Open {tk} error: {e}")

    # 5. Status line
    sig_count = len(signals)
    open_count = sum(1 for p in load_positions() if p['status'] == 'open')
    status = f"[SCAN] Сигналов: {sig_count} | Открыто: {open_count} | Новых: {opened}"
    alerts.append(status)
    print("\n".join(alerts))
    
    return "\n".join(alerts)


if __name__ == '__main__':
    if '--healthcheck' in sys.argv:
        hc = healthcheck()
        if '--verbose' in sys.argv:
            print(json.dumps(hc, indent=2))
        else:
            print(f"status={hc['status']} db={'✅' if hc['db'] else '❌'}")
        sys.exit(0 if hc['status'] == 'ok' else 1)
    main()
