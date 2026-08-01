import sys, numpy as np, bisect
sys.path.insert(0, "/home/user/projects/TQA-MOEX-futures")
import clickhouse_connect as cc
from datetime import timedelta

ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')

# 1. Сопоставление futoi ticker -> mt5_continuous ticker через securities
sec = ch.query("SELECT ticker, shortname FROM moex.securities").result_rows
sec_map = {}
for t, sn in sec:
    sec_map[t] = sn

# mt5 тикеры с ценами
mt5_rows = ch.query("SELECT DISTINCT ticker FROM moex.mt5_continuous WHERE bt>='2024-01-01'").result_rows
mt5_tickers = set(r[0] for r in mt5_rows)

# futoi тикеры с полными данными
futoi_rows = ch.query("""
SELECT ticker, count() FROM moex.futoi 
WHERE bt>='2024-01-01' GROUP BY ticker HAVING count() > 10000 ORDER BY ticker
""").result_rows
futoi_tickers = [r[0] for r in futoi_rows]

# Маппинг по короткому имени
def map_ticker(ft):
    if ft in mt5_tickers: return ft
    # Прямые маппинги
    direct = {'AF':'AFLT','LK':'LKOH','SR':'SBRF','VB':'VTBR','MG':'MGNT',
              'HY':'HYDR','SN':'SNGP','SP':'SBPR','NM':'NOTK','RN':'RN',
              'SV':'SV','GZ':'GZ','BR':'BR','GD':'GD','NG':'NG','CR':'CR',
              'MM':'MM','Eu':'Eu','ED':'ED','Si':'Si',
              # Дополнительные через ALLFUT
              'AL':'AL','GK':'GK','ME':'ME','SF':'SF','TT':'TT','RM':'RM',
              'RB':'RB','UC':'UC','VI':'VI','AU':'AU','PT':'PT','MX':'MX',
              'PD':'PD','HY':'HYDR','MO':'MO','SN':'SNGR','GA':'GAZR','TA':'TATN',
              'RO':'ROSN','SB':'SBRF','SPB':'SBPR'}
    if ft in direct and direct[ft] in mt5_tickers: return direct[ft]
    return None

# Спеки тикеров (ms, sp, fee)
specs = {}
for t in ['AFLT','LKOH','SBRF','VTBR','MGNT','HYDR','SNGP','SBPR','NOTK',
          'RN','SV','GZ','BR','GD','NG','CR','MM','Eu','ED','Si','GAZR','TATN','ROSN']:
    # из PG или securities
    s = ch.query(f"SELECT minstep, stepprice FROM moex.securities WHERE ticker='{t}'").result_rows
    if s:
        specs[t] = (float(s[0][0]), float(s[0][1]))
    else:
        specs[t] = (1.0, 1.0)

FEE = {'GD':44.28,'NG':4.0,'BR':4.0,'CR':4.0,'RN':7.22,'SV':4.0,'GZ':1.96,
       'MM':1.51,'Eu':4.0,'ED':4.0,'Si':2.0,'AFLT':4.0,'LKOH':4.0,'SBRF':4.0,
       'VTBR':4.0,'MGNT':4.0,'HYDR':4.0,'SNGP':4.0,'SBPR':4.0,'NOTK':4.0,
       'GAZR':1.96,'TATN':4.0,'ROSN':7.22,'AL':4.0,'GK':4.0,'ME':4.0,'SF':4.0,'TT':4.0,'RM':4.0,'RB':4.0,'UC':4.0,'VI':4.0,'AU':4.0,'PT':4.0,'MX':4.0,'PD':4.0,'MO':4.0}

def backtest_oi(ticker, threshold_pct, hold_min, start_hour=19):
    # Цены
    rows = ch.query(f"SELECT bt, prc FROM moex.mt5_continuous WHERE ticker='{ticker}' AND bt>='2024-01-01' ORDER BY bt").result_rows
    if not rows: return None
    p_ts = [r[0] for r in rows]; p_prc = [float(r[1]) for r in rows]
    
    # OI
    oi_rows = ch.query(f"SELECT bt,buy_fiz,sell_fiz,buy_yur,sell_yur FROM moex.futoi WHERE ticker='{ticker}' AND bt>='2024-01-01' ORDER BY bt").result_rows
    if len(oi_rows) < 1000: return None
    
    # Дневной старт
    daily_open = {}
    for r in oi_rows:
        day = r[0].date()
        if day not in daily_open:
            daily_open[day] = (int(r[1]) - int(r[2]))  # net_fiz на открытии
    
    trades = []
    for i in range(len(oi_rows)):
        bt, fb, fs, yb, ys = oi_rows[i]
        day = bt.date()
        if day not in daily_open: continue
        if bt.hour < start_hour: continue  # сигналы только после start_hour
        total = fb + fs + yb + ys
        if total <= 0: continue
        net_fiz = fb - fs
        day_net = (net_fiz - daily_open[day]) / total * 100  # накопление физ за день (%)
        
        if day_net > threshold_pct: continue  # только физ продают
        
        idx = bisect.bisect_right(p_ts, bt) - 1
        if idx < 0: continue
        cur = p_prc[idx]
        if cur <= 0: continue
        ft = bt + timedelta(minutes=hold_min)
        j = bisect.bisect_left(p_ts, ft)
        if j >= len(p_ts): continue
        ret = (p_prc[j] - cur) / cur * 100
        trades.append(ret)
    
    return trades

# Sweep по всем маппящимся тикерам
print(f"{'Тикер':<7} {'Сдел':>6} {'WR':>6} {'PF':>6} {'avg%':>7} {'сумма%':>8}")
print("="*50)
results = []
for ft in futoi_tickers:
    mt = map_ticker(ft)
    if not mt: continue
    for thr in [-3.0, -5.0, -7.0]:
        trades = backtest_oi(mt, thr, 120)
        if not trades or len(trades) < 100: continue
        pnls = np.array(trades)
        wins = pnls[pnls > 0]
        losses = pnls[pnls <= 0]
        wr = len(wins)/len(pnls)*100
        pf = sum(wins)/abs(sum(losses)) if sum(losses) else 999
        results.append((wr, pf, np.mean(pnls), sum(pnls), len(pnls), ft, mt, thr))

results.sort(key=lambda x: (-x[1], -x[0]))
for wr, pf, avg, total, n, ft, mt, thr in results[:40]:
    print(f"{mt:<7} {n:>6} {wr:>5.1f}% {pf:>6.2f} {avg:>+6.3f}% {total:>+8.1f}%  (futoi={ft} thr={thr})")

ch.close()
