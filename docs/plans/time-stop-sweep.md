# План: Time-Stop + PF Оптимизация + FIFO Сравнение + Полный Sweep

## Контекст
Проект: TQA-MOEX (/home/user/projects/TQA-MOEX)
Сигналы: OI Divergence, horizon=12, score>=0.3, 7093 сигнала, 47 тикеров
Кэш: .signals_cache.json (7093 сигнала)
Капитал: 100,000 RUB

## Текущая проблема
Позиции в simulate_adaptive_portfolio() не имеют механизма принудительного выхода, кроме rollover (новый сигнал по тому же тикеру) и eviction (вытеснение по приоритету/скору).
→ Слабые сигналы застревают в портфеле и тянут капитал вниз.
→ Нужен time-stop: закрывать позицию через N баров после входа, если её не закрыл rollover или eviction.

## Задача 1 — Добавить Time-Stop в portfolio.py

Файл: trading_bot/portfolio.py, функция simulate_adaptive_portfolio()

Что сделать:
1. Добавить параметр `max_hold_bars: int = 40` в сигнатуру функции (параметр по умолчанию = 40, чтобы не ломать старые вызовы)
2. Каждой открытой позиции в active[tk] добавить поле `'bars_held': 0` при создании
3. На каждом новом баре (сигнале) инкрементировать `bars_held` для всех активных позиций
4. Позиции, у которых `bars_held >= max_hold_bars`, принудительно закрывать по entry_price (т.е. с нулевым PnL по цене — exit = entry, так как мы не знаем реальную цену закрытия)
   ВАЖНО: без look-ahead — закрываем на текущем баре по текущей цене сигнала (entry price)
5. Освобождённый капитал после time-stop сразу доступен для новых позиций
6. Time-stop срабатывает ДО того, как проверяется rollover для текущего сигнала — приоритет: time-stop > rollover > eviction > new entry

Проверка:
- После добавления запустить: python3 -c "from trading_bot.portfolio import simulate_adaptive_portfolio; print('OK')"
- Скрипт portfolio_sweep.py должен импортировать функцию без ошибок

## Задача 2 — Оптимизация PF (поиск лучших параметров)

Запустить portfolio_sweep.py. В нём уже есть:
- simulate_adaptive_portfolio() с v2 (score_sizing, score_eviction, atr_stop=2.0, score_decay)
- simulate_adaptive() (FIFO)
- param_grid: mu=[0.10, 0.15, 0.20], mc=[2,3,5,8], tm=[0.15,0.20,0.30], sl=[0.01,0.02]
- => 3 × 4 × 3 × 2 = 72 комбинации

Запустить: cd /home/user/projects/TQA-MOEX && python3 -u scripts/portfolio_sweep.py

Это займёт ~30-60 секунд с кэшем (сигналы уже загружены из .signals_cache.json).

## Задача 3 — Сравнение PF vs FIFO (честное, без look-ahead)

Результаты уже есть в output portfolio_sweep.py — он выводит TOP-10 для обоих методов при DD<=10%, DD<=15%, DD<=20%.

## Задача 4 — Результаты сохранить

Результаты сохраняются в docs/plans/strategy_v3/portfolio_results.txt.
Прочитать этот файл после завершения и вывести самое важное:
- Лучшая комбинация PF по Calmar (ret%, DD%, Calmar, trades, params)
- Лучшая комбинация FIFO по Calmar
- Сравнение: на сколько % PF лучше FIFO при равной просадке
- Средние значения по всем комбинациям для обоих методов
