#!/home/user/venvs/tqa/main/bin/python
"""
Полный портфельный анализ CH-данных (source=clickhouse)
13 пар, monthly PnL, ранжировка, equity-кривая
"""

import psycopg2
from collections import defaultdict
import math

DB = dict(host='10.0.0.64', port=5432, dbname='forex', user='postgres')

SYMBOLS = ['audjpy','audusd','euraud','eurgbp','eurjpy','eurusd',
           'gbpjpy','gbpusd','nzdusd','usdcad','usdchf','usdjpy','xauusd']

MONTHS = []
for y in range(2025, 2027):
    for m in range(1, 13):
        if y == 2025 and m < 1: continue
        if y == 2026 and m > 6: continue
        MONTHS.append(f"{y}-{m:02d}")

def get_conn():
    return psycopg2.connect(**DB)

def fetch_data():
    conn = get_conn()
    cur = conn.cursor()
    
    # Получаем все закрытые сделки
    cur.execute("""
        SELECT c.symbol, c.entry_time, c.exit_time, c.pnl_pips, c.trading_status
        FROM clusters c
        JOIN tester_runs r ON c.run_id = r.id
        WHERE c.trading_status = 'EXITED'
          AND c.pnl_pips IS NOT NULL
          AND r.params->>'source' = 'clickhouse'
        ORDER BY c.symbol, c.exit_time
    """)
    
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows

def compute_metrics(pnls):
    """PnLs — список float"""
    if not pnls:
        return {'total': 0, 'wr': 0, 'pf': 0, 'dd': 0, 'count': 0}
    total = sum(pnls)
    wins = sum(1 for p in pnls if p > 0)
    losses = sum(1 for p in pnls if p < 0)
    wr = wins / len(pnls) if pnls else 0
    sum_win = sum(p for p in pnls if p > 0)
    sum_loss = abs(sum(p for p in pnls if p < 0))
    pf = sum_win / sum_loss if sum_loss > 0 else (999 if sum_win > 0 else 0)
    
    # Drawdown от equity-кривой
    equity = 0
    peak = 0
    dd = 0
    for p in pnls:
        equity += p
        if equity > peak:
            peak = equity
        dd_curr = peak - equity
        if dd_curr > dd:
            dd = dd_curr
    
    return {'total': total, 'wr': wr, 'pf': pf, 'dd': dd, 'count': len(pnls)}

def normalize(series, higher_better=True):
    """Min-max нормализация"""
    mn = min(series)
    mx = max(series)
    if mx == mn:
        return [0.5] * len(series)
    if higher_better:
        return [(v - mn) / (mx - mn) for v in series]
    else:
        return [(mx - v) / (mx - mn) for v in series]

def main():
    rows = fetch_data()
    print(f"Всего сделок: {len(rows)}")
    
    # === ЭТАП 1: Месячный PnL по парам ===
    # symbol -> {month: sum_pnl}
    monthly = {s: defaultdict(float) for s in SYMBOLS}
    # Также сохраняем все pnls для метрик
    all_pnls = {s: [] for s in SYMBOLS}
    # Для equity — по exit_time, sorted
    equity_trades = {s: [] for s in SYMBOLS}  # (exit_time, pnl)
    
    for sym, et_in, et_out, pnl, status in rows:
        pnl = float(pnl)
        month = et_in.strftime("%Y-%m")
        monthly[sym][month] += pnl
        all_pnls[sym].append(pnl)
        equity_trades[sym].append((et_out, pnl))
    
    # Сортируем equity сделки по времени
    for s in SYMBOLS:
        equity_trades[s].sort(key=lambda x: x[0])
    
    print("\n" + "="*80)
    print("ЭТАП 1: МЕСЯЧНЫЙ PnL ПО ПАРАМ (пипсы)")
    print("="*80)
    
    # Заголовок
    header = f"{'Пара':>8} |" + "".join(f"{m[5:]:>8}" for m in MONTHS) + f"{'Total':>10} {'WR':>7} {'PF':>7} {'DD':>8} {'N':>5}"
    sep = "─" * len(header)
    print(header)
    print("─" * len(header))
    
    # Узкая версия для ASCII
    for sym in SYMBOLS:
        mvals = [monthly[sym].get(m, 0) for m in MONTHS]
        met = compute_metrics(all_pnls[sym])
        row = f"{sym:>8} |" + "".join(f"{v:>8.0f}" for v in mvals) + \
              f"{met['total']:>10.0f} {met['wr']:>6.1%} {met['pf']:>6.2f} {met['dd']:>8.0f} {met['count']:>5}"
        print(row)
    
    # === ЭТАП 2: Ранжировка ===
    print("\n" + "="*80)
    print("ЭТАП 2: МНОГОКРИТЕРИАЛЬНАЯ РАНЖИРОВКА")
    print("="*80)
    
    # Собираем метрики
    metrics = {}
    for sym in SYMBOLS:
        met = compute_metrics(all_pnls[sym])
        # PnL нормализованный по месяцам доступности
        months_active = sum(1 for m in MONTHS if monthly[sym].get(m, 0) != 0)
        pnl_norm = met['total'] / max(months_active, 1)
        metrics[sym] = {
            'pnl_norm': pnl_norm,
            'pnl_total': met['total'],
            'pf': met['pf'],
            'wr': met['wr'],
            'dd': met['dd'],
            'count': met['count'],
            'months_active': months_active
        }
        # Sharpe-like (PnL/DD)
        metrics[sym]['sharpe_like'] = met['total'] / met['dd'] if met['dd'] > 0 else 999
    
    # Нормализуем
    sym_list = SYMBOLS
    pnl_norm_vals = [metrics[s]['pnl_norm'] for s in sym_list]
    pf_vals = [metrics[s]['pf'] for s in sym_list]
    wr_vals = [metrics[s]['wr'] for s in sym_list]
    dd_vals = [metrics[s]['dd'] for s in sym_list]
    sharpe_vals = [metrics[s]['sharpe_like'] for s in sym_list]
    count_vals = [metrics[s]['count'] for s in sym_list]
    
    n_pnl = normalize(pnl_norm_vals)
    n_pf = normalize(pf_vals)
    n_wr = normalize(wr_vals)
    n_dd = normalize(dd_vals, higher_better=False)
    n_sharpe = normalize(sharpe_vals)
    n_count = normalize(count_vals)
    
    # Веса
    W = [0.30, 0.20, 0.15, 0.15, 0.10, 0.10]
    
    scores = {}
    for i, s in enumerate(sym_list):
        score = (n_pnl[i]*W[0] + n_pf[i]*W[1] + n_wr[i]*W[2] + 
                 n_dd[i]*W[3] + n_sharpe[i]*W[4] + n_count[i]*W[5])
        scores[s] = score
    
    # Сортируем
    ranked = sorted(scores.items(), key=lambda x: -x[1])
    
    print(f"\n{'Ранг':>4} {'Пара':>8} {'Score':>7} {'PnL/mo':>8} {'PF':>7} {'WR':>7} {'DD':>8} {'S-Like':>8} {'N':>5}")
    print("─" * 70)
    for rank, (sym, sc) in enumerate(ranked, 1):
        m = metrics[sym]
        print(f"{rank:>4} {sym:>8} {sc:>6.4f} {m['pnl_norm']:>8.1f} {m['pf']:>6.2f} {m['wr']:>6.1%} {m['dd']:>8.0f} {m['sharpe_like']:>7.1f} {m['count']:>5}")
    
    top8 = [s for s, _ in ranked[:8]]
    print(f"\nТоп-8: {', '.join(top8)}")
    
    # === ЭТАП 3: Equity-кривая ===
    print("\n" + "="*80)
    print("ЭТАП 3: ПОРТФЕЛЬНАЯ EQUITY-КРИВАЯ")
    print("="*80)
    
    # Full portfolio equity (все 13)
    all_trades = []
    for s in SYMBOLS:
        all_trades.extend([(t, p, s) for t, p in equity_trades[s]])
    all_trades.sort(key=lambda x: x[0])
    
    # Top-8 portfolio
    top8_set = set(top8)
    top8_trades = [(t, p, s) for t, p, s in all_trades if s in top8_set]
    
    def compute_portfolio_metrics(trades):
        """Ежемесячный PnL и метрики"""
        monthly_pnl = defaultdict(float)
        equity = 0
        peak = 0
        total_dd = 0
        total_pnl = 0
        for t, p, s in trades:
            month = t.strftime("%Y-%m")
            monthly_pnl[month] += p
            equity += p
            total_pnl += p
            if equity > peak:
                peak = equity
            dd = peak - equity
            if dd > total_dd:
                total_dd = dd
        sharpe = total_pnl / total_dd if total_dd > 0 else 999
        return monthly_pnl, total_pnl, total_dd, sharpe
    
    full_mpnl, full_pnl, full_dd, full_sharpe = compute_portfolio_metrics(all_trades)
    top_mpnl, top_pnl, top_dd, top_sharpe = compute_portfolio_metrics(top8_trades)
    
    # Таблица ежемесячного PnL
    print(f"\nЕжемесячный PnL портфелей:")
    print(f"{'Месяц':>8} {'Full(13)':>10} {'Top-8':>10}")
    print("─" * 30)
    for m in MONTHS:
        fv = full_mpnl.get(m, 0)
        tv = top_mpnl.get(m, 0)
        print(f"{m:>8} {fv:>10.0f} {tv:>10.0f}")
    
    print(f"\n{'':>8} {'Full(13)':>10} {'Top-8':>10}")
    print(f"{'Total':>8} {full_pnl:>10.0f} {top_pnl:>10.0f}")
    print(f"{'Max DD':>8} {full_dd:>10.0f} {top_dd:>10.0f}")
    print(f"{'Sharpe':>8} {full_sharpe:>10.1f} {top_sharpe:>10.1f}")
    
    # === ЭТАП 4: Вывод ===
    print("\n" + "="*80)
    print("ЭТАП 4: ОТЧЁТ ПО ОТБОРУ")
    print("="*80)
    
    old_sel = ['audjpy','eurjpy','eurusd','gbpusd','nzdusd','usdchf','usdjpy','xauusd']
    old_set = set(old_sel)
    
    best3 = ranked[:3]
    worst3 = ranked[-3:]
    
    print(f"\n1. Лучшие пары: {best3[0][0]} ({best3[0][1]:.4f}), {best3[1][0]} ({best3[1][1]:.4f}), {best3[2][0]} ({best3[2][1]:.4f})")
    print(f"   Причина: высокий score за счёт PnL, PF, WR и низкого DD")
    
    print(f"\n2. Худшие пары: {worst3[2][0]} ({worst3[2][1]:.4f}), {worst3[1][0]} ({worst3[1][1]:.4f}), {worst3[0][0]} ({worst3[0][1]:.4f})")
    print(f"   Причина: низкий PnL, слабый PF, высокий DD")
    
    old_in_top8 = [s for s in old_sel if s in top8_set]
    old_out_top8 = [s for s in old_sel if s not in top8_set]
    new_in = [s for s in top8 if s not in old_set]
    
    print(f"\n3. Старый отбор (8 пар): из них в текущем топ-8 = {len(old_in_top8)}")
    print(f"   Вошли: {', '.join(old_in_top8)}")
    print(f"   Выпали: {', '.join(old_out_top8)}")
    print(f"   Новые в топ-8: {', '.join(new_in)}")
    
    old_score = sum(scores[s] for s in old_sel) / 8
    cur_score = sum(scores[s] for s in top8) / 8
    print(f"   Средний score старого набора: {old_score:.4f}")
    print(f"   Средний score нового набора: {cur_score:.4f}")
    print(f"   {'Старый отбор был обоснован' if cur_score - old_score < 0.1 else 'Старый отбор требует коррекции'}")
    
    print(f"\n4. Рекомендуемые 8 пар сейчас: {', '.join(top8)}")
    
    # Пары, стабильно убыточные
    print(f"\n5. Пары для исключения:")
    for s, sc in reversed(ranked):
        m = metrics[s]
        if m['pnl_total'] <= 0:
            print(f"   {s}: Total PnL={m['pnl_total']:.0f}, PF={m['pf']:.2f}, WR={m['wr']:.1%} — СТАБИЛЬНО УБЫТОЧНАЯ")
        elif m['pf'] < 1.0:
            print(f"   {s}: Total PnL={m['pnl_total']:.0f}, PF={m['pf']:.2f} — PF<1 (убыточная)")
    
    print(f"\n6. Must-have пары: {best3[0][0]}, {best3[1][0]}, {best3[2][0]}")

if __name__ == '__main__':
    main()
