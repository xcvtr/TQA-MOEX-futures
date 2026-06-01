# TQA-MOEX: Manipulation Detection on Moscow Exchange Futures

Аналитическая система для выявления манипулятивных паттернов на срочном рынке MOEX (ФОРТС). Использует **5-минутные свечи (Alor OpenAPI V2)** и **Open Interest с разделением физ/юр лица (ISS API)** для обнаружения охоты за ликвидностью розничных трейдеров.

**Ключевая идея**: толпа (физлица, FIZ) систематически проигрывает умным деньгам (юрлица, YUR). Система отслеживает аномалии в поведении толпы и выдаёт сигналы на вход ПРОТИВ неё.

---

## Архитектура

```
┌──────────────────────────────────────────────────────────────┐
│                     Источники данных                          │
│                                                              │
│  Alor OpenAPI V2          MOEX ISS API                       │
│  ──────────────           ─────────────                      │
│  5m бары (история)        D1 бары (история)                  │
│  Текущие котировки        Open Interest (FIZ/YUR)            │
│                           Текущий рынок (snapshot)           │
└──────────┬───────────────────────┬───────────────────────────┘
           │                       │
           ▼                       ▼
┌──────────────────────────────────────────────────────────────┐
│                    PostgreSQL (10.0.0.60)                     │
│                                                              │
│  moex_prices_5m    moex_prices      openinterest_moex        │
│  ──────────────    ───────────      ─────────────────        │
│  5m OHLCV + con-   D1: open,high,   OI с разделением         │
│  tract (59 тике-   low,last,vol,    FIZ/YUR для 64 ти-       │
│  ров Alor)         OI, settle       керов ISS                │
└──────────────────────┬───────────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────────┐
│                   Детекция манипуляций                        │
│                                                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐   │
│  │ OI-сигналы    │  │ Ценовые      │  │ Потоковые        │   │
│  │              │  │ паттерны     │  │ сигналы          │   │
│  │ • OI_TRAP    │  │ FALSE_BREAK  │  │ FLOW_EXTREME     │   │
│  │ • OI_EXTREME │  │ STOP_HUNT    │  │ FLOW_DIVERGENCE  │   │
│  │ • OI_DIVERGE │  │ VOL_CLIMAX   │  │                  │   │
│  └──────────────┘  └──────────────┘  └──────────────────┘   │
│                                                              │
│  Общий конвейер: find_swing_points → detect_all →            │
│  add_forward_returns → ATR-фильтр → дедупликация             │
└──────────────────────┬───────────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────────┐
│                   Визуализация / UI                           │
│                                                              │
│  • Web-дашборд (Starlette + Plotly, порт 8080)               │
│  • matplotlib (4-панельный PDF/PNG)                          │
│  • Консольный отчёт с метриками успеха                       │
│  • Еquity-кривая 1лот × сигнал, exit через 6h               │
└──────────────────────────────────────────────────────────────┘
```

---

## Торговая стратегия: охота за ликвидностью толпы

### Базовая концепция

Рынок фьючерсов MOEX публикует Open Interest с разделением на **физлица (FIZ)** и **юрлица (YUR)**. Это уникальное преимущество — на других рынках такого разделения нет.

**Эмпирическое наблюдение**:
- **Физлица (толпа)**: действуют эмоционально, покупают на хаях, продают на лоях. Net-позиция FIZ — контр-индикатор.
- **Юрлица (smart money)**: действуют против толпы. YUR net позиция — подтверждающий индикатор.

### Сигнальная система (6 типов)

#### 1. OI_EXTREME — Экстремум позиции толпы
**Когда**: |z-score FIZ_net| > 2.0 (576-барное окно = ~2 дня) + разворот цены в ближайшие 12 баров (1 час).
**Логика**: Позиция физлиц аномально велика. Когда все лонгуют — некому покупать дальше. Цена разворачивается против толпы.
- `fiz_zscore > +2` → толпа максимально в лонге → signal **BEAR** (продавать)
- `fiz_zscore < -2` → толпа максимально в шорте → signal **BULL** (покупать)

#### 2. FLOW_EXTREME — Экстремальный поток толпы
**Когда**: |fiz_flow_zscore| > 2.0 (288-барное окно = 24ч потока) + разворот цены.
**Логика**: За 5 минут в позицию физлиц влился аномальный объём контрактов. Это паническая покупка/продажа — обычно в локальном экстремуме, после которого следует разворот.
- `fiz_flow_zscore > +2` → толпа резко накупила → signal **BEAR**
- `fiz_flow_zscore < -2` → толпа резко распродала → signal **BULL**

#### 3. FLOW_DIVERGENCE — Дивергенция потоков FIZ/YUR
**Когда**: Физлица активно покупают (fiz_flow_zscore > +1.5), юрлица активно продают (yur_flow_zscore < -1.5) — или наоборот.
**Логика**: Прямое визуальное наблюдение конфликта толпы и умных денег. Самый сильный сигнал из OI-based.
- FIZ покупают / YUR продают → signal **BEAR** (умные деньги раздают толпе)
- FIZ продают / YUR покупают → signal **BULL** (умные деньги набирают позицию)

#### 4. OI_TRAP — OI-ловушка
**Когда**: Цена и FIZ_net движутся в одном направлении, затем разворот в течение 12 баров.
**Логика**: Толпа «подтверждает» движение ценой — это ловушка. Умные деньги используют ликвидность толпы для выхода.

#### 5. OI_DIVERGENCE — OI-дивергенция
**Когда**: Цена растёт, а FIZ сокращает лонг (или цена падает, а FIZ набирает лонг).
**Логика**: Классическая дивергенция — толпа выходит из позиции, хотя цена ещё идёт в их сторону.

#### 6. Ценовые паттерны (без OI)
- **FALSE_BREAK**: Ложный пробой свингового уровня — цена пробивает swing high/low, затем резко разворачивается.
- **STOP_HUNT**: Длинный фитиль через уровень + подтверждение разворота. Охота за стоп-приказами, выставленными за уровнем.
- **VOL_CLIMAX**: Объёмный климакс > 2× среднего + откат на 3+ барах.

### Верификация сигналов

Каждый OI-based паттерн (EXTREME, TRAP, DIVERGENCE, FLOW) проходит forward-return верификацию:

| Метрика | Описание |
|---------|----------|
| **Entry** | close бара-сигнала |
| **Exit** | close через N часов (1h, 2h, 3h, 4h, 5h, 6h) |
| **Success** | -0.3% для BEAR, +0.3% для BULL на любом горизонте |
| **ATR-filter** | Ценовые паттерны с отсечкой по ATR (30-50%) |

### Equity-модель (дашборд)

- Стартовый капитал: 10,000 RUB
- 1 контракт на сигнал (Si: 1 lot → 1,000 USD notional)
- Вход: close паттерна (BULL → long, BEAR → short)
- Выход: close + 6h (72 свечи)
- Результат: equity-кривая, winrate, total PnL

---

## Источники данных

### 5-минутные бары (Alor OpenAPI V2)

**Таблица**: `moex_prices_5m` (59 тикеров)

```
GET /md/v2/history?exchange=MOEX&symbol={contract}&tf=300&from={ts}&to={ts}
```

Особенности:
- **Контрактная модель**: квартальные истечения (март/июнь/сентябрь/декабрь). Для каждого тикера загружаются все контракты от текущего до экспирации.
- **Дедупликация**: для каждого временного слота выбирается контракт с максимальным объёмом.
- **Direct-символы**: CNYRUBF, EURRUBF, GLDRUBF, USDRUBF, SBERF, GAZPF, IMOEXF — не имеют квартальных контрактов, загружаются напрямую.
- **5 OI-only** (CR, MN, MY, RB, RL): только в openinterest_moex, бары из Alor отсутствуют.
- **8 low-liquidity** (CH, VI, AU, FF, W4, HS, NR, DX): исключены из 5m загрузки из-за шума.
- **18 high-liquidity**: основные для сканирования — Si, BR, NG, GD, SV, SR, GZ, VB, CC, BM, NA, MC, SS, GL, CNYRUBF, USDRUBF, GLDRUBF, IMOEXF.

**JWT-токен** (из `ALOR_JWT`): `255375ae-88fa-4f33-bedd-6d9f6a432370`

### Дневные бары (MOEX ISS)

**Таблица**: `moex_prices` (D1)

```
GET https://iss.moex.com/iss/history/engines/futures/markets/forts/securities/{secid}.json
```

Для каждого тикера собираются все контракты с пагинацией. На каждую дату выбирается контракт с максимальным объёмом.

### Open Interest (MOEX ISS futoi)

**Таблица**: `openinterest_moex` (64 тикера)

```
GET https://iss.moex.com/iss/analyticalproducts/futoi/securities/{ticker}.csv
    ?iss.only=futoi&from={date}&till={date}&latest=0
```

Поля на запись: `buy_orders, sell_orders, clgroup (0=FIZ, 1=YUR)`.

Особенности:
- Аутентификация через passport.moex.com (опционально, для полного доступа). Без неё MOEX скрывает последние 14 дней.
- Пропуск выходных (MOEX закрыт).
- Авто-обновление через cron: ежедневный скрипт `loader.py`.

### Snapshot real-time (MOEX ISS)

**Таблица**: `moex_prices` (запись котировок)

```
GET https://iss.moex.com/iss/engines/futures/markets/forts/securities.json?iss.only=marketdata
```

Периодический сбор текущих цен (open, high, low, last, volume, open_interest).

---

## Скрипты

| Скрипт | Назначение | Ключевые опции |
|--------|-----------|----------------|
| `price_history_5m.py` | Загрузка 5m баров из Alor | `[Si] [BR] ...` |
| `price_history.py` | Загрузка D1 из ISS | — |
| `loader.py` | Загрузка OI из ISS futoi | `[days]` |
| `price_loader.py` | Snapshot real-time цен | — |
| `manipulation_search.py` | **Детекция паттернов** (CLI) | `--symbol Si --days 60 --zscore 2` |
| `batch_manipulation_scan.py` | **Массовый прогон** по 18 тикерам | `--days 60 --zscore 2 --csv out.csv` |
| `manipulation_viz.py` | **График** 4-панельный (matplotlib) | `--symbol Si --days 60 --output chart.png` |
| `moex_dashboard.py` | **Web-дашборд** (Starlette) | `--port 8080` |
| `backfill_bar_gaps.py` | Заполнение flat-свечей (volume=0) | — |
| `backfill_old.py` | Дозагрузка исторических баров | — |
| `build_mapping.py` | Построение маппинга ticker→ASSETCODE | — |
| `config.py` | Конфигурация (БД, тикеры, таймауты) | |

---

## Параметры детекции (manipulation_search.py)

```python
SWING_WINDOW = 10         # свечей влево/вправо для свинга
BREAK_LOOKAHEAD = 8       # свечей для подтверждения пробоя
VOLUME_WINDOW = 50        # окно средней волатильности
VOLUME_THRESHOLD = 2.0    # порог объёмного климакса
WICK_BODY_RATIO = 2.0     # фитиль / тело для стоп-ханта
OI_WINDOW = 12            # окно OI SMA (12 × 5мин = 1 час)
OI_ROLLING_WINDOW = 576   # z-score окно (≈2 дня по 288 свечей)
ZSCORE_THRESHOLD = 2.0    # порог z-score
```

---

## Структура БД

### `moex_prices_5m`
| Колонка | Тип | Описание |
|---------|-----|----------|
| symbol | TEXT | Тикер (Si, BR, ...) |
| time | TIMESTAMP | Начало 5-минутной свечи |
| open, high, low, close | DOUBLE | OHLC |
| volume | INT | Контракты |
| contract | TEXT | ID контракта (SBRF-6.26, ...) |
| updated_at | TIMESTAMP | Последнее обновление |

**UNIQUE**: (symbol, time)

### `moex_prices`
| Колонка | Тип | Описание |
|---------|-----|----------|
| symbol | TEXT | Тикер |
| time | TIMESTAMP | D1 (23:50 MOEX close) |
| open, high, low, last, settle_price | DOUBLE | Цены |
| volume | INT | Контракты |
| open_interest | INT | Общий OI |

**UNIQUE**: (symbol, time)

### `openinterest_moex`
| Колонка | Тип | Описание |
|---------|-----|----------|
| symbol | TEXT | Тикер |
| time | TIMESTAMP | Время записи OI |
| buy_orders | INT | Контрактов куплено |
| sell_orders | INT | Контрактов продано |
| clgroup | INT | 0=FIZ (физлица), 1=YUR (юрлица) |

**UNIQUE**: (symbol, time, clgroup)

---

## Быстрый старт

```bash
# 1. Загрузить 5m бары для Si
python3 price_history_5m.py Si

# 2. Сканировать манипуляции
python3 manipulation_search.py --symbol Si --days 60

# 3. Массовый прогон по всем ликвидным тикерам
python3 batch_manipulation_scan.py --days 30 --csv scan_results.csv

# 4. Дашборд
python3 moex_dashboard.py --port 8080

# 5. График
python3 manipulation_viz.py --symbol Si --days 60 --output si_chart.png
```

---

## Пример сигнала (Si)

```
FLOW_EXTREME BEAR at 2026-05-15 14:35  |  fiz_flow_zscore=+2.34
  → Толпа аномально накупила Si за 5 мин (на 2.3σ выше среднего)
  → Следующие 3 свечи: цена -0.15%, -0.22%, -0.18%
  → Исход: SUCCESS (1h = -0.35%)
```

---

## Зависимости

```
pip install requests psycopg2-binary pandas numpy matplotlib starlette uvicorn
```

Подробнее см. `requirements.txt` при наличии.

---

## Roadmap

- [x] 5m бары из Alor (59 тикеров)
- [x] D1 из MOEX ISS (64 тикера)
- [x] OI с разделением FIZ/YUR
- [x] 6 типов детекторов манипуляций
- [x] Forward-return верификация
- [x] ATR-фильтр ценовых паттернов
- [x] Web-дашборд с equity-кривой
- [ ] Telegram-бот для алертов сигналов
- [ ] Запись сделок (entry/exit) в БД
- [ ] Paper trading на исторических данных
- [ ] Режим real-time с streaming Alor API
- [ ] Machine Learning для ранжирования сигналов
