---
title: "Paper Trader: impulse_return отдельный инстанс, дашборд с двумя стратегиями"
checkpoint: 158
date: 2026-07-08
tags: [checkpoint, tqa-moex-futures, paper-trader, dashboard, fix]
---

# 158 — Impulse Return: paper trader + dashboard

## Что изменилось

- `strategies/common/paper_trader.py`: impulse_return добавлен в STRATEGY_MAP
- `paper_trader.py`: --strategy filter для запуска отдельной стратегии
- `paper_trader.py`: раздельные PG таблицы `paper_state`/`paper_state_impulse_return`
- `scripts/dashboard.py`: две колонки — Stop Hunt + Impulse Return
- Cron job `impulse-return-paper-trader` — `*/5 * * * *` через `pt_impulse_return.sh`

## Баги

- ❌ **2026-07-08**: portfolio filter — использовал `strats.items()` на списке вместо словаря. Фикс: `[s for s in strats if s.get('strategy') == strategy_filter]`

## Файлы

- `strategies/common/paper_trader.py`
- `scripts/dashboard.py`
- `cron/pt_impulse_return.sh` (symlink)
- `~/.hermes/scripts/pt_impulse_return.sh`
