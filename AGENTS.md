См. [README.md](README.md)

Перед началом работы — загрузить skill `checkpoint` и проверить последний чекпойнт в `checkpoint/`.

**ВАЖНО:** последние результаты (checkpoint 094):
- **BR 3-red exhaustion + vol climax** — первая рабочая directional стратегия на MOEX фьючерсах
  - Flat: WR 74.3%, +60K (+61%) за 2025-2026, DD 7%, Calmar 5.72
  - Reinvest (5 конт): +303K (+303%), CAGR +171%, DD 19%
  - Верифицировано на OOS 8 мес: WR 63.3%, PnL +30K
- **FUTOI** загружен: 1.58M строк, 78 тикеров — позиции FIZ/YUR по фьючерсам
- **HI2** загружен: 1.14M строк — HHI-индекс концентрации рынка
- **Alerts** загружены: 331K записей — события 99.9 перцентиля
- **Correlation scan:** CR corr=-0.88, GL corr=+0.82 (YUR_net vs price)
- **Данные лежат:** CH 10.0.0.60/63, БД moex (futoi, hi2_fo, alerts_fo)**

**Актуальный Roadmap в README.md** — 4 направления: obstats/orderstats, кластерный анализ стакана, глубокий OI, межрыночные связи.

Основные точки входа:
- `checkpoint/087-futoi-hi2-alerts-correlation.md` — FUTOI/HI2/Alerts анализ
- `checkpoint/086-disb-analysis.md` — disb-анализ (финальный)
- `checkpoint/085-ois-divergence-lookahead-audit.md` — верификация OI divergence
- `checkpoint/084-yurnet-grid-search.md` — yur_net_z + OI spread grid search
- `scripts/final_ls.py` — OI divergence (исправленная версия с shift(1))
- `scripts/yurnet_strategy.py` — yur_net_z стратегия (multi-CPU, честная)
- `scripts/analyze_disb.py` — disb-анализ (исправленная версия)
