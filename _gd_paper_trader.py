#!/usr/bin/env python3
"""
GD Paper Trader — smart_money (yb↑ + fiz↓) → LONG hold=10 sl=1%
Работает по ClickHouse. Когда loader обновит данные — выдаст сделки.
"""
import sys, os, json
from datetime import datetime, timedelta
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import clickhouse_connect
import numpy as np
from config import CH_HOST, CH_PORT, CH_DB

CAPITAL = 100_000
CS = 10
COMM = 4
HOLD = 10
SL = 0.01
TRADES_FILE = 'reports/paper_trading_gd.json'

ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)

# 1. Берём последние 30 дней дневных данных
rows = ch.query("""
    SELECT toDate(p.time) as d,
           argMax(p.open, p.time) as open,
           argMax(p.high, p.time) as high,
           argMax(p.low, p.time) as low,
           argMax(p.close, p.time) as close,
           argMax(o.yur_buy, p.time) as yur_buy,
           argMax(o.fiz_buy, p.time) as fiz_buy,
           argMax(o.fiz_sell, p.time) as fiz_sell,
           argMax(o.total_oi, p.time) as total_oi
    FROM moex.prices_5m p
    INNER JOIN moex.prices_5m_oi o ON p.symbol = o.symbol AND p.time = o.time
    WHERE p.symbol = 'GD' AND p.time >= %(s)s
    GROUP BY d ORDER BY d
""", parameters={'s': (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')}).result_rows

if len(rows) < 3:
    print('NO_NEW_DATA: less than 3 days available')
    sys.exit(0)

dates = [str(r[0]) for r in rows]
opn = np.array([r[1] for r in rows], dtype=float)
high = np.array([r[2] for r in rows], dtype=float)
low = np.array([r[3] for r in rows], dtype=float)
close = np.array([r[4] for r in rows], dtype=float)
yb = np.array([r[5] for r in rows], dtype=float)
fb = np.array([r[6] for r in rows], dtype=float)
fs = np.array([r[7] for r in rows], dtype=float)
toi = np.array([r[8] for r in rows], dtype=float)
toi = np.where(toi <= 0, 1, toi)

dyb = np.diff(yb)
fiz_net = (fb - fs) / toi * 100
dfiz = np.diff(fiz_net)

# 2. Загружаем существующие сделки
trades = []
if os.path.exists(TRADES_FILE):
    with open(TRADES_FILE) as f:
        trades = json.load(f)

# 3. Проверяем последний день на сигнал
last_idx = len(rows) - 2  # последний день, где есть diff
if last_idx >= 0:
    signal = dyb[last_idx] > 0 and dfiz[last_idx] < 0
    today = dates[last_idx + 1]  # сегодня (последний полный день)
    
    # Проверяем, не открыта ли уже сделка на этот entry
    already_open = any(t['entry'] == today and t['exit'] == 'OPEN' for t in trades)
    
    if signal and not already_open:
        entry_price = float(opn[last_idx + 1])
        if entry_price > 0:
            go = entry_price * CS
            nc = max(1, int(CAPITAL // go)) if go > 0 else 1
            trades.append({
                'entry': today,
                'exit': 'OPEN',
                'entry_price': entry_price,
                'contracts': nc,
                'hold_until': str((datetime.strptime(today, '%Y-%m-%d') + timedelta(days=HOLD)).date()),
                'sl': entry_price * (1 - SL),
                'stop_hit': False,
                'pnl': 0
            })
            print(f'SIGNAL: {today} → LONG at {entry_price}, {nc} contracts')

# 4. Проверяем открытые сделки на закрытие
for t in trades:
    if t['exit'] != 'OPEN':
        continue
    
    # Ищем этот день в данных
    for i, d in enumerate(dates):
        if d == t['entry']:
            # Проверяем стоп
            stop_price = t['sl']
            hold_until = t['hold_until']
            
            for j in range(i, min(i + HOLD + 1, len(dates))):
                if float(low[j]) <= stop_price:
                    t['exit'] = dates[j]
                    t['exit_price'] = stop_price
                    t['stop_hit'] = True
                    
                    nc = t['contracts']
                    gp = nc * CS * (stop_price - t['entry_price'])
                    cm = nc * COMM
                    t['pnl'] = round(gp - cm, 0)
                    print(f'CLOSE (STOP): {t["entry"]}→{t["exit"]} pnl={t["pnl"]:+.0f}')
                    break
            else:
                # Проверяем hold expiry
                today_d = datetime.now().date()
                hold_d = datetime.strptime(hold_until, '%Y-%m-%d').date()
                if today_d >= hold_d:
                    # Ищем close на день hold_until
                    exit_price = close[-1]  # fallback
                    for k, d in enumerate(dates):
                        if d == hold_until:
                            exit_price = float(close[k])
                            break
                    
                    t['exit'] = str(hold_until)
                    t['exit_price'] = exit_price
                    nc = t['contracts']
                    gp = nc * CS * (exit_price - t['entry_price'])
                    cm = nc * COMM
                    t['pnl'] = round(gp - cm, 0)
                    print(f'CLOSE (HOLD): {t["entry"]}→{t["exit"]} pnl={t["pnl"]:+.0f}')
            break

# 5. Сохраняем
with open(TRADES_FILE, 'w') as f:
    json.dump(trades, f, indent=2, default=str)

# 6. Статус
active = [t for t in trades if t['exit'] == 'OPEN']
closed = [t for t in trades if t['exit'] != 'OPEN']
total_pnl = sum(t['pnl'] for t in closed)
print(f'\nStatus: {len(closed)} closed ({total_pnl:+.0f} ₽), {len(active)} active')
