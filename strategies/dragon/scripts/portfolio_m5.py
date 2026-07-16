#!/usr/bin/env python3 -u
"""Dragon portfolio backtest на M5 tradestats — топ тикеры, MTM DD, реинвест."""
import sys, os, argparse
from datetime import datetime
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from dragon.scripts.sweep import load_ohlc, calc_pnl, backtest_one, get_all_tickers


def portfolio(tickers_contracts, days=365, capital=200000, knur=0.5):
    """Run portfolio backtest with MTM DD."""
    specs = get_all_tickers()
    all_trades = []

    for ticker, contracts in tickers_contracts:
        if ticker not in specs:
            print(f"  {ticker}: нет в specs")
            continue
        trades = backtest_one(ticker, specs[ticker], days)
        # Multiply PnL by contracts
        for t in trades:
            t['pnl'] *= contracts
            t['ticker'] = ticker
            t['contracts'] = contracts
        n = len(trades)
        if n == 0:
            print(f"  {ticker:4s} ×{contracts}: 0 сделок")
            continue
        pnl = sum(t['pnl'] for t in trades)
        wins = [t for t in trades if t['pnl'] > 0]
        losses = [t for t in trades if t['pnl'] <= 0]
        wr = len(wins)/n*100
        tp = sum(t['pnl'] for t in wins)
        tn = sum(abs(t['pnl']) for t in losses)
        pf = tp/tn if tn else float('inf')
        aw = tp/len(wins) if wins else 0
        al = tn/len(losses) if losses else 0
        print(f"  {ticker:4s} ×{contracts} | n={n:6d} wr={wr:5.1f}% pnl={pnl:>10.0f} pf={pf:.2f} aw={aw:>8.0f} al={al:>8.0f}")
        all_trades.extend(trades)

    if not all_trades:
        print("Нет сделок!")
        return

    # Sort chronologically
    all_trades.sort(key=lambda x: x.get('ts', datetime.min))
    
    # Equity curve + MDD
    cap = capital
    peak = cap
    mdd = 0
    for t in all_trades:
        cap += t['pnl']
        peak = max(peak, cap)
        mdd = max(mdd, (peak - cap) / peak * 100)

    n = len(all_trades)
    wins = [t for t in all_trades if t['pnl'] > 0]
    losses = [t for t in all_trades if t['pnl'] <= 0]
    wr = len(wins)/n*100
    tp = sum(t['pnl'] for t in wins)
    tn = sum(abs(t['pnl']) for t in losses)
    pf = tp/tn if tn else float('inf')
    aw = tp/len(wins) if wins else 0
    al = tn/len(losses) if losses else 0
    ret = (cap - capital) / capital * 100
    calmar = ret / mdd if mdd > 0 else float('inf')

    print(f"\n{'='*65}")
    print(f"ПОРТФЕЛЬ ({len(tickers_contracts)} тикеров, {n} сделок)")
    print(f"{'='*65}")
    print(f"  Капитал: {capital:>10,.0f}₽ → {cap:>12,.0f}₽ ({ret:+.2f}%)")
    print(f"  WR: {wr:.1f}% | PF: {pf:.2f} | MDD: {mdd:.2f}% | Calmar: {calmar:.1f}")
    print(f"  AvgWin: {aw:>+.0f}₽ | AvgLoss: {al:>+.0f}₽ | Сделок: {n}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--days', type=int, default=365)
    parser.add_argument('--capital', type=int, default=200000)
    args = parser.parse_args()

    print(f"\n🐉 Dragon Portfolio M5 — {args.days}д, {args.capital:,}₽")
    print(f"{'='*65}")

    # Варианты портфеля
    print("\n--- A: NG×2, BR×1, SV×1 ---")
    portfolio([('NG',2), ('BR',1), ('SV',1)], args.days, args.capital)

    print("\n--- B: NG×2, BR×1, SV×1, BM×2, Si×1 ---")
    portfolio([('NG',2), ('BR',1), ('SV',1), ('BM',2), ('Si',1)], args.days, args.capital)

    print("\n--- C: NG×2, BR×1, BM×2, MM×2 ---")
    portfolio([('NG',2), ('BR',1), ('BM',2), ('MM',2)], args.days, args.capital)

    print("\n--- D: NG×2, BR×2, SV×1 ---")
    portfolio([('NG',2), ('BR',2), ('SV',1)], args.days, args.capital)
