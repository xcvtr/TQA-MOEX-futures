#!/usr/bin/env python3
"""D1: Multi-factor score portfolio — top/bottom N tickers daily. v3"""
import sys, os, json, numpy as np, pandas as pd, clickhouse_connect
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import CH_HOST, CH_PORT, CH_DB

INITIAL_CAPITAL = 100_000
COMMISSION = 4
TEST_START = "2021-01-01"
TEST_END = "2026-06-20"

TICKERS = ["AF","AL","AU","BM","BR","BT","CC","CE","CH","CL","CR","DX","ED","EH","Eu","FF","FV","GD","GK","GL","GZ","HS","HY","IB","KC","LK","MC","ME","MG","ML","MM","MN","MX","MY","NA","NG","NK","NM","NR","NV","O2","O4","O6","OJ","OV","OX","PD","PI","PT","RB","RI","RL","RM","RN","SE","SF","SN","SP","SR","SS","SV","Si","TN","TT","UC","VB","VI","W4","X5","YD","YN"]

MARGIN = {"AF":5000,"AL":3000,"AU":5000,"BM":3000,"BR":3000,"BT":3000,"CC":5000,"CE":5000,"CH":5000,"CL":5000,"CR":5000,"DX":3000,"ED":5000,"EH":3000,"Eu":5000,"FF":5000,"FV":3000,"GD":5000,"GK":5000,"GL":5000,"GZ":2065,"HS":5000,"HY":3000,"IB":3000,"KC":2500,"LK":5000,"MC":3149,"ME":5000,"MG":5000,"ML":3000,"MM":5000,"MN":5000,"MX":5000,"MY":3000,"NA":5000,"NG":5000,"NK":3000,"NM":1405,"NR":3000,"NV":3000,"O2":3000,"O4":3000,"O6":3000,"OJ":3000,"OV":3000,"OX":3000,"PD":5000,"PI":3000,"PT":3000,"RB":5000,"RI":5000,"RL":5000,"RM":3000,"RN":5000,"SE":5000,"SF":5000,"SN":5000,"SP":5000,"SR":5719,"SS":5000,"SV":5000,"Si":1000,"TN":5000,"TT":5000,"UC":5000,"VB":3000,"VI":5000,"W4":5000,"X5":3000,"YD":5000,"YN":3000}

def rz(s, w=21):
    m = s.rolling(w, min_periods=w).mean()
    std = s.rolling(w, min_periods=w).std()
    return (s - m) / std.clip(lower=1e-10)

def sigmoid(x):
    return 1 / (1 + np.exp(-np.clip(x, -10, 10)))

def precompute():
    ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)
    tk = "','".join(TICKERS)
    q = f"""
        SELECT ticker, tradedate,
               argMax(pr_close, tradetime) AS close,
               argMax(oi_change, tradetime) AS oi_chg,
               sum(vol_sum) AS volume
        FROM moex.supercandles_fo
        WHERE ticker IN ('{tk}') AND tradedate >= '2020-01-01'
        GROUP BY ticker, tradedate ORDER BY ticker, tradedate
    """
    r = ch.query(q)
    raw = pd.DataFrame(r.result_rows, columns=r.column_names)
    raw['tradedate'] = pd.to_datetime(raw['tradedate'])

    panel = {}
    for t in TICKERS:
        tdf = raw[raw['ticker'] == t].copy().sort_values('tradedate')
        if len(tdf) < 30:
            continue
        tdf['ret_5d'] = tdf['close'].pct_change(5)
        tdf['vol_z'] = rz(tdf['volume'])
        tdf['oi_z'] = rz(tdf['oi_chg'])
        tdf['ret_5d_z'] = rz(tdf['ret_5d'])
        for _, row in tdf.iterrows():
            dt = row['tradedate']
            panel.setdefault(dt, {})[t] = {
                'close': row['close'],
                'vol_z': row['vol_z'],
                'oi_z': row['oi_z'],
                'ret_5d_z': row['ret_5d_z'],
                'margin': MARGIN.get(t, 5000),
            }
    return panel

def run_config(panel, all_dates, w1, w2, w3, N, th_z, reverse):
    eq = float(INITIAL_CAPITAL)
    peak = INITIAL_CAPITAL
    max_dd = 0.0
    n_trades = 0
    daily_returns = []
    pos_count_sum = 0
    pos_count_days = 0

    for i in range(len(all_dates) - 1):
        today = all_dates[i]
        tomorrow = all_dates[i + 1]
        today_data = panel.get(today, {})
        if len(today_data) < 2:
            daily_returns.append(0.0)
            continue
        tomorrow_data = panel.get(tomorrow, {})
        if not tomorrow_data:
            daily_returns.append(0.0)
            continue

        scores = {}
        for t, v in today_data.items():
            vz = v['vol_z']; oz = v['oi_z']; r5z = v['ret_5d_z']
            if pd.isna(vz) or pd.isna(oz) or pd.isna(r5z):
                continue
            if v['close'] <= 0:
                continue
            if th_z > 0 and abs(vz) < th_z and abs(oz) < th_z and abs(r5z) < th_z:
                continue
            sc = w1 * sigmoid(vz) + w2 * sigmoid(oz) + w3 * sigmoid(r5z)
            scores[t] = sc

        if len(scores) < 2:
            daily_returns.append(0.0)
            continue

        sorted_sc = sorted(scores.items(), key=lambda x: x[1])
        if reverse:
            long_syms = sorted_sc[:N]
            short_syms = sorted_sc[-N:]
        else:
            long_syms = sorted_sc[-N:]
            short_syms = sorted_sc[:N]

        day_pnl = 0.0
        n_pos = 0
        for t, _ in long_syms:
            tmr = tomorrow_data.get(t)
            if tmr is None:
                continue
            ret_nxt = tmr['close'] / today_data[t]['close'] - 1
            if pd.isna(ret_nxt):
                continue
            mg = today_data[t]['margin']
            cap_per = eq / (2 * N)
            if cap_per < mg:
                continue
            cont = int(cap_per / mg)
            if cont < 1:
                continue
            contrib = ret_nxt * (cont * mg / eq)
            comm = (cont * COMMISSION * 2) / eq
            day_pnl += contrib - comm
            n_pos += 1

        for t, _ in short_syms:
            tmr = tomorrow_data.get(t)
            if tmr is None:
                continue
            ret_nxt = tmr['close'] / today_data[t]['close'] - 1
            if pd.isna(ret_nxt):
                continue
            mg = today_data[t]['margin']
            cap_per = eq / (2 * N)
            if cap_per < mg:
                continue
            cont = int(cap_per / mg)
            if cont < 1:
                continue
            contrib = -ret_nxt * (cont * mg / eq)
            comm = (cont * COMMISSION * 2) / eq
            day_pnl += contrib - comm
            n_pos += 1

        if n_pos == 0:
            daily_returns.append(0.0)
            continue

        pos_count_sum += n_pos
        pos_count_days += 1
        eq *= (1 + day_pnl)
        if eq > peak:
            peak = eq
        dd = eq / peak - 1
        if dd < max_dd:
            max_dd = dd
        if day_pnl != 0:
            n_trades += 1
        daily_returns.append(day_pnl)

    if len(daily_returns) < 50 or eq <= 0:
        return None

    total_ret = (eq / INITIAL_CAPITAL - 1) * 100
    years = len(daily_returns) / 252
    cagr = ((eq / INITIAL_CAPITAL) ** (1 / years) - 1) * 100 if years > 0 else -100.0
    sharpe = np.mean(daily_returns) / np.std(daily_returns) * np.sqrt(252) if np.std(daily_returns) > 0 else 0
    calmar = cagr / abs(max_dd * 100) if max_dd != 0 else 0

    return {
        'reverse': reverse,
        'w1': w1, 'w2': w2, 'w3': round(w3, 2),
        'N': N, 'th_z': th_z,
        'ret_pct': round(total_ret, 1),
        'cagr': round(cagr, 1),
        'dd': round(max_dd * 100, 1),
        'sharpe': round(sharpe, 2),
        'calmar': round(calmar, 2),
        'trades': n_trades,
        'avg_pos': round(pos_count_sum / pos_count_days, 1) if pos_count_days else 0,
    }

def main():
    print("=" * 60)
    print("D1: Multi-factor Score Portfolio v3 (sigmoid)")
    print("=" * 60)

    panel = precompute()
    all_dates = sorted(panel.keys())
    all_dates = [d for d in all_dates if TEST_START <= str(d.date()) <= TEST_END]
    print(f"  Days: {len(all_dates)}, Tickers at start: {len(panel.get(all_dates[0], {}))}")

    W1_GRID = [0.2, 0.33, 0.4]
    W2_GRID = [0.2, 0.33, 0.4]
    N_GRID = [1, 2, 3]
    TH_Z_GRID = [0.0, 0.5, 1.0]
    REVERSE_GRID = [False, True]

    results = []
    total = len(W1_GRID) * len(W2_GRID) * len(N_GRID) * len(TH_Z_GRID) * len(REVERSE_GRID)
    done = 0

    for reverse in REVERSE_GRID:
        for w1 in W1_GRID:
            for w2 in W2_GRID:
                w3 = round(1.0 - w1 - w2, 2)
                if w3 < 0.05 or w3 > 0.8:
                    continue
                for N in N_GRID:
                    for th_z in TH_Z_GRID:
                        r = run_config(panel, all_dates, w1, w2, w3, N, th_z, reverse)
                        done += 1
                        if r is not None:
                            results.append(r)
                            if r['calmar'] > 0 or r['ret_pct'] > 0:
                                print(f"  [+] rev={reverse} w=({w1},{w2},{w3}) N={N} th={th_z} → Ret={r['ret_pct']}% CAGR={r['cagr']}% DD={r['dd']}% Calmar={r['calmar']}")
                        if done % 30 == 0:
                            print(f"  Progress: {done}/{total}")

    if results:
        results.sort(key=lambda r: (-r['calmar'], -r['cagr'], r['dd']))
    else:
        print("  No valid results!")

    print("\n" + "=" * 100)
    print("D1 RESULTS — Top 20 by Calmar")
    print("=" * 100)
    hdr = f"{'rev':>5} {'w1':>4} {'w2':>4} {'w3':>4} {'N':>2} {'th_z':>4} {'Ret%':>7} {'CAGR%':>7} {'DD%':>6} {'Sharpe':>7} {'Calmar':>7} {'Trd':>5} {'avgPos':>6}"
    print(hdr)
    print("-" * 100)
    for r in results[:20]:
        print(f"{str(r['reverse']):>5} {r['w1']:>4.2f} {r['w2']:>4.2f} {r['w3']:>4.2f} {r['N']:>2} {r['th_z']:>4.1f} {r['ret_pct']:>7.1f} {r['cagr']:>7.1f} {r['dd']:>6.1f} {r['sharpe']:>7.2f} {r['calmar']:>7.2f} {r['trades']:>5} {r['avg_pos']:>6.1f}")

    best_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'reports', 'd1_best.json')
    with open(best_path, 'w') as f:
        json.dump(results[:15], f, indent=2)
    print(f"\nSaved to {best_path}")
    return results[:10]

if __name__ == '__main__':
    main()
