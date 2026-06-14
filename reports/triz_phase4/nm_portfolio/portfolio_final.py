"""Final portfolio: NM, VB, SR, Eu with confirmed both-direction patterns."""
import sys, os
sys.path.insert(0, '/home/user/projects/TQA-MOEX')
os.chdir('/home/user/projects/TQA-MOEX')
import numpy as np
import clickhouse_connect
from config import CH_HOST, CH_PORT, CH_DB

ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)

TICKERS = ['NM', 'VB', 'SR', 'Eu']
CAPITAL = 200_000
COMM = 4
MAX_LOT = 5
RISK_PCT = 0.02
MAX_LEV = 3.0

PATTERNS = {
    'vol_up_oi_up_yb_up': lambda dv,dyb,dys,dfn,dtoi: dv>0 and dtoi>0 and dyb>0,
    'smart_money': lambda dv,dyb,dys,dfn,dtoi: dv>0 and dyb>0 and dfn<0,
    'vol_up_oi_down': lambda dv,dyb,dys,dfn,dtoi: dv>0 and dtoi<0,
    'vol_up_yb_down_fiz_up': lambda dv,dyb,dys,dfn,dtoi: dv>0 and dyb<0 and dfn>0,
    'fiz_extreme_vol_up': lambda dv,dyb,dys,dfn,dtoi: dv>0 and abs(dfn)>5,
}

CS_MAP = {'NM':10, 'VB':100, 'SR':100, 'Eu':1000}

# Load data
ticker_data = {}
for ticker in TICKERS:
    rows = ch.query("""
        SELECT toDate(p.time) as d, argMax(p.open,p.time), argMax(p.high,p.time),
               argMax(p.low,p.time), argMax(p.close,p.time), argMax(p.volume,p.time),
               argMax(o.yur_buy,p.time), argMax(o.yur_sell,p.time),
               argMax(o.fiz_buy,p.time), argMax(o.fiz_sell,p.time),
               argMax(o.total_oi,p.time)
        FROM moex.prices_5m p INNER JOIN moex.prices_5m_oi o ON p.symbol=o.symbol AND p.time=o.time
        WHERE p.symbol=%(t)s AND p.time>='2024-01-01' AND p.time<='2026-05-01'
        GROUP BY d ORDER BY d
    """, parameters={'t': ticker}).result_rows
    if len(rows) < 60: continue
    
    a = np.array([list(r) for r in rows], dtype=object)
    dates = [str(r[0]) for r in rows]
    opn=a[:,1].astype(float); high=a[:,2].astype(float); low=a[:,3].astype(float)
    close=a[:,4].astype(float); vol=a[:,5].astype(float)
    yb=a[:,6].astype(float); ys=a[:,7].astype(float)
    fb=a[:,8].astype(float); fs=a[:,9].astype(float); toi=a[:,10].astype(float)
    toi=np.where(toi<=0,1,toi)
    
    tr=np.zeros(len(close))
    tr[1:]=np.maximum(high[1:]-low[1:],np.maximum(abs(high[1:]-close[:-1]),abs(low[1:]-close[:-1])))
    atr=np.full(len(close),np.nan)
    for i in range(14,len(close)): atr[i]=np.mean(tr[i-13:i+1])
    
    v_m=np.mean(vol)+1; yb_m=np.mean(yb)+1; ys_m=np.mean(ys)+1; toi_m=np.mean(toi)+1
    dv=np.diff(vol)/v_m; dyb=np.diff(yb)/yb_m; dys=np.diff(ys)/ys_m; dtoi=np.diff(toi)/toi_m
    fiz_net=(fb-fs)/toi*100; dfn=np.diff(fiz_net)
    
    ticker_data[ticker] = dict(dates=dates, opn=opn, high=high, low=low, close=close, vol=vol,
                                dv=dv, dyb=dyb, dys=dys, dfn=dfn, dtoi=dtoi,
                                atr=atr, n=len(dates))
    print(f'{ticker}: {len(dates)} bars')

def run_portfolio(configs):
    """configs: [(ticker, direction, pname, hold, atr_mult), ...]"""
    # Pre-compute all trade entries
    all_entries = []
    
    for ticker, direction, pname, hold, atr_mult in configs:
        d = ticker_data.get(ticker)
        if not d: continue
        cs = CS_MAP.get(ticker, 1)
        pfunc = PATTERNS.get(pname)
        
        for i in range(50, d['n'] - hold - 1):
            if i >= len(d['dv']): break
            ep = float(d['opn'][i+1])
            
            if pfunc and not pfunc(d['dv'][i], d['dyb'][i], d['dys'][i], d['dfn'][i], d['dtoi'][i]):
                continue
            if d['vol'][i] < np.mean(d['vol'][:i]) * 1.2:
                continue
            
            if direction == 'L':
                sp = ep * (1 - min(max(d['atr'][i]/ep*atr_mult, 0.005), 0.05)) if not np.isnan(d['atr'][i]) else ep*0.95
                r_h = ep
                for j in range(i+1, min(i+hold+1, d['n'])):
                    bh = float(d['high'][j])
                    if bh > r_h:
                        r_h = bh
                        if not np.isnan(d['atr'][j]):
                            sp = max(sp, r_h*(1-min(max(d['atr'][j]/r_h*atr_mult,0.005),0.05)))
                    if float(d['low'][j]) <= sp:
                        all_entries.append((i+1, j, ticker, 'L', ep, sp, cs))
                        break
                else:
                    all_entries.append((i+1, min(i+hold, d['n']-1), ticker, 'L', ep, float(d['close'][min(i+hold, d['n']-1)]), cs))
            else:
                sp = ep * (1 + min(max(d['atr'][i]/ep*atr_mult, 0.005), 0.05)) if not np.isnan(d['atr'][i]) else ep*1.05
                r_l = ep
                for j in range(i+1, min(i+hold+1, d['n'])):
                    bl = float(d['low'][j])
                    if bl < r_l:
                        r_l = bl
                        if not np.isnan(d['atr'][j]):
                            sp = min(sp, r_l*(1+min(max(d['atr'][j]/r_l*atr_mult,0.005),0.05)))
                    if float(d['high'][j]) >= sp:
                        all_entries.append((i+1, j, ticker, 'S', ep, sp, cs))
                        break
                else:
                    all_entries.append((i+1, min(i+hold, d['n']-1), ticker, 'S', ep, float(d['close'][min(i+hold, d['n']-1)]), cs))
    
    all_entries.sort(key=lambda x: x[0])
    
    eq = float(CAPITAL); peak = eq; mdd = 0.0
    trades_log = []; n_signals = max(len(configs), 1)
    
    for entry_idx, exit_idx, ticker, direction, ep, xp, cs in all_entries:
        go_val = ep * cs
        if go_val <= 0: continue
        
        sig_eq = eq / n_signals
        risk_amount = sig_eq * RISK_PCT
        base_nc = risk_amount / (go_val * 0.005)
        base_nc = max(1, int(base_nc))
        nc = min(base_nc, MAX_LOT)
        max_by_go = int(eq * MAX_LEV / go_val) if go_val > 0 else 99
        nc = min(nc, max_by_go)
        if nc < 1: continue
        
        pnl = nc * cs * (xp - ep) - nc * COMM if direction == 'L' else nc * cs * (ep - xp) - nc * COMM
        eq += pnl
        if eq > peak: peak = eq
        dd = (peak - eq) / peak * 100 if peak > 0 else 0
        mdd = max(mdd, dd)
        trades_log.append(pnl)
    
    if not trades_log: return None
    ret = (eq - CAPITAL) / CAPITAL * 100
    wins = sum(1 for p in trades_log if p > 0)
    gp = sum(p for p in trades_log if p > 0)
    gl = sum(p for p in trades_log if p < 0)
    calmar = round(ret/mdd,2) if mdd>0 else 0
    return dict(ret=round(ret,2), mdd=round(mdd,2), wr=round(wins/len(trades_log)*100,1),
                pf=round(abs(gp/(gl+0.001)),2), trades=len(trades_log), calmar=calmar)

# Grid search best configs for each ticker
best_configs = []
print(f"\n{'='*90}")
print(f"Best combo per ticker+dir (Calmar>5, trades>=5)")
print(f"{'='*90}")
print(f"{'Ticker':>6} {'Dir':>4} {'Pat':>22} {'H':>3} {'AM':>3} {'Ret':>8} {'DD':>6} {'Calmar':>7} {'WR':>5} {'Tr':>4}")
print("-"*75)

for ticker in TICKERS:
    for direction in ['L', 'S']:
        best_cal = 0
        best_cfg = None
        for pname in PATTERNS:
            for hold in [5, 8, 13, 21]:
                for am in [2, 3, 5]:
                    r = run_portfolio([(ticker, direction, pname, hold, am)])
                    if r and r['trades'] >= 5 and r['calmar'] > 0:
                        # Both long and short must work
                        other_dir = 'S' if direction == 'L' else 'L'
                        r_other = run_portfolio([(ticker, other_dir, pname, hold, am)])
                        if r_other and r_other['trades'] >= 5 and r_other['ret'] > 0:
                            print(f"{ticker:>6} {direction:>4} {pname:>22} {hold:>3} {am:>3} "
                                  f"{r['ret']:>+7.1f}% {r['mdd']:>5.1f}% {r['calmar']:>6.1f} "
                                  f"{r['wr']:>4.0f}% {r['trades']:>4d}")
