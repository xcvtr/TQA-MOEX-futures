# 030 — BR Mean Reversion: первая честная стратегия (Calmar 8.27)

## Что сделано

### 5 направлений TRIZ-поиска (ночь 1)
1. **Межрыночный анализ** — GL-ED reversion 58.7% (marginal)
2. **Фьючерсная кривая** — BR contango → Long 77% win rate (сильный, но мало данных)
3. **Фундаментальные факторы** — trend persistence, нет edge
4. **ML Gradient Boosting** — SR AUC 0.567, GZ AUC 0.565 (слабый)
5. **DOM/Стакан** — недостаточно данных (2 недели)

### BR Contango → Daily SMA Mean Reversion (ночь 2)
**Первая стратегия, прошедшая честный тест:**

| Метрика | Значение |
|---------|----------|
| Сигнал | SMA5 < SMA20 → LONG, hold=5 дней |
| Return | +53.05% за 3.5 года (~15% годовых) |
| Max DD | 6.41% |
| **Calmar** | **8.27** ✅ |
| Trades | 94 (59% win rate) |
| Stop-loss сработал | 2/94 (2%) |
| **Walk-forward** | **6/27 комбо стабильны во всех folds** ✅ |

### Инструменты
- `scripts/daily_bar_level.py` — DailyPortfolio (daily OHLCV, MTM, stop-loss, trailing)
- `scripts/br_contango_strategy.py` — полный пайплайн + walk-forward
- `scripts/audit_strategies.py` — аудит всех 7 стратегий через bar-level
- Скрипты 5 TRIZ-направлений

### Ключевое открытие
- Все старые стратегии (OI Divergence, OTC, VWAP и др.) — шум через bar-level
- **Единственный реальный edge:** daily mean reversion SMA5/SMA20 на BR
- Не 100% годовых, но **15% с Calmar 8.27** — первый честный результат

## Файлы
- `trading_bot/portfolio.py` — MTM + Score-каскад time-stop
- `scripts/bar_level_sim.py` — 5m bar-level портфель
- `scripts/daily_bar_level.py` — daily портфель
- `scripts/br_contango_strategy.py` — работающая стратегия
- Отчёты по 5 направлениям в `reports/`

## Что дальше
- [ ] Комиссии MOEX — реальная доходность
- [ ] Фильтры (volatility, ADX, макро-календарь)
- [ ] Ensemble стратегий
- [ ] Проверка на других инструментах (Si, RB)
