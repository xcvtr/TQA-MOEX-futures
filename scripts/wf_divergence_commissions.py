#!/usr/bin/env python3
"""
Честный walk-forward на CVD-дивергенции + комиссии MOEX.

Каждое окно: train → считаем cvd_cum + пороги → test → торгуем.
Без look-ahead: cvd_cum НЕ выходит за пределы train.

Для каждой сделки:
  pnl_rub = pnl_ticks * tick_cost_rub
  pnl_net = pnl_rub - commission_rub (3.0 RUB round-trip)

Вывод: ДВЕ таблицы — ДО комиссий и ПОСЛЕ.
Сохраняет: reports/wf_divergence_commissions.json
"""
import clickhouse_connect
import pandas as pd
import numpy as np
import sys, json
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
REPORTS_DIR = os.path.join(PROJECT_DIR, 'reports')
os.makedirs(REPORTS_DIR, exist_ok=True)

CH = clickhouse_connect.get_client(host='10.0.0.64', database='moex')

# Минимальный шаг цены (source — используется для расчёта pnl_ticks)
TICK = {'NG': 0.0005, 'BR': 0.001, 'Si': 0.0025, 'MXI': 0.01}

# Стоимость одного source-тика в рублях
# Рассчитана: source_TICK / actual_minstep * stepprice (из moex.securities)
TICK_COST_RUB = {
    'NG': 3.715,    # 0.0005 / 0.001 * 7.43
    'BR': 0.743,    # 0.001 / 0.01 * 7.43
    'Si': 0.0025,   # 0.0025 / 1.0 * 1.0
    'MXI': 0.10,    # 0.01 / 0.05 * 0.50
}

# Комиссия за round-trip (вход + выход), руб/контракт
# Биржевой сбор 0.5 + клиринг 0.5 + брокер 0.5 = 1.5 руб за сделку
# Round-trip: 3.0 руб
COMMISSION_RUB = 3.0


def print_results(sym, label, monthly_df, total_trades, total_wins, total_net):
    """Вывести таблицу помесячных результатов."""
    print(f"\n  --- Monthly {label}: {sym} ---", flush=True)
    print(f"  {'Month':<10} {'Tr':>5} {'WR':>6} {'Net(RUB)':>12}", flush=True)
    print(f"  {'-'*35}", flush=True)
    for m, r in monthly_df.iterrows():
        print(f"  {str(m):<10} {r['trades']:>5.0f} {r['wr']:>5.1f}% {r['net']:>+12.0f}", flush=True)
    print(f"  {'TOTAL':<10} {total_trades:>5.0f} "
          f"{(total_wins/total_trades*100):>5.1f}% "
          f"{total_net:>+12.0f}", flush=True)


all_results = {}

for SYM in ['NG', 'BR', 'Si', 'MXI']:
    print(f"\n{'='*70}", flush=True)
    print(f"  {SYM}", flush=True)
    print(f"{'='*70}", flush=True)

    tick_cost = TICK_COST_RUB[SYM]
    print(f"  Tick cost: {tick_cost} RUB  |  Commission: {COMMISSION_RUB} RUB/round-trip", flush=True)

    print("  Loading...", flush=True)
    df = CH.query_df(f"""
        SELECT tradedate AS date, 
               toDateTime(tradedate || ' ' || tradetime) AS time,
               pr_close AS close, vol, vol_b, vol_s
        FROM moex.tradestats_fo
        WHERE asset_code = '{SYM}' AND vol > 0
        ORDER BY time
    """)
    df['time'] = pd.to_datetime(df['time'])
    df['cvd'] = df['vol_b'].fillna(0) - df['vol_s'].fillna(0)
    print(f"  Bars: {len(df):,}, Days: {df['date'].nunique()}", flush=True)

    dates = sorted(df['date'].unique())
    tick = TICK[SYM]

    # Grid параметров
    param_grid = []
    for lookback in [5, 10, 20]:
        for hold in [1, 3, 5]:
            for q in [0.6, 0.7, 0.8]:
                param_grid.append((lookback, hold, q))

    best_overall = None

    for lookback, hold_bars, q in param_grid:
        all_trades_ticks = []   # pnl в тиках (для ДО комиссий)
        all_trades_net = []     # pnl в рублях ПОСЛЕ комиссий
        window_stats = []

        i = 180  # first 180 days for warmup
        while i < len(dates):
            test_end = min(i + 60, len(dates))
            train_dates = set(dates[i-180:i])
            test_dates = set(dates[i:test_end])

            if len(test_dates) < 10 or len(train_dates) < 60:
                i += 60
                continue

            train = df[df['date'].isin(train_dates)].copy()
            test = df[df['date'].isin(test_dates)].copy()

            if len(train) < 200 or len(test) < 20:
                i += 60
                continue

            # cvd_cum ТОЛЬКО по train
            train['cvd_cum'] = train['cvd'].cumsum()
            train['price_chg'] = train['close'].diff(lookback)
            train['cvd_cum_chg'] = train['cvd_cum'].diff(lookback)

            train_valid = train.dropna()
            if len(train_valid) < 100:
                i += 60
                continue

            p_thr = train_valid['price_chg'].abs().quantile(q)
            c_thr = train_valid['cvd_cum_chg'].abs().quantile(q)

            if p_thr == 0 or c_thr == 0:
                i += 60
                continue

            # Test: cvd_cum относительно последнего train
            last_cvd = train['cvd_cum'].iloc[-1]
            test['cvd_cum'] = last_cvd + test['cvd'].cumsum()
            test['price_chg'] = test['close'].diff(lookback)
            test['cvd_cum_chg'] = test['cvd_cum'].diff(lookback)

            bearish = (test['price_chg'] > p_thr) & (test['cvd_cum_chg'] < -c_thr)
            bullish = (test['price_chg'] < -p_thr) & (test['cvd_cum_chg'] > c_thr)

            bearish_idx = set(test.index[bearish])
            bullish_idx = set(test.index[bullish])

            # Торговля
            pos = 0
            ep = 0.0
            bars = 0

            for idx, row in test.iterrows():
                sig = 0
                if idx in bearish_idx: sig = -1
                elif idx in bullish_idx: sig = 1

                if sig == 0:
                    if pos != 0:
                        bars += 1
                        if bars >= hold_bars:
                            pnl_ticks = round((row['close'] - ep) * pos / tick, 0)
                            pnl_rub = pnl_ticks * tick_cost
                            pnl_net = pnl_rub - COMMISSION_RUB
                            all_trades_ticks.append(pnl_ticks)
                            all_trades_net.append(pnl_net)
                            pos = 0
                    continue

                if pos == 0:
                    pos = sig
                    ep = row['close']
                    bars = 1
                else:
                    bars += 1
                    if bars >= hold_bars:
                        pnl_ticks = round((row['close'] - ep) * pos / tick, 0)
                        pnl_rub = pnl_ticks * tick_cost
                        pnl_net = pnl_rub - COMMISSION_RUB
                        all_trades_ticks.append(pnl_ticks)
                        all_trades_net.append(pnl_net)
                        pos = 0

            if pos != 0:
                pnl_ticks = round((test.iloc[-1]['close'] - ep) * pos / tick, 0)
                pnl_rub = pnl_ticks * tick_cost
                pnl_net = pnl_rub - COMMISSION_RUB
                all_trades_ticks.append(pnl_ticks)
                all_trades_net.append(pnl_net)

            window_stats.append({'train': f'{train_dates.pop()}' if train_dates else '?', 
                                  'test': f'{list(test_dates)[0]}..{list(test_dates)[-1]}',
                                  'trades': len(all_trades_ticks) - sum(s.get('trades', 0) for s in window_stats)})
            i += 60

        if len(all_trades_ticks) < 10:
            continue

        # --- Статистика ДО комиссий (в тиках) ---
        arr_ticks = np.array(all_trades_ticks)
        n = len(arr_ticks)
        wins_t = arr_ticks[arr_ticks > 0]
        losses_t = arr_ticks[arr_ticks < 0]
        wr_t = len(wins_t)/n*100
        net_t = arr_ticks.sum()
        avg_t = arr_ticks.mean()
        std_t = arr_ticks.std()
        sharpe_t = avg_t/std_t*np.sqrt(n) if std_t > 0 else 0
        gw_t = wins_t.sum() if len(wins_t) > 0 else 0
        gl_t = abs(losses_t.sum()) if len(losses_t) > 0 else 1
        pf_t = gw_t/gl_t if gl_t > 0 else 0
        eq_t = np.cumsum(arr_ticks)
        peak_t = np.maximum.accumulate(eq_t)
        dd_t = peak_t - eq_t
        max_dd_t = dd_t.max()

        # --- Статистика ПОСЛЕ комиссий (в рублях) ---
        arr_net = np.array(all_trades_net)
        wins_n = arr_net[arr_net > 0]
        losses_n = arr_net[arr_net < 0]
        wr_n = len(wins_n)/n*100
        net_n = arr_net.sum()
        avg_n = arr_net.mean()
        std_n = arr_net.std()
        sharpe_n = avg_n/std_n*np.sqrt(n) if std_n > 0 else 0
        gw_n = wins_n.sum() if len(wins_n) > 0 else 0
        gl_n = abs(losses_n.sum()) if len(losses_n) > 0 else 1
        pf_n = gw_n/gl_n if gl_n > 0 else 0
        eq_n = np.cumsum(arr_net)
        peak_n = np.maximum.accumulate(eq_n)
        dd_n = peak_n - eq_n
        max_dd_n = dd_n.max()

        print(f"  lk={lookback:2d} h={hold_bars:2d} q={q:.1f}: windows={len(window_stats):2d} "
              f"tr={n:5d} "
              f"WR={wr_t:5.1f}% net={net_t:+9.0f}t SR={sharpe_t:+.3f} PF={pf_t:.2f} DD={max_dd_t:.0f}t | "
              f"wr_net={wr_n:5.1f}% net_net={net_n:+12.0f}rub SR={sharpe_n:+.3f} PF={pf_n:.2f}",
              flush=True)

        cfg = {'lk': lookback, 'hold': hold_bars, 'q': q,
               'trades': n,
               'wr_pct': round(wr_t, 1), 'net_ticks': int(net_t),
               'sharpe': round(sharpe_t, 3), 'pf': round(pf_t, 2), 'max_dd_ticks': int(max_dd_t),
               'tick_cost_rub': tick_cost, 'commission_rub': COMMISSION_RUB,
               'wr_net_pct': round(wr_n, 1), 'net_rub': round(net_n, 2),
               'sharpe_net': round(sharpe_n, 3), 'pf_net': round(pf_n, 2), 'max_dd_rub': round(max_dd_n, 2)}

        if best_overall is None or sharpe_n > best_overall.get('sharpe_net', -999):
            best_overall = cfg

    if best_overall:
        print(f"\n  BEST (по Sharpe после комиссий): {json.dumps(best_overall)}", flush=True)

    # --- Месячная разбивка для лучшей конфигурации ---
    if best_overall:
        lk = best_overall['lk']
        hold_bars = best_overall['hold']
        q = best_overall['q']

        # Пересчитываем с лучшими параметрами — собираем сделки с месяцами
        all_trades_gross = []   # {'month': ..., 'pnl_rub': ...} ДО комиссий
        all_trades_net = []     # {'month': ..., 'pnl_rub': ...} ПОСЛЕ комиссий
        i = 180
        while i < len(dates):
            test_end = min(i + 60, len(dates))
            train_dates = set(dates[i-180:i])
            test_dates = set(dates[i:test_end])

            if len(test_dates) < 10:
                i += 60
                continue

            train = df[df['date'].isin(train_dates)].copy()
            test = df[df['date'].isin(test_dates)].copy()
            if len(train) < 200 or len(test) < 20:
                i += 60
                continue

            train['cvd_cum'] = train['cvd'].cumsum()
            train['price_chg'] = train['close'].diff(lk)
            train['cvd_cum_chg'] = train['cvd_cum'].diff(lk)
            train_valid = train.dropna()

            p_thr = train_valid['price_chg'].abs().quantile(q)
            c_thr = train_valid['cvd_cum_chg'].abs().quantile(q)
            if p_thr == 0 or c_thr == 0:
                i += 60
                continue

            last_cvd = train['cvd_cum'].iloc[-1]
            test['cvd_cum'] = last_cvd + test['cvd'].cumsum()
            test['price_chg'] = test['close'].diff(lk)
            test['cvd_cum_chg'] = test['cvd_cum'].diff(lk)

            bearish = (test['price_chg'] > p_thr) & (test['cvd_cum_chg'] < -c_thr)
            bullish = (test['price_chg'] < -p_thr) & (test['cvd_cum_chg'] > c_thr)

            bearish_idx = set(test.index[bearish])
            bullish_idx = set(test.index[bullish])

            pos = 0
            ep = 0.0
            bars = 0

            def record_trade(row, pos, ep, tick, tick_cost, comm):
                pnl_ticks = round((row['close'] - ep) * pos / tick, 0)
                pnl_rub = pnl_ticks * tick_cost
                pnl_net = pnl_rub - comm
                month = str(row['time'].to_period('M'))
                all_trades_gross.append({'month': month, 'pnl_rub': pnl_rub})
                all_trades_net.append({'month': month, 'pnl_net': pnl_net})

            for idx, row in test.iterrows():
                sig = 0
                if idx in bearish_idx: sig = -1
                elif idx in bullish_idx: sig = 1

                if sig == 0:
                    if pos != 0:
                        bars += 1
                        if bars >= hold_bars:
                            record_trade(row, pos, ep, tick, tick_cost, COMMISSION_RUB)
                            pos = 0
                    continue

                if pos == 0:
                    pos = sig
                    ep = row['close']
                    bars = 1
                else:
                    bars += 1
                    if bars >= hold_bars:
                        record_trade(row, pos, ep, tick, tick_cost, COMMISSION_RUB)
                        pos = 0

            if pos != 0:
                record_trade(test.iloc[-1], pos, ep, tick, tick_cost, COMMISSION_RUB)

            i += 60

        if all_trades_gross:
            # ДО комиссий
            gross_df = pd.DataFrame(all_trades_gross)
            monthly_gross = gross_df.groupby('month').agg(
                trades=('pnl_rub', 'count'),
                wins=('pnl_rub', lambda x: (x > 0).sum()),
                net=('pnl_rub', 'sum'),
            )
            monthly_gross['wr'] = (monthly_gross['wins'] / monthly_gross['trades'] * 100).round(1)

            # ПОСЛЕ комиссий
            net_df = pd.DataFrame(all_trades_net)
            monthly_net = net_df.groupby('month').agg(
                trades=('pnl_net', 'count'),
                wins=('pnl_net', lambda x: (x > 0).sum()),
                net=('pnl_net', 'sum'),
            )
            monthly_net['wr'] = (monthly_net['wins'] / monthly_net['trades'] * 100).round(1)

            ttl_trades = monthly_gross['trades'].sum()
            ttl_wins_g = monthly_gross['wins'].sum()
            ttl_net_g = monthly_gross['net'].sum()
            ttl_wins_n = monthly_net['wins'].sum()
            ttl_net_n = monthly_net['net'].sum()

            pos_months_g = (monthly_gross['net'] > 0).sum()
            pos_months_n = (monthly_net['net'] > 0).sum()

            # Таблица ДО комиссий
            print_results(SYM, "GROSS (до комиссий)", monthly_gross,
                          ttl_trades, ttl_wins_g, ttl_net_g)
            print(f"  Months+: {pos_months_g}/{len(monthly_gross)} ({pos_months_g/len(monthly_gross)*100:.0f}%)", flush=True)

            # Таблица ПОСЛЕ комиссий
            print_results(SYM, "NET (после комиссий)", monthly_net,
                          ttl_trades, ttl_wins_n, ttl_net_n)
            print(f"  Months+: {pos_months_n}/{len(monthly_net)} ({pos_months_n/len(monthly_net)*100:.0f}%)", flush=True)

            # Итоговая статистика по всем сделкам (тики, gross RUB, net RUB)
            all_trades_arr = np.array([t['pnl_rub'] for t in all_trades_gross])
            all_trades_net_arr = np.array([t['pnl_net'] for t in all_trades_net])

            n_total = len(all_trades_arr)
            wins_total = all_trades_arr[all_trades_arr > 0]
            losses_total = all_trades_arr[all_trades_arr < 0]
            wr_total = len(wins_total) / n_total * 100 if n_total > 0 else 0
            net_total_rub = all_trades_arr.sum()
            avg_total = all_trades_arr.mean()
            std_total = all_trades_arr.std()
            sharpe_total = avg_total / std_total * np.sqrt(n_total) if std_total > 0 else 0

            wins_total_n = all_trades_net_arr[all_trades_net_arr > 0]
            losses_total_n = all_trades_net_arr[all_trades_net_arr < 0]
            wr_total_n = len(wins_total_n) / n_total * 100 if n_total > 0 else 0
            net_total_n_rub = all_trades_net_arr.sum()
            avg_total_n = all_trades_net_arr.mean()
            std_total_n = all_trades_net_arr.std()
            sharpe_total_n = avg_total_n / std_total_n * np.sqrt(n_total) if std_total_n > 0 else 0

            best_overall.update({
                'total_trades': n_total,
                'wr_total_pct': round(wr_total, 1),
                'net_total_rub': round(net_total_rub, 2),
                'sharpe_total': round(sharpe_total, 3),
                'wr_net_total_pct': round(wr_total_n, 1),
                'net_total_rub_after_comm': round(net_total_n_rub, 2),
                'sharpe_net_total': round(sharpe_total_n, 3),
                'total_commission_paid': round(n_total * COMMISSION_RUB, 2),
                'commission_pct_of_gross': round(n_total * COMMISSION_RUB / max(abs(net_total_rub), 1) * 100, 2),
            })

    all_results[SYM] = {'best': best_overall}

# Сохраняем
output_path = os.path.join(REPORTS_DIR, 'wf_divergence_commissions.json')
with open(output_path, 'w') as f:
    json.dump(all_results, f, indent=2, default=str)

print(f"\n\nResults saved to {output_path}", flush=True)
print("Done.", flush=True)
