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

from . import SCAN_SYMBOLS, DEFAULT_CONFIG, TICKERS, DB_CREDENTIALS, REVERSION_TICKERS, DEFAULT_REVERSION_CONFIG, OB_TICKERS, DEFAULT_OB_CONFIG, VWAP_TICKERS, DEFAULT_VWAP_CONFIG
from .engine import detect_signals
from .scanner import load_data, scan_all, format_signal
from .tracker import load_positions, check_exits, open_position, get_stats
from .alerts import send_alert, format_signal_alert, format_position_update, format_stats
from .reversion_engine import detect_mean_reversion_signals, load_price_data
from .ob_engine import detect_order_block_signals, load_price_data as ob_load_price_data
from .vwap_engine import detect_vwap_signals, load_price_data as vwap_load_price_data


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

    # 2. Build configs and scan (Volume Surge)
    configs = {}
    for sym in SCAN_SYMBOLS:
        cfg = TICKERS.get(sym, {})
        if cfg.get('enabled', True):
            configs[sym] = {**DEFAULT_CONFIG, **cfg}

    signals = scan_all(configs)
    vs_count = len(signals)

    # 3. Reversion scanning
    rev_signals = []
    for sym, cfg in REVERSION_TICKERS.items():
        if not cfg.get('enabled', True):
            continue
        rev_cfg = {**DEFAULT_REVERSION_CONFIG, **cfg}
        try:
            price_rows = load_price_data(sym, days=30)
            if price_rows and len(price_rows) >= 50:
                sigs = detect_mean_reversion_signals(sym, price_rows, rev_cfg)
                rev_signals.extend(sigs)
        except Exception as e:
            alerts.append(f"[WARN] Reversion scan {sym} error: {e}")

    rev_count = len(rev_signals)

    # 3b. Order Block scanning
    ob_signals = []
    for sym, cfg in OB_TICKERS.items():
        if not cfg.get('enabled', True):
            continue
        ob_cfg = {**DEFAULT_OB_CONFIG, **cfg}
        try:
            price_rows = ob_load_price_data(sym, days=14)  # OB needs less history, but data lags up to 1 week
            if price_rows and len(price_rows) >= 50:
                sigs = detect_order_block_signals(sym, price_rows, ob_cfg)
                ob_signals.extend(sigs)
        except Exception as e:
            alerts.append(f"[WARN] OB scan {sym} error: {e}")

    ob_count = len(ob_signals)

    # 3c. VWAP Deviation Reversion scanning
    vwap_signals = []
    for sym, cfg in VWAP_TICKERS.items():
        if not cfg.get('enabled', True):
            continue
        vwap_cfg = {**DEFAULT_VWAP_CONFIG, **cfg}
        try:
            price_rows = vwap_load_price_data(sym, days=30)
            if price_rows and len(price_rows) >= 50:
                sigs = detect_vwap_signals(sym, price_rows, vwap_cfg)
                vwap_signals.extend(sigs)
        except Exception as e:
            alerts.append(f"[WARN] VWAP scan {sym} error: {e}")

    vwap_count = len(vwap_signals)

    # 4. Merge all signals
    signals.extend(rev_signals)
    signals.extend(ob_signals)
    signals.extend(vwap_signals)

    # 5. Apply ADX regime filter — skip if filters module doesn't exist
    try:
        from trading_bot.filters import calc_adx
        from trading_bot.scanner import load_data
        adx_data_cache = {}
        # Pre-load for all tickers that might be checked
        all_adx_tickers = set()
        for sig in signals:
            tk = sig['ticker']
            cfg = TICKERS.get(tk, REVERSION_TICKERS.get(tk, OB_TICKERS.get(tk, VWAP_TICKERS.get(tk, {}))))
            if cfg.get('adx_filter', False):
                all_adx_tickers.add(tk)
        for tk in all_adx_tickers:
            adx_data_cache[tk] = load_data(tk, days=30)

        adx_filtered_signals = []
        for sig in signals:
            tk = sig['ticker']
            cfg = TICKERS.get(tk, REVERSION_TICKERS.get(tk, OB_TICKERS.get(tk, VWAP_TICKERS.get(tk, {}))))
            if cfg.get('adx_filter', False):
                rows = adx_data_cache.get(tk, [])
                if rows and len(rows) > 20:
                    close = [float(r[5]) for r in rows]
                    adx = calc_adx(close, 14)
                    # Use last ADX as proxy (no idx available for VS signals)
                    adx_val = adx[-1] if adx else 0
                    if adx_val > cfg.get('adx_threshold', 20):
                        adx_filtered_signals.append(sig)
            else:
                adx_filtered_signals.append(sig)
        signals = adx_filtered_signals
    except ImportError:
        pass  # filters module not installed — skip ADX filtering

    # Filter: only signals from last 30 minutes (recent, not historical)
    from datetime import timedelta
    cutoff = now - timedelta(minutes=30)
    signals = [s for s in signals if s.get('time', '')[:16] >= cutoff.strftime('%Y-%m-%dT%H:%M')]

    # 6. Check exits (horizon/stop)
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
        cfg = TICKERS.get(tk, REVERSION_TICKERS.get(tk, OB_TICKERS.get(tk, VWAP_TICKERS.get(tk, {}))))
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

    # 9. Status line
    sig_count = len(signals)
    open_count = sum(1 for p in load_positions() if p['status'] == 'open')
    status = f"[SCAN] VS: {vs_count} sig | Reversion: {rev_count} sig | OB: {ob_count} sig | VWAP: {vwap_count} sig | Open: {open_count} | Новых: {opened}"
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
