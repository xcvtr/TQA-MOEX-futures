#!/usr/bin/env python3
"""Test and optimize two trading strategies for ALRS (Alrosa):
   1. OI fiz/yur spread strategy
   2. Seasonal short strategy
"""

import psycopg2
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

DB_CONFIG = {
    'host': '10.0.0.60',
    'dbname': 'moex',
    'user': 'postgres',
    'password': 'postgres'
}

def get_connection():
    return psycopg2.connect(**DB_CONFIG)

# ============================================================
# DATA LOADING
# ============================================================

def load_oi_data():
    conn = get_connection()
    query = """
        SELECT time, fiz_buy, fiz_sell, yur_buy, yur_sell, total_oi
        FROM moex_prices_5m_oi
        WHERE symbol = 'AL'
        ORDER BY time
    """
    df = pd.read_sql(query, conn)
    conn.close()
    df['fiz_net'] = (df['fiz_buy'] - df['fiz_sell']) / (df['fiz_buy'] + df['fiz_sell'] + 1)
    df['yur_net'] = (df['yur_buy'] - df['yur_sell']) / (df['yur_buy'] + df['yur_sell'] + 1)
    df['spread'] = df['fiz_net'] - df['yur_net']
    return df

def load_price_data():
    conn = get_connection()
    query = """
        SELECT time::date as date,
               MAX(high) as high,
               MIN(low) as low,
               MIN(open) as open,
               MAX(close) as close,
               SUM(volume) as volume
        FROM moex_prices_5m
        WHERE symbol = 'AL'
        GROUP BY time::date
        ORDER BY date
    """
    df = pd.read_sql(query, conn)
    conn.close()
    df['date'] = pd.to_datetime(df['date'])
    df['month'] = df['date'].dt.month
    df['year'] = df['date'].dt.year
    return df

def load_5m_price_data():
    conn = get_connection()
    query = """
        SELECT time, close
        FROM moex_prices_5m
        WHERE symbol = 'AL'
        ORDER BY time
    """
    df = pd.read_sql(query, conn)
    conn.close()
    df['time'] = pd.to_datetime(df['time'])
    return df

# ============================================================
# STRATEGY 1: OI fiz/yur spread
# ============================================================

def test_oi_spread_strategy(oi_df, price_df, entry_threshold, holding_days):
    price_5m = price_df.set_index('time')
    oi_idx = oi_df.set_index('time')
    
    merged = oi_idx[['spread']].join(price_5m[['close']], how='inner')
    merged = merged.dropna()
    
    trades = []
    i = 0
    while i < len(merged):
        row = merged.iloc[i]
        spread = row['spread']
        
        signal = 0
        if spread > entry_threshold:
            signal = -1  # SELL
        elif spread < -entry_threshold:
            signal = 1   # BUY
        
        if signal != 0:
            entry_price = row['close']
            entry_time = merged.index[i]
            lookahead = merged.index[i:]
            
            target_time = entry_time + timedelta(days=holding_days)
            time_diffs = np.abs((lookahead - target_time).total_seconds().values)
            exit_idx = time_diffs.argmin()
            
            if exit_idx < len(lookahead):
                exit_row = merged.iloc[i + exit_idx]
                exit_price = exit_row['close']
                
                if signal == -1:  # SELL
                    pnl_pct = (entry_price - exit_price) / entry_price * 100
                else:  # BUY
                    pnl_pct = (exit_price - entry_price) / entry_price * 100
                    
                trades.append({
                    'entry_time': entry_time,
                    'exit_time': merged.index[i + exit_idx],
                    'signal': 'SELL' if signal == -1 else 'BUY',
                    'entry_price': entry_price,
                    'exit_price': exit_price,
                    'pnl_pct': pnl_pct,
                    'spread': spread
                })
                i += exit_idx + 1
                continue
        i += 1
    
    if not trades:
        return {'total_trades': 0, 'winrate': 0, 'total_pnl': 0, 'profit_factor': 0, 
                'max_drawdown': 0, 'sharpe': 0, 'avg_pnl': 0}
    
    trades_df = pd.DataFrame(trades)
    total_trades = len(trades_df)
    wins = trades_df[trades_df['pnl_pct'] > 0]
    losses = trades_df[trades_df['pnl_pct'] <= 0]
    winrate = len(wins) / total_trades * 100
    total_pnl = trades_df['pnl_pct'].sum()
    avg_pnl = trades_df['pnl_pct'].mean()
    gross_profit = wins['pnl_pct'].sum() if len(wins) > 0 else 0
    gross_loss = abs(losses['pnl_pct'].sum()) if len(losses) > 0 else 1e-10
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
    
    equity = trades_df['pnl_pct'].cumsum()
    rolling_max = equity.cummax()
    drawdown = rolling_max - equity
    max_drawdown = drawdown.max()
    
    returns = trades_df['pnl_pct'] / 100
    if len(returns) > 1 and returns.std() > 0:
        sharpe = (returns.mean() / returns.std()) * np.sqrt(252 / max(holding_days, 1))
    else:
        sharpe = 0
    
    return {
        'total_trades': total_trades, 'winrate': winrate, 'total_pnl': total_pnl,
        'avg_pnl': avg_pnl, 'profit_factor': profit_factor, 'max_drawdown': max_drawdown,
        'sharpe': sharpe, 'wins': len(wins), 'losses': len(losses),
        'gross_profit': gross_profit, 'gross_loss': gross_loss,
        'trades_df': trades_df
    }

# ============================================================
# STRATEGY 2: Seasonal short strategy
# ============================================================

def test_seasonal_strategy(daily_df, short_months):
    daily = daily_df.copy()
    trades = []
    
    for (year, month), group in daily.groupby([daily['date'].dt.year, daily['date'].dt.month]):
        if month in short_months:
            group = group.sort_values('date')
            if len(group) < 2:
                continue
            entry_price = group.iloc[0]['close']
            entry_date = group.iloc[0]['date']
            exit_price = group.iloc[-1]['close']
            exit_date = group.iloc[-1]['date']
            pnl_pct = (entry_price - exit_price) / entry_price * 100
            bh_pnl = (exit_price - entry_price) / entry_price * 100
            trades.append({
                'entry_date': entry_date, 'exit_date': exit_date,
                'entry_price': entry_price, 'exit_price': exit_price,
                'pnl_pct': pnl_pct, 'bh_pnl': bh_pnl,
                'year': year, 'month': month
            })
    
    if not trades:
        return {'total_trades': 0, 'winrate': 0, 'total_pnl': 0, 'profit_factor': 0,
                'max_drawdown': 0, 'sharpe': 0, 'avg_pnl': 0}
    
    trades_df = pd.DataFrame(trades)
    total_trades = len(trades_df)
    wins = trades_df[trades_df['pnl_pct'] > 0]
    losses = trades_df[trades_df['pnl_pct'] <= 0]
    winrate = len(wins) / total_trades * 100
    total_pnl = trades_df['pnl_pct'].sum()
    avg_pnl = trades_df['pnl_pct'].mean()
    gross_profit = wins['pnl_pct'].sum() if len(wins) > 0 else 0
    gross_loss = abs(losses['pnl_pct'].sum()) if len(losses) > 0 else 1e-10
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
    
    equity = trades_df['pnl_pct'].cumsum()
    rolling_max = equity.cummax()
    drawdown = rolling_max - equity
    max_drawdown = drawdown.max()
    
    returns = trades_df['pnl_pct'] / 100
    if len(returns) > 1 and returns.std() > 0:
        n_months = len(short_months)
        sharpe = (returns.mean() / returns.std()) * np.sqrt(12 / max(n_months, 1))
    else:
        sharpe = 0
    
    total_bh_pnl = trades_df['bh_pnl'].sum()
    
    yearly = trades_df.groupby('year').agg(
        trades=('pnl_pct', 'count'),
        pnl_sum=('pnl_pct', 'sum'),
        wins=('pnl_pct', lambda x: (x > 0).sum()),
        winrate=('pnl_pct', lambda x: (x > 0).sum() / len(x) * 100),
        avg_pnl=('pnl_pct', 'mean')
    ).reset_index()
    
    return {
        'total_trades': total_trades, 'winrate': winrate, 'total_pnl': total_pnl,
        'avg_pnl': avg_pnl, 'profit_factor': profit_factor, 'max_drawdown': max_drawdown,
        'sharpe': sharpe, 'wins': len(wins), 'losses': len(losses),
        'gross_profit': gross_profit, 'gross_loss': gross_loss,
        'total_bh_pnl': total_bh_pnl, 'yearly': yearly, 'trades_df': trades_df
    }

def test_seasonal_all_qs(daily_df):
    scenarios = {
        'Q2 (Apr-May)': [4, 5],
        'Q3 (Jul-Sep)': [7, 8, 9],
        'Q4 (Oct-Dec)': [10, 11, 12],
        'Aug-Oct': [8, 9, 10],
        'Apr-Jun (Q2 full)': [4, 5, 6],
        'Jan-Mar': [1, 2, 3],
        'May only': [5],
        'April only': [4],
        'Sep only': [9],
        'Oct only': [10],
        'Jun only': [6],
        'Aug only': [8],
    }
    results = []
    for name, months in scenarios.items():
        res = test_seasonal_strategy(daily_df, months)
        results.append({
            'Scenario': name, 'Months': str(months),
            'Trades': res['total_trades'],
            'WinR%': f"{res['winrate']:.1f}",
            'TotPnL%': f"{res['total_pnl']:.2f}",
            'AvgPnL%': f"{res['avg_pnl']:.2f}",
            'PF': f"{res['profit_factor']:.2f}",
            'MaxDD%': f"{res['max_drawdown']:.2f}",
            'Sharpe': f"{res['sharpe']:.2f}",
            'BH_PnL%': f"{res['total_bh_pnl']:.2f}",
        })
    return pd.DataFrame(results)

# ============================================================
# MAIN
# ============================================================

def main():
    print("=" * 95)
    print("  ALRS Strategy Testing & Optimization")
    print("  Data: moex (10.0.0.60) | Prices: 2023-01 to 2026-05 | OI: 2021-01 to 2026-05")
    print("=" * 95)
    
    print("\n[1/4] Loading OI data...", flush=True)
    oi_df = load_oi_data()
    print(f"  OI data: {len(oi_df):,} rows, {oi_df['time'].min()} to {oi_df['time'].max()}")
    
    print("[2/4] Loading 5m price data...", flush=True)
    price_5m = load_5m_price_data()
    print(f"  Price 5m: {len(price_5m):,} rows, {price_5m['time'].min()} to {price_5m['time'].max()}")
    
    print("[3/4] Loading daily OHLCV...", flush=True)
    daily_df = load_price_data()
    print(f"  Daily: {len(daily_df):,} rows, {daily_df['date'].min()} to {daily_df['date'].max()}")
    
    print("[4/4] Running strategy tests...", flush=True)
    
    # =========================================
    # STRATEGY 1
    # =========================================
    print()
    print("=" * 95)
    print("  STRATEGY 1: OI fiz/yur Spread Strategy")
    print("=" * 95)
    
    thresholds = [0.15, 0.20, 0.25, 0.30, 0.40, 0.50]
    hold_periods = [2, 3, 5, 7, 10, 14]
    
    print("\n  [Spread Distribution]")
    print(f"  Mean: {oi_df['spread'].mean():.4f} | Std: {oi_df['spread'].std():.4f} | Median: {oi_df['spread'].median():.4f}")
    print(f"  Min: {oi_df['spread'].min():.4f} | Max: {oi_df['spread'].max():.4f}")
    for p in [90, 95, 98, 99]:
        val = oi_df['spread'].abs().quantile(p/100)
        print(f"  |spread| p{p}: {val:.4f}")
    
    print("\n  [Optimization Grid: Threshold x Hold Days]")
    print()
    print(f"  {'Threshold':<10} {'Hold(d)':<8} {'Trades':<8} {'WinR%':<8} {'TotPnL%':<10} {'AvgPnL%':<9} {'PF':<8} {'MaxDD%':<9} {'Sharpe':<8}")
    print(f"  " + "-" * 85)
    
    opt_results = []
    for thresh in thresholds:
        for hold in hold_periods:
            res = test_oi_spread_strategy(oi_df, price_5m, thresh, hold)
            opt_results.append({
                'threshold': thresh, 'hold_days': hold,
                'trades': res['total_trades'], 'winrate': res['winrate'],
                'total_pnl': res['total_pnl'], 'avg_pnl': res['avg_pnl'],
                'profit_factor': res['profit_factor'], 'max_dd': res['max_drawdown'],
                'sharpe': res['sharpe']
            })
            print(f"  {thresh:<10.2f} {hold:<8d} {res['total_trades']:<8d} {res['winrate']:<8.1f} {res['total_pnl']:<10.2f} {res['avg_pnl']:<9.2f} {res['profit_factor']:<8.2f} {res['max_drawdown']:<9.2f} {res['sharpe']:<8.2f}")
    
    # Best by Sharpe
    best_s = max(opt_results, key=lambda x: x['sharpe'])
    print(f"\n  >> Best by Sharpe: threshold={best_s['threshold']:.2f}, hold={best_s['hold_days']}d (Sharpe={best_s['sharpe']:.2f}, PnL={best_s['total_pnl']:.2f}%)")
    
    # Best by Total PnL
    best_p = max(opt_results, key=lambda x: x['total_pnl'])
    print(f"  >> Best by PnL: threshold={best_p['threshold']:.2f}, hold={best_p['hold_days']}d (PnL={best_p['total_pnl']:.2f}%, Sharpe={best_p['sharpe']:.2f})")
    
    # Top 3 by PF
    best_pf = sorted(opt_results, key=lambda x: x['profit_factor'], reverse=True)[:3]
    print(f"  >> Top 3 by Profit Factor:")
    for r in best_pf:
        print(f"      threshold={r['threshold']:.2f}, hold={r['hold_days']}d -> PF={r['profit_factor']:.2f}, PnL={r['total_pnl']:.2f}%, Sharpe={r['sharpe']:.2f}")
    
    # Detailed best Sharpe
    print(f"\n  [Detailed: Best Sharpe Config ({best_s['threshold']:.2f} thresh, {best_s['hold_days']}d hold)]")
    det_res = test_oi_spread_strategy(oi_df, price_5m, best_s['threshold'], best_s['hold_days'])
    tdf = det_res.get('trades_df')
    if tdf is not None and len(tdf) > 0:
        tdf['year'] = tdf['entry_time'].dt.year
        for yr in sorted(tdf['year'].unique()):
            yr_trades = tdf[tdf['year'] == yr]
            yr_wins = (yr_trades['pnl_pct'] > 0).sum()
            print(f"      {int(yr)}: {len(yr_trades)} trades, {yr_wins}W/{len(yr_trades)-yr_wins}L, WinR={yr_wins/len(yr_trades)*100:.1f}%, PnL={yr_trades['pnl_pct'].sum():.2f}%")
        
        # Signal distribution
        sell_trades = tdf[tdf['signal'] == 'SELL']
        buy_trades = tdf[tdf['signal'] == 'BUY']
        print(f"      SELL signals: {len(sell_trades)} trades, PnL={sell_trades['pnl_pct'].sum():.2f}%, WinR={(sell_trades['pnl_pct']>0).sum()/len(sell_trades)*100:.1f}%")
        if len(buy_trades) > 0:
            print(f"      BUY signals: {len(buy_trades)} trades, PnL={buy_trades['pnl_pct'].sum():.2f}%, WinR={(buy_trades['pnl_pct']>0).sum()/len(buy_trades)*100:.1f}%")
    
    # =========================================
    # STRATEGY 2
    # =========================================
    print()
    print("=" * 95)
    print("  STRATEGY 2: Seasonal Short Strategy")
    print("=" * 95)
    
    years_available = sorted(daily_df['year'].unique())
    print(f"\n  Available years: {years_available}")
    
    print(f"\n  [All Scenarios - Full Period]")
    scenarios_df = test_seasonal_all_qs(daily_df)
    print(f"\n  {scenarios_df.to_string(index=False)}")
    
    # Yearly breakdowns for top scenarios
    print(f"\n  [Yearly Breakdown: Q2 (Apr-May) Short]")
    q2_res = test_seasonal_strategy(daily_df, [4, 5])
    if q2_res['total_trades'] > 0:
        print(f"  {'Year':<8} {'Trades':<8} {'PnL%':<10} {'Wins':<8} {'WinR%':<8} {'AvgPnL%':<9}")
        print(f"  " + "-" * 51)
        for _, row in q2_res['yearly'].iterrows():
            print(f"  {int(row['year']):<8} {int(row['trades']):<8} {row['pnl_sum']:<10.2f} {int(row['wins']):<8} {row['winrate']:<8.1f} {row['avg_pnl']:<9.2f}")
        print(f"  Total: PnL={q2_res['total_pnl']:.2f}% | BH={q2_res['total_bh_pnl']:.2f}% | WinR={q2_res['winrate']:.1f}% | PF={q2_res['profit_factor']:.2f} | Sharpe={q2_res['sharpe']:.2f} | MaxDD={q2_res['max_drawdown']:.2f}%")
    
    print(f"\n  [Yearly Breakdown: Aug-Oct Short]")
    ao_res = test_seasonal_strategy(daily_df, [8, 9, 10])
    if ao_res['total_trades'] > 0:
        print(f"  {'Year':<8} {'Trades':<8} {'PnL%':<10} {'Wins':<8} {'WinR%':<8} {'AvgPnL%':<9}")
        print(f"  " + "-" * 51)
        for _, row in ao_res['yearly'].iterrows():
            print(f"  {int(row['year']):<8} {int(row['trades']):<8} {row['pnl_sum']:<10.2f} {int(row['wins']):<8} {row['winrate']:<8.1f} {row['avg_pnl']:<9.2f}")
        print(f"  Total: PnL={ao_res['total_pnl']:.2f}% | BH={ao_res['total_bh_pnl']:.2f}% | WinR={ao_res['winrate']:.1f}% | PF={ao_res['profit_factor']:.2f} | Sharpe={ao_res['sharpe']:.2f} | MaxDD={ao_res['max_drawdown']:.2f}%")
    
    print(f"\n  [Yearly Breakdown: Q3 (Jul-Sep) Short]")
    q3_res = test_seasonal_strategy(daily_df, [7, 8, 9])
    if q3_res['total_trades'] > 0:
        print(f"  {'Year':<8} {'Trades':<8} {'PnL%':<10} {'Wins':<8} {'WinR%':<8} {'AvgPnL%':<9}")
        print(f"  " + "-" * 51)
        for _, row in q3_res['yearly'].iterrows():
            print(f"  {int(row['year']):<8} {int(row['trades']):<8} {row['pnl_sum']:<10.2f} {int(row['wins']):<8} {row['winrate']:<8.1f} {row['avg_pnl']:<9.2f}")
        print(f"  Total: PnL={q3_res['total_pnl']:.2f}% | BH={q3_res['total_bh_pnl']:.2f}% | WinR={q3_res['winrate']:.1f}% | PF={q3_res['profit_factor']:.2f} | Sharpe={q3_res['sharpe']:.2f} | MaxDD={q3_res['max_drawdown']:.2f}%")
    
    # Individual month seasonality
    print(f"\n  [Individual Month Seasonality - Short PnL]")
    monthly_results = []
    for m in range(1, 13):
        res = test_seasonal_strategy(daily_df, [m])
        if res['total_trades'] > 0:
            monthly_results.append({
                'Month': f"{m:02d}", 'Trades': res['total_trades'],
                'AvgPnL%': res['avg_pnl'], 'TotPnL%': res['total_pnl'],
                'WinR%': res['winrate'], 'PF': res['profit_factor'],
                'Sharpe': res['sharpe']
            })
    
    print(f"\n  {'Month':<8} {'Trades':<8} {'AvgPnL%':<10} {'TotPnL%':<10} {'WinR%':<8} {'PF':<8} {'Sharpe':<8}")
    print(f"  " + "-" * 64)
    for r in monthly_results:
        print(f"  {r['Month']:<8} {r['Trades']:<8} {r['AvgPnL%']:<10.2f} {r['TotPnL%']:<10.2f} {r['WinR%']:<8.1f} {r['PF']:<8.2f} {r['Sharpe']:<8.2f}")
    
    # =========================================
    # SUMMARY
    # =========================================
    print()
    print("=" * 95)
    print("  SUMMARY")
    print("=" * 95)
    
    print("\n  STRATEGY 1: OI fiz/yur Spread")
    print(f"    Best config:  threshold={best_s['threshold']:.2f}, hold={best_s['hold_days']}d")
    print(f"    Sharpe={best_s['sharpe']:.2f}, PnL={best_s['total_pnl']:.2f}%, PF={best_s['profit_factor']:.2f}")
    
    print(f"\n    Best PnL config: threshold={best_p['threshold']:.2f}, hold={best_p['hold_days']}d")
    print(f"    PnL={best_p['total_pnl']:.2f}%, Sharpe={best_p['sharpe']:.2f}, PF={best_p['profit_factor']:.2f}")
    
    print(f"\n  STRATEGY 2: Seasonal Short")
    print(f"    Q2 (Apr-May): PnL={q2_res['total_pnl']:.2f}% | Sharpe={q2_res['sharpe']:.2f} | WinR={q2_res['winrate']:.1f}%")
    print(f"    Aug-Oct:      PnL={ao_res['total_pnl']:.2f}% | Sharpe={ao_res['sharpe']:.2f} | WinR={ao_res['winrate']:.1f}%")
    print(f"    Q3 (Jul-Sep):  PnL={q3_res['total_pnl']:.2f}% | Sharpe={q3_res['sharpe']:.2f} | WinR={q3_res['winrate']:.1f}%")
    
    best_month = min(monthly_results, key=lambda x: x['AvgPnL%'])
    print(f"\n    Best single month to short: Month {best_month['Month']} (Avg PnL={best_month['AvgPnL%']:.2f}%)")
    
    neg_months = [r for r in monthly_results if r['AvgPnL%'] < 0]
    print(f"    Months with negative avg short return: {len(neg_months)}/12")
    for r in sorted(neg_months, key=lambda x: x["AvgPnL%"]):
        print(f"      Month {r['Month']}: Avg={r['AvgPnL%']:.2f}%, WinR={r['WinR%']:.1f}%, Sharpe={r['Sharpe']:.2f}")

if __name__ == '__main__':
    main()
