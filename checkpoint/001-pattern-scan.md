# 001 — Crowd Bias: Volume Surge + FIZ/YUR Divergence

**Дата:** 2026-06-06
**Статус:** Первичный скрининг завершён

---

## Что сделано

### 1. Инвентаризация данных
- **openinterest_moex**: 64 тикера, 18M записей, FIZ/YUR разделение, buy_accounts
- **moex_prices_5m**: 59 тикеров, 5m OHLCV с 2023-01-03 (Alor OpenAPI)
- **moex_prices_5m_oi**: 5m OI агрегаты, 2021-2026
- **moex_prices**: пустая (D1 не загружены)

### 2. Скрининг anti-crowd (944 теста)
- 4 стратегии × 4 порога × 59 тикеров
- No look-ahead, bar-by-bar
- **Результат**: anti-crowd не работает ни на одном тикере (WR ≤ 50%)

### 3. Скрининг Volume Surge + Divergence (64 тикера)
- Метод: вспышка объёма (vol_z ≥ 2.0) + FIZ↔YUR divergence
- Анализ 2h профиля после сигнала: max, min, last, up_close, asymmetry
- **Результат**: 24 тикера KEEP, 10 MAYBE, 20 KILL

### 4. Финальная классификация

| Вердикт | Кол-во | Тикеры |
|---------|:------:|--------|
| ✅ KEEP | 24 | AF, AL, CC, CE, DX, GL, HS, HY, MC, MG, NG, NM, NR, OJ, PD, SE, SF, SN, SP, SS, TN, TT, W4, YD |
| ⚠️ MAYBE | 10 | BM, GK, IB, KC, ME, MM, PT, RN, SV, VB |
| ❌ KILL | 20 | BR, CNY, ED, EUR, Eu, GAZ, GD, GLD, GZ, IMO, LK, MX, NA, RI, RM, SBE, SR, Si, UC, USD |

### 5. Рабочие стратегии (найдено ранее)

| Стратегия | WR% | Тикер | Суть |
|-----------|:---:|:-----:|------|
| VolClimax + FIZ filter | 67.4% | Si | Торговать ПО FIZ |
| YUR-Follow | PF=2.21 | VB | За умными деньгами |
| NG Divergence | 55.3% | NG | FIZ↔YUR врозь |

---

## Результаты

### KEEP-тикеры: ключевые параметры

| Тикер | LONG max | asym | SHORT max | asym | Особенность |
|-------|:--------:|:----:|:---------:|:----:|-------------|
| GL (Gold) | +0.77% | **2.24** | +0.73% | 1.95 | Цена летит вверх 2.2x |
| AF (Aeroflot) | +1.01% | 1.64 | +1.00% | 1.59 | max > min, bear skew |
| CC (Cocoa) | +1.39% | 1.52 | +0.94% | 0.95 | Дикие движения |
| HY (Hydra) | +1.34% | 1.85 | +1.13% | 1.47 | Огромные, bearish |
| NG (NG) | +1.02% | 1.37 | +1.14% | 1.51 | Стабильные оба направления |
| NR (Nickel) | +1.44% | 1.59 | +1.26% | 1.40 | Самый сильный сигнал |
| OJ (Orange) | +1.39% | 1.05 | +1.24% | 0.79 | max > min |
| PD (Palladium) | +0.96% | 1.51 | +0.99% | 1.47 | Стабильный |
| SE (Soybean) | +1.34% | 1.89 | +1.27% | 1.56 | Асимметрия |
| SF (FinEx) | +4.89% | 1.20 | +5.07% | 1.21 | WILD ±5% |
| SP (SPBE) | +2.02% | 1.34 | +2.13% | 1.55 | WILD ±2% |
| SS (Sugar) | +1.04% | 1.17 | +1.09% | 1.16 | Bearish (up=40%) |
| TN (T) | +0.73% | 1.72 | +0.66% | 1.23 | Long bias |
| TT (T) | +0.76% | 1.55 | +0.75% | 1.61 | Симметричный |
| W4 (Wheat) | +2.04% | **2.59** | +2.18% | 2.11 | WILD, максимальная asym |
| YD (YDEX) | +0.90% | 1.67 | +0.82% | 1.53 | Стабильный |

### Убитые тикеры

Eu, EURRUBF, Si, ED, BR, GD, SR, CNYRUBF, USDRUBF, GLDRUBF и др. — max < 0.3%, asym ~1.0. Полный шум.

---

## Что дальше

### Этап 2 — Детальный разбор KEEP-тикеров

Для каждого из 24 KEEP-тикеров:
1. Оптимальный z-score порог (vol_z и div_z)
2. Лучший exit horizon (15min? 1h? 2h?)
3. LONG только, SHORT только или оба?
4. Профит-фактор и WR (close-based)
5. Сессионный анализ (утро/день/вечер)
6. Сезонность (по месяцам)

### Этап 3 — Построение стратегии
1. Объединение сигналов с нескольких тикеров
2. Risk management (размер позиции)
3. Backtest с комиссиями и проскальзыванием

---

## Файлы

- `strategies/crowd-bias/README.md` — описание стратегии
- `strategies/crowd-bias/research/analysis-notes.md` — заметки
- `reports/2026-06-06-crowd-bias-verification.md` — первый отчёт
- `reports/2026-06-06-anti-crowd-pre-svo-vs-current.md` — pre-SVO сравнение
- `research/001-pattern-scan.md` — этот файл
- `scripts/crowd_full_scan.py` — скрипт полного скана
