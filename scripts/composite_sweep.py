#!/usr/bin/env python3
"""
composite_sweep.py — Full composite sweep with ALL strategies.

Extends capital_growth_sim.py with 4 new strategies:
  1. Whale Detection (OI Volume Burst)
  2. Momentum Breakout + OI Confirmation
  3. Volume Profile (HVN)
  4. Spread Trading (pair trading)
  5. OI Divergence v2 (with ATR bands)

Usage:
    python scripts/composite_sweep.py [--sweep-dd 10] [--sweep-adaptive]
"""

import json, os, sys
from datetime import datetime, timedelta, timezone
from typing import List, Dict

import pandas as pd
import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from trading_bot import (
    TICKERS, DEFAULT_CONFIG,
    OB_TICKERS, DEFAULT_OB_CONFIG,
    REVERSION_TICKERS, DEFAULT_REVERSION_CONFIG,
    VWAP_TICKERS, DEFAULT_VWAP_CONFIG,
    OI_DIVERGENCE_TICKERS, DEFAULT_OI_DIVERGENCE_CONFIG,
)
from trading_bot.scanner import load_data
from trading_bot.engine import detect_signals_limit
from trading_bot.ob_engine import detect_order_block_signals, load_price_data as ob_load
from trading_bot.reversion_engine import detect_mean_reversion_signals_limit, load_price_data as rev_load
from trading_bot.vwap_engine import detect_vwap_signals_limit, load_price_data as vwap_load
from trading_bot.new_strategies import (
    detect_oi_divergence_signals_limit,
    load_ohlcv, load_oi, merge_ohlcv_oi,
)
from trading_bot.strategy_whale import detect_whale_signals_limit
from trading_bot.strategy_momentum import detect_momentum_signals_limit
from trading_bot.strategy_profile import detect_profile_signals_limit
from trading_bot.strategy_spread import detect_spread_signals_for_pair
from trading_bot.filters import add_regime_filter_adx, add_atr_channel_filter

# ── Config ──
INITIAL_CAPITAL = 100_000
HISTORY_DAYS = 730
OUTPUT_DIR = os.path.join(PROJECT_ROOT, 'docs', 'plans', 'strategy_v2')

ALL_TICKER_CONFIGS: dict = {}
ALL_TICKER_CONFIGS.update(TICKERS)
ALL_TICKER_CONFIGS.update(OB_TICKERS)
ALL_TICKER_CONFIGS.update(REVERSION_TICKERS)
ALL_TICKER_CONFIGS.update(VWAP_TICKERS)
ALL_TICKER_CONFIGS.update(OI_DIVERGENCE_TICKERS)

# ── New strategy configurations ──
WHALE_TICKERS = {
    'Si': {'enabled': True, 'go': 1000, 'tick_rub': 1.0, 'minstep': 0.01, 'label': 'Si (Whale)', 'horizon': 12, 'max_loss': -5.0},
    'RI': {'enabled': True, 'go': 5000, 'tick_rub': 1.0, 'minstep': 1, 'label': 'RI (Whale)', 'horizon': 12, 'max_loss': -5.0},
    'GL': {'enabled': True, 'go': 5000, 'tick_rub': 1.0, 'minstep': 0.01, 'label': 'GL (Whale)', 'horizon': 12, 'max_loss': -5.0},
    'BR': {'enabled': True, 'go': 5000, 'tick_rub': 1.0, 'minstep': 0.01, 'label': 'BR (Whale)', 'horizon': 12, 'max_loss': -5.0},
    'CNYRUBF': {'enabled': True, 'go': 1000, 'tick_rub': 1.0, 'minstep': 0.01, 'label': 'CNY (Whale)', 'horizon': 12, 'max_loss': -5.0},
    'GZ': {'enabled': True, 'go': 2065, 'tick_rub': 0.01, 'minstep': 0.01, 'label': 'GZ (Whale)', 'horizon': 12, 'max_loss': -5.0},
    'SR': {'enabled': True, 'go': 5719, 'tick_rub': 1.0, 'minstep': 0.01, 'label': 'SR (Whale)', 'horizon': 12, 'max_loss': -5.0},
}
ALL_TICKER_CONFIGS.update(WHALE_TICKERS)

MOMENTUM_TICKERS = {
    'RI': {'enabled': True, 'go': 5000, 'tick_rub': 1.0, 'minstep': 1, 'label': 'RI (Momentum)', 'horizon': 24, 'max_loss': -5.0},
    'GL': {'enabled': True, 'go': 5000, 'tick_rub': 1.0, 'minstep': 0.01, 'label': 'GL (Momentum)', 'horizon': 24, 'max_loss': -5.0},
    'Si': {'enabled': True, 'go': 1000, 'tick_rub': 1.0, 'minstep': 0.01, 'label': 'Si (Momentum)', 'horizon': 24, 'max_loss': -5.0},
    'BR': {'enabled': True, 'go': 5000, 'tick_rub': 1.0, 'minstep': 0.01, 'label': 'BR (Momentum)', 'horizon': 24, 'max_loss': -5.0},
    'CNYRUBF': {'enabled': True, 'go': 1000, 'tick_rub': 1.0, 'minstep': 0.01, 'label': 'CNY (Momentum)', 'horizon': 24, 'max_loss': -5.0},
    'GZ': {'enabled': True, 'go': 2065, 'tick_rub': 0.01, 'minstep': 0.01, 'label': 'GZ (Momentum)', 'horizon': 24, 'max_loss': -5.0},
}
ALL_TICKER_CONFIGS.update(MOMENTUM_TICKERS)

PROFILE_TICKERS = {
    'RI': {'enabled': True, 'go': 5000, 'tick_rub': 1.0, 'minstep': 1, 'label': 'RI (Profile)', 'horizon': 12, 'max_loss': -5.0},
    'GL': {'enabled': True, 'go': 5000, 'tick_rub': 1.0, 'minstep': 0.01, 'label': 'GL (Profile)', 'horizon': 12, 'max_loss': -5.0},
    'Si': {'enabled': True, 'go': 1000, 'tick_rub': 1.0, 'minstep': 0.01, 'label': 'Si (Profile)', 'horizon': 12, 'max_loss': -5.0},
    'GZ': {'enabled': True, 'go': 2065, 'tick_rub': 0.01, 'minstep': 0.01, 'label': 'GZ (Profile)', 'horizon': 12, 'max_loss': -5.0},
    'SR': {'enabled': True, 'go': 5719, 'tick_rub': 1.0, 'minstep': 0.01, 'label': 'SR (Profile)', 'horizon': 12, 'max_loss': -5.0},
}
ALL_TICKER_CONFIGS.update(PROFILE_TICKERS)

# OI Divergence v2 tickers (with ATR filter) — same as OI Div but filtered
OIDIV2_TICKERS = {
    'RI': {'enabled': True, 'go': 5000, 'tick_rub': 1.0, 'minstep': 1, 'label': 'RI (OIDiv2)', 'horizon': 6, 'max_loss': -5.0},
    'GL': {'enabled': True, 'go': 5000, 'tick_rub': 1.0, 'minstep': 0.01, 'label': 'GL (OIDiv2)', 'horizon': 6, 'max_loss': -5.0},
    'Si': {'enabled': True, 'go': 1000, 'tick_rub': 1.0, 'minstep': 0.01, 'label': 'Si (OIDiv2)', 'horizon': 6, 'max_loss': -5.0},
    'CNYRUBF': {'enabled': True, 'go': 1000, 'tick_rub': 1.0, 'minstep': 0.01, 'label': 'CNY (OIDiv2)', 'horizon': 6, 'max_loss': -5.0},
}
ALL_TICKER_CONFIGS.update(OIDIV2_TICKERS)

# ── Spread pairs ──
SPREAD_PAIRS = [
    ('Si_CNYRUBF', 'Si', 'CNYRUBF'),
]

# ── Helpers ──

def calc_pnl(direction, entry, exit_price, contracts, symbol):
    cfg = ALL_TICKER_CONFIGS.get(symbol, {'minstep': 1, 'tick_rub': 1})
    minstep = cfg['minstep']
    tick_rub = cfg.get('tick_rub', 1)
    moves = (exit_price - entry) / minstep
    if direction.upper() == 'SHORT':
        moves = -moves
    return round(moves * tick_rub * contracts, 2)


def max_drawdown(equity):
    if not equity:
        return 0.0
    peak = equity[0]
    mdd = 0.0
    for v in equity:
        if v > peak:
            peak = v
        dd = (peak - v) / peak if peak > 0 else 0
        if dd > mdd:
            mdd = dd
    return mdd


def _tf_hours(tf):
    m = {'5m': 5/60, '15m': 15/60, '30m': 30/60, 'H1': 1, 'H2': 2, 'H4': 4}
    return m.get(tf, 1)


_5m_cache: dict = {}
def _load_5m(ticker, days=730):
    if ticker not in _5m_cache:
        from trading_bot.ob_engine import load_price_data as _ob5
        _5m_cache[ticker] = _ob5(ticker, days)
    return _5m_cache[ticker]


def adjust_exit_for_stop(sig, stop_pct):
    entry_price = sig.get('entry', 0)
    direction = sig.get('direction', 'LONG')
    entry_time = str(sig.get('time', ''))
    ticker = sig.get('ticker', '')

    if stop_pct <= 0 or not entry_time or not ticker:
        return sig

    cfg = ALL_TICKER_CONFIGS.get(ticker, {})
    tf = cfg.get('tf', 'H1')
    horizon = sig.get('horizon', 2)
    exit_delta_h = _tf_hours(tf) * horizon
    if exit_delta_h <= 0:
        return sig

    stop_price = entry_price * (1 + stop_pct) if direction == 'SHORT' else entry_price * (1 - stop_pct)
    rows = _load_5m(ticker)
    if not rows:
        return sig

    entry_dt = datetime.fromisoformat(entry_time)
    exit_dt = entry_dt + timedelta(hours=exit_delta_h)

    for r in rows:
        t = r[0] if isinstance(r[0], str) else str(r[0])
        if t < entry_time:
            continue
        dt = datetime.fromisoformat(t)
        if dt > exit_dt:
            break
        high = float(r[2])
        low = float(r[3])
        if direction == 'LONG' and low <= stop_price:
            sig = dict(sig)
            sig['exit'] = stop_price
            sig['stopped'] = True
            return sig
        elif direction == 'SHORT' and high >= stop_price:
            sig = dict(sig)
            sig['exit'] = stop_price
            sig['stopped'] = True
            return sig

    sig['stopped'] = False
    return sig


# ── Signal Collection ──

def compute_stats(signals):
    if not signals:
        return {'n': 0, 'wr': 0.0, 'pf': 0.0, 'dd': 0.0, 'avg_ret': 0.0}
    returns = [s['return_pct'] for s in signals]
    n = len(returns)
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r < 0]
    wr = len(wins) / n * 100 if n > 0 else 0.0
    sum_wins = sum(wins) if wins else 0.0
    sum_losses = abs(sum(losses)) if losses else 0.0
    pf = sum_wins / sum_losses if sum_losses > 0 else (sum_wins if sum_wins > 0 else 0.0)
    cum, peak, max_dd = 0.0, 0.0, 0.0
    for r in returns:
        cum += r
        if cum > peak:
            peak = cum
        dd = peak - cum
        if peak > 0 and dd > max_dd:
            max_dd = dd
    return {
        'n': n, 'wr': round(wr, 1), 'pf': round(pf, 2),
        'dd': round(max_dd, 1), 'avg_ret': round(sum(returns)/n, 2)
    }


def collect_all_signals() -> List[Dict]:
    """Загрузить все данные, прогнать ВСЕ стратегии. Вернуть список сигналов."""
    all_signals: List[Dict] = []
    errors: List[str] = []

    # ── 1. OI Divergence (existing) ──
    print("[OI Divergence] Running...")
    for ticker, cfg in OI_DIVERGENCE_TICKERS.items():
        if not cfg.get('enabled', True):
            continue
        try:
            ohlcv = load_ohlcv(ticker, HISTORY_DAYS)
            oi_data = load_oi(ticker, HISTORY_DAYS)
            if ohlcv and oi_data:
                merged = merge_ohlcv_oi(ohlcv, oi_data)
                if merged:
                    sigs = detect_oi_divergence_signals_limit(merged, DEFAULT_OI_DIVERGENCE_CONFIG)
                    # Apply ADX regime filter
                    closes = [r['close'] for r in merged]
                    indices = [s['idx'] for s in sigs]
                    sigs = add_regime_filter_adx(sigs, closes, indices, adx_min=20)
                    for s in sigs:
                        s['strategy'] = 'oi_divergence'
                        s['ticker'] = ticker
                    all_signals.extend(sigs)
                    print(f"  {ticker}: {len(sigs)} sig")
        except Exception as e:
            errors.append(f"OI {ticker}: {e}")

    # ── 2. OI Divergence v2 (with ATR filter) ──
    print("[OI Divergence v2] Running...")
    for ticker, cfg in OIDIV2_TICKERS.items():
        if not cfg.get('enabled', True):
            continue
        try:
            ohlcv = load_ohlcv(ticker, HISTORY_DAYS)
            oi_data = load_oi(ticker, HISTORY_DAYS)
            if ohlcv and oi_data:
                merged = merge_ohlcv_oi(ohlcv, oi_data)
                if merged:
                    sigs = detect_oi_divergence_signals_limit(merged, DEFAULT_OI_DIVERGENCE_CONFIG)
                    # Apply ATR channel filter
                    closes = [r['close'] for r in merged]
                    highs = [r['high'] for r in merged]
                    lows = [r['low'] for r in merged]
                    indices = [s['idx'] for s in sigs]
                    sigs = add_atr_channel_filter(sigs, highs, lows, closes, indices)
                    for s in sigs:
                        s['strategy'] = 'oi_divergence_v2'
                        s['ticker'] = ticker
                    all_signals.extend(sigs)
                    print(f"  {ticker}: {len(sigs)} sig (after ATR filter)")
        except Exception as e:
            errors.append(f"OIDIV2 {ticker}: {e}")

    # ── 3. Mean Reversion (existing) ──
    print("[Mean Reversion] Running...")
    for ticker, cfg in REVERSION_TICKERS.items():
        if not cfg.get('enabled', True):
            continue
        try:
            rows = rev_load(ticker, HISTORY_DAYS)
            if rows:
                sigs = detect_mean_reversion_signals_limit(ticker, rows, DEFAULT_REVERSION_CONFIG)
                for s in sigs:
                    s['strategy'] = 'mean_reversion'
                    s['ticker'] = ticker
                all_signals.extend(sigs)
                print(f"  {ticker}: {len(sigs)} sig")
        except Exception as e:
            errors.append(f"REV {ticker}: {e}")

    # ── 4. Whale Detection (tuned: th=2.0 for more signals, fiz_z_max=1.0 to filter noise) ──
    print("[Whale Detection] Running...")
    for ticker, cfg in WHALE_TICKERS.items():
        if not cfg.get('enabled', True):
            continue
        try:
            ohlcv = load_ohlcv(ticker, HISTORY_DAYS)
            oi_data = load_oi(ticker, HISTORY_DAYS)
            if ohlcv and oi_data:
                merged = merge_ohlcv_oi(ohlcv, oi_data)
                if merged:
                    whale_cfg = {'yur_z_thresh': 2.0, 'horizon': 12, 'fiz_z_max': 1.0}
                    sigs = detect_whale_signals_limit(ticker, merged, whale_cfg)
                    for s in sigs:
                        s['strategy'] = 'whale'
                    all_signals.extend(sigs)
                    print(f"  {ticker}: {len(sigs)} sig")
        except Exception as e:
            errors.append(f"Whale {ticker}: {e}")

    # ── 5. Momentum Breakout — DISABLED (WR=47%, negative edge) ──
    print("[Momentum Breakout] DISABLED — WR<50%")

    # ── 6. Volume Profile — DISABLED (WR=32%, negative edge) ──
    print("[Volume Profile] DISABLED — WR<50%")

    # ── 7. Spread Trading (SKIPPED — requires pair-based simulator) ──
    print("[Spread Trading] SKIPPED — requires pair-based simulator")

    # ── Sort + Dedup (per ticker, keep all) ──
    all_signals.sort(key=lambda s: str(s.get('time', '')))

    strat_counts = {}
    for s in all_signals:
        strat = s.get('strategy', 'unknown')
        strat_counts[strat] = strat_counts.get(strat, 0) + 1

    print(f"\n  Total signals: {len(all_signals)}")
    print(f"  By strategy: {strat_counts}")
    if errors:
        print(f"  Errors: {len(errors)}")
        for e in errors[:5]:
            print(f"    - {e}")

    return all_signals


# ── Simulation ──

def simulate(signals, initial_capital, margin_usage, max_concurrent,
             max_dd_limit, stop_loss_pct=0.02, total_margin_limit=1.0):
    capital = float(initial_capital)
    equity = [capital]
    margin_ratio_history = [0.0]
    peak = capital
    active = {}
    trades = []

    def _total_equity():
        return capital + sum(p['locked_go'] for p in active.values())

    def _record_margin():
        te = _total_equity()
        if te > 0:
            locked = sum(p['locked_go'] for p in active.values())
            margin_ratio_history.append(locked / te)
        else:
            margin_ratio_history.append(0.0)

    for sig in signals:
        tk = sig.get('ticker', '')
        if not tk or tk not in ALL_TICKER_CONFIGS:
            continue

        te = _total_equity()
        dd = (peak - te) / peak if peak > 0 else 0
        if dd > max_dd_limit:
            for t in list(active.keys()):
                pos = active.pop(t)
                pnl = calc_pnl(pos['direction'], pos['entry_price'], pos['exit_price'], pos['contracts'], t)
                capital += pos['locked_go'] + pnl
            equity.append(_total_equity())
            _record_margin()
            break

        if tk in active:
            pos = active.pop(tk)
            pnl = calc_pnl(pos['direction'], pos['entry_price'], pos['exit_price'], pos['contracts'], tk)
            capital += pos['locked_go'] + pnl
            peak = max(peak, _total_equity())
            equity.append(_total_equity())
            _record_margin()
            trades.append({
                'ticker': tk, 'pnl': pnl,
                'entry_time': pos['entry_time'],
                'exit_time': sig.get('time', ''),
                'direction': pos['direction'],
                'contracts': pos['contracts'],
                'entry_price': pos['entry_price'],
                'strategy': pos.get('strategy', 'unknown'),
            })

        if len(active) >= max_concurrent:
            continue

        try:
            cfg = ALL_TICKER_CONFIGS[tk]
        except KeyError:
            continue
        go = cfg.get('go', 0)
        if go <= 0:
            continue

        total_cap = _total_equity()
        max_risk = total_cap * margin_usage
        contracts = int(max_risk // go) if max_risk >= go else 0
        if contracts < 1:
            continue
        locked_go = contracts * go

        total_locked = sum(p['locked_go'] for p in active.values())
        if total_locked + locked_go > total_cap * total_margin_limit:
            continue

        sig = adjust_exit_for_stop(sig, stop_loss_pct)
        entry_price = sig.get('entry', 0)
        exit_price = sig.get('exit', 0)
        direction = sig.get('direction', 'LONG')

        if locked_go > capital:
            continue
        capital -= locked_go

        active[tk] = {
            'entry_price': entry_price, 'exit_price': exit_price,
            'direction': direction, 'contracts': contracts,
            'entry_time': sig.get('time', ''),
            'strategy': sig.get('strategy', ''),
            'locked_go': locked_go,
        }
        _record_margin()

    for tk in list(active.keys()):
        pos = active.pop(tk)
        pnl = calc_pnl(pos['direction'], pos['entry_price'], pos['exit_price'], pos['contracts'], tk)
        capital += pos['locked_go'] + pnl
        peak = max(peak, _total_equity())
        equity.append(_total_equity())
        _record_margin()

    return {
        'final_capital': round(_total_equity(), 2),
        'equity': equity, 'trades': trades,
        'margin_ratio': margin_ratio_history,
    }


def simulate_adaptive(signals, initial_capital, base_margin_usage, max_concurrent,
                      base_total_margin_limit, max_dd_limit, stop_loss_pct=0.02):
    capital = float(initial_capital)
    equity = [capital]
    margin_ratio_history = [0.0]
    peak = capital
    compression_history = [1.0]
    active = {}
    trades = []

    def _total_equity():
        return capital + sum(p['locked_go'] for p in active.values())

    def _record_margin():
        te = _total_equity()
        if te > 0:
            locked = sum(p['locked_go'] for p in active.values())
            margin_ratio_history.append(locked / te)
        else:
            margin_ratio_history.append(0.0)

    for sig in signals:
        tk = sig.get('ticker', '')
        if not tk or tk not in ALL_TICKER_CONFIGS:
            continue

        te = _total_equity()
        if te > peak:
            peak = te
        compression = max(min(te / peak if peak > 0 else 1.0, 1.0), 0.3)
        compression_history.append(compression)

        adaptive_margin = base_margin_usage * compression
        adaptive_tm_limit = base_total_margin_limit * compression

        dd = (peak - te) / peak if peak > 0 else 0
        if dd > max_dd_limit:
            for t in list(active.keys()):
                pos = active.pop(t)
                pnl = calc_pnl(pos['direction'], pos['entry_price'], pos['exit_price'], pos['contracts'], t)
                capital += pos['locked_go'] + pnl
            equity.append(_total_equity())
            _record_margin()
            break

        if tk in active:
            pos = active.pop(tk)
            pnl = calc_pnl(pos['direction'], pos['entry_price'], pos['exit_price'], pos['contracts'], tk)
            capital += pos['locked_go'] + pnl
            peak = max(peak, _total_equity())
            equity.append(_total_equity())
            _record_margin()
            trades.append({
                'ticker': tk, 'pnl': pnl,
                'entry_time': pos['entry_time'],
                'exit_time': sig.get('time', ''),
                'direction': pos['direction'],
                'contracts': pos['contracts'],
                'entry_price': pos['entry_price'],
                'strategy': pos.get('strategy', 'unknown'),
            })

        if len(active) >= max_concurrent:
            continue

        try:
            cfg = ALL_TICKER_CONFIGS[tk]
        except KeyError:
            continue
        go = cfg.get('go', 0)
        if go <= 0:
            continue

        total_cap = _total_equity()
        max_risk = total_cap * adaptive_margin
        contracts = int(max_risk // go) if max_risk >= go else 0
        if contracts < 1:
            continue
        locked_go = contracts * go

        total_locked = sum(p['locked_go'] for p in active.values())
        if total_locked + locked_go > total_cap * adaptive_tm_limit:
            continue

        sig = adjust_exit_for_stop(sig, stop_loss_pct)
        entry_price = sig.get('entry', 0)
        exit_price = sig.get('exit', 0)
        direction = sig.get('direction', 'LONG')

        if locked_go > capital:
            continue
        capital -= locked_go

        active[tk] = {
            'entry_price': entry_price, 'exit_price': exit_price,
            'direction': direction, 'contracts': contracts,
            'entry_time': sig.get('time', ''),
            'strategy': sig.get('strategy', ''),
            'locked_go': locked_go,
        }
        _record_margin()

    for tk in list(active.keys()):
        pos = active.pop(tk)
        pnl = calc_pnl(pos['direction'], pos['entry_price'], pos['exit_price'], pos['contracts'], tk)
        capital += pos['locked_go'] + pnl
        peak = max(peak, _total_equity())
        equity.append(_total_equity())
        _record_margin()

    return {
        'final_capital': round(_total_equity(), 2),
        'equity': equity, 'trades': trades,
        'margin_ratio': margin_ratio_history,
        'compression': compression_history,
    }


# ── Grid Search ──

def full_sweep_adaptive(signals, initial_capital):
    param_grid = {
        'base_margin_usage': [0.01, 0.02, 0.03, 0.05, 0.08, 0.10, 0.15],
        'max_concurrent': [2, 3, 5, 8],
        'base_total_margin_limit': [0.05, 0.08, 0.10, 0.15, 0.20, 0.30],
        'max_dd_limit': [0.05, 0.10],
        'stop_loss_pct': [0.005, 0.01, 0.015, 0.02],
    }
    total = (len(param_grid['base_margin_usage']) *
             len(param_grid['max_concurrent']) *
             len(param_grid['base_total_margin_limit']) *
             len(param_grid['max_dd_limit']) *
             len(param_grid['stop_loss_pct']))
    count = 0
    all_results = []

    for mu in param_grid['base_margin_usage']:
        for mc in param_grid['max_concurrent']:
            for tm in param_grid['base_total_margin_limit']:
                for dd in param_grid['max_dd_limit']:
                    for sl in param_grid['stop_loss_pct']:
                        count += 1
                        if count % 200 == 0:
                            print(f"    Progress: {count}/{total} ({count*100//total}%)")
                        res = simulate_adaptive(signals, initial_capital, mu, mc, tm, dd, sl)
                        mdd = max_drawdown(res['equity'])
                        all_results.append({
                            'params': {
                                'base_margin_usage': mu, 'max_concurrent': mc,
                                'base_total_margin_limit': tm, 'max_dd_limit': dd,
                                'stop_loss_pct': sl,
                            },
                            'max_dd': mdd,
                            'final_capital': res['final_capital'],
                            'result': res,
                        })

    all_results.sort(key=lambda r: r['final_capital'], reverse=True)
    print(f"    Total combinations: {count}")
    return all_results


def run_sweep_dd(signals, initial_capital, dd_threshold):
    dd_pct = int(dd_threshold * 100)
    print("\n" + "=" * 60)
    print(f"  COMPOSITE SWEEP DD ≤ {dd_pct}% — ADAPTIVE RISK")
    print(f"  Signals: {len(signals)}")
    print(f"  Capital: {initial_capital:,} RUB")
    print("=" * 60)

    all_results = full_sweep_adaptive(signals, initial_capital)
    qualified = [r for r in all_results if r['max_dd'] <= dd_threshold]
    qualified.sort(key=lambda r: r['final_capital'], reverse=True)

    print(f"\n  With DD ≤ {dd_pct}%: {len(qualified)} / {len(all_results)}")

    if not qualified:
        print(f"  ⚠ No combination meets DD ≤ {dd_pct}%")
        closest = min(all_results, key=lambda r: r['max_dd'])
        cp = closest['params']
        print(f"  Closest: DD={closest['max_dd']*100:.2f}% "
              f"final={closest['final_capital']:,.0f} "
              f"mu={cp['base_margin_usage']} mc={cp['max_concurrent']}")
        top10 = [closest]
    else:
        top10 = qualified[:10]

    # Top-10 table
    print(f"\n  TOP-10 by final_capital (DD ≤ {dd_pct}%)")
    print(f"  {'Rank':<5} {'final_cap':>12} {'DD%':<7} {'base_mu':<8} {'mc':<4} {'base_tm':<8} {'dd_lim':<7} {'sl':<5}")
    print(f"  {'-'*55}")
    for i, r in enumerate(top10):
        p = r['params']
        print(f"  {i+1:<5} {r['final_capital']:>12,.0f} {r['max_dd']*100:<7.2f} "
              f"{p['base_margin_usage']:<8} {p['max_concurrent']:<4} {p['base_total_margin_limit']:<8} "
              f"{p['max_dd_limit']:<7} {p['stop_loss_pct']:<5}")

    # Save
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    rows = []
    for i, r in enumerate(top10):
        p = r['params']
        rows.append({
            'rank': i+1, 'final_capital': r['final_capital'],
            'max_dd_pct': round(r['max_dd']*100, 2),
            'base_margin_usage': p['base_margin_usage'],
            'max_concurrent': p['max_concurrent'],
            'base_total_margin_limit': p['base_total_margin_limit'],
            'max_dd_limit': p['max_dd_limit'],
            'stop_loss_pct': p['stop_loss_pct'],
            'n_trades': len(r['result']['trades']),
        })
    csv_path = os.path.join(OUTPUT_DIR, f'composite_pareto_dd{dd_pct}.csv')
    pd.DataFrame(rows).to_csv(csv_path, index=False)
    print(f"\n  ✅ Saved {csv_path}")

    # Top-1 detailed report
    best = top10[0]
    bp = best['params']
    eq = best['result']['equity']
    trades = best['result']['trades']
    ret_pct = (best['final_capital'] - initial_capital) / initial_capital * 100
    margin_hist = best['result'].get('margin_ratio', [0.0])
    compression_hist = best['result'].get('compression', [1.0])
    avg_margin = (sum(margin_hist)/len(margin_hist)*100) if margin_hist else 0
    avg_compression = (sum(compression_hist)/len(compression_hist)) if compression_hist else 1.0

    report = [
        "=" * 60,
        f"  COMPOSITE SWEEP DD≤{dd_pct}% — TOP-1 REPORT",
        "=" * 60,
        f"  Start capital:  {initial_capital:,.0f} RUB",
        f"  Final capital:  {best['final_capital']:,.2f} RUB",
        f"  Return:         {ret_pct:+.2f}%",
        f"  Max DD:         {best['max_dd']*100:.2f}%",
        f"  Trades:         {len(trades)}",
        "",
        f"  Params:",
        f"    base_margin_usage:       {bp['base_margin_usage']}",
        f"    max_concurrent:          {bp['max_concurrent']}",
        f"    base_total_margin_limit: {bp['base_total_margin_limit']}",
        f"    max_dd_limit:            {bp['max_dd_limit']}",
        f"    stop_loss_pct:           {bp['stop_loss_pct']}",
        "",
        f"  Margin: avg {avg_margin:.1f}%",
        f"  Compression: avg {avg_compression:.3f}",
        "",
        f"  Trade stats:",
        f"    Win rate: {sum(1 for t in trades if t['pnl']>0)/len(trades)*100:.1f}%" if trades else "    N/A",
        f"    Avg PnL: {sum(t['pnl'] for t in trades)/len(trades):.2f}" if trades else "    N/A",
        "",
        "  Strategy breakdown:",
    ]

    if trades:
        strat_pnl = {}
        for t in trades:
            strat = t.get('strategy', 'unknown')
            if strat not in strat_pnl:
                strat_pnl[strat] = {'n': 0, 'pnl': 0, 'wins': 0}
            strat_pnl[strat]['n'] += 1
            strat_pnl[strat]['pnl'] += t['pnl']
            if t['pnl'] > 0:
                strat_pnl[strat]['wins'] += 1
        for s, v in sorted(strat_pnl.items(), key=lambda x: x[1]['pnl'], reverse=True):
            wr = v['wins'] / v['n'] * 100 if v['n'] > 0 else 0
            report.append(f"    {s:<20} sig={v['n']:<4} WR={wr:.1f}%  PnL={v['pnl']:+.0f} RUB")

    report.append("=" * 60)
    report_text = '\n'.join(report)
    print(f"\n{report_text}")

    summary_path = os.path.join(OUTPUT_DIR, f'composite_summary_dd{dd_pct}.txt')
    with open(summary_path, 'w') as f:
        f.write(report_text)
    print(f"  ✅ Saved {summary_path}")

    eq_df = pd.DataFrame({'step': range(len(eq)), 'equity': eq})
    eq_csv = os.path.join(OUTPUT_DIR, f'composite_equity_dd{dd_pct}.csv')
    eq_df.to_csv(eq_csv, index=False)
    print(f"  ✅ Saved {eq_csv}")

    return top10


def main():
    if '--sweep-dd' in sys.argv:
        idx = sys.argv.index('--sweep-dd')
        dd_val = float(sys.argv[idx + 1])
        dd_threshold = dd_val / 100.0
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        print("=" * 60)
        print("  COMPOSITE STRATEGY SWEEP")
        print(f"  Capital: {INITIAL_CAPITAL:,} RUB")
        print(f"  History: {HISTORY_DAYS} days")
        print("=" * 60)

        all_signals = collect_all_signals()
        if not all_signals:
            print("  ❌ No signals!")
            sys.exit(1)

        # Per-strategy stats
        print("\n── Per-Strategy Stats ──")
        strat_signals = {}
        for s in all_signals:
            strat = s.get('strategy', 'unknown')
            if strat not in strat_signals:
                strat_signals[strat] = []
            strat_signals[strat].append(s)
        for strat, sigs in sorted(strat_signals.items(), key=lambda x: len(x[1]), reverse=True):
            st = compute_stats(sigs)
            print(f"  {strat:<20} {st['n']:<5} sig WR={st['wr']:<6}% PF={st['pf']:<6} avgRet={st['avg_ret']:<8}% DD={st['dd']:<6}%")

        run_sweep_dd(all_signals, INITIAL_CAPITAL, dd_threshold)
        print(f"\n✅ Done! Results in {OUTPUT_DIR}")
        return

    # Default: show help
    print("Usage: python scripts/composite_sweep.py --sweep-dd 10")
    print("       python scripts/composite_sweep.py --sweep-dd 5")


if __name__ == '__main__':
    main()
