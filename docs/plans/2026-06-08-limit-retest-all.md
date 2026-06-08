# План: Переделка всех стратегий (кроме OB) на лимитные ордера + аудит

## 1. Текущее состояние

**Все 4 стратегии** используют market entry: `entry = open[i+1]` (открытие следующего бара после триггерного).

| Стратегия | Файл | Функция | Триггер | Направление |
|:----------|:-----|:--------|:--------|:------------|
| Volume Surge | `engine.py` | `detect_signals()` | vol_z[i]≥thresh, fiz/yur divergence | LONG if yur_z[i]>0 |
| Mean Reversion | `reversion_engine.py` | `detect_mean_reversion_signals()` | 3-bar pattern + vol_z + wide range | SHORT if all_up |
| VWAP Deviation | `vwap_engine.py` | `detect_vwap_signals()` | close[i] deviates > dev_thresh×ATR | SHORT if dev>0 |
| OI Divergence | `new_strategies.py` | `detect_oi_divergence_signals()` | OI diverges from price at extreme | SHORT if bearish div |

## 2. Новая entry-логика (единая для всех)

После триггерного бара i (который закрылся и подтвердил сигнал), вместо market на open[i+1]:

**Лимитный ордер на экстремуме триггерного бара:**
- LONG: `limit_price = low[i]` (купить на минимуме импульса)
- SHORT: `limit_price = high[i]` (продать на максимуме импульса)
- Ждать `limit_lookback = 5` баров для исполнения
- Искать fill_bar: для LONG — `low[j] <= limit_price`, для SHORT — `high[j] >= limit_price`
- Если заполнился: `entry = limit_price`, `exit = close[fill_bar + horizon]`
- Если не заполнился за 5 баров: сигнал пропускается
- Если fill_bar + horizon >= n: пропустить

**Параметры (добавить в каждый config):**
```python
'limit_lookback': 5,
'use_limit': True,  # если False — сохранить старую market логику для сравнения
```

## 3. Что менять в каждом файле

### 3a. engine.py (Volume Surge)
- Новая функция: `detect_signals_limit(symbol, rows, config)` 
- Копирует detect_signals, меняет entry-логику
- Старая функция сохраняется как `detect_signals()` (для сравнения)
- ticker configs: HS, KC, DX, HY, BM

### 3b. reversion_engine.py (Mean Reversion)
- Новая функция: `detect_mean_reversion_signals_limit(symbol, rows, config)`
- Меняет entry на limit
- tickers: NM, AF

### 3c. vwap_engine.py (VWAP Deviation)
- Новая функция: `detect_vwap_signals_limit(symbol, df, config)`
- Меняет entry на limit
- tickers: GZ, Eu, SR, Si, MC

### 3d. new_strategies.py (OI Divergence)
- Новая функция: `detect_oi_divergence_signals_limit(merged, config)`
- Меняет entry на limit
- tickers: RI, GL, Si

## 4. Бэктест-скрипт: scripts/limit_retest_all.py

Запустить все 4 стратегии на ВСЕХ тикерах, сравнить market vs limit entry.

**Входные параметры:**
- Стратегия (имена из `__init__.py`)
- Все тикеры данной стратегии
- days=30 (5m для VS/Reversion/VWAP, 5m+OI для OI Div)

**Output CSV в `docs/plans/limit_retest_results/`:**
```
market_vs_limit_comparison.csv — сводная таблица:
  strategy, ticker, entry_type, horizon, n, wr, pf, avg_return, max_dd, fill_rate
```

**Параметры horizon для каждой стратегии (как в конфиге):**
- VS: [6, 12, 24]
- Reversion: [6, 12]
- VWAP: [6, 12, 24]
- OI Div: [3, 6, 12]

**limit_lookback = 5** для всех (как в OB).

## 5. Аудит (делать после написания кода, ДО запуска)

### 5a. Look-ahead bias
- [ ] Rolling median/window — только по предыдущим барам
- [ ] Trigger bar i — используется close[i], vol_z[i] — ЭТО ок, но entry не может быть на open[i]
- [ ] Для market entry: entry = open[i+1] — корректно (на открытии следующего бара)
- [ ] Для limit entry: проверяем fill начиная с i (триггерного бара), entry = limit_price — корректно (лимит на уровне внутри триггерного бара)
- [ ] OI данные — проверка, что OI[i] известен к моменту close[i]

### 5b. Direction-specific return
- [ ] LONG: `(exit - entry) / entry * 100`
- [ ] SHORT: `(entry - exit) / entry * 100`
- [ ] Проверить ВСЕ 4 функции на sign error

### 5c. Fill rate sanity
- [ ] fill_rate не может быть 100% (иначе это market entry, а не limit)
- [ ] fill_rate не может быть 0% на тикере с 1000+ сигналов (баг в поиске fill)
- [ ] Если fill_rate > 95% — подозрительно, проверить логику

### 5d. Config consistency
- [ ] Все новые параметры (`limit_lookback`, `use_limit`) добавлены в `DEFAULT_*_CONFIG` в `__init__.py`
- [ ] В `cron_scanner.py` подставлены правильные имена функций
- [ ] `max_signal_age` или фильтр свежести учтён

### 5e. Signal dict format
- [ ] Все signal dict содержат `ticker`, `direction`, `entry`, `time`, `return_pct`, `strategy`, `idx`
- [ ] Для limit: добавить `fill_bar`, `ob_level` (для OI — не нужно)

## 6. Критерии приёмки

- [ ] Limit WR не ниже Market WR более чем на 10% (иначе limit логика сломана)
- [ ] fill_rate 30-70% для каждой стратегии
- [ ] Min 50 сигналов на тикер для статистической значимости
- [ ] Хотя бы 3/4 стратегий показывают WR > 55% на limit entry
- [ ] PF > 1.2 после перехода на limit
- [ ] Аудит прошёл — 0 ошибок look-ahead, 0 ошибок direction

## 7. Output

Скрипт печатает в stdout:
```
=== MARKET vs LIMIT COMPARISON ===
Strategy     | Ticker | Market WR | Limit WR | Fill% | ΔWR
Volume Surge | HS     | 58.2%     | 56.1%    | 48.3% | -2.1%
...
```

И сохраняет CSV в `docs/plans/limit_retest_results/`.
