#!/usr/bin/env python3
"""TRIZ 300%: Полный поиск паттернов × тикеров × параметров.
64 тикера × 5 паттернов × hold[1,2,3,5] × sl[0,0.01,0.02] + 4h + adaptive sizing + trend filter + walk-forward.
"""
import sys, os, json, time
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import clickhouse_connect
from config import CH_HOST, CH_PORT, CH_DB

# ── Config ──────────────────────────────────────────────────────────────
CAPITAL = 1_000_000         # базовый капитал для поиска (потом реинвест)
COMM = 4                     # round-trip комиссия
RISK_PCT = 0.02              # 2% риска на сделку (TRIZ дробление)
FEEDBACK = True              # обратная связь: снижать после убытков
TREND_FILTER = True          # SMA50 трендовый фильтр
MAX_DD_HALT = 50.0           # остановка при просадке (высокое значение — не ограничиваем поиск)
MAX_LEVERAGE = 5.0           # макс. плечо: position_value ≤ eq * MAX_LEVERAGE

DAILY_HOLD = [1, 2, 3, 5]
DAILY_SL = [0, 0.01, 0.02]

H4_HOLD = [1, 2, 4, 8]
H4_SL = [0, 0.01, 0.02]

CS_MAP = {
    'AF': 100, 'AL': 25, 'AU': 1, 'BM': 10, 'BR': 10, 'CC': 10, 'CE': 100, 'CH': 100,
    'CNYRUBF': 1000, 'CR': 10, 'DX': 10000, 'ED': 1000, 'EURRUBF': 1000, 'Eu': 1000,
    'FF': 100, 'GAZPF': 100, 'GD': 10, 'GK': 100, 'GL': 10, 'GLDRUBF': 10,
    'GZ': 100, 'HS': 100, 'HY': 1000, 'IB': 100, 'IMOEXF': 10, 'KC': 100,
    'LK': 100, 'MC': 100, 'ME': 100, 'MG': 100, 'MM': 1, 'MN': 100, 'MX': 1,
    'MY': 1, 'NA': 1, 'NG': 100, 'NM': 100, 'NR': 1, 'OJ': 10, 'PD': 10,
    'PT': 10, 'RB': 1000, 'RI': 10, 'RL': 1, 'RM': 10, 'RN': 100,
    'SBERF': 100, 'SE': 100, 'SF': 1000, 'Si': 1000, 'SN': 100, 'SP': 100,
    'SR': 100, 'SS': 100, 'SV': 1, 'TN': 100, 'TT': 1, 'UC': 1000,
    'USDRUBF': 1000, 'VB': 1000, 'VI': 1, 'W4': 1, 'X5': 100, 'YD': 100,
}

PATTERNS = [
    ('vol_up_oi_up_yb_up',    lambda dv, dyb, dys, dfn, dtoi: dv > 0 and dtoi > 0 and dyb > 0),
    ('smart_money',            lambda dv, dyb, dys, dfn, dtoi: dv > 0 and dyb > 0 and dfn < 0),
    ('vol_up_oi_down',        lambda dv, dyb, dys, dfn, dtoi: dv > 0 and dtoi < 0),
    ('vol_up_yb_down_fiz_up', lambda dv, dyb, dys, dfn, dtoi: dv > 0 and dyb < 0 and dfn > 0),
    ('fiz_extreme_vol_up',    lambda dv, dyb, dys, dfn, dtoi: dv > 0 and abs(dfn) > 5),
]

# ── ClickHouse ──────────────────────────────────────────────────────────
ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)

def get_symbols():
    rows = ch.query("""
        SELECT symbol, count(*) as cnt
        FROM moex.prices_5m_oi
        WHERE time >= '2024-01-01'
        GROUP BY symbol HAVING cnt > 1000
        ORDER BY cnt DESC
    """).result_rows
    return [r[0] for r in rows]

def compute_sma50(close):
    sma = np.full(len(close), np.nan)
    if len(close) >= 50:
        cs = np.cumsum(close)
        sma[49] = cs[49] / 50
        sma[50:] = (cs[50:] - cs[:-50]) / 50
    return sma

def compute_features(dates, opn, high, low, close, vol, yb, ys, fb, fs, toi):
    toi = np.where(toi <= 0, 1, toi)
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
    sma50 = compute_sma50(close)
    return dv, dyb, dys, dfn, dtoi, sma50

def load_daily(ticker):
    rows = ch.query("""
        SELECT toDate(p.time) as d,
               argMax(p.open, p.time) as open,
               argMax(p.high, p.time) as high,
               argMax(p.low, p.time) as low,
               argMax(p.close, p.time) as close,
               argMax(p.volume, p.time) as volume,
               argMax(o.yur_buy, p.time) as yur_buy,
               argMax(o.yur_sell, p.time) as yur_sell,
               argMax(o.fiz_buy, p.time) as fiz_buy,
               argMax(o.fiz_sell, p.time) as fiz_sell,
               argMax(o.total_oi, p.time) as total_oi
        FROM moex.prices_5m p
        INNER JOIN moex.prices_5m_oi o ON p.symbol = o.symbol AND p.time = o.time
        WHERE p.symbol = %(t)s AND p.time >= '2024-01-01' AND p.time <= '2026-05-01'
        GROUP BY d ORDER BY d
    """, parameters={'t': ticker}).result_rows
    if len(rows) < 60:
        return None
    a = np.array([list(r) for r in rows], dtype=object)
    dates = [str(r[0]) for r in rows]
    opn = a[:, 1].astype(float); high = a[:, 2].astype(float); low = a[:, 3].astype(float)
    close = a[:, 4].astype(float); vol = a[:, 5].astype(float)
    yb = a[:, 6].astype(float); ys = a[:, 7].astype(float)
    fb = a[:, 8].astype(float); fs = a[:, 9].astype(float); toi = a[:, 10].astype(float)
    dv, dyb, dys, dfn, dtoi, sma50 = compute_features(dates, opn, high, low, close, vol, yb, ys, fb, fs, toi)
    return dict(dates=dates, opn=opn, high=high, low=low, close=close, vol=vol,
                dv=dv, dyb=dyb, dys=dys, dfn=dfn, dtoi=dtoi, sma50=sma50, n=len(rows))

def load_4h(ticker):
    rows = ch.query("""
        SELECT p.time, p.open, p.high, p.low, p.close, p.volume,
               o.yur_buy, o.yur_sell, o.fiz_buy, o.fiz_sell, o.total_oi
        FROM moex.prices_5m p
        INNER JOIN moex.prices_5m_oi o ON p.symbol = o.symbol AND p.time = o.time
        WHERE p.symbol = %(t)s AND p.time >= '2024-01-01' AND p.time <= '2026-05-01'
        ORDER BY p.time
    """, parameters={'t': ticker}).result_rows
    if len(rows) < 1000:
        return None
    buckets = {}
    for r in rows:
        t = r[0]
        h4 = t.replace(hour=(t.hour // 4) * 4, minute=0, second=0, microsecond=0)
        if h4 not in buckets:
            buckets[h4] = [r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9], r[10]]
        else:
            b = buckets[h4]
            b[1] = max(b[1], r[2])
            b[2] = min(b[2], r[3])
            b[3] = r[4]
            b[4] = b[4] + r[5]
            b[5] = r[6]; b[6] = r[7]; b[7] = r[8]; b[8] = r[9]; b[9] = r[10]
    sk = sorted(buckets.keys())
    a = np.array([buckets[k] for k in sk], dtype=float)
    dates = [str(k) for k in sk]
    opn = a[:, 0]; high = a[:, 1]; low = a[:, 2]; close = a[:, 3]; vol = a[:, 4]
    yb = a[:, 5]; ys = a[:, 6]; fb = a[:, 7]; fs = a[:, 8]; toi = a[:, 9]
    if len(close) < 30:
        return None
    dv, dyb, dys, dfn, dtoi, sma50 = compute_features(dates, opn, high, low, close, vol, yb, ys, fb, fs, toi)
    return dict(dates=dates, opn=opn, high=high, low=low, close=close, vol=vol,
                dv=dv, dyb=dyb, dys=dys, dfn=dfn, dtoi=dtoi, sma50=sma50, n=len(rows))

# ── Backtest ───────────────────────────────────────────────────────────
def backtest_wf(data, pfunc, hold, sl, cs, capital=CAPITAL, comm=COMM,
                risk_pct=RISK_PCT, feedback=FEEDBACK, trend_filter=TREND_FILTER,
                max_dd_halt=MAX_DD_HALT):
    dates = data['dates']; opn = data['opn']; high = data['high']
    low = data['low']; close = data['close']
    dv = data['dv']; dyb = data['dyb']; dys = data['dys']
    dfn = data['dfn']; dtoi = data['dtoi']; sma50 = data['sma50']
    n = len(close)
    if n < 60:
        return None
    nf = 4
    fsize = n // nf
    fold_res = []
    for f in range(nf):
        fs_ = f * fsize
        fe_ = n if f == nf - 1 else (f + 1) * fsize
        eq = float(capital)
        peak = eq
        mdd = 0.0
        trades = []
        cons_w, cons_l = 0, 0
        sizing_mult = 1.0
        stopped = False
        for i in range(fs_, min(fe_ - 1, n - 2)):
            if i >= len(dv) or i >= len(dyb) or i >= len(dfn) or i >= len(dtoi):
                break
            if not pfunc(dv[i], dyb[i], dys[i], dfn[i], dtoi[i]):
                continue
            if trend_filter and sma50 is not None and i < len(sma50) and not np.isnan(sma50[i]):
                if close[i] <= sma50[i]:
                    continue
            if stopped:
                continue
            ei = i + 1
            if ei >= n - 1:
                continue
            ep = float(opn[ei])
            xi = min(ei + hold, n - 1)
            sp = ep * (1 - sl) if sl > 0 else 0
            stop_hit = False
            xp = float(close[xi])
            if sl > 0:
                for j in range(ei, xi + 1):
                    if float(low[j]) <= sp:
                        xp = sp
                        stop_hit = True
                        break
            go = ep * cs
            if go <= 0:
                continue
            max_nc_by_capital = int(eq * MAX_LEVERAGE / go)
            if max_nc_by_capital < 1:
                continue
            if sl > 0:
                base_nc = eq * risk_pct / (ep * cs * sl)
            else:
                base_nc = eq * risk_pct / go * 5
            base_nc = max(1, int(base_nc))
            base_nc = min(base_nc, max_nc_by_capital)
            if feedback:
                if cons_l >= 3:
                    sizing_mult = 0.5
                elif cons_w >= 3:
                    sizing_mult = 1.25
                else:
                    sizing_mult = 1.0
            nc = max(1, int(base_nc * sizing_mult))
            nc = min(nc, max_nc_by_capital)
            eq_before = eq
            gp = nc * cs * (xp - ep)
            cm = nc * comm
            npnl = gp - cm
            eq += npnl
            if eq > peak:
                peak = eq
            dd = (peak - eq) / peak * 100 if peak > 0 else 0
            mdd = max(mdd, dd)
            if npnl > 0:
                cons_w += 1; cons_l = 0
            else:
                cons_l += 1; cons_w = 0
            if mdd >= max_dd_halt:
                stopped = True
            pnl_pct = npnl / eq_before * 100 if eq_before > 0 else 0
            trades.append(dict(entry=dates[ei], exit=dates[xi],
                               ep=round(ep,2), xp=round(xp,2),
                               nc=nc, gp=round(gp,0), cm=round(cm,0),
                               npnl=round(npnl,0), pnl_pct=round(pnl_pct,2),
                               stop=stop_hit, bars=xi-ei, sizing=sizing_mult))
        ret = (eq - capital) / capital * 100
        wins = sum(1 for t in trades if t['npnl'] > 0)
        losses = len(trades) - wins
        wr = wins / len(trades) * 100 if trades else 0
        gp_sum = sum(t['npnl'] for t in trades if t['npnl'] > 0)
        gl_sum = sum(t['npnl'] for t in trades if t['npnl'] < 0)
        pf = abs(gp_sum / (gl_sum + 1))
        tr_comm = sum(t['cm'] for t in trades)
        fold_res.append(dict(fold=f+1, start=dates[fs_],
                             end=dates[min(fe_-1, n-1)],
                             trades=len(trades), wins=wins, losses=losses,
                             wr=round(wr,1), pf=round(pf,2),
                             ret=round(ret,2), mdd=round(mdd,2),
                             net_pnl=round(gp_sum+gl_sum,0),
                             commission=round(tr_comm,0)))
    return fold_res

# ── Grid Search ────────────────────────────────────────────────────────
def run_grid(tickers, hold_vals, sl_vals, timeframe='daily', max_tickers=None):
    loader = load_daily if timeframe == 'daily' else load_4h
    all_res = []
    t0 = time.time()
    n_tickers = len(tickers)
    if max_tickers:
        tickers = tickers[:max_tickers]
    for ti, ticker in enumerate(tickers):
        cs = CS_MAP.get(ticker, 1)
        t1 = time.time()
        sys.stderr.write(f'\r[{timeframe}] {ti+1}/{n_tickers} {ticker}... ')
        sys.stderr.flush()
        data = loader(ticker)
        if data is None:
            sys.stderr.write('no data, skip\n')
            continue
        for pname, pfunc in PATTERNS:
            t_pattern = time.time()
            for hold in hold_vals:
                for sl in sl_vals:
                    fr = backtest_wf(data, pfunc, hold, sl, cs)
                    if fr is None:
                        continue
                    rets = [r['ret'] for r in fr]
                    dds = [r['mdd'] for r in fr]
                    mean_ret = np.mean(rets)
                    mean_dd = np.mean(dds)
                    min_ret = min(rets)
                    max_dd = max(dds)
                    n_tr = sum(r['trades'] for r in fr)
                    n_neg = sum(1 for r in rets if r < 0)
                    denom = mean_dd + 0.1
                    score = (mean_ret / denom) * (1 - n_neg / 4 * 0.5)
                    all_res.append(dict(ticker=ticker, pattern=pname,
                                        timeframe=timeframe, hold=hold, sl=sl,
                                        mean_ret=round(mean_ret,2),
                                        mean_dd=round(mean_dd,2),
                                        min_ret=round(min_ret,2),
                                        max_dd=round(max_dd,2),
                                        n_trades=n_tr,
                                        n_neg_folds=n_neg,
                                        wr=round(np.mean([r['wr'] for r in fr]),1),
                                        pf=round(np.mean([r['pf'] for r in fr]),2),
                                        score=round(score,2)))
        elapsed = time.time() - t1
        sys.stderr.write(f'{elapsed:.0f}s ({len(all_res)} total combos)\n')
    sys.stderr.write(f'\n[{timeframe}] Total: {time.time()-t0:.0f}s, {len(all_res)} combos\n')
    return all_res

# ── Portfolio ──────────────────────────────────────────────────────────
def build_portfolio(all_res, top_n=5):
    valid = [r for r in all_res if r['mean_ret'] > 0 and r['mean_dd'] < 15
             and r['n_neg_folds'] <= 1 and r['n_trades'] >= 10]
    valid.sort(key=lambda x: -x['score'])
    selected = []
    used_tickers = set()
    used_patterns = set()
    for r in valid:
        if len(selected) >= top_n:
            break
        if r['ticker'] not in used_tickers:
            selected.append(r)
            used_tickers.add(r['ticker'])
            used_patterns.add(r['pattern'])
    return selected

def simulate_portfolio(signals, tickers_data, capital=CAPITAL, comm=COMM,
                       risk_pct=RISK_PCT, feedback=FEEDBACK,
                       trend_filter=TREND_FILTER, max_dd_halt=MAX_DD_HALT):
    """Simulate a portfolio of multiple independent signal strategies."""
    portfolios = []
    for sig in signals:
        ticker = sig['ticker']
        data = tickers_data.get(ticker)
        if data is None:
            continue
        pattern_name = sig['pattern']
        pfunc = dict(PATTERNS)[pattern_name]
        hold = sig['hold']
        sl = sig['sl']
        cs = CS_MAP.get(ticker, 1)
        fr = backtest_wf(data, pfunc, hold, sl, cs,
                         capital=capital, comm=comm, risk_pct=risk_pct,
                         feedback=feedback, trend_filter=trend_filter,
                         max_dd_halt=max_dd_halt)
        if fr:
            portfolios.append(dict(signal=sig, folds=fr))
    return portfolios

# ── Report ─────────────────────────────────────────────────────────────
def generate_report(daily_res, h4_res, portfolio, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    lines = []
    lines.append('# TRIZ 300%: Отчёт по поиску паттернов\n')
    lines.append(f'_Дата: {time.strftime("%Y-%m-%d %H:%M")}_\n')
    lines.append('---\n')
    
    # Best daily results
    lines.append('## 1. Daily Grid Search Results (top 30)\n')
    sorted_daily = sorted(daily_res, key=lambda x: -x['score'])
    lines.append('| # | Ticker | Pattern | Hold | SL | Mean Ret | Mean DD | Min Ret | Max DD | Trades | WR | PF | Score |')
    lines.append('|---|--------|---------|------|----|----------|---------|---------|--------|--------|----|----|-------|')
    for i, r in enumerate(sorted_daily[:30]):
        lines.append(f'| {i+1} | {r["ticker"]} | {r["pattern"]} | {r["hold"]} | {r["sl"]:.0%} '
                     f'| {r["mean_ret"]:+.2f}% | {r["mean_dd"]:.2f}% | {r["min_ret"]:+.2f}% '
                     f'| {r["max_dd"]:.2f}% | {r["n_trades"]} | {r["wr"]:.0f}% '
                     f'| {r["pf"]:.2f} | {r["score"]:.2f} |')
    lines.append('')
    
    # Best 4h results
    lines.append('## 2. 4h Grid Search Results (top 30)\n')
    sorted_h4 = sorted(h4_res, key=lambda x: -x['score'])
    lines.append('| # | Ticker | Pattern | Hold | SL | Mean Ret | Mean DD | Min Ret | Max DD | Trades | WR | PF | Score |')
    lines.append('|---|--------|---------|------|----|----------|---------|---------|--------|--------|----|----|-------|')
    for i, r in enumerate(sorted_h4[:30]):
        lines.append(f'| {i+1} | {r["ticker"]} | {r["pattern"]} | {r["hold"]} | {r["sl"]:.0%} '
                     f'| {r["mean_ret"]:+.2f}% | {r["mean_dd"]:.2f}% | {r["min_ret"]:+.2f}% '
                     f'| {r["max_dd"]:.2f}% | {r["n_trades"]} | {r["wr"]:.0f}% '
                     f'| {r["pf"]:.2f} | {r["score"]:.2f} |')
    lines.append('')
    
    # Best stable combos (profitable in ALL folds)
    lines.append('## 3. Stable Combos (profitable in ALL 4 folds)\n')
    stable = [r for r in daily_res if r['n_neg_folds'] == 0 and r['mean_ret'] > 0 and r['mean_dd'] < 15]
    stable.sort(key=lambda x: -x['score'])
    lines.append(f'Found {len(stable)} stable combos.\n')
    if stable:
        lines.append('| # | Ticker | Pattern | Hold | SL | Mean Ret | Mean DD | Score |')
        lines.append('|---|--------|---------|------|----|----------|---------|-------|')
        for i, r in enumerate(stable[:20]):
            lines.append(f'| {i+1} | {r["ticker"]} | {r["pattern"]} | {r["hold"]} | {r["sl"]:.0%} '
                         f'| {r["mean_ret"]:+.2f}% | {r["mean_dd"]:.2f}% | {r["score"]:.2f} |')
    lines.append('')
    
    # Portfolio
    lines.append('## 4. Portfolio (Top 5 Non-Overlapping Signals)\n')
    lines.append(f'| # | Ticker | Pattern | Hold | SL | Mean Ret | Mean DD | Score |')
    lines.append(f'|---|--------|---------|------|----|----------|---------|-------|')
    for i, s in enumerate(portfolio):
        r = s['signal']
        lines.append(f'| {i+1} | {r["ticker"]} | {r["pattern"]} | {r["hold"]} | {r["sl"]:.0%} '
                     f'| {r["mean_ret"]:+.2f}% | {r["mean_dd"]:.2f}% | {r["score"]:.2f} |')
    lines.append('')
    
    if portfolio:
        lines.append('### Portfolio Fold Details\n')
        for pi, p in enumerate(portfolio):
            lines.append(f'**Signal {pi+1}: {p["signal"]["ticker"]} {p["signal"]["pattern"]} hold={p["signal"]["hold"]} sl={p["signal"]["sl"]:.0%}**\n')
            lines.append('| Fold | Period | Trades | Ret | DD | WR | PF |')
            lines.append('|------|--------|--------|-----|-----|----|----|')
            for fr in p['folds']:
                lines.append(f'| {fr["fold"]} | {fr["start"]}–{fr["end"]} | {fr["trades"]} '
                             f'| {fr["ret"]:+.2f}% | {fr["mdd"]:.2f}% | {fr["wr"]:.0f}% | {fr["pf"]:.2f} |')
            lines.append('')
    
    lines.append('---\n')
    lines.append('## 5. Key Findings\n')
    lines.append(f'- Total daily combos tested: {len(daily_res)}')
    lines.append(f'- Total 4h combos tested: {len(h4_res)}')
    lines.append(f'- Daily: {len(stable)} stable combos (profitable all 4 folds)')
    lines.append(f'- Portfolio: {len(portfolio)} non-overlapping signals')
    if daily_res:
        best = sorted_daily[0]
        lines.append(f'- Best daily: {best["ticker"]} {best["pattern"]} hold={best["hold"]} sl={best["sl"]:.0%} '
                     f'→ {best["mean_ret"]:+.2f}% mean ret, {best["mean_dd"]:.2f}% mean DD, score={best["score"]:.2f}')
    if h4_res:
        best_h4 = sorted_h4[0]
        lines.append(f'- Best 4h: {best_h4["ticker"]} {best_h4["pattern"]} hold={best_h4["hold"]} sl={best_h4["sl"]:.0%} '
                     f'→ {best_h4["mean_ret"]:+.2f}% mean ret, {best_h4["mean_dd"]:.2f}% mean DD, score={best_h4["score"]:.2f}')
    
    # Top patterns by frequency
    pattern_counts = defaultdict(int)
    for r in daily_res:
        if r['mean_ret'] > 0 and r['n_neg_folds'] <= 1:
            pattern_counts[r['pattern']] += 1
    lines.append(f'\n### Pattern Frequency (profitable)\n')
    for pname, cnt in sorted(pattern_counts.items(), key=lambda x: -x[1]):
        lines.append(f'- {pname}: {cnt} tickers')
    
    with open(os.path.join(output_dir, 'report.md'), 'w') as f:
        f.write('\n'.join(lines) + '\n')

# ── Main ────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    t_start = time.time()
    output_dir = 'reports/triz_300pct'
    os.makedirs(output_dir, exist_ok=True)
    
    symbols = get_symbols()
    print(f'Symbols with data: {len(symbols)}')
    
    # Phase 1: Daily grid search
    print(f'\n{"="*60}')
    print(f'PHASE 1: Daily Grid Search ({len(symbols)} tickers × {len(PATTERNS)} patterns × {len(DAILY_HOLD)} hold × {len(DAILY_SL)} sl)')
    print(f'{"="*60}')
    daily_results = run_grid(symbols, DAILY_HOLD, DAILY_SL, 'daily')
    with open(os.path.join(output_dir, 'grid_daily.json'), 'w') as f:
        json.dump(daily_results, f, indent=2)
    print(f'Saved {len(daily_results)} daily combos.')
    
    # Phase 2: 4h grid search
    print(f'\n{"="*60}')
    print(f'PHASE 2: 4h Grid Search')
    print(f'{"="*60}')
    h4_results = run_grid(symbols, H4_HOLD, H4_SL, '4h')
    with open(os.path.join(output_dir, 'grid_4h.json'), 'w') as f:
        json.dump(h4_results, f, indent=2)
    print(f'Saved {len(h4_results)} 4h combos.')
    
    # Phase 3: Portfolio construction
    print(f'\n{"="*60}')
    print(f'PHASE 3: Portfolio Construction')
    print(f'{"="*60}')
    portfolio_signals = build_portfolio(daily_results)
    print(f'Selected {len(portfolio_signals)} portfolio signals:')
    for i, s in enumerate(portfolio_signals):
        print(f'  {i+1}. {s["ticker"]} {s["pattern"]} hold={s["hold"]} sl={s["sl"]:.0%} '
              f'ret={s["mean_ret"]:+.2f}% dd={s["mean_dd"]:.2f}% score={s["score"]:.2f}')
    
    with open(os.path.join(output_dir, 'portfolio_signals.json'), 'w') as f:
        json.dump(portfolio_signals, f, indent=2)
    
    # Load data for portfolio simulation
    tickers_data = {}
    for s in portfolio_signals:
        if s['ticker'] not in tickers_data:
            data = load_daily(s['ticker'])
            if data:
                tickers_data[s['ticker']] = data
    
    portfolio = simulate_portfolio(portfolio_signals, tickers_data)
    
    # Phase 4: Report
    print(f'\n{"="*60}')
    print(f'PHASE 4: Report Generation')
    print(f'{"="*60}')
    generate_report(daily_results, h4_results, portfolio, output_dir)
    
    t_total = time.time() - t_start
    print(f'\n{"="*60}')
    print(f'DONE in {t_total:.0f}s')
    print(f'Results: {output_dir}/')
    print(f'{"="*60}')
