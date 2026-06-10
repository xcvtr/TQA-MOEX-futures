# 027 — Bar-Level Portfolio: правда о доходности OI Divergence

## Что сделано

### Mark-to-Market (MTM) в portfolio.py
- Добавлен `use_mtm: bool = True` в `simulate_adaptive_portfolio()`
- `_total_equity()` учитывает unrealized PnL через `last_price`
- DD-лимит срабатывает раньше, предотвращая каскадные убытки
- Результат с MTM: **+0.62%** (против +117.6% без MTM)

### Bar-Level Симуляция (scripts/bar_level_sim.py)
- Полноценная портфельная симуляция на OHLCV барах всех 47 тикеров
- Класс `BarLevelPortfolio` с 15 параметрами
- На каждом шаге: OHLCV close как current_price → MTM → DD-проверка
- Stop-loss на реальных ценах (фиксированный + ATR)
- Rollover по entry сигнала (лимитный ордер)
- Time-stop с score-каскадом
- Без trailing stop (exit_price — реальная цена закрытия по OHLCV)
- **7 секунд на walk-forward 72 комбо × 4 folds**

### Ключевое открытие
| Симуляция | Return | DD | Причина |
|-----------|--------|-----|---------|
| portfolio.py без MTM | +117.6% | 23.68% | exit_price, убытки не видны |
| portfolio.py с MTM | +0.62% | 20.75% | unrealized PnL виден |
| bar-level (OHLCV) | +2.92% | 20.66% | честные рыночные цены |

**OI Divergence не даёт статистического преимущества** при честном учёте риска. FIFO (+39.5% без MTM) — база.

### Исправления
1. Удалён trailing stop из bar-level симуляции (мешал позициям дойти до exit_price)
2. Rollover по entry сигнала (лимитный ордер), не по OHLCV close
3. `.ohlcv_cache.pkl` добавлен в .gitignore (89MB)

## Файлы
- `scripts/bar_level_sim.py` — bar-level портфельная симуляция
- `trading_bot/portfolio.py` — MTM + Score-каскад time-stop
- `.gitignore` — добавлен `*.pkl`

## Что дальше
- [ ] Проверить FIFO через bar-level (реальна ли +39.5%?)
- [ ] Искать стратегии с настоящим edge
- [ ] ERE (Expected Remaining Edge) — следующий уровень
