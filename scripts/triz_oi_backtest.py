#!/usr/bin/env python3
"""TRIZ OI backtester — SV MOEX futures.

CLI-флаги для параллельных свипов:
  --ticker SV|RN|TATN|BR|NG|Si|Eu
  --year 2025|2026
  --risk 0.10
  --thr 3.0
  --hold 60
  --dom 0.05        # подтверждение стаканом (imb_thr), 0=выкл
  --pyramid 3       # макс каскадных позиций (1=выкл)
  --lq              # Local Quality: размер = f(|day_net|)
  --athr            # адаптивный thr: перцентиль day_net за 20д (вместо статичного)
  --out path.json   # сохранить trades + метрики
"""
import sys, os, json, argparse, bisect
import numpy as np
import clickhouse_connect as cc, psycopg2
from datetime import timedelta, datetime

TZ_SHIFT = 5 * 3600  # futoi MSK → цены IRK
MT = {'SV': 'SILV', 'TATN': 'TATN', 'RN': 'RN', 'BR': 'BR', 'NG': 'NG', 'Si': 'Si', 'Eu': 'Eu_ALLFUT'}


def load_data(ticker, year):
    ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')
    START, END = f'{year}-01-01', f'{year}-12-31'
    if year == 2026:
        END = '2026-08-07'
    def q(sql): return ch.query(sql).result_rows

    # futoi (MSK) → +5ч
    ft_map = {'SV': 'SV', 'RN': 'RN', 'TATN': 'TT', 'BR': 'BR', 'NG': 'NG', 'Si': 'Si', 'Eu': 'Eu'}
    r = q(f"SELECT bt, (buy_fiz - sell_fiz) * 1.0 / NULLIF(buy_fiz + sell_fiz, 0) * 100 as dn "
          f"FROM moex.futoi WHERE ticker='{ft_map[ticker]}' AND bt >= '{START} 00:00:00' AND bt <= '{END} 23:59:59'")
    futoi = {bt.replace(tzinfo=None).timestamp() + TZ_SHIFT: dn for bt, dn in r}

    # цены (IRK)
    r = q(f"SELECT toUnixTimestamp(toDateTime(bt)), prc FROM moex.mt5_continuous "
          f"WHERE ticker='{MT[ticker]}' AND bt >= '{START}' AND bt <= '{END} 23:59:59'")
    rows = [(ts, c) for ts, c in r if c and c > 0]
    arr = np.array(rows, dtype=np.float64)
    order = np.argsort(arr[:, 0])
    prices = (arr[order, 0], arr[order, 1])

    # imb (IRK)
    r = q(f"SELECT toUnixTimestamp(min), imb FROM moex.dom_imb_qsh WHERE ticker LIKE '{ticker}%' "
          f"AND min >= '{START}' AND min <= '{END} 23:59:59'")
    imb = {ts: v for ts, v in r}
    ch.close()

    # specs
    pg = psycopg2.connect(host='10.0.0.60', dbname='moex', user='postgres', connect_timeout=5)
    cur = pg.cursor()
    cur.execute("SELECT go, min_step, step_price, fee_entry FROM futures.ticker_specs WHERE ticker=%s", (ticker,))
    row = cur.fetchone()
    pg.close()
    specs = (float(row[0]), float(row[1]), float(row[2]), float(row[3]))
    return futoi, prices, imb, specs


def run(args):
    futoi, prices, imb, (GO, MS, SP, FEE) = load_data(args.ticker, args.year)
    fts = sorted(futoi.keys())
    pts = prices[0]
    it = sorted(imb.keys()) if args.dom else []

    # адаптивный thr: перцентиль day_net за 20 торговых дней
    athr_cache = {}
    if args.athr:
        dn_hist = []
        prev_day = None
        for ts in fts:
            day = datetime.fromtimestamp(ts - TZ_SHIFT).date()
            if prev_day is None: prev_day = day
            if day != prev_day:
                prev_day = day
            # будем считать перцентиль по последним ~20 дням (≈2800 точек)

    def get_athr(ts):
        # перцентиль 90 |day_net| за последние 20 дней
        day = datetime.fromtimestamp(ts - TZ_SHIFT).date()
        cutoff = ts - 20 * 86400
        if cutoff not in athr_cache:
            vals = [abs(futoi[t]) for t in fts if cutoff <= t <= ts]
            if len(vals) > 200:
                athr_cache[cutoff] = float(np.percentile(vals, 90))
            else:
                athr_cache[cutoff] = args.thr
        return athr_cache[cutoff]

    equity = 200000.0
    peak = equity
    mdd = 0.0
    n = 0
    wins = 0
    pnl_sum = 0.0
    trades = []
    positions = []  # (entry_ts, direction, shares, entry_price, size_pct)
    occ_until = None
    max_pos = args.pyramid if args.pyramid and args.pyramid > 1 else 1

    for ts in fts:
        dn = futoi[ts]
        idx = bisect.bisect_right(pts, ts) - 1
        if idx < 0:
            continue
        pt, prc = prices[0][idx], prices[1][idx]
        if prc <= 0 or (ts - pt) > 600:
            continue

        thr = get_athr(ts) if args.athr else args.thr

        # закрытие по timeout
        for pi in range(len(positions) - 1, -1, -1):
            pos = positions[pi]
            if ts >= pos[0] + 60 * args.hold:
                pnl = (prc - pos[3]) / MS * SP * pos[2]
                if pos[1] < 0:
                    pnl = (pos[3] - prc) / MS * SP * pos[2]
                pnl -= FEE
                equity += pnl
                n += 1
                if pnl > 0:
                    wins += 1
                pnl_sum += pnl
                trades.append({'entry_ts': pos[0], 'exit_ts': ts, 'dir': pos[1],
                               'shares': pos[2], 'entry': pos[3], 'exit': prc,
                               'pnl': pnl, 'size_pct': pos[4], 'reason': 'timeout'})
                positions.pop(pi)
                occ_until = ts + 300

        if positions:
            mv_total = sum((prc - p[3]) / MS * SP * p[2] * (1 if p[1] > 0 else -1) for p in positions)
            peak = max(peak, equity + mv_total)
            mdd = max(mdd, (peak - (equity + mv_total)) / peak)
            if occ_until and ts < occ_until:
                continue

        if len(positions) >= max_pos:
            continue

        # сигнал
        sig = False
        direction = 0
        if dn <= -thr:
            sig, direction = True, 1
        elif dn >= thr:
            sig, direction = True, -1

        # подтверждение стаканом
        if sig and args.dom:
            j = bisect.bisect_right(it, ts) - 1
            imbv = imb[it[j]] if j >= 0 else None
            if imbv is None or abs(imbv) < args.dom:
                sig = False

        if not sig:
            continue

        # Local Quality: размер = f(|dn|)
        if args.lq:
            strength = min(abs(dn) / thr, 3.0)
            risk_pct = args.risk * (0.6 + 0.4 * (strength - 1) / 2) if strength > 1 else args.risk * 0.6
            risk_pct = max(risk_pct, args.risk * 0.5)
        else:
            risk_pct = args.risk

        shares = int(equity * risk_pct / GO)
        if shares < 1:
            continue
        positions.append((ts, direction, shares, prc, risk_pct))

        mv_total = sum((prc - p[3]) / MS * SP * p[2] * (1 if p[1] > 0 else -1) for p in positions)
        peak = max(peak, equity + mv_total)
        mdd = max(mdd, (peak - (equity + mv_total)) / peak)

    # закрыть остаток
    for pos in positions:
        prc = prices[1][-1]
        pnl = (prc - pos[3]) / MS * SP * pos[2]
        if pos[1] < 0:
            pnl = (pos[3] - prc) / MS * SP * pos[2]
        pnl -= FEE
        equity += pnl
        n += 1
        if pnl > 0:
            wins += 1
        pnl_sum += pnl
        trades.append({'entry_ts': pos[0], 'exit_ts': fts[-1], 'dir': pos[1],
                       'shares': pos[2], 'entry': pos[3], 'exit': prc,
                       'pnl': pnl, 'size_pct': pos[4], 'reason': 'eod'})

    roi = (equity - 200000) / 200000 * 100
    wr = wins / n * 100 if n else 0
    res = {
        'ticker': args.ticker, 'year': args.year, 'risk': args.risk, 'thr': args.thr,
        'hold': args.hold, 'dom': args.dom, 'pyramid': max_pos, 'lq': args.lq, 'athr': args.athr,
        'roi_pct': round(roi, 1), 'mdd_pct': round(mdd * 100, 1), 'n_trades': n,
        'wr_pct': round(wr, 1), 'pnl': round(pnl_sum, 0), 'n_pos': max_pos,
        'trades': trades,
    }
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, 'w') as f:
            json.dump(res, f)
    print(json.dumps({k: v for k, v in res.items() if k != 'trades'}, ensure_ascii=False))


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--ticker', default='SV')
    ap.add_argument('--year', type=int, default=2026)
    ap.add_argument('--risk', type=float, default=0.10)
    ap.add_argument('--thr', type=float, default=3.0)
    ap.add_argument('--hold', type=int, default=60)
    ap.add_argument('--dom', type=float, default=0.0)
    ap.add_argument('--pyramid', type=int, default=1)
    ap.add_argument('--lq', action='store_true')
    ap.add_argument('--athr', action='store_true')
    ap.add_argument('--out', default='')
    args = ap.parse_args()
    run(args)
