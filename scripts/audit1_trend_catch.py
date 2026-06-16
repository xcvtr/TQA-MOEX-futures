#!/usr/bin/env python3
"""
Аудит 1: Ловля тренда.
Сравниваем Phase 5 стратегию vs B&H IMOEXF.
"""
import json, sys, os
from datetime import datetime, timedelta
from collections import defaultdict
import numpy as np
import pandas as pd
import clickhouse_connect

sys.path.insert(0, '/home/user/projects/TQA-MOEX/scripts')
from phase5_walkforward import (
    INITIAL_CAPITAL, PORTFOLIO, TRAIN_END, TEST_END,
    precompute_signals, simulate_period, TICKER_CONFIGS
)

# ========================
# 1. Загружаем IMOEXF дневные данные
# ========================
ch = clickhouse_connect.get_client(host='127.0.0.1', port=8123)

print("=== Загрузка IMOEXF ===")
q_imoex = """
    SELECT 
        toDate(time) as dt,
        argMin(open, time) as open,
        max(high) as high,
        min(low) as low,
        argMax(close, time) as close
    FROM moex.prices_5m 
    WHERE symbol = 'IMOEXF' 
      AND time >= '2024-01-01' AND time <= '2026-04-30 23:59:59'
    GROUP BY dt
    ORDER BY dt
"""
r = ch.query(q_imoex)
cols = ['dt','open','high','low','close']
imoex = pd.DataFrame(r.result_rows, columns=cols)
imoex['dt'] = pd.to_datetime(imoex['dt'])
imoex.set_index('dt', inplace=True)
print(f"  IMOEXF дней: {len(imoex)}")

# ========================
# 2. Скачиваем данные для стратегии
# ========================
all_symbols = set()
for lst in PORTFOLIO.values():
    for c in lst:
        all_symbols.add(c[0])
print(f"\n=== Загрузка данных портфеля ===")
print(f"Тикеры: {sorted(all_symbols)}")

data_all = {}
for sym in sorted(all_symbols):
    q = f"""
        SELECT p.time,p.open,p.high,p.low,p.close,p.volume,
               o.fiz_buy,o.fiz_sell,o.yur_buy,o.yur_sell
        FROM moex.prices_5m p
        LEFT JOIN moex.prices_5m_oi o ON p.time=o.time AND p.symbol=o.symbol
        WHERE p.symbol='{sym}' AND p.time>='2024-01-01' AND p.time<='{TEST_END}'
        ORDER BY p.time
    """
    try:
        r = ch.query(q)
        if r.result_rows:
            cols2=['time','open','high','low','close','volume','fiz_buy','fiz_sell','yur_buy','yur_sell']
            df=pd.DataFrame(r.result_rows, columns=cols2)
            df['time']=pd.to_datetime(df['time']); df.set_index('time',inplace=True)
            data_all[sym]=df
            print(f"  ✓ {sym}: {len(df)} bars")
    except Exception as e:
        print(f"  ✗ {sym}: {e}")

# ========================
# 3. Предвычисляем сигналы
# ========================
print("\n=== Предвычисление сигналов ===")
signals_all = precompute_signals(data_all, list(all_symbols))
print(f"Сигналов: {len(signals_all)} тикеров")

# ========================
# 4. Запускаем симуляцию с трекингом daily equity
# ========================
test_start = datetime.strptime('2025-01-01', '%Y-%m-%d')
test_end_dt = datetime.strptime(TEST_END, '%Y-%m-%d') + timedelta(days=1)

# Патчим simulate_period для сбора equity
_original = simulate_period

def simulate_with_equity(data, signals, start, end, kelly_min, kelly_max, label):
    """Как simulate_period, но возвращает ещё daily_equity"""
    cash = INITIAL_CAPITAL; peak = INITIAL_CAPITAL; max_dd = 0
    kelly_hist = defaultdict(lambda: {'w':0,'l':0,'pnl':[]})
    positions = {}; all_trades = []
    daily_equity = []  # (date, equity)
    
    all_ts = []
    for sym in data:
        for t in data[sym].index:
            t_naive = t.to_pydatetime().replace(tzinfo=None) if hasattr(t, 'tz') and t.tz else t
            if isinstance(t_naive, pd.Timestamp):
                t_naive = t_naive.to_pydatetime().replace(tzinfo=None)
            start_n = start; end_n = end
            if start_n <= t_naive <= end_n:
                all_ts.append(t)
    all_ts = sorted(set(all_ts))
    
    for idx, ts in enumerate(all_ts):
        # Выходы
        to_close = []
        for sym,pos in list(positions.items()):
            rs=pos.get('real_sym',sym)
            if rs not in data or ts not in data[rs].index: continue
            bar=data[rs].loc[ts]
            ep=None; r=''
            if pos['dir']=='L' and bar['low']<=pos['stop']: ep=pos['stop']; r='stop'
            elif pos['dir']=='S' and bar['high']>=pos['stop']: ep=pos['stop']; r='stop'
            if ep is None and pos.get('bars_held',0)>=pos.get('hold',40): ep=bar['close']; r='time'
            if ep is None and 'pattern' in pos:
                sk=f"{pos['pattern']}_{pos['dir']}"
                if rs in signals and sk in signals[rs]:
                    dfsig,_,_,_=signals[rs][sk]
                    if ts in dfsig.index and float(dfsig.loc[ts,'score'])<0.10:
                        ep=bar['close']; r='fade'
            if ep is not None:
                dm=1 if pos['dir']=='L' else -1
                pp=dm*(ep-pos['entry'])/pos['entry']
                pr=pp*pos['go']*pos['contracts']; cash+=pr
                all_trades.append({'sym':rs,'dir':pos['dir'],'pnl_rub':pr,'reason':r})
                if pr>0: kelly_hist[rs]['w']+=1
                else: kelly_hist[rs]['l']+=1
                kelly_hist[rs]['pnl'].append(pr)
                if len(kelly_hist[rs]['pnl'])>50: kelly_hist[rs]['pnl'].pop(0)
                to_close.append(sym)
        for s in to_close: del positions[s]
        
        # MTM
        mtm=0
        for sym,pos in list(positions.items()):
            rs=pos.get('real_sym',sym)
            if rs in data and ts in data[rs].index:
                bar=data[rs].loc[ts]; dm=1 if pos['dir']=='L' else -1
                mtm+=dm*(bar['close']-pos['entry'])/pos['entry']*pos['go']*pos['contracts']
        teq=cash+mtm
        if teq>peak: peak=teq
        ddv=(peak-teq)/peak if peak>0 else 0
        if ddv>max_dd: max_dd=ddv
        
        # Сохраняем equity по дням (конец торгового дня)
        ts_date = ts.date() if hasattr(ts, 'date') else ts
        daily_equity.append((ts_date, teq))
        
        # Входы
        if ts.hour<7 or ts.hour>=23: continue
        locked=sum(p['go']*p.get('contracts',0) for p in positions.values())
        avail=cash-locked
        if avail<=0: continue
        
        entries=[]
        for lst_name,lst in PORTFOLIO.items():
            for sym,pat,di,hold,atm,w in lst:
                if sym in positions or sym not in data: continue
                if sym not in signals: continue
                sk=f"{pat}_{di}"; 
                if sk not in signals[sym]: continue
                dfsig,_,_,_=signals[sym][sk]
                if ts not in dfsig.index: continue
                bs=dfsig.loc[ts]
                score=float(bs.get('score',0))
                if np.isnan(score) or score<(0.25 if di=='L' else 0.20): continue
                go=TICKER_CONFIGS.get(sym,{}).get('go',5000)
                kh=kelly_hist[sym]
                kelly=kelly_min
                if kh['w']+kh['l']>=10:
                    wr_=kh['w']/max(kh['w']+kh['l'],1)
                    aw=max(sum(p for p in kh['pnl'] if p>0)/max(kh['w'],1),1)
                    al=max(abs(sum(p for p in kh['pnl'] if p<0)/max(kh['l'],1)),1)
                    rr=aw/al if al>0 else 1.5
                    k=wr_-(1-wr_)/max(rr,0.5)
                    kelly=max(kelly_min,min(k,kelly_max))
                pct=min(kelly*score*w,0.35)
                mr=avail*pct
                ct=max(1,int(mr/go))
                if ct==0: continue
                atrv=float(bs.get('atr14',0))
                if atrv==0 or np.isnan(atrv): continue
                ep=float(bs['close'])
                stop=ep-atrv*atm if di=='L' else ep+atrv*atm
                entries.append((sym,pat,di,hold,ct,ep,stop,go,score,lst_name))
        entries.sort(key=lambda e:e[8],reverse=True)
        for ent in entries[:5]:
            sym,pat,di,hold,ct,ep,stop,go,score,role=ent
            cost=ct*go
            if cost>avail: continue
            positions[sym]={'real_sym':sym,'dir':di,'hold':hold,'entry':ep,'stop':stop,'contracts':ct,'go':go,'bars_held':0,'entry_ts':ts,'pattern':pat}
            avail-=cost
    
    # Закрытие остатков
    for sym,pos in list(positions.items()):
        rs=pos.get('real_sym',sym)
        if rs in data:
            lb=data[rs].iloc[-1]
            dm=1 if pos['dir']=='L' else -1
            pp=dm*(lb['close']-pos['entry'])/pos['entry']
            pr=pp*pos['go']*pos['contracts']; cash+=pr
            all_trades.append({'sym':rs,'dir':pos['dir'],'pnl_rub':pr,'reason':'eod'})
    
    tr=(cash-INITIAL_CAPITAL)/INITIAL_CAPITAL*100
    wins=sum(1 for t in all_trades if t.get('pnl_rub',0)>0)
    total_t=len(all_trades)
    wr_=wins/total_t*100 if total_t>0 else 0
    if all_ts:
        days=(all_ts[-1]-all_ts[0]).days
        years=max(days/365.25,0.1)
    ann=(cash/INITIAL_CAPITAL)**(1/max(years,0.1))-1
    cal=ann/(max_dd) if max_dd>0 else 0
    
    # Конвертируем equity в DataFrame
    eq_df = pd.DataFrame(daily_equity, columns=['dt', 'equity'])
    eq_df['dt'] = pd.to_datetime(eq_df['dt'])
    eq_df = eq_df.drop_duplicates(subset='dt').set_index('dt')
    # Дневная доходность
    eq_df['ret'] = eq_df['equity'].pct_change()
    
    return {
        'capital': cash,
        'return_pct': tr,
        'annual_return': ann*100,
        'max_dd_pct': max_dd*100,
        'calmar': cal,
        'wr': wr_,
        'n_trades': total_t,
        'daily_equity': eq_df,
        'daily_ret': eq_df['ret']
    }

print("\n=== Симуляция Kelly 40-150% ===")
res40 = simulate_with_equity(data_all, signals_all, test_start, test_end_dt, 0.40, 1.50, "Kelly 40-150%")
eq40 = res40['daily_equity']

print("\n=== Симуляция Kelly 20-70% ===")
res20 = simulate_with_equity(data_all, signals_all, test_start, test_end_dt, 0.20, 0.70, "Kelly 20-70%")
eq20 = res20['daily_equity']

# ========================
# 5. B&H IMOEXF
# ========================
print("\n=== B&H IMOEXF ===")
imoex_bh = imoex['close'].copy()
imoex_bh = imoex_bh[imoex_bh.index >= '2025-01-01']
imoex_ret = imoex_bh.pct_change()

# ========================
# 6. Анализ по периодам
# ========================
print("\n" + "="*70)
print("АНАЛИЗ: ЛОВЛЯ ТРЕНДА")
print("="*70)

# Периоды
periods = {
    '2024': ('2024-01-01', '2024-12-31'),
    '2025': ('2025-01-01', '2025-12-31'),
    '2026-YTD': ('2026-01-01', '2026-04-30'),
    'Bull 2023-2024': ('2024-01-01', '2024-12-31'),  # IMOEXF only from Nov 2023
    'Sideways 2025-2026': ('2025-01-01', '2026-04-30'),
    'Full': ('2025-01-01', '2026-04-30'),
}

# Для IMOEXF считаем B&H return по годам
print(f"\n{'Период':25} {'IMOEXF B&H':>15} {'Strat 40-150%':>15} {'Strat 20-70%':>15}")
print("-"*70)

imoex_annual = {}
str40_annual = {}
str20_annual = {}

for pname, (ps, pe) in periods.items():
    ps_dt = pd.to_datetime(ps)
    pe_dt = pd.to_datetime(pe)
    
    # IMOEXF B&H
    imoex_p = imoex['close'][(imoex.index >= ps_dt) & (imoex.index <= pe_dt)]
    if len(imoex_p) > 1:
        imoex_ret_period = (imoex_p.iloc[-1] / imoex_p.iloc[0] - 1) * 100
    else:
        imoex_ret_period = np.nan
    imoex_annual[pname] = imoex_ret_period
    
    # Strategy 40-150%
    eq_p = eq40['equity'][(eq40.index >= ps_dt) & (eq40.index <= pe_dt)]
    if len(eq_p) > 1:
        str40_ret = (eq_p.iloc[-1] / eq_p.iloc[0] - 1) * 100
    else:
        str40_ret = np.nan
    str40_annual[pname] = str40_ret
    
    # Strategy 20-70%
    eq_p2 = eq20['equity'][(eq20.index >= ps_dt) & (eq20.index <= pe_dt)]
    if len(eq_p2) > 1:
        str20_ret = (eq_p2.iloc[-1] / eq_p2.iloc[0] - 1) * 100
    else:
        str20_ret = np.nan
    str20_annual[pname] = str20_ret
    
    print(f"{pname:25} {imoex_ret_period:>14.1f}% {str40_ret:>14.1f}% {str20_ret:>14.1f}%")

# ========================
# 7. Correlation
# ========================
print(f"\n{'='*70}")
print("CORRELATION: Ежедневная доходность")
print('='*70)

# Align dates
common_dates = eq40['ret'].dropna().index.intersection(imoex_ret.dropna().index)
corr40 = eq40['ret'].loc[common_dates].corr(imoex_ret.loc[common_dates])
corr20 = eq20['ret'].loc[common_dates].corr(imoex_ret.loc[common_dates])

print(f"Strategy Kelly 40-150% vs IMOEXF: {corr40:.4f}")
print(f"Strategy Kelly 20-70%  vs IMOEXF: {corr20:.4f}")

# Также по подпериодам
for pname, (ps, pe) in periods.items():
    if pname in ('2024', 'Bull 2023-2024'):
        continue  # strategy only from 2025
    ps_dt = pd.to_datetime(ps)
    pe_dt = pd.to_datetime(pe)
    cd = eq40['ret'].dropna().index.intersection(imoex_ret.dropna().index)
    cd = cd[(cd >= ps_dt) & (cd <= pe_dt)]
    if len(cd) > 5:
        c40 = eq40['ret'].loc[cd].corr(imoex_ret.loc[cd])
        c20 = eq20['ret'].loc[cd].corr(imoex_ret.loc[cd])
        print(f"Corr {pname:20} Kelly40: {c40:+.4f}  Kelly20: {c20:+.4f}  (n={len(cd)})")

# ========================
# 8. Итоговый вывод
# ========================
print(f"\n{'='*70}")
print("ВЫВОД")
print('='*70)

# Sideways period analysis
side40 = str40_annual.get('Sideways 2025-2026', 0)
side20 = str20_annual.get('Sideways 2025-2026', 0)
imoex_side = imoex_annual.get('Sideways 2025-2026', 0)

print(f"Боковой период (2025-2026):")
print(f"  IMOEXF B&H:      {imoex_side:+.1f}%")
print(f"  Strategy 40-150%: {side40:+.1f}%")
print(f"  Strategy 20-70%:  {side20:+.1f}%")

# Full period
full40 = str40_annual.get('Full', 0)
full20 = str20_annual.get('Full', 0)
imoex_full = imoex_annual.get('Full', 0)

print(f"\nЗа весь период (2025-янв/апр 2026):")
print(f"  IMOEXF B&H:      {imoex_full:+.1f}%")
print(f"  Strategy 40-150%: {full40:+.1f}%")
print(f"  Strategy 20-70%:  {full20:+.1f}%")

# Оценка
if corr40 > 0.5:
    print(f"\n⚠️  Высокая корреляция с индексом ({corr40:.2f}) — возможно ловля тренда")
else:
    print(f"\n✅ Низкая корреляция с индексом ({corr40:.2f}) — не похоже на простую ловлю тренда")

if side40 < -5:
    print(f"⚠️  Стратегия падает в боковике ({side40:+.1f}%) — может ловить тренд")
elif side40 > 10:
    print(f"✅ Сильная доходность в боковике ({side40:+.1f}%) — стратегия не просто ловит тренд")
else:
    print(f"→ Доходность в боковике около нуля ({side40:+.1f}%) — нейтрально")

# Сохраняем результаты
os.makedirs('reports/phase5_walkforward', exist_ok=True)
eq40.to_csv('reports/phase5_walkforward/equity_kelly40.csv')
eq20.to_csv('reports/phase5_walkforward/equity_kelly20.csv')
imoex.to_csv('reports/phase5_walkforward/imoex_daily.csv')

results = {
    'imoex_bh_return': {k: v for k, v in imoex_annual.items()},
    'strategy_40_return': {k: v for k, v in str40_annual.items()},
    'strategy_20_return': {k: v for k, v in str20_annual.items()},
    'correlation_40_vs_imoex': corr40,
    'correlation_20_vs_imoex': corr20,
    'conclusion': {
        'correlation_verdict': 'high_trend_following' if corr40 > 0.5 else 'low_correlation',
        'sideways_verdict': 'trend_following' if side40 < -5 else 'not_trend_following' if side40 > 10 else 'neutral'
    }
}
with open('reports/phase5_walkforward/audit1_trend_catch.json', 'w') as f:
    json.dump(results, f, indent=2, ensure_ascii=False, default=str)

print(f"\nРезультаты сохранены: reports/phase5_walkforward/audit1_trend_catch.json")
print(f"Equity кривые: reports/phase5_walkforward/equity_kelly*.csv")
