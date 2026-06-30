#!/usr/bin/env python3
"""PaperTrader runner — запускается по cron каждые 5 мин."""
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
    old_positions = len([p for p in pt.executor.positions if not p.closed])

    try:
        pt.tick()
        pt._save_state()
    except Exception as e:
        import traceback
        print(f"❌ PaperTrader ошибка: {e}")
        traceback.print_exc()
        sys.exit(1)

    s = pt.status()
    new_trades = len(pt.executor.trades) - old_trades
    new_positions = len([p for p in pt.executor.positions if not p.closed])
    dd = (pt.executor.peak - pt.executor.equity) / pt.executor.peak * 100 if pt.executor.peak > 0 else 0

    lines = []

    # Новая сделка
    if new_trades > 0:
        t = pt.executor.trades[-1]
        sign = '✅' if t.pnl > 0 else '❌'
        lines.append(f"{sign} {t.ticker} {t.direction} {t.strategy} pnl={t.pnl:+.0f} | Eq={s['equity']:>.0f}")

    # Открытие/закрытие позиции
    if new_positions > old_positions:
        for p in s['positions']:
            lines.append(f"📌 {p['ticker']} {p['direction']} {p['strategy']} entry={p['entry']}")

    # DD предупреждение
    if dd >= 20:
        lines.append(f"⚠️ Просадка {dd:.1f}% — RiskManager STOP")

    if lines:
        print("\n".join(lines))
