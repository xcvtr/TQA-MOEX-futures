# Phase 2 — Детальный разбор KEEP-тикеров

**Goal:** Для каждого из 24 KEEP-тикеров определить оптимальные параметры стратегии: z-score пороги, exit horizon, направление, WR/PF, сессионный профиль и сезонность.

**Данные:** PostgreSQL (БД `MOEX`), таблицы `moex_prices_5m_oi`, `openinterest_moex`, `moex_prices_5m`
**База:** скрипт `scripts/crowd_full_scan.py` — логика Volume Surge + FIZ/YUR divergence

---

### Task 1: Изучить код crowd_full_scan.py

**Objective:** Понять существующую логику загрузки данных, расчёта z-score, divergence и анализа профиля после сигнала.

**Files:**
- Читать: `scripts/crowd_full_scan.py`
- Читать: `strategies/crowd-bias/README.md`

**Step 1:** Прочитать оба файла и извлечь:
- Как загружаются 5m бары и OI
- Как считается z-score по объёму
- Как считается divergence FIZ↔YUR
- Как строится post-signal профиль
- Какие выходные метрики используются

**Step 2:** Записать ключевые функции и сигнатуры для использования в новом скрипте.

---

### Task 2: Написать scripts/phase2_detailed_analysis.py

**Objective:** Создать скрипт, который для заданного списка тикеров проводит полный анализ по 6 параметрам.

**Files:**
- Создать: `scripts/phase2_detailed_analysis.py`

**Скрипт должен:**
1. Принимать список тикеров (аргумент командной строки или hardcoded KEEP)
2. Для каждого тикера:
   - Загрузить 5m данные (OHLCV + OI) с 2023-01-01
   - Рассчитать Volume Surge (vol_z) — rolling z-score объёма за N баров
   - Рассчитать FIZ↔YUR divergence (div_z) — расхождение потоков
   - **Оптимизация z-порога:** перебрать пороги z от 1.5 до 3.0 с шагом 0.25 для vol_z и div_z
   - **Exit horizon:** для каждого сигнала посмотреть профиль через 15m, 30m, 1h, 2h, 4h (max, min, last, close)
   - **Направление:** оценить asymmetry — в какую сторону движение сильнее
   - **WR/PF:** close-based Win Rate и Profit Factor (entry=open+0.1%, TP=0.4%, SL=0.8%)
   - **Сессионный анализ:** утро (10-13 MSK), день (13-17), вечер (17-19)
   - **Сезонность:** распределение сигналов по месяцам
3. Вывести results в Markdown + CSV
4. Сохранить в `reports/phase2/<ticker>/` (по папке на тикер)

**Критические правила:**
- No look-ahead bias — rolling/z-score только на прошлых данных
- Все расчёты bar-by-bar, без использования целого периода
- Обработка ошибок: если тикер не найден — пропустить, не падать

---

### Task 3: Запустить Batch 1 — сильнейшие

**Objective:** Запустить phase2_detailed_analysis.py на первой партии (6 самых сильных тикеров).

**Тикеры:** GL, AF, CC, HY, NG, NR

**Команда:**
```bash
cd ~/projects/TQA-MOEX && python scripts/phase2_detailed_analysis.py GL AF CC HY NG NR
```

**Проверка:**
- `ls reports/phase2/` — 6 папок созданы
- В каждой папке `summary.md` и `details.csv`
- Все метрики имеют разумные значения (WR 40-80%, PF > 0.5)

---

### Task 4: Запустить Batch 2 — средние

**Objective:** Вторая партия тикеров.

**Тикеры:** OJ, PD, SE, SF, SP, SS

**Команда:**
```bash
cd ~/projects/TQA-MOEX && python scripts/phase2_detailed_analysis.py OJ PD SE SF SP SS
```

**Проверка:** Аналогично Task 3.

---

### Task 5: Запустить Batch 3 — оставшиеся

**Objective:** Третья партия KEEP-тикеров.

**Тикеры:** TN, TT, W4, YD

**Команда:**
```bash
cd ~/projects/TQA-MOEX && python scripts/phase2_detailed_analysis.py TN TT W4 YD
```

**Проверка:** Аналогично Task 3.

---

### Task 6: MAYBE-тикеры — повторная проверка

**Objective:** Перепроверить 10 MAYBE-тикеров — может, при оптимизированных порогах они дают сигнал.

**Тикеры:** BM, GK, IB, KC, ME, MM, PT, RN, SV, VB

**Команда:**
```bash
cd ~/projects/TQA-MOEX && python scripts/phase2_detailed_analysis.py BM GK IB KC ME MM PT RN SV VB
```

**Проверка:** Сравнить с KEEP — есть ли MAYBE, которые по WR/PF догоняют худших из KEEP?

---

### Task 7: Сводный отчёт

**Objective:** Собрать все результаты в единый отчёт — таблица лидеров по WR, PF, asymmetry.

**Files:**
- Создать: `reports/phase2/SUMMARY.md`

**Содержание:**
1. Top-10 по WR (close-based)
2. Top-10 по PF
3. Лучшие направления (LONG/SHORT)
4. Лучшие exit horizons
5. Оптимальные z-пороги (vol_z, div_z)
6. Сессионные паттерны
7. Сезонность
8. Вердикт по MAYBE
