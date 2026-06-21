#!/usr/bin/env python3
"""Портфель divergence strategy — 10 тикеров (расширение).

Топ-10 uncorrelated тикеров из scan, каждый со своим best config.
Equal weight: 10K на тикер → DD диверсифицируется.
Slippage 0.02%, comm 0.05%, full reinvest, MTM equity.

Usage:
  python3 scripts/portfolio_divergence_10tk.py
"""
import subprocess, sys, numpy as np
sys.path.insert(0, '/home/user/projects/TQA-MOEX')
from scripts.divergence_backtest import load_ticker, ch

CH = "10.0.0.63"
DB = "moex_algopack_v2"
SLIPPAGE = 0.0002
COMMISSION = 0.0005

# Топ-5 по Calmar + uncorrelated, исключая выбросы по DD
TICKERS = [
    'AFKS',  # телеком, Calmar=10.7, DD=17.7
    'AFLT',  # авиа, Calmar=8.7, DD=12.7
    'BRZL',  # сельхоз, Calmar=8.0, DD=21.7
    'CBOM',  # банки, Calmar=7.8, DD=18.7
    'CHMF',  # металлургия, Calmar=6.4, DD=16.3
]

CONFIGS = {
    'AFKS': (10, 10, 0.01),
    'AFLT': (10, 10, 0.01),
    'BRZL': (15, 5, 0.02),
    'CBOM': (10, 5, 0.01),
    'CHMF': (15, 10, 0.01),
}

LOTS = {
    'AFKS': 100, 'AFLT': 10, 'BRZL': 1, 'CBOM': 10000, 'CHMF': 1,
}


def load(ticker):
    """Load with o_imb/t_imb calculation, returning dict."""
    try:
        data = load_ticker(ticker, '2024-01-01', '2026-06-18')
    except:
        return None
    if data is None or len(data['open']) < 500:
        return None
    
    n = len(data['open'])
    o_imb_sm = np.array(data['o_imb_sm'])
    t_imb = np.array(data['t_imb'])
    
    return {
        'n': n,
        'opn': np.array(data['open']),
        'close': np.array(data['close']),
        'high': np.array(data['high']),
        'low': np.array(data['low']),
        'o_imb': o_imb_sm,
        't_imb': t_imb,
    }


def run(ticker, config):
    """Run backtest for one ticker. Returns equity curve."""
    div_thr, hold, stop = config
    data = load(ticker)
    if data is None:
        return None
    
    n = data['n']
    opn = data['opn']; close = data['close']; high = data['high']; low = data['low']
    o_imb = data['o_imb']; t_imb = data['t_imb']
    
    cash = 10000.0  # 10K per ticker
    pos = 0; ep = 0.0; eb = 0; tc = 0; wc = 0
    eq = [cash]
    
    for i in range(10, n - 2):
        if pos != 0:
            if pos == 1 and low[i] <= ep * (1 - stop):
                ret = -stop
                cash *= (1 + ret - SLIPPAGE - COMMISSION)
                tc += 1; pos = 0
            elif pos == -1 and high[i] >= ep * (1 + stop):
                ret = -stop
                cash *= (1 + ret - SLIPPAGE - COMMISSION)
                tc += 1; pos = 0
            elif (i - eb) >= hold:
                ret = (close[i] / ep - 1) * pos
                cash *= (1 + ret - SLIPPAGE - COMMISSION)
                if ret > 0: wc += 1
                tc += 1; pos = 0
        
        if pos == 0 and i > 10:
            o = o_imb[i]; t = t_imb[i]
            if abs(o - t) > div_thr:
                if t > abs(o) * 0.5 and t > 5:
                    pos = 1; ep = opn[i + 1]; eb = i + 1
                    cash *= (1 - SLIPPAGE - COMMISSION)
                elif t < -abs(o) * 0.5 and t < -5:
                    pos = -1; ep = opn[i + 1]; eb = i + 1
                    cash *= (1 - SLIPPAGE - COMMISSION)
        
        if pos == 1:
            eq.append(cash * close[i] / ep)
        elif pos == -1:
            eq.append(cash * ep / close[i])
        else:
            eq.append(cash)
    
    if pos != 0:
        ret = (close[-1] / ep - 1) * pos
        cash *= (1 + ret - SLIPPAGE - COMMISSION)
        if ret > 0: wc += 1
        tc += 1
    eq.append(cash)
    
    total_ret = (cash / 10000.0 - 1) * 100
    ea = np.array(eq)
    pk = np.maximum.accumulate(ea)
    dd = np.max((pk - ea) / pk * 100)
    wr = wc / max(tc, 1) * 100
    
    return {'ret': total_ret, 'dd': dd, 'trades': tc, 'wr': wr, 'eq': eq, 'final': cash}


def main():
    print("═══ DIVERGENCE — 10 TICKER PORTFOLIO ═══")
    print(f"Slippage {SLIPPAGE:.2%}, comm {COMMISSION:.2%}, full reinvest, MTM DD")
    print()
    
    results = {}
    print(f"{'Ticker':>6} {'Ret%':>8} {'DD%':>7} {'Calmar':>7} {'Trades':>7} {'WR%':>5}  Config")
    print("-" * 60)
    
    for t in TICKERS:
        cfg = CONFIGS[t]
        r = run(t, cfg)
        if r:
            results[t] = r
            cm = r['ret'] / max(r['dd'], 0.01)
            print(f"  {t:>6} {r['ret']:>+7.1f}% {r['dd']:>6.1f}% {cm:>6.1f}x {r['trades']:>6} {r['wr']:>4.0f}%  "
                  f"div={cfg[0]} h={cfg[1]} s={cfg[2]:.0%}")
        else:
            print(f"  {t:>6} {'NO DATA':>8}")
    
    if not results:
        print("\nNo results")
        return
    
    # Portfolio: sum of scaled equity curves
    capital_total = 100000.0
    capital_per = capital_total / len(results)
    
    pf_eq = None
    min_len = min(len(r['eq']) for r in results.values())
    
    for t, r in results.items():
        eq = np.array(r['eq'][:min_len])
        es = eq / eq[0] * capital_per
        if pf_eq is None:
            pf_eq = es
        else:
            pf_eq += es
    
    final = pf_eq[-1]
    total_ret = (final / capital_total - 1) * 100
    peak_pf = np.maximum.accumulate(pf_eq)
    dd_pf = (peak_pf - pf_eq) / peak_pf * 100
    max_dd_pf = np.max(dd_pf)
    calmar_pf = total_ret / max(max_dd_pf, 0.01)
    
    years = 2.47
    cagr = ((1 + total_ret/100) ** (1/years) - 1) * 100
    
    print(f"\n{'='*55}")
    print(f"  ПОРТФЕЛЬ 10 ТИКЕРОВ")
    print(f"{'='*55}")
    print(f"  Capital: {capital_total:,.0f} → {final:,.0f} RUB")
    print(f"  Total Return: {total_ret:+.1f}%")
    print(f"  CAGR (годовых): {cagr:.1f}%")
    print(f"  Max DD: {max_dd_pf:.1f}%")
    print(f"  Calmar: {calmar_pf:.1f}x")
    
    # DD events
    print(f"\n── DD events > 3% ──")
    in_dd = False
    events = []
    start = 0
    for i, d in enumerate(dd_pf):
        if d > 3 and not in_dd:
            in_dd = True; start = i
        elif d <= 1 and in_dd:
            in_dd = False
            events.append((start, i, np.max(dd_pf[start:i+1])))
    if in_dd:
        events.append((start, len(dd_pf)-1, np.max(dd_pf[start:])))
    events.sort(key=lambda x: x[2], reverse=True)
    for ev in events[:5]:
        print(f"  DD {ev[2]:.1f}% ({ev[1]-ev[0]} bars)")
    
    # Yearly
    print(f"\n── Yearly (approximate) ──")
    # Use AFKS dates as reference
    try:
        ref = load_ticker('AFKS', '2024-01-01', '2026-06-18')
        if ref:
            dates = ref['date']
            for yr in ['2024', '2025']:
                yr_idxs = [i for i, d in enumerate(dates[:min_len]) if d.startswith(yr)]
                if yr_idxs:
                    s, e = yr_idxs[0], yr_idxs[-1]
                    yr_eq = pf_eq[s:e+1]
                    yr_ret = (yr_eq[-1] / yr_eq[0] - 1) * 100
                    yr_peak = np.maximum.accumulate(yr_eq)
                    yr_dd = np.max((yr_peak - yr_eq) / yr_peak * 100)
                    print(f"  {yr}: {yr_ret:+.1f}%  DD={yr_dd:.1f}%")
    except:
        pass
    
    # Comparison with 3-ticker
    print(f"\n── СРАВНЕНИЕ С 3-ТИКЕРНЫМ ПОРТФЕЛЕМ ──")
    print(f"{'Портфель':<15} {'Ret%':>8} {'DD%':>7} {'CAGR':>8} {'Calmar':>8}")
    print("-" * 50)
    print(f"{'3 tk (BASE)':<15} {'+341.2%':>8} {'5.8%':>7} {'82.4%':>8} {'58.4x':>8}")
    print(f"{'10 tk':<15} {f'+{total_ret:.1f}%':>8} {f'{max_dd_pf:.1f}%':>7} {f'{cagr:.1f}%':>8} {f'{calmar_pf:.1f}x':>8}")
    print()
    delta_ret = total_ret - 341.2
    delta_dd = max_dd_pf - 5.8
    print(f"  Δ vs 3tk: ret={delta_ret:+.1f}pp, dd={delta_dd:+.1f}pp")
    print(f"  Ratio: ret {total_ret/341.2:.1f}x, DD {max_dd_pf/max(5.8,0.01):.1f}x")


if __name__ == '__main__':
    main()
