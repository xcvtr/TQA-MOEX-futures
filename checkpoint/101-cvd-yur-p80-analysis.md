# Checkpoint 101 — CVD+YUR P80 Analysis & Systematic Correlation Prep

**Дата:** 2026-06-27
**Проект:** TQA-MOEX-futures
**Предыдущий:** #100 — cvd-yur-filter-portfolio

---

## Что сделано

### 1. Маппинг тикеров: tradestats_fo ↔ prices_5m_oi
- `tradestats_fo`: 3367 secids → 238 базовых тикеров
- `prices_5m_oi`: 64 символа, из них **57** совпадают с tradestats_fo base tickers
- 7 не совпадают: CNYRUBF, EURRUBF, GAZPF, GLDRUBF, IMOEXF, SBERF, USDRUBF

### 2. CVD-only P80 (baseline) — все 57 тикеров
- Метод: z(ΔCVD, 20) > 0.6 раздельно для long/short
- Period=20 (100 мин), Lookahead=12 (60 мин)
- Результат: 57 тикеров с ≥100 сигналами
- Avg NetP80: +0.54%
- Avg WinRate: 48.6%

### 3. CVD + Total OI (oi_close) — неверный подход
- Использовал z(ΔOI) — изменение OI как поток
- Результат: 37 тикеров выжили (остальные <100 sig)
- Avg ΔNetP80: +0.11%, Avg ΔWR: +1.23pp
- Слишком агрессивно режет сигналы

### 4. CVD + YUR_NET level — правильная формула (из чекпойнта #099)
- Формула: z(ΔCVD, 20) > 0.6 AND z(yur_net, 20) > 0.6 (LONG)
- Использует **уровень** yur_net (yur_buy - yur_sell), а не Δ
- 55 тикеров с ≥100 сигналами (из 57)
- Avg ΔNetP80: +0.02%, Avg ΔWR: +1.3pp
- **WR растёт стабильно (+1–3pp), но NetP80 не улучшается**
- 2 тикера с улучшением >+0.1: W4 (+0.22), OJ (+0.17)
- 0 ухудшений

## Файлы

| Файл | Назначение |
|:-----|:-----------|
| `scripts/cvd_yur_p80_analysis.py` | P80 анализ CVD vs CVD+YUR (level) |
| `reports/cvd_yur_p80_results.json` | Результаты: CVD=57, CVD+YUR=55 тикеров |
| `reports/cvd_totaloi_comparison.json` | CVD vs total OI (для истории) |

## Ключевые открытия

1. **YUR_NET level как фильтр**: WR растёт (+1.3pp) у 53/55 тикеров, но NetP80 стоит на месте (+0.02%). Фильтр улучшает качество, но не доходность.
2. **Покрытие prices_5m_oi**: только 57 из 238 тикеров — YUR_NET доступен не для всех.
3. **Total OI есть для всех 238**: oi_close в tradestats_fo — покрытие 100%, OI >0 для 100% баров.

## Что дальше — Correlation Analysis

Системное исследование связей CVD сигнала с данными:
- **OI** (total OI, yur_net, fiz_net — уровень и поток)
- **HHI** (hi2_fo — концентрация)
- **Alerts** (alerts_fo — 99.9% события)
- Разные комбинации и таймфреймы
