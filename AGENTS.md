См. [README.md](README.md)

Перед началом работы — загрузить skill `checkpoint-save` и проверить последний чекпойнт в `checkpoint/`.

**ВАЖНО:** последняя верификация (checkpoint 085) показала, что OIS divergence (083) имела look-ahead bias. Реальные результаты стратегий:
- yur_net_z — Calmar ≤ 1.9 (честно)
- OIS divergence — Calmar ≤ 2.0 (после исправления)
- Все стратегии проверены на look-ahead

Основные точки входа:
- `checkpoint/085-ois-divergence-lookahead-audit.md` — результаты верификации
- `checkpoint/084-yurnet-grid-search.md` — yur_net_z + OI spread grid search
- `scripts/final_ls.py` — OI divergence (исправленная версия с shift(1))
- `scripts/yurnet_strategy.py` — yur_net_z стратегия (multi-CPU, честная)
