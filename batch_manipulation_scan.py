#!/usr/bin/env python3
"""
Batch Manipulation Scan — прогон по всем high-liquidity MOEX фьючерсам.

Собирает OI-сводку и все типы паттернов для каждого тикера,
выводит сводную таблицу и сохраняет CSV.

Usage:
    python3 services/MOEX_LOADER/batch_manipulation_scan.py
    python3 services/MOEX_LOADER/batch_manipulation_scan.py --days 30 --zscore 1.5
    python3 services/MOEX_LOADER/batch_manipulation_scan.py --csv ./scan_results.csv
"""

import sys, os, argparse
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from manipulation_search import (
    load_price_data, load_oi_data, prepare_data,
    load_price_daily, load_oi_daily, prepare_oi_daily,
    detect_all, oi_summary, resolve_symbol, ZSCORE_THRESHOLD
)

# 18 high-liquidity tickers (из price_history_5m.py)
HIGH_LIQUIDITY = sorted([
    "CNYRUBF", "CC", "Si", "BR", "NG", "IMOEXF", "BM", "VB",
    "SV", "NA", "USDRUBF", "MC", "GD", "GLDRUBF", "SR", "SS", "GZ", "GL",
])


def run_scan(tickers: list, days: int, zscore: float) -> list:
    """Прогнать все тикеры, вернуть список результатов."""
    results = []
    for t in tickers:
        sym = resolve_symbol(t)
        df_p = load_price_data(sym, days)
        if df_p.empty:
            results.append({'symbol': t, 'error': 'no price data'})
            continue

        df_o = load_oi_data(sym, days)
        df = prepare_data(df_p, df_o, sym)
        oi = oi_summary(df)

        # D1 data for OI-level patterns
        df_p_daily = load_price_daily(sym, days)
        df_o_daily = load_oi_daily(sym, days)
        df_daily = prepare_oi_daily(df_p_daily, df_o_daily, sym) if not df_p_daily.empty else None

        patterns = detect_all(df, zscore, use_oi=not df_o.empty, df_daily=df_daily)

        r = {
            'symbol': t,
            'n_candles': len(df_p),
            'has_oi': 1 if not df_o.empty else 0,
            'total': len(patterns),
            'oi_extreme': sum(1 for p in patterns if p['type'] == 'OI_EXTREME'),
            'flow_extreme': sum(1 for p in patterns if p['type'] == 'FLOW_EXTREME'),
            'oi_trap': sum(1 for p in patterns if p['type'] == 'OI_TRAP'),
            'oi_divergence': sum(1 for p in patterns if p['type'] == 'OI_DIVERGENCE'),
            'false_break': sum(1 for p in patterns if p['type'] == 'FALSE_BREAK'),
            'stop_hunt': sum(1 for p in patterns if p['type'] == 'STOP_HUNT'),
            'vol_climax': sum(1 for p in patterns if p['type'] == 'VOL_CLIMAX'),
            'bull': sum(1 for p in patterns if p['direction'] == 'BULL'),
            'bear': sum(1 for p in patterns if p['direction'] == 'BEAR'),
            'fiz_net': oi.get('fiz_net', 0),
            'fiz_bias': oi.get('fiz_bias', ''),
            'fiz_long_pct': oi.get('fiz_long_pct', 0),
        }
        # Forward return verification (FLOW_EXTREME + OI_EXTREME)
        verif = [p for p in patterns if p['type'] in ('FLOW_EXTREME', 'OI_EXTREME') and 'fwd_ret_1h' in p]
        r['verif_n'] = len(verif)
        r['verif_ok'] = sum(1 for p in verif if p.get('success'))
        if verif:
            r['verif_ret_1h'] = round(sum(p.get('fwd_ret_1h', 0) or 0 for p in verif) / len(verif), 2)
            r['verif_ret_3h'] = round(sum(p.get('fwd_ret_3h', 0) or 0 for p in verif if p.get('fwd_ret_3h') is not None) / max(1, sum(1 for p in verif if p.get('fwd_ret_3h') is not None)), 2)
            r['verif_ret_6h'] = round(sum(p.get('fwd_ret_6h', 0) or 0 for p in verif if p.get('fwd_ret_6h') is not None) / max(1, sum(1 for p in verif if p.get('fwd_ret_6h') is not None)), 2)
        else:
            r['verif_ret_1h'] = r['verif_ret_3h'] = r['verif_ret_6h'] = 0

        results.append(r)
        print(f"  {t:>10}  {r['total']:>5} паттернов  "
              f"Extr={r['oi_extreme']} Flow={r['flow_extreme']} "
              f"OK={r.get('verif_ok',0)}/{r.get('verif_n',0)} "
              f"1h={r.get('verif_ret_1h',0):+.1f}%  "
              f"FIZ={oi.get('fiz_net', 0):+,d}",
              flush=True)
    return results


def print_table(results: list):
    """Сводная таблица."""
    print(f"\n{'='*95}")
    hdr = (f"{'Ticker':>10} {'Свечи':>7} {'OI':>3} {'Всего':>6} "
           f"{'Extr':>5} {'Flow':>5} {'Trap':>5} {'OK%':>5} {'1h%':>6} {'3h%':>6} "
           f"{'BULL':>5} {'BEAR':>5} {'FIZ_net':>10} {'FIZ%':>6}")
    print(hdr)
    print('-' * len(hdr))
    for r in results:
        if r.get('error'):
            print(f"{r['symbol']:>10}  error: {r['error']}")
            continue
        fiz_str = f"{r['fiz_net']:+,d}"[:10]
        fiz_pct = f"{r['fiz_long_pct']:.0f}%" if r['fiz_long_pct'] else ''
        ok_pct = f"{r['verif_ok']}/{r['verif_n']}" if r.get('verif_n') else '-'
        r1 = f"{r.get('verif_ret_1h',0):+.1f}%" if r.get('verif_n') else '-'
        r3 = f"{r.get('verif_ret_3h',0):+.1f}%" if r.get('verif_n') else '-'
        print(f"{r['symbol']:>10} {r['n_candles']:>7} {r['has_oi']:>3} "
              f"{r['total']:>6} {r['oi_extreme']:>5} {r['flow_extreme']:>5} {r['oi_trap']:>5} "
              f"{ok_pct:>5} {r1:>6} {r3:>6} "
              f"{r['bull']:>5} {r['bear']:>5} "
              f"{fiz_str:>10} {fiz_pct:>6}")
    print('-' * 95)


def main():
    parser = argparse.ArgumentParser(description='MOEX Batch Manipulation Scan')
    parser.add_argument('--days', type=int, default=60, help='Глубина в днях')
    parser.add_argument('--zscore', type=float, default=ZSCORE_THRESHOLD,
                        help=f'Порог z-score (по умолч. {ZSCORE_THRESHOLD})')
    parser.add_argument('--csv', default='', help='Сохранить CSV')
    parser.add_argument('--tickers', default='',
                        help='Тикеры через запятую (по умолч. все 18)')
    args = parser.parse_args()

    tickers = [t.strip() for t in args.tickers.split(',')] if args.tickers else HIGH_LIQUIDITY

    print(f"\n{'=' * 95}")
    print(f"  MOEX Batch Manipulation Scan")
    print(f"  {len(tickers)} tickers, {args.days} days, z-score={args.zscore}")
    print(f"  {datetime.now():%Y-%m-%d %H:%M}")
    print(f"{'=' * 95}")

    results = run_scan(tickers, args.days, args.zscore)
    print_table(results)

    if args.csv:
        import csv
        keys = ['symbol', 'n_candles', 'has_oi', 'total',
                'oi_extreme', 'flow_extreme', 'oi_trap', 'oi_divergence',
                'false_break', 'stop_hunt', 'vol_climax',
                'bull', 'bear', 'fiz_net', 'fiz_bias', 'fiz_long_pct',
                'verif_n', 'verif_ok', 'verif_ret_1h', 'verif_ret_3h', 'verif_ret_6h']
        with open(args.csv, 'w', newline='') as f:
            w = csv.DictWriter(f, keys)
            w.writeheader()
            for r in results:
                if 'error' not in r:
                    w.writerow({k: r.get(k, '') for k in keys})
        print(f"\n  CSV: {args.csv}")


if __name__ == '__main__':
    main()
