#!/usr/bin/env python3 -u
"""Предсказание волатильности для NG/BR/SV (сырьё).

Вопрос: можно ли заранее знать, что год/месяц будет волатильным?

Три проверки на данных 2021-2026:
1. СЕЗОННОСТЬ: реализованная волатильность по месяцам (есть ли устойчивые месяцы)
2. ПЕРСИСТЕНТНОСТЬ: если волатильность высокая сейчас — останется ли высокой?
   (автокорреляция волатильности, regime detection)
3. OI-СИГНАЛ: сила |day_net| (частота/амплитуда сигналов) предсказывает будущую волатильность?
"""
import sys, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
import numpy as np
import clickhouse_connect as cc

ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')

MT = {'SV': 'SILV', 'NG': 'NG', 'BR': 'BR'}
FT = {'SV': 'SV', 'NG': 'NG', 'BR': 'BR'}


def load_prices(ticker):
    r = ch.query(f"SELECT toUnixTimestamp(toDateTime(bt)), prc FROM moex.mt5_continuous "
                 f"WHERE ticker='{MT[ticker]}' AND bt>='2021-01-01' ORDER BY bt").result_rows
    arr = np.array([(ts, c) for ts, c in r if c and c > 0], dtype=np.float64)
    order = np.argsort(arr[:, 0])
    return arr[order, 0], arr[order, 1]


def daily_vol(ts, prc):
    """Реализованная волатильность: std дневных доходностей (годовая, %)."""
    # ресемпл до дневных закрытий
    days = {}
    for t, p in zip(ts, prc):
        d = int(t // 86400)
        days[d] = p
    ds = sorted(days.keys())
    closes = np.array([days[d] for d in ds])
    rets = np.diff(closes) / closes[:-1]
    return ds, rets


def monthly_vol(ds, rets):
    """Волатильность по месяцам (годовая, %)."""
    import datetime as dt
    out = {}
    for d, r in zip(ds[1:], rets):
        day = dt.datetime.fromtimestamp(d * 86400)
        key = f"{day.year}-{day.month:02d}"
        out.setdefault(key, []).append(r)
    res = {}
    for k, v in out.items():
        res[k] = float(np.std(v) * np.sqrt(252) * 100) if len(v) > 1 else 0
    return res


print("=" * 80)
print("1. СЕЗОННОСТЬ: средняя волатильность по месяцам (2021-2026)")
print("=" * 80)
all_monthly = {}
for tk in ['NG', 'BR', 'SV']:
    ts, prc = load_prices(tk)
    ds, rets = daily_vol(ts, prc)
    mv = monthly_vol(ds, rets)
    all_monthly[tk] = mv
    print(f"\n{tk} (годовая волатильность % по месяцам, среднее 2021-26):")
    months = {}
    for k, v in mv.items():
        m = int(k.split('-')[1])
        months.setdefault(m, []).append(v)
    line = ""
    for m in range(1, 13):
        vals = months.get(m, [])
        line += f"{m:2d}月:{np.mean(vals):5.1f}  " if vals else f"{m:2d}月:  n/a  "
    print(line)

print("\n" + "=" * 80)
print("2. ПЕРСИСТЕНТНОСТЬ РЕЖИМА: автокорреляция месячной волатильности")
print("=" * 80)
for tk in ['NG', 'BR', 'SV']:
    mv = all_monthly[tk]
    keys = sorted(mv.keys())
    vals = np.array([mv[k] for k in keys])
    # автокорреляция лаг 1..3 месяца
    ac = []
    for lag in [1, 2, 3]:
        a = vals[:-lag]; b = vals[lag:]
        ac.append(float(np.corrcoef(a, b)[0, 1]) if len(a) > 5 else 0)
    print(f"{tk}: AC(1мес)={ac[0]:+.2f}  AC(2мес)={ac[1]:+.2f}  AC(3мес)={ac[2]:+.2f}")
    # медиана и перцентили
    print(f"   медиана={np.median(vals):.1f}%  p25={np.percentile(vals,25):.1f}%  p75={np.percentile(vals,75):.1f}%  "
          f"min={vals.min():.1f}% max={vals.max():.1f}%")

print("\n" + "=" * 80)
print("3. OI-СИГНАЛ КАК ПРЕДИКТОР ВОЛАТИЛЬНОСТИ")
print("   (частота сильных сигналов |day_net|>=4 в месяце -> волатильность следующего месяца?)")
print("=" * 80)
for tk in ['NG', 'BR', 'SV']:
    r = ch.query(f"SELECT bt, (buy_fiz - sell_fiz) * 1.0 / NULLIF(buy_fiz + sell_fiz, 0) * 100 as dn "
                 f"FROM moex.futoi WHERE ticker='{FT[tk]}' AND bt>='2021-01-01'").result_rows
    import datetime as dt
    sig_per_month = {}
    for bt, dn in r:
        if dn is None: continue
        key = f"{bt.year}-{bt.month:02d}"
        sig_per_month.setdefault(key, []).append(float(dn))
    mv = all_monthly[tk]
    keys = sorted(set(sig_per_month.keys()) & set(mv.keys()))
    n_sig = [sum(1 for v in sig_per_month[k] if abs(v) >= 4) for k in keys]
    vol_now = np.array([mv[k] for k in keys])
    vol_next = np.array([mv[keys[i + 1]] for i in range(len(keys) - 1)] + [np.nan])
    # корреляция: число сигналов в месяце vs волатильность СЛЕДУЮЩЕГО месяца
    mask = ~np.isnan(vol_next)
    if mask.sum() > 10:
        corr_next = float(np.corrcoef(np.array(n_sig)[mask], vol_next[mask])[0, 1])
        corr_same = float(np.corrcoef(np.array(n_sig), vol_now)[0, 1])
    else:
        corr_next = corr_same = 0
    print(f"{tk}: corr(сигналы_месяц, vol_тот_же_месяц)={corr_same:+.2f}, "
          f"corr(сигналы_месяц, vol_след_месяц)={corr_next:+.2f}")

ch.close()
