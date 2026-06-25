#!/usr/bin/env python3
"""
CVD divergence: ПОЛНЫЙ АУДИТ лучшего конфига.
Вход лимиткой по close сигнала, комиссия 0, slippage 0.5 тика.
"""
import clickhouse_connect
import pandas as pd
import numpy as np
import sys

ch = clickhouse_connect.get_client(host='10.0.0.64', database='moex')

INITIAL_CAPITAL = 100_000
SLIPPAGE = 0.5

TICK_COST = {'NG': 3.715, 'BR': 0.743, 'Si': 0.0025, 'MXI': 0.10}
TICK = {'NG': 0.0005, 'BR': 0.001, 'Si': 0.0025, 'MXI': 0.01}
GO = {'NG': 4800, 'BR': 3500, 'Si': 2500, 'MXI': 2000}
SYMBOLS = ['NG', 'BR', 'Si', 'MXI']
N_SYMS = len(SYMBOLS)

TF = '5min'
lk = 20
hold_bars = 1
q = 0.6

# Load + resample
def resample_to_5min(df):
    df = df.copy()
    df['time'] = pd.to_datetime(df['time'])
    df = df.set_index('time')
    ohlc = {'open': 'first', 'close': 'last', 'vol_b': 'sum', 'vol_s': 'sum'}
    resampled = df.resample('5min').agg(ohlc).dropna(subset=['open'])
    resampled = resampled.reset_index()
    resampled['cvd'] = resampled['vol_b'].fillna(0) - resampled['vol_s'].fillna(0)
    resampled['date'] = resampled['time'].dt.date
    return resampled

data = {}
for SYM in SYMBOLS:
    sys.stdout.write(f"Loading {SYM}...\n"); sys.stdout.flush()
    df = ch.query_df(f"""
        SELECT toDateTime(tradedate || ' ' || tradetime) AS time,
               pr_open AS open, pr_close AS close, vol_b, vol_s
        FROM moex.tradestats_fo
        WHERE asset_code = '{SYM}' AND vol > 0 ORDER BY time
    """)
    data[SYM] = resample_to_5min(df)
    sys.stdout.write(f"  {SYM}: {len(data[SYM])} bars\n"); sys.stdout.flush()

all_trades = []
all_signals = []  # (time, sym, sig, entry_price)

for SYM in SYMBOLS:
    df = data[SYM].copy()
    tc = TICK_COST[SYM]; tick = TICK[SYM]
    dates = sorted(df['date'].unique())
    ws_train = min(180, max(60, len(dates) // 3))
    ws_test = min(60, max(20, len(dates) // 6))
    
    i = ws_train
    while i < len(dates):
        test_end = min(i + ws_test, len(dates))
        train_dates = set(dates[i-ws_train:i])
        test_dates = set(dates[i:test_end])
        if len(test_dates) < 20:
            i += ws_test; continue
        
        train = df[df['date'].isin(train_dates)].copy()
        test = df[df['date'].isin(test_dates)].copy().reset_index(drop=True)
        if len(train) < 50 or len(test) < 10:
            i += ws_test; continue
        
        train['cvd_cum'] = train['cvd'].cumsum()
        train['pchg'] = train['close'].diff(lk).dropna()
        train['cchg'] = train['cvd_cum'].diff(lk).dropna()
        train_v = train.dropna()
        if len(train_v) < 30:
            i += ws_test; continue
        p_thr = train_v['pchg'].abs().quantile(q)
        c_thr = train_v['cchg'].abs().quantile(q)
        if p_thr == 0 or c_thr == 0:
            i += ws_test; continue
        
        last_cvd = train['cvd_cum'].iloc[-1]
        test['cvd_cum'] = last_cvd + test['cvd'].cumsum()
        test['pchg'] = test['close'].diff(lk)
        test['cchg'] = test['cvd_cum'].diff(lk)
        test_v = test.dropna()
        
        bearish = (test_v['pchg'] > p_thr) & (test_v['cchg'] < -c_thr)
        bullish = (test_v['pchg'] < -p_thr) & (test_v['cchg'] > c_thr)
        
        for sig_idx in range(len(test_v)):
            sig = -1 if bearish.iloc[sig_idx] else (1 if bullish.iloc[sig_idx] else 0)
            if sig == 0: continue
            if sig_idx >= len(test_v) - 1: continue
            
            entry_price = test_v.iloc[sig_idx]['close']
            all_signals.append({
                'time': test_v.iloc[sig_idx]['time'],
                'symbol': SYM, 'sig': sig,
                'entry_price': entry_price,
            })
            
            exit_idx = min(sig_idx + hold_bars, len(test_v) - 1)
            exit_price = test_v.iloc[exit_idx]['close']
            
            pnl_ticks = (exit_price - entry_price) * sig / tick
            pnl_rub = pnl_ticks * tc - SLIPPAGE * tc
            
            all_trades.append({
                'time': test_v.iloc[exit_idx]['time'],
                'pnl_rub': pnl_rub, 'symbol': SYM,
                'month': str(test_v.iloc[exit_idx]['time'].to_period('M')),
                'sig': sig, 'entry_time': test_v.iloc[sig_idx]['time'],
                'entry_price': entry_price, 'exit_price': exit_price,
            })
        
        i += ws_test

trades_df = pd.DataFrame(all_trades).sort_values('time')
signals_df = pd.DataFrame(all_signals).sort_values('time')

trade_count = len(trades_df)
trade_wr = (trades_df['pnl_rub'] > 0).sum() / trade_count * 100
gross_pnl = trades_df['pnl_rub'].sum()
avg_trade = trades_df['pnl_rub'].mean()
med_trade = trades_df['pnl_rub'].median()
win_avg = trades_df[trades_df['pnl_rub'] > 0]['pnl_rub'].mean()
loss_avg = trades_df[trades_df['pnl_rub'] < 0]['pnl_rub'].mean()
profit_factor = abs(trades_df[trades_df['pnl_rub'] > 0]['pnl_rub'].sum() / max(trades_df[trades_df['pnl_rub'] < 0]['pnl_rub'].sum(), 0.001))

print(f"\n{'='*70}")
print(f"  TRADE STATISTICS")
print(f"{'='*70}")
print(f"  Total trades:   {trade_count:,}")
print(f"  Win rate:       {trade_wr:.1f}%")
print(f"  Gross PnL:      {gross_pnl:+,.0f} RUB")
print(f"  Avg trade:      {avg_trade:+.1f} RUB")
print(f"  Median trade:   {med_trade:+.1f} RUB")
print(f"  Avg win:        {win_avg:+.1f} RUB")
print(f"  Avg loss:       {loss_avg:+.1f} RUB")
print(f"  Profit factor:  {profit_factor:.2f}")

# ===== Equity curve =====
print(f"\n{'='*70}")
print(f"  EQUITY CURVE ANALYSIS")
print(f"{'='*70}")

capital = INITIAL_CAPITAL
peak = capital
max_dd = 0.0
max_dd_peak = capital
max_dd_trough = capital
equity = []
for _, trade in trades_df.iterrows():
    go_entry = GO.get(trade['symbol'], 3000)
    max_lots = max(1, int(capital / N_SYMS / go_entry))
    lots = min(4, max_lots)
    capital += trade['pnl_rub'] * lots
    peak = max(peak, capital)
    dd = (peak - capital) / peak * 100
    if dd > max_dd:
        max_dd = dd
        max_dd_peak = peak
        max_dd_trough = capital
    equity.append({'time': trade['time'], 'capital': capital, 'peak': peak, 'dd': dd})

equity_df = pd.DataFrame(equity)
final_capital = capital
total_return = (capital / INITIAL_CAPITAL - 1) * 100

duration_days = (trades_df['time'].max() - trades_df['time'].min()).days
duration_years = max(duration_days / 365.25, 0.1)
cagr = (capital / INITIAL_CAPITAL) ** (1 / duration_years) - 1 if capital > 0 else 0
calmar = cagr / (max_dd / 100) if max_dd > 0.001 else 0

print(f"  Initial:        {INITIAL_CAPITAL:,.0f} RUB")
print(f"  Final:          {final_capital:,.0f} RUB")
print(f"  Return:         {total_return:+.1f}%")
print(f"  CAGR:           {cagr*100:.1f}%")
print(f"  Max DD:         {max_dd:.2f}%")
print(f"  Max DD peak:    {max_dd_peak:,.0f} RUB")
print(f"  Max DD trough:  {max_dd_trough:,.0f} RUB")
print(f"  Calmar:         {calmar:.2f}")

# ===== Monthly distribution =====
print(f"\n{'='*70}")
print(f"  MONTHLY DISTRIBUTION")
print(f"{'='*70}")

mon_pnl = trades_df.groupby('month').agg(
    trades=('pnl_rub','count'),
    net=('pnl_rub','sum'),
    win=('pnl_rub', lambda x: (x>0).sum()),
    gross=('pnl_rub', 'sum'),
)
mon_pnl['wr'] = mon_pnl['win'] / mon_pnl['trades'] * 100
pos_m = (mon_pnl['net'] > 0).sum()
neg_m = (mon_pnl['net'] < 0).sum()

print(f"  Total months:   {len(mon_pnl)}")
print(f"  Positive months: {pos_m} ({pos_m/len(mon_pnl)*100:.1f}%)")
print(f"  Negative months: {neg_m} ({neg_m/len(mon_pnl)*100:.1f}%)")
print(f"  Best month:     {mon_pnl['net'].max():>10,.0f} RUB ({mon_pnl['net'].idxmax()})")
print(f"  Worst month:    {mon_pnl['net'].min():>10,.0f} RUB ({mon_pnl['net'].idxmin()})")
print(f"  Median month:   {mon_pnl['net'].median():>10,.0f} RUB")
print(f"  Months with <0 trades (no signals): {(mon_pnl['trades']==0).sum()}")

# Worst 5 months
print(f"\n  Worst 5 months:")
for m in mon_pnl.nsmallest(5, 'net').itertuples():
    print(f"    {m.Index}: {m.net:>10,.0f} RUB (trades={m.trades}, WR={m.wr:.1f}%)")

# ===== Consecutive losses =====
print(f"\n{'='*70}")
print(f"  DRAWDOWN & STREAKS")
print(f"{'='*70}")

# Max consecutive losses
max_consec_loss = 0
cur_consec_loss = 0
for _, trade in trades_df.iterrows():
    if trade['pnl_rub'] < 0:
        cur_consec_loss += 1
        max_consec_loss = max(max_consec_loss, cur_consec_loss)
    else:
        cur_consec_loss = 0

max_consec_win = 0
cur_consec_win = 0
for _, trade in trades_df.iterrows():
    if trade['pnl_rub'] > 0:
        cur_consec_win += 1
        max_consec_win = max(max_consec_win, cur_consec_win)
    else:
        cur_consec_win = 0

print(f"  Max consecutive wins:   {max_consec_win}")
print(f"  Max consecutive losses: {max_consec_loss}")

# Max DD from equity curve — find start/end
max_dd_period = equity_df.loc[equity_df['dd'].idxmax()]
print(f"  Max DD start/end:      {max_dd_period['time']}")

# ===== Long vs Short symmetry =====
print(f"\n{'='*70}")
print(f"  LONG vs SHORT SYMMETRY")
print(f"{'='*70}")

long_trades = trades_df[trades_df['sig'] == 1]
short_trades = trades_df[trades_df['sig'] == -1]
print(f"  Long trades:  {len(long_trades):,} ({(len(long_trades)/len(trades_df)*100):.1f}%)")
print(f"    WR:  {(long_trades['pnl_rub']>0).mean()*100:.1f}%")
print(f"    Avg: {long_trades['pnl_rub'].mean():+.1f} RUB")
print(f"    Net: {long_trades['pnl_rub'].sum():+,.0f} RUB")
print(f"  Short trades: {len(short_trades):,} ({(len(short_trades)/len(trades_df)*100):.1f}%)")
print(f"    WR:  {(short_trades['pnl_rub']>0).mean()*100:.1f}%")
print(f"    Avg: {short_trades['pnl_rub'].mean():+.1f} RUB")
print(f"    Net: {short_trades['pnl_rub'].sum():+,.0f} RUB")

# ===== Per-symbol performance =====
print(f"\n{'='*70}")
print(f"  PER SYMBOL")
print(f"{'='*70}")
for sym in SYMBOLS:
    sym_t = trades_df[trades_df['symbol'] == sym]
    if len(sym_t) == 0: continue
    sym_wr = (sym_t['pnl_rub'] > 0).mean() * 100
    sym_net = sym_t['pnl_rub'].sum()
    sym_long = sym_t[sym_t['sig'] == 1]
    sym_short = sym_t[sym_t['sig'] == -1]
    print(f"  {sym}: {len(sym_t):,} trades, WR={sym_wr:.1f}%, Net={sym_net:+,.0f} RUB")
    print(f"    Long:  {len(sym_long):,} WR={(sym_long['pnl_rub']>0).mean()*100:.1f}% Net={sym_long['pnl_rub'].sum():+,.0f}")
    print(f"    Short: {len(sym_short):,} WR={(sym_short['pnl_rub']>0).mean()*100:.1f}% Net={sym_short['pnl_rub'].sum():+,.0f}")

# ===== Buy & Hold comparison =====
print(f"\n{'='*70}")
print(f"  BUY & HOLD COMPARISON")
print(f"{'='*70}")
for sym in SYMBOLS:
    s = data[sym]
    start_price = s['close'].iloc[0]
    end_price = s['close'].iloc[-1]
    bh_return = (end_price - start_price) / start_price * 100
    print(f"  {sym}: Buy&Hold {bh_return:+.1f}% (start={start_price}, end={end_price})")

# Strategy total return vs BH
strat_return = total_return
print(f"\n  Strategy: {strat_return:+.1f}% (reinvest, 4 sym, multilot)")

# ===== Slippage sensitivity =====
print(f"\n{'='*70}")
print(f"  SLIPPAGE SENSITIVITY")
print(f"{'='*70}")
for slippage_test in [0.0, 0.5, 1.0, 2.0]:
    test_pnl = sum(
        t['pnl_rub'] + SLIPPAGE * TICK_COST[t['symbol']]  # revert original slippage
        - slippage_test * TICK_COST[t['symbol']]  # apply test slippage
        for t in all_trades
    )
    print(f"  Slippage {slippage_test:.1f} ticks: gross PnL = {test_pnl:+,.0f} RUB")

# ===== Equity concentration (last 3 months %) =====
print(f"\n{'='*70}")
print(f"  EQUITY CONCENTRATION CHECK")
print(f"{'='*70}")
last_3_months = sorted(trades_df['month'].unique())[-3:]
conc_pnl = trades_df[trades_df['month'].isin(last_3_months)]['pnl_rub'].sum()
conc_pct = conc_pnl / gross_pnl * 100 if gross_pnl != 0 else 0
print(f"  Last 3 months PnL: {conc_pnl:+,.0f} RUB ({conc_pct:.1f}% of total)")
print(f"  Danger if >50% — hockey-stick effect")

print(f"\n{'='*70}")
print(f"  AUDIT COMPLETE")
print(f"{'='*70}")
