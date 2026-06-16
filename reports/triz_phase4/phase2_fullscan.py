#!/usr/bin/env python3
"""
Фаза 2 — Full scan 63 tickers на 5-минутных данных с WFA.

Конвейер:
  1. Audit: проверка данных (цены + OI)
  2. Загрузка 5m баров per ticker (1 SQL запрос)
  3. Индикаторы: ATR, dv, dyb, dys, dtoi, dfn (in-memory, numpy)
  4. Grid: 5 паттернов × 4 hold × 3 am × 2 dir = 120 конфигов без reload
  5. Walk-forward: train 2024, test 2025 — 2026
  6. Audit: OOS stability, trade count, sample verification

Usage: python3 phase2_fullscan.py
Output: reports/triz_phase4/phase2_fullscan.json
"""
import sys, os, json, time, argparse
sys.path.insert(0, '/home/user/projects/TQA-MOEX')
os.chdir('/home/user/projects/TQA-MOEX')
import numpy as np
import clickhouse_connect
from config import CH_HOST, CH_PORT, CH_DB

ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)

# ── Config ─────────────────────────────────────────────────────────────
MIN_5M_BARS = 20000     # min 5m bars per ticker (~80 trading days)
MIN_OI_BARS = 10000     # min OI bars
MIN_TRADES_IS = 5       # min trades in-sample
MIN_TRADES_OOS = 3      # min trades out-of-sample
CAPITAL = 200_000
COMM = 4
CS_DEFAULT = 1
MAX_LOT = 10
RISK_PCT = 0.02
TRAIN_END = '2025-01-01'

PATTERNS = {
    'v':   lambda dv, dyb, dys, dfn, dtoi: dv > 0 and dtoi > 0 and dyb > 0,
    's':   lambda dv, dyb, dys, dfn, dtoi: dv > 0 and dyb > 0 and dfn < 0,
    'd':   lambda dv, dyb, dys, dfn, dtoi: dv > 0 and dtoi < 0,
    'y':   lambda dv, dyb, dys, dfn, dtoi: dv > 0 and dyb < 0 and dfn > 0,
    'f':   lambda dv, dyb, dys, dfn, dtoi: dv > 0 and abs(dfn) > 5,
}

PATTERN_NAMES = {'v': 'vou', 's': 'sm', 'd': 'vod', 'y': 'vyf', 'f': 'fev'}

HOLD_VALUES = [5, 8, 13, 21]
AM_VALUES = [2, 3, 5]
DIRECTIONS = ['L', 'S']


# ── Step 1: Audit symbols ──────────────────────────────────────────────
def audit_symbols():
    """Собрать список валидных символов с 5m ценами + OI."""
    print("=" * 80)
    print("[AUDIT] Проверка символов (5m данные)")
    print("=" * 80)

    price_rows = ch.query("""
        SELECT symbol, count() as bars,
               min(time) as tmin, max(time) as tmax
        FROM moex.prices_5m
        WHERE time >= '2024-01-01' AND time <= '2026-05-01'
        GROUP BY symbol
        HAVING bars >= %(min_bars)s
        ORDER BY symbol
    """, parameters={'min_bars': MIN_5M_BARS}).result_rows

    oi_rows = ch.query("""
        SELECT symbol, count() as oi_bars
        FROM moex.prices_5m_oi
        WHERE time >= '2024-01-01' AND time <= '2026-05-01'
        GROUP BY symbol
        HAVING oi_bars >= %(min_oi)s
    """, parameters={'min_oi': MIN_OI_BARS}).result_rows
    oi_map = {r[0]: r[1] for r in oi_rows}

    last_rows = ch.query("""
        SELECT symbol, argMax(close, time), toDate(max(time))
        FROM moex.prices_5m
        WHERE time >= '2024-01-01' AND time <= '2026-05-01'
        GROUP BY symbol
    """).result_rows
    last_map = {r[0]: {'close': r[1], 'last_date': str(r[2])} for r in last_rows}

    valid = []
    for r in price_rows:
        sym = r[0]
        if sym not in oi_map:
            continue
        valid.append({
            'symbol': sym, 'bars': r[1],
            'range': f"{str(r[2])[:10]} .. {str(r[3])[:10]}",
            'oi_bars': oi_map[sym],
            'last_close': float(last_map.get(sym, {}).get('close', 0)),
            'last_date': last_map.get(sym, {}).get('last_date', ''),
        })

    print(f"  Символов с ценами ≥{MIN_5M_BARS}: {len(price_rows)}")
    print(f"  С OI ≥{MIN_OI_BARS}: {len(oi_map)}")
    print(f"  Валидных: {len(valid)}")
    return valid


# ── Step 2: Load 5m data ──────────────────────────────────────────────
def load_5m(symbol):
    """Загрузить 5m OHLCV + OI. Возвращает словарь numpy-массивов."""
    rows = ch.query(f"""
        SELECT time, open, high, low, close, volume,
               yur_buy, yur_sell, fiz_buy, fiz_sell, total_oi
        FROM moex.prices_5m p
        INNER JOIN moex.prices_5m_oi o
            ON p.symbol = o.symbol AND p.time = o.time
        WHERE p.symbol = '{symbol}'
          AND p.time >= '2024-01-01' AND p.time <= '2026-05-01'
        ORDER BY p.time
    """).result_rows

    if not rows or len(rows) < MIN_5M_BARS:
        return None

    N = len(rows)
    times = np.array([str(r[0]) for r in rows])
    opn = np.array([float(r[1]) for r in rows])
    high = np.array([float(r[2]) for r in rows])
    low = np.array([float(r[3]) for r in rows])
    close = np.array([float(r[4]) for r in rows])
    vol = np.array([float(r[5]) for r in rows])
    yb = np.array([float(r[6]) for r in rows])
    ys = np.array([float(r[7]) for r in rows])
    fb = np.array([float(r[8]) for r in rows])
    fs = np.array([float(r[9]) for r in rows])
    toi = np.array([float(r[10]) for r in rows])

    # Prices and OI sanity
    toi = np.where(toi <= 0, 1, toi)

    # ATR (14-period on 5m)
    tr = np.zeros(N)
    tr[1:] = np.maximum(
        high[1:] - low[1:],
        np.maximum(
            np.abs(high[1:] - close[:-1]),
            np.abs(low[1:] - close[:-1]),
        ),
    )
    atr = np.full(N, np.nan)
    for i in range(14, N):
        atr[i] = np.mean(tr[i - 13 : i + 1])

    # Дневная дата для split
    dates = np.array([str(r[0])[:10] for r in rows])

    # Normalized diffs
    v_m = np.mean(vol) + 1
    yb_m = np.mean(yb) + 1
    ys_m = np.mean(ys) + 1
    toi_m = np.mean(toi) + 1
    dv = np.diff(vol) / v_m
    dyb = np.diff(yb) / yb_m
    dys = np.diff(ys) / ys_m
    dtoi = np.diff(toi) / toi_m
    fiz_net = (fb - fs) / toi * 100
    dfn = np.diff(fiz_net)

    return {
        'N': N,
        'times': times,
        'dates': dates,
        'opn': opn, 'high': high, 'low': low, 'close': close,
        'vol': vol, 'atr': atr,
        'dv': dv, 'dyb': dyb, 'dys': dys, 'dtoi': dtoi, 'dfn': dfn,
    }


# ── Step 3: Run one strategy ──────────────────────────────────────────
def run_bt(data, direction, pfunc, hold, atr_mult, start_idx=64, end_idx=None):
    """
    Backtest for one (direction, pfunc, hold, atr_mult) config on 5m data.
    Returns list of (entry_idx, exit_idx, ep, xp, entry_time, exit_time, pnl_1ctr).
    """
    N = data['N']
    opn = data['opn']; high = data['high']; low = data['low']
    close = data['close']; vol = data['vol']; atr = data['atr']
    dv = data['dv']; dyb = data['dyb']; dys = data['dys']
    dfn = data['dfn']; dtoi = data['dtoi']
    times = data['times']

    if end_idx is None:
        end_idx = N - max(hold, 2) - 1

    # Precompute vol mean (60-bar rolling)
    vol_mean_arr = np.full(N, np.nan)
    vol_cum = np.cumsum(vol)
    for i in range(60, N):
        vol_mean_arr[i] = (vol_cum[i] - vol_cum[i - 60]) / 60

    trades = []
    for i in range(start_idx, end_idx):
        if i >= len(dv):
            break
        ep = float(opn[i + 1])
        if not pfunc(dv[i], dyb[i], dys[i], dfn[i], dtoi[i]):
            continue
        if np.isnan(vol_mean_arr[i]) or vol[i] < vol_mean_arr[i] * 1.2:
            continue

        xi = min(i + 1 + hold, N - 1)

        if direction == 'L':
            sp = (
                ep * (1 - min(max(atr[i] / ep * atr_mult, 0.005), 0.05))
                if not np.isnan(atr[i])
                else ep * 0.95
            )
            r_h = ep
            exit_idx = xi
            xp = float(close[xi])
            for j in range(i + 1, xi + 1):
                bh = float(high[j])
                if bh > r_h:
                    r_h = bh
                    if not np.isnan(atr[j]):
                        sp = max(
                            sp,
                            r_h * (1 - min(max(atr[j] / r_h * atr_mult, 0.005), 0.05)),
                        )
                if float(low[j]) <= sp:
                    xp = sp
                    exit_idx = j
                    break
            pnl = xp - ep
        else:
            sp = (
                ep * (1 + min(max(atr[i] / ep * atr_mult, 0.005), 0.05))
                if not np.isnan(atr[i])
                else ep * 1.05
            )
            r_l = ep
            exit_idx = xi
            xp = float(close[xi])
            for j in range(i + 1, xi + 1):
                bl = float(low[j])
                if bl < r_l:
                    r_l = bl
                    if not np.isnan(atr[j]):
                        sp = min(
                            sp,
                            r_l * (1 + min(max(atr[j] / r_l * atr_mult, 0.005), 0.05)),
                        )
                if float(high[j]) >= sp:
                    xp = sp
                    exit_idx = j
                    break
            pnl = ep - xp

        trades.append((i + 1, exit_idx, ep, xp, str(times[i + 1]), str(times[exit_idx]), pnl))

    return trades


def calc_stats(trades, capital=CAPITAL):
    """Compute stats from trade list (pnl per contract)."""
    if not trades or len(trades) < 1:
        return None

    pnls = [t[6] for t in trades]
    n = len(pnls)
    wins = sum(1 for p in pnls if p > 0)
    wr = wins / n * 100
    gp = sum(p for p in pnls if p > 0)
    gl = sum(p for p in pnls if p < 0)
    pf = abs(gp / (gl + 0.001))

    # Simple equity-based DD
    eq = float(capital)
    peak = eq
    mdd = 0.0
    for pnl in pnls:
        eq += pnl
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak * 100 if peak > 0 else 0
        mdd = max(mdd, dd)

    ret = (eq - capital) / capital * 100

    return {
        'ret': round(ret, 2),
        'mdd': round(mdd, 2),
        'calmar': round(ret / mdd, 2) if mdd > 0 else 0,
        'wr': round(wr, 1),
        'pf': round(pf, 2),
        'n': n,
        'avg_win': round(gp / max(wins, 1), 2),
        'avg_loss': round(abs(gl) / max(n - wins, 1), 2),
    }


# ── Step 4: Scan ticker ───────────────────────────────────────────────
def scan_ticker(symbol):
    """Полный scan одного тикера: 120 конфигов с WFA."""
    data = load_5m(symbol)
    if data is None:
        return None

    # Determine train/test split by date
    train_end_idx = 0
    for i, d in enumerate(data['dates']):
        if d >= TRAIN_END:
            train_end_idx = i
            break
    if train_end_idx < 20000:
        train_end_idx = len(data['dates']) // 2

    is_data = {k: v[:train_end_idx] if isinstance(v, np.ndarray) else v for k, v in data.items()}
    is_data['N'] = len(is_data['dates'])

    # OOS with context window for indicators
    ctx = max(0, train_end_idx - 64)
    oos_data = {k: v[ctx:] if isinstance(v, np.ndarray) else v for k, v in data.items()}
    oos_data['N'] = len(oos_data['dates'])
    oos_start = train_end_idx - ctx

    results = []
    for pkey, pfunc in PATTERNS.items():
        for hold in HOLD_VALUES:
            for am in AM_VALUES:
                for direction in DIRECTIONS:
                    is_trades = run_bt(is_data, direction, pfunc, hold, am, start_idx=64)
                    oos_trades = run_bt(oos_data, direction, pfunc, hold, am, start_idx=oos_start)

                    results.append({
                        'pattern': pkey,
                        'pattern_name': PATTERN_NAMES[pkey],
                        'hold': hold,
                        'atr_mult': am,
                        'direction': direction,
                        'is': calc_stats(is_trades),
                        'oos': calc_stats(oos_trades),
                        'n_is': len(is_trades),
                        'n_oos': len(oos_trades),
                    })

    # ── Audit ───────────────────────────────────────────────────────
    audit = {}

    # A1: Zero-trade patterns
    zero = [r for r in results if r['n_is'] == 0 and r['n_oos'] == 0]
    audit['zero_trade_strats'] = len(zero)

    # A2: WFA pass — OOS calmar >= IS calmar * 0.3, min trades
    passed = []
    for r in results:
        if r['is'] is None or r['oos'] is None:
            continue
        if r['is']['n'] < MIN_TRADES_IS or r['oos']['n'] < MIN_TRADES_OOS:
            continue
        if r['is']['calmar'] <= 0 or r['oos']['calmar'] <= 0:
            continue
        if r['oos']['calmar'] >= r['is']['calmar'] * 0.5:
            passed.append(r)

    audit['wfa_passed'] = len(passed)

    # A3: Sample trades (verify 2 trades from IS)
    samples = []
    for r in results:
        if r['is'] and r['is']['n'] > 0:
            ts = run_bt(is_data, r['direction'], PATTERNS[r['pattern']],
                       r['hold'], r['atr_mult'], start_idx=64)
            if ts:
                for t in ts[:2]:
                    samples.append({
                        'pattern': r['pattern'],
                        'direction': r['direction'],
                        'hold': r['hold'],
                        'atr_mult': r['atr_mult'],
                        'entry_time': t[4], 'exit_time': t[5],
                        'ep': round(t[2], 2), 'xp': round(t[3], 2),
                        'pnl': round(t[6], 2),
                    })
            if len(samples) >= 2:
                break

    audit['sample_trades'] = samples

    # Score and rank — чистая OOS Calmar, без множителя на n
    configs = []
    for r in passed:
        oos = r['oos']
        # Минимальные требования к IS
        if oos['calmar'] < 2:
            continue
        score = oos['calmar']
        configs.append({**r, 'score': round(score, 2)})

    configs.sort(key=lambda x: -x['score'])
    audit['top_configs'] = configs[:10]

    return audit


# ── Main ───────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--force', action='store_true', help='Ignore cache, rescan all')
    args = parser.parse_args()

    output_path = 'reports/triz_phase4/phase2_fullscan.json'
    cache_file = 'reports/triz_phase4/phase2_cache.json'

    # Load cache
    completed = {}
    if not args.force and os.path.exists(cache_file):
        with open(cache_file) as f:
            completed = json.load(f)
        print(f"[CACHE] Loaded {len(completed)} completed tickers")

    valid = audit_symbols()
    all_syms = [v['symbol'] for v in valid]
    print(f"\n{'=' * 80}")
    print(f"[SCAN] {len(all_syms)} tickers на 5m данных")
    print(f"{'=' * 80}")

    results = {}
    t0 = time.time()

    for idx, sym in enumerate(all_syms):
        if sym in completed and not args.force:
            results[sym] = completed[sym]
            continue

        elapsed = time.time() - t0
        eta = elapsed / max(idx + 1, 1) * (len(all_syms) - idx - 1) / 60
        print(f"\n[{idx+1}/{len(all_syms)}] {sym} ETA:{eta:.0f}min", end=' ')

        r = scan_ticker(sym)
        if r is not None:
            results[sym] = r
            completed[sym] = r
            print(f"✓ wfa={r['wfa_passed']}")
        else:
            print(f"✗ no data")

        # Save cache every 5
        if len(completed) % 5 == 0:
            with open(cache_file, 'w') as f:
                json.dump(completed, f, indent=2, default=str)

    # Save final
    report = {
        'config': {
            'min_5m_bars': MIN_5M_BARS,
            'min_oi_bars': MIN_OI_BARS,
            'min_trades_is': MIN_TRADES_IS,
            'min_trades_oos': MIN_TRADES_OOS,
            'train_end': TRAIN_END,
            'patterns': list(PATTERN_NAMES.values()),
            'hold_values': HOLD_VALUES,
            'atr_mult_values': AM_VALUES,
            'directions': DIRECTIONS,
        },
        'valid_symbols': valid,
        'results': results,
        'summary': {},
    }

    total = len(results)
    wfa_total = sum(1 for r in results.values() if r.get('wfa_passed', 0) > 0)
    top_total = sum(len(r.get('top_configs', [])) for r in results.values())

    report['summary'] = {
        'total_tickers': total,
        'tickers_with_wfa': wfa_total,
        'total_top_configs': top_total,
        'by_ticker': {
            sym: {
                'wfa_passed': r.get('wfa_passed', 0),
                'top_configs': len(r.get('top_configs', [])),
                'zero_trade_strats': r.get('zero_trade_strats', 0),
            }
            for sym, r in results.items()
        },
    }

    with open(output_path, 'w') as f:
        json.dump(report, f, indent=2, default=str)

    if os.path.exists(cache_file):
        os.remove(cache_file)

    print(f"\n{'=' * 80}")
    print(f"[DONE] {time.time()-t0:.0f}s")
    print(f"  Tickers: {total}")
    print(f"  WFA passed: {wfa_total}")
    print(f"  Top configs: {top_total}")
    print(f"  Report: {output_path}")

    # Top 15 tickers by best score
    print(f"\n{'=' * 80}")
    print(f"[TOP 15] Топ тикеров (по score)")
    print(f"{'Ticker':>10} {'WFA':>4} {'Top':>4} {'Score':>8}  {'Best config':>35}")
    print("-" * 70)

    scored = []
    for sym, r in results.items():
        tc = r.get('top_configs', [])
        if tc:
            best = max(tc, key=lambda x: x.get('score', 0))
            scored.append((
                sym, r['wfa_passed'], len(tc),
                best['score'],
                f"{best['pattern_name']} {best['direction']} h={best['hold']} am={best['atr_mult']} oos_c={best['oos']['calmar']:.1f}",
            ))

    scored.sort(key=lambda x: -x[3])
    for sym, wfa, tc, score, cfg in scored[:15]:
        print(f"{sym:>10} {wfa:>4} {tc:>4} {score:>7.1f}  {cfg}")


if __name__ == '__main__':
    main()
