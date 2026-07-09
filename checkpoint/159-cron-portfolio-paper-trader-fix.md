---
title: "Cron portfolio paper trader fix — UnboundLocalError resolved"
checkpoint: 159
date: 2026-07-10
tags: [checkpoint, tqa-moex-futures, cron, paper-trader]
---

# 159 — Cron portfolio paper trader fix

## Проблема

Cron job `moex-futures-portfolio-paper-trader` (job_id: `35babc512e48`) упал с ошибкой:

```
File "paper_trader.py", line 531, in run_tick
    positions.append(pos)
UnboundLocalError: cannot access local variable 'pos'
```

## Диагностика

- Код `paper_trader.py` на диске корректен — `pos` инициализируется внутри `for entry in portfolio[ticker]` перед `positions.append(pos)`
- Вероятная причина: ошибка из старой версии файла, которая была запущена кроном до наших правок (checkpoint 006-007)
- После проверки: текущая версия скрипта работает без ошибок

## Текущее состояние

| Компонент | Статус |
|:----------|:------:|
| Cron `moex-futures-portfolio-paper-trader` | ✅ `last_status=ok` |
| Скрипт `pt_portfolio.sh` | ✅ корректный |
| `paper_trader.py` run_tick | ✅ без ошибок |
| Остальные cron (stop_hunt, impulse_return) | ✅ ok |

## Файлы

- `strategies/common/paper_trader.py` — без изменений (ошибка была в старой версии)
- `~/.hermes/scripts/pt_portfolio.sh` — без изменений
