# Portfolio Optimizer — Priority + Correlation + Sector Filter

## Проблема

Сейчас портфель FIFO: сигналы обрабатываются в порядке времени. FF (WR=77%) конкурирует за маржу с AL (WR=45%). RI и GL (коррелированные) могут открыться одновременно, удваивая риск без диверсификации.

## Решение

Добавить `simulate_adaptive_portfolio()` — замена `simulate_adaptive()` с портфельной логикой.

### 1. Per-ticker Priority
```python
TICKER_PRIORITY = {
    'FF': 1, 'AU': 2, 'AF': 3, 'GK': 4, 'GL': 5, 'CNYRUBF': 6,
    'CR': 7, 'DX': 8, 'ED': 9, 'GZ': 10, 'HS': 11, 'NA': 12,
    # ... все 47 тикеров, отсортированы по WR из oi_screening.txt
}
```

### 2. Correlation Groups
```python
CORRELATION_GROUPS = {
    'rates': ['ED', 'FF', 'CR'],        # ставки/кредит
    'gold': ['AU', 'GLDRUBF'],           # золото
    'silver': ['SV'],                    # серебро
    'aluminum': ['AF'],                  # алюминий
    'copper': ['GK'],                    # медь
    'nickel': ['NM'],                    # никель
    'oil': ['BR'],                       # нефть
    'gas': ['NG'],                       # газ
    'rts': ['RI'],                       # RTS index
    'imoex': ['IMOEXF'],                 # MOEX index
    'usd': ['Si', 'USDRUBF', 'EURRUBF'], # валюта
    'cny': ['CNYRUBF'],                  # юань
    'sber': ['SBERF'],                   # Сбер
    'agri': ['FF', 'W4', 'CC'],          # сельское хозяйство
    'metal': ['GL', 'AF', 'AU', 'GK', 'NM', 'SV', 'SN', 'ZN'],
}
```

### 3. Portfolio Logic
В `simulate_adaptive_portfolio()`:

Когда приходит сигнал:
1. Определить correlation_group тикера
2. Проверить: есть ли уже открытая позиция в этой группе?
   - Если да → пропустить (не дублировать корреляцию)
3. Определить priority тикера (1 = лучший)
4. Если concurrent < mc и margin < tm → открыть
5. Если concurrent = mc → закрыть позицию с самым низким priority и открыть новую

### 4. Sector cap
Не более 1 позиции на correlation_group.
Для rates и agri групп — не более 2 (они менее коррелированы внутри).

### 5. Капитал по приоритету
Распределение капитала: не равномерное, а по весам:
```
priority 1-5:   вес 3.0 (получают больше маржи)
priority 6-15:  вес 1.5
priority 16+:   вес 1.0
```

При расчёте contracts: `adaptive_margin * weight / max_weight`

## Реализация
Создать `trading_bot/portfolio.py` с:
- `TICKER_PRIORITY` — рейтинг всех тикеров
- `CORRELATION_GROUPS` — группы корреляции
- `simulate_adaptive_portfolio()` — замена simulate_adaptive

Протестировать на тех же 7189 сигналах OI Divergence.
Сравнить с текущим simulate_adaptive: доходность, DD, Calmar.

## Ожидаемый эффект
- Увеличение Calmar за счёт лучшего распределения маржи
- Снижение DD за счёт исключения дублирующихся рисков
- Рост доходности за счёт приоритета лучшим тикерам
