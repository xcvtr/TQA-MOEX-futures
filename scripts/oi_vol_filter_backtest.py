#!/usr/bin/env python3 -u
"""Бэктест OI-портфеля с режимным vol-фильтром (оптимизированный).

Предвычисляем волатильность и медиану один раз на тикер — по дневным закрытиям.
Режимы: 0=baseline, 1=SV vol>med, 2=все vol>0.8×med, 3=все vol>med,
        4=все vol>1.2×med, 5=сезонный множитель риска.
"""
import sys, bisect
import numpy as np
import clickhouse_connect as cc, psycopg2

TZ_SHIFT = 5 * 3600
MT = {'SV': 'SILV', 'NG': 'NG', 'BR': 'BR'}
FT = {'SV': 'SV', 'NG': 'NG', 'BR': 'BR'}
RISK = {'NG': 0.25, 'BR': 0.25, 'SV': 0.15}
THR, HOLD, PYR, MAX_MARGIN = 4.0, 60, 3, 0.9

ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')

def load(ticker, year):
    START, END = f'{year}-01-01', f'{year}-12-31'
    if year == 2026: END = '2026-08-07'
    def q(sql): return ch.query(sql).result_rows
    r = q(f"SELECT bt, (buy_fiz - sell_fiz) * 1.0 / NULLIF(buy_fiz + sell_fiz, 0) * 100 as dn "
          f"FROM moex.futoi WHERE ticker='{FT[ticker]}' AND bt >= '{START} 00:00:00' AND bt <= '{END} 23:59:59'")
    futoi = {bt.replace(tzinfo=None).timestamp() + TZ_SHIFT: dn for bt, dn in r}
    r = q(f"SELECT toUnixTimestamp(toDateTime(bt)), prc FROM moex.mt5_continuous "
          f"WHERE ticker='{MT[ticker]}' AND bt >= '{START}' AND bt <= '{END} 23:59:59'")
    rows = [(ts, c) for ts, c in r if c and c > 0]
    arr = np.array(rows, dtype=np.float64)
    order = np.argsort(arr[:, 0])
    prices = (arr[order, 0], arr[order, 1])
    pg = psycopg2.connect(host='10.0.0.60', dbname='moex', user='postgres', connect_timeout=5)
    cur = pg.cursor()
    cur.execute("SELECT go, min_step, step_price, fee_entry FROM futures.ticker_specs WHERE ticker=%s", (ticker,))
    row = cur.fetchone()
    pg.close()
    return futoi, prices, (float(row[0]), float(row[1]), float(row[2]), float(row[3]))


def precompute_vol(ts_arr, prc_arr):
    """Для каждого бара: реализованная волатильность 20д назад + медиана 180д назад.
    Векторизовано: дневные закрытия → дневные волатильности → маппинг на бары."""
    n = len(ts_arr)
    vol = np.full(n, np.nan)
    med = np.full(n, np.nan)
    # дневные закрытия (векторизованно)
    day_ids = (ts_arr // 86400).astype(np.int64)
    # последний индекс каждого дня
    uniq_days, first_idx, counts = np.unique(day_ids, return_index=True, return_counts=True)
    last_idx = first_idx + counts - 1
    day_close = prc_arr[last_idx]
    day_rets = np.diff(day_close) / day_close[:-1]
    nd = len(day_close)
    day_vol = np.full(nd, np.nan)
    day_med = np.full(nd, np.nan)
    # волатильность 20 торговых дней (скользящее окно, векторизованно)
    for i in range(20, nd):
        day_vol[i] = np.std(day_rets[i - 20:i]) * np.sqrt(252) * 100
    # медиана месячных волатильностей за ~180 дней — шаг 5 торговых дней
    for i in range(30, nd):
        start = max(0, i - 180)
        seg = day_rets[start:i]
        if len(seg) >= 25:
            # волатильности 20-дневных окон со сдвигом 5
            nw = len(seg) - 20
            if nw > 0:
                wins = np.array([np.std(seg[j:j + 20]) for j in range(0, nw, 5)])
                day_med[i] = np.median(wins) * np.sqrt(252) * 100
    # маппинг: бар -> день -> vol/med
    for i in range(n):
        d = day_ids[i]
        pos = int(np.searchsorted(uniq_days, d))
        if pos < nd:
            vol[i] = day_vol[pos]
            med[i] = day_med[pos]
    return vol, med


def season_mult(ticker, ts):
    import datetime as dt
    m = dt.datetime.fromtimestamp(ts - TZ_SHIFT).month
    if ticker in ('NG', 'SV'):
        if m in (12, 1, 2): return 1.0
        if m in (7, 8): return 0.5
    return 1.0


def run_portfolio(years, mode):
    tickers = ['NG', 'BR', 'SV']
    data = {}
    vol_cache = {}
    vol_idx_cache = {}
    for t in tickers:
        data[t] = {}
        for y in years:
            futoi, prices, spec = load(t, y)
            data[t][y] = (futoi, prices, spec)
        # предвычисляем vol/med по объединённым ценам
        all_p = np.concatenate([data[t][y][1][0] for y in years])
        all_c = np.concatenate([data[t][y][1][1] for y in years])
        # сортируем по времени (годы идут подряд)
        o = np.argsort(all_p)
        vol_cache[t] = precompute_vol(all_p[o], all_c[o])
        # маппинг ts -> индекс в объединённом отсортированном массиве (для vol/med)
        ts_sorted = all_p[o]
        # предвычисляем: bisect по ts_sorted для каждого futoi ts
        fts_all = sorted(set().union(*[set(data[t][y][0].keys()) for y in years]))
        gi_cache = {}
        gi = 0
        for ts in fts_all:
            while gi < len(ts_sorted) and ts_sorted[gi] <= ts:
                gi += 1
            gi_cache[ts] = gi - 1
        vol_idx_cache[t] = (vol_cache[t], gi_cache, ts_sorted)

    eq = 200000.0; peak = eq; mdd = 0.0
    n = 0; wins = 0
    by_t = {t: 0.0 for t in tickers}
    by_y = {y: 0.0 for y in years}
    all_ts = set()
    for t in tickers:
        for y in years:
            all_ts.update(data[t][y][0].keys())
    all_ts = sorted(all_ts)
    pos = {t: [] for t in tickers}
    occ = {t: None for t in tickers}

    for ts in all_ts:
        for t in tickers:
            for pi in range(len(pos[t]) - 1, -1, -1):
                p = pos[t][pi]
                if ts >= p[3] + 60 * HOLD:
                    futoi, prices, spec = data[t][p[4]]
                    pts, pprc = prices
                    j = bisect.bisect_right(pts, ts) - 1
                    if j < 0: continue
                    exit_p = pprc[j]
                    ms, sp = spec[1], spec[2]
                    pnl = (exit_p - p[1]) / ms * sp * p[2]
                    if p[0] < 0: pnl = (p[1] - exit_p) / ms * sp * p[2]
                    pnl -= spec[3] * 2 * p[2]
                    eq += pnl; n += 1
                    if pnl > 0: wins += 1
                    by_t[t] += pnl; by_y[p[4]] += pnl
                    pos[t].pop(pi); occ[t] = ts + 300

        for t in tickers:
            futoi = prices = spec = None
            for y in years:
                if ts in data[t][y][0]:
                    futoi, prices, spec = data[t][y]; break
            if futoi is None: continue
            dn = futoi[ts]
            if len(pos[t]) >= PYR: continue
            if occ[t] and ts < occ[t]: continue
            sig = False; direction = 0
            if dn <= -THR: sig, direction = True, 1
            elif dn >= THR: sig, direction = True, -1
            if not sig: continue
            pts, pprc = prices
            idx = bisect.bisect_right(pts, ts) - 1
            if idx < 0: continue
            pt, prc = pts[idx], pprc[idx]
            if prc <= 0 or (ts - pt) > 600: continue

            apply = True; risk_mult = 1.0
            if mode >= 1:
                v, m = vol_cache[t]
                gi = vol_idx_cache[t][1].get(ts, -1)
                if gi >= 0 and gi < len(v):
                    v_now = v[gi]; v_med = m[gi]
                    if np.isnan(v_now) or np.isnan(v_med):
                        apply = False
                    else:
                        if mode == 1:
                            if t == 'SV' and v_now <= v_med: apply = False
                        elif mode == 2:
                            if v_now < 0.8 * v_med: apply = False
                        elif mode == 3:
                            if v_now < v_med: apply = False
                        elif mode == 4:
                            if v_now < 1.2 * v_med: apply = False
                else:
                    apply = False
            if mode == 5:
                risk_mult = season_mult(t, ts)
            if not apply: continue

            go, ms, sp, fee = spec
            risk_pct = RISK[t] * risk_mult
            shares = int(eq * risk_pct / go)
            if shares < 1: continue
            used = 0.0
            for tt in tickers:
                for p in pos[tt]:
                    for y in years:
                        if p[4] == y:
                            used += p[2] * data[tt][y][2][0]; break
            if (used + shares * go) > eq * MAX_MARGIN:
                shares = max(0, int((eq * MAX_MARGIN - used) / go))
                if shares < 1: continue
            pos[t].append((direction, prc, shares, ts, _year_of(ts, years)))
            occ[t] = ts + 60 * (HOLD + 5)

        mv = 0.0
        for t in tickers:
            for p in pos[t]:
                for y in years:
                    if p[4] == y:
                        futoi, prices, spec = data[t][y]
                        j = bisect.bisect_right(prices[0], ts) - 1
                        if j < 0: break
                        prc = prices[1][j]
                        m = (prc - p[1]) / spec[1] * spec[2] * p[2]
                        if p[0] < 0: m = -m
                        mv += m; break
        peak = max(peak, eq + mv)
        mdd = max(mdd, (peak - (eq + mv)) / peak)

    for t in tickers:
        for p in pos[t]:
            for y in years:
                if p[4] == y:
                    futoi, prices, spec = data[t][y]
                    exit_p = prices[1][-1]
                    pnl = (exit_p - p[1]) / spec[1] * spec[2] * p[2]
                    if p[0] < 0: pnl = (p[1] - exit_p) / spec[1] * spec[2] * p[2]
                    pnl -= spec[3] * 2 * p[2]
                    eq += pnl; n += 1
                    if pnl > 0: wins += 1
                    by_t[t] += pnl; by_y[p[4]] += pnl
                    break

    roi = (eq - 200000) / 200000 * 100
    wr = wins / n * 100 if n else 0
    return {'roi': round(roi, 1), 'mdd': round(mdd * 100, 1), 'n': n, 'wr': round(wr, 1),
            'by_t': {k: round(v) for k, v in by_t.items()},
            'by_y': {k: round(v) for k, v in by_y.items()}}


def _year_of(ts, years):
    import datetime as dt
    y = dt.datetime.fromtimestamp(ts - TZ_SHIFT).year
    return y if y in years else years[0]


MODES = {
    0: 'BASELINE (без фильтра)',
    1: 'SV только при vol>медианы',
    2: 'все при vol>0.8×медианы',
    3: 'все при vol>медианы',
    4: 'все при vol>1.2×медианы',
    5: 'сезонный (зима 1.0, лето 0.5)',
}

print(f"{'режим':<28}{'ROI%':>10}{'MDD%':>8}{'сдел':>7}{'WR%':>7}")
print("-" * 64)
for mode in [0, 1, 2, 3, 4, 5]:
    res = run_portfolio([2024, 2025, 2026], mode)
    print(f"{MODES[mode]:<28}{res['roi']:>+10.1f}{res['mdd']:>8.1f}{res['n']:>7}{res['wr']:>7.1f}")

ch.close()
