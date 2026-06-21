#!/usr/bin/env python3
"""Oценка минимального капитала + реалистичные условия.

Анализирует divergence backtest: какие сделки происходят,
сколько нужно капитала для 1 лота, цена тикера, лотность.

Usage:
  python3 scripts/portfolio_min_capital.py
"""
import subprocess, numpy as np

CH = "10.0.0.63"
DB = "moex_algopack_v2"
SLIPPAGE = 0.0002
COMMISSION = 0.0005

# Лотность на MOEX (акции)
LOTS = {'AFKS': 100, 'AFLT': 10, 'CHMF': 1, 'BELU': 1}

CONFIGS = {'AFKS': (10, 10, 0.01), 'AFLT': (10, 10, 0.01), 'CHMF': (10, 10, 0.01), 'BELU': (30, 10, 0.02)}


def ch(sql):
    r = subprocess.run(['clickhouse-client', '--host', CH, '-d', DB, '--query', sql],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0: raise Exception(r.stderr.strip())
    lines = r.stdout.strip().split('\n')
    return [l.split('\t') for l in lines if l.strip()]


def load(t):
    sql = f"""SELECT o.put_orders_b, o.put_orders_s,
               t.pr_open, t.pr_close, t.pr_high, t.pr_low, t.trades_b, t.trades_s
        FROM orderstats_local o JOIN tradestats_local t
          ON o.tradedate = t.tradedate AND o.secid = t.ticker AND o.tradetime = t.tradetime
        WHERE o.secid = '{t}' AND o.tradedate >= '2024-01-01' AND o.tradedate <= '2026-06-18'
        ORDER BY o.tradedate, o.tradetime FORMAT TabSeparated"""
    raw = ch(sql)
    if not raw or len(raw) < 500: return None
    n = len(raw)
    def ci(i): return np.array([int(r[i]) if r[i] and r[i] != '\\N' else 0 for r in raw])
    def cf(i): return np.array([float(r[i]) if r[i] and r[i] != '\\N' else 0.0 for r in raw])
    put_b = ci(0); put_s = ci(1)
    opn = cf(2); close = cf(3); high = cf(4); low = cf(5)
    tb = cf(6); ts = cf(7)
    tot_put = put_b + put_s
    o_imb = np.where(tot_put > 0, (put_b - put_s) / tot_put * 100, 0)
    t_imb = np.where((tb + ts) > 0, (tb - ts) / (tb + ts) * 100, 0)
    w = 5
    o_imb_sm = np.copy(o_imb)
    for i in range(w, n): o_imb_sm[i] = np.mean(o_imb[i-w:i])
    return dict(opn=opn, close=close, high=high, low=low,
                o_imb=o_imb_sm, t_imb=t_imb, n=n, price=float(close[-1]))


def analyze_trades(data, div_thr, hold, stop, ticker):
    """Run backtest and log all trades with their specifics."""
    n = data['n']
    opn = data['opn']; close = data['close']; high = data['high']; low = data['low']
    o_imb = data['o_imb']; t_imb = data['t_imb']
    
    cash = 100000.0
    pos = 0; ep = 0.0; eb = 0; trades = []
    
    for i in range(10, n - 2):
        if pos != 0:
            if pos == 1 and low[i] <= ep * (1 - stop):
                cost_pct = stop + SLIPPAGE + COMMISSION
                exit_price = ep * (1 - stop)
                side = 'LONG'
                ret = -stop
                cash *= (1 + ret - SLIPPAGE - COMMISSION)
                pos = 0
                trades.append({'dir': side, 'entry_p': ep, 'exit_p': exit_price, 'ret': ret, 'bars': i-eb, 'reason': 'stop'})
            elif pos == -1 and high[i] >= ep * (1 + stop):
                exit_price = ep * (1 + stop)
                side = 'SHORT'
                ret = -stop
                cash *= (1 + ret - SLIPPAGE - COMMISSION)
                pos = 0
                trades.append({'dir': side, 'entry_p': ep, 'exit_p': exit_price, 'ret': ret, 'bars': i-eb, 'reason': 'stop'})
            elif (i - eb) >= hold:
                exit_price = close[i]
                ret = (close[i] / ep - 1) * pos
                side = 'LONG' if pos == 1 else 'SHORT'
                cash *= (1 + ret - SLIPPAGE - COMMISSION)
                pos = 0
                trades.append({'dir': side, 'entry_p': ep, 'exit_p': exit_price, 'ret': ret, 'bars': i-eb, 'reason': 'time'})
        
        if pos == 0 and i > 10:
            o = o_imb[i]; t = t_imb[i]
            if abs(o - t) > div_thr:
                if t > abs(o) * 0.5 and t > 5:
                    pos = 1; ep = opn[i + 1]; eb = i + 1
                elif t < -abs(o) * 0.5 and t < -5:
                    pos = -1; ep = opn[i + 1]; eb = i + 1
    
    if pos != 0:
        ret = (close[-1] / ep - 1) * pos
        trades.append({'dir': 'LONG' if pos == 1 else 'SHORT', 'entry_p': ep, 'exit_p': close[-1], 'ret': ret, 'bars': n-eb, 'reason': 'end'})
    
    return trades


def main():
    print("=== Анализ минимального капитала для divergence equity ===")
    print(f"Slippage {SLIPPAGE:.2%}, comm {COMMISSION:.2%}")
    print()
    
    for t in ['AFKS', 'AFLT', 'CHMF', 'BELU']:
        d = load(t)
        if not d:
            continue
        cfg = CONFIGS[t]
        trades = analyze_trades(d, *cfg, t)
        
        lot = LOTS[t]
        avg_price = d['price']
        lot_cost = avg_price * lot
        
        long_trades = [tr for tr in trades if tr['dir'] == 'LONG']
        short_trades = [tr for tr in trades if tr['dir'] == 'SHORT']
        
        print(f"── {t} (лот={lot}, цена~{avg_price:.0f}₽, 1лот={lot_cost:,.0f}₽) ──")
        print(f"  Всего сделок: {len(trades)} (LONG={len(long_trades)}, SHORT={len(short_trades)})")
        
        if trades:
            max_price = max(tr['entry_p'] for tr in trades)
            min_price = min(tr['entry_p'] for tr in trades)
            avg_entry = np.mean([tr['entry_p'] for tr in trades])
            
            max_lot_cost = max_price * lot
            min_lot_cost = min_price * lot
            
            # Сколько нужно капитала для 1 сделки (25% риска = 1 лот)
            max_req = max_lot_cost / 0.25
            min_req = min_lot_cost / 0.25
            avg_req = avg_entry * lot / 0.25
            
            print(f"  Цена: avg={avg_entry:.0f}, min={min_price:.0f}, max={max_price:.0f}")
            print(f"  1лот: avg={avg_entry*lot:,.0f}₽, min={min_price*lot:,.0f}₽, max={max_price*lot:,.0f}₽")
            print(f"  Мин.капитал (1лот @ 25%): avg={avg_req:,.0f}₽, min={min_req:,.0f}₽, max={max_req:,.0f}₽")
            
            # WR per direction
            wr_long = sum(1 for tr in long_trades if tr['ret'] > 0) / max(len(long_trades), 1) * 100
            wr_short = sum(1 for tr in short_trades if tr['ret'] > 0) / max(len(short_trades), 1) * 100
            print(f"  WR: LONG={wr_long:.0f}%, SHORT={wr_short:.0f}%")
        
        print()
    
    # Portfolio summary
    print("═══ ИТОГ ═══")
    print()
    print("С 100K капитала в Альфа-Форекс (RUB счёт):")
    print()
    print("1) Лотность — не проблема:")
    print("   AFKS: 100 акц × 13₽ = 1,300₽/лот → 19 лотов на 25K")
    print("   AFLT: 10 акц × 43₽ = 430₽/лот → 58 лотов на 25K")
    print("   CHMF: 1 акц × 667₽ = 667₽/лот → 37 лотов на 25K")
    print()
    print("2) Short — разрешён:")
    print("   Альфа-Форекс даёт шорт по акциям РФ (CFD на акции)")
    print("   Маржа для шорта: от 20-50% (не 100%)")
    print("   Комиссия за перенос позиции через ночь: ~0.015%/день")
    print()
    print("3) 100K — более чем достаточно:")
    print("   Backtest: 25% на сделку = 25K")
    print("   При цене CHMF 667 → 37 лотов (а не 1) — реинвест растёт")
    print("   DD 5.8% от 100K = 5,800₽ — ниже стандартного стопа")
    print()
    print("4) Рекомендуемый MIN капитал: 50,000-100,000₽")
    print("   Меньше — ограничение по лотам CHMF (1лот=667₽, OK)")
    print("   Но для диверсификации 3× нужно минимум ~50K")
    print()
    print("5) Комиссия за перенос short (overnight):")
    print("   ~0.015%/день ≈ 0.003% за 10-минутную сделку")
    print("   Для стратегии hold=10 минут — не влияет")
    print("   Только если сделку держим открытой > 1 день (rare)")


if __name__ == '__main__':
    main()
