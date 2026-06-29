#!/usr/bin/env python3
"""PaperTrader runner — запускается по cron каждые 15 мин."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from strategies.common.paper_trader import PaperTrader
from strategies.stop_hunt.prod.engine import check_signal as sh
from strategies.cvd.prod.engine import check_signal as cvd

STRATEGIES = [
    ('stop_hunt', sh, ['GZ','SR','NG','VB','W4','Si','CR'], None),
    ('cvd', cvd, ['GZ','SR','Si','CR'], None),
]

if __name__ == '__main__':
    pt = PaperTrader(STRATEGIES, capital=100_000, use_pg=True)
    pt.init()
    old_trades = len(pt.executor.trades)
    old_equity = pt.executor.equity
    
    try:
        pt.tick()
        pt._save_state()
    except Exception as e:
        import traceback
        print(f"[PaperTrader] ❌ Ошибка: {e}")
        traceback.print_exc()
        sys.exit(1)

    s = pt.status()
    new_trades = len(pt.executor.trades) - old_trades
    dd = (pt.executor.peak - pt.executor.equity) / pt.executor.peak * 100 if pt.executor.peak > 0 else 0
    
    report = []
    report.append(f"📊 PaperTrader | Eq={s['equity']:>.0f} ({s['return_pct']:>.1f}%) DD={dd:.1f}% | Сделок: {s['total_trades']}")
    
    from collections import Counter
    if pt.executor.trades:
        sc = Counter(t.strategy for t in pt.executor.trades)
        report.append(f"  По стратегиям: {dict(sc)}")
    
    if s['open_positions']:
        for p in s['positions']:
            report.append(f"  📌 {p['ticker']} {p['direction']} {p['strategy']} entry={p['entry']} pnl={p['pnl']:+.0f}")
    
    if dd >= 20:
        report.append(f"  ⚠️  DD={dd:.1f}% — RiskManager STOP")
    
    print("\n".join(report))
