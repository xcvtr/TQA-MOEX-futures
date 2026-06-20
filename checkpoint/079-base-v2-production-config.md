# 033 — BASE v2 Final: Grid Search + OOS + Walk-Forward + Kelly — Production Config (2026-06-15)

## Контекст
Оптимизировали 4 параметра стратегии Volume × OI на MOEX фьючерсах. Начали с поиска улучшений (ADX, TOD, RAW_OI — все проиграли), пошли вглубь — grid search по параметрам BASE (score, bars, stop, leverage). Прошли полную цепочку верификации.

## Полная цепочка аудита

### 1. Grid Search (checkpoint 031)
- **Лучшая комбинация**: score>0.10, bars=8, stop=1.0ATR, lev=0.50
- **GL**: Ret=345.7%, DD=4.2%, Calmar=81.9 (vs BASE 55.6%/13.2)
- **Параметры**: score_thresh=0.10, bars_left=8, stop_atr=1.0, leverage=0.50

### 2. OOS Validation (checkpoint 032)
- **2024 OOS** (чистый out-of-sample): 7/7 тикеров побед 🟢
  - BEST avg Ret=91.6%, avg Calmar=16.3 vs BASE 14.3%/6.0
- **2025 INS**: 7/7 🟢
- **FULL (2024-2026)**: 7/7 🟢
- **2023-2024**: 6/7 🟢 (DX паритет)

### 3. Walk-Forward
- 6m train → 3m test, скользящее окно
- **0/6 тикеров** — WF проигрывает фиксированным параметрам
- **Вывод**: фиксированные параметры стабильнее адаптивных. WF не нужен.

### 4. Kelly Sizing
- Adaptive Kelly на окне 200 сделок, fractional=0.5
- **0/6 тикеров** — Kelly проигрывает фиксированному lev=0.50
- Причина: WR=36-40%, payoff < 1.5 → Kelly даёт f* < 0.05 → позиция слишком мала
- **Вывод**: фиксированный leverage 0.50 остаётся лучшим

### Портфельный тест BEST vs BASE (FULL 2024-2026)

| Тикер | V2 Ret | V2 DD | V2 Calmar | BASE Ret | BASE DD | BASE Calmar |
|-------|--------|-------|-----------|----------|---------|-------------|
| GL | 1057.2% | 4.6% | **230.7** | 113.0% | 4.5% | 25.3 |
| HS | 882.3% | 4.6% | **193.1** | 85.4% | 4.0% | 21.4 |
| HY | 950.8% | 10.2% | **93.4** | 66.8% | 5.4% | 12.3 |
| DX | 18.6% | 33.2% | **0.6** | -37.7% | 40.4% | -0.9 |
| RN | 586.8% | 6.2% | **95.2** | 36.4% | 6.6% | 5.5 |
| NM | 543.7% | 8.4% | **64.5** | 31.7% | 9.3% | 3.4 |
| AF | 1142.3% | 12.4% | **92.1** | 61.6% | 11.3% | 5.4 |

**Итог: 7/7 🟢 все тикеры улучшены.**

### DX — исключён из портфеля
DX не даёт профита ни в одной конфигурации (Calmar < 1). Исключён из CORRELATION_GROUPS_FOR_SWEEP.

## Конфигурация (зафиксирована в config.py)

```python
BASE_V2_SCORE_THRESH = 0.10
BASE_V2_BARS_LEFT = 8
BASE_V2_STOP_ATR = 1.0
BASE_V2_LEVERAGE = 0.50
```

## Скрипты
- `scripts/grid_search_base.py` — двухраундовый grid search (однопроходный)
- `scripts/oos_validation.py` — OOS-валидация на 4 периодах
- `scripts/walk_forward_opt.py` — walk-forward оптимизация (6m train + 3m test)
- `scripts/kelly_sizing.py` — Kelly adaptive sizing
- `scripts/portfolio_sweep_enhancements.py` — портфельный тест
- `config.py` — BASE_V2_* конфигурация

## Что дальше
1. Добавить SHORT-сигналы (сейчас только LONG)
2. Алготрейдинг — deployment через брокера
3. Опционное хеджирование поверх фьючерсной позиции
