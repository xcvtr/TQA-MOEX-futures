#!/usr/bin/env python3
"""
CVD divergence backtest v4 — РЕАЛИСТИЧНАЯ МОДЕЛЬ ИСПОЛНЕНИЯ.

Исправления против v3:
1. Touch-check: лимитный ордер исполняется только если цена коснулась уровня
2. Выход по close следующего 5м бара (hold=1)
3. Slippage: 0.5 тика на вход + 1.0 тик на выход = 1.5 тика round-trip
4. Адаптивный сдвиг лимитки: 30% от ATR(14), мин 5 тиков, макс 20 тиков
5. TICK/TICK_COST синхронизированы с MOEX specs (из lib_cvd_divergence)

Параметры: M5, lk=20, hold=1, q=0.6
Источник данных: ClickHouse moex.tradestats_fo
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import clickhouse_connect
import pandas as pd
import numpy as np

import warnings
warnings.filterwarnings('ignore', '.*to_period.*', UserWarning)

from lib_cvd_divergence import (
    TICK, TICK_COST, GO, SYMBOLS, N_SYMS,
    LK, HOLD_BARS, Q, INITIAL_CAPITAL,
    SLIPPAGE_IN_TICKS, SLIPPAGE_OUT_TICKS,
    MIN_SLIPPAGE_TICKS, MAX_SLIPPAGE_TICKS, FIXED_SLIPPAGE_TICKS,
    resample_to_5m, deduplicate_1m, calc_thresholds, detect_signals,
    calc_entry_price, check_touch, calc_pnl_rub,
    calc_slippage_ticks, walk_forward_split,
)

CH_HOST = os.environ.get('MOEX_CH_HOST', '10.0.0.64')
ch = clickhouse_connect.get_client(host=CH_HOST, database='moex')

# ── Загрузка и ресемпл ──────────────────────────────────────────────────

def load_data(symbol):
    """Загрузить tradestats из ClickHouse и ресемплировать в 5м.
    
    Использует pr_high/pr_low из tradestats_fo для точного touch-check.
    """
    sys.stdout.write(f"Loading {symbol}...\n"); sys.stdout.flush()
    df = ch.query_df(f"""
        SELECT toDateTime(tradedate || ' ' || tradetime) AS time,
               pr_open AS open, pr_high AS high, pr_low AS low,
               pr_close AS close, vol_b, vol_s
        FROM moex.tradestats_fo
        WHERE asset_code = '{symbol}' AND vol > 0
        ORDER BY time
    """)
    if df.empty:
        return pd.DataFrame()
    # В CH данные уже дедуплицированы (один ряд на минуту)
    return resample_to_5m(df)


# ── Загрузка всех символов ──────────────────────────────────────────────

data = {}
for sym in SYMBOLS:
    df_5m = load_data(sym)
    if df_5m.empty:
        print(f"  ⚠️  {sym}: no data")
        continue
    data[sym] = df_5m
    print(f"  {sym}: {len(df_5m)} bars ({df_5m.iloc[0]['time']} .. {df_5m.iloc[-1]['time']})")


# ── Бэктест ─────────────────────────────────────────────────────────────

all_trades = []
all_signals = []

for SYM in SYMBOLS:
    if SYM not in data:
        continue
    df = data[SYM].copy()
    tick = TICK[SYM]
    tick_cost = TICK_COST[SYM]
    go = GO[SYM]

    dates = sorted(df['date'].unique())
    ws_train = min(180, max(60, len(dates) // 3))
    ws_test = min(60, max(20, len(dates) // 6))

    for train_dates, test_dates in walk_forward_split(dates, ws_train, ws_test):
        train = df[df['date'].isin(train_dates)].copy()
        test = df[df['date'].isin(test_dates)].copy().reset_index(drop=True)

        if len(train) < 50 or len(test) < 10:
            continue

        # Пороги на train
        p_thr, c_thr = calc_thresholds(train)
        if p_thr is None:
            continue

        # Детектим сигналы на test
        test_with_sig = detect_signals(test, p_thr, c_thr)
        if test_with_sig.empty:
            continue

        # Проходим по барам test
        for si in range(len(test_with_sig)):
            sig = int(test_with_sig.iloc[si]['signal'])
            if sig == 0:
                continue
            # Нужен следующий бар для выхода
            if si >= len(test_with_sig) - HOLD_BARS:
                continue

            signal_bar = test_with_sig.iloc[si]
            next_bar = test_with_sig.iloc[si + HOLD_BARS]
            close_price = float(signal_bar['close'])

            # Адаптивный сдвиг лимитки
            slippage_ticks = calc_slippage_ticks(SYM, test if len(test) >= 14 else None)
            limit_price = calc_entry_price(close_price, sig, slippage_ticks, tick)

            # Проверка касания
            bar_high = float(signal_bar.get('high', close_price))
            bar_low = float(signal_bar.get('low', close_price))

            touches = check_touch(bar_high, bar_low, limit_price, sig)
            if not touches:
                continue  # сделка не исполнилась

            # Выход по close следующего бара
            exit_price = float(next_bar['close'])

            # PnL с slippage
            pnl_rub, slippage_total = calc_pnl_rub(
                SYM, limit_price, exit_price, sig,
                slippage_in_ticks=SLIPPAGE_IN_TICKS,
                slippage_out_ticks=SLIPPAGE_OUT_TICKS,
            )

            all_trades.append({
                'time': next_bar['time'],
                'pnl_rub': pnl_rub,
                'symbol': SYM,
                'month': str(pd.Timestamp(next_bar['time']).tz_localize(None).to_period('M')),
                'sig': sig,
                'slippage': slippage_total,
                'entry_price': limit_price,
                'exit_price': exit_price,
                'entry_time': signal_bar['time'],
                'slippage_ticks': slippage_ticks,
                'touch': True,
            })

            all_signals.append({
                'time': signal_bar['time'],
                'symbol': SYM,
                'sig': sig,
                'entry_price': limit_price,
                'close_price': close_price,
                'touches': touches,
            })


# ═══════════════════════════════════════════════════════════════════════
#  РЕЗУЛЬТАТЫ
# ═══════════════════════════════════════════════════════════════════════

trades_df = pd.DataFrame(all_trades).sort_values('time')

total_trades = len(trades_df)
if total_trades == 0:
    print("\n❌ No trades generated")
    sys.exit(0)

net_pnl = trades_df['pnl_rub'].sum()
wr = (trades_df['pnl_rub'] > 0).mean() * 100
avg_w = trades_df[trades_df['pnl_rub'] > 0]['pnl_rub'].mean()
avg_l = trades_df[trades_df['pnl_rub'] < 0]['pnl_rub'].mean()
gross_win = trades_df[trades_df['pnl_rub'] > 0]['pnl_rub'].sum()
gross_loss = trades_df[trades_df['pnl_rub'] < 0]['pnl_rub'].sum()
pf = abs(gross_win / max(gross_loss, 1))

# Slippage анализ
avg_slippage_ticks = trades_df['slippage_ticks'].mean()
total_slippage_rub = trades_df['slippage'].sum()

# Equity curve
capital = INITIAL_CAPITAL
peak = capital
max_dd = 0.0
max_dd_peak = capital
max_dd_trough = capital
equity = []
for _, trade in trades_df.iterrows():
    capital += trade['pnl_rub']
    peak = max(peak, capital)
    dd = (peak - capital) / peak * 100 if peak > 0 else 0
    if dd > max_dd:
        max_dd = dd
        max_dd_peak = peak
        max_dd_trough = capital
    equity.append({'time': trade['time'], 'capital': capital, 'peak': peak, 'dd': dd})

final_capital = capital
total_return = (capital / INITIAL_CAPITAL - 1) * 100

duration_days = (trades_df['time'].max() - trades_df['time'].min()).days
duration_years = max(duration_days / 365.25, 0.1)
cagr = (capital / INITIAL_CAPITAL) ** (1 / duration_years) - 1 if capital > 0 else 0
calmar = cagr / (max_dd / 100) if max_dd > 0.001 else 0

# Monthly
mon_pnl = trades_df.groupby('month')['pnl_rub'].sum()
pos_m = (mon_pnl > 0).sum()
neg_m = (mon_pnl < 0).sum()

print(f"\n{'='*70}")
print(f"  CVD DIVERGENCE — РЕАЛИСТИЧНЫЙ БЭКТЕСТ v4")
print(f"  Модель: лимитный вход + touch-check + выход по close след. бара")
print(f"{'='*70}")
print(f"  Параметры: M5 lk={LK} hold={HOLD_BARS} q={Q}")
print(f"  Slippage: {SLIPPAGE_IN_TICKS}т вход + {SLIPPAGE_OUT_TICKS}т выход = {SLIPPAGE_IN_TICKS+SLIPPAGE_OUT_TICKS}т round-trip")
print(f"  Адаптивный сдвиг: 30% ATR(14) | мин={MIN_SLIPPAGE_TICKS}т | макс={MAX_SLIPPAGE_TICKS}т")
print(f"  Touch-check: ✅ (LONG=low<=limit, SHORT=high>=limit)")
print(f"  TICK/TICK_COST: MOEX specs (из lib_cvd_divergence)")
print(f"{'─'*70}")
print(f"  Сделок:          {total_trades:,}")
print(f"  Win rate:        {wr:.1f}%")
print(f"  Net PnL:         {net_pnl:+,.0f} RUB")
print(f"  Avg win:         {avg_w:+.1f} RUB")
print(f"  Avg loss:        {avg_l:+.1f} RUB")
print(f"  Profit factor:   {pf:.2f}")
print(f"  Avg slippage:   {avg_slippage_ticks:.1f} тиков (сдвиг лимитки)")
print(f"  Slippage (руб):  {total_slippage_rub:+,.0f} RUB")
print(f"{'─'*70}")
print(f"  Начальный:       {INITIAL_CAPITAL:,.0f} RUB")
print(f"  Финальный:       {final_capital:,.0f} RUB")
print(f"  Доходность:      {total_return:+.1f}%")
print(f"  CAGR:            {cagr*100:.1f}%")
print(f"  Max DD:          {max_dd:.2f}%")
print(f"  Calmar:          {calmar:.2f}")
print(f"{'─'*70}")
print(f"  Месяцев:         {len(mon_pnl)} ({pos_m} пол, {neg_m} отр)")
print(f"  Месяцев>0:       {pos_m/len(mon_pnl)*100:.1f}%")

# Per symbol
print(f"\n{'─'*70}")
print(f"  PER SYMBOL")
for sym in SYMBOLS:
    st = trades_df[trades_df['symbol'] == sym]
    if len(st) == 0:
        continue
    swr = (st['pnl_rub'] > 0).mean() * 100
    snet = st['pnl_rub'].sum()
    s_avg_slip = st['slippage_ticks'].mean()
    print(f"  {sym}: {len(st):,} trades, WR={swr:.1f}%, Net={snet:+,.0f}, slippage={s_avg_slip:.1f}т")

# Long vs Short
print(f"\n{'─'*70}")
print(f"  LONG vs SHORT")
for sig_val, name in [(1, 'Long'), (-1, 'Short')]:
    st = trades_df[trades_df['sig'] == sig_val]
    if len(st) == 0:
        continue
    swr = (st['pnl_rub'] > 0).mean() * 100
    snet = st['pnl_rub'].sum()
    print(f"  {name}: {len(st):,} trades, WR={swr:.1f}%, Net={snet:+,.0f}")

# Slippage sensitivity
print(f"\n{'─'*70}")
print(f"  SLIPPAGE SENSITIVITY")
for slip_test_in, slip_test_out in [(0.5, 1.0), (0.5, 0.5), (0.0, 0.0), (1.0, 1.0)]:
    pnl_adj = sum(
        t['pnl_rub'] + (SLIPPAGE_IN_TICKS + SLIPPAGE_OUT_TICKS) * TICK_COST[t['symbol']]
        - (slip_test_in + slip_test_out) * TICK_COST[t['symbol']]
        for t in all_trades
    )
    print(f"    In={slip_test_in:.1f}t/Out={slip_test_out:.1f}t: Net PnL = {pnl_adj:+,.0f} RUB")

print(f"\n{'='*70}")
print(f"  ✅ БЭКТЕСТ ЗАВЕРШЁН")
print(f"{'='*70}")
