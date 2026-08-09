#!/usr/bin/env python3 -u
"""Портфельный тест: OI (BR/NG/SV) + Базис-арбитраж (Si/Eu/CNY), ОБЩИЙ капитал.

Обе стратегии конкурируют за equity: лоты от общего капитала, лимит маржи 80%.
Базис-часть использует ПРОВЕРЕННЫЕ функции из backtest.py (load_h1_clean IRK + zscore).
"""
import sys, bisect
import clickhouse_connect as cc
import numpy as np
import psycopg2
from datetime import datetime, timezone

ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')
DAY_SEC = 86400

conn = psycopg2.connect(host='10.0.0.60', dbname='moex', user='postgres', connect_timeout=5)
cur = conn.cursor()
cur.execute("SELECT ticker, go, min_step, step_price, fee_entry FROM futures.ticker_specs")
SPECS = {}
for t, go, ms, sp, fee in cur.fetchall():
    SPECS[t] = (float(go), float(ms), float(sp), float(fee))
conn.close()

# ── OI параметры (чекп.221: BR/NG/SV, thr=3, exit=2, hold 120ч, pyr=3, pyra_pct=0.5 как live) ──
OI_TICKERS = {'BR': 'BR', 'NG': 'NG', 'SV': 'SILV'}
OI_RISK = 0.10
OI_THR = 3.0
OI_EXIT = 2.0
OI_HOLD_H = 120
OI_MAX_LOTS = 100
OI_PYR = 3          # пирамидинг: до 2 добавок (как live pyra_pct=0.5)
OI_PYRA_PCT = 0.5   # % движения в плюс для добавки

# ── Базис-арбитраж (чекп.222 + ТРИЗ: ср/чт/пт, risk 6%) ──
BASIS_PAIRS = [('Si', 'USDRUBF', 1000.0, 5309), ('Eu', 'EURRUBF', 1000.0, 8328), ('CNY', 'CNYRUBF', 1.0, 302)]
BASIS_RISK = 0.06
BASIS_ZTHR = 1.5
BASIS_HOLD_H = 168
BASIS_MAX_LOTS = 30
BASIS_DOW = {2, 3, 4}

def irk_day(ts): return int((ts - 7*3600) // DAY_SEC)

def load_oi_net():
    net = {}
    for fut_tk in OI_TICKERS:
        r = ch.query(f"SELECT toUnixTimestamp(toDateTime(bt)), buy_fiz, sell_fiz, buy_yur, sell_yur "
                     f"FROM moex.futoi WHERE ticker='{fut_tk}' AND bt>='2024-01-01' AND bt<='2026-08-09 23:59:59'").result_rows
        day_start = {}
        for ts, fb, fs, yb, ys in r:
            d = irk_day(ts)
            if d not in day_start: day_start[d] = int(fb) - int(fs)
            total = int(fb)+int(fs)+int(yb)+int(ys)
            if total <= 0: continue
            net.setdefault(fut_tk, {})[ts] = (int(fb)-int(fs)-day_start[d]) / total * 100
    return net

# ── ПРОВЕРЕННАЯ загрузка из backtest.py (toStartOfHour в IRK, фильтр 7-23) ──
def load_h1_clean(f, p):
    def load(tk):
        rows = ch.query(f"""
            SELECT toStartOfHour(bt) h, argMax(prc, bt) prc
            FROM moex.mt5_continuous WHERE ticker='{tk}' AND bt >= '2024-01-01'
            GROUP BY h ORDER BY h
        """).result_rows
        out = {}
        prev = None
        for r in rows:
            t, pr = r[0], float(r[1])
            if prev is not None and abs(pr/prev - 1) > 0.05:
                continue
            out[t] = pr
            prev = pr
        return out
    fu = load(f); pf = load(p)
    common = sorted(set(fu) & set(pf))
    ts = [t for t in common if 7 <= t.hour <= 23]
    return ts, np.array([fu[t] for t in ts]), np.array([pf[t] for t in ts])

def load_h1_ohlc(f, p, scale):
    """H1 close + hi/lo базиса (для честного MTM). fu_hi−pf_lo = макс базис, fu_lo−pf_hi = мин."""
    def load(tk):
        rows = ch.query(f"""
            SELECT toStartOfHour(bt) h, argMax(prc, bt) c, max(hi) hi, min(lo) lo
            FROM moex.mt5_continuous WHERE ticker='{tk}' AND bt >= '2024-01-01'
            GROUP BY h ORDER BY h
        """).result_rows
        out = {}
        prev = None
        for r in rows:
            t = r[0]; c, h, l = float(r[1]), float(r[2]), float(r[3])
            if prev is not None and abs(c/prev - 1) > 0.05:
                continue
            out[t] = (c, h, l)
            prev = c
        return out
    fu = load(f); pf = load(p)
    common = sorted(set(fu) & set(pf))
    ts = [t for t in common if 7 <= t.hour <= 23]
    b_close = np.array([fu[t][0]/scale - pf[t][0] for t in ts])
    b_hi = np.array([fu[t][1]/scale - pf[t][2] for t in ts])
    b_lo = np.array([fu[t][2]/scale - pf[t][1] for t in ts])
    return ts, b_close, b_hi, b_lo

def zscore_arr(arr, win):
    out = np.full(len(arr), np.nan)
    for i in range(win, len(arr)):
        w = arr[i-win:i]
        m, s = w.mean(), w.std()
        out[i] = (arr[i]-m)/s if s > 1e-9 else 0.0
    return out

def load_oi_prices():
    """Цены OI-тикеров (M1: hi/lo/close на момент ts)"""
    prices = {}
    for fut_tk, mt_tk in OI_TICKERS.items():
        r = ch.query(f"SELECT toUnixTimestamp(toDateTime(bt)), opn, hi, lo, prc FROM moex.mt5_continuous "
                     f"WHERE ticker='{mt_tk}' AND bt>='2024-01-01' AND bt<='2026-08-09 23:59:59'").result_rows
        d = {}
        prev = None
        for ts, o, h, l, c in r:
            if not c or c <= 0: continue
            if prev is not None and abs(c/prev - 1) > 0.05:
                prev = None; continue
            d[ts] = (float(h), float(l), float(c)); prev = float(c)
        prices[fut_tk] = d
    return prices

def run(capital=200000, enable_oi=True, enable_basis=True, liq_frac=0.10):
    """liq_frac: лимит лотов = доля дневного объёма (ёмкость рынка)"""
    eq = capital; peak_mtm = eq; peak_cash = eq
    cash_mdd = mtm_mdd = 0.0
    trades = []
    pos_oi = {}; pos_basis = {}; pending_basis = {}

    # базис-данные (close + hi/lo для честного MTM)
    basis_data = {}
    for f, p, scale, go in BASIS_PAIRS:
        ts, b_close, b_hi, b_lo = load_h1_ohlc(f, p, scale)
        z = zscore_arr(b_close, 120)
        basis_data[f] = {'ts': ts, 'basis': b_close, 'basis_hi': b_hi, 'basis_lo': b_lo, 'z': z, 'go': go}
    # индекс ts→позиция в массиве
    b_idx = {f: {t: i for i, t in enumerate(d['ts'])} for f, d in basis_data.items()}

    net_map = load_oi_net()
    oi_prices = load_oi_prices()
    oi_keys = {fut_tk: sorted(d.keys()) for fut_tk, d in oi_prices.items()}

    # ── Лимиты ликвидности: 10% реального дневного объёма (AlgoPack контракты) ──
    # BR 631K, NG 1.65M, SILV 651K; перпетуалы — mt5 tick_vol × 20 (оценка: тик/сделка vs контракт)
    liq_rows = ch.query("""
        SELECT asset_code, round(sum(vol) / NULLIF(count(DISTINCT tradedate), 0))
        FROM moex.tradestats_fo
        WHERE asset_code IN ('BR','NG','SILV') AND tradedate >= '2025-01-01' AND vol > 0
        GROUP BY asset_code
    """).result_rows
    DAILY_VOL = {r[0]: float(r[1]) for r in liq_rows}
    # перпетуалы: mt5 tick_vol за день ≈ avg_vol × 1440 × 20 (оценка контрактов)
    perp_rows = ch.query("""
        SELECT ticker, round(avg(vol)) FROM moex.mt5_continuous
        WHERE ticker IN ('USDRUBF','EURRUBF','CNYRUBF','Si','Eu','CNY')
          AND bt >= '2025-01-01' GROUP BY ticker
    """).result_rows
    for tk, avg_vol in perp_rows:
        DAILY_VOL.setdefault(tk, float(avg_vol) * 1440 * 20)
    def liq_limit(tk):
        return max(10, int(DAILY_VOL.get(tk, 100000) * liq_frac))

    # таймлайн: OI futoi точки + H1 базис-бары
    all_ts = set()
    for fut_tk in OI_TICKERS:
        all_ts.update(net_map[fut_tk].keys())
    for f in basis_data:
        all_ts.update(t.timestamp() for t in basis_data[f]['ts'])
    timeline = sorted(all_ts)
    print(f'Таймлайн: {len(timeline)} точек')

    def go_of(fut_tk):
        return SPECS.get(fut_tk, (10000, 1.0, 1.0, 3.81))[0]

    def margin_used():
        m = 0.0
        for fut_tk, p in pos_oi.items():
            m += go_of(fut_tk) * p['lots']
        for f, p in pos_basis.items():
            m += basis_data[f]['go'] * p['lots']
        return m

    # кэш последних цен OI для MTM
    for ts in timeline:
        # ── 1. Тик OI ──
        if enable_oi:
            for fut_tk in list(pos_oi):
                p = pos_oi[fut_tk]
                dn = net_map[fut_tk].get(ts)
                if dn is None: continue
                kk = oi_keys[fut_tk]
                idx = bisect.bisect_right(kk, ts) - 1
                if idx < 0: continue
                hi, lo, cur_p = oi_prices[fut_tk][kk[idx]]
                exit_cond = (dn >= OI_EXIT) if p['side'] == 'long' else (dn <= -OI_EXIT)
                hold_h = (ts - p['entry_ts']) / 3600
                # ── пирамидинг (как oi_audit_final pyr=3, pyra_pct=0.5) + проверка маржи ──
                go, ms, sp, fee = SPECS.get(fut_tk, (10000, 1.0, 1.0, 3.81))
                if len(p['pyra_prices']) < OI_PYR - 1:
                    # добавка требует ГО на доп. лот — только если маржа позволяет
                    add_margin = go * p['lots']
                    if margin_used() + add_margin <= eq * 0.80:
                        if p['side'] == 'long':
                            if (hi - p['entry_p']) / p['entry_p'] * 100 >= (len(p['pyra_prices'])+1) * OI_PYRA_PCT:
                                p['pyra_prices'].append(hi + ms)
                        else:
                            if (p['entry_p'] - lo) / p['entry_p'] * 100 >= (len(p['pyra_prices'])+1) * OI_PYRA_PCT:
                                p['pyra_prices'].append(lo - ms)
                if exit_cond or hold_h >= OI_HOLD_H:
                    exit_p = cur_p - ms if p['side'] == 'long' else cur_p + ms
                    pnl = 0.0
                    for p_in in [p['entry_p']] + p['pyra_prices']:
                        if p['side'] == 'long': pnl += ((exit_p - p_in)/ms*sp - fee*2) * p['lots']
                        else: pnl += ((p_in - exit_p)/ms*sp - fee*2) * p['lots']
                    eq += pnl
                    trades.append((ts, f'OI-{fut_tk}', pnl, p['lots']))
                    peak_cash = max(peak_cash, eq)
                    cash_mdd = max(cash_mdd, (peak_cash-eq)/peak_cash*100)
                    del pos_oi[fut_tk]

        # ── 2. Тик базис ──
        if enable_basis:
            for f in list(pos_basis):
                d = basis_data[f]
                i = b_idx[f].get(datetime.fromtimestamp(ts, tz=timezone.utc))
                if i is None: continue
                p = pos_basis[f]
                exit_cond = d['z'][i] <= 0
                hold_h = (ts - p['entry_ts']) / 3600
                if exit_cond or hold_h >= BASIS_HOLD_H:
                    pnl = (p['entry_b'] - d['basis'][i]) * 1000.0 * p['lots'] - 60.0 * p['lots']
                    eq += pnl
                    trades.append((ts, f'BA-{f}', pnl, p['lots']))
                    peak_cash = max(peak_cash, eq)
                    cash_mdd = max(cash_mdd, (peak_cash-eq)/peak_cash*100)
                    del pos_basis[f]

        # ── 3. Открытие OI ──
        if enable_oi:
            for fut_tk, dn_map in net_map.items():
                if fut_tk in pos_oi: continue
                dn = dn_map.get(ts)
                if dn is None: continue
                kk = oi_keys[fut_tk]
                idx = bisect.bisect_right(kk, ts) - 1
                if idx < 0: continue
                hi, lo, cur_p = oi_prices[fut_tk][kk[idx]]
                go, ms, sp, fee = SPECS.get(fut_tk, (10000, 1.0, 1.0, 3.81))
                if dn <= -OI_THR:
                    side = 'long'; fill = cur_p + ms
                elif dn >= OI_THR:
                    side = 'short'; fill = cur_p - ms
                else: continue
                lots = max(1, int(eq * OI_RISK / go))
                lots = min(lots, OI_MAX_LOTS)
                lots = min(lots, liq_limit(fut_tk))  # ёмкость рынка
                if margin_used() + go * lots <= eq * 0.80:
                    pos_oi[fut_tk] = {'side': side, 'entry_ts': ts, 'entry_p': fill, 'lots': lots, 'pyra_prices': []}

        # ── 4. Открытие базис (вход по close СЛЕДУЮЩЕГО бара — анти-look-ahead) ──
        if enable_basis:
            # 4a. Сначала исполняем отложенные сигналы (сигнал был на prev баре → вход по close текущего)
            for f in list(pending_basis):
                d = basis_data[f]
                i = b_idx[f].get(datetime.fromtimestamp(ts, tz=timezone.utc))
                if i is None: continue
                lots = pending_basis.pop(f)
                pos_basis[f] = {'entry_ts': ts, 'entry_b': d['basis'][i], 'lots': lots}
            # 4b. Новые сигналы → ставим в pending (вход на следующем баре)
            for f, d in basis_data.items():
                if f in pos_basis or f in pending_basis: continue
                i = b_idx[f].get(datetime.fromtimestamp(ts, tz=timezone.utc))
                if i is None or i < 120 or np.isnan(d['z'][i]): continue
                if d['ts'][i].weekday() not in BASIS_DOW: continue
                if d['z'][i] <= BASIS_ZTHR: continue
                loss_est = 389.0 if f == 'Si' else (1000.0 if f == 'Eu' else 54.0)
                lots = max(1, int(eq * BASIS_RISK / loss_est))
                lots = min(lots, BASIS_MAX_LOTS)
                # ёмкость рынка: лимит по обоим инструментам пары
                perp_map = {'Si': 'USDRUBF', 'Eu': 'EURRUBF', 'CNY': 'CNYRUBF'}
                lots = min(lots, liq_limit(f), liq_limit(perp_map[f]))
                if margin_used() + d['go'] * lots <= eq * 0.80:
                    pending_basis[f] = lots

        # ── MTM (по lo/hi — худший внутрисделочный уровень) ──
        mtm = eq
        if enable_oi:
            for fut_tk, p in pos_oi.items():
                kk = oi_keys[fut_tk]
                idx = bisect.bisect_right(kk, ts) - 1
                if idx < 0: continue
                hi, lo, cur = oi_prices[fut_tk][kk[idx]]
                go, ms, sp, fee = SPECS.get(fut_tk, (10000, 1.0, 1.0, 3.81))
                worst = lo if p['side'] == 'long' else hi  # long: худший = lo; short: худший = hi
                for p_in in [p['entry_p']] + p['pyra_prices']:
                    if p['side'] == 'long': mtm += ((worst - p_in)/ms*sp - fee*2) * p['lots']
                    else: mtm += ((p_in - worst)/ms*sp - fee*2) * p['lots']
        if enable_basis:
            for f, p in pos_basis.items():
                d = basis_data[f]
                i = b_idx[f].get(datetime.fromtimestamp(ts, tz=timezone.utc))
                if i is not None:
                    # SHORT базиса: худший = базис максимален (basis_hi)
                    mtm += (p['entry_b'] - d['basis_hi'][i]) * 1000.0 * p['lots']
        peak_mtm = max(peak_mtm, mtm)
        if peak_mtm > 0:
            mtm_mdd = max(mtm_mdd, (peak_mtm-mtm)/peak_mtm*100)

    # закрыть хвосты
    for fut_tk, p in pos_oi.items():
        go, ms, sp, fee = SPECS.get(fut_tk, (10000, 1.0, 1.0, 3.81))
        kk = oi_keys[fut_tk]
        hi, lo, cur = oi_prices[fut_tk][kk[-1]]
        exit_p = cur - ms if p['side'] == 'long' else cur + ms
        pnl = 0.0
        for p_in in [p['entry_p']] + p['pyra_prices']:
            if p['side'] == 'long': pnl += ((exit_p - p_in)/ms*sp - fee*2) * p['lots']
            else: pnl += ((p_in - exit_p)/ms*sp - fee*2) * p['lots']
        eq += pnl; trades.append((timeline[-1], f'OI-{fut_tk}', pnl, p['lots']))
    for f, p in pos_basis.items():
        d = basis_data[f]
        pnl = (p['entry_b'] - d['basis'][-1]) * 1000.0 * p['lots'] - 60.0 * p['lots']
        eq += pnl; trades.append((timeline[-1], f'BA-{f}', pnl, p['lots']))

    return eq, cash_mdd, mtm_mdd, trades

years = 2.6
import itertools
print('=== СКАН: риск × пирамидинг × lots (MTM DD ≤ 20%, ликвидность 10%) ===')
results = []
for risk in [0.03, 0.04, 0.05, 0.06, 0.07, 0.08, 0.10]:
    for pyr in [1, 2, 3]:
        for mla in [100, 300, 500, 1000]:
            OI_RISK = risk
            OI_PYR = pyr
            OI_MAX_LOTS = mla
            eq, cd, md, trades = run(200000, enable_oi=True, enable_basis=True, liq_frac=0.10)
            pnls = np.array([t[2] for t in trades])
            cagr = (eq/200000)**(1/years) - 1 if eq > 0 else -1
            if md <= 20.0 and cagr > 0:
                results.append((cagr/(md/100) if md else 0, cagr, md, cd, risk, pyr, mla, len(pnls), (pnls>0).mean()*100))
results.sort(reverse=True)
print(f'Вариантов MTM DD≤20%: {len(results)}')
print('ТОП-10 по Calmar:')
for cal, cagr, md, cd, risk, pyr, mla, n, wr in results[:10]:
    print(f'risk={risk:.0%} pyr={pyr} lots<={mla}: CAGR={cagr*100:.0f}% MTM={md:.1f}% Cash={cd:.1f}% Calmar={cal:.1f} N={n} WR={wr:.0f}%')
print('\nТОП-5 по CAGR (MTM DD≤20%):')
for cal, cagr, md, cd, risk, pyr, mla, n, wr in sorted(results, key=lambda x: -x[1])[:5]:
    print(f'risk={risk:.0%} pyr={pyr} lots<={mla}: CAGR={cagr*100:.0f}% MTM={md:.1f}% Cash={cd:.1f}% Calmar={cal:.1f} N={n} WR={wr:.0f}%')

# ── Детали лучшего варианта (risk 4%, pyr 3, lots 100) ──
print('\n=== ДЕТАЛИ: risk=4% pyr=3 lots<=100 (с ликвидностью 10%) ===')
OI_RISK = 0.04; OI_PYR = 3; OI_MAX_LOTS = 100
eq, cd, md, trades = run(200000, enable_oi=True, enable_basis=True, liq_frac=0.10)
pnls = np.array([t[2] for t in trades])
cagr = (eq/200000)**(1/years) - 1
print(f'eq={eq/1000:.0f}K CAGR={cagr*100:.0f}% CashDD={cd:.1f}% MTMDD={md:.1f}% Calmar={cagr/(md/100):.1f}')
print(f'N={len(pnls)} WR={(pnls>0).mean()*100:.0f}% net={pnls.sum()/1000:.0f}K')
by_type = {}
for t in trades:
    key = t[1].split('-')[0]
    by_type.setdefault(key, []).append(t[2])
for k, v in by_type.items():
    p = np.array(v)
    print(f'  {k}: N={len(p)} WR={(p>0).mean()*100:.0f}% net={p.sum()/1000:.0f}K')
by_year = {}
for t in trades:
    by_year.setdefault(datetime.fromtimestamp(t[0], tz=timezone.utc).year, []).append(t[2])
for y, v in sorted(by_year.items()):
    print(f'  {y}: N={len(v)} net={sum(v)/1000:.0f}K')
