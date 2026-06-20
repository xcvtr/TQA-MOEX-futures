#!/usr/bin/env python3
"""Портфельный тест E6 (M15, lot=50%, bars=4, stop=1.0ATR) на 6 тикерах с equity curve."""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
import clickhouse_connect
from scripts.bar_level_sim import TICKER_CONFIGS
from config import CH_HOST, CH_PORT, CH_DB

INITIAL_CAPITAL = 100_000
TEST_START = pd.Timestamp('2025-01-01')
TEST_END = pd.Timestamp('2026-05-01')
SYMBOLS = ['GL', 'HS', 'HY', 'RN', 'NM', 'AF']

def rz(s, w=20):
    m = s.rolling(w, min_periods=w).mean()
    std = s.rolling(w, min_periods=w).std()
    return (s - m) / std.clip(lower=1e-10)

def calc_atr(df, p=14):
    prev = df['close'].shift(1)
    tr = pd.concat([
        df['high'] - df['low'],
        (df['high'] - prev).abs(),
        (df['low'] - prev).abs()
    ], axis=1).max(axis=1)
    return tr.rolling(p, min_periods=p).mean().bfill().fillna(0)

def load_data(sym):
    ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)
    q = f"""
        SELECT p.time, p.open, p.high, p.low, p.close, p.volume,
               o.fiz_buy, o.fiz_sell, o.yur_buy, o.yur_sell, o.total_oi
        FROM moex.prices_5m p
        LEFT JOIN moex.prices_5m_oi o ON p.time = o.time AND p.symbol = o.symbol
        WHERE p.symbol='{sym}' AND p.time>='2023-01-01' AND p.time<='2026-04-30'
        ORDER BY p.time
    """
    r = ch.query(q)
    cols = ['time','open','high','low','close','volume',
            'fiz_buy','fiz_sell','yur_buy','yur_sell','total_oi']
    df = pd.DataFrame(r.result_rows, columns=cols)
    df['time'] = pd.to_datetime(df['time']).dt.tz_localize(None)
    df.set_index('time', inplace=True)
    return df

def resample_to_15m(df):
    ohlc = df['close'].resample('15min').ohlc()
    vol = df['volume'].resample('15min').sum()
    rez = ohlc.copy()
    rez['volume'] = vol
    for col in ['fiz_buy','fiz_sell','yur_buy','yur_sell','total_oi']:
        rez[col] = df[col].resample('15min').last().fillna(0)
    return rez.dropna()

def precompute_base(df):
    d = df.copy()
    d['volume'] = d['volume'].astype(float)
    d['vma20'] = d['volume'].rolling(20).mean().fillna(d['volume'])
    d['vr'] = d['volume'] / d['vma20'].clip(lower=1)
    d['vz'] = rz(d['volume'], 20).shift(1)
    d['fiz_net'] = d['fiz_buy'].fillna(0) - d['fiz_sell'].fillna(0)
    d['yur_net'] = d['yur_buy'].fillna(0) - d['yur_sell'].fillna(0)
    d['oi_r'] = (d['yur_buy']+d['yur_sell']).fillna(0) / (d['fiz_buy']+d['fiz_sell']+1).fillna(0)
    d['oima'] = d['oi_r'].rolling(20).mean()
    d['atr14'] = calc_atr(d)
    d['atr_pct'] = d['atr14'] / d['close'].clip(lower=1) * 100
    vs = np.clip((d['vr'] - 1.5) / 3.0, 0, 1)
    os_ = np.clip((d['oima'] - d['oi_r']) / d['oima'].clip(lower=0.1), 0, 1)
    d['oi_accel'] = d['oi_r'].diff().rolling(5).mean()
    d['fiz_yur_delta'] = (d['fiz_net'] - d['yur_net']).abs() / (d['fiz_net'].abs() + d['yur_net'].abs() + 1)
    raw = vs*0.3 + os_*0.7 + d['oi_accel']*0.5 + d['fiz_yur_delta']*0.3
    af = np.clip(1 - (d['atr_pct'] - 0.3) / 3.0, 0, 1)
    score = np.clip(raw * af * np.clip(1 + d['vz'] / 5, 0.5, 1.5), 0, 1)
    d['score'] = score
    yur_vol = (d['yur_buy'] + d['yur_sell']).clip(lower=1)
    d['oi_ratio'] = d['yur_net'] / yur_vol
    d['oi_ratio_z'] = rz(d['oi_ratio'], 20)
    return d

def simulate(df, start, end, lot_pct=0.50, bars_left=4, stop_atr=1.0, sym=None, collect_equity=False):
    mask = (df.index >= start) & (df.index < end)
    d = df[mask].copy()
    if len(d) == 0:
        return None

    cash = INITIAL_CAPITAL / len(SYMBOLS)  # равное распределение
    peak = cash
    max_dd = 0
    trades = 0
    wins = 0
    equity = [] if collect_equity else None

    pos = None
    for i in range(1, len(d)):
        bar = d.iloc[i]
        ts = bar.name
        h = ts.hour if hasattr(ts, 'hour') else pd.Timestamp(ts).hour
        if h < 7 or h >= 23:
            if collect_equity:
                teq = cash if pos is None else cash + (1 if pos['dir']=='L' else -1) * (bar['close']-pos['entry'])/pos['entry']*pos['go']*pos['contracts']
                equity.append({'time': ts, 'equity': teq, 'sym': sym})
            continue

        if pos is not None:
            pos['bars_left'] -= 1
            hit = False
            ep = bar['close']
            if pos['dir'] == 'L' and bar['low'] <= pos['stop']:
                hit = True; ep = pos['stop']
            elif pos['dir'] == 'S' and bar['high'] >= pos['stop']:
                hit = True; ep = pos['stop']
            elif pos['bars_left'] <= 0:
                hit = True
            if hit:
                dm = 1 if pos['dir'] == 'L' else -1
                pp = dm * (ep - pos['entry']) / pos['entry']
                pr = pp * pos['go'] * pos['contracts']
                cash += pr
                trades += 1
                if pr > 0: wins += 1
                pos = None

        if pos is not None:
            dm = 1 if pos['dir'] == 'L' else -1
            mtm = dm * (bar['close']-pos['entry'])/pos['entry']*pos['go']*pos['contracts']
            teq = cash + mtm
        else:
            teq = cash
        if teq > peak: peak = teq
        ddv = (peak - teq) / peak if peak > 0 else 0
        if ddv > max_dd: max_dd = ddv

        if collect_equity:
            equity.append({'time': ts, 'equity': teq, 'sym': sym})

        if pos is not None:
            continue

        score = float(bar['score'])
        if np.isnan(score) or score < 0.10:
            continue

        go = TICKER_CONFIGS.get(sym, {}).get('go', 5000)
        max_rub = cash * lot_pct
        contracts = int(max_rub / go)
        if contracts < 1:
            continue
        atrv = float(bar.get('atr14', 1))
        ep = float(bar['close'])
        stop = ep - atrv * stop_atr if True else ep + atrv * stop_atr  # L only for now
        pos = {'dir': 'L', 'entry': ep, 'stop': stop,
               'bars_left': bars_left, 'go': go, 'contracts': contracts}

    if pos is not None:
        lb = d.iloc[-1]
        dm = 1 if pos['dir'] == 'L' else -1
        pp = dm * (lb['close'] - pos['entry']) / pos['entry']
        pr = pp * pos['go'] * pos['contracts']
        cash += pr
        trades += 1
        if pr > 0: wins += 1

    tr = (cash - INITIAL_CAPITAL/len(SYMBOLS)) / (INITIAL_CAPITAL/len(SYMBOLS)) * 100
    days = (end - start).days
    years = max(days / 365.25, 0.1)
    cagr = ((cash / (INITIAL_CAPITAL/len(SYMBOLS))) ** (1/years) - 1) * 100 if cash > 0 else -100
    calmar = tr / 100 / max(max_dd, 0.001) if max_dd > 0 else tr * 10

    return {
        'sym': sym,
        'return_pct': round(tr, 2),
        'cagr_pct': round(cagr, 2),
        'max_dd_pct': round(max_dd * 100, 2),
        'calmar': round(calmar, 2),
        'wr_pct': round(wins/trades*100, 2) if trades>0 else 0,
        'trades': trades,
        'final_capital': round(cash, 2),
    }, equity or []

def main():
    all_equity = []
    results = []
    for sym in SYMBOLS:
        print(f"  {sym}...", end=' ', flush=True)
        df = load_data(sym)
        d15 = resample_to_15m(df)
        d = precompute_base(d15)
        res, eq = simulate(d, TEST_START, TEST_END, collect_equity=True, sym=sym)
        if res:
            results.append(res)
            all_equity.extend(eq)
            print(f"Ret={res['return_pct']:.1f}% DD={res['max_dd_pct']:.1f}% Calmar={res['calmar']:.1f} Trades={res['trades']}")
        else:
            print("NO DATA")

    # Портфельная equity — суммируем по времени
    eq_df = pd.DataFrame(all_equity)
    eq_df['time'] = pd.to_datetime(eq_df['time'])
    portfolio_eq = eq_df.groupby('time')['equity'].sum().reset_index()
    portfolio_eq.columns = ['time', 'equity']

    # Добавляем начальный капитал
    initial_total = INITIAL_CAPITAL
    first_row = pd.DataFrame({'time': [portfolio_eq['time'].min() - pd.Timedelta(minutes=15)], 'equity': [initial_total]})
    portfolio_eq = pd.concat([first_row, portfolio_eq], ignore_index=True).sort_values('time')

    port_val = portfolio_eq['equity'].values
    port_peak = np.maximum.accumulate(port_val)
    port_dd = (port_peak - port_val) / port_peak
    port_max_dd = port_dd.max()
    port_return = (port_val[-1] - initial_total) / initial_total * 100
    days = (portfolio_eq['time'].iloc[-1] - portfolio_eq['time'].iloc[0]).total_seconds() / 86400
    years = max(days / 365.25, 0.1)
    port_cagr = ((port_val[-1] / initial_total) ** (1/years) - 1) * 100 if port_val[-1] > 0 else -100
    port_calmar = port_return / 100 / max(port_max_dd, 0.001)

    print(f"\n{'='*60}")
    print(f"  ПОРТФЕЛЬ E6 (M15, lot=50%, bars=4, stop=1.0ATR)")
    print(f"{'='*60}")
    print(f"  Начальный капитал: {initial_total:,.0f} ₽")
    print(f"  Конечный капитал:  {port_val[-1]:,.0f} ₽")
    print(f"  Доходность:        {port_return:.1f}%")
    print(f"  CAGR:              {port_cagr:.1f}%")
    print(f"  MAX DD:            {port_max_dd*100:.1f}%")
    print(f"  Calmar:            {port_calmar:.1f}")
    print(f"  Период:            {portfolio_eq['time'].iloc[0].strftime('%Y-%m-%d')} — {portfolio_eq['time'].iloc[-1].strftime('%Y-%m-%d')}")

    # Сохраняем equity для графика
    portfolio_eq.to_json('reports/equity_e6.json', orient='records', date_format='iso')
    print(f"\n  Equity сохранена в reports/equity_e6.json")

    # Вывод по тикерам
    print(f"\n  По тикерам:")
    print(f"  {'Тикер':<6} {'Ret%':>8} {'CAGR%':>8} {'DD%':>6} {'Calmar':>8} {'WR%':>6} {'Сделок':>7}")
    print(f"  {'-'*49}")
    for r in results:
        print(f"  {r['sym']:<6} {r['return_pct']:>7.1f}% {r['cagr_pct']:>7.1f}% {r['max_dd_pct']:>5.1f}% {r['calmar']:>7.1f} {r['wr_pct']:>5.1f}% {r['trades']:>6}")

if __name__ == '__main__':
    main()
