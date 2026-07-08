#!/usr/bin/env python3
"""Test Impulse Return strategy standalone on 2024-2026."""
import sys, os, logging
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import numpy as np
import clickhouse_connect as cc
from strategies.common.engine import PortfolioEngine
from strategies.common.broker import BrokerSim
from strategies.impulse_return.prod.engine import check_signal, reset_state

logging.basicConfig(level=logging.WARNING)
CAPITAL = 200_000
CH_HOST = '10.0.0.60'
CH_DB = 'moex'

# Тикеры из tradestats_fo
TICKERS = ['Si', 'GAZR', 'ROSN', 'MIX', 'LKOH', 'SNGP', 'MTSI', 'TATN']
ASSET_MAP = {'Si':'Si','GAZR':'GAZR','ROSN':'ROSN','MIX':'MX','LKOH':'LK','SNGP':'SN','MTSI':'MN','TATN':'TT'}
# ticker_specs (ГО, step_price, min_step) — из PG или SPECS
TICKER_SPECS = {}
try:
    import psycopg2
    pg = psycopg2.connect(host=CH_HOST, dbname='moex', user='user')
    cur = pg.cursor()
    cur.execute("SELECT ticker, go, step_price, min_step, commission FROM futures.ticker_specs")
    for r in cur.fetchall():
        TICKER_SPECS[r[0]] = {'go': float(r[1]), 'sp': float(r[2]), 'ms': float(r[3]), 'fee': float(r[4])}
    pg.close()
except:
    pass
# Fallback SPECS
FALLBACK = {
    'Si': {'go':12543,'sp':1,'ms':1,'fee':4.02},
    'GAZR': {'go':5000,'sp':1,'ms':1,'fee':5.0},
    'ROSN': {'go':3322,'sp':1,'ms':1,'fee':3.49},
    'MIX': {'go':12983,'sp':25,'ms':25,'fee':7.64},
    'LKOH': {'go':4081,'sp':1,'ms':1,'fee':4.81},
    'SNGP': {'go':4121,'sp':1,'ms':1,'fee':4.17},
    'MTSI': {'go':1500,'sp':1,'ms':1,'fee':2.0},
    'TATN': {'go':4158,'sp':1,'ms':1,'fee':4.78},
}

def get_spec(ticker):
    return TICKER_SPECS.get(ticker, FALLBACK.get(ticker, {'go':5000,'sp':1,'ms':1,'fee':4}))

print("Loading data...", end=' ', flush=True)
ch = cc.get_client(host=CH_HOST, port=8123, database=CH_DB)
import pandas as pd
all_data = {}
for ticker in TICKERS:
    q = f"""
        SELECT tradedate, tradetime, pr_open, pr_high, pr_low, pr_close, vol
        FROM moex.tradestats_fo
        WHERE asset_code='{ticker}' AND tradedate>='2024-06-01' AND tradedate<'2026-06-01' AND vol>0
        ORDER BY tradedate, tradetime
    """
    r = ch.query(q)
    rows = r.result_rows
    if len(rows) < 200: continue
    
    bt = [f"{x[0]} {x[1]}" for x in rows]
    df = pd.DataFrame({
        'bt': pd.to_datetime(bt),
        'opn': [float(x[2]) for x in rows],
        'hi': [float(x[3]) for x in rows],
        'lo': [float(x[4]) for x in rows],
        'prc': [float(x[5]) for x in rows],
        'vol': [float(x[6]) for x in rows],
    })
    # Фильтр off-hours (MSK 10-18:45 → IRK 15-23:45)
    h = df['bt'].dt.hour
    m = df['bt'].dt.minute
    df = df[(h >= 15) | ((h >= 0) & (h <= 4))].copy()
    # Только основные часы 15-23:45 IRK = 10-18:45 MSK
    df = df[((h >= 15) | ((h >= 0) & (h <= 4)))]
    df = df[(h != 4) | (m == 0)]  # 4:00 IRK = 23:00 MSK? нет, 4:00 IRK оставляем
    df = df[(h >= 15) | (h <= 4)]
    
    if len(df) < 200: continue
    df.set_index('bt', inplace=True)
    # Localize to IRK (CH server timezone)
    df.index = df.index.tz_localize('Asia/Irkutsk')
    # MSK hour/min для стратегий
    df['hour'] = df.index.hour
    df['minute'] = df.index.minute
    all_data[ticker] = df

ch.close()
print(f"{len(all_data)} tickers loaded")

# Median vol per ticker for filtering
med_vols = {t: float(df['vol'].median()) for t, df in all_data.items()}

# Параметры
params = {
    'impulse_bars': 4,
    'impulse_pct': 0.5,
    'retrace': 0.618,
    'cooldown': 24,
    'min_vol_pct': 0.8,
}

# Override check_signal to inject median_vol
def check_with_vol(bd, tk, p=None):
    if p is None: p = params
    p = {**p}
    if tk in med_vols:
        p['median_vol'] = med_vols[tk]
    sig = check_signal(bd, tk, p)
    return sig

reset_state()
engine = PortfolioEngine(
    strategies=[('impulse_return', check_with_vol, list(all_data.keys()), params)],
    broker=BrokerSim(CAPITAL),
    capital=CAPITAL,
)

# Override process_signal для пропуска ticker_specs
specs = {t: get_spec(t) for t in all_data.keys()}
orig_process = engine.executor.process_signal
def process_with_specs(signal, bar_idx, specs, bar_data):
    if not signal: return False
    t = signal['ticker']; d = signal['direction']
    sp = specs.get(t, FALLBACK.get(t, {'go':5000,'sp':1,'ms':1,'fee':4}))
    go = sp['go']; step_price = sp['sp']; min_step = sp['ms']; fee = sp['fee']
    
    contracts = 1  # 1 контракт фикс
    # Проверка ГО
    if go * contracts > engine.executor.broker.capital * 0.99:
        return False
    slippage = min_step
    entry_price = bar_data['opn'] + (slippage if d == 'long' else -slippage)
    engine.executor.broker.open_position(
        t, d, entry_price, bar_idx, contracts,
        go=go, step_price=step_price, min_step=min_step,
        strategy=signal.get('strategy', ''),
        trailing_params={'timeout': 12, 'activation': 0.5, 'trail': 0.3, 'stop_loss': 0.7, 'commission': fee, 'slippage': 1, 'exit_mode': 'trailing'}
    )
    return True
engine.executor.process_signal = process_with_specs

print("Running backtest...")
trades, eq_curve = engine.run(all_data, ticker_specs=specs)
print(f"Done: {len(trades)} trades")

# Summary
by_t = {}
for t in trades: by_t.setdefault(t.ticker, []).append(t)
total_pnl = sum(t.pnl_rub for t in trades)
n = len(trades)
wr = sum(1 for t in trades if t.pnl_rub > 0) / n * 100 if n else 0

print(f"\n{'='*60}")
print(f"  IMPULSE RETURN — 2024-06 → 2026-06")
print(f"{'='*60}")
print(f"{'':>30s}{'Сделок':>8s}{'WR%':>7s}{'PnL ₽':>15s}")
print(f"{'─'*60}")
for t in sorted(by_t.keys()):
    tl = by_t[t]
    swr = sum(1 for x in tl if x.pnl_rub > 0) / len(tl) * 100
    spnl = sum(x.pnl_rub for x in tl)
    print(f"  {t:>30s} {len(tl):>8d} {swr:>6.1f}% {spnl:>+14,.0f}")
print(f"{'─'*60}")
print(f"  {'ИТОГО':>30s} {n:>8d} {wr:>6.1f}% {total_pnl:>+14,.0f}")
print(f"  Capital: {CAPITAL:,} → {CAPITAL + total_pnl:,.0f} ₽")
print(f"  Return: {total_pnl/CAPITAL*100:+.1f}%")

# MDD
if len(eq_curve) > 1:
    eq = np.array(eq_curve)
    peak = np.maximum.accumulate(eq)
    dd = (peak - eq) / peak * 100
    print(f"  Cash MDD: {np.max(dd):.1f}%")
