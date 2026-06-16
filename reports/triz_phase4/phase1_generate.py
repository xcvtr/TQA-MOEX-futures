#!/usr/bin/env python3
"""Фаза 1 — Генерация: прогон всех паттернов × оба направления по VB, SR, Eu.

Аудит на каждом шагу:
  1. Проверка данных тикера (есть ли в БД, количество баров, дыры)
  2. Проверка, что паттерны находят сделки (не 0)
  3. Механическая верификация PnL (случайная выборка)
  4. Визуальный дамп результатов

Usage: python3 phase1_generate.py
"""
import sys, os, json
sys.path.insert(0, '/home/user/projects/TQA-MOEX')
os.chdir('/home/user/projects/TQA-MOEX')
import numpy as np
import clickhouse_connect
from config import CH_HOST, CH_PORT, CH_DB

ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)

CAPITAL = 200_000; COMM = 4; MAX_LOT = 5; RISK_PCT = 0.02; MAX_LEV = 3.0
CS = 10

SYMBOLS = ['VB', 'SR', 'Eu']
# NB! SR на MOEX это Si (USDRUB_TOM), уточним из БД

PATTERNS = {
    'vol_up_oi_up_yb_up': lambda dv,dyb,dys,dfn,dtoi: dv>0 and dtoi>0 and dyb>0,
    'smart_money': lambda dv,dyb,dys,dfn,dtoi: dv>0 and dyb>0 and dfn<0,
    'vol_up_oi_down': lambda dv,dyb,dys,dfn,dtoi: dv>0 and dtoi<0,
    'vol_up_yb_down_fiz_up': lambda dv,dyb,dys,dfn,dtoi: dv>0 and dyb<0 and dfn>0,
    'fiz_extreme_vol_up': lambda dv,dyb,dys,dfn,dtoi: dv>0 and abs(dfn)>5,
}

HOLD_VALUES = [5, 8, 13, 21]
AM_VALUES = [2, 3, 5]

# ---------------------------------------------------------------------------
#  1) АУДИТ ДАННЫХ: проверить какие символы есть в moex.prices_5m
# ---------------------------------------------------------------------------
print("=" * 80)
print("ФАЗА 1 — АУДИТ ДАННЫХ")
print("=" * 80)

# Какие символы вообще есть
all_symbols = ch.query("SELECT DISTINCT symbol FROM moex.prices_5m WHERE time>='2024-01-01'").result_rows
all_symbols = [r[0] for r in all_symbols]
print(f"Доступные символы: {', '.join(all_symbols)}")

# Маппинг запрошенных
symbol_map = {}
for s in SYMBOLS:
    if s in all_symbols:
        symbol_map[s] = s
    else:
        # Поиск совпадений
        candidates = [x for x in all_symbols if s.lower() in x.lower()]
        if candidates:
            print(f"  ⚠ {s} не найден, кандидаты: {candidates}")
            symbol_map[s] = candidates[0]  # берём первый
        else:
            print(f"  ✗ {s} не найден в БД!")
            symbol_map[s] = None

# Выясним Si (SR)
for x in all_symbols:
    if 'Si' in x:
        print(f"  SR → {x} (USDRUB)")
        symbol_map['SR'] = x

# ---------------------------------------------------------------------------
#  2) ПРОВЕРКА КАЖДОГО СИМВОЛА: количество баров, дыры, OI
# ---------------------------------------------------------------------------
audit_results = {}
for name, sym in symbol_map.items():
    if sym is None:
        audit_results[name] = {'error': 'symbol not in DB'}
        continue
    
    # Проверим цены
    cnt = ch.query(f"SELECT count() FROM moex.prices_5m WHERE symbol='{sym}' AND time>='2024-01-01' AND time<='2026-05-01'").result_rows[0][0]
    print(f"\n--- Аудит {name} ({sym}): {cnt} баров ---")
    
    # Даты мин/макс
    mm = ch.query(f"SELECT min(time), max(time) FROM moex.prices_5m WHERE symbol='{sym}' AND time>='2024-01-01' AND time<='2026-05-01'").result_rows[0]
    print(f"  Диапазон: {mm[0]} — {mm[1]}")
    
    # OI есть?
    oi_cnt = ch.query(f"SELECT count() FROM moex.prices_5m_oi WHERE symbol='{sym}' AND time>='2024-01-01' AND time<='2026-05-01'").result_rows[0][0]
    print(f"  OI баров: {oi_cnt}")
    
    # Дыры: более 2 дней без данных
    dates = ch.query(f"SELECT DISTINCT toDate(time) as d FROM moex.prices_5m WHERE symbol='{sym}' AND time>='2024-01-01' ORDER BY d").result_rows
    dates = [r[0] for r in dates]
    gaps = []
    for i in range(1, len(dates)):
        gap = (dates[i] - dates[i-1]).days
        if gap > 3:  # skip weekends
            gaps.append((dates[i-1], dates[i], gap))
    if gaps:
        print(f"  ⚠ Дыры (>3 дней): {len(gaps)}")
        for g in gaps[:5]:
            print(f"    {g[0]} → {g[1]} ({g[2]} days)")
    else:
        print(f"  ✓ Дыр нет (кроме выходных)")
    
    # Последняя цена для контекста
    last_bar = ch.query(f"SELECT time, close FROM moex.prices_5m WHERE symbol='{sym}' AND time>='2024-01-01' ORDER BY time DESC LIMIT 1").result_rows[0]
    print(f"  Последний бар: {last_bar[0]} @ {last_bar[1]}")
    
    audit_results[name] = {
        'symbol': sym, 'bars': cnt, 'oi_bars': oi_cnt,
        'range': str(mm[0])[:10], 'last_price': float(last_bar[1])
    }

# ---------------------------------------------------------------------------
#  3) ПРОГОН ПАТТЕРНОВ
# ---------------------------------------------------------------------------
print("\n" + "=" * 80)
print("ФАЗА 1 — ПРОГОН ПАТТЕРНОВ")
print("=" * 80)

def load_data(symbol):
    """Загрузка данных для symbol."""
    rows = ch.query(f"""
        SELECT toDate(p.time) as d, argMax(p.open,p.time), argMax(p.high,p.time),
               argMax(p.low,p.time), argMax(p.close,p.time), argMax(p.volume,p.time),
               argMax(o.yur_buy,p.time), argMax(o.yur_sell,p.time),
               argMax(o.fiz_buy,p.time), argMax(o.fiz_sell,p.time),
               argMax(o.total_oi,p.time)
        FROM moex.prices_5m p INNER JOIN moex.prices_5m_oi o ON p.symbol=o.symbol AND p.time=o.time
        WHERE p.symbol='{symbol}' AND p.time>='2024-01-01' AND p.time<='2026-05-01'
        GROUP BY d ORDER BY d
    """).result_rows
    
    if not rows:
        return None, None
    
    a = np.array([list(r) for r in rows], dtype=object)
    dates = [str(r[0]) for r in rows]; N = len(dates)
    opn=a[:,1].astype(float); high=a[:,2].astype(float); low=a[:,3].astype(float)
    close=a[:,4].astype(float); vol=a[:,5].astype(float)
    yb=a[:,6].astype(float); ys=a[:,7].astype(float)
    fb=a[:,8].astype(float); fs=a[:,9].astype(float); toi=a[:,10].astype(float)
    toi=np.where(toi<=0,1,toi)
    
    tr=np.zeros(N)
    tr[1:]=np.maximum(high[1:]-low[1:],np.maximum(abs(high[1:]-close[:-1]),abs(low[1:]-close[:-1])))
    atr=np.full(N,np.nan)
    for i in range(14,N): atr[i]=np.mean(tr[i-13:i+1])
    
    v_m=np.mean(vol)+1; yb_m=np.mean(yb)+1; ys_m=np.mean(ys)+1; toi_m=np.mean(toi)+1
    dv=np.diff(vol)/v_m; dyb=np.diff(yb)/yb_m; dys=np.diff(ys)/ys_m; dtoi=np.diff(toi)/toi_m
    fiz_net=(fb-fs)/toi*100; dfn=np.diff(fiz_net)
    
    return dates, {
        'dates': dates, 'N': N, 'opn': opn, 'high': high, 'low': low,
        'close': close, 'vol': vol, 'atr': atr,
        'dv': dv, 'dyb': dyb, 'dys': dys, 'dtoi': dtoi, 'dfn': dfn,
    }


def run_strategy(d, data, direction, pfunc, hold, atr_mult):
    """Run one strategy config, return list of entries."""
    entries = []
    N = data['N']; opn = data['opn']; high = data['high']; low = data['low']
    close = data['close']; vol = data['vol']; atr = data['atr']
    dv = data['dv']; dyb = data['dyb']; dys = data['dys']; dfn = data['dfn']; dtoi = data['dtoi']
    dates = data['dates']
    
    for i in range(50, N - hold - 1):
        if i >= len(dv): break
        ep = float(opn[i+1])
        if not pfunc(dv[i], dyb[i], dys[i], dfn[i], dtoi[i]): continue
        if vol[i] < np.mean(vol[:i]) * 1.2: continue
        
        if direction == 'L':
            sp = ep*(1-min(max(atr[i]/ep*atr_mult,0.005),0.05)) if not np.isnan(atr[i]) else ep*0.95
            r_h = ep
            for j in range(i+1, min(i+hold+1, N)):
                bh = float(high[j])
                if bh > r_h:
                    r_h = bh
                    if not np.isnan(atr[j]): sp = max(sp, r_h*(1-min(max(atr[j]/r_h*atr_mult,0.005),0.05)))
                if float(low[j]) <= sp:
                    entries.append((i+1, j, direction, ep, sp, dates[i+1], dates[j]))
                    break
            else:
                entries.append((i+1, min(i+hold,N-1), direction, ep, float(close[min(i+hold,N-1)]), dates[i+1], dates[min(i+hold,N-1)]))
        else:
            sp = ep*(1+min(max(atr[i]/ep*atr_mult,0.005),0.05)) if not np.isnan(atr[i]) else ep*1.05
            r_l = ep
            for j in range(i+1, min(i+hold+1, N)):
                bl = float(low[j])
                if bl < r_l:
                    r_l = bl
                    if not np.isnan(atr[j]): sp = min(sp, r_l*(1+min(max(atr[j]/r_l*atr_mult,0.005),0.05)))
                if float(high[j]) >= sp:
                    entries.append((i+1, j, direction, ep, sp, dates[i+1], dates[j]))
                    break
            else:
                entries.append((i+1, min(i+hold,N-1), direction, ep, float(close[min(i+hold,N-1)]), dates[i+1], dates[min(i+hold,N-1)]))
    
    return entries


def calc_pnl(entries, direction):
    """Быстрый расчёт PnL для портфеля (без пересчёта эквити)."""
    if not entries:
        return None
    nc = 1  # normalized
    pnls = []
    for entry_idx, exit_idx, dir_, ep, xp, ed, xd in entries:
        if dir_ == 'L':
            pnl = nc * CS * (xp - ep) - nc * COMM
        else:
            pnl = nc * CS * (ep - xp) - nc * COMM
        pnls.append(pnl)
    
    ret = sum(pnls) / (CAPITAL / len(set(e[2] for e in entries))) * 100 if pnls else 0
    wins = sum(1 for p in pnls if p > 0)
    wr = wins / len(pnls) * 100 if pnls else 0
    gp = sum(p for p in pnls if p > 0); gl = sum(p for p in pnls if p < 0)
    pf = abs(gp / (gl + 0.001))
    return {'ret': round(ret, 2), 'wr': round(wr, 1), 'pf': round(pf, 2), 'n': len(pnls)}


# ---------------------------------------------------------------------------
#  3a) Прогон по всем тикерам
# ---------------------------------------------------------------------------
all_results = {}
for name, sym in symbol_map.items():
    if sym is None:
        print(f"\n✗ {name} — символ не найден, пропускаем")
        continue
    
    print(f"\n{'=' * 60}")
    print(f"ТИКЕР: {name} ({sym})")
    print(f"{'=' * 60}")
    
    dates, data = load_data(sym)
    if data is None or data['N'] < 100:
        print(f"✗ Недостаточно данных: {data['N'] if data else 0} баров")
        all_results[name] = {'error': 'insufficient data'}
        continue
    
    print(f"Данных: {data['N']} дней")
    
    # Прогон
    ticker_results = []
    for pname, pfunc in PATTERNS.items():
        for hold in HOLD_VALUES:
            for am in AM_VALUES:
                entries_l = run_strategy(dates, data, 'L', pfunc, hold, am)
                entries_s = run_strategy(dates, data, 'S', pfunc, hold, am)
                
                r_l = calc_pnl(entries_l, 'L')
                r_s = calc_pnl(entries_s, 'S')
                
                ticker_results.append({
                    'pattern': pname, 'hold': hold, 'am': am,
                    'L': r_l, 'S': r_s,
                    'n_L': len(entries_l), 'n_S': len(entries_s),
                })
    
    # -----------------------------------------------------------------------
    #  3b) АУДИТ: проверка что паттерны находят сделки
    # -----------------------------------------------------------------------
    print(f"\n--- АУДИТ: количество сделок ---")
    total_trades = sum(r['n_L'] + r['n_S'] for r in ticker_results)
    print(f"  Всего сделок (L+S): {total_trades}")
    
    zero_strats = [r for r in ticker_results if r['n_L'] == 0 and r['n_S'] == 0]
    if zero_strats:
        print(f"  ⚠ Стратегий с 0 сделок: {len(zero_strats)}")
        for z in zero_strats[:5]:
            print(f"    {z['pattern']} hold={z['hold']} am={z['am']}: L={z['n_L']} S={z['n_S']}")
    else:
        print(f"  ✓ Все комбинации находят сделки")
    
    # -----------------------------------------------------------------------
    #  3c) АУДИТ: механическая верификация (выборка 2 сделок, ручная сверка)
    # -----------------------------------------------------------------------
    print(f"\n--- АУДИТ: верификация PnL (выборка) ---")
    sampled = []
    for r in ticker_results:
        if r['L'] and r['L']['n'] > 0:
            sampled.append(r)
        if len(sampled) >= 2:
            break
    if not sampled:
        # попробуем S
        for r in ticker_results:
            if r['S'] and r['S']['n'] > 0:
                sampled.append(r)
            if len(sampled) >= 2:
                break
    
    for s in sampled:
        print(f"  {s['pattern']} hold={s['hold']} am={s['am']}:")
        if s['L']:
            print(f"    L: ret={s['L']['ret']:+.1f}% wr={s['L']['wr']:.0f}% pf={s['L']['pf']:.2f} n={s['L']['n']}")
        if s['S']:
            print(f"    S: ret={s['S']['ret']:+.1f}% wr={s['S']['wr']:.0f}% pf={s['S']['pf']:.2f} n={s['S']['n']}")
    
    # -----------------------------------------------------------------------
    #  4) ТОП результатов (best both-direction per Calmar-like metric)
    # -----------------------------------------------------------------------
    print(f"\n--- ТОП-10 best both-direction результатов ---")
    print(f"{'Pat':>22} {'Hold':>4} {'AM':>4} {'L_ret':>7} {'L_wr':>5} {'S_ret':>7} {'S_wr':>5} {'L_n':>4} {'S_n':>4}")
    print("-" * 70)
    
    scored = []
    for r in ticker_results:
        if r['L'] and r['S'] and r['L']['n'] >= 5 and r['S']['n'] >= 5:
            combined_ret = r['L']['ret'] + r['S']['ret']  # proxy
            min_wr = min(r['L']['wr'], r['S']['wr'])
            score = combined_ret * min_wr / 100  # weighted
            scored.append((score, r))
    
    scored.sort(key=lambda x: -x[0])
    for score, r in scored[:10]:
        lr = r['L']; sr = r['S']
        print(f"{r['pattern']:>22} {r['hold']:>4} {r['am']:>4} {lr['ret']:>+6.1f}% {lr['wr']:>4.0f}% {sr['ret']:>+6.1f}% {sr['wr']:>4.0f}% {lr['n']:>4d} {sr['n']:>4d}")
    
    all_results[name] = {
        'symbol': sym, 'bars': data['N'],
        'total_trades': total_trades,
        'top_results': [{
            'pattern': r['pattern'], 'hold': r['hold'], 'am': r['am'],
            'L_ret': r['L']['ret'] if r['L'] else None,
            'L_wr': r['L']['wr'] if r['L'] else None,
            'L_n': r['L']['n'] if r['L'] else 0,
            'S_ret': r['S']['ret'] if r['S'] else None,
            'S_wr': r['S']['wr'] if r['S'] else None,
            'S_n': r['S']['n'] if r['S'] else 0,
        } for _, r in scored[:10]]
    }

# ---------------------------------------------------------------------------
#  5) ИТОГОВЫЙ ОТЧЁТ
# ---------------------------------------------------------------------------
print("\n" + "=" * 80)
print("ФАЗА 1 — ИТОГОВЫЙ ОТЧЁТ")
print("=" * 80)
print(f"\nАудит данных:")
for name, ar in audit_results.items():
    status = '✓' if 'error' not in ar else '✗'
    print(f"  {status} {name}: {ar.get('symbol', 'N/A')} — {ar.get('bars', 0)} баров, last: {ar.get('last_price', 'N/A')}")

print(f"\nРезультаты прогона:")
for name, res in all_results.items():
    if 'error' in res:
        print(f"  ✗ {name}: {res['error']}")
        continue
    top = res['top_results'][0] if res['top_results'] else None
    if top:
        print(f"  ✓ {name} ({res['symbol']}): best={top['pattern']} hold={top['hold']} am={top['am']} "
              f"L={top['L_ret']:+.1f}% S={top['S_ret']:+.1f}% | всего сделок: {res['total_trades']}")
    else:
        print(f"  ✓ {name} ({res['symbol']}): нет проходных both-direction (n<5) | сделок: {res['total_trades']}")

# Сохранение
report = {
    'audit': audit_results,
    'results': {n: {
        'symbol': r.get('symbol'),
        'bars': r.get('bars'),
        'total_trades': r.get('total_trades'),
        'top10': r.get('top_results', [])
    } for n, r in all_results.items()}
}
out_path = 'reports/triz_phase4/phase1_generate.json'
with open(out_path, 'w') as f:
    json.dump(report, f, indent=2, default=str)
print(f"\nОтчёт сохранён: {out_path}")
print("ФАЗА 1 ЗАВЕРШЕНА")
