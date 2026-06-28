#!/usr/bin/env python3
"""
ALRS OI fiz/yur spread strategy analysis.
"""

import psycopg2
import pandas as pd
import numpy as np
import json

DB_CONFIG = {
    'host': '10.0.0.60',
    'dbname': 'moex',
    'user': 'postgres',
    'password': 'postgres',
}

def get_connection():
    return psycopg2.connect(**DB_CONFIG)

def load_data():
    conn = get_connection()
    
    print("Loading daily OHLCV from moex_prices_5m...")
    price_query = """
    WITH ordered AS (
        SELECT 
            time::date as day,
            open, high, low, close, volume,
            ROW_NUMBER() OVER (PARTITION BY time::date ORDER BY time) as rn_asc,
            ROW_NUMBER() OVER (PARTITION BY time::date ORDER BY time DESC) as rn_desc
        FROM moex_prices_5m
        WHERE symbol='AL' AND time >= '2023-01-01' AND time < '2026-06-01'
    )
    SELECT 
        day,
        MAX(CASE WHEN rn_asc = 1 THEN open END) as open,
        MAX(high) as high,
        MIN(low) as low,
        MAX(CASE WHEN rn_desc = 1 THEN close END) as close,
        SUM(volume) as volume
    FROM ordered
    GROUP BY day
    ORDER BY day;
    """
    prices = pd.read_sql(price_query, conn)
    prices['day'] = pd.to_datetime(prices['day'])
    prices.set_index('day', inplace=True)
    print(f"  Loaded {len(prices)} daily rows [{prices.index[0].date()} to {prices.index[-1].date()}]")
    
    print("Loading daily OI data from moex_prices_5m_oi...")
    oi_query = """
    SELECT 
        time::date as day,
        SUM(fiz_buy) as sum_fiz_buy,
        SUM(fiz_sell) as sum_fiz_sell,
        SUM(yur_buy) as sum_yur_buy,
        SUM(yur_sell) as sum_yur_sell,
        (array_agg(total_oi ORDER BY time DESC))[1] as end_total_oi
    FROM moex_prices_5m_oi
    WHERE symbol='AL'
      AND time >= '2023-01-01'
      AND time < '2026-06-01'
    GROUP BY time::date
    ORDER BY day;
    """
    oi = pd.read_sql(oi_query, conn)
    oi['day'] = pd.to_datetime(oi['day'])
    oi.set_index('day', inplace=True)
    print(f"  Loaded {len(oi)} daily rows [{oi.index[0].date()} to {oi.index[-1].date()}]")
    
    conn.close()
    
    df = prices.join(oi, how='inner')
    print(f"Merged dataset: {len(df)} rows")
    return df

def compute_indicators(df):
    df['fiz_net'] = (df['sum_fiz_buy'] - df['sum_fiz_sell']) / (df['sum_fiz_buy'] + df['sum_fiz_sell'] + 1)
    df['yur_net'] = (df['sum_yur_buy'] - df['sum_yur_sell']) / (df['sum_yur_buy'] + df['sum_yur_sell'] + 1)
    df['spread'] = df['fiz_net'] - df['yur_net']
    return df

def simulate_strategy(df, spread_buy_thresh=-0.3, spread_sell_thresh=0.3, hold_days=5):
    df = df.copy()
    df['signal'] = 0
    df.loc[df['spread'] > spread_sell_thresh, 'signal'] = -1
    df.loc[df['spread'] < spread_buy_thresh, 'signal'] = 1
    
    trades = []
    position = None
    
    for i in range(len(df)):
        date = df.index[i]
        close_val = df.iloc[i]['close']
        signal = df.iloc[i]['signal']
        
        if position is not None:
            days_held = (date - position['entry_date']).days
            if days_held >= hold_days:
                pnl_pct = (close_val / position['entry_price'] - 1) * position['direction']
                trades.append({
                    'entry_date': str(position['entry_date'].date()),
                    'exit_date': str(date.date()),
                    'entry_price': round(position['entry_price'], 2),
                    'exit_price': round(close_val, 2),
                    'direction': 'LONG' if position['direction'] == 1 else 'SHORT',
                    'pnl_pct': round(pnl_pct, 6),
                    'days_held': days_held,
                    'entry_spread': round(position['signal_strength'], 4),
                    'exit_reason': 'time_exit',
                })
                position = None
        
        if position is None and signal != 0:
            position = {
                'entry_date': date,
                'entry_price': close_val,
                'direction': signal,
                'signal_strength': df.iloc[i]['spread'],
            }
    
    if position is not None:
        last_date = df.index[-1]
        last_close = df.iloc[-1]['close']
        days_held = (last_date - position['entry_date']).days
        pnl_pct = (last_close / position['entry_price'] - 1) * position['direction']
        trades.append({
            'entry_date': str(position['entry_date'].date()),
            'exit_date': str(last_date.date()),
            'entry_price': round(position['entry_price'], 2),
            'exit_price': round(last_close, 2),
            'direction': 'LONG' if position['direction'] == 1 else 'SHORT',
            'pnl_pct': round(pnl_pct, 6),
            'days_held': days_held,
            'entry_spread': round(position['signal_strength'], 4),
            'exit_reason': 'end_of_data',
        })
    
    trades_df = pd.DataFrame(trades)
    return trades_df, df

def calculate_metrics(trades_df, df, capital=1_000_000):
    if len(trades_df) == 0:
        return {'error': 'No trades generated'}, trades_df, None
    
    total_trades = len(trades_df)
    winning_trades = trades_df[trades_df['pnl_pct'] > 0]
    losing_trades = trades_df[trades_df['pnl_pct'] <= 0]
    winrate = len(winning_trades) / total_trades * 100
    
    equity = [capital]
    for _, trade in trades_df.iterrows():
        equity.append(equity[-1] * (1 + trade['pnl_pct']))
    
    total_return_pct = (equity[-1] / capital - 1) * 100
    peak = np.maximum.accumulate(equity)
    drawdown = (equity - peak) / peak * 100
    max_dd = drawdown.min()
    avg_return = trades_df['pnl_pct'].mean() * 100
    
    gross_profit = winning_trades['pnl_pct'].sum() if len(winning_trades) > 0 else 0
    gross_loss = abs(losing_trades['pnl_pct'].sum()) if len(losing_trades) > 0 else 1e-10
    profit_factor = gross_profit / gross_loss
    
    returns_series = trades_df['pnl_pct']
    sharpe = np.sqrt(252) * returns_series.mean() / returns_series.std() if returns_series.std() > 0 else 0
    
    first_close = df.iloc[0]['close']
    last_close = df.iloc[-1]['close']
    bh_return = (last_close / first_close - 1) * 100
    bh_daily_returns = df['close'].pct_change().dropna()
    bh_sharpe = np.sqrt(252) * bh_daily_returns.mean() / bh_daily_returns.std() if bh_daily_returns.std() > 0 else 0
    
    long_trades = trades_df[trades_df['direction'] == 'LONG']
    short_trades = trades_df[trades_df['direction'] == 'SHORT']
    long_winrate = len(long_trades[long_trades['pnl_pct'] > 0]) / len(long_trades) * 100 if len(long_trades) > 0 else 0
    short_winrate = len(short_trades[short_trades['pnl_pct'] > 0]) / len(short_trades) * 100 if len(short_trades) > 0 else 0
    
    metrics = {
        'total_trades': total_trades,
        'winning_trades': len(winning_trades),
        'losing_trades': len(losing_trades),
        'winrate_pct': round(winrate, 2),
        'total_return_pct': round(total_return_pct, 2),
        'final_capital': round(equity[-1], 2),
        'max_drawdown_pct': round(max_dd, 2),
        'avg_return_per_trade_pct': round(avg_return, 4),
        'profit_factor': round(profit_factor, 4),
        'sharpe_ratio': round(sharpe, 4),
        'long_trades': len(long_trades),
        'long_winrate_pct': round(long_winrate, 2),
        'short_trades': len(short_trades),
        'short_winrate_pct': round(short_winrate, 2),
        'buy_and_hold_return_pct': round(bh_return, 2),
        'buy_and_hold_sharpe': round(bh_sharpe, 4),
        'strategy_vs_bh_outperformance_pct': round(total_return_pct - bh_return, 2),
    }
    return metrics, trades_df, None

def analyze_signals(df, trades_df):
    print("\n=== Signal Distribution ===")
    signal_counts = df['signal'].value_counts()
    for sig, cnt in sorted(signal_counts.items()):
        label = {1: 'BUY', -1: 'SELL', 0: 'NONE'}.get(sig, sig)
        print(f"  {label}: {cnt} days")
    
    print("\n=== Spread Statistics ===")
    print(f"  Mean: {df['spread'].mean():.6f}")
    print(f"  Std:  {df['spread'].std():.6f}")
    print(f"  Min:  {df['spread'].min():.6f}")
    print(f"  Max:  {df['spread'].max():.6f}")
    for pct in [1, 5, 10, 25, 50, 75, 90, 95, 99]:
        print(f"  {pct}th percentile: {df['spread'].quantile(pct/100):.6f}")
    
    if len(trades_df) > 0:
        print(f"\n=== Direction Analysis ===")
        long_t = trades_df[trades_df['direction']=='LONG']
        short_t = trades_df[trades_df['direction']=='SHORT']
        long_avg = long_t['pnl_pct'].mean() * 100 if len(long_t) > 0 else 0
        short_avg = short_t['pnl_pct'].mean() * 100 if len(short_t) > 0 else 0
        print(f"  Avg LONG return:  {long_avg:.4f}%")
        print(f"  Avg SHORT return: {short_avg:.4f}%")

def sensitivity_analysis(df):
    print("\n=== Sensitivity Analysis ===")
    results = []
    thresholds = [0.1, 0.15, 0.2, 0.25, 0.3, 0.4, 0.5]
    holds = [3, 5, 7, 10, 14]
    for thresh in thresholds:
        for hold in holds:
            trades, _ = simulate_strategy(df, spread_buy_thresh=-thresh, spread_sell_thresh=thresh, hold_days=hold)
            if len(trades) == 0:
                continue
            winners = len(trades[trades['pnl_pct'] > 0])
            results.append({
                'threshold': thresh, 'hold_days': hold,
                'total_trades': len(trades),
                'winrate_pct': round(winners/len(trades)*100, 1),
                'total_return_pct': round(trades['pnl_pct'].sum()*100, 2),
            })
    results_df = pd.DataFrame(results)
    if len(results_df) > 0:
        print("\nTotal Return % by Threshold / Hold Days:")
        print(results_df.pivot_table(index='threshold', columns='hold_days', values='total_return_pct', aggfunc='first').to_string())
        print("\nWinrate % by Threshold / Hold Days:")
        print(results_df.pivot_table(index='threshold', columns='hold_days', values='winrate_pct', aggfunc='first').to_string())
    return results_df

def year_analysis(df):
    print("\n=== Year-by-Year Analysis (|spread|>0.3, hold=5d) ===")
    df_copy = df.copy()
    df_copy['year'] = df_copy.index.year
    for year in sorted(df_copy['year'].unique()):
        year_df = df_copy[df_copy['year'] == year]
        trades, _ = simulate_strategy(year_df)
        if len(trades) == 0:
            print(f"  {year}: No trades")
        else:
            wr = len(trades[trades['pnl_pct']>0])/len(trades)*100
            print(f"  {year}: {len(trades)} trades, Winrate {wr:.1f}%, Return {trades['pnl_pct'].sum()*100:+.2f}%")

def main():
    print("=" * 70)
    print("ALRS OI Fiz/Yur Spread Strategy Analysis")
    print("=" * 70)
    
    df = load_data()
    df = compute_indicators(df)
    
    print(f"\n=== Data Summary ===")
    print(f"Period: {df.index[0].date()} to {df.index[-1].date()}")
    print(f"Trading days: {len(df)}")
    print(f"ALRS price range: {df['close'].min():.0f} - {df['close'].max():.0f} RUB")
    
    print("\n=== Spread (fiz_net - yur_net) Analysis ===")
    print(f"  Mean: {df['spread'].mean():.6f}")
    print(f"  Std:  {df['spread'].std():.6f}")
    print(f"  Min:  {df['spread'].min():.6f}")
    print(f"  Max:  {df['spread'].max():.6f}")
    
    print("\n" + "=" * 70)
    print("MAIN STRATEGY: |spread| > 0.3, exit after 5 days")
    print("=" * 70)
    
    trades_df, df_sig = simulate_strategy(df)
    metrics, _, _ = calculate_metrics(trades_df, df)
    
    print(f"\nStrategy Performance:")
    for k, v in metrics.items():
        print(f"  {k}: {v}")
    
    if len(trades_df) > 0:
        print(f"\nFirst 5 trades:")
        print(trades_df.head(5).to_string())
        print(f"\nLast 5 trades:")
        print(trades_df.tail(5).to_string())
    
    analyze_signals(df_sig, trades_df)
    year_analysis(df)
    sens_results = sensitivity_analysis(df)
    
    print("\n" + "=" * 70)
    print("Saving results...")
    df.to_csv('/home/user/alrs_oi_daily_data.csv', index=True)
    if len(trades_df) > 0:
        trades_df.to_csv('/home/user/alrs_oi_trades.csv', index=False)
    with open('/home/user/alrs_oi_metrics.json', 'w') as f:
        json.dump(metrics, f, indent=2, default=str)
    if len(sens_results) > 0:
        sens_results.to_csv('/home/user/alrs_oi_sensitivity.csv', index=False)
    print("Files saved to /home/user/")
    print("=" * 70)
    print("ANALYSIS COMPLETE")
    print("=" * 70)

if __name__ == '__main__':
    main()
