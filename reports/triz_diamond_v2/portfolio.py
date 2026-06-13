#!/usr/bin/env python3
"""Portfolio sim for top diamonds: RI + USDRUBF + GL + check for correlated signals."""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/../..')
os.chdir(os.path.dirname(os.path.abspath(__file__)) + '/../..')

import numpy as np
import clickhouse_connect
from config import CH_HOST, CH_PORT, CH_DB

COMM = 4
RISK_PCT = 0.02
MAX_LOT = 5
MAX_LEV = 5.0

PATTERNS = {
    'vol_up_oi_up_yb_up':    lambda dv, dyb, dys, dfn, dtoi: dv > 0 and dtoi > 0 and dyb > 0,
    'smart_money':            lambda dv, dyb, dys, dfn, dtoi: dv > 0 and dyb > 0 and dfn < 0,
    'vol_up_oi_down':        lambda dv, dyb, dys, dfn, dtoi: dv > 0 and dtoi < 0,
    'vol_up_yb_down_fiz_up': lambda dv, dyb, dys, dfn, dtoi: dv > 0 and dyb < 0 and dfn > 0,
    'fiz_extreme_vol_up':    lambda dv, dyb, dys, dfn, dtoi: dv > 0 and abs(dfn) > 5,
}

GO_MAP = {'RI':27034, 'USDRUBF':11186, 'GL':1352, 'VB':1556, 'AF':673, 'CC':506, 'PD':24487, 'PT':31749, 'NM':256, 'GD':32003, 'BR':17228, 'SR':6620, 'SV':12960, 'IMOEXF':2596, 'LK':11606, 'Si':12330, 'CNYRUBF':875, 'Eu':14478, 'RN':3152}

TOP_SIGNALS = [
    # (ticker, pattern, hold, sl, use_cbr, use_atr, yb_ch, dv_thr)
    ('RI', 'vol_up_oi_up_yb_up', 5, 0.01, True, True, False, 0),
    ('USDRUBF', 'vol_up_yb_down_fiz_up', 5, 0.01, True, True, False, 0),
    ('GL', 'vol_up_oi_up_yb_up', 5, 0.01, True, True, False, 0),
    ('VB', 'smart_money', 5, 0.02, True, False, False, 0),
    ('AF', 'vol_up_oi_up_yb_up', 5, 0.01, True, True, False, 0),
    ('CC', 'vol_up_oi_down', 3, 0.01, True, True, False, 0),
]

# Also try: non-overlapping best diamonds
ALT_PORTFOLIO = [
    ('RI', 'vol_up_oi_up_yb_up', 5, 0.01, True, True, False, 0),
    ('USDRUBF', 'vol_up_yb_down_fiz_up', 5, 0.01, True, True, False, 0),
    ('GL', 'vol_up_oi_up_yb_up', 5, 0.01, True, True, False, 0),
    ('NM', 'vol_up_oi_up_yb_up', 5, 0.01, True, True, False, 0),
    ('PD', 'vol_up_oi_down', 5, 0.01, True, True, False, 2.0),
]

ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)

CBR_DATES = [
    '2024-02-16','2024-03-22','2024-04-26','2024-06-07','2024-07-26',
    '2024-09-13','2024-10-25','2024-12-20',
    '2025-02-14','2025-03-21','2025-04-25','2025-06-13',
    '2025-07-25','2025-09-12','2025-10-24','2025-12-19',
    '2026-02-14','2026-03-21','2026-04-25',
]

def is_cbr_week(d):
    dt = __import__('datetime').datetime.strptime(d[:10], '%Y-%m-%d')
    for cbr in CBR_DATES:
        cdt = __import__('datetime').datetime.strptime(cbr, '%Y-%m-%d')
        if abs((dt - cdt).days) <= 2:
            return True
    return False

def load_daily(ticker):
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
    
    tr = np.zeros(len(close))
    tr[1:] = np.maximum(high[1:]-low[1:], np.maximum(abs(high[1:]-close[:-1]), abs(low[1:]-close[:-1])))
    atr = np.full(len(close), np.nan)
    if len(close) >= 15:
        atr_smooth = np.convolve(tr, np.ones(14)/14, mode='valid')[:len(close)]
        for i in range(14, len(close)):
            atr[i] = atr_smooth[i-14]
    
    yb_change = np.diff(yb)
    dv_mag = np.abs(dv)
    
    sma50 = np.full(len(close), np.nan)
    if len(close) >= 50:
        cs_ = np.cumsum(close); sma50[49] = cs_[49] / 50; sma50[50:] = (cs_[50:] - cs_[:-50]) / 50
    
    return dict(dates=dates, opn=opn, high=high, low=low, close=close, vol=vol,
                dv=dv, dyb=dyb, dys=dys, dfn=dfn, dtoi=dtoi, sma50=sma50,
                atr=atr, cbr_filter=np.array([not is_cbr_week(d) for d in dates]), 
                yb_change=yb_change, dv_mag=dv_mag, n=len(rows))


def run_strategy(sig, data, capital):
    ticker, pname, hold, sl_pct, use_cbr, use_atr, yb_ch_f, dv_thr = sig
    pfunc = PATTERNS[pname]
    dates = data['dates']; opn = data['opn']; high = data['high']
    low = data['low']; close = data['close']
    dv = data['dv']; dyb = data['dyb']; dys = data['dys']
    dfn = data['dfn']; dtoi = data['dtoi']; sma50 = data['sma50']
    atr = data['atr']; cbr_f = data['cbr_filter']
    yb_ch = data['yb_change']; dv_mag = data['dv_mag']
    n = len(close)
    go_val = GO_MAP.get(ticker, 1000)
    cs = {'RI':1, 'USDRUBF':1000, 'GL':1, 'VB':100, 'AF':1, 'CC':10, 'NM':10, 'PD':1}.get(ticker, 1)
    
    eq = float(capital)
    peak = eq
    trades = []
    
    for i in range(max(50, 15), n - max(hold, 2)):
        if i >= len(dv) or i >= len(dfn) or i >= len(dtoi): break
        if not pfunc(dv[i], dyb[i], dys[i], dfn[i], dtoi[i]): continue
        if dv_mag[i] < dv_thr: continue
        if yb_ch_f and yb_ch[i] < 0: continue
        if use_cbr and i < len(cbr_f) and not cbr_f[i]: continue
        if sma50 is not None and i < len(sma50) and not np.isnan(sma50[i]):
            if close[i] <= sma50[i]: continue
        
        ei = i + 1
        if ei >= n - 1: continue
        ep = float(opn[ei])
        
        if use_atr and not np.isnan(atr[i]) and atr[i] > 0:
            atr_pct = atr[i] / close[i]
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
        if go <= 0: continue
        
        risk_amount = eq * RISK_PCT
        if sl_pct > 0:
            base_nc = risk_amount / (go * sl_pct)
        else:
            base_nc = risk_amount / go * 5
        base_nc = max(1, int(base_nc))
        nc = min(base_nc, MAX_LOT)
        max_by_go = int(eq * MAX_LEV / go) if go > 0 else 99
        nc = min(nc, max_by_go)
        if nc < 1: continue
        
        eq_before = eq
        gp = nc * cs * (xp - ep)
        cm_val = nc * COMM
        npnl = gp - cm_val
        eq += npnl
        if eq > peak: peak = eq
        dd = (peak - eq) / peak * 100 if peak > 0 else 0
        
        trades.append(dict(ticker=ticker, pattern=pname,
            entry=dates[ei], exit=dates[xi], ep=round(ep,2), xp=round(xp,2),
            nc=nc, npnl=round(npnl,0), stop=stop_hit))
    
    return trades


# ── Run portfolios ──────────────────────
for capital in [200_000, 100_000]:
    for pf_name, signals in [('top6', TOP_SIGNALS), ('alt5', ALT_PORTFOLIO)]:
        print(f"\n{'='*60}")
        print(f"PORTFOLIO: {pf_name}, CAPITAL={capital:,}")
        print(f"{'='*60}")
        
        sig_cap = capital / len(signals)
        all_trades = []
        
        for sig in signals:
            ticker = sig[0]
            data = load_daily(ticker)
            if data is None:
                print(f"  {ticker}: no data")
                continue
            trades = run_strategy(sig, data, sig_cap)
            ret = sum(t['npnl'] for t in trades) / sig_cap * 100 if trades else 0
            wins = sum(1 for t in trades if t['npnl'] > 0)
            wr = wins / len(trades) * 100 if trades else 0
            print(f"  {ticker:>8}: {len(trades):>3d}tr ret={ret:>+6.1f}% WR={wr:>4.1f}%")
            all_trades.extend(trades)
        
        if not all_trades:
            continue
        
        all_trades.sort(key=lambda t: t['entry'])
        equity = capital
        peak = equity
        mdd = 0.0
        
        for t in all_trades:
            equity += t['npnl']
            if equity > peak: peak = equity
            dd = (peak - equity) / peak * 100 if peak > 0 else 0
            mdd = max(mdd, dd)
        
        total_ret = (equity - capital) / capital * 100
        wins_total = sum(1 for t in all_trades if t['npnl'] > 0)
        wr_total = wins_total / len(all_trades) * 100 if all_trades else 0
        gp_sum = sum(t['npnl'] for t in all_trades if t['npnl'] > 0)
        gl_sum = sum(t['npnl'] for t in all_trades if t['npnl'] < 0)
        pf_total = abs(gp_sum / (gl_sum + 1))
        calmar = total_ret / mdd if mdd > 0 else 0
        
        print(f"\n  TOTAL: ret={total_ret:+.1f}% DD={mdd:.1f}% Calmar={calmar:.1f} WR={wr_total:.0f}% PF={pf_total:.2f}")
        
        # Monthly
        from collections import defaultdict
        by_month = defaultdict(lambda: {'trades': 0, 'pnl': 0})
        for t in all_trades:
            by_month[t['entry'][:7]]['trades'] += 1
            by_month[t['entry'][:7]]['pnl'] += t['npnl']
        
        # Correlation check: how many trades overlap by month
        months_with_trades = sum(1 for v in by_month.values() if v['trades'] > 0)
        print(f"  Active months: {months_with_trades}")
        
        # Worst 3 months
        sorted_months = sorted(by_month.items(), key=lambda x: x[1]['pnl'])
        print("  Worst months:")
        for ym, v in sorted_months[:3]:
            print(f"    {ym}: {v['trades']}tr PnL={v['pnl']:>+8,.0f}")
        
        # Check correlation between RI and USDRUBF trade dates
        try:
            ri_trades = [t for t in all_trades if t['ticker'] == 'RI']
            usd_trades = [t for t in all_trades if t['ticker'] == 'USDRUBF']
            ri_dates = set(t['entry'][:10] for t in ri_trades)
            usd_dates = set(t['entry'][:10] for t in usd_trades)
            overlap = ri_dates & usd_dates
            if len(overlap) > 0:
                print(f"  ⚠ RI-USDRUBF overlap: {len(overlap)}/{len(ri_dates)+len(usd_dates)} days ({len(overlap)/(len(ri_dates)+len(usd_dates)-len(overlap))*100:.0f}% of union)")
            else:
                print(f"  ✅ RI-USDRUBF: zero overlap (independent)")
        except:
            pass

print("\nDone.")
