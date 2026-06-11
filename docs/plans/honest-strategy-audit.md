# План: ПРАВДИВЫЙ тест всех стратегий + поиск 100% годовых при DD ≤ 20%

## Контекст
Все существующие стратегии тестировались через portfolio.py с exit_price — это артефакт. 
Bar-level симуляция (scripts/bar_level_sim.py) использует OHLCV close для стопов и mark-to-market — показывает ПРАВДУ.

## Этап 1: Аудит всех стратегий

Проверить ВСЕ стратегии из trading_bot/new_strategies.py через bar_level_sim:
- detect_oi_divergence_signals_limit (уже есть, ~3%)
- detect_mean_reversion_signals (?)
- detect_volume_climax (?)
- detect_whale_trap (?)
- Все остальные detect_* функции

Для каждой:
1. Собрать сигналы (score >= 0.3, horizon=12, как OI Divergence)
2. Запустить bar-level симуляцию (params из portfolio.py best)
3. Записать ret%, DD%, Calmar, trades, exit_reasons

## Этап 2: ТРИЗ-дизайн стратегии

Противоречие: стратегия должна давать 100% годовых при DD ≤ 20%, но сигналы не предсказывают будущую цену точно.

ИКР: Стратегия, где entry = реальная исполнимая цена, exit = реальная исполнимая цена, а между ними — защита капитала stop-loss.

Принципы:
- **Проскок**: вход до движения (лимитный ордер на low/high)
- **Асимметрия**: big wins на редких движениях, small losses на частых
- **Динамичность**: параметры входа зависят от волатильности
- **Дробление**: несколько независимых стратегий, ensemble
- **Матрёшка**: partial take-profit + trailing остатка

Кандидаты:
1. **Mean Reversion v2**: отклонение z-score > 2.0 от скользящей, вход по market (open next bar), выход по return to mean (close when z-score < 0.5). Честный entry (market) и честный exit (at close).
2. **Limit Order Reversion**: entry по limit (low для LONG при z-score > 2), exit по limit (high при z-score < 0). ОБЕ стороны — лимитные ордера. Максимальная реалистичность.
3. **Volatility Breakout**: entry при пробое ATR-канала, stop-loss на противоположной стороне, trailing profit.
4. **OI Divergence + wide stops**: те же сигналы OI, но stop-loss = 5% (вместо 1%), time-stop = 80 bars. Дать позициям дышать.

## Этап 3: Оптимизация

Для лучшей стратегии:
1. Grid scan: mu=[0.05,0.10,0.15,0.20], mc=[2,3,5,8], tm=[0.10,0.15,0.20,0.30], sl=[0.01,0.02,0.03,0.05]
2. Walk-forward: 4 folds, отсеять комбинации с любым минусовым fold
3. Выбрать топ-3 по Calmar
4. Проверить: ensemble из топ-3 комбинаций (каждая управляет ⅓ капитала)

## Требования
- Все тесты через BarLevelPortfolio.run() (OHLCV close, MTM)
- Walk-forward stability check обязателен
- exit_reason анализ: stop_loss/atr_stop не должны доминировать
- Результат: таблица стратегий с ret%, DD%, Calmar trades
- Цель: хотя бы одна стратегия ≥ 80% return при DD ≤ 20% (на stretch цель 100%)
