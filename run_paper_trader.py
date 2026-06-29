#!/usr/bin/env python3
"""PaperTrader runner — запускается по cron каждые 5 мин.

Читает портфель из PG, загружает последние 50 баров из PG,
запускает стратегии, сохраняет состояние.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from strategies.common.paper_trader import PaperTrader
from strategies.stop_hunt.prod.engine import check_signal as sh
from strategies.cvd.prod.engine import check_signal as cvd
from strategies.lunch_rev.prod.engine import check_signal as lunch

# Портфель: какие стратегии на каких тикерах
STRATEGIES = [
    ('stop_hunt', sh, ['GZ','SR','NG','VB','W4','Si','CR'], None),
    ('cvd', cvd, ['GZ','SR','Si','CR'], None),
    ('lunch_rev', lunch, ['Si'], None),
]

if __name__ == '__main__':
    pt = PaperTrader(STRATEGIES, capital=100_000, use_pg=True)
    pt.init()

    # Сброс: каждый запуск — один тик (cron запускает каждые 5 мин)
    pt.tick()
    pt._save_state()

    s = pt.status()
    print(f"[PaperTrader] equity={s['equity']} return={s['return_pct']:.1f}% "
          f"open={s['open_positions']} trades={s['total_trades']}")
