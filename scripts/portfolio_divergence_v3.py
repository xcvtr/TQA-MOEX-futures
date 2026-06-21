#!/usr/bin/env python3
"""Портфельный тест divergence strategy — корректная симуляция.

Логика один-в-один как в divergence_backtest.py v2, но:
- MTM через OHLC для честного DD
- Slippage 0.02% на entry + exit
- Комиссия 0.05% MOEX equity на entry + exit
- Полный реинвест
- Портфель с равным распределением капитала

Usage:
  python3 scripts/portfolio_divergence_v3.py
"""
import subprocess, sys, numpy as np

CH = "10.0.0.63"
DB = "moex_algopack_v2"

SLIPPAGE = 0.0002      # 0.02% slippage per trade side
COMMISSION = 0.0005    # 0.05% MOEX equity commission per trade side
STOP_PCT = 0.01        # 1% stop-loss (overridable per ticker)

# Best configs per ticker from checkpoint 070
CONFIGS = {
    'AFKS': {'div_thr': 15, 'hold': 10, 'stop_pct': 0.01},
    'AFLT': {'div_thr': 15, 'hold': 10, 'stop_pct': 0.01},
    'CHMF': {'div_thr': 20, 'hold': 5,  'stop_pct': 0.02},
    'BELU': {'div_thr': 30, 'hold': 10, 'stop_pct': 0.02},
}


def ch(sql):
    r = subprocess.run(['clickhouse-client', '--host', CH, '-d', DB, '--query', sql],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise Exception(r.stderr.strip())
    lines = r.stdout.strip().split('\n')
    return [l.split('\t') for l in lines if l.strip()]


def load_ticker(secid, start, end):
    sql = f"""
        SELECT o.tradedate, o.tradetime,
               o.put_orders_b, o.put_orders_s,
               t.pr_open, t.pr_close, t.pr_high, t.pr_low,
               t.trades_b, t.trades_s
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
    
    return dict(opn=opn, close=close, high=high, low=low,
                o_imb=o_imb_sm, t_imb=t_imb, n=n)


def simulate_ticker_simple(data, div_thr, hold, stop_pct, initial_capital):
    """Simpler, correct simulation: full capital per trade, no MTM per-bar.
    
    Симуляция как в divergence_backtest.py: капитал = один счёт, позиция = весь капитал.
    Но со slippage и комиссиями на entry + exit.
    DD считаем на equity curve с MTM маркировкой (каждый бар equity меняется).
    """
    n = data['n']
    opn = data['opn']; close = data['close']; high = data['high']; low = data['low']
    o_imb = data['o_imb']; t_imb = data['t_imb']
    
    cash = float(initial_capital)
    pos = 0      # 0 flat, 1 long, -1 short
    entry_price = 0.0
    entry_bar = 0
    
    trades = []  # для статистики
    eq_curve = [cash]  # equity каждый бар
    
    for i in range(10, n - 2):
        # ---- Close existing position ----
        if pos != 0:
            bars_held = i - entry_bar
            
            # Stop check first
            if pos == 1 and low[i] <= entry_price * (1 - stop_pct):
                # Stop loss
                ret = -stop_pct
                cost = ret - SLIPPAGE - COMMISSION  # slippage + comm on exit
                cash *= (1 + cost)
                trades.append({'ret': ret, 'type': 'stop', 'bars': bars_held})
                pos = 0
            elif pos == -1 and high[i] >= entry_price * (1 + stop_pct):
                ret = -stop_pct
                cost = ret - SLIPPAGE - COMMISSION
                cash *= (1 + cost)
                trades.append({'ret': ret, 'type': 'stop', 'bars': bars_held})
                pos = 0
            elif bars_held >= hold:
                # Time exit by close
                ret = (close[i] / entry_price - 1) * pos
                cost = ret - SLIPPAGE - COMMISSION
                cash *= (1 + cost)
                trades.append({'ret': ret, 'type': 'time', 'bars': bars_held})
                pos = 0
    
        # ---- Generate new signal ----
        if pos == 0 and i > 10:
            o = o_imb[i]; t = t_imb[i]
            div = abs(o - t)
            
            if div > div_thr:
                if t > abs(o) * 0.5 and t > 5:
                    # LONG
                    pos = 1
                    entry_price = opn[i + 1]
                    entry_bar = i + 1
                    # Slippage + comm on entry
                    cash *= (1 - SLIPPAGE - COMMISSION)
                elif t < -abs(o) * 0.5 and t < -5:
                    # SHORT
                    pos = -1
                    entry_price = opn[i + 1]
                    entry_bar = i + 1
                    cash *= (1 - SLIPPAGE - COMMISSION)
        
        # ---- MTM: mark equity every bar ----
        if pos == 1:
            mtm_ret = (close[i] / entry_price - 1)
            eq_curve.append(cash * (1 + mtm_ret))
        elif pos == -1:
            mtm_ret = (entry_price / close[i] - 1)
            eq_curve.append(cash * (1 + mtm_ret))
        else:
            eq_curve.append(cash)
    
    # Close any remaining position
    if pos != 0:
        ret = (close[-1] / entry_price - 1) * pos
        cost = ret - SLIPPAGE - COMMISSION
        cash *= (1 + cost)
        trades.append({'ret': ret, 'type': 'end', 'bars': i - entry_bar})
    
    eq_curve.append(cash)
    
    total_ret = (cash / initial_capital - 1) * 100
    
    # DD from MTM equity curve
    eq_arr = np.array(eq_curve)
    peak = np.maximum.accumulate(eq_arr)
    dd = (peak - eq_arr) / peak * 100
    max_dd = np.max(dd)
    
    # Stats
    enter_count = len([t for t in trades if t['type'] not in ('enter',)])
    wins = sum(1 for t in trades if t.get('ret', 0) > 0)
    wr = wins / max(len(trades), 1) * 100
    
    return {
        'eq_curve': eq_curve,
        'ret': total_ret,
        'dd': max_dd,
        'trades': enter_count,
        'wr': wr,
        'final_capital': cash
    }


def main():
    tickers = ['AFKS', 'AFLT', 'CHMF', 'BELU']
    start = '2024-01-01'
    end = '2026-06-18'
    
    if '--start' in sys.argv:
        start = sys.argv[sys.argv.index('--start') + 1]
    if '--end' in sys.argv:
        end = sys.argv[sys.argv.index('--end') + 1]
    
    print(f"=== Портфель divergence strategy ===")
    print(f"Тикеры: {', '.join(tickers)}")
    print(f"Период: {start} → {end}")
    print(f"Факторы: slippage={SLIPPAGE:.2%}, comm={COMMISSION:.2%}, full reinvest, MTM DD")
    print()
    
    # Load data
    all_data = {}
    for t in tickers:
        print(f"  {t}...", end=' ', flush=True)
        data = load_ticker(t, start, end)
        if data:
            all_data[t] = data
            print(f"{len(data['opn']):,} bars ✅")
        else:
            print(f"❌ no data")
    
    if not all_data:
        print("No data")
        return
    
    # Per-ticker hasil (full capital each)
    print(f"\n── Per-ticker (100% capital, with costs) ──")
    print(f"{'Ticker':>6} {'Ret%':>8} {'DD%':>7} {'Calmar':>7} {'Trades':>7} {'WR%':>5}")
    print("-" * 45)
    
    per_ticker = {}
    for t in tickers:
        if t not in all_data:
            continue
        cfg = CONFIGS[t]
        res = simulate_ticker_simple(all_data[t], cfg['div_thr'], cfg['hold'], cfg['stop_pct'], 100000)
        per_ticker[t] = res
        calmar = res['ret'] / max(res['dd'], 0.01)
        print(f"  {t}: {res['ret']:>+7.1f}% {res['dd']:>6.1f}% {calmar:>6.1f}x {res['trades']:>6} {res['wr']:>4.0f}%")
    
    # Portfolio: equal weight, sum of scaled equity curves
    print(f"\n═══ Portfolio simulation ═══")
    capital_total = 100000.0
    capital_per_ticker = capital_total / len(per_ticker)
    
    portfolio_eq = None
    min_len = min(len(v['eq_curve']) for v in per_ticker.values())
    
    for t in tickers:
        if t not in per_ticker:
            continue
        eq = np.array(per_ticker[t]['eq_curve'][:min_len])
        eq_scaled = eq / eq[0] * capital_per_ticker
        if portfolio_eq is None:
            portfolio_eq = eq_scaled
        else:
            portfolio_eq += eq_scaled
    
    if portfolio_eq is None:
        print("No portfolio data")
        return
    
    final = portfolio_eq[-1]
    total_ret = (final / capital_total - 1) * 100
    peak_pf = np.maximum.accumulate(portfolio_eq)
    dd_pf = (peak_pf - portfolio_eq) / peak_pf * 100
    max_dd_pf = np.max(dd_pf)
    calmar_pf = total_ret / max(max_dd_pf, 0.01)
    
    print(f"  Capital: {capital_total:,.0f} → {final:,.0f} RUB")
    print(f"  Total Return: {total_ret:+.1f}%")
    print(f"  Max DD: {max_dd_pf:.1f}%")
    print(f"  Calmar: {calmar_pf:.1f}x")
    
    # DD events
    print(f"\n── DD events >3% ──")
    # Find local DD peaks
    in_dd = False
    events = []
    start_idx = 0
    for i, d in enumerate(dd_pf):
        if d > 3 and not in_dd:
            in_dd = True
            start_idx = i
        elif d <= 1 and in_dd:
            in_dd = False
            peak_dd = np.max(dd_pf[start_idx:i+1])
            events.append({'start': start_idx, 'end': i, 'peak': peak_dd})
    if in_dd:
        peak_dd = np.max(dd_pf[start_idx:])
        events.append({'start': start_idx, 'end': len(dd_pf)-1, 'peak': peak_dd})
    
    if events:
        events.sort(key=lambda x: x['peak'], reverse=True)
        for ev in events[:5]:
            print(f"  DD {ev['peak']:.1f}% ({ev['start']}→{ev['end']}, {ev['end']-ev['start']} bars)")
    
    # Yearly
    print(f"\n── Yearly breakdown ──")
    # Approximate: ~390 bars per trading day, ~252 days per year
    # Each bar = 1 minute, ~390 min per day = 1 trading day
    bars_per_day = 390
    for yr in ['2024', '2025', '2026']:
        if yr == '2024':
            s_i = 0
            e_i = min(252 * bars_per_day, len(portfolio_eq) - 1)
        elif yr == '2025':
            s_i = min(252 * bars_per_day, len(portfolio_eq) - 1)
            e_i = min(2 * 252 * bars_per_day, len(portfolio_eq) - 1)
        else:
            s_i = min(2 * 252 * bars_per_day, len(portfolio_eq) - 1)
            e_i = len(portfolio_eq) - 1
        
        if s_i >= len(portfolio_eq):
            break
        
        yr_ret = (portfolio_eq[e_i] / portfolio_eq[s_i] - 1) * 100
        yr_peak = np.maximum.accumulate(portfolio_eq[s_i:e_i+1])
        yr_dd = np.max((yr_peak - portfolio_eq[s_i:e_i+1]) / yr_peak * 100) if e_i > s_i else 0
        print(f"  {yr}: {yr_ret:+.1f}%  DD={yr_dd:.1f}%")


if __name__ == '__main__':
    main()
