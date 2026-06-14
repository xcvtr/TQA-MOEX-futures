# Checkpoint 056: NM both-direction portfolio — отсев трендовых стратегий

## Контекст

**Проблема:** стратегия GL hold=13 chandelier atr_mult=2 даёт +639.7%, но это чистая ловля восходящего тренда золота. Short на GL не работает (−0.1%). Нужна стратегия, которая работает в обе стороны (long + short) и не зависит от направления тренда.

**Проверка:** протестированы все тикеры портфеля (GL, GD, NM, VB, SR, Eu, LK, Si, RN и др.) на chandelier hold=13 atr=2 в long vs short:

| Инструмент | Long | Short | Вердикт |
|-----------|------|-------|---------|
| **GL** (Gold) | +50% | -0.1% | ❌ Тренд |
| **GD** (Gold big) | +34% | -12% | ❌ Тренд |
| **LK** (Лукойл) | -29% | +330% | ❌ Тренд (шорт) |
| **Si** (Серебро) | -79% | +453% | ❌ Тренд (шорт) |
| **RN** (Роснефть) | -26% | +292% | ❌ Тренд (шорт) |
| **VB** (VTB) | +16% | +16% | ⚠️ Частично |
| **SR** (Сбер) | +67% | +63% | ⚠️ Частично |
| **Eu** (Евро) | +25% | +228% | ⚠️ Нестабильно (cs=1000) |
| **NM** (НорНикель) | +31% | +82% | ✅ **Работает в обе стороны** |

**Только NM** прошёл фильтр: long +31%/DD 18.9%, short +82%/DD 8.2%, both **+113%/DD 3.8%**, Calmar 29.8.

## Результаты NM

### Конфигурация
- **Сигнал:** vol_up_yb_down_fiz_up (volume up + yur_buy down + fiz_net extreme)
- **Long:** hold=13, atr_mult=3, sl=0.005
- **Short:** hold=21, atr_mult=3, sl=0.005  
- **Chandelier exit** в обе стороны
- **Капитал:** 200K, реинвест
- **Лимит:** MAX_LOT=5, MAX_LEV=3.0
- **Комиссия:** 4₽ на контракт
- **CS (contract size):** 10
- **GO (margin):** 256₽

### Результат портфеля (long+short с реинвестом)
- **Return: +164%** за 2 года (2024-01 — 2026-05)
- **Max DD: 5.8%**
- **Calmar: 28.1**
- **Win Rate: 62%**
- **Trades: 29**
- **Net PnL: +328,482₽** с 200K

### NM long сам по себе (vol_up_oi_down, hold=13, atr=3)
- **+79.0%**, DD 12.2%, Calmar 6.5, 9 trades, WR 67%

### NM short сам по себе (vol_up_yb_down_fiz_up, hold=21, atr=3)
- **+200.1%**, DD 14.2%, Calmar 14.1, 10 trades, WR 70%

### Скрипты
- `~/projects/TQA-MOEX/reports/triz_phase4/megagrid.py` — основной мегагрид (long-only)
- `/tmp/portfolio_final.py` — финальный тест NM портфеля
- `/tmp/portfolio_clean.py` — чистый портфель с реинвестом
- `/tmp/test_all_both.py` — тест long vs short по всем тикерам
- `/tmp/test_gl_both.py` — тест GL long/short
- `/tmp/test_gl_short.py` — тест GL short-only

### Данные
- ClickHouse: 127.0.0.1:8123, БД moex
- Таблицы: moex.prices_5m, moex.prices_5m_oi
- Период: 2024-01-01 — 2026-05-01

### Дашборды
- Megagrid: http://localhost:5059
- Cluster DOM: http://localhost:5052

## Что дальше
1. Запустить NM портфель с полным sweep (все hold/atr/pattern комбинации)
2. Добавить walk-forward проверку
3. Написать live-скринер сигналов NM
4. Подключить к paper trader или live счёту

## Ссылки
- Репозиторий: github.com/xcvtr/TQA-MOEX.git
- Чекпойнт 055: dev-сессия с матрикс-тестами
- Чекпойнт 054: megagrid dashboard
- Чекпойнт 053: аудит sl_pct бага
