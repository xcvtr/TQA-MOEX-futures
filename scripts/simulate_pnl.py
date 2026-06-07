#!/usr/bin/env python3
"""
Realistic PnL simulation: Volume Surge + Divergence strategy
with commission, slippage, and leverage (ГО).
"""

import numpy as np

# ─── Strategy parameters from actual backtest ──────────────────────

CONTRACTS = {
    'HS': {
        'avg_price': 24927,   # пунктов
        'lot': 1,             # контрактов
        'contract_rub': 24927, # руб за контракт
        'tick_rub': 1.0,      # руб за пункт
        'min_step': 1,        # шаг цены
        'est_go': 5000,       # оценочное ГО
        'period_days': 300,   # дней в тестовом периоде
        'signals': 274,
        'wr': 60.9,
        'pf': 1.96,
        'avg_return': 0.12,   # % от стоимости контракта
    },
    'KC': {
        'avg_price': 3.01,    # USD/фунт
        'lot': 100,           # контракт = 100 фунтов
        'contract_rub': 3.01 * 100 * 80,  # ≈ 24,080 руб (~$3 × 100 × 80 курс)
        'tick_rub': 0.01 * 100 * 80,      # = 80 руб за тик
        'min_step': 0.01,
        'est_go': 2500,
        'period_days': 300,
        'signals': 108,
        'wr': 57.4,
        'pf': 1.54,
        'avg_return': 0.11,
    },
    'HY': {
        'avg_price': 4633,    # пунктов
        'lot': 1,
        'contract_rub': 4633,
        'tick_rub': 1.0,
        'min_step': 1,
        'est_go': 3000,
        'period_days': 500,
        'signals': 282,
        'wr': 57.8,
        'pf': 1.35,
        'avg_return': 0.18,
    },
    'DX': {
        'avg_price': 19064,
        'lot': 1,
        'contract_rub': 19064,
        'tick_rub': 1.0,
        'min_step': 1,
        'est_go': 3000,
        'period_days': 500,
        'signals': 123,
        'wr': 57.7,
        'pf': 2.0,
        'avg_return': 0.10,
    },
}

# ─── Cost parameters ───────────────────────────────────────────────

COMMISSION_PER_SIDE = 2.0    # руб (MOEX ~1-3 руб/контракт)
SLIPPAGE_TICKS = 1            # тиков проскальзывания на вход + выход
CAPITAL = 300_000             # начальный капитал, руб

def simulate(ct, comm_per_side=2.0, slippage_ticks=1):
    """Monte Carlo style simulation of 100 equity curves."""
    
    p = CONTRACTS[ct]
    n = p['signals']
    wr = p['wr'] / 100
    pf = p['pf']
    
    # Derive avg_win and avg_loss from PF and WR
    # PF = (n_wins × avg_win) / (n_losses × avg_loss)
    # avg_return = WR × avg_win - (1-WR) × avg_loss
    # => avg_loss = avg_return / (1-WR - WR × PF)
    # Hmm, more complex...
    
    # Actually from PF and WR:
    # Let avg_loss = 1 (unit), avg_win = PF × (1-WR)/WR × 1
    # avg_return = WR × avg_win - (1-WR) × avg_loss
    # avg_return = WR × PF × (1-WR)/WR × avg_loss - (1-WR) × avg_loss
    # avg_return = (1-WR) × avg_loss × (PF - 1)
    # avg_loss = avg_return / ((1-WR) × (PF - 1))
    
    avg_ret_pct = p['avg_return'] / 100  # as decimal
    contract_rub = p['contract_rub']
    avg_ret_pct = p['avg_return'] / 100  # as decimal of contract value
    
    # Derive avg_win and avg_loss from PF and WR
    # avg_return = WR × avg_win - (1-WR) × avg_loss
    # PF = (n_wins × avg_win) / (n_losses × avg_loss)
    # avg_return = (1-WR) × avg_loss × (PF - 1)
    avg_loss_pct = avg_ret_pct / ((1 - wr) * (pf - 1)) if pf > 1 else avg_ret_pct
    avg_win_pct = pf * (1 - wr) / wr * avg_loss_pct if wr > 0 else 0
    
    min_step = p['min_step']
    est_go = p['est_go']
    tick_rub = p['tick_rub']
    
    # Per trade costs
    comm_cost_rub = comm_per_side * 2  # round trip
    slippage_rub = slippage_ticks * min_step * tick_rub * 2  # entry + exit
    total_cost_rub = comm_cost_rub + slippage_rub
    total_cost_pct = total_cost_rub / contract_rub * 100
    
    # Net returns (in RUB, then back to %)
    avg_win_rub = avg_win_pct * contract_rub
    avg_loss_rub = avg_loss_pct * contract_rub
    
    avg_win_net_pct = (avg_win_rub - total_cost_rub) / contract_rub * 100
    avg_loss_net_pct = (avg_loss_rub + total_cost_rub) / contract_rub * 100
    
    # "% of avg win/loss eaten by costs"
    cost_eat_win = total_cost_rub / avg_win_rub * 100 if avg_win_rub > 0 else 0
    cost_eat_loss = total_cost_rub / avg_loss_rub * 100 if avg_loss_rub > 0 else 0
    
    # Net PF
    total_gains_rub = wr * avg_win_rub * n
    total_losses_rub = (1 - wr) * avg_loss_rub * n
    total_cost_all = n * total_cost_rub
    
    net_gains_rub = total_gains_rub - total_cost_all * (wr)
    net_losses_rub = total_losses_rub + total_cost_all * (1 - wr)
    
    net_pf = net_gains_rub / net_losses_rub if net_losses_rub > 0 else 99
    
    # Net return per trade
    net_ret_per_trade = (total_gains_rub - total_losses_rub - total_cost_all) / n / contract_rub * 100
    
    # Leverage
    leverage = contract_rub / est_go
    margin_per_contract = est_go
    contracts_per_signal = int(CAPITAL / margin_per_contract * 0.5)  # 50% margin usage
    if contracts_per_signal < 1: contracts_per_signal = 1
    
    # Annualization
    period_days = p['period_days']
    signals_per_day = n / period_days
    trades_per_year = int(signals_per_day * 252)
    
    gross_annual_rub = net_ret_per_trade / 100 * contract_rub * contracts_per_signal * trades_per_year
    net_annual_pct = gross_annual_rub / CAPITAL * 100
    
    # DD on capital (leverage magnifies)
    dd_per_trade_pct = avg_loss_net_pct / 100  # decimal
    dd_on_capital = dd_per_trade_pct * leverage * 0.5 * 100  # ~50% margin used
    
    # For longer DD estimate: worst 5-trade streak
    worst_streak = 5
    dd_worst = avg_loss_net_pct * worst_streak * contracts_per_signal * contract_rub / 100 / CAPITAL * 100
    
    return {
        'symbol': ct,
        'capital': CAPITAL,
        'contract_rub': round(contract_rub, 0),
        'go': est_go,
        'leverage': round(leverage, 1),
        'n': n,
        'trades_per_year': trades_per_year,
        'signals_per_day': round(signals_per_day, 1),
        'gross_wr': p['wr'],
        'gross_pf': p['pf'],
        'avg_win_gross': round(avg_win_pct * 100, 2),
        'avg_loss_gross': round(avg_loss_pct * 100, 2),
        'avg_return_gross': round(avg_ret_pct * 100, 3),
        'commission_rub': comm_per_side,
        'slippage_rub': round(slippage_rub, 1),
        'total_cost_rub': round(total_cost_rub, 1),
        'total_cost_pct': round(total_cost_pct, 3),
        'cost_eats_win': round(cost_eat_win, 1),
        'cost_eats_loss': round(cost_eat_loss, 1),
        'net_avg_return': round(net_ret_per_trade, 3),
        'net_pf': round(net_pf, 2),
        'net_wr': p['wr'],
        'contracts_per_signal': contracts_per_signal,
        'gross_annual_rub': round(gross_annual_rub, 0),
        'net_annual_pct': round(net_annual_pct, 1),
        'dd_per_trade_pct': round(avg_loss_pct * 100, 2),
        'dd_worst_5_pct': round(dd_worst, 1),
    }

def simulate_portfolio(tickers, alloc_pct=None):
    """Simulate portfolio of multiple uncorrelated tickers."""
    if alloc_pct is None:
        alloc_pct = [1.0/len(tickers)] * len(tickers)
    
    results = []
    total_capital_per = CAPITAL * np.array(alloc_pct)
    
    for i, ct in enumerate(tickers):
        base = simulate(ct)
        r = simulate(ct)
        r['capital_alloc'] = total_capital_per[i]
        
        # Scale trades to allocated capital
        scale = total_capital_per[i] / CAPITAL
        r['trades_per_year'] = int(base['trades_per_year'] * scale)
        r['net_annual_pct'] = base['net_annual_pct']  # % is same
        r['gross_annual_rub'] = base['gross_annual_rub'] * scale
        results.append(r)
    
    return results

# ─── RUN ───────────────────────────────────────────────────────────

print("=" * 120)
print("СИМУЛЯЦИЯ: Volume Surge + Divergence на MOEX фьючерсах")
print(f"Капитал: {CAPITAL:,.0f} руб")
print("=" * 120)

for ct in ['HS', 'KC', 'HY', 'DX']:
    r = simulate(ct)
    print(f"\n{'─'*80}")
    print(f"📊 {ct} (контракт ~{r['contract_rub']:,.0f} руб, ГО ~{r['go']:,.0f}, плечо ~{r['leverage']}x)")
    print(f"{'─'*80}")
    print(f"  Сигналов:       {r['n']} за период (~{r['trades_per_year']} в год, {r['signals_per_day']:.1f}/день)")
    print(f"  Гросс:          WR={r['gross_wr']}%  PF={r['gross_pf']}  avg_win={r['avg_win_gross']}%  avg_loss={r['avg_loss_gross']}%")
    print(f"  Издержки:")
    print(f"    Комиссия:     {r['commission_rub']:.0f} руб × 2 = {r['commission_rub']*2:.0f} руб/сделку")
    print(f"    Slippage:     {r['slippage_rub']:.1f} руб/сделку ({SLIPPAGE_TICKS} тик)")
    print(f"    Всего:        {r['total_cost_rub']:.1f} руб ({r['total_cost_pct']:.3f}% от контракта)")
    print(f"    Съедает:      {r['cost_eats_win']:.1f}% выигрыша | {r['cost_eats_loss']:.1f}% проигрыша")
    print(f"  Нетто:")
    print(f"    WR:           {r['net_wr']}% (без изменений)")
    print(f"    PF:           {r['net_pf']}")
    print(f"    Avg return:   {r['net_avg_return']:.3f}%/сделку")
    print(f"  С leverage (~{r['leverage']}x, 50% ГО):")
    print(f"    Контрактов:   {r['contracts_per_signal']:.0f} на сигнал")
    print(f"    P&L год:      {r['gross_annual_rub']:,.0f} руб")
    print(f"    Доходность:   {r['net_annual_pct']:+.1f}%/год на капитал")
    print(f"    DD/сделку:    {r['dd_per_trade_pct']:.2f}% на капитал")
    print(f"    DD 5 streak:  {r['dd_worst_5_pct']:.1f}%")

# Portfolio
print(f"\n\n{'='*120}")
print("ПОРТФЕЛЬ: HS + HY + DX (равномерно)")
print(f"{'='*120}")
portfolio = simulate_portfolio(['HS', 'HY', 'DX'])
total_annual_rub = sum(r['gross_annual_rub'] for r in portfolio)
total_trades = sum(r['trades_per_year'] for r in portfolio)
avg_return = np.mean([r['net_annual_pct'] for r in portfolio])
max_dd = max(r['dd_worst_5_pct'] for r in portfolio)

print(f"{'Тикер':>5s} | {'Сделок/год':>10s} | {'Капитал':>10s} | {'Доход%':>7s} | {'P&L руб':>10s} | {'DD 5str':>7s}")
print("-" * 60)
for r in portfolio:
    print(f"{r['symbol']:>5s} | {r['trades_per_year']:>10d} | {r['capital_alloc']:>10,.0f} | "
          f"{r['net_annual_pct']:>+6.1f}% | {r['gross_annual_rub']:>10,.0f} | {r['dd_worst_5_pct']:>6.1f}%")

print(f"\n  Итого: {total_trades} сделок/год, P&L {total_annual_rub:,.0f} руб")
print(f"  Средняя доходность: {avg_return:+.1f}%/год")
print(f"  Худшая DD: {max_dd:.1f}%")
sharp_ratio = avg_return / max_dd if max_dd > 0 else 0
print(f"  Sharpe-like: {sharp_ratio:.2f}")
