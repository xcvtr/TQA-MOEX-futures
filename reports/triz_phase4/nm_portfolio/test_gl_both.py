"""Test GL chandelier long+short vs long-only."""
import sys, os, json
sys.path.insert(0, '/home/user/projects/TQA-MOEX')
os.chdir('/home/user/projects/TQA-MOEX')
import numpy as np
import clickhouse_connect
from config import CH_HOST, CH_PORT, CH_DB

ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)

# GL data
rows = ch.query("""
    SELECT toDate(p.time) as d,
           argMax(p.open,p.time), argMax(p.high,p.time), argMax(p.low,p.time),
           argMax(p.close,p.time), argMax(p.volume,p.time),
           argMax(o.yur_buy,p.time), argMax(o.yur_sell,p.time),
           argMax(o.fiz_buy,p.time), argMax(o.fiz_sell,p.time),
           argMax(o.total_oi,p.time)
    FROM moex.prices_5m p INNER JOIN moex.prices_5m_oi o ON p.symbol=o.symbol AND p.time=o.time
    WHERE p.symbol='GL' AND p.time>='2024-01-01' AND p.time<='2026-05-01'
    GROUP BY d ORDER BY d
""").result_rows

a = np.array([list(r) for r in rows], dtype=object)
dates = [str(r[0]) for r in rows]
opn=a[:,1].astype(float); high=a[:,2].astype(float); low=a[:,3].astype(float)
close=a[:,4].astype(float); vol=a[:,5].astype(float)
yb=a[:,6].astype(float); ys=a[:,7].astype(float)
fb=a[:,8].astype(float); fs=a[:,9].astype(float); toi=a[:,10].astype(float)

# ATR(14)
tr = np.zeros(len(close))
tr[1:] = np.maximum(high[1:]-low[1:], np.maximum(abs(high[1:]-close[:-1]), abs(low[1:]-close[:-1])))
atr = np.full(len(close), np.nan)
for i in range(14, len(close)):
    atr[i] = np.mean(tr[i-13:i+1])

# SMA50
sma50 = np.full(len(close), np.nan)
if len(close) >= 50:
    cs = np.cumsum(close)
    sma50[49] = cs[49]/50
    sma50[50:] = (cs[50:] - cs[:-50]) / 50

CAP = 200_000
COMM = 4
MAX_LOT = 5
RISK_PCT = 0.02

def backtest(direction='long', sl_pct=0.01, atr_mult=2.0, hold=13):
    """direction: 'long', 'short', or 'both'"""
    eq = float(CAP)
    peak = eq
    mdd = 0.0
    trades = []
    
    for i in range(50, len(close) - hold - 1):
        if i >= len(close) - hold:
            break
        
        ep = float(opn[i+1])
        go = ep * 1  # cs=1
        if go <= 0:
            continue
        
        # Filter: simple vol-up signal (any day with above-average volume)
        if vol[i] < np.mean(vol[:i]) * 1.2:
            continue
        
        # Sizing
        risk_amount = eq * RISK_PCT
        base_nc = risk_amount / (go * sl_pct) if sl_pct > 0 else risk_amount / go * 5
        base_nc = max(1, int(base_nc))
        nc = min(base_nc, MAX_LOT)
        if nc < 1:
            continue
        
        if direction in ('long', 'both'):
            npnl = 0
            rem = nc
            sp = ep * (1 - min(max(atr[i]/ep*atr_mult, 0.005), 0.05)) if not np.isnan(atr[i]) else ep * 0.95
            r_high = ep
            stop_hit = False
            for j in range(i+1, min(i+hold+1, len(close))):
                bh = float(high[j])
                if bh > r_high:
                    r_high = bh
                    if not np.isnan(atr[j]):
                        new_trail = max(atr[j]/r_high*atr_mult, 0.005)
                        sp = max(sp, r_high * (1 - min(new_trail, 0.05)))
                if float(low[j]) <= sp:
                    xp = sp
                    npnl = rem * 1 * (xp - ep) - rem * COMM
                    stop_hit = True
                    break
            if not stop_hit:
                xp = float(close[min(i+hold, len(close)-1)])
                npnl = rem * 1 * (xp - ep) - rem * COMM
            
            if npnl != 0:
                eq += npnl
                if eq > peak: peak = eq
                dd = (peak - eq) / peak * 100 if peak > 0 else 0
                mdd = max(mdd, dd)
                trades.append({'dir': 'L', 'entry': dates[i+1], 'ep': round(ep,2), 'xp': round(xp,2), 'npnl': round(npnl,0), 'stop': stop_hit})
        
        if direction in ('short', 'both'):
            npnl_s = 0
            rem_s = nc
            sp_s = ep * (1 + min(max(atr[i]/ep*atr_mult, 0.005), 0.05)) if not np.isnan(atr[i]) else ep * 1.05
            r_low = ep
            stop_hit_s = False
            for j in range(i+1, min(i+hold+1, len(close))):
                bl = float(low[j])
                if bl < r_low:
                    r_low = bl
                    if not np.isnan(atr[j]):
                        new_trail = max(atr[j]/r_low*atr_mult, 0.005)
                        sp_s = min(sp_s, r_low * (1 + min(new_trail, 0.05)))
                if float(high[j]) >= sp_s:
                    xp_s = sp_s
                    npnl_s = rem_s * 1 * (ep - xp_s) - rem_s * COMM
                    stop_hit_s = True
                    break
            if not stop_hit_s:
                xp_s = float(close[min(i+hold, len(close)-1)])
                npnl_s = rem_s * 1 * (ep - xp_s) - rem_s * COMM
            
            if npnl_s != 0:
                eq += npnl_s
                if eq > peak: peak = eq
                dd = (peak - eq) / peak * 100 if peak > 0 else 0
                mdd = max(mdd, dd)
                trades.append({'dir': 'S', 'entry': dates[i+1], 'ep': round(ep,2), 'xp': round(xp_s,2), 'npnl': round(npnl_s,0), 'stop': stop_hit_s})
    
    if not trades:
        return None
    ret = (eq - CAP) / CAP * 100
    wins = sum(1 for t in trades if t['npnl'] > 0)
    wr = wins / len(trades) * 100
    gp = sum(t['npnl'] for t in trades if t['npnl'] > 0)
    gl = sum(t['npnl'] for t in trades if t['npnl'] < 0)
    pf = abs(gp / (gl + 0.001))
    
    return dict(ret=round(ret,2), mdd=round(mdd,2), wr=round(wr,1), pf=round(pf,2),
                trades=len(trades), wins=wins, calmar=round(ret/mdd,2) if mdd>0 else 0)

# Test various configurations
print(f"{'Direction':>8} {'Hold':>4} {'AM':>4} {'SL':>6} {'Ret':>8} {'DD':>6} {'Calmar':>7} {'WR':>5} {'Tr':>4}")
print("-"*60)
for direction in ['long', 'both']:
    for hold in [8, 13, 21]:
        for am in [2, 3]:
            r = backtest(direction=direction, sl_pct=0.005, atr_mult=am, hold=hold)
            if r:
                print(f"{direction:>8} {hold:>4} {am:>4} {'0.5%':>6} {r['ret']:>+7.1f}% {r['mdd']:>5.1f}% {r['calmar']:>6.1f} {r['wr']:>4.0f}% {r['trades']:>4d}")
