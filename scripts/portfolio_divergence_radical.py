#!/usr/bin/env python3
"""Пирамидинг + anti-stop: radical TRIZ-идеи для divergence strategy.

Anti-stop: убираем стоп-лосс. Каждая позиция живёт hold баров.
Пирамидинг: новый сигнал ДОБАВЛЯЕТ позицию (каскад).
Каждая позиция — отдельный entry, со своим hold.

Сценарии:
1. BASE — как в v4 (1% stop, hold=10, 1 pos)
2. ANTI-STOP — без стопа (только time exit hold=10)
3. PYRAMID — каскад до 3 позиций, 1% stop
4. ANTI-PYRAMID — без стопа + каскад до 3
5. FLAT MAX — все 3 позиции сразу на 1% стопе (max aggression)
"""
import subprocess, sys, numpy as np
sys.path.insert(0, '/home/user/projects/TQA-MOEX')
from scripts.divergence_backtest import load_ticker, ch

CH = "10.0.0.63"
DB = "moex_algopack_v2"
SLIPPAGE = 0.0002
COMMISSION = 0.0005

TICKERS = ['AFKS', 'AFLT', 'CHMF']
CONFIGS = {
    'AFKS': (10, 10, 0.01),
    'AFLT': (10, 10, 0.01),
    'CHMF': (10, 10, 0.01),
}
CAPITAL_PER = 33333.0


def load(t):
    data = load_ticker(t, '2024-01-01', '2026-06-18')
    if data is None or len(data['open']) < 500:
        return None
    n = len(data['open'])
    return {
        'n': n, 'opn': np.array(data['open']), 'close': np.array(data['close']),
        'high': np.array(data['high']), 'low': np.array(data['low']),
        'o_imb': np.array(data['o_imb_sm']), 't_imb': np.array(data['t_imb']),
    }


def run(data, div_thr, hold, stop, use_pyramid=False, anti_stop=False):
    """Run backtest with optional pyramid (multi-pos cascade) and anti-stop.
    
    Без пирамидинга: 1 pos per ticker.
    С пирамидингом: до 3 параллельных позиций (каждая со своим hold).
    Anti-stop: нет stop-loss, только time exit.
    """
    n = data['n']
    opn = data['opn']; close = data['close']; high = data['high']; low = data['low']
    o_imb = data['o_imb']; t_imb = data['t_imb']
    
    cash = float(CAPITAL_PER)
    
    # Список позиций: [(direction, entry_price, entry_bar), ...]
    positions = []
    max_pos = 3 if use_pyramid else 1
    
    tc = 0; wc = 0
    eq = [cash]

    def close_position(pos_idx, exit_price, reason='time'):
        nonlocal cash, tc, wc
        dir_, ep, eb = positions[pos_idx]
        ret = (exit_price / ep - 1) * dir_
        cost = ret - SLIPPAGE - COMMISSION
        if anti_stop:
            cost = ret - SLIPPAGE - COMMISSION  # no stop slippage
        cash *= (1 + cost)
        if ret > 0: wc += 1
        tc += 1
        positions.pop(pos_idx)

    for i in range(10, n - 2):
        # Check existing positions
        for p_idx in range(len(positions) - 1, -1, -1):
            dir_, ep, eb = positions[p_idx]
            bars = i - eb
            
            if not anti_stop:
                # Stop check
                if dir_ == 1 and low[i] <= ep * (1 - stop):
                    close_position(p_idx, ep * (1 - stop), 'stop')
                    continue
                elif dir_ == -1 and high[i] >= ep * (1 + stop):
                    close_position(p_idx, ep * (1 + stop), 'stop')
                    continue
            
            # Time exit
            if bars >= hold:
                close_position(p_idx, close[i], 'time')
                continue
        
        # New signal
        if len(positions) < max_pos:
            o = o_imb[i]; t = t_imb[i]
            if abs(o - t) > div_thr:
                if t > abs(o) * 0.5 and t > 5:
                    ep = opn[i + 1]
                    cash *= (1 - SLIPPAGE - COMMISSION)
                    positions.append((1, ep, i + 1))
                elif t < -abs(o) * 0.5 and t < -5:
                    ep = opn[i + 1]
                    cash *= (1 - SLIPPAGE - COMMISSION)
                    positions.append((-1, ep, i + 1))
        
        # MTM equity
        mtm_val = cash
        for dir_, ep, eb in positions:
            if dir_ == 1:
                mtm_val += cash * (close[i] / ep - 1)  # simplified
            else:
                mtm_val += cash * (ep / close[i] - 1)
        eq.append(max(cash, 1))
    
    # Close remaining at end
    for p_idx in range(len(positions) - 1, -1, -1):
        close_position(p_idx, close[-1], 'end')
    
    total_ret = (cash / CAPITAL_PER - 1) * 100
    ea = np.array(eq)
    pk = np.maximum.accumulate(ea)
    dd = np.max((pk - ea) / pk * 100)
    wr = wc / max(tc, 1) * 100
    
    return {'ret': total_ret, 'dd': dd, 'trades': tc, 'wr': wr, 'eq': eq, 'final': cash}


def main():
    print("═══ DIVERGENCE — RADICAL TRIZ ═══")
    print(f"3 tickers (AFKS, AFLT, CHMF), {SLIPPAGE:.2%} slp, {COMMISSION:.2%} comm")
    print()
    
    scenarios = [
        ("BASE", False, False, "1 pos, 1% stop, hold=10 ✅"),
        ("ANTI-STOP", False, True, "1 pos, NO stop, hold=10"),
        ("PYRAMID", True, False, "3 pos cascade, 1% stop"),
        ("PYRAMID-ANTI", True, True, "3 pos cascade, NO stop"),
    ]
    
    for sname, pyramid, antistop, desc in scenarios:
        print(f"\n{'='*60}")
        print(f"  {sname}: {desc}")
        print(f"{'='*60}")
        
        per_t = {}
        for t in TICKERS:
            d = load(t)
            if not d:
                continue
            cfg = CONFIGS[t]
            r = run(d, cfg[0], cfg[1], cfg[2], pyramid, antistop)
            per_t[t] = r
            cm = r['ret'] / max(r['dd'], 0.01)
            print(f"  {t}: ret={r['ret']:+.1f}% dd={r['dd']:.1f}% calmar={cm:.1f}x trades={r['trades']} wr={r['wr']:.0f}%")
        
        if not per_t:
            continue
        
        # Portfolio
        cpt = 100000.0 / len(per_t)
        pf = None
        ml = min(len(r['eq']) for r in per_t.values())
        for t in TICKERS:
            if t not in per_t: continue
            eq = np.array(per_t[t]['eq'][:ml])
            pf = eq / eq[0] * cpt if pf is None else pf + eq / eq[0] * cpt
        fv = pf[-1]
        rr = (fv / 100000.0 - 1) * 100
        dd2 = np.max((np.maximum.accumulate(pf) - pf) / np.maximum.accumulate(pf) * 100)
        cm2 = rr / max(dd2, 0.01)
        years = 2.47
        cagr = ((1 + rr/100) ** (1/years) - 1) * 100
        print(f"  → PORTFOLIO: {rr:+.1f}% dd={dd2:.1f}% calmar={cm2:.1f}x cagr={cagr:.1f}% final={fv:,.0f}")
    
    print(f"\n═══ CRAZY: FLAT MAX (все 20K сразу в 3 позиции на 1% стопе) ═══")
    # Каждый тикер: сразу 3 каскадные позиции (имитация 3x leverage)
    for t in TICKERS:
        d = load(t)
        if not d:
            continue
        # 3x capital, 3 pos cascade, 1% stop
        cfg = CONFIGS[t]
        # Сохраняем CAPITAL_PER, заменяем на 3x
        import scripts.portfolio_divergence_10tk as _ignore
        r = run(d, cfg[0], cfg[1], cfg[2], use_pyramid=True, anti_stop=False)
        cm = r['ret'] / max(r['dd'], 0.01)
        print(f"  {t}: {r['ret']:+.1f}% dd={r['dd']:.1f}% calmar={cm:.1f}x")


if __name__ == '__main__':
    main()
