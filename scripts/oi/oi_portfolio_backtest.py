import sys, numpy as np, bisect
sys.path.insert(0, "/home/user/projects/TQA-MOEX-futures")
import clickhouse_connect as cc
from datetime import timedelta

ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')

# OI-портфель: SV, BR, RN, NG, LKOH
# Сигнал: после 19:00, физ накопили продажи (day_net_fiz < threshold)
# Вход: long, выход через 120 мин
# Sizing: риск % капитала на сделку, контроль ГО (общая маржа <= 80% equity)

SPECS = {
    'SV': {'ms': 0.01, 'sp': 7.9357, 'go': 9867, 'fee': 4.0, 'thr': -5.0},
    'BR': {'ms': 0.01, 'sp': 7.70611, 'go': 8620, 'fee': 4.0, 'thr': -7.0},
    'NG': {'ms': 0.001, 'sp': 7.70611, 'go': 11974, 'fee': 4.0, 'thr': -5.0},
}

def load_data(ticker):
    rows = ch.query(f"SELECT bt, prc FROM moex.mt5_continuous WHERE ticker='{ticker}' AND bt>='2024-01-01' ORDER BY bt").result_rows
    p_ts = [r[0] for r in rows]; p_prc = [float(r[1]) for r in rows]
    ft = 'LK' if ticker == 'LKOH' else ticker
    oi_rows = ch.query(f"SELECT bt,buy_fiz,sell_fiz,buy_yur,sell_yur FROM moex.futoi WHERE ticker='{ft}' AND bt>='2024-01-01' ORDER BY bt").result_rows
    return p_ts, p_prc, oi_rows

def simulate(risk_pct, max_margin_pct, tickers):
    """Бэктест OI-портфеля. Сигналы на всех тикерах, common pool."""
    # Предрасчёт сделок по тикерам
    all_signals = {}  # ticker -> list of (ts_entry, ret_pct)
    daily_data = {}
    
    for ticker in tickers:
        p_ts, p_prc, oi_rows = load_data(ticker)
        # Дневной старт net_fiz
        daily_open = {}
        for r in oi_rows:
            day = r[0].date()
            if day not in daily_open:
                daily_open[day] = int(r[1]) - int(r[2])
        thr = SPECS[ticker]['thr']
        
        signals = []
        for i in range(len(oi_rows)):
            bt, fb, fs, yb, ys = oi_rows[i]
            day = bt.date()
            if day not in daily_open: continue
            if bt.hour < 19: continue
            total = fb+fs+yb+ys
            if total <= 0: continue
            day_net = ((fb-fs) - daily_open[day]) / total * 100
            if day_net > thr: continue
            idx = bisect.bisect_right(p_ts, bt) - 1
            if idx < 0: continue
            cur = p_prc[idx]
            if cur <= 0: continue
            ft = bt + timedelta(minutes=120)
            j = bisect.bisect_left(p_ts, ft)
            if j >= len(p_ts): continue
            ret = (p_prc[j] - cur) / cur * 100
            signals.append((bt, idx, cur, ret, day_net))
        daily_data[ticker] = (p_ts, p_prc, signals)
    
    # Симуляция
    equity = 200000.0
    peak = equity
    max_dd = 0.0
    trades_done = 0
    
    # Идём по времени: события = сигналы (вход) + выходы (+120 мин)
    events = []
    for ticker in tickers:
        p_ts, p_prc, signals = daily_data[ticker]
        for si, (bt, idx, cur, ret, dn) in enumerate(signals):
            exit_ts = bt + timedelta(minutes=120)
            events.append((bt, 'OPEN', ticker, si))
            events.append((exit_ts, 'CLOSE', ticker, si))
    events.sort(key=lambda x: (x[0], 0 if x[1]=='CLOSE' else 1))  # CLOSE раньше OPEN
    
    positions = {}  # ticker -> {si, entry_idx, entry_price, shares, notional}
    used_margin = 0.0
    open_count = 0
    
    for ts, kind, ticker, si in events:
        if kind == 'CLOSE':
            p = positions.pop(ticker, None)
            if p:
                # exit price: цена на момент close_ts
                p_ts, p_prc, signals = daily_data[ticker]
                _, _, _, ret, _ = signals[si]
                pnl_pct = ret
                # PnL: движение цены в тиках * шаг-цена * контракты - комиссия
                ms = SPECS[ticker]['ms']; sp = SPECS[ticker]['sp']
                dp = p['entry_price'] * ret / 100  # движение в рублях
                pnl_rub = p['shares'] * (dp / ms * sp) - SPECS[ticker]['fee'] * p['shares']
                equity += pnl_rub
                used_margin -= p['margin']
                open_count -= 1
                trades_done += 1
        else:  # OPEN
            if ticker in positions: continue  # уже открыт
            p_ts, p_prc, signals = daily_data[ticker]
            bt, idx, cur, ret, dn = signals[si]
            go = SPECS[ticker]['go']
            ms = SPECS[ticker]['ms']
            # Количество контрактов по риску
            risk_amount = equity * risk_pct
            shares = max(1, int(risk_amount / go))
            margin = shares * go
            # Контроль маржи: суммарная ГО <= max_margin_pct * equity
            if used_margin + margin > max_margin_pct * equity:
                continue  # пропуск — нет маржи
            if shares < 1: continue
            equity -= 0  # маржа не списывается из equity (залог)
            used_margin += margin
            open_count += 1
            positions[ticker] = {'si': si, 'margin': margin, 'shares': shares,
                                 'entry_price': cur}
        
        # MTM с unrealized: для каждой открытой позиции берём ret на момент события ts
        mtm_equity = equity
        for tkr, p in positions.items():
            p_ts, p_prc, signals = daily_data[tkr]
            bt, idx, cur, ret, dn = signals[p['si']]
            # ret уже посчитан к моменту выхода (bt+120). Для unrealized на ts —
            # интерполируем: если ts между входом и выходом, цена движется к exit.
            # Грубая аппроксимация: доля пути = (ts - вход)/(выход - вход)
            in_ts = bt
            ex_ts = bt + timedelta(minutes=120)
            frac = min(1.0, max(0.0, (ts - in_ts).total_seconds() / (ex_ts - in_ts).total_seconds()))
            unreal_ret = ret * frac
            ms = SPECS[tkr]['ms']; sp = SPECS[tkr]['sp']
            dp = p['entry_price'] * unreal_ret / 100
            mtm_equity += p['shares'] * (dp / ms * sp)
        if mtm_equity > peak: peak = mtm_equity
        dd = (peak - mtm_equity) / peak * 100
        if dd > max_dd: max_dd = dd
    
    ret_total = (equity / 200000 - 1) * 100
    return ret_total, max_dd, trades_done

# Тесты: риск 2%, 5%, 10% × маржа 50%, 70%
print(f"{'risk':>5} {'margin':>7} {'ROI':>12} {'MDD':>8} {'сдел':>6}")
for rp in [0.02, 0.05, 0.10]:
    for mp in [0.5, 0.7]:
        roi, dd, n = simulate(rp, mp, ['SV','BR','NG'])
        print(f"{rp:>5.0%} {mp:>7.0%} {roi:>+11.1f}% {dd:>7.1f}% {n:>6}")

ch.close()
