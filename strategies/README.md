# Стратегии MOEX (TQA-MOEX)

| # | Стратегия | ТФ | WR | Сигналов | Статус |
|:-:|:----------|:--:|:--:|:--------:|:------|
| 1 | 🐋 [Whale Detector](whale-detector/README.md) — OI fiz/yur | D1 | **77.8%** | 18/3.3г | ✅ Работает |
| 2 | 📈 [Volume Climax](volume-climax/README.md) — H4 экстремумы | H4 | **78%** | 50-200/мес | ✅ Работает |
| 3 | 🎯 [Crowd Bias](crowd-bias/README.md) — против толпы | WIP | 46.4% | — | 🔬 Анализ |

## Данные

- Цены: `moex_prices_5m` (Alor OpenAPI, 59 тикеров)
- OI: `openinterest_moex` (MOEX ISS futoi, 64 тикера, лаг ~14д)
- Securities: `moex_securities` (ГО/плечо, ежедневное обновление)
- Дашборд: http://10.0.0.60:5057/
- Скрипты: корень проекта (whale_detector.py, moex_equity_dashboard.py и др.)
