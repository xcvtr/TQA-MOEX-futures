# 036 — MOEX OI Dashboard + физ/юр структура данных

**Дата:** 12 июн 2026

## MOEX OI Dashboard

**Запущен:** `http://10.0.0.60:5058`

5 графиков на canvas:
1. **Цена + 4 OI линии** — цена на правой шкале + fiz_long/short, yur_long/short в %
2. **Fiz Long/Short** — физики отдельно
3. **Yur Long/Short** — юрики отдельно
4. **Crowd Share** — доля fiz объёма в total OI
5. **Fiz z-score** — наш сигнал (z-score fiz_net за W=40)

Переключение: тикер / TF (5m, 15m, H1) / период (1-30d)
Обновление: каждые 60s

## Структура OI данных

**Сырые данные** → `moex.openinterest`:
```
symbol, time, buy_orders, sell_orders, buy_accounts, sell_accounts, clgroup
                                                                    └── 0=FIZ, 1=YUR
```
Источник: MOEX ISS API `/analyticalproducts/futoi/securities/{ticker}.csv`

**Агрегированные** → `moex.prices_5m_oi`:
```
time, symbol, fiz_buy, fiz_sell, yur_buy, yur_sell, total_oi
```

## Ключевое открытие: fiz_net = -yur_net

Проверено на 21 тикере — **corr(fiz_net, yur_net) = -1.0 у всех**. Это не баг, а закон сохранения: каждая сделка — FIZ покупает, YUR продаёт. fiz_net = -(yur_net) всегда.

**Сигнал divergence (`fiz_z - yur_z`) = `2 * fiz_z`** — просто удвоенный z-score fiz_net.

**Signal V2 (`yur*2 - fiz`) = `-3 * fiz_z`** — fiz_net с обратным знаком.

**Никакого расхождения fiz/yur не существует.** Стратегия работает не как divergence, а как **retail flow reversal**: когда fiz_net аномально высок (fiz_z > 2), физики перекуплены → SHORT. Когда fiz_net аномально низок (fiz_z < -2) → LONG.

## Файлы

- `moex_oi_dashboard.py` — дашборд
- `scripts/phase3f_audit.py` — проверенная симуляция с ATR-стопами
- `scripts/phase3c_divergence_behavior.py` — анализ по режимам
