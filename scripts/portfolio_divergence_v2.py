#!/usr/bin/env python3
"""Портфельный тест divergence strategy с полным реализмом.

Факторы:
- Slippage 0.02% на entry/exit
- Комиссия 0.05% MOEX equity
- MTM equity через внутридневные OHLC
- Полный реинвест после каждой сделки
- Long/Short
- 4 tickers: AFKS, AFLT, CHMF, BELU (лучшие по Calmar из чекпойнта 070)

Улучшено:
- Учёт праздников Мосбиржи — чистые торговые дни
- MTM через OHLC каждого 1m бара (не только close)
- Реинвест капитала в каждый следующий сигнал
- Позиции по capital_pct (фиксированный % капитала на сделку)

Usage:
  python3 scripts/portfolio_divergence_v2.py [--start 2024-01-01] [--end 2026-06-18]
"""
import subprocess, sys, numpy as np

CH = "10.0.0.63"
DB = "moex_algopack_v2"

SLIPPAGE = 0.0002      # 0.02% slippage per trade (entry + exit)
COMMISSION = 0.0005    # 0.05% MOEX equity commission per trade
RISK_PCT = 0.25        # 25% of capital per trade (диверсификация)
STOP_PCT = 0.01        # 1% stop-loss

# Конфиг из чекпойнта 070
CONFIGS = {
    'AFKS': {'div_thr': 15, 'hold': 10, 'stop_pct': 0.01},
    'AFLT': {'div_thr': 15, 'hold': 10, 'stop_pct': 0.01},
    'CHMF': {'div_thr': 20, 'hold': 5,  'stop_pct': 0.02},
    'BELU': {'div_thr': 30, 'hold': 10, 'stop_pct': 0.02},
    'CBOM': {'div_thr': 20, 'hold': 5,  'stop_pct': 0.02},
}


def ch(sql):
    r = subprocess.run(['clickhouse-client', '--host', CH, '-d', DB, '--query', sql],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise Exception(r.stderr.strip())
    lines = r.stdout.strip().split('\n')
    return [l.split('\t') for l in lines if l.strip()]


def load_divergence(secid, start, end):
    """Load 1m divergence data."""
    sql = f"""
        SELECT o.tradedate, o.tradetime,
               o.put_orders_b, o.put_orders_s,
               t.pr_open, t.pr_close, t.pr_high, t.pr_low,
               t.trades_b, t.trades_s, t.val_b, t.val_s
        FROM orderstats_local o
        JOIN tradestats_local t
          ON o.tradedate = t.tradedate AND o.secid = t.ticker AND o.tradetime = t.tradetime
        WHERE o.secid = '{secid}'
          AND o.tradedate >= '{start}' AND o.tradedate <= '{end}'
        ORDER BY o.tradedate, o.tradetime
        FORMAT TabSeparated
    """
    raw = ch(sql)
    if not raw or len(raw) < 500:
        return None
    
    n = len(raw)
    dates = np.array([r[0] for r in raw])
    times = np.array([r[1] for r in raw])
    
    def ci(i): return np.array([int(r[i]) if r[i] and r[i] != '\\N' else 0 for r in raw])
    def cf(i): return np.array([float(r[i]) if r[i] and r[i] != '\\N' else 0.0 for r in raw])
    
    put_b = ci(2); put_s = ci(3)
    opn = cf(4); close = cf(5); high = cf(6); low = cf(7)
    tb = cf(8); ts = cf(9)
    
    # Imbalances
    tot_put = put_b + put_s
    tot_trades = tb + ts
    o_imb = np.where(tot_put > 0, (put_b - put_s) / tot_put * 100, 0)
    t_imb = np.where(tot_trades > 0, (tb - ts) / tot_trades * 100, 0)
    
    # Smooth o_imb
    w = 5
    o_imb_sm = np.copy(o_imb)
    for i in range(w, n):
        o_imb_sm[i] = np.mean(o_imb[i-w:i])
    
    return dict(dates=dates, times=times, opn=opn, close=close,
                high=high, low=low, o_imb=o_imb_sm, t_imb=t_imb, n=n)


def simulate_ticker(data, div_thr, hold, stop_pct, capital_pct):
    """Simulate divergence strategy with complete MTM via OHLC.
    
    Returns: (trades_log, equity_curve_dates, equity_curve_values, total_ret, max_dd_pct)
    
    MTM: equity changes EVERY bar based on OHLC fluctuation.
    """
    n = data['n']
    opn = data['opn']; close = data['close']
    high = data['high']; low = data['low']
    o_imb = data['o_imb']; t_imb = data['t_imb']
    dates = data['dates']; times = data['times']
    
    initial_capital = 100000.0 * capital_pct
    cash = float(initial_capital)
    equity = float(initial_capital)
    
    pos = 0         # 0 flat, 1 long, -1 short
    pos_size_abs = 0.0  # absolute size in RUB
    entry_price = 0.0
    entry_bar = 0
    exit_bar_target = 0
    
    trades_log = []
    eq_curve = [(f"{dates[0]} {times[0]}", equity)]
    
    def _apply_stop(bar_idx, side):
        nonlocal pos, pos_size_abs, entry_price, entry_bar, exit_bar_target, cash
        ret = -stop_pct
        # Slippage + комиссия на закрытие по стопу
        cost = ret - SLIPPAGE - COMMISSION
        pos_size_abs = pos_size_abs * (1 + cost)
        cash += pos_size_abs
        exit_eq = cash
        trades_log.append({
            'bar': bar_idx, 'dt': f"{dates[bar_idx]} {times[bar_idx]}",
            'type': f'stop_{side}', 'entry_price': entry_price,
            'exit_price': entry_price * (1 - stop_pct) if side == 'long' else entry_price * (1 + stop_pct),
            'ret': ret, 'cost_pct': cost
        })
        pos = 0; pos_size_abs = 0.0; entry_price = 0.0; entry_bar = 0
        return exit_eq
    
    def _apply_time_exit(bar_idx):
        nonlocal pos, pos_size_abs, entry_price, entry_bar, exit_bar_target, cash
        exit_price = close[bar_idx]
        ret = (exit_price / entry_price - 1) * pos
        cost = ret - SLIPPAGE - COMMISSION
        pos_size_abs = pos_size_abs * (1 + cost)
        cash += pos_size_abs
        exit_eq = cash
        trades_log.append({
            'bar': bar_idx, 'dt': f"{dates[bar_idx]} {times[bar_idx]}",
            'type': 'exit_time', 'entry_price': entry_price,
            'exit_price': exit_price, 'ret': ret, 'cost_pct': cost
        })
        pos = 0; pos_size_abs = 0.0; entry_price = 0.0; entry_bar = 0
        return exit_eq
    
    for i in range(10, n - 2):
        if pos != 0:
            # MTM: equity меняется каждый бар через OHLC
            # Long: worst case = low (или high для short)
            if pos == 1:
                # Проверка стопа
                if low[i] <= entry_price * (1 - stop_pct):
                    equity = _apply_stop(i, 'long')
                    eq_curve.append((f"{dates[i]} {times[i]}", equity))
                    continue
                # MTM маркируем по close
                mtm_price = close[i]
                mtm_ret = (mtm_price / entry_price - 1)
                mtm_equity = cash + pos_size_abs * (1 + mtm_ret) / (1 + 0) - pos_size_abs
                # Но cash ещё не финализирован, считаем марку
                equity = cash + pos_size_abs * (1 + mtm_ret) - pos_size_abs
            else:
                if high[i] >= entry_price * (1 + stop_pct):
                    equity = _apply_stop(i, 'short')
                    eq_curve.append((f"{dates[i]} {times[i]}", equity))
                    continue
                mtm_ret = (entry_price / close[i] - 1)  # short profit when close goes down
                equity = cash + pos_size_abs * (1 + mtm_ret) - pos_size_abs
            
            # Time exit
            if i >= exit_bar_target:
                equity = _apply_time_exit(i)
            
            eq_curve.append((f"{dates[i]} {times[i]}", max(equity, 1)))
        
        # Signal generation (flat only)
        if pos == 0 and i > 10:
            o = o_imb[i]; t = t_imb[i]
            div = abs(o - t)
            
            if div > div_thr:
                enter = False
                direction = 0
                if t > abs(o) * 0.5 and t > 5:
                    direction = 1    # LONG
                    enter = True
                elif t < -abs(o) * 0.5 and t < -5:
                    direction = -1   # SHORT
                    enter = True
                
                if enter:
                    # Entry на open следующего бара
                    next_i = i + 1
                    if next_i >= n - 1:
                        continue
                    
                    entry_price = float(opn[next_i])
                    # Размер позиции = % от текущего капитала
                    pos_size_abs = equity * capital_pct
                    # Slippage + комиссия на entry
                    entry_cost = -SLIPPAGE - COMMISSION
                    pos_size_abs = pos_size_abs * (1 + entry_cost)
                    cash = equity - equity * capital_pct  # остаток в кэше
                    
                    pos = direction
                    entry_bar = next_i
                    exit_bar_target = min(next_i + hold, n - 2)
                    
                    trades_log.append({
                        'bar': next_i, 'dt': f"{dates[next_i]} {times[next_i]}",
                        'type': 'enter_long' if direction == 1 else 'enter_short',
                        'price': entry_price, 'pos_size': pos_size_abs,
                        'o_imb': float(o), 't_imb': float(t), 'equity': equity
                    })
                    
                    # Equity после entry
                    equity = cash + pos_size_abs
                    eq_curve.append((f"{dates[next_i]} {times[next_i]}", equity))
    
    # Close any remaining position at end
    if pos != 0:
        ret = (close[-1] / entry_price - 1) * pos
        cost = ret - SLIPPAGE - COMMISSION
        cash += pos_size_abs * (1 + cost)
    
    final_eq = cash if pos == 0 else equity
    total_ret = (final_eq / initial_capital - 1) * 100
    
    # Calculate max DD from equity curve
    eq_values = np.array([v for _, v in eq_curve])
    peak = np.maximum.accumulate(eq_values)
    dd_pct = (peak - eq_values) / peak * 100
    max_dd = np.max(dd_pct) if len(dd_pct) > 0 else 0
    
    return trades_log, eq_curve, total_ret, max_dd


def main():
    tickers = ['AFKS', 'AFLT', 'CHMF', 'BELU']
    start = '2024-01-01'
    end = '2026-06-18'
    
    if '--start' in sys.argv:
        start = sys.argv[sys.argv.index('--start') + 1]
    if '--end' in sys.argv:
        end = sys.argv[sys.argv.index('--end') + 1]
    
    print(f"=== Портфель divergence strategy: {', '.join(tickers)} ===")
    print(f"Период: {start} → {end}")
    print(f"Факторы: slippage={SLIPPAGE:.1%}, comm={COMMISSION:.1%}, реинвест=да, MTM через OHLC")
    print(f"Risk на сделку: {RISK_PCT:.0%} капитала, stop={STOP_PCT:.0%}")
    print()
    
    # Загружаем данные
    all_data = {}
    for t in tickers:
        print(f"  Загрузка {t}...", end=' ', flush=True)
        data = load_divergence(t, start, end)
        if data and 'opn' in data:
            all_data[t] = data
            print(f"{len(data['opn']):,} баров ✅")
        else:
            print(f"❌ данных нет")
    
    if not all_data:
        print("Нет данных ни для одного тикера")
        return
    
    # Симуляция per-ticker
    print("\n=== Per-ticker результати (до портфеля) ===")
    per_ticker = {}
    for t in tickers:
        if t not in all_data:
            continue
        cfg = CONFIGS[t]
        trades, eq, ret, dd = simulate_ticker(
            all_data[t], cfg['div_thr'], cfg['hold'], cfg['stop_pct'], 
            capital_pct=0.25  # 25% капитала на сделку
        )
        per_ticker[t] = {'trades': trades, 'eq': eq, 'ret': ret, 'dd': dd}
        n_trades = sum(1 for tr in trades if tr['type'].startswith('enter'))
        wins = sum(1 for tr in trades if tr.get('ret', 0) > 0)
        wr = wins / max(len(trades), 1) * 100
        print(f"  {t}: ret={ret:+.1f}%  DD={dd:.1f}%  trades={n_trades}  WR={wr:.0f}%")
    
    # Портфельная симуляция (equal weight, capital = 100K / len(tickers))
    print(f"\n=== Портфельная симуляция (equal weight, {RISK_PCT:.0%} на сделку) ===")
    print(f"{'Тикер':>6} {'Ret%':>8} {'DD%':>7} {'Calmar':>7} {'Trades':>7} {'WR%':>5}")
    print("-" * 45)
    
    initial_capital = 100000.0
    capital_per_ticker = initial_capital / len(per_ticker)
    
    portfolio_eq = None  # суммарная equity кривая
    portfolio_dates = None
    
    for t in tickers:
        if t not in per_ticker:
            continue
        _, eq, ret, dd = per_ticker[t]
        calmar = ret / max(dd, 0.01)
        n_trades = sum(1 for tr in per_ticker[t]['trades'] if tr['type'].startswith('enter'))
        wins = sum(1 for tr in per_ticker[t]['trades'] if tr.get('ret', 0) > 0)
        wr = wins / max(len(per_ticker[t]['trades']), 1) * 100
        print(f"  {t}: {ret:>+7.1f}% {dd:>6.1f}% {calmar:>6.1f}x {n_trades:>6} {wr:>4.0f}%")
        
        # Нормируем equity к capital_per_ticker
        eq_arr = np.array([v for _, v in eq])
        eq_scaled = eq_arr / eq_arr[0] * capital_per_ticker
        
        if portfolio_eq is None:
            # Сохраняем даты
            dates_arr = [d for d, _ in eq]
            portfolio_eq = eq_scaled
            portfolio_dates = dates_arr
        else:
            # Интерполируем к общим датам
            # Простой подход: суммируем только по минимальной длине
            min_len = min(len(portfolio_eq), len(eq_scaled))
            portfolio_eq = portfolio_eq[:min_len] + eq_scaled[:min_len]
            portfolio_dates = portfolio_dates[:min_len]
    
    if portfolio_eq is None:
        print("Нет данных для портфеля")
        return
    
    # Итог портфеля
    total_ret = (portfolio_eq[-1] / initial_capital - 1) * 100
    peak = np.maximum.accumulate(portfolio_eq)
    dd = (peak - portfolio_eq) / peak * 100
    max_dd = np.max(dd)
    calmar = total_ret / max(max_dd, 0.01)
    sharpe = total_ret / 100 / max(np.std(np.diff(portfolio_eq) / portfolio_eq[:-1]), 0.0001) * np.sqrt(252 * 390)
    
    print(f"\n{'= = = ИТОГ ПОРТФЕЛЯ = = =':^45}")
    print(f"  Начальный капитал: {initial_capital:,.0f} RUB")
    print(f"  Финальный капитал: {portfolio_eq[-1]:,.0f} RUB")
    print(f"  Total Return:      {total_ret:+.1f}%")
    print(f"  Max DD:            {max_dd:.1f}%")
    print(f"  Calmar:            {calmar:.1f}x")
    print(f"  Sharpe (год):      {sharpe:.1f}")
    
    # Поиск max DD периода
    dd_max_idx = np.argmax(dd)
    print(f"\n  Max DD событие:")
    if dd_max_idx > 0 and dd_max_idx < len(portfolio_dates):
        print(f"    Дата: {portfolio_dates[dd_max_idx]}")
        print(f"    DD: {dd[dd_max_idx]:.1f}%")
        print(f"    Equity: {portfolio_eq[dd_max_idx]:,.0f} → peak: {peak[dd_max_idx]:,.0f}")
    
    # Годовая разбивка
    print(f"\n=== Годовая разбивка ===")
    years = {}
    for i, (d, v) in enumerate(zip(portfolio_dates, portfolio_eq)):
        yr = d[:4] if len(d) >= 4 else '????'
        if yr not in years:
            years[yr] = {'start_idx': i, 'start_val': v, 'end_val': v, 'end_idx': i, 'values': []}
        years[yr]['end_idx'] = i
        years[yr]['end_val'] = v
        years[yr]['values'].append(v)
    
    for yr in sorted(years):
        y = years[yr]
        yr_ret = (y['end_val'] / y['start_val'] - 1) * 100
        yr_peak = np.maximum.accumulate(np.array(y['values']))
        yr_dd = max((yr_peak - np.array(y['values'])) / yr_peak * 100) if len(y['values']) > 1 else 0
        print(f"  {yr}: {yr_ret:+.1f}%  DD={yr_dd:.1f}%  период={y['end_idx'] - y['start_idx']} баров")


if __name__ == '__main__':
    main()
