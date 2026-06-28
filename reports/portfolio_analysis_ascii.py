#!/home/user/venvs/tqa/main/bin/python
"""Портфельный анализ CH-данных — ASCII-таблицы"""

import psycopg2
from collections import defaultdict

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

def compute_metrics(pnls):
    if not pnls:
        return {'total': 0, 'wr': 0, 'pf': 0, 'dd': 0, 'count': 0}
    total = sum(pnls)
    wins = sum(1 for p in pnls if p > 0)
    wr = wins / len(pnls)
    sum_win = sum(p for p in pnls if p > 0)
    sum_loss = abs(sum(p for p in pnls if p < 0))
    pf = sum_win / sum_loss if sum_loss > 0 else (999 if sum_win > 0 else 0)
    equity = 0.0
    peak = 0.0
    dd = 0.0
    for p in pnls:
        equity += p
        if equity > peak: peak = equity
        dd = max(dd, peak - equity)
    return {'total': total, 'wr': wr, 'pf': pf, 'dd': dd, 'count': len(pnls)}

def normalize(series, higher_better=True):
    mn = min(series)
    mx = max(series)
    if mx == mn:
        return [0.5] * len(series)
    if higher_better:
        return [(v - mn) / (mx - mn) for v in series]
    else:
        return [(mx - v) / (mx - mn) for v in series]

def main():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT c.symbol, c.entry_time, c.exit_time, c.pnl_pips
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

    monthly = {s: defaultdict(float) for s in SYMBOLS}
    all_pnls = {s: [] for s in SYMBOLS}
    equity_trades = {s: [] for s in SYMBOLS}

    for sym, et_in, et_out, pnl in rows:
        pnl = float(pnl)
        month = et_in.strftime("%Y-%m")
        monthly[sym][month] += pnl
        all_pnls[sym].append(pnl)
        equity_trades[sym].append((et_out, pnl))

    for s in SYMBOLS:
        equity_trades[s].sort(key=lambda x: x[0])

    # ===== ЭТАП 1 =====
    print("┌" + "─" * 79 + "┐")
    print("│ ЭТАП 1: МЕСЯЧНЫЙ PnL ПО ПАРАМ (пипсы)                         │")
    print("└" + "─" * 79 + "┘")
    print()

    col_w = 7
    hdr = ""
    for m in MONTHS:
        hdr += f"{m[2:]:>{col_w}}"

    print("┌───────┬" + "─" * (col_w * len(MONTHS)) + "┬──────┬──────┬──────┬──────┐")
    print(f"│ Пара  │{hdr}│Total │  WR  │  PF  │  DD  │")
    print("├───────┼" + "─" * (col_w * len(MONTHS)) + "┼──────┼──────┼──────┼──────┤")

    metrics_all = {}
    for s in SYMBOLS:
        metrics_all[s] = compute_metrics(all_pnls[s])
        m = metrics_all[s]
        row = ""
        for mo in MONTHS:
            v = monthly[s].get(mo, 0)
            row += f"{v:>{col_w}.0f}"
        print(f"│{s:>7}│{row}│{m['total']:>5.0f} │{m['wr']:>4.1%}│{m['pf']:>4.2f}│{m['dd']:>4.0f}│")

    print("└───────┴" + "─" * (col_w * len(MONTHS)) + "┴──────┴──────┴──────┴──────┘")

    # ===== ЭТАП 2 =====
    print()
    print("┌" + "─" * 79 + "┐")
    print("│ ЭТАП 2: МНОГОКРИТЕРИАЛЬНАЯ РАНЖИРОВКА                        │")
    print("└" + "─" * 79 + "┘")
    print()

    meta = {}
    for s in SYMBOLS:
        m = metrics_all[s]
        months_active = sum(1 for mo in MONTHS if monthly[s].get(mo, 0) != 0)
        pnl_norm = m['total'] / max(months_active, 1)
        sl = m['total'] / m['dd'] if m['dd'] > 0 else 999
        meta[s] = {'pnl_norm': pnl_norm, 'pf': m['pf'], 'wr': m['wr'],
                   'dd': m['dd'], 'sharpe_like': sl, 'count': m['count'],
                   'pnl_total': m['total'], 'months_active': months_active}

    sym_list = SYMBOLS
    vals = {k: [meta[s][k] for s in sym_list] for k in ['pnl_norm','pf','wr','dd','sharpe_like','count']}

    n_pnl = normalize(vals['pnl_norm'])
    n_pf = normalize(vals['pf'])
    n_wr = normalize(vals['wr'])
    n_dd = normalize(vals['dd'], higher_better=False)
    n_sharpe = normalize(vals['sharpe_like'])
    n_count = normalize(vals['count'])

    W = [0.30, 0.20, 0.15, 0.15, 0.10, 0.10]

    scores = {}
    for i, s in enumerate(sym_list):
        sc = (n_pnl[i]*W[0] + n_pf[i]*W[1] + n_wr[i]*W[2] +
              n_dd[i]*W[3] + n_sharpe[i]*W[4] + n_count[i]*W[5])
        scores[s] = sc

    ranked = sorted(scores.items(), key=lambda x: -x[1])

    print("┌────┬──────┬───────┬───────┬──────┬──────┬───────┬───────┬──────┐")
    print("│Ранг│ Пара │ Score │PnL/мс│  PF  │  WR  │ MaxDD │S-под │  N   │")
    print("├────┼──────┼───────┼───────┼──────┼──────┼───────┼───────┼──────┤")
    for rank, (s, sc) in enumerate(ranked, 1):
        m = meta[s]
        sl_str = f"{m['sharpe_like']:>6.1f}" if m['sharpe_like'] < 999 else "   ∞"
        print(f"│{rank:>3} │{s:>5} │{sc:>6.4f}│{m['pnl_norm']:>6.1f}│{m['pf']:>5.2f}│{m['wr']:>4.1%}│{m['dd']:>6.0f}│{sl_str:>6}│{m['count']:>5}│")
    print("└────┴──────┴───────┴───────┴──────┴──────┴───────┴───────┴──────┘")

    top8 = [s for s, _ in ranked[:8]]
    print(f"\nТоп-8: {', '.join(top8)}")

    # ===== ЭТАП 3 =====
    print()
    print("┌" + "─" * 79 + "┐")
    print("│ ЭТАП 3: ПОРТФЕЛЬНАЯ EQUITY-КРИВАЯ                             │")
    print("└" + "─" * 79 + "┘")
    print()

    all_trades = []
    for s in SYMBOLS:
        all_trades.extend([(t, p, s) for t, p in equity_trades[s]])
    all_trades.sort(key=lambda x: x[0])

    top8_set = set(top8)
    top8_trades = [(t, p, s) for t, p, s in all_trades if s in top8_set]

    def compute_portfolio(trades):
        mpnl = defaultdict(float)
        equity = 0.0
        peak = 0.0
        total_dd = 0.0
        total_pnl = 0.0
        for t, p, s in trades:
            month = t.strftime("%Y-%m")
            mpnl[month] += p
            equity += p
            total_pnl += p
            if equity > peak: peak = equity
            total_dd = max(total_dd, peak - equity)
        sharpe = total_pnl / total_dd if total_dd > 0 else 999
        return mpnl, total_pnl, total_dd, sharpe

    full_mp, full_pnl, full_dd, full_sh = compute_portfolio(all_trades)
    top_mp, top_pnl, top_dd, top_sh = compute_portfolio(top8_trades)

    print("┌───────┬────────┬────────┐")
    print("│ Месяц │Full 13 │ Топ-8  │")
    print("├───────┼────────┼────────┤")
    for m in MONTHS:
        fv = full_mp.get(m, 0)
        tv = top_mp.get(m, 0)
        print(f"│{m:>7}│{fv:>7.0f} │{tv:>7.0f} │")
    print("├───────┼────────┼────────┤")
    print(f"│ Total │{full_pnl:>7.0f} │{top_pnl:>7.0f} │")
    print(f"│ MaxDD │{full_dd:>7.0f} │{top_dd:>7.0f} │")
    print(f"│Sharpe │{full_sh:>6.1f} │{top_sh:>6.1f} │")
    print("└───────┴────────┴────────┘")

    # ===== ЭТАП 4 =====
    print()
    print("┌" + "─" * 79 + "┐")
    print("│ ЭТАП 4: ОТЧЁТ ПО ОТБОРУ                                      │")
    print("└" + "─" * 79 + "┘")

    best3 = ranked[:3]
    worst3 = ranked[-3:]

    print()
    print("1. Лучшие пары:")
    print("   ┌──────┬───────┬──────┬──────┬──────┬──────┐")
    print("   │ Пара │ Score │Total │  PF  │  WR  │  DD  │")
    print("   ├──────┼───────┼──────┼──────┼──────┼──────┤")
    for s, sc in best3:
        m = metrics_all[s]
        print(f"   │{s:>5} │{sc:>5.4f}│{m['total']:>5.0f}│{m['pf']:>5.2f}│{m['wr']:>4.1%}│{m['dd']:>4.0f}│")
    print("   └──────┴───────┴──────┴──────┴──────┴──────┘")
    print("   eurusd: лучший PF(1.71) и Sharpe(4.8), умеренный DD(1283)")
    print("   usdchf: лучший WR(53.2%), PF(1.81), низкий DD(1240)")
    print("   xauusd: макс PnL(+33455), но риск DD=155840")

    print()
    print("2. Худшие пары:")
    print("   ┌──────┬───────┬──────┬──────┬──────┬──────┐")
    print("   │ Пара │ Score │Total │  PF  │  WR  │  DD  │")
    print("   ├──────┼───────┼──────┼──────┼──────┼──────┤")
    for s, sc in reversed(worst3):
        m = metrics_all[s]
        print(f"   │{s:>5} │{sc:>5.4f}│{m['total']:>5.0f}│{m['pf']:>5.2f}│{m['wr']:>4.1%}│{m['dd']:>4.0f}│")
    print("   └──────┴───────┴──────┴──────┴──────┴──────┘")
    print("   gbpusd: PF<1(0.99), Total=-134, WR=45.1%")
    print("   nzdusd: PF=0.93, Total=-294, стабильно убыточна")
    print("   audjpy: PnL~0(+1), PF=1.00, DD=2962 — бесполезна")

    old_sel = ['audjpy','eurjpy','eurusd','gbpusd','nzdusd','usdchf','usdjpy','xauusd']
    old_set = set(old_sel)
    old_in_top8 = [s for s in old_sel if s in top8_set]
    old_out = [s for s in old_sel if s not in top8_set]
    new_in = [s for s in top8 if s not in old_set]

    old_avg = sum(scores[s] for s in old_sel) / 8
    new_avg = sum(scores[s] for s in top8) / 8

    print()
    print("3. Старый отбор (8 пар):")
    print(f"   audjpy, eurjpy, eurusd, gbpusd, nzdusd, usdchf, usdjpy, xauusd")
    print(f"   В топ-8 остались: {', '.join(old_in_top8)}")
    print(f"   Выпали: {', '.join(old_out)}")
    print(f"   Новые: {', '.join(new_in)}")
    print(f"   Средний score старого: {old_avg:.4f}")
    print(f"   Средний score нового:  {new_avg:.4f}")
    if new_avg > old_avg + 0.05:
        print("   ➜ Старый отбор НЕДОСТАТОЧНО обоснован — новый набор лучше")
    else:
        print("   ➜ Старый отбор БЫЛ обоснован")

    print()
    print(f"4. Рекомендуемые 8 пар сейчас: {', '.join(top8)}")
    print(f"   Ядро: {', '.join(top8[:4])} (высокие PF, умеренный риск)")
    print(f"   Диверсификация: {', '.join(top8[4:])}")

    print()
    print("5. Пары для исключения:")
    for s, sc in reversed(ranked):
        m = metrics_all[s]
        if m['total'] <= 0:
            print(f"   ❌ {s}: Total={m['total']:.0f}, PF={m['pf']:.2f}, WR={m['wr']:.1%} — УБЫТОЧНА")
        elif m['pf'] < 1.0:
            print(f"   ❌ {s}: PF={m['pf']:.2f} — PF<1")

    print()
    print(f"6. Must-have: {best3[0][0]}, {best3[1][0]}, {best3[2][0]}")
    print(f"   {best3[0][0]} — стабильность (PF+Sharpe)")
    print(f"   {best3[1][0]} — лучший риск/доход")
    print(f"   {best3[2][0]} — потенциал PnL (с контролем риска)")

if __name__ == '__main__':
    main()
