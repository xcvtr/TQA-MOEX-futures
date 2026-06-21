См. [README.md](README.md)

Перед началом работы — загрузить skill `checkpoint` и проверить последний чекпойнт в `checkpoint/`.

**ВАЖНО:** последние результаты (checkpoint 086):
- disb-анализ: корреляции нулевые, WR 49-51% — шум
- OIS divergence: Calmar ≤ 2.0 после исправления look-ahead
- yur_net_z: Calmar ≤ 1.9 (честно)

**Актуальный Roadmap в README.md** — 4 направления: obstats/orderstats, кластерный анализ стакана, глубокий OI, межрыночные связи.

Основные точки входа:
- `checkpoint/086-disb-analysis.md` — disb-анализ (финальный)
- `checkpoint/085-ois-divergence-lookahead-audit.md` — верификация OI divergence
- `checkpoint/084-yurnet-grid-search.md` — yur_net_z + OI spread grid search
- `scripts/final_ls.py` — OI divergence (исправленная версия с shift(1))
- `scripts/yurnet_strategy.py` — yur_net_z стратегия (multi-CPU, честная)
- `scripts/analyze_disb.py` — disb-анализ (исправленная версия)
