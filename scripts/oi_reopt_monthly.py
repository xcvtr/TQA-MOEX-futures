#!/usr/bin/env python3 -u
"""OI Реоптимизатор — пересматривает параметры стратегии раз в месяц.

Логика (как walk-forward):
- Берёт последние 12 месяцев данных (futoi + mt5_continuous)
- Прогоняет сетку параметров: thr ∈ {3,4,5}, exit_thr ∈ {2,3}, risk ∈ {0.05, 0.10}
- Выбирает лучший по Calmar (ROI/MDD) на окне
- Обновляет futures.portfolio params для oi-стратегий
- Пишет отчёт в /tmp/oi_reopt_report.txt

Формула day_net — КАК В БЭКТЕСТЕ: (cur_b - day_start_b) / total_oi * 100
(накопление физ за день, НЕ мгновенный дисбаланс!)
"""
import sys, bisect, json
sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
import numpy as np
import clickhouse_connect as cc, psycopg2
from datetime import datetime, timezone, timedelta

CH_HOST = '10.0.0.60'
PG_HOST = '10.0.0.60'
DAY_SEC = 86400
MAX_MARGIN = 0.80
TICKER_LIMITS = {'BR': 100, 'NG': 100, 'SV': 80, 'RN': 80, 'RI': 50, 'TT': 30}

# futoi-тикер → mt5-тикер
ALL = {'BR': 'BR', 'NG': 'NG', 'SV': 'SILV', 'RI': 'RTSI', 'TT': 'TATN'}

ch = cc.get_client(host=CH_HOST, port=8123, database='moex')

def irk_day(ts):
    return int((ts - 7 * 3600) // DAY_SEC)

def load_recent(months=12):
    """Данные за последние N месяцев до сегодня."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=30 * months)
    s, e = start.strftime('%Y-%m-%d'), end.strftime('%Y-%m-%d')
    data = {}
    for fut_tk, mt_tk in ALL.items():
        r = ch.query(f"SELECT toUnixTimestamp(toDateTime(bt)), buy_fiz, sell_fiz, buy_yur, sell_yur "
                     f"FROM moex.futoi WHERE ticker='{fut_tk}' AND bt>='{s}' AND bt<='{e}'").result_rows
        day_start = {}
        net_map = {}
        for ts, fb, fs, yb, ys in r:
            d = irk_day(ts)
            if d not in day_start:
                day_start[d] = int(fb) - int(fs)
            total = int(fb) + int(fs) + int(yb) + int(ys)
            if total <= 0: continue
            net_map[ts] = (int(fb) - int(fs) - day_start[d]) / total * 100
        r2 = ch.query(f"SELECT toUnixTimestamp(toDateTime(bt)), opn, hi, lo, prc "
                      f"FROM moex.mt5_continuous WHERE ticker='{mt_tk}' AND bt>='{s}' AND bt<='{e}'").result_rows
        arr = np.array([(ts, o, h, l, c) for ts, o, h, l, c in r2 if c and c > 0], dtype=np.float64)
        if arr.size == 0: continue
        o = np.argsort(arr[:, 0])
        data[fut_tk] = (net_map, arr[o])
    return data

def specs_from_pg():
    conn = psycopg2.connect(host=PG_HOST, dbname='moex', user='postgres', connect_timeout=5)
    cur = conn.cursor()
    cur.execute("SELECT ticker, go, min_step, step_price, fee_entry FROM futures.ticker_specs")
    specs = {}
    for t, go, ms, sp, fee in cur.fetchall():
        specs[t] = (float(go), float(ms), float(sp), float(fee))
    conn.close()
    return specs

def gen_signals(data, specs, thr, exit_thr, max_hold=120, pyr=3, pyra_pct=0.5):
    sigs = []
    for fut_tk, (net_map, bars) in data.items():
        if fut_tk not in specs: continue
        go, ms, sp, fee = specs[fut_tk]
        pts = bars[:, 0]
        fts = sorted(net_map.keys())
        for direction in ['long', 'short']:
            pos = None
            for ts in fts:
                dn = net_map[ts]
                if pos is not None:
                    idx = bisect.bisect_right(pts, ts) - 1
                    if idx < 0: continue
                    cur_p = bars[idx, 4]
                    exit_cond = (dn >= exit_thr) if direction == 'long' else (dn <= -exit_thr)
                    hold_h = (ts - pos['entry_ts']) / 3600
                    if exit_cond or hold_h >= max_hold:
                        exit_p = cur_p - ms if direction == 'long' else cur_p + ms
                        # sig: entry_p + список цен частей (реальные цены входа)
                        sigs.append({'entry_ts': pos['entry_ts'], 'exit_ts': ts, 'exit_p': exit_p,
                                     'dir': direction, 'tk': fut_tk, 'go': go, 'ms': ms, 'sp': sp, 'fee': fee,
                                     'entry_p': pos['entry_p'], 'pyra_prices': list(pos['pyra_prices'])})
                        pos = None
                if pos is None:
                    in_cond = (dn <= -thr) if direction == 'long' else (dn >= thr)
                    if in_cond:
                        idx = bisect.bisect_right(pts, ts) - 1
                        if idx < 0: continue
                        fill_p = bars[idx, 4] + ms if direction == 'long' else bars[idx, 4] - ms
                        pos = {'entry_ts': ts, 'entry_p': fill_p, 'pyra_prices': []}
                elif pos is not None and len(pos['pyra_prices']) < pyr - 1:
                    idx = bisect.bisect_right(pts, ts) - 1
                    if idx >= 0:
                        if direction == 'long':
                            hi = bars[idx, 2]
                            gain = (hi - pos['entry_p']) / pos['entry_p'] * 100
                            if gain >= (len(pos['pyra_prices']) + 1) * pyra_pct:
                                pos['pyra_prices'].append(hi + ms)  # реальная цена добавки
                        else:
                            lo = bars[idx, 3]
                            gain = (pos['entry_p'] - lo) / pos['entry_p'] * 100
                            if gain >= (len(pos['pyra_prices']) + 1) * pyra_pct:
                                pos['pyra_prices'].append(lo - ms)  # реальная цена добавки
    return sigs

def simulate(sigs, start_cap=200000.0, risk=0.10):
    """Честная симуляция: лоты от текущего eq, лимит контрактов,
    суммарная маржа ≤ MAX_MARGIN, пирамидинг по РЕАЛЬНЫМ ценам частей."""
    eq = start_cap; peak = eq; cash_mdd = 0.0
    n = 0; wins = 0
    open_pos = []
    for s in sorted(sigs, key=lambda x: x['entry_ts']):
        open_pos = [p for p in open_pos if p[0] > s['entry_ts']]
        go = s['go']
        max_lots = TICKER_LIMITS.get(s['tk'], 50)
        n_parts = 1 + len(s['pyra_prices'])
        base_lots = max(1, int(eq * risk / go))
        base_lots = min(base_lots, max_lots)
        go_total = base_lots * go * n_parts
        used_go = sum(p[1] for p in open_pos)
        avail = eq * MAX_MARGIN - used_go
        if avail <= 0: continue
        if go_total > avail:
            base_lots = max(1, int(avail / (go * n_parts)))
            base_lots = min(base_lots, max_lots)
            go_total = base_lots * go * n_parts
        if base_lots < 1: continue
        # PnL по каждой части с её реальной ценой входа
        pnl = 0.0
        all_prices = [s['entry_p']] + s['pyra_prices']
        for p_in in all_prices:
            if s['dir'] == 'long':
                pnl += ((s['exit_p'] - p_in) / s['ms'] * s['sp'] - s['fee']*2) * base_lots
            else:
                pnl += ((p_in - s['exit_p']) / s['ms'] * s['sp'] - s['fee']*2) * base_lots
        eq += pnl; n += 1
        if pnl > 0: wins += 1
        peak = max(peak, eq)
        cash_mdd = max(cash_mdd, (peak - eq) / peak * 100)
        open_pos.append((s['exit_ts'], go_total))
    return eq, cash_mdd, n, wins

def optimize(data, specs):
    grid = []
    for thr in [3, 4, 5]:
        for exit_thr in [2, 3]:
            for risk in [0.05, 0.10]:
                grid.append((thr, exit_thr, risk))
    best = None; best_score = -1e9
    results = []
    for thr, exit_thr, risk in grid:
        sigs = gen_signals(data, specs, thr, exit_thr)
        if len(sigs) < 20: continue
        eq_f, mdd, n, w = simulate(sigs, risk=risk)
        roi = (eq_f / 200000 - 1) * 100
        calmar = roi / mdd if mdd > 0 else 0
        results.append({'thr': thr, 'exit_thr': exit_thr, 'risk': risk,
                        'roi': round(roi, 1), 'mdd': round(mdd, 1), 'calmar': round(calmar, 1),
                        'trades': len(sigs), 'wr': round(w / n * 100, 1) if n else 0})
        if calmar > best_score:
            best_score = calmar
            best = (thr, exit_thr, risk, roi, mdd, len(sigs))
    return best, results

def update_pg(best):
    thr, exit_thr, risk, roi, mdd, ntr = best
    conn = psycopg2.connect(host=PG_HOST, dbname='moex', user='postgres', connect_timeout=5)
    cur = conn.cursor()
    cur.execute("SELECT ticker, strategy, params FROM futures.portfolio WHERE strategy='oi' AND enabled")
    rows = cur.fetchall()
    for ticker, strategy, params in rows:
        p = dict(params) if params else {}
        p['thr'] = float(thr)
        p['exit_thr'] = float(exit_thr)
        p['risk'] = float(risk)
        p['direction'] = 'contrarian'
        p['max_positions'] = 3
        p['pyra_pct'] = 0.5
        p['stop_loss_pct'] = 99
        p['reopt'] = datetime.now(timezone.utc).isoformat()
        cur.execute("UPDATE futures.portfolio SET params=%s, updated_at=now() WHERE ticker=%s AND strategy='oi'",
                    (json.dumps(p), ticker))
        print(f"  UPDATE {ticker}: thr={thr} exit={exit_thr} risk={risk} direction=contrarian")
    conn.commit(); conn.close()

def main():
    print(f"=== OI Реоптимизация {datetime.now(timezone.utc).isoformat()} ===")
    specs = specs_from_pg()
    data = load_recent(12)
    n_sigs_total = sum(len(v[0]) for v in data.values())
    print(f"Данные: {len(data)} тикеров, {n_sigs_total} сигнальных точек за 12 мес")
    best, results = optimize(data, specs)
    if best is None:
        print("НЕТ достаточно данных — не обновляю параметры")
        return
    thr, exit_thr, risk, roi, mdd, ntr = best
    print(f"\nЛучший: thr={thr} exit_thr={exit_thr} risk={risk:.0%} "
          f"ROI={roi:+.1f}% MDD={mdd:.1f}% trades={ntr}")
    print("\nВсе конфиги:")
    for r in sorted(results, key=lambda x: -x['calmar'])[:8]:
        print(f"  thr{r['thr']} ex{r['exit_thr']} r{r['risk']:.0%}: "
              f"ROI {r['roi']:+.1f}% MDD {r['mdd']:.1f}% Calmar {r['calmar']:.1f} WR {r['wr']}% ({r['trades']}t)")
    update_pg(best)
    print("\n✅ Параметры обновлены в futures.portfolio")

if __name__ == '__main__':
    main()
