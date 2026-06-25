См. [README.md](README.md)

Перед началом работы — загрузить skill `checkpoint` и проверить последний чекпойнт в `checkpoint/`.

**⚠️ 2026-06-14: force push — очищена история от больших файлов (signals JSON 500MB+, reports/oi_divergence_scan 1.9GB).**
Если на этой машине (Hermes-dev .63) `git pull` не работает — выполнить:
```
git fetch --force && git reset --hard origin/main
```

**ВАЖНО:** последние результаты (checkpoint 098):
- **CVD divergence paper trader** — live M5 через AlgoPack API
  - Скрипт: `scripts/cvd_divergence_paper_trader.py`
  - Данные: AlgoPack fo tradestats (не CH)
  - Хранение: CH `strategy_paper_trades`, `strategy_portfolio_state`
  - Cron: `cvd_divergence_scanner.sh` каждые 5 мин будни
- **CVD divergence v4 (лимитки, комиссия 0)** — 4 фьючерса (NG, BR, Si, MXI), портфельный WFO
  - M5 lk=20 hold=1 q=0.6 — 66,961 сделок, **WR 66.2%**, Net PnL +73.2M RUB
  - Все 3 ТФ (M5/M15/H1) положительны
  - Long/Short симметрия ✅, все 4 символа в плюс
  - **Ключевые исправления:**
    - Entry timing look-ahead исправлен (сигнал N → вход N+1 по close)
    - DD trade-level (а не daily aggregate) — реальная MDD 301% (шум старта)
    - Margin locking per-symbol (не max(GO))
  - **70/70 месяцев положительных**, равномерное распределение
  - Следующий шаг: реалистичный тестер с тейкерскими комиссиями
- **BR 3-red exhaustion + TRIZ smart exit** — стратегия подтверждена на OOS
  - 15m, лимитка min4, комбинированный выход (vol_decay + smacross + proskok)
  - Лучший: zv=3.0 tg=2.0 sl=1.5 → OOS WR 56.4%, PnL +4,861 за 8 мес
  - **Все 48 конфигов положительны на OOS** — TRIZ-выход решил проблему
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
