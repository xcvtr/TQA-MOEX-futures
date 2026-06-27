# Архитектура проекта TQA-MOEX-futures

## 🏗 PG структура

Одна БД `moex` на 10.0.0.60, схемы по типам инструментов:

```
moex
├── futures                    ← фьючерсы (наш проект)
│   ├── ticker_specs           ← ГО, лотность, шаг цены
│   └── strategy_cvd_*         ← CVD-стратегия (portfolio, params, trades…)
├── shared
│   └── calendar               ← макро-календарь (CBR, праздники)
├── stocks / options / bonds   ← когда появятся
└── public (пусто)
```

**Правило:** схема = тип инструмента (не стратегия, не проект).\
**Исключение:** `shared` для кросс-рыночных данных (календарь).

## 📁 Структура проекта

```
strategies/
  cvd/                          ← одна стратегия
    __init__.py                 ← экспортирует prod (по умолчанию)
    prod/                       ← стабильная версия (paper/live)
      engine.py                 ← ядро сигнала, immutable
      lib.py                    ← PG утилиты, PnL, slippage
      paper_trader.py           ← executor (крон)
    dev/                        ← эксперименты (копия prod, можно править)
      engine.py                 ← модифицированная версия для тестов
    scripts/                    ← вспомогательные скрипты
      analyze_tpsl.py
      scan.py
      wf_*.py
      mtm_portfolio.py
      analyze_tpsl.py           ← P80/P20 анализ
      scan.py                   ← correlation scan
      wf_*.py                   ← walk-forward / backtest
      mtm_portfolio.py          ← портфельный MTM
      paper_trader.sh

  oi_div/                       ← следующая стратегия (когда появится)

scripts/                        ← общие утилиты, не привязанные к стратегии
```

## 🧠 Принципы

1. **Схема = рынок** (futures, stocks, options), а не стратегия
2. **Новая стратегия = новая папка в `strategies/`**, новый код не трогает старый
3. **Engine immutable в проде** — эксперименты через новый engine, не правку существующего
4. **PG — единый источник конфигов**, хардкода нет
5. **`shared.` только для кросс-рыночных данных**
6. **TP/SL = P80/P20 per symbol per direction**, из `futures.strategy_cvd_portfolio`
7. **Конфиги не в JSON/файлах** — всё в PG с репликацией

## 🔑 Текущее состояние (chkpt 103b)

- CVD-портфель: 18 тикеров (FV, OZ, TI, AS, VI, DL, S0, PS, Si, FN, TN, SS, W4, WU, GZ, IP, RB, CR, GC)
- TP/SL: P80/P20, long/short отдельно
- Specs: 64 тикера в `futures.ticker_specs`
- Engine: `strategies/cvd/engine.py` — чистый сигнал, без зависимостей
- PG библиотека: `strategies/cvd/lib.py` — загрузка из PG, PnL, slippage
- Следующий шаг: интеграция TP/SL в paper trader

## ⚠️ Force push

Если не работает `git pull`:
```
git fetch --force && git reset --hard origin/main
```
