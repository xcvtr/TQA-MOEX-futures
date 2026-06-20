#!/usr/bin/env python3
"""
SHORT-сигналы для BASE v2.
Симметричный score: [-1, 1], abs(score) > 0.10 = сигнал,
знак = направление.

Сравнение: только LONG vs LONG+SHORT на всём портфеле.
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
import clickhouse_connect
from bar_level_sim import TICKER_CONFIGS
from config import CH_HOST, CH_PORT, CH_DB

INITIAL = 100_000
SYMBOLS = ['GL', 'HS', 'HY', 'RN', 'NM', 'AF']
START = '2024-01-01'
END = '2026-05-01'


def rz(s, w=20):
    m = s.rolling(w, min_periods=w).mean()
    std = s.rolling(w, min_periods=w).std()
    return (s - m) / std.clip(lower=1e-10)


def calc_atr(df, p=14):
    prev = df['close'].shift(1)
    tr = pd.concat([df['high']-df['low'],(df['high']-prev).abs(),(df['low']-prev).abs()],axis=1).max(axis=1)
    return tr.rolling(p, min_periods=p).mean().bfill().fillna(0)


def load_data(sym):
    ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)
    q=f"""
        SELECT p.time,p.open,p.high,p.low,p.close,p.volume,
               o.fiz_buy,o.fiz_sell,o.yur_buy,o.yur_sell,o.total_oi
        FROM moex.prices_5m p
        LEFT JOIN moex.prices_5m_oi o ON p.time=o.time AND p.symbol=o.symbol
        WHERE p.symbol='{sym}' AND p.time>='2023-01-01' AND p.time<='2026-05-01'
        ORDER BY p.time
    """
    r = ch.query(q)
    cols=['time','open','high','low','close','volume','fiz_buy','fiz_sell','yur_buy','yur_sell','total_oi']
    df = pd.DataFrame(r.result_rows, columns=cols)
    df['time'] = pd.to_datetime(df['time']).dt.tz_localize(None)
    df.set_index('time', inplace=True)
    return df


def load_accounts(sym):
    ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)
    q=f"""
        SELECT time,clgroup,buy_accounts,sell_accounts
        FROM moex.openinterest
        WHERE symbol='{sym}' AND time>='2023-01-01' AND time<='2026-05-01'
        ORDER BY time,clgroup
    """
    r = ch.query(q)
    rows = r.result_rows
    if not rows: return pd.DataFrame()
    recs = [{'time':r[0],'clg':r[1],'buy_a':r[2],'sell_a':r[3]} for r in rows]
    df = pd.DataFrame(recs)
    df['time'] = pd.to_datetime(df['time']).dt.tz_localize(None)
    fiz = df[df['clg']==0][['time','buy_a','sell_a']].rename(columns={'buy_a':'fiz_buy_a','sell_a':'fiz_sell_a'})
    yur = df[df['clg']==1][['time','buy_a','sell_a']].rename(columns={'buy_a':'yur_buy_a','sell_a':'yur_sell_a'})
    merged = pd.merge(fiz,yur,on='time',how='outer').fillna(0)
    merged.set_index('time', inplace=True)
    return merged


def precompute(df, acc_df=None):
    d = df.copy()
    d['volume'] = d['volume'].astype(float)
    d['vma20'] = d['volume'].rolling(20).mean().fillna(d['volume'])
    d['vr'] = d['volume']/d['vma20'].clip(lower=1)
    d['vz'] = rz(d['volume'], 20)
    d['fiz_net'] = d['fiz_buy'].fillna(0)-d['fiz_sell'].fillna(0)
    d['yur_net'] = d['yur_buy'].fillna(0)-d['yur_sell'].fillna(0)
    d['fz'] = rz(d['fiz_net'], 20)
    d['yz'] = rz(d['yur_net'], 20)
    d['oi_r'] = (d['yur_buy']+d['yur_sell']).fillna(0)/(d['fiz_buy']+d['fiz_sell']+1).fillna(0)
    d['oima'] = d['oi_r'].rolling(20).mean()
    d['atr14'] = calc_atr(d)
    d['atr_pct'] = d['atr14']/d['close'].clip(lower=1)*100

    # === СИММЕТРИЧНЫЙ SCORE [-1, 1] (balanced: vs=0.3, os_=0.7) ===
    vs = np.tanh(d['vz'] / 3)  # [-1, 1] симметрично
    os_raw = (d['oima'] - d['oi_r']) / d['oima'].clip(lower=0.1)
    os_ = np.clip(os_raw, -1, 1)

    # Score: vs * 0.3 + os_ * 0.7
    score_sym = vs * 0.3 + os_ * 0.7

    # Adjust by volatility
    af = np.clip(1 - (d['atr_pct'] - 0.3) / 3.0, 0, 1)

    # Accounts confidence
    if acc_df is not None and len(acc_df)>0:
        d = d.join(acc_df, how='left').fillna(0)
        d['fiz_vol_pa'] = d['fiz_net'].abs()/(d['fiz_buy_a']+d['fiz_sell_a']+1)
        d['yur_a_change'] = d['yur_buy_a']-d['yur_sell_a']
        d['yur_a_z'] = rz(d['yur_a_change'], 20)
        d['conc'] = np.clip(d['fiz_vol_pa']/1000.0, 0, 1)
        d['yur_conf'] = np.clip(d['yur_a_z']/2.0, 0, 1)
        score_sym = score_sym * (1 + d['conc']*0.5 + d['yur_conf']*0.3)

    d['score_sym'] = np.clip(score_sym * af, -1, 1)

    # Для старого score (LONG only) — оставляем для сравнения
    vs_old = np.clip((d['vr']-1.5)/3.0, 0, 1)
    os_old = np.clip((d['oima']-d['oi_r'])/d['oima'].clip(lower=0.1), 0, 1)
    raw_old = vs_old*0.6+os_old*0.4
    d['score'] = np.clip(raw_old*af, 0, 1)
    if acc_df is not None and len(acc_df)>0:
        d['fiz_vol_pa'] = d['fiz_net'].abs()/(d['fiz_buy_a']+d['fiz_sell_a']+1)
        d['yur_a_change'] = d['yur_buy_a']-d['yur_sell_a']
        d['yur_a_z'] = rz(d['yur_a_change'], 20)
        d['conc'] = np.clip(d['fiz_vol_pa']/1000.0, 0, 1)
        d['yur_conf'] = np.clip(d['yur_a_z']/2.0, 0, 1)
        d['score'] = np.clip(d['score']*(1+d['conc']*0.5+d['yur_conf']*0.3), 0, 1)
    d['score_conf'] = d['score']

    return d


def simulate(d, start, end, sym, use_short=False, lot_pct=1.00):
    """lot_pct не используется напрямую (всегда 100% для чистоты сравнения)"""
    mask = (d.index >= pd.Timestamp(start)) & (d.index < pd.Timestamp(end))
    dd = d[mask].copy()
    if len(dd) < 100: return None

    cash = float(INITIAL)
    peak = float(INITIAL)
    max_dd = 0.0
    trades = 0
    wins = 0
    go = TICKER_CONFIGS.get(sym, {}).get('go', 5000)
    pos = None

    for i in range(1, len(dd)):
        bar = dd.iloc[i]
        h = bar.name.hour
        if h < 7 or h >= 23: continue

        if pos is not None:
            pos['bars_left'] -= 1
            hit = False; ep = float(bar['close'])
            if pos['dir'] == 'L' and float(bar['low']) <= pos['stop']:
                hit = True; ep = pos['stop']
            elif pos['dir'] == 'S' and float(bar['high']) >= pos['stop']:
                hit = True; ep = pos['stop']
            elif pos['bars_left'] <= 0:
                hit = True
            if hit:
                dm = 1 if pos['dir'] == 'L' else -1
                pp = dm * (ep - pos['entry']) / pos['entry']
                pr = pp * pos['go'] * pos['contracts']
                cash += pr; trades += 1
                if pr > 0: wins += 1
                pos = None

        if pos is not None:
            dm = 1 if pos['dir'] == 'L' else -1
            mtm = dm * (float(bar['close']) - pos['entry']) / pos['entry'] * pos['go'] * pos['contracts']
            teq = cash + mtm
        else: teq = cash

        if teq > peak: peak = teq
        ddv = (peak - teq) / peak if peak > 0 else 0
        if ddv > max_dd: max_dd = ddv
        if pos is not None: continue

        # Сигнал
        if use_short:
            # Симметричный: abs(score) > 0.10, направление по знаку
            s = float(bar['score_sym'])
            if np.isnan(s) or abs(s) < 0.10: continue
            direction = 'L' if s > 0 else 'S'
        else:
            # Только LONG: score > 0.10
            s = float(bar['score_conf'])
            if np.isnan(s) or s < 0.10: continue
            direction = 'L'

        # Определяем лот
        lp = lot_pct
        if sym in ['HY', 'AF'] and lp > 0.75:
            lp = 0.75

        contracts = max(1, int(cash * lp / go))
        atrv = float(bar.get('atr14', 1))
        ep = float(bar['close'])

        if direction == 'L':
            stop_p = ep - atrv * 1.0
        else:
            stop_p = ep + atrv * 1.0

        pos = {'dir': direction, 'entry': ep, 'stop': stop_p,
               'bars_left': 8, 'go': go, 'contracts': contracts}

    if pos is not None:
        lb = dd.iloc[-1]
        dm = 1 if pos['dir'] == 'L' else -1
        pp = dm * (float(lb['close']) - pos['entry']) / pos['entry']
        pr = pp * pos['go'] * pos['contracts']
        cash += pr; trades += 1
        if pr > 0: wins += 1

    tr = (cash - INITIAL) / INITIAL * 100
    days = max((pd.Timestamp(end) - pd.Timestamp(start)).days, 30)
    yrs_ = days / 365.25
    cagr = ((cash / INITIAL) ** (1 / max(yrs_, 0.1)) - 1) * 100 if cash > 0 else -100
    calmar = tr / 100 / max(max_dd, 0.001) if max_dd > 0 else tr * 10
    return {'ret': round(tr, 2), 'cagr': round(cagr, 2),
            'dd': round(max_dd * 100, 2), 'calmar': round(calmar, 2),
            'wr': round(wins / trades * 100, 2) if trades > 0 else 0,
            'trades': trades}


def main():
    print("=" * 100)
    print("SHORT-СИГНАЛЫ: BASE v2 LONG vs LONG+SHORT")
    print(f"Период: {START} — {END}")
    print("=" * 100, flush=True)

    loaded = {}
    for sym in SYMBOLS:
        t0 = time.time()
        df = load_data(sym); acc = load_accounts(sym); loaded[sym] = precompute(df, acc)
        print(f"  {sym}: {len(loaded[sym])} баров за {time.time()-t0:.1f}s", flush=True)

    # По каждому тикеру
    for sym in SYMBOLS:
        d = loaded[sym]
        r_l = simulate(d, START, END, sym, use_short=False, lot_pct=1.00)
        r_ls = simulate(d, START, END, sym, use_short=True, lot_pct=1.00)

        print(f"\n{sym}:")
        print(f"{'Режим':<12}{'Ret%':>9}{'DD%':>7}{'Calmar':>8}{'WR%':>6}{'CAGR%':>8}{'Trades':>8}")
        print('-'*58)
        if r_l:  print(f"{'LONG':<12}{r_l['ret']:>8.1f}%{r_l['dd']:>6.1f}%{r_l['calmar']:>8.1f}{r_l['wr']:>5.1f}%{r_l['cagr']:>7.1f}%{r_l['trades']:>8}")
        if r_ls: print(f"{'LONG+SHORT':<12}{r_ls['ret']:>8.1f}%{r_ls['dd']:>6.1f}%{r_ls['calmar']:>8.1f}{r_ls['wr']:>5.1f}%{r_ls['cagr']:>7.1f}%{r_ls['trades']:>8}")

        # Детализация SHORT части: сделаем отдельно
        r_s = simulate(d, START, END, sym, use_short=True, lot_pct=1.00)
        if r_s and r_l:
            long_trades = r_l['trades']
            total_trades = r_s['trades']
            short_trades = total_trades - long_trades
            print(f"  из них SHORT: ~{short_trades} сделок ({short_trades/max(total_trades,1)*100:.0f}%)")
        print(f"  done", flush=True)

    # Сводная
    print(f"\n{'='*100}")
    print(f"СВОДНАЯ: среднее по {len(SYMBOLS)} тикерам")
    print(f"{'='*100}")

    for metric_name, key in [('Доходность Ret%','ret'), ('Просадка DD%','dd'),
                              ('Calmar','calmar'), ('CAGR%','cagr'), ('WR%','wr'), ('Trades','trades')]:
        print(f"\n{metric_name}:")
        print(f"{'Тикер':<7} {'LONG':>10} {'L+S':>10} {'Δ':>10}")
        print('-'*40)

        long_vals = []
        ls_vals = []

        for sym in SYMBOLS:
            d = loaded[sym]
            r_l = simulate(d, START, END, sym, use_short=False, lot_pct=1.00)
            r_ls = simulate(d, START, END, sym, use_short=True, lot_pct=1.00)
            if r_l and r_ls:
                dv = r_ls[key] - r_l[key]
                if key == 'trades':
                    print(f"{sym:<7} {r_l[key]:>10} {r_ls[key]:>10} {dv:+>10}")
                elif key == 'calmar':
                    print(f"{sym:<7} {r_l[key]:>10.1f} {r_ls[key]:>10.1f} {dv:+>10.1f}")
                else:
                    print(f"{sym:<7} {r_l[key]:>9.1f}% {r_ls[key]:>9.1f}% {dv:+>9.1f}%")
                long_vals.append(r_l[key])
                ls_vals.append(r_ls[key])

        if long_vals and ls_vals:
            avg_l = sum(long_vals)/len(long_vals)
            avg_ls = sum(ls_vals)/len(ls_vals)
            dv = avg_ls - avg_l
            if key == 'trades':
                print(f"{'СР':<7} {avg_l:>10.0f} {avg_ls:>10.0f} {dv:+>10.0f}")
            elif key == 'calmar':
                print(f"{'СР':<7} {avg_l:>10.1f} {avg_ls:>10.1f} {dv:+>10.1f}")
            else:
                print(f"{'СР':<7} {avg_l:>9.1f}% {avg_ls:>9.1f}% {dv:+>9.1f}%")

    # Вердикт
    print(f"\n{'='*100}")
    print(f"ВЕРДИКТ")
    print(f"{'='*100}")
    ls_wins = 0
    for sym in SYMBOLS:
        d = loaded[sym]
        r_l = simulate(d, START, END, sym, use_short=False, lot_pct=1.00)
        r_ls = simulate(d, START, END, sym, use_short=True, lot_pct=1.00)
        if r_l and r_ls:
            delta = r_ls['calmar'] - r_l['calmar']
            flag = '🟢' if delta > 1 else ('🔴' if delta < -1 else '➡️')
            print(f"  {sym}: LONG Calmar={r_l['calmar']:.1f} → L+S Calmar={r_ls['calmar']:.1f} (Δ={delta:+.1f}) {flag}")
            if delta > 1: ls_wins += 1

    print(f"LONG+SHORT лучше на {ls_wins}/{len(SYMBOLS)} тикерах")
    if ls_wins >= len(SYMBOLS) * 0.5:
        print(f"✅ SHORT-сигналы имеют смысл — добавляем в стратегию")
    else:
        print(f"❌ SHORT не улучшает — оставляем только LONG")


if __name__ == '__main__':
    main()
