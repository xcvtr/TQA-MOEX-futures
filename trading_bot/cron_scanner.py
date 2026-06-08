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

from . import SCAN_SYMBOLS, DEFAULT_CONFIG, TICKERS, DB_CREDENTIALS, REVERSION_TICKERS, DEFAULT_REVERSION_CONFIG, OB_TICKERS, DEFAULT_OB_CONFIG, VWAP_TICKERS, DEFAULT_VWAP_CONFIG, OI_DIVERGENCE_TICKERS, DEFAULT_OI_DIVERGENCE_CONFIG
from .engine import detect_signals, detect_signals_limit
from .scanner import load_data, scan_all, format_signal
from .tracker import load_positions, check_exits, open_position, get_stats
from .alerts import send_alert, format_signal_alert, format_position_update, format_stats
from .reversion_engine import detect_mean_reversion_signals, detect_mean_reversion_signals_limit, load_price_data
from .ob_engine import detect_order_block_signals, load_price_data as ob_load_price_data
from .vwap_engine import detect_vwap_signals, detect_vwap_signals_limit, load_price_data as vwap_load_price_data
from .new_strategies import detect_oi_divergence_signals, detect_oi_divergence_signals_limit, load_ohlcv, load_oi, merge_ohlcv_oi


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

    # 2. Build configs and scan (Volume Surge) — limit entry
    configs = {}
    for sym in SCAN_SYMBOLS:
        cfg = TICKERS.get(sym, {})
        if cfg.get('enabled', True):
            configs[sym] = {**DEFAULT_CONFIG, **cfg}

    # Use limit entry for VS with per-ticker TF
    import pandas as pd
    vs_signals = []
    for sym, cfg in configs.items():
        try:
            rows = load_data(sym, days=730)
            if not rows or len(rows) < 50:
                continue
            
            # Per-ticker timeframe resampling
            ticker_tf = cfg.get('tf', '5m')
            if ticker_tf != '5m':
                df = pd.DataFrame(rows, columns=['time','open','high','low','close','volume','fiz_buy','fiz_sell','yur_buy','yur_sell'])
                df['time'] = pd.to_datetime(df['time'])
                df.set_index('time', inplace=True)
                rule_map = {'15m': '15min', '30m': '30min', 'H1': '1h'}
                rule = rule_map.get(ticker_tf, ticker_tf)
                df = df.resample(rule).agg({
                    'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last',
                    'volume': 'sum', 'fiz_buy': 'sum', 'fiz_sell': 'sum',
                    'yur_buy': 'sum', 'yur_sell': 'sum',
                }).dropna()
                # Convert back to rows format
                rows = [(idx.isoformat(), r['open'], r['high'], r['low'], r['close'],
                        r['volume'], r['fiz_buy'], r['fiz_sell'], r['yur_buy'], r['yur_sell'])
                        for idx, r in df.iterrows()]
            
            sigs = detect_signals_limit(rows, cfg)
            vs_signals.extend(sigs)
        except Exception as e:
            alerts.append(f"[WARN] VS scan {sym} error: {e}")
    signals = vs_signals
    vs_count = len(signals)

    # 3. Reversion scanning
    rev_signals = []
    for sym, cfg in REVERSION_TICKERS.items():
        if not cfg.get('enabled', True):
            continue
        rev_cfg = {**DEFAULT_REVERSION_CONFIG, **cfg}
        try:
            price_rows = load_price_data(sym, days=730)
            if not price_rows or len(price_rows) < 50:
                continue
            
            # Per-ticker TF resampling
            ticker_tf = cfg.get('tf', '5m')
            if ticker_tf != '5m':
                df = pd.DataFrame(price_rows, columns=['time','open','high','low','close','volume'])
                df['time'] = pd.to_datetime(df['time'])
                df.set_index('time', inplace=True)
                rule_map = {'15m': '15min', '30m': '30min', 'H1': '1h'}
                rule = rule_map.get(ticker_tf, ticker_tf)
                df = df.resample(rule).agg({
                    'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum',
                }).dropna()
                price_rows = [(idx.isoformat(), r['open'], r['high'], r['low'], r['close'], r['volume'])
                              for idx, r in df.iterrows()]
            
            sigs = detect_mean_reversion_signals_limit(sym, price_rows, rev_cfg)
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
            price_rows = ob_load_price_data(sym, days=30)  # 30 days of 5m for H1 resample
            if price_rows and len(price_rows) >= 100:
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
            price_rows = vwap_load_price_data(sym, days=730)
            if price_rows and len(price_rows) >= 50:
                sigs = detect_vwap_signals_limit(sym, price_rows, vwap_cfg)
                vwap_signals.extend(sigs)
        except Exception as e:
            alerts.append(f"[WARN] VWAP scan {sym} error: {e}")

    vwap_count = len(vwap_signals)

    # 3d. OI Divergence scanning
    oi_div_signals = []
    for sym, cfg in OI_DIVERGENCE_TICKERS.items():
        if not cfg.get('enabled', True):
            continue
        oi_div_cfg = {**DEFAULT_OI_DIVERGENCE_CONFIG, **cfg}
        try:
            ohlcv = load_ohlcv(sym, days=730)
            oi = load_oi(sym, days=730)
            if ohlcv and oi and len(ohlcv) >= 50:
                merged = merge_ohlcv_oi(ohlcv, oi)
                if merged and len(merged) >= 50:
                    sigs = detect_oi_divergence_signals_limit(merged, oi_div_cfg)
                    oi_div_signals.extend(sigs)
        except Exception as e:
            alerts.append(f"[WARN] OI Div scan {sym} error: {e}")

    oi_div_count = len(oi_div_signals)

    # 4. Merge all signals
    signals.extend(rev_signals)
    signals.extend(ob_signals)
    signals.extend(vwap_signals)
    signals.extend(oi_div_signals)

    # 5. Apply ADX regime filter — skip if filters module doesn't exist
    try:
        from trading_bot.filters import calc_adx
        from trading_bot.scanner import load_data
        adx_data_cache = {}
        # Pre-load for all tickers that might be checked
        all_adx_tickers = set()
        for sig in signals:
            tk = sig['ticker']
            cfg = TICKERS.get(tk, REVERSION_TICKERS.get(tk, OB_TICKERS.get(tk, VWAP_TICKERS.get(tk, OI_DIVERGENCE_TICKERS.get(tk, {})))))
            if cfg.get('adx_filter', False):
                all_adx_tickers.add(tk)
        for tk in all_adx_tickers:
            adx_data_cache[tk] = load_data(tk, days=30)

        adx_filtered_signals = []
        for sig in signals:
            tk = sig['ticker']
            cfg = TICKERS.get(tk, REVERSION_TICKERS.get(tk, OB_TICKERS.get(tk, VWAP_TICKERS.get(tk, OI_DIVERGENCE_TICKERS.get(tk, {})))))
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

    # Filter: only recent signals (not historical)
    from datetime import timedelta
    cutoff = now - timedelta(minutes=30)
    ob_cutoff = now - timedelta(hours=6)  # OB on H1, needs wider window
    filtered = []
    for s in signals:
        if s.get('strategy') == 'order_block':
            if s.get('time', '')[:16] >= ob_cutoff.strftime('%Y-%m-%dT%H:%M'):
                filtered.append(s)
        else:
            if s.get('time', '')[:16] >= cutoff.strftime('%Y-%m-%dT%H:%M'):
                filtered.append(s)
    signals = filtered

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
        cfg = TICKERS.get(tk, REVERSION_TICKERS.get(tk, OB_TICKERS.get(tk, VWAP_TICKERS.get(tk, OI_DIVERGENCE_TICKERS.get(tk, {})))))
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
    status = f"[SCAN] VS: {vs_count} sig | Reversion: {rev_count} sig | OB: {ob_count} sig | VWAP: {vwap_count} sig | OI Div: {oi_div_count} sig | Open: {open_count} | Новых: {opened}"
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
