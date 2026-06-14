# Checkpoint 057: NM both-direction portfolio — финальный результат

## Контекст

Продолжение чекпойнта 056. После отсева трендовых стратегий (GL, GD, LK, Si, RN — работают только в одну сторону) остался NM (Норильский Никель) — единственный тикер, где long и short оба дают положительный результат.

## Конфигурация портфеля

**6 стратегий (3 паттерна × 2 направления), все с chandelier exit:**

| # | Паттерн | Напр | Hold | ATR | Смысл |
|---|---------|------|------|-----|-------|
| 1 | vol_up_oi_up_yb_up | Long | 8 | 2 | Рост объёма + OI + юр-покупки |
| 2 | vol_up_oi_up_yb_up | Short | 8 | 2 | То же, шорт |
| 3 | vol_up_yb_down_fiz_up | Long | 21 | 2 | Рост объёма + падение юр-покупок + физ-экстрим |
| 4 | vol_up_yb_down_fiz_up | Short | 21 | 2 | То же, шорт |
| 5 | smart_money | Long | 13 | 2 | Рост объёма + рост юр-покупок + падение физ-нет |
| 6 | smart_money | Short | 13 | 2 | То же, шорт |

**Параметры:**
- Капитал: 200,000₽
- CS (contract size): 10
- MAX_LOT: 5
- MAX_LEV: 3.0x
- RISK_PCT: 2%
- Комиссия: 4₽/контракт
- Реинвест: да (равная доля капитала на каждую из 6 стратегий)
- Период: 2024-01-03 — 2026-04-30 (601 торговых дней)

## Результаты

### Общие
| Метрика | Значение |
|---------|----------|
| **Return** | **+73.1%** |
| **Max DD** | **4.2%** |
| **Calmar** | **17.6** |
| **Win Rate** | 48% (26/54) |
| **Profit Factor** | 2.68 |
| **Net PnL** | +146,248₽ |
| **Final Equity** | 346,248₽ |

### По годам
| Год | PnL | Return | WR | Trades |
|-----|-----|--------|----|--------|
| 2024 | +99,169₽ | +49.6% | 50% | 26 |
| 2025 | +34,244₽ | +11.4% | 45% | 20 |
| 2026 (4 мес) | +12,834₽ | +4.3% | 50% | 8 |

### Аудит сигналов
- 54 raw signal за 2.3 года = ~2 сигнала/месяц
- Каждая из 6 стратегий даёт 8-10 сигналов за период
- Chandelier срабатывает (SL) в ~50% случаев, остальные — по холду

## Дальнейшие шаги

1. **Вернуться к другим тикерам** — проверить VB, SR, Eu на both-direction с оптимизацией
2. Walk-forward NM портфеля
3. Добавить live-скринер на NM
4. Запустить NM портфель в paper trader

## Скрипты

- `reports/triz_phase4/nm_portfolio/portfolio_nm_final.py` — финальный портфель с аудитом
- `reports/triz_phase4/nm_portfolio/sweep_nm_both.py` — полный sweep NM
- `reports/triz_phase4/nm_portfolio/portfolio_clean.py` — чистый портфель
- `reports/triz_phase4/nm_portfolio/portfolio_final.py` — финальный скрининг
- `reports/triz_phase4/nm_portfolio/test_all_both.py` — long vs short по всем тикерам
- `reports/triz_phase4/nm_portfolio/result.json` — полный JSON отчёт

## Ссылки

- Дашборд: http://localhost:5059
- Cluster DOM: http://localhost:5052
- CH: 127.0.0.1:8123 (moex.prices_5m, moex.prices_5m_oi)
- Репозиторий: github.com/xcvtr/TQA-MOEX.git
