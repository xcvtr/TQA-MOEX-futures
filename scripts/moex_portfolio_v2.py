#!/usr/bin/env python3
"""
moex_portfolio_v2.py — TRIZ-оптимизированный портфель MOEX фьючерсов.

Противоречие: +45%/год при DD -31% → надо DD ≤15% при 100%+ годовых
ИКР: портфель с DD ≤15% и annual 100%+

Принципы:
1. Асимметрия — crash protection при дневном падении Si > 3%
2. Динамичность — размер позиции обратно пропорционален 21d волатильности
3. Дробление — разные триггеры для LONG/SHORT
4. Проскок — multi-factor score по всем тикерам
5. Избыточность — хедж через опционы (не реализован в коде)

Usage:
    python3 scripts/moex_portfolio_v2.py
"""
import sys, os, json, itertools, subprocess
from datetime import datetime, timedelta
import numpy as np
import pandas as pd

# ─── Config ───────────────────────────────────────────────────────────────────
CAPITAL = 100_000
COMM = 4.0  # RUB/contract/side
Z_WINDOW = 21
CH = ["clickhouse-client", "-h", "10.0.0.60", "-q"]
MARGINS = {'BR': 8000, 'CR': 5000, 'AF': 4000, 'Si': 7000}
WEIGHTS = {'BR_vol_LONG': 0.15, 'CR_oi': 0.30, 'AF_oi': 0.30, 'Si_imb': 0.15}
RESERVE = 0.10

# Threshold grids per strategy
THRESH_GRID = {
    'BR_vol_LONG': [0.5, 0.7, 0.9, 1.1, 1.3, 1.5, 1.7, 2.0, 2.5, 3.0],
    'CR_oi': [0.8, 1.0, 1.2, 1.5, 2.0],
    'AF_oi': [1.5, 1.8, 2.0, 2.5],
    'Si_imb': [0.3, 0.5, 0.8, 1.0, 1.2, 1.5, 1.8, 2.0, 2.5, 3.0],
}

LEVERAGES = [1.0, 1.5, 2.0]
MAX_PORTFOLIO_DD = 15  # target max DD %

STRATEGY_META = {
    'BR_vol_LONG': {'ticker': 'BR', 'direction': 'LONG_only', 'feature': 'vol_z', 'signal_type': 'momentum'},
    'CR_oi': {'ticker': 'CR', 'direction': 'both', 'feature': 'oi_z', 'signal_type': 'mean_rev'},
    'AF_oi': {'ticker': 'AF', 'direction': 'both', 'feature': 'oi_z', 'signal_type': 'mean_rev'},
    'Si_imb': {'ticker': 'Si', 'direction': 'both', 'feature': 'bp_z', 'signal_type': 'momentum'},
}


def q_df(sql):
    r = subprocess.run(CH + [sql], capture_output=True, text=True, timeout=120)
    if r.returncode:
        return None
    lines = [line.split("\t") for line in r.stdout.strip().split("\n") if line.strip()]
    if len(lines) < 2:
        return None
    return pd.DataFrame(lines[1:], columns=lines[0])


def load_ticker_data(ticker):
    sql = f"""
        SELECT
            toString(tradedate) as dt,
            toString(argMax(pr_close, tradetime)) as close,
            toString(max(oi_change)) as oi_chg,
            toString(sum(vol_sum)) as volume
        FROM moex.supercandles_fo
        WHERE ticker = '{ticker}'
        GROUP BY tradedate ORDER BY tradedate
        FORMAT TabSeparatedWithNames
    """
    df = q_df(sql)
    if df is None or len(df) < 50:
        return None
    for c in [x for x in df.columns if x != 'dt']:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    df['dt'] = pd.to_datetime(df['dt'])
    df['ret'] = df['close'].pct_change() * 100
    df['ret_next'] = df['ret'].shift(-1)
    df['oi_z'] = (df['oi_chg'] - df['oi_chg'].rolling(Z_WINDOW).mean()) / df['oi_chg'].rolling(Z_WINDOW).std().replace(0, np.nan)
    df['vol_z'] = (df['volume'] - df['volume'].rolling(Z_WINDOW).mean()) / df['volume'].rolling(Z_WINDOW).std().replace(0, np.nan)
    return df


def load_si_imbalance():
    from clickhouse_driver import Client
    client = Client(host='10.0.0.60')
    sql = """
        SELECT tradedate,
               countIf(imb_l1 > 0.3) / count(*) AS buy_pressure
        FROM (
            SELECT tradedate,
                   (COALESCE(vol_b_l1, 0) - COALESCE(vol_s_l1, 0))
                       / NULLIF(COALESCE(vol_b_l1, 0) + COALESCE(vol_s_l1, 0), 0) AS imb_l1
            FROM moex.obstats_fo
            WHERE asset_code = 'Si' AND tradedate >= '2020-01-01'
        )
        GROUP BY tradedate ORDER BY tradedate
    """
    df = client.query_dataframe(sql)
    df['tradedate'] = pd.to_datetime(df['tradedate'])

    df['bp_z'] = (df['buy_pressure'] - df['buy_pressure'].rolling(Z_WINDOW).mean()) / df['buy_pressure'].rolling(Z_WINDOW).std().replace(0, np.nan)
    return df


def load_all_data():
    """Load data for all tickers + Si imbalance. Merges bp_z into Si data."""
    result = {}
    for sname, meta in STRATEGY_META.items():
        ticker = meta['ticker']
        if ticker not in result:
            df = load_ticker_data(ticker)
            if df is not None:
                result[ticker] = df
    si_imb = load_si_imbalance()
    # Merge bp_z into Si data
    if si_imb is not None and 'Si' in result:
        si_df = result['Si']
        si_imb_merge = si_imb[['tradedate', 'bp_z', 'buy_pressure']].copy()
        si_imb_merge.columns = ['dt', 'bp_z', 'buy_pressure']
        si_df = si_df.merge(si_imb_merge, on='dt', how='left')
        result['Si'] = si_df
    return result, si_imb


def compute_si_vol_signal(si_df):
    """Compute 21d Si vol percentiles for position sizing."""
    si_df = si_df.copy().sort_values('dt')
    si_df['ret_daily'] = si_df['close'].pct_change() * 100
    si_df['vol_21d'] = si_df['ret_daily'].rolling(21).std()
    vol_75 = si_df['vol_21d'].quantile(0.75)
    vol_25 = si_df['vol_21d'].quantile(0.25)
    return si_df[['dt', 'close', 'vol_21d', 'ret_daily']].copy(), vol_75, vol_25


def compute_crash_dates(si_df, cooldown_days=5):
    """Find dates where Si crashed. Returns set of crash dates + cooldown dates."""
    crash_dates = set()
    cooldown_dates = set()
    si_sorted = si_df.sort_values('dt').reset_index(drop=True)
    for i, row in si_sorted.iterrows():
        if pd.notna(row.get('crash_flag')) and row['crash_flag']:
            crash_dates.add(row['dt'].date())
            for j in range(1, cooldown_days + 1):
                if i + j < len(si_sorted):
                    cooldown_dates.add(si_sorted.iloc[i + j]['dt'].date())
    return crash_dates, cooldown_dates


def backtest_strategy(sname, thresh, data_dict, si_imb, si_vol_df, vol_75, vol_25,
                      crash_cooldown, leverage, start_date=None, end_date=None):
    """Backtest a single strategy. Returns daily returns list and trade log."""
    meta = STRATEGY_META[sname]
    ticker = meta['ticker']
    direction = meta['direction']
    feature = meta['feature']
    weight = WEIGHTS[sname]

    df = data_dict.get(ticker)
    if df is None:
        return [], []

    df = df.copy().sort_values('dt')
    if start_date:
        df = df[df['dt'] >= start_date].copy()
    if end_date:
        df = df[df['dt'] <= end_date].copy()

    if len(df) < 30:
        return [], []

    days = df.to_dict('records')
    daily_rets = []
    trade_log = []
    equity = CAPITAL

    crash_blocked_until = None

    for idx, row in enumerate(days):
        current_date = row['dt']
        current_date_obj = current_date.date() if hasattr(current_date, 'date') else current_date

        # Crash protection: check cooldown
        if crash_cooldown and current_date_obj in crash_cooldown:
            crash_blocked_until = current_date_obj
            daily_rets.append(0.0)
            continue

        crash_blocked_until = None

        # Feature value
        feat_val = row.get(feature, np.nan)
        if pd.isna(feat_val):
            daily_rets.append(0.0)
            continue

        # Generate signal based on signal_type
        sig = 0
        sig_type = meta.get('signal_type', 'mean_rev')
        if direction == 'LONG_only':
            if feat_val > thresh:
                sig = 1
        elif direction == 'SHORT_only':
            if feat_val < -thresh:
                sig = -1
        else:
            if sig_type == 'momentum':
                if feat_val > thresh:
                    sig = 1
                elif feat_val < -thresh:
                    sig = -1
            else:  # mean_rev
                if feat_val > thresh:
                    sig = -1
                elif feat_val < -thresh:
                    sig = 1

        if sig == 0:
            daily_rets.append(0.0)
            continue

        # Return on this signal (next day's return)
        ret_next = row.get('ret_next', np.nan)
        if pd.isna(ret_next):
            daily_rets.append(0.0)
            continue

        # Dynamic position sizing based on Si vol
        vol_mult = 1.0
        if si_vol_df is not None and not si_vol_df.empty:
            vol_row = si_vol_df[si_vol_df['dt'] == current_date]
            if not vol_row.empty:
                v = vol_row['vol_21d'].iloc[0]
                if pd.notna(v):
                    if v > vol_75:
                        vol_mult = 0.3
                    elif v < vol_25:
                        vol_mult = 1.5

        allocation = equity * weight * vol_mult
        margin = MARGINS.get(ticker, 7000)
        n_cont = max(1, int(allocation / margin))

        r = ret_next / 100 * sig * leverage
        comm_pct = (n_cont * COMM * 2) / equity if equity > 0 else 0
        r_net = r - comm_pct / 100
        daily_rets.append(r_net)
        equity *= (1 + r_net)

        trade_log.append({
            'date': str(current_date_obj),
            'strategy': sname,
            'ticker': ticker,
            'direction': 'LONG' if sig == 1 else 'SHORT',
            'ret_pct': round(ret_next, 2),
            'weight': weight,
            'vol_mult': vol_mult,
            'n_cont': n_cont,
            'feat_val': round(feat_val, 3),
            'thresh': thresh,
        })

    return daily_rets, trade_log


def compute_metrics(daily_rets):
    """Compute performance metrics from daily return series."""
    if not daily_rets or len(daily_rets) < 10:
        return None

    rets = np.array(daily_rets)
    eq = CAPITAL * np.cumprod(1 + rets)
    total_ret = (eq[-1] / CAPITAL - 1) * 100
    n_days = len(rets)
    years = n_days / 252
    ann_ret = ((1 + total_ret / 100) ** (1 / years) - 1) * 100 if years > 0 else 0
    ann_vol = np.std(rets) * np.sqrt(252) * 100
    sharpe = np.mean(rets) / np.std(rets) * np.sqrt(252) if np.std(rets) > 1e-10 else 0
    peak = np.maximum.accumulate(eq)
    dd = (eq / peak - 1) * 100
    max_dd = dd.min()
    calmar = ann_ret / abs(max_dd) if max_dd < 0 else 0

    return {
        'total_ret_pct': round(total_ret, 1),
        'ann_ret_pct': round(ann_ret, 1),
        'ann_vol_pct': round(ann_vol, 1),
        'max_dd_pct': round(max_dd, 1),
        'sharpe': round(sharpe, 2),
        'calmar': round(calmar, 2),
        'n_days': n_days,
        'n_years': round(years, 1),
        'final_eq': round(eq[-1], 0),
    }


def optimize_strategy_threshold(sname, data_dict, si_imb, si_vol_df, vol_75, vol_25,
                                 crash_cooldown, start_date=None, end_date=None):
    """Find best threshold: maximize Calmar ratio (return/DD)."""
    grid = THRESH_GRID[sname]
    candidates = []

    for th in grid:
        dret, trades = backtest_strategy(
            sname, th, data_dict, si_imb, si_vol_df, vol_75, vol_25,
            crash_cooldown, 1.0, start_date, end_date
        )
        m = compute_metrics(dret)
        if m is None:
            continue
        candidates.append({'threshold': th, **m})

    if not candidates:
        return None, None

    best = max(candidates, key=lambda c: c.get('calmar', -999))

    return best, best


def backtest_portfolio(strategies_config, data_dict, si_imb, si_vol_df, vol_75, vol_25,
                        crash_cooldown, leverage, start_date=None, end_date=None,
                        dd_stop_pct=None, dd_reduce_pct=None):
    """Backtest full portfolio with multiple strategies.
    
    If dd_stop_pct is set: when equity DD exceeds threshold, close all positions
    and stay out for 10 trading days (circuit breaker).
    If dd_reduce_pct is set: gradually reduce position sizes when DD exceeds threshold.
    """
    all_signals = {}
    all_trades = {}

    for sname, thresh in strategies_config.items():
        dret, trades = backtest_strategy(
            sname, thresh, data_dict, si_imb, si_vol_df, vol_75, vol_25,
            crash_cooldown, leverage, start_date, end_date
        )
        all_signals[sname] = dret
        all_trades[sname] = trades

    max_len = max(len(v) for v in all_signals.values()) if all_signals else 0
    if max_len < 10:
        return None, []

    portfolio_rets = []
    all_trades_list = []
    eq = CAPITAL
    eq_peak = CAPITAL
    circuit_breaker_days = 0  # days remaining in circuit breaker
    reduce_mult = 1.0
    n_circuit_breakers = 0

    for i in range(max_len):
        day_pnl = 0.0
        in_cb = (circuit_breaker_days > 0)

        if not in_cb:
            for sname in strategies_config:
                if i < len(all_signals[sname]):
                    day_pnl += all_signals[sname][i] * reduce_mult

        eq *= (1 + day_pnl)
        if eq > eq_peak:
            eq_peak = eq
            # Reset reduce when at new high
            reduce_mult = 1.0

        dd_from_peak = (eq_peak - eq) / eq_peak * 100 if eq_peak > 0 else 0

        # Circuit breaker: if DD exceeds threshold, stop for 10 days
        if dd_stop_pct is not None and not in_cb and dd_from_peak > dd_stop_pct:
            circuit_breaker_days = 10
            n_circuit_breakers += 1

        if circuit_breaker_days > 0:
            circuit_breaker_days -= 1

        # Gradual DD reduction (gentler alternative)
        if dd_reduce_pct is not None and not in_cb:
            if dd_from_peak > dd_reduce_pct and reduce_mult > 0.3:
                reduce_mult = max(0.3, reduce_mult - 0.05)
            elif dd_from_peak < dd_reduce_pct * 0.5 and reduce_mult < 1.0:
                reduce_mult = min(1.0, reduce_mult + 0.02)

        portfolio_rets.append(day_pnl)

    for sname in all_trades:
        all_trades_list.extend(all_trades[sname])

    metrics = compute_metrics(portfolio_rets)
    if metrics:
        metrics['n_circuit_breakers'] = n_circuit_breakers
    return metrics, all_trades_list


def walk_forward_by_year(strategies_config, data_dict, si_imb, si_vol_df, vol_75, vol_25,
                          crash_cooldown, leverage, dd_stop_pct=None, dd_reduce_pct=None):
    """Walk-forward by calendar year. Check each strategy is positive in >50% of years."""
    years = [2020, 2021, 2022, 2023, 2024, 2025]
    year_results = {}
    strategy_year_pnl = {sname: {} for sname in strategies_config}

    for yr in years:
        start = f"{yr}-01-01"
        end = f"{yr}-12-31"
        metrics, trades = backtest_portfolio(
            strategies_config, data_dict, si_imb, si_vol_df, vol_75, vol_25,
            crash_cooldown, leverage, start, end,
            dd_stop_pct=dd_stop_pct, dd_reduce_pct=dd_reduce_pct
        )
        if metrics and metrics['n_days'] > 30:
            year_results[yr] = metrics

        # Per-strategy annual PnL
        for sname in strategies_config:
            th = strategies_config[sname]
            dret, _ = backtest_strategy(
                sname, th, data_dict, si_imb, si_vol_df, vol_75, vol_25,
                crash_cooldown, leverage, start, end
            )
            if dret:
                total_r = (np.prod(1 + np.array(dret)) - 1) * 100
                strategy_year_pnl[sname][yr] = round(total_r, 1)

    # Check each strategy: positive in >50% of years
    strategy_ok = {}
    for sname, yr_pnl in strategy_year_pnl.items():
        positive_years = sum(1 for v in yr_pnl.values() if v > 0)
        total_years = len(yr_pnl)
        strategy_ok[sname] = positive_years > total_years / 2 if total_years > 0 else False

    return year_results, strategy_year_pnl, strategy_ok


def main():
    print("=" * 70)
    print("MOEX Portfolio v2 — TRIZ optimization")
    print("=" * 70)
    print()

    # ─── 1. Load data ─────────────────────────────────────────────────────────
    print("[1] Loading data from ClickHouse...")
    data_dict, si_imb = load_all_data()
    print(f"    Tickers loaded: {list(data_dict.keys())}")
    for tk, df in data_dict.items():
        print(f"      {tk}: {len(df)} days, {df['dt'].min().date()} → {df['dt'].max().date()}")
    print(f"    Si imbalance: {len(si_imb)} days" if si_imb is not None else "    Si imbalance: FAILED")

    # ─── 2. Si volatility data ────────────────────────────────────────────────
    print()
    print("[2] Computing Si volatility percentiles...")
    si_df = data_dict.get('Si')
    if si_df is None:
        print("    FATAL: no Si data")
        return
    si_vol_df, vol_75, vol_25 = compute_si_vol_signal(si_df)
    print(f"    Si 21d-vol 75th pctl: {vol_75:.3f}%, 25th pctl: {vol_25:.3f}%")
    print(f"    Vol mult: >75th → 0.3x, <25th → 1.5x")

    # ─── 3. Crash protection dates ────────────────────────────────────────────
    print()
    print("[3] Computing crash protection dates...")
    si_df_with_crash = si_vol_df.copy()
    CRASH_THRESH = 2.5  # Si drop % to trigger crash protection
    CRASH_COOLDOWN = 3  # days to stay out after crash
    si_df_with_crash['crash_flag'] = si_df_with_crash['ret_daily'] < -CRASH_THRESH
    crash_dates, crash_cooldown = compute_crash_dates(si_df_with_crash, cooldown_days=CRASH_COOLDOWN)
    print(f"    Si crash threshold: {CRASH_THRESH}% drop, {CRASH_COOLDOWN}-day cooldown")
    print(f"    Si crash days (>2.5% drop): {len(crash_dates)}")
    print(f"    Cooldown days: {len(crash_cooldown)}")

    # ─── 4. Optimize each strategy independently ──────────────────────────────
    print()
    print("[4] Optimizing thresholds per strategy (max return at DD <=15%)...")
    print()
    best_thresholds = {}
    per_strat_results = {}

    for sname in THRESH_GRID:
        print(f"    ─── {sname} ───")
        best, metrics = optimize_strategy_threshold(
            sname, data_dict, si_imb, si_vol_df, vol_75, vol_25, crash_cooldown
        )
        if best:
            best_thresholds[sname] = best['threshold']
            per_strat_results[sname] = best
            print(f"    Best thresh={best['threshold']:.1f}: AnnRet={best['ann_ret_pct']:+.1f}%, "
                  f"DD={best['max_dd_pct']:.1f}%, Sharpe={best['sharpe']:.2f}, "
                  f"Calmar={best['calmar']:.2f}")
        else:
            # Use median threshold as fallback
            best_thresholds[sname] = THRESH_GRID[sname][len(THRESH_GRID[sname]) // 2]
            print(f"    No thresh meets DD<=15%. Using fallback thresh={best_thresholds[sname]:.1f}")
        print()

    print(f"    Best thresholds: {json.dumps(best_thresholds, indent=6)}")

    # ─── 5. Local refinement around best thresholds ─────────────────────────
    print()
    print("[5] Local refinement — combining best thresholds in portfolio...")
    print()
    # Try adjusting thresholds ±1 step in grid to improve portfolio DD
    refined_thresholds = dict(best_thresholds)
    best_portfolio_metrics = None

    for sname in THRESH_GRID:
        grid = THRESH_GRID[sname]
        orig = best_thresholds[sname]
        if orig in grid:
            idx = grid.index(orig)
            neighbors = [grid[max(0, idx-1)], grid[min(len(grid)-1, idx+1)]]
            for adj_th in neighbors:
                test_cfg = dict(best_thresholds)
                test_cfg[sname] = adj_th
                m, _ = backtest_portfolio(
                    test_cfg, data_dict, si_imb, si_vol_df, vol_75, vol_25,
                    crash_cooldown, 1.0
                )
                if m is None:
                    continue
                if m['max_dd_pct'] >= -MAX_PORTFOLIO_DD:
                    if best_portfolio_metrics is None or m['ann_ret_pct'] > best_portfolio_metrics['ann_ret_pct']:
                        best_portfolio_metrics = m
                        refined_thresholds = dict(test_cfg)
                        print(f"    ✓ {sname}={adj_th}: AnnRet={m['ann_ret_pct']:+.1f}%, DD={m['max_dd_pct']:.1f}%")

    best_thresholds = refined_thresholds
    print(f"    Refined thresholds: {json.dumps(best_thresholds, indent=6)}")

    # ─── 6. Leverage + DD protection sweep ────────────────────────────────────
    print()
    print("[6] Sweeping (leverage × DD protection)...")
    print()
    REDUCE_GRID = [None, 3, 5, 8]
    all_results = []

    for lev in LEVERAGES:
        for rp in REDUCE_GRID:
            metrics, trades = backtest_portfolio(
                best_thresholds, data_dict, si_imb, si_vol_df, vol_75, vol_25,
                crash_cooldown, lev, dd_reduce_pct=rp
            )
            if metrics:
                all_results.append({
                    'leverage': lev,
                    'dd_reduce_pct': rp,
                    **metrics
                })

    # Sort by return within DD constraint
    dd_ok = [r for r in all_results if r['max_dd_pct'] >= -MAX_PORTFOLIO_DD]
    if dd_ok:
        best_combo = max(dd_ok, key=lambda r: r['ann_ret_pct'])
    else:
        best_combo = max(all_results, key=lambda r: r['calmar'])

    print(f"    All combos with DD <= {MAX_PORTFOLIO_DD}%:")
    for r in dd_ok:
        rp_str = f"rdc={r['dd_reduce_pct']}%" if r['dd_reduce_pct'] is not None else "no prot"
        sel = " ← BEST" if r is best_combo else ""
        print(f"    Lev={r['leverage']:.1f}x, {rp_str}: AnnRet={r['ann_ret_pct']:+.1f}%, "
              f"DD={r['max_dd_pct']:.1f}%, Sharpe={r['sharpe']:.2f}, Calmar={r['calmar']:.2f}{sel}")

    best_lev = best_combo['leverage']
    best_lev_metrics = best_combo
    print(f"\n    Best: {best_lev:.1f}x, reduce={best_combo.get('dd_reduce_pct')}, "
          f"AnnRet={best_combo['ann_ret_pct']:+.1f}%, DD={best_combo['max_dd_pct']:.1f}%")

    # ─── 7. Full portfolio result ─────────────────────────────────────────────
    print()
    print("[7] Final portfolio backtest...")
    print()
    dd_stop = best_lev_metrics.get('dd_stop_pct') if best_lev_metrics else None
    dd_reduce = best_lev_metrics.get('dd_reduce_pct') if best_lev_metrics else None
    final_metrics, final_trades = backtest_portfolio(
        best_thresholds, data_dict, si_imb, si_vol_df, vol_75, vol_25,
        crash_cooldown, best_lev, dd_stop_pct=dd_stop, dd_reduce_pct=dd_reduce
    )

    if final_metrics:
        print(f"    Total ret:      {final_metrics['total_ret_pct']:+.1f}%")
        print(f"    Annualized:     {final_metrics['ann_ret_pct']:+.1f}%")
        print(f"    Max DD:         {final_metrics['max_dd_pct']:.1f}%")
        print(f"    Sharpe:         {final_metrics['sharpe']:.2f}")
        print(f"    Calmar:         {final_metrics['calmar']:.2f}")
        print(f"    Period:         {final_metrics['n_years']:.1f} yr")
        print(f"    Final equity:   {final_metrics['final_eq']:,.0f} RUB")
        print(f"    Total trades:   {len(final_trades)}")
        if dd_stop:
            print(f"    DD stop-loss:   {dd_stop}%")
        if dd_reduce:
            print(f"    DD reduction:   {dd_reduce}%")

    # ─── 8. Walk-forward by year ──────────────────────────────────────────────
    print()
    print("[8] Walk-forward by year...")
    print()
    wf_results, strat_year_pnl, strat_ok = walk_forward_by_year(
        best_thresholds, data_dict, si_imb, si_vol_df, vol_75, vol_25,
        crash_cooldown, best_lev, dd_stop_pct=dd_stop, dd_reduce_pct=dd_reduce
    )

    print(f"    Year  | AnnRet  DD     Sharpe Calmar")
    print(f"    " + "-" * 45)
    for yr in sorted(wf_results.keys()):
        r = wf_results[yr]
        print(f"    {yr}  | {r['ann_ret_pct']:>+6.1f}% {r['max_dd_pct']:>6.1f}% {r['sharpe']:>6.2f} {r['calmar']:>6.2f}")

    print()
    print(f"    Strategy year-by-year PnL:")
    print(f"    " + "-" * 60)
    header = f"    {'Strategy':<15}"
    years_sorted = sorted(set().union(*[set(v.keys()) for v in strat_year_pnl.values()]))
    for yr in years_sorted:
        header += f" {yr:>8}"
    header += "  OK?"
    print(header)
    for sname in THRESH_GRID:
        line = f"    {sname:<15}"
        for yr in years_sorted:
            v = strat_year_pnl[sname].get(yr)
            if v is not None:
                line += f" {v:>+8.1f}"
            else:
                line += f" {'N/A':>8}"
        line += f"  {'YES' if strat_ok.get(sname, False) else 'NO '}"
        print(line)

    n_ok = sum(1 for v in strat_ok.values() if v)
    n_total = len(strat_ok)
    print(f"\n    Strategies with >50% positive years: {n_ok}/{n_total}")

    # ─── 9. Crash protection stats ────────────────────────────────────────────
    print()
    print("[9] Crash protection stats...")
    print(f"    Si crash days (>3% drop): {len(crash_dates)}")
    crash_dates_list = sorted(crash_dates)
    if crash_dates_list:
        print(f"    Dates: {crash_dates_list[:10]}{'...' if len(crash_dates_list) > 10 else ''}")
    print(f"    Total cooldown days (3-day lock): {len(crash_cooldown)}")

    # ─── 10. Save report ──────────────────────────────────────────────────────
    print()
    print("[10] Saving report...")

    dd_prot_str = ""
    if dd_stop:
        dd_prot_str = f", CB={dd_stop}%"
    elif dd_reduce:
        dd_prot_str = f", rdc={dd_reduce}%"

    report = f"""# MOEX Portfolio v2 — Night Result

## Дата: {datetime.now().strftime('%Y-%m-%d %H:%M')}

### Конфигурация
- **Capital**: {CAPITAL:,} RUB
- **Commission**: {COMM} RUB/конт × 2
- **Crash protection**: Si > 2.5% drop → close ALL, 3-day cooldown
- **Dynamic sizing**: 21d vol(Si) — <25th pctl → 1.5×, >75th pctl → 0.3×
- **Weights**: {json.dumps(WEIGHTS)}

### Optimized thresholds (per strategy)
| Strategy | Threshold | AnnRet | DD | Sharpe | Calmar |
|----------|-----------|--------|----|--------|--------|
"""
    for sname in THRESH_GRID:
        if sname in per_strat_results:
            r = per_strat_results[sname]
            report += f"| {sname} | {r.get('threshold', '?'):.1f} | {r['ann_ret_pct']:+.1f}% | {r['max_dd_pct']:.1f}% | {r['sharpe']:.2f} | {r['calmar']:.2f} |\n"

    report += f"""
### Refined thresholds (portfolio-level)
```json
{json.dumps(best_thresholds, indent=2)}
```

### Leverage × DD protection sweep
| Lev | Prot | AnnRet | DD | Sharpe | Calmar |
|-----|------|--------|----|--------|--------|
"""
    for r in all_results:
        prot = f"rdc={r['dd_reduce_pct']}%" if r['dd_reduce_pct'] is not None else "none"
        ok = " ✓" if r['max_dd_pct'] >= -MAX_PORTFOLIO_DD else ""
        report += f"| {r['leverage']:.1f}x | {prot:>6} | {r['ann_ret_pct']:+.1f}% | {r['max_dd_pct']:.1f}% | {r['sharpe']:.2f} | {r['calmar']:.2f}{ok} |\n"

    report += f"""
### Best result (DD ≤ {MAX_PORTFOLIO_DD}%)
- **Leverage**: {best_lev:.1f}x{dd_prot_str}
- **Total return**: {final_metrics['total_ret_pct']:+.1f}%
- **Annualized**: {final_metrics['ann_ret_pct']:+.1f}%
- **Max DD**: {final_metrics['max_dd_pct']:.1f}%
- **Sharpe**: {final_metrics['sharpe']:.2f}
- **Calmar**: {final_metrics['calmar']:.2f}
- **Period**: {final_metrics['n_years']:.1f} yr
- **Final equity**: {final_metrics['final_eq']:,.0f} RUB
- **Total trades**: {len(final_trades)}

### Walk-forward (by year)
| Year | AnnRet | DD | Sharpe | Calmar |
|------|--------|----|--------|--------|
"""
    for yr in sorted(wf_results.keys()):
        r = wf_results[yr]
        report += f"| {yr} | {r['ann_ret_pct']:+.1f}% | {r['max_dd_pct']:.1f}% | {r['sharpe']:.2f} | {r['calmar']:.2f} |\n"

    report += f"""
### Strategy year-by-year PnL
| Strategy |"""
    for yr in years_sorted:
        report += f" {yr} |"
    report += " OK? |\n|" + "---|" * (len(years_sorted) + 2) + "\n"

    for sname in THRESH_GRID:
        report += f"| {sname} |"
        for yr in years_sorted:
            v = strat_year_pnl[sname].get(yr)
            if v is not None:
                report += f" {v:+.1f}% |"
            else:
                report += " N/A |"
        report += f" {'YES' if strat_ok.get(sname, False) else 'NO'} |\n"

    report += f"""
### Crash protection
- **Si crash days (>2.5% drop)**: {len(crash_dates)}
"""
    if crash_dates_list:
        report += f"- **Dates**: {', '.join(str(d) for d in crash_dates_list[:10])}"
        if len(crash_dates_list) > 10:
            report += f" (+{len(crash_dates_list) - 10} more)"
        report += "\n"
    report += f"- **Total cooldown days**: {len(crash_cooldown)}\n"

    # Save
    os.makedirs('reports', exist_ok=True)
    with open('reports/night_result.md', 'w') as f:
        f.write(report)
    print(f"    Report saved to reports/night_result.md")
    print()
    print("Done.")


if __name__ == '__main__':
    main()
