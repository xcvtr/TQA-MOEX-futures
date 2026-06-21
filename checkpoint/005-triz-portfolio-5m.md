# Checkpoint: Phase 5.3 — ТРИЗ-портфель 5m

**Дата:** 2026-06-14  
**Проект:** TQA-MOEX  
**Предыдущий этап:** Phase 5.2 (score-based + Kelly)  
**Текущий этап:** Phase 5.3 (MTM + Collar + ACB + Kelly adaptive)

---

## Результат финального теста

| Метрика | Значение |
|:--------|:--------:|
| Начальный капитал | 100,000 ₽ |
| Конечный капитал | 173,937 ₽ |
| Доходность (полная) | +73.9% |
| **Годовая доходность** | **+27.0%** |
| **Max DD** | **2.8%** |
| **Calmar** | **9.47** |
| Win Rate | 45.8% |
| Сделок | 33,892 |
| Период | 847 дней (2024-01-03 → 2026-04-29) |
| Circuit Breaker | ❌ Не сработал |

## Портфель

### Core (LONG)
| Тикер | Паттерн | PnL | WR | Сделок |
|:------|:--------|:---:|:--:|:------:|
| GL (Золото) | vod_L | +16,730 ₽ | 48% | 5,607 |
| RN (Роснефть) | vou_L | +24,203 ₽ | 49% | 4,969 |
| HY (HY-акции) | vou_L | +9,443 ₽ | 47% | 3,313 |
| NM (НорНикель) | vod_L | +2,288 ₽ | 46% | 4,971 |
| AF (Аэрофлот) | sm_L | +10,276 ₽ | 42% | 4,327 |

### Hedge (SHORT)
| Тикер | Паттерн | PnL | WR | Сделок |
|:------|:--------|:---:|:--:|:------:|
| BR (Brent) | vyf_S | +8,679 ₽ | 47% | 2,443 |
| SF (S&P500) | vod_S | +4,700 ₽ | 44% | 2,222 |
| SV (Серебро) | vod_S | -2,369 ₽ | 42% | 6,033 |

### Collar
| Si (USD/RUB) | S | -13 ₽ | 14% | 7 | — не сработал |

## Механизмы
- **Score-based entry** (порог 0.4 L / 0.3 S)  
- **Adaptive Kelly** (скользящее окно 50 сделок, clamp 3%-20%)  
- **ATR-стоп** (per-ticker множитель: 2-5x ATR)  
- **Time-stop** (hold=5,8,13,21 баров по настройке)  
- **Score fade exit** (выход при ослаблении сигнала <0.15)  
- **Collar hedge** — 30% позиции Si SHORT при открытых LONG  
- **Circuit Breaker** — 15% DD → стоп  

## Проблемы
1. Si collar: 7 сделок — неэффективен на 5m (микро-хедж в 0.3% портфеля)
2. SV SHORT: -2.4K — убыточен
3. WR в целом 45.8% — ниже 50% означает шум 
4. Только 27%/год — далеко от сотен процентов

## Конфиги Phase2 WFA (исходные)
```python
# Из phase2_fullscan.json, top10 по Calmar OOS
'SF': {'pattern': 'vod', 'direction': 'S', 'hold': 8,  'atr_mult': 3, 'weight': 0.3},
'GL': {'pattern': 'vod', 'direction': 'L', 'hold': 21, 'atr_mult': 2, 'weight': 1.0},
'RN': {'pattern': 'vou', 'direction': 'L', 'hold': 5,  'atr_mult': 5, 'weight': 1.0},
'BR': {'pattern': 'vyf', 'direction': 'S', 'hold': 13, 'atr_mult': 5, 'weight': 1.0},
'SV': {'pattern': 'vod', 'direction': 'S', 'hold': 5,  'atr_mult': 5, 'weight': 1.0},
'AF': {'pattern': 'sm',  'direction': 'L', 'hold': 21, 'atr_mult': 2, 'weight': 1.0},
'HY': {'pattern': 'vou', 'direction': 'L', 'hold': 5,  'atr_mult': 5, 'weight': 1.0},
'NM': {'pattern': 'vod', 'direction': 'L', 'hold': 21, 'atr_mult': 3, 'weight': 1.0},
```

## Скрипты
- `scripts/phase5_triz_final.py` — финальный портфельный тест
- `scripts/phase5_triz_portfolio.py` — первый ТРИЗ-портфель
- `scripts/phase5_portfolio_5m.py` — базовый 5m тест
- `reports/phase5_triz/final_result.json` — результат
