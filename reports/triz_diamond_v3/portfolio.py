#!/usr/bin/env python3
"""Portfolio sim for stacked-confirmed diamonds."""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/../..')
os.chdir(os.path.dirname(os.path.abspath(__file__)) + '/../..')

import numpy as np
import clickhouse_connect
from config import CH_HOST, CH_PORT, CH_DB
from collections import defaultdict

COMM = 4; RISK_PCT = 0.02; MAX_LOT = 5; MAX_LEV = 5.0

GO_MAP = {'RI':27034, 'GL':1352, 'USDRUBF':11186, 'AF':673, 'BR':17228, 'IMOEXF':2596, 'CC':506, 'NM':256, 'PD':24487}
CS_MAP = {'RI':1, 'GL':1, 'USDRUBF':1000, 'AF':1, 'BR':10, 'IMOEXF':10, 'CC':10, 'NM':10, 'PD':1}

PATTERNS = {
    'vol_up_oi_up_yb_up':    lambda dv, dyb, dys, dfn, dtoi: dv > 0 and dtoi > 0 and dyb > 0,
    'smart_money':            lambda dv, dyb, dys, dfn, dtoi: dv > 0 and dyb > 0 and dfn < 0,
    'vol_up_oi_down':        lambda dv, dyb, dys, dfn, dtoi: dv > 0 and dtoi < 0,
    'vol_up_yb_down_fiz_up': lambda dv, dyb, dys, dfn, dtoi: dv > 0 and dyb < 0 and dfn > 0,
    'fiz_extreme_vol_up':    lambda dv, dyb, dys, dfn, dtoi: dv > 0 and abs(dfn) > 5,
}

# Top stacked-confirmed diamonds
TOP_SIGNALS = [
    ('RI', 'vol_up_oi_up_yb_up', 3, 0.01, 2.0),    # Calmar 21.9, WR 67%
    ('AF', 'vol_up_oi_up_yb_up', 2, 0.01, 2.0),    # Calmar 32.9, WR 88%
    ('AF', 'vol_up_oi_down', 5, 0.01, 2.0),         # Calmar 17.3, WR 64%
    ('BR', 'vol_up_yb_down_fiz_up', 5, 0.01, 1.5),  # Calmar 23.9, WR 88%
    ('IMOEXF', 'vol_up_oi_up_yb_up', 5, 0.01, 2.0), # Calmar 486, WR 83% — редкие сделки
    ('USDRUBF', 'vol_up_oi_up_yb_up', 5, 0.01, 1.5),# Calmar 11.5, WR 60%
    ('GL', 'vol_up_yb_down_fiz_up', 5, 0.01, 1.5),  # Calmar 11.2, WR 67%
    ('CC', 'vol_up_oi_up_yb_up', 5, 0.01, 1.5),     # Calmar 11.0, WR 67%
]

# Non-overlapping subset (for capital efficiency)
ALT_PF = [
    ('RI', 'vol_up_oi_up_yb_up', 3, 0.01, 2.0),
    ('AF', 'vol_up_oi_up_yb_up', 2, 0.01, 2.0),
    ('BR', 'vol_up_yb_down_fiz_up', 5, 0.01, 1.5),
    ('USDRUBF', 'vol_up_oi_up_yb_up', 5, 0.01, 1.5),
    ('GL', 'vol_up_yb_down_fiz_up', 5, 0.01, 1.5),
]

ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)

CBR_DATES = [
    '2024-02-16','2024-03-22','2024-04-26','2024-06-07','2024-07-26',
    '2024-09-13','2024-10-25','2024-12-20','2025-02-14','2025-03-21',
    '2025-04-25','2025-06-13','2025-07-25','2025-09-12','2025-10-24',
    '2025-12-19','2026-02-14','2026-03-21','2026-04-25',
]

def is_cbr(d):
    dt = __import__('datetime').datetime.strptime(d[:10], '%Y-%m-%d')
    for c in CBR_DATES:
        cdt = __import__('datetime').datetime.strptime(c, '%Y-%m-%d')
        if abs((dt - cdt).days) <= 2: return True
    return False

def load_with_5m(ticker):
    """Load daily + 5m fiz_z confirmation."""
    d_rows = ch.query("""
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
    if len(d_rows) < 60: return None
    
    m5_rows = ch.query("""
        SELECT p.time, p.volume, o.yur_buy, o.yur_sell, o.fiz_buy, o.fiz_sell, o.total_oi
        FROM moex.prices_5m_oi o 
        INNER JOIN moex.prices_5m p ON p.symbol = o.symbol AND p.time = o.time
        WHERE p.symbol = %(t)s AND p.time >= '2024-01-01' AND p.time <= '2026-05-01'
        ORDER BY p.time
    """, parameters={'t': ticker}).result_rows
    if len(m5_rows) < 200: return None
    
    # Daily features
    a = np.array([list(r) for r in d_rows], dtype=object)
    d_dates = [str(r[0]) for r in d_rows]
    d_opn = a[:, 1].astype(float); d_high = a[:, 2].astype(float)
    d_low = a[:, 3].astype(float); d_close = a[:, 4].astype(float)
    d_vol = a[:, 5].astype(float)
    d_yb = a[:, 6].astype(float); d_ys = a[:, 7].astype(float)
    d_fb = a[:, 8].astype(float); d_fs = a[:, 9].astype(float); d_toi = a[:, 10].astype(float)
    d_toi = np.where(d_toi <= 0, 1, d_toi)
    v_m = np.mean(d_vol)+1; yb_m = np.mean(d_yb)+1; ys_m = np.mean(d_ys)+1; toi_m = np.mean(d_toi)+1
    dv = np.diff(d_vol)/v_m; dyb = np.diff(d_yb)/yb_m; dys = np.diff(d_ys)/ys_m
    dtoi = np.diff(d_toi)/toi_m
    fiz_net = (d_fb-d_fs)/d_toi*100; dfn = np.diff(fiz_net)
    sma50 = np.full(len(d_close), np.nan)
    if len(d_close) >= 50:
        cs = np.cumsum(d_close); sma50[49]=cs[49]/50; sma50[50:]=(cs[50:]-cs[:-50])/50
    
    # 5m fiz_z
    m5_toi = np.array([float(r[6]) for r in m5_rows])
    m5_fb = np.array([float(r[4]) for r in m5_rows])
    m5_fs = np.array([float(r[5]) for r in m5_rows])
    m5_vol = np.array([float(r[1]) for r in m5_rows])
    m5_toi = np.where(m5_toi<=0, 1, m5_toi)
    m5_fiz_net = (m5_fb-m5_fs)/m5_toi*100
    
    m5_fiz_z = np.zeros(len(m5_fiz_net))
    for i in range(20, len(m5_fiz_net)):
        s = m5_fiz_net[i-20:i]
        mu = np.mean(s); sd = np.std(s)+0.001
        m5_fiz_z[i] = (m5_fiz_net[i]-mu)/sd
    
    # Map daily dates -> 5m fiz_z (last 3 bars)
    m5_times = [r[0] for r in m5_rows]
    daily_5m = defaultdict(list)
    for i in range(len(m5_rows)):
        ds = m5_times[i].strftime('%Y-%m-%d')
        daily_5m[ds].append(m5_fiz_z[i])
    
    daily_fiz_z = {}
    for ds, zs in daily_5m.items():
        last3 = zs[-3:] if len(zs)>=3 else zs
        daily_fiz_z[ds] = np.mean(last3) if last3 else 0
    
    return dict(dates=d_dates, opn=d_opn, high=d_high, low=d_low, close=d_close,
                dv=dv, dyb=dyb, dys=dys, dfn=dfn, dtoi=dtoi, sma50=sma50, n=len(d_rows),
                daily_fiz_z=daily_fiz_z)


def run_strategy(sig, data, capital):
    ticker, pname, hold, sl_pct, fiz_thr = sig
    pfunc = PATTERNS[pname]
    dates = data['dates']; opn = data['opn']; high = data['high']
    low = data['low']; close = data['close']
    dv = data['dv']; dyb = data['dyb']; dys = data['dys']
    dfn = data['dfn']; dtoi = data['dtoi']; sma50 = data['sma50']
    fiz_z = data['daily_fiz_z']
    n = len(close)
    go_val = GO_MAP.get(ticker, 1000)
    cs = CS_MAP.get(ticker, 1)
    
    eq = float(capital); peak = eq; trades = []
    
    for i in range(50, n - max(hold, 2)):
        if i >= len(dv) or i >= len(dfn): break
        if not pfunc(dv[i], dyb[i], dys[i], dfn[i], dtoi[i]): continue
        if is_cbr(dates[i]): continue
        
        # Stacked fiz_z filter
        fz = fiz_z.get(dates[i], 0)
        if fz < fiz_thr: continue
        
        if sma50 is not None and i < len(sma50) and not np.isnan(sma50[i]):
            if close[i] <= sma50[i]: continue
        
        ei = i + 1
        if ei >= n - 1: continue
        ep = float(opn[ei])
        xi = min(ei + hold, n - 1)
        sp = ep * (1 - sl_pct) if sl_pct > 0 else 0
        stop_hit = False
        xp = float(close[xi])
        if sl_pct > 0:
            for j in range(ei, xi+1):
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
        
        gp = nc * cs * (xp - ep)
        npnl = gp - nc * COMM
        eq += npnl
        if eq > peak: peak = eq
        dd = (peak - eq) / peak * 100 if peak > 0 else 0
        
        trades.append(dict(ticker=ticker, entry=dates[ei], exit=dates[xi],
            ep=round(ep,2), npnl=round(npnl,0), nc=nc))
    
    return trades


# ── Run ─────────────────────────────
for capital in [200_000, 100_000]:
    for pf_name, signals in [('top8', TOP_SIGNALS), ('alt5', ALT_PF)]:
        print(f"\n{'='*60}")
        print(f"PORTFOLIO: {pf_name}, CAPITAL={capital:,}")
        print(f"{'='*60}")
        
        sig_cap = capital / len(signals)
        all_trades = []
        
        for sig in signals:
            ticker = sig[0]
            data = load_with_5m(ticker)
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
            print("  — нет сделок")
            continue
        
        all_trades.sort(key=lambda t: t['entry'])
        equity = capital; peak = equity; mdd = 0.0
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
        
        from datetime import datetime
        days = (datetime.strptime(max(t['exit'] for t in all_trades), '%Y-%m-%d') - 
                datetime.strptime(min(t['entry'] for t in all_trades), '%Y-%m-%d')).days
        ann = ((1+total_ret/100)**(365/max(days,1))-1)*100 if days>0 else 0
        
        print(f"\n  TOTAL: ret={total_ret:+.1f}% DD={mdd:.1f}% Calmar={calmar:.1f} WR={wr_total:.0f}% PF={pf_total:.2f}")
        print(f"  Annualized: {ann:+.1f}%  ({days} days, {len(all_trades)} trades)")
        
        # By ticker
        by_t = defaultdict(lambda: {'t':0, 'w':0, 'p':0})
        for t in all_trades:
            by_t[t['ticker']]['t'] += 1
            by_t[t['ticker']]['w'] += 1 if t['npnl']>0 else 0
            by_t[t['ticker']]['p'] += t['npnl']
        for tk, v in sorted(by_t.items(), key=lambda x: -x[1]['p']):
            print(f"    {tk}: {v['t']}tr WR={v['w']/v['t']*100:.0f}% PnL={v['p']:>+8,.0f}")
        
        # Worst months
        by_m = defaultdict(lambda: {'t':0, 'p':0})
        for t in all_trades:
            by_m[t['entry'][:7]]['t'] += 1
            by_m[t['entry'][:7]]['p'] += t['npnl']
        worst = sorted(by_m.items(), key=lambda x: x[1]['p'])[:3]
        print(f"  Worst months:")
        for ym, v in worst:
            print(f"    {ym}: {v['t']}tr PnL={v['p']:>+8,.0f}")

print("\nDone.")
