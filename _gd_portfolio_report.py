#!/usr/bin/env python3
"""GD портфель: детальный отчёт p1_or_p2 hold=2 sl=2% capital=200K."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import clickhouse_connect
from config import CH_HOST, CH_PORT, CH_DB

CAPITAL = 200_000
CS = 10
COMM = 4
HOLD = 2
SL = 0.02
MODE = 'p1_or_p2'

ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)
rows = ch.query("""
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
    WHERE p.symbol = 'GD' AND p.time >= '2024-01-01' AND p.time <= '2026-05-01'
    GROUP BY d ORDER BY d
""").result_rows

dates = [str(r[0]) for r in rows]
opn = np.array([r[1] for r in rows], dtype=float)
high = np.array([r[2] for r in rows], dtype=float)
low = np.array([r[3] for r in rows], dtype=float)
close = np.array([r[4] for r in rows], dtype=float)
vol = np.array([r[5] for r in rows], dtype=float)
yb = np.array([r[6] for r in rows], dtype=float)
fb = np.array([r[8] for r in rows], dtype=float)
fs = np.array([r[9] for r in rows], dtype=float)
toi = np.array([r[10] for r in rows], dtype=float)
toi = np.where(toi <= 0, 1, toi)

v_m = np.mean(vol) + 1
yb_m = np.mean(yb) + 1
toi_m = np.mean(toi) + 1

dv = np.diff(vol) / v_m
dyb = np.diff(yb) / yb_m
fiz_net = (fb - fs) / toi * 100
dfn = np.diff(fiz_net)
dtoi = np.diff(toi) / toi_m

n = len(rows)
n_folds = 4
fsize = n // n_folds

print(f'GD {MODE} hold={HOLD} sl={SL:.0%} capital={CAPITAL:,}')
print(f'Data: {n} days ({dates[0]} to {dates[-1]}), {n_folds} folds x ~{fsize} days')
print()

def sig1(i):
    return bool(dv[i] > 0 and dtoi[i] < 0)

def sig2(i):
    return bool(dv[i] > 0 and dyb[i] > 0 and dfn[i] < 0)

fold_results = []

for f in range(n_folds):
    s = f * fsize
    e = n if f == n_folds - 1 else (f + 1) * fsize

    cash_p1 = CAPITAL // 2
    cash_p2 = CAPITAL // 2
    trades_p1 = []
    trades_p2 = []
    eq_curve = []

    pos_p1 = None
    pos_p2 = None

    fold_end = min(e + HOLD + 1, n)
    for j in range(s, fold_end):
        if j >= n:
            break

        # Check for new signals at j-1 -> entry at open[j]; only within fold range
        if j - 1 >= s and j - 1 < e and j - 1 < n - 1 and j - 1 < n - HOLD - 2:
            i = j - 1
            if pos_p1 is None and sig1(i):
                ei = j
                xi = min(ei + HOLD, n - 2)
                if ei < n - 1:
                    ep = float(opn[ei])
                    sp = ep * (1 - SL)
                    go = ep * CS
                    nc = max(1, int(cash_p1 // go)) if go > 0 else 1
                    pos_p1 = {
                        'entry_bar': ei, 'entry_price': ep,
                        'exit_bar': xi, 'stop_price': sp,
                        'contracts': nc, 'stop_hit': False,
                        'entry_date': dates[ei], 'pattern': 'vol_up_oi_down',
                        'signal_date': dates[i+1], 'signal_i': i+1,
                    }
            if pos_p2 is None and sig2(i):
                ei = j
                xi = min(ei + HOLD, n - 2)
                if ei < n - 1:
                    ep = float(opn[ei])
                    sp = ep * (1 - SL)
                    go = ep * CS
                    nc = max(1, int(cash_p2 // go)) if go > 0 else 1
                    pos_p2 = {
                        'entry_bar': ei, 'entry_price': ep,
                        'exit_bar': xi, 'stop_price': sp,
                        'contracts': nc, 'stop_hit': False,
                        'entry_date': dates[ei], 'pattern': 'vol_up_yb_up_fiz_down',
                        'signal_date': dates[i+1], 'signal_i': i+1,
                    }

        # Check p1 position exit
        if pos_p1 is not None and j >= pos_p1['entry_bar']:
            if float(low[j]) <= pos_p1['stop_price'] and j < pos_p1['exit_bar']:
                xp = pos_p1['stop_price']
                gp = pos_p1['contracts'] * CS * (xp - pos_p1['entry_price'])
                cm = pos_p1['contracts'] * COMM
                npnl = gp - cm
                cash_p1 += npnl
                trades_p1.append({
                    'entry': pos_p1['entry_date'],
                    'exit': dates[j],
                    'entry_price': round(pos_p1['entry_price'], 2),
                    'exit_price': round(xp, 2),
                    'contracts': pos_p1['contracts'],
                    'gross_pnl': round(gp, 0),
                    'commission': round(cm, 0),
                    'net_pnl': round(npnl, 0),
                    'stop_hit': True,
                    'bars_held': j - pos_p1['entry_bar'],
                    'pattern': pos_p1['pattern'],
                })
                pos_p1 = None
            elif j >= pos_p1['exit_bar']:
                xp = float(close[pos_p1['exit_bar']])
                gp = pos_p1['contracts'] * CS * (xp - pos_p1['entry_price'])
                cm = pos_p1['contracts'] * COMM
                npnl = gp - cm
                cash_p1 += npnl
                trades_p1.append({
                    'entry': pos_p1['entry_date'],
                    'exit': dates[pos_p1['exit_bar']],
                    'entry_price': round(pos_p1['entry_price'], 2),
                    'exit_price': round(xp, 2),
                    'contracts': pos_p1['contracts'],
                    'gross_pnl': round(gp, 0),
                    'commission': round(cm, 0),
                    'net_pnl': round(npnl, 0),
                    'stop_hit': False,
                    'bars_held': pos_p1['exit_bar'] - pos_p1['entry_bar'],
                    'pattern': pos_p1['pattern'],
                })
                pos_p1 = None

        # Check p2 position exit
        if pos_p2 is not None and j >= pos_p2['entry_bar']:
            if float(low[j]) <= pos_p2['stop_price'] and j < pos_p2['exit_bar']:
                xp = pos_p2['stop_price']
                gp = pos_p2['contracts'] * CS * (xp - pos_p2['entry_price'])
                cm = pos_p2['contracts'] * COMM
                npnl = gp - cm
                cash_p2 += npnl
                trades_p2.append({
                    'entry': pos_p2['entry_date'],
                    'exit': dates[j],
                    'entry_price': round(pos_p2['entry_price'], 2),
                    'exit_price': round(xp, 2),
                    'contracts': pos_p2['contracts'],
                    'gross_pnl': round(gp, 0),
                    'commission': round(cm, 0),
                    'net_pnl': round(npnl, 0),
                    'stop_hit': True,
                    'bars_held': j - pos_p2['entry_bar'],
                    'pattern': pos_p2['pattern'],
                })
                pos_p2 = None
            elif j >= pos_p2['exit_bar']:
                xp = float(close[pos_p2['exit_bar']])
                gp = pos_p2['contracts'] * CS * (xp - pos_p2['entry_price'])
                cm = pos_p2['contracts'] * COMM
                npnl = gp - cm
                cash_p2 += npnl
                trades_p2.append({
                    'entry': pos_p2['entry_date'],
                    'exit': dates[pos_p2['exit_bar']],
                    'entry_price': round(pos_p2['entry_price'], 2),
                    'exit_price': round(xp, 2),
                    'contracts': pos_p2['contracts'],
                    'gross_pnl': round(gp, 0),
                    'commission': round(cm, 0),
                    'net_pnl': round(npnl, 0),
                    'stop_hit': False,
                    'bars_held': pos_p2['exit_bar'] - pos_p2['entry_bar'],
                    'pattern': pos_p2['pattern'],
                })
                pos_p2 = None

        # MTM equity
        mtm_p1 = float(cash_p1)
        if pos_p1 is not None and j >= pos_p1['entry_bar']:
            cur_price = float(close[j])
            mtm_p1 += pos_p1['contracts'] * CS * (cur_price - pos_p1['entry_price'])

        mtm_p2 = float(cash_p2)
        if pos_p2 is not None and j >= pos_p2['entry_bar']:
            cur_price = float(close[j])
            mtm_p2 += pos_p2['contracts'] * CS * (cur_price - pos_p2['entry_price'])

        eq_curve.append((dates[j], mtm_p1, mtm_p2, mtm_p1 + mtm_p2))

    all_trades = trades_p1 + trades_p2
    all_trades.sort(key=lambda t: t['entry'])

    total_ret = (cash_p1 + cash_p2 - CAPITAL) / CAPITAL * 100
    peak = max(ec[3] for ec in eq_curve)
    mdd = 0
    for ec_point in eq_curve:
        dd = (peak - ec_point[3]) / peak * 100
        if dd > mdd:
            mdd = dd
        if ec_point[3] > peak:
            peak = ec_point[3]

    wins = sum(1 for t in all_trades if t['net_pnl'] > 0)
    losses = sum(1 for t in all_trades if t['net_pnl'] <= 0)
    wr = wins / len(all_trades) * 100 if all_trades else 0
    gross_profit = sum(t['net_pnl'] for t in all_trades if t['net_pnl'] > 0)
    gross_loss = sum(t['net_pnl'] for t in all_trades if t['net_pnl'] < 0)
    pf = abs(gross_profit / (gross_loss + 1))
    avg_win = np.mean([t['net_pnl'] for t in all_trades if t['net_pnl'] > 0]) if wins else 0
    avg_loss = np.mean([t['net_pnl'] for t in all_trades if t['net_pnl'] < 0]) if losses else 0
    calmar = total_ret / mdd if mdd > 0 else 0
    sharpe_ratio = 0
    daily_rets = []
    prev_eq = CAPITAL
    for ec in eq_curve:
        dr = (ec[3] - prev_eq) / prev_eq
        daily_rets.append(dr)
        prev_eq = ec[3]
    if np.std(daily_rets) > 0:
        sharpe_ratio = np.mean(daily_rets) / np.std(daily_rets) * np.sqrt(252)

    fold_results.append({
        'fold': f + 1,
        'start': dates[s],
        'end': dates[min(e - 1, n - 1)],
        'ret': round(total_ret, 2),
        'mdd': round(mdd, 2),
        'trades': len(all_trades),
        'wins': wins,
        'losses': losses,
        'wr': round(wr, 1),
        'pf': round(pf, 2),
        'avg_win': round(avg_win, 0),
        'avg_loss': round(avg_loss, 0),
        'calmar': round(calmar, 2),
        'sharpe': round(sharpe_ratio, 2),
        'gross_profit': round(gross_profit, 0),
        'gross_loss': round(gross_loss, 0),
        'net_pnl': round(gross_profit + gross_loss, 0),
        'total_commission': sum(t['commission'] for t in all_trades),
        'trades_p1': len(trades_p1),
        'trades_p2': len(trades_p2),
        'trade_list': all_trades,
        'eq_curve': eq_curve,
        'ret_p1': round((cash_p1 - CAPITAL // 2) / (CAPITAL // 2) * 100, 2),
        'ret_p2': round((cash_p2 - CAPITAL // 2) / (CAPITAL // 2) * 100, 2),
    })

    dstart = fold_results[-1]['start']
    dend = fold_results[-1]['end']
    print(f'Fold {f+1} ({dstart} – {dend}):')
    print(f'  Сделок: {len(all_trades)} (p1={len(trades_p1)}, p2={len(trades_p2)})')
    print(f'  Доходность: {total_ret:+.2f}% (p1: {fold_results[-1]["ret_p1"]:+.2f}%, p2: {fold_results[-1]["ret_p2"]:+.2f}%)')
    print(f'  Max DD: {mdd:.2f}%')
    print(f'  WR: {wr:.1f}% | PF: {pf:.2f} | Calmar: {calmar:.2f} | Sharpe: {sharpe_ratio:.2f}')
    print(f'  Gross+ : {gross_profit:+.0f} | Gross- : {gross_loss:+.0f} | Net: {gross_profit+gross_loss:+.0f}')
    print(f'  Avg win: {avg_win:.0f} | Avg loss: {avg_loss:.0f}')
    print(f'  Комиссий: {sum(t["commission"] for t in all_trades):.0f}')

    sorted_t = sorted(all_trades, key=lambda x: -x['net_pnl'])
    print(f'  Топ-5 лучших:')
    for t in sorted_t[:5]:
        print(f'    {t["entry"]}→{t["exit"]} ep={t["entry_price"]:.0f} xp={t["exit_price"]:.0f} '
              f'pnl={t["net_pnl"]:+.0f} n={t["contracts"]} stop={t["stop_hit"]} [{t["pattern"]}]')
    print(f'  Топ-5 худших:')
    for t in sorted_t[-5:]:
        print(f'    {t["entry"]}→{t["exit"]} ep={t["entry_price"]:.0f} xp={t["exit_price"]:.0f} '
              f'pnl={t["net_pnl"]:+.0f} n={t["contracts"]} stop={t["stop_hit"]} [{t["pattern"]}]')
    print()

# Write report
lines = []
lines.append(f'# Отчёт по GD портфелю')
lines.append(f'')
lines.append(f'**Параметры:** mode=`{MODE}`, hold=`{HOLD}`, sl=`{SL:.0%}`, capital=`{CAPITAL:,} RUB`')
lines.append(f'**Данные:** {dates[0]} — {dates[-1]} ({n} дней)')
lines.append(f'**Инструмент:** GD (Gold, CS={CS}, комиссия={COMM} RUB/контракт)')
lines.append(f'**Подход:** Два субпортфеля по {CAPITAL//2:,} RUB, каждый торгует свой паттерн независимо')
lines.append(f'')
lines.append(f'---')
lines.append(f'')

overall_pnl = sum(fr['net_pnl'] for fr in fold_results)
overall_comm = sum(fr['total_commission'] for fr in fold_results)
overall_trades = sum(fr['trades'] for fr in fold_results)
overall_wins = sum(fr['wins'] for fr in fold_results)
overall_losses = sum(fr['losses'] for fr in fold_results)
overall_wr = overall_wins / overall_trades * 100 if overall_trades else 0
avg_ret = np.mean([fr['ret'] for fr in fold_results])
avg_dd = np.mean([fr['mdd'] for fr in fold_results])
min_ret = min(fr['ret'] for fr in fold_results)
max_dd = max(fr['mdd'] for fr in fold_results)

lines.append(f'## Сводка по фолдам')
lines.append(f'')
lines.append(f'| Фолд | Период | Сделок | Доходность | Max DD | WR | PF | Calmar | Sharpe |')
lines.append(f'|------|--------|--------|------------|--------|----|----|--------|--------|')
for fr in fold_results:
    lines.append(f'| {fr["fold"]} | {fr["start"]}–{fr["end"]} | {fr["trades"]} '
                 f'| {fr["ret"]:+.2f}% | {fr["mdd"]:.2f}% | {fr["wr"]:.1f}% | {fr["pf"]:.2f} | {fr["calmar"]:.2f} | {fr["sharpe"]:.2f} |')
lines.append(f'| **Σ/μ** | | **{overall_trades}** | **{avg_ret:+.2f}%** | **{avg_dd:.2f}%** | **{overall_wr:.1f}%** | | | |')
lines.append(f'')
lines.append(f'- **Min ret:** {min_ret:+.2f}% | **Max DD:** {max_dd:.2f}%')
lines.append(f'- **Общий PnL:** {overall_pnl:+.0f} RUB | **Комиссий:** {overall_comm:.0f} RUB')
lines.append(f'- **Всего сделок:** {overall_trades} (wins: {overall_wins}, losses: {overall_losses})')
lines.append(f'')

for fr in fold_results:
    lines.append(f'---')
    lines.append(f'')
    lines.append(f'## Фолд {fr["fold"]}: {fr["start"]} — {fr["end"]}')
    lines.append(f'')
    lines.append(f'### Параметры фолда')
    lines.append(f'- Сделок: {fr["trades"]} (p1: {fr["trades_p1"]}, p2: {fr["trades_p2"]})')
    lines.append(f'- Доходность портфеля: {fr["ret"]:+.2f}%')
    lines.append(f'- Доходность p1 (vol_up_oi_down): {fr["ret_p1"]:+.2f}%')
    lines.append(f'- Доходность p2 (vol_up_yb_up_fiz_down): {fr["ret_p2"]:+.2f}%')
    lines.append(f'- Max Drawdown: {fr["mdd"]:.2f}%')
    lines.append(f'- Win Rate: {fr["wr"]:.1f}% ({fr["wins"]}/{fr["trades"]})')
    lines.append(f'- Profit Factor: {fr["pf"]:.2f}')
    lines.append(f'- Calmar Ratio: {fr["calmar"]:.2f}')
    lines.append(f'- Sharpe Ratio (год.): {fr["sharpe"]:.2f}')
    lines.append(f'- Gross Profit: {fr["gross_profit"]:+.0f} RUB')
    lines.append(f'- Gross Loss: {fr["gross_loss"]:+.0f} RUB')
    lines.append(f'- Net PnL: {fr["net_pnl"]:+.0f} RUB')
    lines.append(f'- Avg Win: {fr["avg_win"]:.0f} RUB')
    lines.append(f'- Avg Loss: {fr["avg_loss"]:.0f} RUB')
    lines.append(f'- Комиссий: {fr["total_commission"]:.0f} RUB')
    lines.append(f'')

    lines.append(f'### Кривая капитала (MTM, ежедневная)')
    eq = fr['eq_curve']
    for chunk_start in range(0, len(eq), 30):
        chunk = eq[chunk_start:chunk_start+30]
        lines.append(f'| Дата | p1 | p2 | Итого |')
        lines.append(f'|------|----|----|-------|')
        for e in chunk:
            lines.append(f'| {e[0]} | {e[1]:.0f} | {e[2]:.0f} | {e[3]:.0f} |')
        if chunk_start + 30 < len(eq):
            lines.append(f'| ... | ... | ... | ... |')
    lines.append(f'')

    lines.append(f'### Статистика просадок')
    eq_vals = [e[3] for e in eq]
    peak = eq_vals[0]
    dd_start = None
    dd_end = None
    current_dd = 0
    dd_periods = []
    for idx, val in enumerate(eq_vals):
        if val > peak:
            peak = val
            if current_dd > 0 and dd_start is not None:
                dd_end = idx
                dd_periods.append((dd_start, dd_end, current_dd))
                current_dd = 0
                dd_start = None
        else:
            dd = (peak - val) / peak * 100
            if dd > current_dd:
                current_dd = dd
                if dd_start is None:
                    dd_start = idx
            else:
                pass
    if current_dd > 0 and dd_start is not None:
        dd_periods.append((dd_start, len(eq_vals)-1, current_dd))

    dd_periods.sort(key=lambda x: -x[2])
    lines.append(f'**Топ-5 просадок:**')
    lines.append(f'| # | Начало | Конец | Длина (дни) | Глубина |')
    lines.append(f'|---|--------|-------|-------------|---------|')
    for rank, (ds, de, dd) in enumerate(dd_periods[:5], 1):
        lines.append(f'| {rank} | {eq[ds][0]} | {eq[de][0]} | {de-ds} | {dd:.2f}% |')
    lines.append(f'')

    lines.append(f'### Список сделок')
    lines.append(f'')
    lines.append(f'| # | Паттерн | Вход | Выход | EP | XP | Контр. | Gross PnL | Comm | Net PnL | Stop | Бар. |')
    lines.append(f'|---|---------|------|-------|----|----|--------|-----------|------|---------|------|------|')
    for rank, t in enumerate(fr['trade_list'], 1):
        lines.append(f'| {rank} | {t["pattern"]} | {t["entry"]} | {t["exit"]} | {t["entry_price"]:.2f} | {t["exit_price"]:.2f} '
                     f'| {t["contracts"]} | {t["gross_pnl"]:+.0f} | {t["commission"]:.0f} | {t["net_pnl"]:+.0f} '
                     f'| {"Y" if t["stop_hit"] else "N"} | {t["bars_held"]} |')
    lines.append(f'')

# Summary
lines.append(f'---')
lines.append(f'')
lines.append(f'## Итоговое заключение')
lines.append(f'')
ret_by_fold = [fr['ret'] for fr in fold_results]
dd_by_fold = [fr['mdd'] for fr in fold_results]
trades_by_fold = [fr['trades'] for fr in fold_results]
lines.append(f'- **Средняя доходность по фолдам:** {np.mean(ret_by_fold):+.2f}%')
lines.append(f'- **Средняя макс. просадка:** {np.mean(dd_by_fold):.2f}%')
lines.append(f'- **Среднее количество сделок:** {np.mean(trades_by_fold):.0f}')
lines.append(f'- **Всего сделок:** {overall_trades}')
lines.append(f'- **Общий PnL:** {overall_pnl:+.0f} RUB')
lines.append(f'- **Общая комиссия:** {overall_comm:.0f} RUB')
lines.append(f'- **Общая WR:** {overall_wr:.1f}%')
lines.append(f'- **Доходности по фолдам:** {[f"{r:+.2f}%" for r in ret_by_fold]}')
lines.append(f'- **Просадки по фолдам:** {[f"{d:.2f}%" for d in dd_by_fold]}')
lines.append(f'')

os.makedirs('reports', exist_ok=True)
with open('reports/gd_portfolio_report.md', 'w') as f:
    f.write('\n'.join(lines))

print(f'Отчёт сохранён: reports/gd_portfolio_report.md')
