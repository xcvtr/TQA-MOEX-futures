#!/usr/bin/env python3
"""
capital_growth_sim.py — Моделирование разгона депозита 100K RUB на MOEX фьючерсах.

Собирает сигналы со всех 5 стратегий trading_bot за 2 года,
выполняет walk-forward оптимизацию риск-менеджмента,
симулирует разгон с реинвестированием.

Результаты: docs/plans/capital_growth/
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional

import pandas as pd
import numpy as np

# ── Добавляем корень проекта в sys.path ────────────────────────────────────
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

# ── Конфигурация ───────────────────────────────────────────────────────────
INITIAL_CAPITAL = 100_000  # RUB
HISTORY_DAYS = 730  # 2 года
OUTPUT_DIR = os.path.join(PROJECT_ROOT, 'docs', 'plans', 'capital_growth')

# Все конфиги тикеров для расчёта PnL
ALL_TICKER_CONFIGS: dict = {}
ALL_TICKER_CONFIGS.update(TICKERS)
ALL_TICKER_CONFIGS.update(OB_TICKERS)
ALL_TICKER_CONFIGS.update(REVERSION_TICKERS)
ALL_TICKER_CONFIGS.update(VWAP_TICKERS)
ALL_TICKER_CONFIGS.update(OI_DIVERGENCE_TICKERS)

# ── Утилиты ────────────────────────────────────────────────────────────────


def calc_pnl(direction: str, entry: float, exit_price: float, contracts: int, symbol: str) -> float:
    """Рассчитать PnL в рублях. Копия логики из tracker.py."""
    cfg = ALL_TICKER_CONFIGS[symbol]
    minstep = cfg['minstep']
    tick_rub = cfg['tick_rub']
    moves = (exit_price - entry) / minstep
    if direction.upper() == 'SHORT':
        moves = -moves
    return round(moves * tick_rub * contracts, 2)


def max_drawdown(equity: List[float]) -> float:
    """Максимальная просадка в долях (0..1)."""
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


# ── Stop-loss: сканирование 5m баров между entry и exit ─────────────────────

def _tf_hours(tf: str) -> float:
    """Длительность таймфрейма в часах."""
    m = {'5m': 5/60, '15m': 15/60, '30m': 30/60, 'H1': 1, 'H2': 2, 'H4': 4}
    return m.get(tf, 1)

_5m_cache: dict = {}
def _load_5m(ticker: str, days: int = 730) -> list:
    """Загрузить 5m OHLCV с кэшированием."""
    if ticker not in _5m_cache:
        from trading_bot.ob_engine import load_price_data as _ob5
        _5m_cache[ticker] = _ob5(ticker, days)
    return _5m_cache[ticker]

def adjust_exit_for_stop(sig: dict, stop_pct: float) -> dict:
    """
    Проверить, сработал бы стоп-лосс между entry_time и exit_time.
    Если да — выход по стоп-цене. Если нет — оригинальный exit.
    Возвращает скорректированный сигнал.
    """
    entry_price = sig.get('entry', 0)
    direction = sig.get('direction', 'LONG')
    entry_time = str(sig.get('time', ''))
    strategy = sig.get('strategy', '')
    ticker = sig.get('ticker', '')
    
    if stop_pct <= 0 or not entry_time or not ticker:
        return sig  # SL выключен
    
    # Определяем время выхода по горизонту
    # Для каждого сигнала exit_time = entry_time + horizon * TF_duration
    # Но horizon хранится в сигнале не у всех стратегий
    # Используем упрощение: exit = entry_time + ~4 часа (макс. H2*2 или H4*1)
    # Для точности читаем tf из конфига тикера
    cfg = ALL_TICKER_CONFIGS.get(ticker, {})
    tf = cfg.get('tf', 'H1')
    horizon = sig.get('horizon', 2)
    exit_delta_h = _tf_hours(tf) * horizon
    
    if exit_delta_h <= 0:
        return sig
    
    stop_price = entry_price * (1 + stop_pct) if direction == 'SHORT' else entry_price * (1 - stop_pct)
    
    # Загружаем 5m данные
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
            # SL сработал — закрываем по стоп-цене
            sig = dict(sig)
            sig['exit'] = stop_price
            sig['exit_time'] = t
            sig['stopped'] = True
            return sig
        elif direction == 'SHORT' and high >= stop_price:
            sig = dict(sig)
            sig['exit'] = stop_price
            sig['exit_time'] = t
            sig['stopped'] = True
            return sig
    
    # SL не сработал
    sig['stopped'] = False
    return sig




def adjust_exit_for_hedge(sig: dict, hedge_premium_pct: float, hedge_strike_pct: float) -> dict:
    """Option hedge вместо stop-loss.
    
    Покупаем PUT (LONG) или CALL (SHORT) с OTM страйком.
    - Премия платится на каждую сделку (уменьшает return)
    - Убыток ограничен hedge_strike_pct
    - Позиция НЕ выбивается досрочно
    """
    sig = dict(sig)
    entry = sig['entry']
    exit_p = sig['exit']
    direction = sig['direction']
    
    if direction == 'LONG':
        raw_ret = (exit_p - entry) / entry
    else:
        raw_ret = (entry - exit_p) / entry
    
    hedged_ret = raw_ret - hedge_premium_pct
    if hedged_ret < -hedge_strike_pct:
        hedged_ret = -hedge_strike_pct
    
    if direction == 'LONG':
        effective_exit = entry * (1 + hedged_ret)
    else:
        effective_exit = entry * (1 - hedged_ret)
    sig['exit'] = round(effective_exit, 2)
    return sig

# ── 1. Сбор сигналов ────────────────────────────────────────────────────────


def collect_all_signals() -> List[Dict]:
    """Загрузить данные и прогнать все 5 стратегий. Вернуть список сигналов."""
    all_signals: List[Dict] = []
    errors: List[str] = []

    # --- 1. Volume Surge — DISABLED (46.3% WR, avg -0.067%, отрицательное матожидание)
    print("  [VS] DISABLED — negative edge (46.3% WR, avg -0.067%)")
    _ = TICKERS

    # --- 2. Order Block — DISABLED temporarily (regime-dependent, needs stop-loss rework)
    print("  [OB] DISABLED — regime-dependent, will rework with proper SL later")
    _ = OB_TICKERS

    # --- 3. Mean Reversion ---
    for ticker, cfg in REVERSION_TICKERS.items():
        if not cfg.get('enabled', True):
            continue
        print(f"  [REV] Загрузка {ticker}...")
        try:
            rows = rev_load(ticker, HISTORY_DAYS)
            if not rows:
                print(f"    ⚠ Нет данных для {ticker}")
                continue
            ticker_cfg = dict(DEFAULT_REVERSION_CONFIG)
            sigs = detect_mean_reversion_signals_limit(ticker, rows, ticker_cfg)
            for s in sigs:
                if 'strategy' not in s or s['strategy'] == 'reversion':
                    s['strategy'] = 'mean_reversion'
                s['ticker'] = ticker
            all_signals.extend(sigs)
            print(f"    → {len(sigs)} сигналов")
        except Exception as e:
            msg = f"REV {ticker}: {e}"
            errors.append(msg)
            print(f"    ⚠ {msg}")

    # --- VWAP Deviation — excluded (97% signal flood, breaks walk-forward)
    print("  [VWAP] SKIPPED — produces 97% of signals, rare tail events dominate")
    _ = VWAP_TICKERS  # keep import reference

    # --- 5. OI Divergence ---
    for ticker, cfg in OI_DIVERGENCE_TICKERS.items():
        if not cfg.get('enabled', True):
            continue
        print(f"  [OI] Загрузка {ticker}...")
        try:
            ohlcv = load_ohlcv(ticker, HISTORY_DAYS)
            if not ohlcv:
                print(f"    ⚠ Нет данных OHLCV для {ticker}")
                continue
            oi_data = load_oi(ticker, HISTORY_DAYS)
            if not oi_data:
                print(f"    ⚠ Нет данных OI для {ticker}")
                continue
            merged = merge_ohlcv_oi(ohlcv, oi_data)
            if not merged:
                print(f"    ⚠ Нет объединённых данных для {ticker}")
                continue
            sigs = detect_oi_divergence_signals_limit(merged, DEFAULT_OI_DIVERGENCE_CONFIG)
            for s in sigs:
                if 'strategy' not in s or s.get('strategy') == 'otc':
                    s['strategy'] = 'oi_divergence'
                s['ticker'] = ticker
            all_signals.extend(sigs)
            print(f"    → {len(sigs)} сигналов")
        except Exception as e:
            msg = f"OI {ticker}: {e}"
            errors.append(msg)
            print(f"    ⚠ {msg}")

    # --- Постобработка ---
    # Сортировка по time
    all_signals.sort(key=lambda s: str(s.get('time', '')))

    # Удаление дубликатов (одинаковый time + ticker)
    seen = set()
    unique_signals = []
    for s in all_signals:
        key = (str(s.get('time', '')), str(s.get('ticker', '')))
        if key in seen:
            continue
        seen.add(key)
        unique_signals.append(s)

    # Статистика по стратегиям
    strat_counts = {}
    for s in unique_signals:
        strat = s.get('strategy', 'unknown')
        strat_counts[strat] = strat_counts.get(strat, 0) + 1

    print(f"\n  Всего собрано: {len(all_signals)} сигналов")
    print(f"  После удаления дубликатов: {len(unique_signals)} сигналов")
    print(f"  По стратегиям: {strat_counts}")
    if errors:
        print(f"  Ошибок: {len(errors)}")
        for e in errors:
            print(f"    - {e}")

    return unique_signals


# ── 2. Симуляция (ИСПРАВЛЕННАЯ: ГО блокируется, сигнал = полный цикл) ────────


def simulate(
    signals: List[Dict],
    initial_capital: float,
    margin_usage: float,
    max_concurrent: int,
    max_dd_limit: float,
    stop_loss_pct: float = 0.02,
    total_margin_limit: float = 1.0,
) -> Dict:
    """
    Walk through signals sequentially, managing risk.

    Каждый сигнал — это полный цикл entry→exit (return_pct уже включает horizon).
    
    **ВАЖНО**: ГО блокируется при открытии и возвращается при закрытии.
    capital = free_cash + locked_margin
    
    Алгоритм:
    1. Сигнал по тикеру → проверяем лимиты (DD, concurrent)
    2. Рассчитываем контракты: floor(capital * margin_usage / GO)
    3. Блокируем ГО: capital -= contracts * GO
    4. Рассчитываем PnL от этой сделки
    5. Возвращаем ГО + PnL: capital += contracts * GO + pnl
    """
    capital = float(initial_capital)
    equity = [capital]
    margin_ratio_history = [0.0]
    peak = capital
    active: Dict[str, Dict] = {}  # ticker -> position info
    trades: List[Dict] = []

    def _total_equity() -> float:
        """Полный капитал: свободные средства + заблокированное ГО."""
        return capital + sum(p['locked_go'] for p in active.values())

    def _record_margin_usage():
        te = _total_equity()
        if te > 0:
            locked = sum(p['locked_go'] for p in active.values())
            margin_ratio_history.append(locked / te)
        else:
            margin_ratio_history.append(0.0)

    for sig_idx, sig in enumerate(signals):
        tk = sig.get('ticker', '')
        if not tk or tk not in ALL_TICKER_CONFIGS:
            continue

        # ── Drawdown limit (по полному капиталу, а не по свободному) ──
        te = _total_equity()
        dd = (peak - te) / peak if peak > 0 else 0
        if dd > max_dd_limit:
            # Close all at exit prices
            for t in list(active.keys()):
                pos = active.pop(t)
                pnl = calc_pnl(pos['direction'], pos['entry_price'], pos['exit_price'], pos['contracts'], t)
                capital += pos['locked_go'] + pnl
            equity.append(_total_equity())
            _record_margin_usage()
            break

        # ── Закрываем старые позиции по этому тикеру ──
        if tk in active:
            pos = active.pop(tk)
            pnl = calc_pnl(pos['direction'], pos['entry_price'], pos['exit_price'], pos['contracts'], tk)
            capital += pos['locked_go'] + pnl
            peak = max(peak, _total_equity())
            equity.append(_total_equity())
            _record_margin_usage()
            trades.append({
                'ticker': tk,
                'pnl': pnl,
                'entry_time': pos['entry_time'],
                'exit_time': pos.get('exit_time', sig.get('time', '')),
                'direction': pos['direction'],
                'contracts': pos['contracts'],
                'entry_price': pos['entry_price'],
            })

        # ── Проверка лимита concurrent ──
        if len(active) >= max_concurrent:
            continue

        # ── Get ticker config ──
        try:
            cfg = ALL_TICKER_CONFIGS[tk]
        except KeyError:
            continue
        go = cfg.get('go', 0)
        if go <= 0:
            continue

        # ── Calculate number of contracts (from total equity, not just free cash) ──
        total_cap = _total_equity()
        max_risk = total_cap * margin_usage
        contracts = int(max_risk // go) if max_risk >= go else 0
        if contracts < 1:
            continue

        locked_go = contracts * go

        # ── Проверка лимита суммарной маржи ──
        total_locked = sum(p['locked_go'] for p in active.values())
        if total_locked + locked_go > total_cap * total_margin_limit:
            continue

        # ── Apply stop-loss adjustment ──
        sig = adjust_exit_for_stop(sig, stop_loss_pct)

        entry_price = sig.get('entry', 0)
        exit_price = sig.get('exit', 0)
        direction = sig.get('direction', 'LONG')

        # ── Блокируем ГО перед входом ──
        if locked_go > capital:
            continue  # not enough free capital (shouldn't happen with margin_usage)
        capital -= locked_go
        # equity НЕ обновляем здесь — это ещё не результат сделки

        # ── Открываем позицию ──
        active[tk] = {
            'entry_price': entry_price,
            'exit_price': exit_price,
            'direction': direction,
            'contracts': contracts,
            'entry_time': sig.get('time', ''),
            'exit_time': sig.get('time', ''),
            'strategy': sig.get('strategy', ''),
            'locked_go': locked_go,
        }
        _record_margin_usage()

    # ── Close remaining positions at last prices ──
    for tk in list(active.keys()):
        pos = active.pop(tk)
        pnl = calc_pnl(pos['direction'], pos['entry_price'], pos['exit_price'], pos['contracts'], tk)
        capital += pos['locked_go'] + pnl
        peak = max(peak, _total_equity())
        equity.append(_total_equity())
        _record_margin_usage()

    return {
        'final_capital': round(_total_equity(), 2),
        'equity': equity,
        'trades': trades,
        'margin_ratio': margin_ratio_history,
    }


# ── 2b. Adaptive Risk Simulation ─────────────────────────────


def simulate_adaptive(
    signals: List[Dict],
    initial_capital: float,
    base_margin_usage: float,
    max_concurrent: int,
    base_total_margin_limit: float,
    max_dd_limit: float,
    stop_loss_pct: float = 0.02,
) -> Dict:
    """
    Adaptive risk version of simulate().

    compression = current_equity / peak_equity  (cap 1.0, floor 0.3)
    adaptive_margin_usage = base_margin_usage * compression
    adaptive_total_margin_limit = base_total_margin_limit * compression
    """
    capital = float(initial_capital)
    equity = [capital]
    margin_ratio_history = [0.0]
    peak = capital
    compression_history = [1.0]
    active: Dict[str, Dict] = {}
    trades: List[Dict] = []

    def _total_equity() -> float:
        return capital + sum(p['locked_go'] for p in active.values())

    def _record_margin_usage():
        te = _total_equity()
        if te > 0:
            locked = sum(p['locked_go'] for p in active.values())
            margin_ratio_history.append(locked / te)
        else:
            margin_ratio_history.append(0.0)

    for sig_idx, sig in enumerate(signals):
        tk = sig.get('ticker', '')
        if not tk or tk not in ALL_TICKER_CONFIGS:
            continue

        # ── Adaptive compression ──
        te = _total_equity()
        if te > peak:
            peak = te
        compression = te / peak if peak > 0 else 1.0
        compression = min(compression, 1.0)
        compression = max(compression, 0.3)
        compression_history.append(compression)

        adaptive_margin = base_margin_usage * compression
        adaptive_tm_limit = base_total_margin_limit * compression

        # ── Drawdown limit ──
        dd = (peak - te) / peak if peak > 0 else 0
        if dd > max_dd_limit:
            for t in list(active.keys()):
                pos = active.pop(t)
                pnl = calc_pnl(pos['direction'], pos['entry_price'], pos['exit_price'], pos['contracts'], t)
                capital += pos['locked_go'] + pnl
            equity.append(_total_equity())
            _record_margin_usage()
            break

        # ── Close old positions for this ticker ──
        if tk in active:
            pos = active.pop(tk)
            pnl = calc_pnl(pos['direction'], pos['entry_price'], pos['exit_price'], pos['contracts'], tk)
            capital += pos['locked_go'] + pnl
            peak = max(peak, _total_equity())
            equity.append(_total_equity())
            _record_margin_usage()
            trades.append({
                'ticker': tk,
                'pnl': pnl,
                'entry_time': pos['entry_time'],
                'exit_time': pos.get('exit_time', sig.get('time', '')),
                'direction': pos['direction'],
                'contracts': pos['contracts'],
                'entry_price': pos['entry_price'],
            })

        # ── Concurrent limit ──
        if len(active) >= max_concurrent:
            continue

        # ── Ticker config ──
        try:
            cfg = ALL_TICKER_CONFIGS[tk]
        except KeyError:
            continue
        go = cfg.get('go', 0)
        if go <= 0:
            continue

        # ── Calculate contracts with ADAPTIVE margin_usage ──
        total_cap = _total_equity()
        max_risk = total_cap * adaptive_margin
        contracts = int(max_risk // go) if max_risk >= go else 0
        if contracts < 1:
            continue

        locked_go = contracts * go

        # ── Check ADAPTIVE total margin limit ──
        total_locked = sum(p['locked_go'] for p in active.values())
        if total_locked + locked_go > total_cap * adaptive_tm_limit:
            continue

        # ── Stop-loss ──
        sig = adjust_exit_for_stop(sig, stop_loss_pct)

        entry_price = sig.get('entry', 0)
        exit_price = sig.get('exit', 0)
        direction = sig.get('direction', 'LONG')

        # ── Lock GO ──
        if locked_go > capital:
            continue
        capital -= locked_go

        active[tk] = {
            'entry_price': entry_price,
            'exit_price': exit_price,
            'direction': direction,
            'contracts': contracts,
            'entry_time': sig.get('time', ''),
            'exit_time': sig.get('time', ''),
            'strategy': sig.get('strategy', ''),
            'locked_go': locked_go,
        }
        _record_margin_usage()

    # ── Close remaining ──
    for tk in list(active.keys()):
        pos = active.pop(tk)
        pnl = calc_pnl(pos['direction'], pos['entry_price'], pos['exit_price'], pos['contracts'], tk)
        capital += pos['locked_go'] + pnl
        peak = max(peak, _total_equity())
        equity.append(_total_equity())
        _record_margin_usage()

    return {
        'final_capital': round(_total_equity(), 2),
        'equity': equity,
        'trades': trades,
        'margin_ratio': margin_ratio_history,
        'compression': compression_history,
    }





# ── 2b. Adaptive + Option Hedge Simulation ─────────────────────


def simulate_adaptive_hedge(
    signals: List[Dict],
    initial_capital: float,
    base_margin_usage: float,
    max_concurrent: int,
    base_total_margin_limit: float,
    max_dd_limit: float,
    hedge_premium_pct: float = 0.003,
    hedge_strike_pct: float = 0.01,
) -> Dict:
    """Adaptive risk with OPTION HEDGE instead of stop-loss.
    
    Каждая сделка хеджируется опционом:
    - Премия платится на каждую сделку
    - Убыток ограничен hedge_strike_pct
    - Позиция живёт до горизонта
    """
    capital = float(initial_capital)
    equity = [capital]
    margin_ratio_history = [0.0]
    peak = capital
    compression_history = [1.0]
    active: Dict[str, Dict] = {}
    trades: List[Dict] = []

    def _total_equity() -> float:
        return capital + sum(p['locked_go'] for p in active.values())

    def _record_margin_usage():
        te = _total_equity()
        if te > 0:
            locked = sum(p['locked_go'] for p in active.values())
            margin_ratio_history.append(locked / te)
        else:
            margin_ratio_history.append(0.0)

    for sig_idx, sig in enumerate(signals):
        tk = sig.get('ticker', '')
        if not tk or tk not in ALL_TICKER_CONFIGS:
            continue

        te = _total_equity()
        if te > peak:
            peak = te
        compression = te / peak if peak > 0 else 1.0
        compression = min(compression, 1.0)
        compression = max(compression, 0.3)
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
            _record_margin_usage()
            break

        if tk in active:
            pos = active.pop(tk)
            pnl = calc_pnl(pos['direction'], pos['entry_price'], pos['exit_price'], pos['contracts'], tk)
            capital += pos['locked_go'] + pnl
            peak = max(peak, _total_equity())
            equity.append(_total_equity())
            _record_margin_usage()
            trades.append({
                'ticker': tk, 'pnl': pnl,
                'entry_time': pos['entry_time'],
                'exit_time': pos.get('exit_time', sig.get('time', '')),
                'direction': pos['direction'],
                'contracts': pos['contracts'],
                'entry_price': pos['entry_price'],
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

        # ── Apply OPTION HEDGE ──
        sig = adjust_exit_for_hedge(sig, hedge_premium_pct, hedge_strike_pct)

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
            'exit_time': sig.get('time', ''),
            'strategy': sig.get('strategy', ''),
            'locked_go': locked_go,
        }
        _record_margin_usage()

    for tk in list(active.keys()):
        pos = active.pop(tk)
        pnl = calc_pnl(pos['direction'], pos['entry_price'], pos['exit_price'], pos['contracts'], tk)
        capital += pos['locked_go'] + pnl
        peak = max(peak, _total_equity())
        equity.append(_total_equity())
        _record_margin_usage()

    return {
        'final_capital': round(_total_equity(), 2),
        'equity': equity,
        'trades': trades,
        'margin_ratio': margin_ratio_history,
        'compression': compression_history,
    }


# ── 2d. Hybrid Hedge/SL Simulation ──────────────────────────


STRATEGY_HEDGE_MAP = {
    # Strategies that use option hedge (high WR)
    'oi_divergence': 'hedge',
    # Strategies that use stop-loss (lower WR, premium not worth it)
    'reversion': 'stop',
}


def simulate_adaptive_hybrid(
    signals: List[Dict],
    initial_capital: float,
    base_margin_usage: float,
    max_concurrent: int,
    base_total_margin_limit: float,
    max_dd_limit: float,
    hedge_premium_pct: float = 0.002,
    hedge_strike_pct: float = 0.005,
    stop_loss_pct: float = 0.02,
) -> Dict:
    """Adaptive risk with per-strategy choice: HEDGE (high WR) or STOP (low WR).

    OI Divergence → option hedge (survive to horizon, premium is worth it)
    Mean Reversion → stop-loss (cut quickly, premium not worth it)
    """
    capital = float(initial_capital)
    equity = [capital]
    margin_ratio_history = [0.0]
    peak = capital
    compression_history = [1.0]
    active: Dict[str, Dict] = {}
    trades: List[Dict] = []

    def _total_equity() -> float:
        return capital + sum(p['locked_go'] for p in active.values())

    def _record_margin_usage():
        te = _total_equity()
        if te > 0:
            locked = sum(p['locked_go'] for p in active.values())
            margin_ratio_history.append(locked / te)
        else:
            margin_ratio_history.append(0.0)

    for sig_idx, sig in enumerate(signals):
        tk = sig.get('ticker', '')
        if not tk or tk not in ALL_TICKER_CONFIGS:
            continue

        te = _total_equity()
        if te > peak:
            peak = te
        compression = te / peak if peak > 0 else 1.0
        compression = min(compression, 1.0)
        compression = max(compression, 0.3)
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
            _record_margin_usage()
            break

        if tk in active:
            pos = active.pop(tk)
            pnl = calc_pnl(pos['direction'], pos['entry_price'], pos['exit_price'], pos['contracts'], tk)
            capital += pos['locked_go'] + pnl
            peak = max(peak, _total_equity())
            equity.append(_total_equity())
            _record_margin_usage()
            trades.append({
                'ticker': tk, 'pnl': pnl,
                'entry_time': pos['entry_time'],
                'exit_time': pos.get('exit_time', sig.get('time', '')),
                'direction': pos['direction'],
                'contracts': pos['contracts'],
                'entry_price': pos['entry_price'],
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

        # ── Per-strategy risk method ──
        strategy = sig.get('strategy', 'unknown')
        method = STRATEGY_HEDGE_MAP.get(strategy, 'stop')

        if method == 'hedge':
            sig = adjust_exit_for_hedge(sig, hedge_premium_pct, hedge_strike_pct)
        else:
            # Inline stop-loss: cap loss at stop_loss_pct
            sig = dict(sig)
            entry_price_in = sig['entry']
            exit_price_in = sig['exit']
            direction_in = sig['direction']
            raw_ret = (exit_price_in - entry_price_in) / entry_price_in if direction_in == 'LONG' else (entry_price_in - exit_price_in) / entry_price_in
            if raw_ret < -stop_loss_pct:
                capped_ret = -stop_loss_pct
                if direction_in == 'LONG':
                    sig['exit'] = round(entry_price_in * (1 + capped_ret), 2)
                else:
                    sig['exit'] = round(entry_price_in * (1 - capped_ret), 2)

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
            'exit_time': sig.get('time', ''),
            'strategy': sig.get('strategy', ''),
            'locked_go': locked_go,
        }
        _record_margin_usage()

    for tk in list(active.keys()):
        pos = active.pop(tk)
        pnl = calc_pnl(pos['direction'], pos['entry_price'], pos['exit_price'], pos['contracts'], tk)
        capital += pos['locked_go'] + pnl
        peak = max(peak, _total_equity())
        equity.append(_total_equity())
        _record_margin_usage()

    return {
        'final_capital': round(_total_equity(), 2),
        'equity': equity,
        'trades': trades,
        'margin_ratio': margin_ratio_history,
        'compression': compression_history,
    }


# ── 3. Walk-Forward ───────────────────────────────────────────


def get_date_bounds(signals: List[Dict]) -> tuple:
    """Определить реальные даты начала и конца данных."""
    dates = []
    for s in signals:
        t = str(s.get('time', ''))
        if t:
            dates.append(t[:10])
    if not dates:
        return ('2024-01-01', '2025-12-31')
    dates.sort()
    return (dates[0], dates[-1])


def split_folds(signals: List[Dict]) -> List[Dict]:
    """
    Разбить сигналы на 2 фолда по датам.
    Фолды перекрываются на train (rolling window), test не пересекаются.
    """
    start, end = get_date_bounds(signals)
    print(f"  Дата начала данных: {start}")
    print(f"  Дата конца данных:  {end}")

    start_dt = datetime.strptime(start[:10], '%Y-%m-%d')
    end_dt = datetime.strptime(end[:10], '%Y-%m-%d')
    total_days = (end_dt - start_dt).days

    # Делим на 2 равных периода
    mid = start_dt + timedelta(days=total_days // 2)

    folds = [
        {
            'name': 'fold_1',
            'train_start': start,
            'train_end': mid.strftime('%Y-%m-%d') + 'T23:59:59',
            'test_start': (mid + timedelta(days=1)).strftime('%Y-%m-%d'),
            'test_end': end,
        },
    ]

    # Если данных больше 18 месяцев — делаем второй фолд
    if total_days > 365:
        # Второй фолд: train со сдвигом на 1/4, test — остаток
        q1 = start_dt + timedelta(days=total_days // 4)
        q3 = start_dt + timedelta(days=3 * total_days // 4)
        folds.append({
            'name': 'fold_2',
            'train_start': q1.strftime('%Y-%m-%d'),
            'train_end': q3.strftime('%Y-%m-%d') + 'T23:59:59',
            'test_start': (q3 + timedelta(days=1)).strftime('%Y-%m-%d'),
            'test_end': end,
        })

    result = []
    for fold in folds:
        train = [
            s for s in signals
            if fold['train_start'] <= str(s.get('time', '')) <= fold['train_end']
        ]
        test = [
            s for s in signals
            if fold['test_start'] <= str(s.get('time', '')) <= fold['test_end']
        ]
        result.append({
            'name': fold['name'],
            'train': train,
            'test': test,
            'train_start': fold['train_start'][:10],
            'train_end': fold['train_end'][:10],
            'test_start': fold['test_start'][:10],
            'test_end': fold['test_end'][:10],
        })
    return result


def score_func(final_capital: float, equity: List[float], initial_capital: float) -> float:
    """Calmar ratio: return / max_drawdown. Штрафует за просадку сильнее."""
    mdd = max_drawdown(equity)
    ret = (final_capital - initial_capital) / initial_capital
    if ret <= 0 or mdd <= 0.001:
        return ret  # отрицательный ≈ 0 return — плохо
    return ret / mdd  # Calmar ratio — сколько % роста на 1% просадки


def grid_search(signals: List[Dict], initial_capital: float) -> Dict:
    """Grid search по параметрам риск-менеджмента."""
    param_grid = {
        'margin_usage': [0.005, 0.01, 0.015, 0.02, 0.03, 0.05],
        'max_concurrent': [2, 3, 5, 8, 10],
        'total_margin_limit': [0.03, 0.05, 0.08, 0.10, 0.15],
        'max_dd_limit': [0.05, 0.08, 0.10],
        'stop_loss_pct': [0.005, 0.01, 0.015, 0.02],
    }
    # 6 × 5 × 5 × 3 × 4 = 1800 комбинаций

    total = (len(param_grid['margin_usage']) *
             len(param_grid['max_concurrent']) *
             len(param_grid['total_margin_limit']) *
             len(param_grid['max_dd_limit']) *
             len(param_grid['stop_loss_pct']))
    count = 0

    best_under_5: Optional[dict] = None
    best_under_8: Optional[dict] = None
    best_any: Optional[dict] = None

    for mu in param_grid['margin_usage']:
        for mc in param_grid['max_concurrent']:
            for tm in param_grid['total_margin_limit']:
                for dd in param_grid['max_dd_limit']:
                    for sl in param_grid['stop_loss_pct']:
                        count += 1
                        res = simulate(signals, initial_capital, mu, mc, dd, sl, tm)
                        mdd = max_drawdown(res['equity'])
                        score = score_func(res['final_capital'], res['equity'], initial_capital)
                        entry = {
                            'params': {'margin_usage': mu, 'max_concurrent': mc, 'total_margin_limit': tm, 'max_dd_limit': dd, 'stop_loss_pct': sl},
                            'score': score,
                            'max_dd': mdd,
                            'final_capital': res['final_capital'],
                            'result': res,
                        }
                        if mdd <= 0.05 and (best_under_5 is None or score > best_under_5['score']):
                            best_under_5 = entry
                        if mdd <= 0.08 and (best_under_8 is None or score > best_under_8['score']):
                            best_under_8 = entry
                        if best_any is None or score > best_any['score']:
                            best_any = entry

    # Фильтр: max_drawdown ≤ 5%, запасной — 8%
    if best_under_5 is not None:
        best = best_under_5
        dd_threshold_used = 0.05
    elif best_under_8 is not None:
        best = best_under_8
        dd_threshold_used = 0.08
    else:
        best = best_any
        dd_threshold_used = best['max_dd']

    return {
        'best_params': best['params'],
        'best_score': round(best['score'], 4),
        'best_result': best['result'],
        'best_max_dd': best['max_dd'],
        'total_combinations': total,
        'dd_threshold_used': dd_threshold_used,
    }


# ── 4. Full Sweep (без walk-forward) ──────────────────────────────────


def full_sweep_grid_search(signals: List[Dict], initial_capital: float) -> list:
    """Полный grid search на всех сигналах сразу. Возвращает список всех комбинаций, sorted by final_capital DESC."""
    param_grid = {
        'margin_usage': [0.005, 0.01, 0.015, 0.02, 0.03, 0.05],
        'max_concurrent': [2, 3, 5, 8, 10],
        'total_margin_limit': [0.03, 0.05, 0.08, 0.10, 0.15],
        'max_dd_limit': [0.05, 0.08, 0.10],
        'stop_loss_pct': [0.005, 0.01, 0.015, 0.02],
    }

    total = (len(param_grid['margin_usage']) *
             len(param_grid['max_concurrent']) *
             len(param_grid['total_margin_limit']) *
             len(param_grid['max_dd_limit']) *
             len(param_grid['stop_loss_pct']))
    count = 0

    all_results = []

    for mu in param_grid['margin_usage']:
        for mc in param_grid['max_concurrent']:
            for tm in param_grid['total_margin_limit']:
                for dd in param_grid['max_dd_limit']:
                    for sl in param_grid['stop_loss_pct']:
                        count += 1
                        if count % 200 == 0:
                            print(f"    Progress: {count}/{total} ({count*100//total}%)")
                        res = simulate(signals, initial_capital, mu, mc, dd, sl, tm)
                        mdd = max_drawdown(res['equity'])
                        all_results.append({
                            'params': {
                                'margin_usage': mu,
                                'max_concurrent': mc,
                                'total_margin_limit': tm,
                                'max_dd_limit': dd,
                                'stop_loss_pct': sl,
                            },
                            'max_dd': mdd,
                            'final_capital': res['final_capital'],
                            'result': res,
                        })

    all_results.sort(key=lambda r: r['final_capital'], reverse=True)
    print(f"    Total combinations evaluated: {count}")
    return all_results


def run_full_sweep(signals: List[Dict], initial_capital: float):
    """Full sweep: grid search на всех сигналах, фильтр DD≤5%, TOP-10 по финальному капиталу."""
    print("\n" + "=" * 60)
    print("  FULL SWEEP (без walk-forward)")
    print(f"  Сигналов: {len(signals)}")
    print(f"  Капитал: {initial_capital:,} RUB")
    print("=" * 60)

    all_results = full_sweep_grid_search(signals, initial_capital)

    # Фильтр DD ≤ 5%
    qualified = [r for r in all_results if r['max_dd'] <= 0.05]
    qualified.sort(key=lambda r: r['final_capital'], reverse=True)

    print(f"\n  Комбинаций с DD ≤ 5%: {len(qualified)} из {len(all_results)}")

    # Edge case: ни одна не прошла
    if not qualified:
        print("\n  ⚠ НИ ОДНА комбинация не дала DD ≤ 5%")
        closest = min(all_results, key=lambda r: r['max_dd'])
        cp = closest['params']
        print(f"\n  🔍 Closest: DD={closest['max_dd']*100:.2f}% "
              f"final_cap={closest['final_capital']:,.0f} "
              f"mu={cp['margin_usage']} mc={cp['max_concurrent']} "
              f"tm={cp['total_margin_limit']} dd_limit={cp['max_dd_limit']} "
              f"sl={cp['stop_loss_pct']}")
        top10 = [closest]
    else:
        top10 = qualified[:10]

    # ── Вывод TOP-10 ──
    print(f"\n  {'#'*50}")
    print(f"  TOP-10 by final_capital (DD ≤ 5%)")
    print(f"  {'#'*50}")
    header = f"  {'Rank':<5} {'final_cap':>10} {'DD%':<7} {'mu':<6} {'mc':<4} {'tm':<6} {'dd_lim':<7} {'sl':<5}"
    print(header)
    print(f"  {'-'*55}")
    for i, r in enumerate(top10):
        p = r['params']
        print(f"  {i+1:<5} {r['final_capital']:>10,.0f} {r['max_dd']*100:<7.2f} "
              f"{p['margin_usage']:<6} {p['max_concurrent']:<4} {p['total_margin_limit']:<6} "
              f"{p['max_dd_limit']:<7} {p['stop_loss_pct']:<5}")

    # ── Сохраняем TOP-10 в CSV ──
    rows = []
    for i, r in enumerate(top10):
        p = r['params']
        rows.append({
            'rank': i + 1,
            'final_capital': r['final_capital'],
            'max_dd_pct': round(r['max_dd'] * 100, 2),
            'margin_usage': p['margin_usage'],
            'max_concurrent': p['max_concurrent'],
            'total_margin_limit': p['total_margin_limit'],
            'max_dd_limit': p['max_dd_limit'],
            'stop_loss_pct': p['stop_loss_pct'],
            'n_trades': len(r['result']['trades']),
        })
    csv_path = os.path.join(OUTPUT_DIR, 'pareto_top10.csv')
    pd.DataFrame(rows).to_csv(csv_path, index=False)
    print(f"\n  ✅ Saved {csv_path}")

    # ── Top-1 детальный отчёт ──
    best = top10[0]
    bp = best['params']
    eq = best['result']['equity']
    trades = best['result']['trades']
    margin_hist = best['result'].get('margin_ratio', [0.0])

    ret_pct = (best['final_capital'] - initial_capital) / initial_capital * 100
    avg_margin = (sum(margin_hist) / len(margin_hist) * 100) if margin_hist else 0
    max_margin = (max(margin_hist) * 100) if margin_hist else 0

    report = [
        "=" * 60,
        "  FULL SWEEP — TOP-1 REPORT",
        "=" * 60,
        f"  Start capital:  {initial_capital:,.0f} RUB",
        f"  Final capital:  {best['final_capital']:,.2f} RUB",
        f"  Return:         {ret_pct:+.2f}%",
        f"  Max DD:         {best['max_dd']*100:.2f}%",
        f"  Trades:         {len(trades)}",
        "",
        f"  Params:",
        f"    margin_usage:       {bp['margin_usage']}",
        f"    max_concurrent:     {bp['max_concurrent']}",
        f"    total_margin_limit: {bp['total_margin_limit']}",
        f"    max_dd_limit:       {bp['max_dd_limit']}",
        f"    stop_loss_pct:      {bp['stop_loss_pct']}",
        "",
        f"  Margin usage:",
        f"    avg: {avg_margin:.1f}%",
        f"    max: {max_margin:.1f}%",
        "",
        "  Trade stats:",
        f"    Win rate: {sum(1 for t in trades if t['pnl'] > 0) / len(trades) * 100:.1f}%" if trades else "    N/A",
        f"    Avg PnL: {sum(t['pnl'] for t in trades) / len(trades):.2f}" if trades else "    N/A",
        "=" * 60,
    ]
    report_text = '\n'.join(report)
    print(f"\n{report_text}")

    # Save equity curve for top-1
    eq_df = pd.DataFrame({'step': range(len(eq)), 'equity': eq})
    eq_csv = os.path.join(OUTPUT_DIR, 'equity_curve_v2.csv')
    eq_df.to_csv(eq_csv, index=False)
    print(f"\n  ✅ Saved {eq_csv} ({len(eq)} steps)")

    # Save summary
    summary_path = os.path.join(OUTPUT_DIR, 'summary_v2.txt')
    with open(summary_path, 'w') as f:
        f.write(report_text)
    print(f"  ✅ Saved {summary_path}")

    return top10


# ── 4b. Adaptive Full Sweep ────────────────────────────────


def full_sweep_adaptive(signals: List[Dict], initial_capital: float) -> list:
    """Полный grid search adaptive risk. Список всех комбинаций, sorted by final_capital DESC."""
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
                                'base_margin_usage': mu,
                                'max_concurrent': mc,
                                'base_total_margin_limit': tm,
                                'max_dd_limit': dd,
                                'stop_loss_pct': sl,
                            },
                            'max_dd': mdd,
                            'final_capital': res['final_capital'],
                            'result': res,
                        })

    all_results.sort(key=lambda r: r['final_capital'], reverse=True)
    print(f"    Total combinations evaluated: {count}")
    return all_results


def run_full_sweep_adaptive(signals: List[Dict], initial_capital: float):
    """Full sweep adaptive: grid search, фильтр DD≤5%, TOP-10."""
    print("\n" + "=" * 60)
    print("  FULL SWEEP ADAPTIVE RISK")
    print(f"  Сигналов: {len(signals)}")
    print(f"  Капитал: {initial_capital:,} RUB")
    print("=" * 60)

    all_results = full_sweep_adaptive(signals, initial_capital)

    qualified = [r for r in all_results if r['max_dd'] <= 0.05]
    qualified.sort(key=lambda r: r['final_capital'], reverse=True)

    print(f"\n  Комбинаций с DD ≤ 5%: {len(qualified)} из {len(all_results)}")

    if not qualified:
        print("\n  ⚠ НИ ОДНА комбинация не дала DD ≤ 5%")
        closest = min(all_results, key=lambda r: r['max_dd'])
        cp = closest['params']
        print(f"\n  🔍 Closest: DD={closest['max_dd']*100:.2f}% "
              f"final_cap={closest['final_capital']:,.0f} "
              f"mu={cp['base_margin_usage']} mc={cp['max_concurrent']} "
              f"tm={cp['base_total_margin_limit']} dd_limit={cp['max_dd_limit']} "
              f"sl={cp['stop_loss_pct']}")
        top10 = [closest]
    else:
        top10 = qualified[:10]

    # ── Вывод TOP-10 ──
    print(f"\n  {'#'*50}")
    print(f"  TOP-10 by final_capital (DD ≤ 5%) — Adaptive Risk")
    print(f"  {'#'*50}")
    header = f"  {'Rank':<5} {'final_cap':>10} {'DD%':<7} {'base_mu':<8} {'mc':<4} {'base_tm':<8} {'dd_lim':<7} {'sl':<5}"
    print(header)
    print(f"  {'-'*60}")
    for i, r in enumerate(top10):
        p = r['params']
        print(f"  {i+1:<5} {r['final_capital']:>10,.0f} {r['max_dd']*100:<7.2f} "
              f"{p['base_margin_usage']:<8} {p['max_concurrent']:<4} {p['base_total_margin_limit']:<8} "
              f"{p['max_dd_limit']:<7} {p['stop_loss_pct']:<5}")

    # ── Сохраняем TOP-10 в CSV ──
    rows = []
    for i, r in enumerate(top10):
        p = r['params']
        rows.append({
            'rank': i + 1,
            'final_capital': r['final_capital'],
            'max_dd_pct': round(r['max_dd'] * 100, 2),
            'base_margin_usage': p['base_margin_usage'],
            'max_concurrent': p['max_concurrent'],
            'base_total_margin_limit': p['base_total_margin_limit'],
            'max_dd_limit': p['max_dd_limit'],
            'stop_loss_pct': p['stop_loss_pct'],
            'n_trades': len(r['result']['trades']),
        })
    csv_path = os.path.join(OUTPUT_DIR, 'pareto_adaptive_top10.csv')
    pd.DataFrame(rows).to_csv(csv_path, index=False)
    print(f"\n  ✅ Saved {csv_path}")

    # ── Top-1 детальный отчёт ──
    best = top10[0]
    bp = best['params']
    eq = best['result']['equity']
    trades = best['result']['trades']
    margin_hist = best['result'].get('margin_ratio', [0.0])
    compression_hist = best['result'].get('compression', [1.0])

    ret_pct = (best['final_capital'] - initial_capital) / initial_capital * 100
    avg_margin = (sum(margin_hist) / len(margin_hist) * 100) if margin_hist else 0
    max_margin = (max(margin_hist) * 100) if margin_hist else 0
    avg_compression = (sum(compression_hist) / len(compression_hist)) if compression_hist else 1.0
    min_compression = min(compression_hist) if compression_hist else 1.0

    report = [
        "=" * 60,
        "  FULL SWEEP ADAPTIVE — TOP-1 REPORT",
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
        f"  Margin usage:",
        f"    avg: {avg_margin:.1f}%",
        f"    max: {max_margin:.1f}%",
        "",
        f"  Compression:",
        f"    avg: {avg_compression:.3f}",
        f"    min: {min_compression:.3f}",
        "",
        "  Trade stats:",
        f"    Win rate: {sum(1 for t in trades if t['pnl'] > 0) / len(trades) * 100:.1f}%" if trades else "    N/A",
        f"    Avg PnL: {sum(t['pnl'] for t in trades) / len(trades):.2f}" if trades else "    N/A",
        "=" * 60,
    ]
    report_text = '\n'.join(report)
    print(f"\n{report_text}")

    # Save equity curve
    eq_df = pd.DataFrame({'step': range(len(eq)), 'equity': eq})
    eq_csv = os.path.join(OUTPUT_DIR, 'equity_curve_adaptive.csv')
    eq_df.to_csv(eq_csv, index=False)
    print(f"\n  ✅ Saved {eq_csv} ({len(eq)} steps)")

    # Save summary
    summary_path = os.path.join(OUTPUT_DIR, 'summary_adaptive.txt')
    with open(summary_path, 'w') as f:
        f.write(report_text)
    print(f"  ✅ Saved {summary_path}")

    return top10


# ── 4c. Sweep DD (generic DD filter) ─────────────────────────


def run_sweep_dd(signals: List[Dict], initial_capital: float, dd_threshold: float):
    """Full sweep adaptive with custom DD filter + verification."""
    dd_pct = int(dd_threshold * 100)
    print("\n" + "=" * 60)
    print(f"  SWEEP DD ≤ {dd_pct}% — ADAPTIVE RISK")
    print(f"  Сигналов: {len(signals)}")
    print(f"  Капитал: {initial_capital:,} RUB")
    print(f"  DD threshold: {dd_threshold*100:.0f}%")
    print("=" * 60)

    all_results = full_sweep_adaptive(signals, initial_capital)

    qualified = [r for r in all_results if r['max_dd'] <= dd_threshold]
    qualified.sort(key=lambda r: r['final_capital'], reverse=True)

    print(f"\n  Комбинаций с DD ≤ {dd_pct}%: {len(qualified)} из {len(all_results)}")

    if not qualified:
        print(f"\n  ⚠ НИ ОДНА комбинация не дала DD ≤ {dd_pct}%")
        closest = min(all_results, key=lambda r: r['max_dd'])
        cp = closest['params']
        print(f"\n  🔍 Closest: DD={closest['max_dd']*100:.2f}% "
              f"final_cap={closest['final_capital']:,.0f} "
              f"mu={cp['base_margin_usage']} mc={cp['max_concurrent']} "
              f"tm={cp['base_total_margin_limit']} dd_limit={cp['max_dd_limit']} "
              f"sl={cp['stop_loss_pct']}")
        top10 = [closest]
    else:
        top10 = qualified[:10]

    # ── Вывод TOP-10 ──
    print(f"\n  {'#'*50}")
    print(f"  TOP-10 by final_capital (DD ≤ {dd_pct}%) — Adaptive Risk")
    print(f"  {'#'*50}")
    header = f"  {'Rank':<5} {'final_cap':>10} {'DD%':<7} {'base_mu':<8} {'mc':<4} {'base_tm':<8} {'dd_lim':<7} {'sl':<5}"
    print(header)
    print(f"  {'-'*60}")
    for i, r in enumerate(top10):
        p = r['params']
        print(f"  {i+1:<5} {r['final_capital']:>10,.0f} {r['max_dd']*100:<7.2f} "
              f"{p['base_margin_usage']:<8} {p['max_concurrent']:<4} {p['base_total_margin_limit']:<8} "
              f"{p['max_dd_limit']:<7} {p['stop_loss_pct']:<5}")

    # ── Сохраняем TOP-10 в CSV ──
    rows = []
    for i, r in enumerate(top10):
        p = r['params']
        rows.append({
            'rank': i + 1,
            'final_capital': r['final_capital'],
            'max_dd_pct': round(r['max_dd'] * 100, 2),
            'base_margin_usage': p['base_margin_usage'],
            'max_concurrent': p['max_concurrent'],
            'base_total_margin_limit': p['base_total_margin_limit'],
            'max_dd_limit': p['max_dd_limit'],
            'stop_loss_pct': p['stop_loss_pct'],
            'n_trades': len(r['result']['trades']),
        })
    csv_path = os.path.join(OUTPUT_DIR, f'pareto_dd{dd_pct}_top10.csv')
    pd.DataFrame(rows).to_csv(csv_path, index=False)
    print(f"\n  ✅ Saved {csv_path}")

    # ── Top-1 детальный отчёт ──
    best = top10[0]
    bp = best['params']
    eq = best['result']['equity']
    trades = best['result']['trades']
    margin_hist = best['result'].get('margin_ratio', [0.0])
    compression_hist = best['result'].get('compression', [1.0])

    ret_pct = (best['final_capital'] - initial_capital) / initial_capital * 100
    avg_margin = (sum(margin_hist) / len(margin_hist) * 100) if margin_hist else 0
    max_margin = (max(margin_hist) * 100) if margin_hist else 0
    avg_compression = (sum(compression_hist) / len(compression_hist)) if compression_hist else 1.0
    min_compression = min(compression_hist) if compression_hist else 1.0

    report = [
        "=" * 60,
        f"  SWEEP DD≤{dd_pct}% — TOP-1 REPORT",
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
        f"  Margin usage:",
        f"    avg: {avg_margin:.1f}%",
        f"    max: {max_margin:.1f}%",
        "",
        f"  Compression:",
        f"    avg: {avg_compression:.3f}",
        f"    min: {min_compression:.3f}",
        "",
        "  Trade stats:",
        f"    Win rate: {sum(1 for t in trades if t['pnl'] > 0) / len(trades) * 100:.1f}%" if trades else "    N/A",
        f"    Avg PnL: {sum(t['pnl'] for t in trades) / len(trades):.2f}" if trades else "    N/A",
        "=" * 60,
    ]
    report_text = '\n'.join(report)
    print(f"\n{report_text}")

    # Save equity curve
    eq_df = pd.DataFrame({'step': range(len(eq)), 'equity': eq})
    eq_csv = os.path.join(OUTPUT_DIR, f'equity_curve_dd{dd_pct}.csv')
    eq_df.to_csv(eq_csv, index=False)
    print(f"\n  ✅ Saved {eq_csv} ({len(eq)} steps)")

    # Save summary
    summary_path = os.path.join(OUTPUT_DIR, f'summary_dd{dd_pct}.txt')
    with open(summary_path, 'w') as f:
        f.write(report_text)
    print(f"  ✅ Saved {summary_path}")

    # ── Verification: TOP-1 adaptive vs static simulate ──
    print(f"\n  {'='*50}")
    print(f"  VERIFICATION: TOP-1 adaptive vs static simulate")
    print(f"  {'='*50}")

    static_res = simulate(
        signals, initial_capital,
        bp['base_margin_usage'], bp['max_concurrent'],
        bp['max_dd_limit'], bp['stop_loss_pct'],
        bp['base_total_margin_limit'],
    )
    static_mdd = max_drawdown(static_res['equity'])
    static_final = static_res['final_capital']
    adaptive_mdd = best['max_dd']
    adaptive_final = best['final_capital']

    print(f"\n    {'Metric':<20} {'Adaptive':>12} {'Static':>12}")
    print(f"    {'-'*44}")
    print(f"    {'Final capital':<20} {adaptive_final:>12,.0f} {static_final:>12,.0f}")
    print(f"    {'Max DD':<20} {adaptive_mdd*100:>11.2f}% {static_mdd*100:>11.2f}%")
    print(f"    {'Return':<20} {(adaptive_final-initial_capital)/initial_capital*100:>11.2f}% "
          f"{(static_final-initial_capital)/initial_capital*100:>11.2f}%")

    if adaptive_mdd <= static_mdd:
        print(f"\n    ✅ PASS: adaptive DD ({adaptive_mdd*100:.2f}%) ≤ static DD ({static_mdd*100:.2f}%)")
    else:
        print(f"\n    ❌ FAIL: adaptive DD ({adaptive_mdd*100:.2f}%) > static DD ({static_mdd*100:.2f}%)")

    return top10


# ── 5. Запуск ───────────────────────────────────────────────────────────


def main():
    # CLI: --sweep-dd N — adaptive risk grid search with DD≤N% filter
    if '--sweep-dd' in sys.argv:
        idx = sys.argv.index('--sweep-dd')
        if idx + 1 >= len(sys.argv):
            print("  ❌ --sweep-dd requires a number (e.g. --sweep-dd 10)")
            sys.exit(1)
        dd_val = float(sys.argv[idx + 1])
        dd_threshold = dd_val / 100.0
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        print("=" * 60)
        print("  CAPITAL GROWTH SIMULATION — SWEEP DD")
        print(f"  Initial capital: {INITIAL_CAPITAL:,} RUB")
        print(f"  DD threshold: {dd_val:.0f}%")
        print("=" * 60)
        all_signals = collect_all_signals()
        if not all_signals:
            print("  ❌ No signals collected! Aborting.")
            sys.exit(1)
        run_sweep_dd(all_signals, INITIAL_CAPITAL, dd_threshold)
        print(f"\n✅ Sweep DD≤{dd_val:.0f}% done!")
        return

    # CLI: --sweep-adaptive — adaptive risk full grid search
    if '--sweep-adaptive' in sys.argv:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        print("=" * 60)
        print("  CAPITAL GROWTH SIMULATION — ADAPTIVE RISK SWEEP")
        print(f"  Initial capital: {INITIAL_CAPITAL:,} RUB")
        print("=" * 60)
        all_signals = collect_all_signals()
        if not all_signals:
            print("  ❌ No signals collected! Aborting.")
            sys.exit(1)
        run_full_sweep_adaptive(all_signals, INITIAL_CAPITAL)
        print("\n✅ Adaptive sweep done!")
        return

    # CLI: --sweep — full grid search без walk-forward
    if '--sweep' in sys.argv:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        print("=" * 60)
        print("  CAPITAL GROWTH SIMULATION — FULL SWEEP")
        print(f"  Initial capital: {INITIAL_CAPITAL:,} RUB")
        print("=" * 60)
        all_signals = collect_all_signals()
        if not all_signals:
            print("  ❌ No signals collected! Aborting.")
            sys.exit(1)
        run_full_sweep(all_signals, INITIAL_CAPITAL)
        print("\n✅ Full sweep done!")
        return
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print("=" * 60)
    print("  CAPITAL GROWTH SIMULATION")
    print(f"  Initial capital: {INITIAL_CAPITAL:,} RUB")
    print(f"  History: {HISTORY_DAYS} days")
    print("=" * 60)

    # ── Step 1: Collect signals ──
    print("\n📡 Step 1: Collecting signals from all strategies...")
    all_signals = collect_all_signals()
    print(f"  Total unique signals: {len(all_signals)}")

    if not all_signals:
        print("  ❌ No signals collected! Aborting.")
        sys.exit(1)

    # ── Step 2: Split into walk-forward folds ──
    print("\n📊 Step 2: Splitting into walk-forward folds...")
    folds = split_folds(all_signals)
    for f in folds:
        print(f"  {f['name']}:")
        print(f"    Train: {f['train_start']}..{f['train_end'][:10]} ({len(f['train'])} sig)")
        print(f"    Test:  {f['test_start'][:10]}..{f['test_end'][:10]} ({len(f['test'])} sig)")

    # ── Step 3: Walk-forward optimization ──
    print("\n⚙️ Step 3: Walk-forward grid search...")
    fold_results = []
    combined_equity = [INITIAL_CAPITAL]
    combined_trades = []
    running_capital = INITIAL_CAPITAL  # капитал переходит между фолдами

    for fold in folds:
        print(f"\n  --- {fold['name']} ---")
        if not fold['train'] or len(fold['train']) < 10:
            print(f"    ⚠ Insufficient train signals ({len(fold['train'])}), skipping")
            continue
        if not fold['test']:
            print(f"    ⚠ No test signals, skipping")
            continue

        # Grid search on train (всегда с INITIAL_CAPITAL для comparability)
        print(f"    Training on {len(fold['train'])} signals...")
        opt = grid_search(fold['train'], INITIAL_CAPITAL)
        bp = opt['best_params']
        tr = opt['best_result']
        train_mdd = max_drawdown(tr['equity'])
        train_ret = (tr['final_capital'] - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100
        print(f"    Best params: margin={bp['margin_usage']}, concurrent={bp['max_concurrent']}, tm_limit={bp.get('total_margin_limit', 'N/A')}, dd_limit={bp['max_dd_limit']}, sl={bp.get('stop_loss_pct', 'N/A')}")
        print(f"    DD filter: ≤{opt.get('dd_threshold_used', 0.05)*100:.0f}%, best train maxDD={opt.get('best_max_dd', 0)*100:.1f}%")
        print(f"    Train result: +{train_ret:.1f}%, maxDD={train_mdd*100:.1f}%")

        # Test with best params — НА ТЕКУЩЕМ running_capital
        print(f"    Testing on {len(fold['test'])} signals (capital={running_capital:,.0f} RUB)...")
        test_res = simulate(fold['test'], running_capital,
                            bp['margin_usage'], bp['max_concurrent'], bp['max_dd_limit'],
                            bp['stop_loss_pct'], bp.get('total_margin_limit', 1.0))
        test_mdd = max_drawdown(test_res['equity'])
        test_ret = (test_res['final_capital'] - running_capital) / running_capital * 100
        print(f"    Test result: {running_capital:,.0f} → {test_res['final_capital']:,.2f} ({test_ret:+.1f}%, maxDD={test_mdd*100:.1f}%)")

        # Store margin stats from test
        test_margin = test_res.get('margin_ratio', [0.0])
        avg_margin_pct = (sum(test_margin) / len(test_margin) * 100) if test_margin else 0
        max_margin_pct = (max(test_margin) * 100) if test_margin else 0

        fold_results.append({
            'fold': fold['name'],
            'train_signals': len(fold['train']),
            'test_signals': len(fold['test']),
            'best_params': bp,
            'train_return_pct': round(train_ret, 2),
            'train_max_dd': round(train_mdd, 4),
            'test_return_pct': round(test_ret, 2),
            'test_max_dd': round(test_mdd, 4),
            'test_trades': len(test_res['trades']),
            'dd_threshold_used': opt.get('dd_threshold_used', 0.05),
            'avg_margin_pct': round(avg_margin_pct, 1),
            'max_margin_pct': round(max_margin_pct, 1),
        })

        # Append test equity to combined curve (relative to running capital)
        for v in test_res['equity'][1:]:
            combined_equity.append(v)
        combined_trades.extend(test_res['trades'])
        running_capital = test_res['final_capital']

    # ── Step 4: Combined results ──
    print("\n📈 Step 4: Final results...")

    combined_ret = (combined_equity[-1] - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100
    combined_mdd = max_drawdown(combined_equity)

    summary_lines = [
        "=" * 60,
        "  CAPITAL GROWTH SIMULATION — SUMMARY",
        "=" * 60,
        f"  Initial capital: {INITIAL_CAPITAL:,} RUB",
        f"  Final capital:   {combined_equity[-1]:,.2f} RUB",
        f"  Total return:    {combined_ret:+.2f}%",
        f"  Max drawdown:    {combined_mdd*100:.1f}%",
        f"  Total trades:    {len(combined_trades)}",
        f"  History:         {HISTORY_DAYS} days",
        "",
        "  Walk-Forward Folds:",
    ]

    for fr in fold_results:
        bp = fr['best_params']
        summary_lines.append(f"    {fr['fold']}:")
        summary_lines.append(f"      Floor: DD≤{fr.get('dd_threshold_used', 0.05)*100:.0f}%")
        summary_lines.append(f"      Train: {fr['train_signals']} sig → "
                             f"{fr['train_return_pct']:+.2f}% (maxDD {fr['train_max_dd']*100:.1f}%)")
        summary_lines.append(f"      Test:  {fr['test_signals']} sig → "
                             f"{fr['test_return_pct']:+.2f}% (maxDD {fr['test_max_dd']*100:.1f}%)")
        summary_lines.append(f"      Params: margin={bp['margin_usage']}, "
                             f"concurrent={bp['max_concurrent']}, tm_limit={bp.get('total_margin_limit','N/A')}, "
                             f"dd_limit={bp['max_dd_limit']}, sl={bp.get('stop_loss_pct','N/A')}")

    summary_lines.extend([
        "",
        "  COMBINED TEST (concatenated):",
        f"    Start:  {INITIAL_CAPITAL:,} RUB",
        f"    Final:  {combined_equity[-1]:,.2f} RUB",
        f"    Return: {combined_ret:+.2f}%",
        f"    Max DD: {combined_mdd*100:.1f}%",
        f"    Trades: {len(combined_trades)}",
        "",
        "  Margin Usage (test period):",
    ])

    for fr in fold_results:
        avg_m = fr.get('avg_margin_pct', 0)
        max_m = fr.get('max_margin_pct', 0)
        summary_lines.append(f"    {fr['fold']}: avg {avg_m:.1f}%, max {max_m:.1f}%")
        bar_len = min(int(max_m / 2), 40)
        summary_lines.append(f"      {'█' * bar_len} ({max_m:.1f}%)")

    summary_lines.extend([
        "",
        "  Output files:",
        f"    {os.path.join(OUTPUT_DIR, 'results_v2.json')}",
        f"    {os.path.join(OUTPUT_DIR, 'equity_curve_v2.csv')}",
        f"    {os.path.join(OUTPUT_DIR, 'summary_v2.txt')}",
        f"    {os.path.join(OUTPUT_DIR, 'optimal_params_v2.csv')}",
        "=" * 60,
    ])

    print('\n'.join(summary_lines))

    # ── Save results ──
    results_data = {
        'initial_capital': INITIAL_CAPITAL,
        'final_capital': combined_equity[-1],
        'total_return_pct': round(combined_ret, 2),
        'max_drawdown_pct': round(combined_mdd * 100, 2),
        'total_trades': len(combined_trades),
        'history_days': HISTORY_DAYS,
        'fold_results': fold_results,
    }
    with open(os.path.join(OUTPUT_DIR, 'results_v2.json'), 'w') as f:
        json.dump(results_data, f, indent=2, ensure_ascii=False)
    print(f"  ✅ Saved results_v2.json")

    eq_df = pd.DataFrame({'step': range(len(combined_equity)), 'equity': combined_equity})
    eq_df.to_csv(os.path.join(OUTPUT_DIR, 'equity_curve_v2.csv'), index=False)
    print(f"  ✅ Saved equity_curve_v2.csv ({len(eq_df)} steps)")

    with open(os.path.join(OUTPUT_DIR, 'summary_v2.txt'), 'w') as f:
        f.write('\n'.join(summary_lines))
    print(f"  ✅ Saved summary_v2.txt")

    params_rows = []
    for fr in fold_results:
        bp = fr['best_params']
        params_rows.append({
            'fold': fr['fold'],
            'margin_usage': bp['margin_usage'],
            'max_concurrent': bp['max_concurrent'],
            'total_margin_limit': bp.get('total_margin_limit', ''),
            'max_dd_limit': bp['max_dd_limit'],
            'stop_loss_pct': bp.get('stop_loss_pct', ''),
            'train_return_pct': fr['train_return_pct'],
            'train_max_dd': fr['train_max_dd'],
            'test_return_pct': fr['test_return_pct'],
            'test_max_dd': fr['test_max_dd'],
            'avg_margin_pct': fr.get('avg_margin_pct', ''),
            'max_margin_pct': fr.get('max_margin_pct', ''),
        })
    params_df = pd.DataFrame(params_rows)
    params_df.to_csv(os.path.join(OUTPUT_DIR, 'optimal_params_v2.csv'), index=False)
    print(f"  ✅ Saved optimal_params_v2.csv")

    print("\n✅ Done!")


if __name__ == '__main__':
    main()
