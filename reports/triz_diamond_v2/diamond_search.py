#!/usr/bin/env python3
"""
TRIZ Diamond Search v2 — переосмысленный поиск с:
1. Экономический календарь — исключение дней вокруг ставки ЦБ РФ
2. TRIZ фильтры: качество сигнала, stacked confirmation, volatility-adjusted hold
3. OI-фильтр: yur_buy change direction (работает на D1)
4. Trade filtering по magnitude сигнала (не все равны)
5. ATR-adjusted exit вместо фиксированного hold
"""
import sys, os, json, time, math
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/../..')
os.chdir(os.path.dirname(os.path.abspath(__file__)) + '/../..')

import numpy as np
import clickhouse_connect
from datetime import datetime, timedelta
from config import CH_HOST, CH_PORT, CH_DB

# ── Config ─────────────────────────────
OUTPUT_DIR = 'reports/triz_diamond_v2'
CAPITAL = 200_000
COMM = 4
RISK_PCT = 0.02
MAX_LOT = 5
MAX_LEV = 5.0

# Тикеры с OI + volume + ГО < CAPITAL/3
TICKERS = ['AF','AL','BR','CC','CNYRUBF','Eu','GD','GL',
           'IMOEXF','LK','NM','PD','PT','RI','RN','Si','SR','SV',
           'USDRUBF','VB','CR','NG','MX']

CS_MAP = {
    'AF':1, 'AL':100, 'BR':10, 'CC':10, 'CNYRUBF':1000,
    'Eu':1000, 'GD':1, 'GL':1, 'IMOEXF':10, 'LK':10,
    'NM':10, 'PD':1, 'PT':1, 'RI':1, 'RN':100, 'Si':1000,
    'SR':100, 'SV':10, 'USDRUBF':1000, 'VB':100, 'CR':10, 'NG':100, 'MX':1,
}

GO_MAP = {
    'AF':673, 'CC':506, 'PT':31749, 'GD':32003, 'BR':17228,
    'SR':6620, 'VB':1556, 'NM':256, 'LK':11606, 'RI':27034,
    'PD':24487, 'Si':12330, 'CNYRUBF':875, 'USDRUBF':11186,
    'Eu':14478, 'SV':12960, 'IMOEXF':2596, 'GL':1352,
    'RN':3152, 'NG':8027, 'AL':728, 'MX':4133, 'CR':17200,
}

PATTERNS = {
    'vol_up_oi_up_yb_up':    lambda dv, dyb, dys, dfn, dtoi: dv > 0 and dtoi > 0 and dyb > 0,
    'smart_money':            lambda dv, dyb, dys, dfn, dtoi: dv > 0 and dyb > 0 and dfn < 0,
    'vol_up_oi_down':        lambda dv, dyb, dys, dfn, dtoi: dv > 0 and dtoi < 0,
    'vol_up_yb_down_fiz_up': lambda dv, dyb, dys, dfn, dtoi: dv > 0 and dyb < 0 and dfn > 0,
    'fiz_extreme_vol_up':    lambda dv, dyb, dys, dfn, dtoi: dv > 0 and abs(dfn) > 5,
}

# Ставки ЦБ РФ 2024-2026 (первые 5 дат — ориентир)
# Реальные: 2024-02-16 16%, 2024-03-22 16%, 2024-04-26 16%, 2024-06-07 16%
# 2024-07-26 18%, 2024-09-13 19%, 2024-10-25 21%, 2024-12-20 21%
# 2025-02-14 21%, 2025-03-21 20%, 2025-04-25 19%, 2025-06-13 18%
# 2025-07-25 18%, 2025-09-12 17%, 2025-10-24 17%, 2025-12-19 16%
CBR_DATES = [
    '2024-02-16','2024-03-22','2024-04-26','2024-06-07','2024-07-26',
    '2024-09-13','2024-10-25','2024-12-20',
    '2025-02-14','2025-03-21','2025-04-25','2025-06-13',
    '2025-07-25','2025-09-12','2025-10-24','2025-12-19',
    '2026-02-14','2026-03-21','2026-04-25',
]
CBR_EXCLUDE_WINDOW = 2  # days before and after

ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)

def is_cbr_week(d):
    """Check if date is within CBR decision window."""
    dt = datetime.strptime(d[:10], '%Y-%m-%d') if isinstance(d, str) else d
    for cbr in CBR_DATES:
        cdt = datetime.strptime(cbr, '%Y-%m-%d')
        diff = abs((dt - cdt).days)
        if diff <= CBR_EXCLUDE_WINDOW:
            return True
    return False

def load_daily_wfcbr(ticker):
    """Load daily data, return with CBR filter columns."""
    rows = ch.query("""
        SELECT toDate(p.time) as d,
               argMax(p.open, p.time) as open,
               argMax(p.high, p.time) as high,
               argMax(p.low, p.time) as low,
               argMax(p.close, p.time) as close,
               argMax(p.volume, p.time) as volume,
               argMax(o.yur_buy, p.time) as yur_buy,
               argMax(o.yur_sell, p.time) as yur_sell,
               argMax(o.fiz_buy, p.time) as fiz_buy,
               argMax(o.fiz_sell, p.time) as fiz_sell,
               argMax(o.total_oi, p.time) as total_oi
        FROM moex.prices_5m p
        INNER JOIN moex.prices_5m_oi o ON p.symbol = o.symbol AND p.time = o.time
        WHERE p.symbol = %(t)s AND p.time >= '2024-01-01' AND p.time <= '2026-05-01'
        GROUP BY d ORDER BY d
    """, parameters={'t': ticker}).result_rows
    if len(rows) < 60:
        return None
    a = np.array([list(r) for r in rows], dtype=object)
    dates = [str(r[0]) for r in rows]
    opn = a[:, 1].astype(float); high = a[:, 2].astype(float); low = a[:, 3].astype(float)
    close = a[:, 4].astype(float); vol = a[:, 5].astype(float)
    yb = a[:, 6].astype(float); ys = a[:, 7].astype(float)
    fb = a[:, 8].astype(float); fs = a[:, 9].astype(float); toi = a[:, 10].astype(float)
    toi = np.where(toi <= 0, 1, toi)
    v_m = np.mean(vol) + 1; yb_m = np.mean(yb) + 1; ys_m = np.mean(ys) + 1; toi_m = np.mean(toi) + 1
    dv = np.diff(vol) / v_m; dyb = np.diff(yb) / yb_m; dys = np.diff(ys) / ys_m; dtoi = np.diff(toi) / toi_m
    fiz_net = (fb - fs) / toi * 100; dfn = np.diff(fiz_net)
    
    # CBR filter per date
    cbr_filter = np.array([not is_cbr_week(d) for d in dates[:len(dates)-1]], dtype=bool)
    
    # ATR(14) for adaptive exit
    tr = np.zeros(len(close))
    tr[1:] = np.maximum(high[1:]-low[1:], np.maximum(abs(high[1:]-close[:-1]), abs(low[1:]-close[:-1])))
    atr = np.full(len(close), np.nan)
    if len(close) >= 15:
        atr_smooth = np.convolve(tr, np.ones(14)/14, mode='valid')[:len(close)]
        for i in range(14, len(close)):
            atr[i] = atr_smooth[i-14]
        if len(atr) > 14 and not np.isnan(atr[14]):
            for i in range(14, len(close)):
                if np.isnan(atr[i]):
                    atr[i] = np.nanmean(tr[max(0,i-14):i+1])
    
    # yur_buy direction signal
    yb_change = np.diff(yb)  # positive = yur increased buying
    
    # Signal-to-noise: dv magnitude
    dv_mag = np.abs(dv)
    
    sma50 = np.full(len(close), np.nan)
    if len(close) >= 50:
        cs = np.cumsum(close); sma50[49] = cs[49] / 50; sma50[50:] = (cs[50:] - cs[:-50]) / 50
    
    return dict(dates=dates, opn=opn, high=high, low=low, close=close, vol=vol,
                dv=dv, dyb=dyb, dys=dys, dfn=dfn, dtoi=dtoi, sma50=sma50,
                atr=atr, cbr_filter=cbr_filter, yb_change=yb_change, dv_mag=dv_mag,
                n=len(rows))


def backtest_signal(data, pfunc, hold, sl_pct, cs, ticker,
                    use_cbr_filter=True, use_atr_exit=False,
                    dv_threshold=0.0, yb_change_filter=False):
    """Backtest with optional CBR filter and ATR exit."""
    dates = data['dates']; opn = data['opn']; high = data['high']
    low = data['low']; close = data['close']
    dv = data['dv']; dyb = data['dyb']; dys = data['dys']
    dfn = data['dfn']; dtoi = data['dtoi']; sma50 = data['sma50']
    atr = data['atr']; cbr_f = data['cbr_filter']; yb_ch = data['yb_change']
    dv_mag = data['dv_mag']
    n = len(close)
    
    go_val = GO_MAP.get(ticker, 1000)
    
    eq = float(CAPITAL)
    peak = eq
    mdd = 0.0
    trades = []
    
    for i in range(max(50, 15), n - max(hold, 2)):
        if i >= len(dv) or i >= len(dfn) or i >= len(dtoi):
            break
        
        # Pattern filter
        if not pfunc(dv[i], dyb[i], dys[i], dfn[i], dtoi[i]):
            continue
        
        # dv threshold filter (TRIZ: quality filter)
        if dv_mag[i] < dv_threshold:
            continue
        
        # yur_buy direction filter
        if yb_change_filter and yb_ch[i] < 0:
            continue
        
        # CBR filter
        if use_cbr_filter and i < len(cbr_f) and not cbr_f[i]:
            continue
        
        # Trend filter
        if sma50 is not None and i < len(sma50) and not np.isnan(sma50[i]):
            if close[i] <= sma50[i]:
                continue
        
        ei = i + 1
        if ei >= n - 1:
            continue
        ep = float(opn[ei])
        
        # Adaptive hold via ATR
        if use_atr_exit and not np.isnan(atr[i]) and atr[i] > 0:
            atr_pct = atr[i] / close[i]
            # If vol low, extend hold; if vol high, shorten
            adj_hold = max(1, min(10, int(hold * (0.02 / max(atr_pct, 0.005)))))
        else:
            adj_hold = hold
        
        xi = min(ei + adj_hold, n - 1)
        sp = ep * (1 - sl_pct) if sl_pct > 0 else 0
        stop_hit = False
        xp = float(close[xi])
        if sl_pct > 0:
            for j in range(ei, xi + 1):
                if float(low[j]) <= sp:
                    xp = sp; stop_hit = True; break
        
        go = ep * cs
        if go <= 0:
            continue
        
        # Sizing
        risk_amount = eq * RISK_PCT
        if sl_pct > 0:
            base_nc = risk_amount / (go * sl_pct)
        else:
            base_nc = risk_amount / go * 5
        base_nc = max(1, int(base_nc))
        nc = min(base_nc, MAX_LOT)
        
        # GO-based cap
        max_by_go = int(eq * MAX_LEV / go_val) if go_val > 0 else 99
        nc = min(nc, max_by_go)
        # Also cap by contract GO
        max_by_contract_go = int(eq * MAX_LEV / go) if go > 0 else 99
        nc = min(nc, max_by_contract_go)
        
        if nc < 1:
            continue
        
        eq_before = eq
        gp = nc * cs * (xp - ep)
        cm_val = nc * COMM
        npnl = gp - cm_val
        eq += npnl
        if eq > peak: peak = eq
        dd = (peak - eq) / peak * 100 if peak > 0 else 0
        mdd = max(mdd, dd)
        
        trades.append(dict(entry=dates[ei], exit=dates[xi],
                           ep=round(ep,2), xp=round(xp,2),
                           nc=nc, go=round(go,0), go_val=go_val,
                           gp=round(gp,0), cm=round(cm_val,0),
                           npnl=round(npnl,0),
                           stop=stop_hit, adj_hold=adj_hold))
    
    if not trades:
        return None
    
    ret = (eq - CAPITAL) / CAPITAL * 100
    wins = sum(1 for t in trades if t['npnl'] > 0)
    wr = wins / len(trades) * 100 if trades else 0
    gp_sum = sum(t['npnl'] for t in trades if t['npnl'] > 0)
    gl_sum = sum(t['npnl'] for t in trades if t['npnl'] < 0)
    pf = abs(gp_sum / (gl_sum + 1))
    tr_comm = sum(t['cm'] for t in trades)
    
    return dict(ret=round(ret,2), mdd=round(mdd,2), wr=round(wr,1), pf=round(pf,2),
                trades=len(trades), wins=wins, net_pnl=round(gp_sum+gl_sum,0),
                comm=round(tr_comm,0), calmar=round(ret/mdd,2) if mdd > 0 else 0)


# Phase 1: Sweep with CBR filter
print("="*60)
print("PHASE 1: CBR Filter + dv_threshold sweep")
print("="*60)

t0 = time.time()
results = []
for ticker in TICKERS:
    cs = CS_MAP.get(ticker, 1)
    data = load_daily_wfcbr(ticker)
    if data is None:
        print(f"  {ticker}: no data")
        continue
    
    ticker_results = []
    for pname, pfunc in PATTERNS.items():
        for hold in [2, 3, 5]:
            for sl_pct in [0.01, 0.02]:
                for dv_thr in [0, 1.0, 2.0]:
                    r = backtest_signal(data, pfunc, hold, sl_pct, cs, ticker,
                                        use_cbr_filter=True, dv_threshold=dv_thr)
                    if r and r['trades'] >= 8:
                        ticker_results.append(dict(ticker=ticker, pattern=pname,
                            hold=hold, sl=sl_pct, dv_thr=dv_thr,
                            ret=r['ret'], mdd=r['mdd'], wr=r['wr'], pf=r['pf'],
                            trades=r['trades'], calmar=r['calmar'],
                            use_cbr=True, use_atr=False, yb_change=False))
        
        # YB change filter
        for hold in [2, 5]:
            for sl_pct in [0.01]:
                r = backtest_signal(data, pfunc, hold, sl_pct, cs, ticker,
                                    use_cbr_filter=True, yb_change_filter=True)
                if r and r['trades'] >= 8:
                    ticker_results.append(dict(ticker=ticker, pattern=pname,
                        hold=hold, sl=sl_pct, dv_thr=0,
                        ret=r['ret'], mdd=r['mdd'], wr=r['wr'], pf=r['pf'],
                        trades=r['trades'], calmar=r['calmar'],
                        use_cbr=True, use_atr=False, yb_change=True))
        
        # ATR adaptive exit
        for base_hold in [3, 5]:
            for sl_pct in [0.01]:
                r = backtest_signal(data, pfunc, base_hold, sl_pct, cs, ticker,
                                    use_cbr_filter=True, use_atr_exit=True)
                if r and r['trades'] >= 8:
                    ticker_results.append(dict(ticker=ticker, pattern=pname,
                        hold=base_hold, sl=sl_pct, dv_thr=0,
                        ret=r['ret'], mdd=r['mdd'], wr=r['wr'], pf=r['pf'],
                        trades=r['trades'], calmar=r['calmar'],
                        use_cbr=True, use_atr=True, yb_change=False))
    
    results.extend(ticker_results)
    best = sorted(ticker_results, key=lambda x: -x['calmar'])
    if best:
        print(f"  {ticker}: {len(ticker_results)} combos, best Calmar={best[0]['calmar']:.2f} ret={best[0]['ret']:+.1f}% {best[0]['pattern']} hold={best[0]['hold']}")
    else:
        print(f"  {ticker}: 0 working combos")

# Filter diamonds
diamonds = [r for r in results if r['calmar'] >= 3.0 and r['ret'] >= 20 and r['trades'] >= 10]
diamonds.sort(key=lambda x: -x['calmar'])

print(f"\n{'='*60}")
print(f"DIAMONDS FOUND: {len(diamonds)}")
print(f"{'='*60}")
print(f"{'Ticker':>8} {'Pattern':>22} {'H':>2} {'SL':>5} {'CBR':>4} {'ATR':>4} {'YB':>3} {'Ret':>8} {'DD':>6} {'WR':>5} {'PF':>6} {'Calmar':>7} {'Tr':>4}")
print("-"*100)
for r in diamonds[:30]:
    pname = r['pattern'][:22]
    print(f"{r['ticker']:>8} {pname:>22} {r['hold']:>2} {r['sl']:.0%} "
          f"{'Y' if r['use_cbr'] else 'N':>4} {'Y' if r['use_atr'] else 'N':>4} {'Y' if r['yb_change'] else 'N':>3} "
          f"{r['ret']:>+7.1f}% {r['mdd']:>5.1f}% {r['wr']:>4.1f}% {r['pf']:>6.2f} {r['calmar']:>6.1f} {r['trades']:>4d}")

# Save
os.makedirs(OUTPUT_DIR, exist_ok=True)
with open(f'{OUTPUT_DIR}/diamonds.json', 'w') as f:
    json.dump(diamonds, f, indent=2)
with open(f'{OUTPUT_DIR}/full_results.json', 'w') as f:
    json.dump(results, f, indent=2)

print(f"\nSaved to {OUTPUT_DIR}/")
print(f"Total time: {time.time()-t0:.0f}s")
print(f"Total combos: {len(results)}, Diamonds: {len(diamonds)}")
