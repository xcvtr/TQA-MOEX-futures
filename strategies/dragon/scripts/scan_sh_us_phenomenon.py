#!/usr/bin/env python3 -u
"""Чистая статистика феномена: движение цены после пробоя диапазона.

Без торговой модели (без SL/TP/timeout). Вопрос: есть ли edge в самом
сигнале "ложный пробой" на ES/NASD?

Для каждого сигнала (пробой минимума/максимума 20-барового диапазона):
  fwd_5  = (close[i+5]  - close[i]) / close[i]
  fwd_12 = (close[i+12] - close[i]) / close[i]
  fwd_36 = (close[i+36] - close[i]) / close[i]
Сравниваем: вход ПРОТИВ пробоя (как SH) vs ПО пробою (инверсия).
"""
import sys, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
import numpy as np, clickhouse_connect as cc

ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')

LB = 20
RT = 0.3

def load_m1(ticker, since, till):
    rows = ch.query(f"SELECT bt,opn,hi,lo,prc,vol FROM moex.mt5_continuous "
                    f"WHERE ticker='{ticker}' AND bt>='{since}' AND bt<'{till}' ORDER BY bt").result_rows
    return [(r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])) for r in rows]

def resample_m5(m1):
    g = {}
    for ts, opn, hi, lo, prc, vol in m1:
        tm = ts.hour * 60 + ts.minute; km = (tm // 5) * 5
        k = ts.replace(minute=km % 60, hour=km // 60, second=0)
        if k not in g:
            g[k] = {'ts': k, 'opn': opn, 'hi': hi, 'lo': lo, 'prc': prc, 'vol': vol}
        else:
            gg = g[k]; gg['hi'] = max(gg['hi'], hi); gg['lo'] = min(gg['lo'], lo)
            gg['prc'] = prc; gg['vol'] += vol
    return sorted(g.values(), key=lambda x: x['ts'])

def analyze(ticker):
    m1 = load_m1(ticker, '2024-01-01', '2027-01-01')
    bars = resample_m5(m1)
    close = np.array([b['prc'] for b in bars])
    hi = np.array([b['hi'] for b in bars])
    lo = np.array([b['lo'] for b in bars])
    n = len(bars)
    print(f"\n===== {ticker}: {n} M5 баров 2024-2026 =====")
    print(f"{'сигнал':<10}{'n':>6}{'fwd5%':>9}{'fwd12%':>9}{'fwd36%':>9}{'WR5%':>7}{'WR12%':>7}{'WR36%':>7}")
    print("-" * 66)

    for name, mask in [("пробой_мин", lo[LB:n] < np.array([lo[i-LB:i].min() for i in range(LB, n)])),
                       ("пробой_макс", hi[LB:n] > np.array([hi[i-LB:i].max() for i in range(LB, n)]))]:
        idx = np.where(mask)[0] + LB
        if len(idx) == 0:
            continue
        # retrace-фильтр как в SH: close вернулся внутрь диапазона
        sig = []
        for i in idx:
            if name == "пробой_мин":
                if close[i] > lo[i] + RT * (hi[i] - lo[i]):
                    sig.append(i)
            else:
                if close[i] < hi[i] - RT * (hi[i] - lo[i]):
                    sig.append(i)
        sig = np.array(sig)
        if len(sig) == 0:
            continue
        # исключаем сигналы на последних 36 барах
        sig = sig[sig + 36 < n]
        if len(sig) == 0:
            continue
        fwd5 = (close[sig + 5] - close[sig]) / close[sig]
        fwd12 = (close[sig + 12] - close[sig]) / close[sig]
        fwd36 = (close[sig + 36] - close[sig]) / close[sig]
        print(f"{name:<10}{len(sig):>6}{fwd5.mean()*100:>+9.3f}{fwd12.mean()*100:>+9.3f}{fwd36.mean()*100:>+9.3f}"
              f"{(fwd5>0).mean()*100:>7.1f}{(fwd12>0).mean()*100:>7.1f}{(fwd36>0).mean()*100:>7.1f}")

    # общий контроль: случайные точки
    rng = np.random.default_rng(42)
    rand = rng.integers(LB + 36, n - 36, size=5000)
    fwd5 = (close[rand + 5] - close[rand]) / close[rand]
    fwd12 = (close[rand + 12] - close[rand]) / close[rand]
    fwd36 = (close[rand + 36] - close[rand]) / close[rand]
    print(f"{'случайный':<10}{len(rand):>6}{fwd5.mean()*100:>+9.3f}{fwd12.mean()*100:>+9.3f}{fwd36.mean()*100:>+9.3f}"
          f"{(fwd5>0).mean()*100:>7.1f}{(fwd12>0).mean()*100:>7.1f}{(fwd36>0).mean()*100:>7.1f}")

for tk in ["NASD", "ES"]:
    analyze(tk)
ch.close()
