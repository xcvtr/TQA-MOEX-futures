# Cross-Market Correlation Analysis — 2026-06-10

## TRIZ Direction 1: ПРОТИВОРЕЧИЕ → ИКР → РЕШЕНИЕ → РЕЗУЛЬТАТ

### ПРОТИВОРЕЧИЕ (Contradiction)
Требуется одновременно высокая точность входа и низкая задержка сигнала. Корреляция между инструментами нестабильна: частые ложные схождения/расхождения маскируют истинные точки входа.

### ИКР (Ideal Final Result)
Система сама определяет моменты статистически значимого расхождения корреляции и подтверждает mean-reversion паттерн с win rate > 55%.

### РЕШЕНИЕ (Solution)
Rolling корреляция 60 баров (5 часов) по 8 ключевым тикерам MOEX. Выявление расхождений > 2σ от среднего. Проверка возврата отстающего инструмента за 5/10/20 баров.

### РЕЗУЛЬТАТ (Result)

**✅ EDGE FOUND** — 20 edge(s) detected:
  - Si-FF (L20: 80.0%, 4/5)
  - Si-AU (L5: 80.0%, 4/5)
  - Si-CNYRUBF (L5: 56.5%, 236/418)
  - BR-GL (L10: 55.3%, 84/152)
  - BR-ED (L10: 56.7%, 89/157)
  - BR-AU (L5: 71.4%, 5/7)
  - BR-AU (L20: 57.1%, 4/7)
  - RI-ED (L10: 55.0%, 66/120)
  - RI-AU (L5: 66.7%, 4/6)
  - RI-AU (L10: 66.7%, 4/6)
  - RI-AU (L20: 66.7%, 4/6)
  - RI-CNYRUBF (L10: 55.2%, 224/406)
  - GL-ED (L10: 58.0%, 87/150)
  - GL-ED (L20: 58.7%, 88/150)
  - GL-FF (L10: 100.0%, 1/1)
  - GL-FF (L20: 100.0%, 1/1)
  - GL-CNYRUBF (L10: 55.5%, 208/375)
  - ED-AU (L20: 60.0%, 3/5)
  - ED-CNYRUBF (L10: 55.8%, 29/52)
  - AU-CNYRUBF (L5: 60.0%, 3/5)

## Data Summary

- **Data source**: `moex_prices_5m` on 10.0.0.64
- **Tickers**: Si, BR, RI, GL, ED, FF, AU, CNYRUBF
- **Period**: 2023-01-03 06:00:00 to 2026-06-09 20:45:00
- **Total rows (all tickers)**: 882658
- **Correlation window**: 60 bars (5 hours)
- **Reversion windows**: 5 bars, 10 bars, 20 bars

## Pair-by-Pair Results (sorted by win rate)

| Pair | Lookahead | Events | Wins | Win Rate |
|------|-----------|--------|------|----------|
| GL-FF | 10 bar | 1 | 1 | 100.0% |
| GL-FF | 20 bar | 1 | 1 | 100.0% |
| Si-FF | 20 bar | 5 | 4 | 80.0% |
| Si-AU | 5 bar | 5 | 4 | 80.0% |
| BR-AU | 5 bar | 7 | 5 | 71.4% |
| RI-AU | 5 bar | 6 | 4 | 66.7% |
| RI-AU | 10 bar | 6 | 4 | 66.7% |
| RI-AU | 20 bar | 6 | 4 | 66.7% |
| ED-AU | 20 bar | 5 | 3 | 60.0% |
| AU-CNYRUBF | 5 bar | 5 | 3 | 60.0% |
| GL-ED | 20 bar | 150 | 88 | 58.7% |
| GL-ED | 10 bar | 150 | 87 | 58.0% |
| BR-AU | 20 bar | 7 | 4 | 57.1% |
| BR-ED | 10 bar | 157 | 89 | 56.7% |
| Si-CNYRUBF | 5 bar | 418 | 236 | 56.5% |
| ED-CNYRUBF | 10 bar | 52 | 29 | 55.8% |
| GL-CNYRUBF | 10 bar | 375 | 208 | 55.5% |
| BR-GL | 10 bar | 152 | 84 | 55.3% |
| RI-CNYRUBF | 10 bar | 406 | 224 | 55.2% |
| RI-ED | 10 bar | 120 | 66 | 55.0% |
| GL-CNYRUBF | 5 bar | 375 | 206 | 54.9% |
| BR-ED | 5 bar | 157 | 86 | 54.8% |
| Si-CNYRUBF | 10 bar | 417 | 227 | 54.4% |
| RI-CNYRUBF | 5 bar | 406 | 221 | 54.4% |
| BR-GL | 5 bar | 152 | 82 | 53.9% |
| BR-RI | 5 bar | 182 | 98 | 53.8% |
| ED-CNYRUBF | 20 bar | 52 | 28 | 53.8% |
| Si-BR | 5 bar | 108 | 58 | 53.7% |
| RI-CNYRUBF | 20 bar | 406 | 214 | 52.7% |
| Si-RI | 5 bar | 525 | 276 | 52.6% |
| Si-ED | 10 bar | 139 | 73 | 52.5% |
| Si-GL | 5 bar | 376 | 197 | 52.4% |
| BR-GL | 20 bar | 152 | 79 | 52.0% |
| Si-RI | 10 bar | 525 | 272 | 51.8% |
| BR-RI | 10 bar | 182 | 93 | 51.1% |
| Si-CNYRUBF | 20 bar | 417 | 213 | 51.1% |
| Si-GL | 10 bar | 376 | 192 | 51.1% |
| Si-GL | 20 bar | 376 | 192 | 51.1% |
| RI-GL | 20 bar | 241 | 123 | 51.0% |
| Si-RI | 20 bar | 525 | 267 | 50.9% |
| GL-CNYRUBF | 20 bar | 375 | 189 | 50.4% |
| BR-RI | 20 bar | 182 | 91 | 50.0% |
| BR-FF | 10 bar | 4 | 2 | 50.0% |
| BR-FF | 20 bar | 4 | 2 | 50.0% |
| ED-FF | 5 bar | 2 | 1 | 50.0% |
| BR-ED | 20 bar | 157 | 76 | 48.4% |
| RI-ED | 5 bar | 120 | 58 | 48.3% |
| RI-ED | 20 bar | 120 | 58 | 48.3% |
| RI-GL | 10 bar | 241 | 116 | 48.1% |
| GL-ED | 5 bar | 150 | 71 | 47.3% |
| Si-BR | 10 bar | 108 | 51 | 47.2% |
| RI-GL | 5 bar | 241 | 113 | 46.9% |
| Si-BR | 20 bar | 108 | 50 | 46.3% |
| ED-CNYRUBF | 5 bar | 52 | 24 | 46.2% |
| Si-ED | 5 bar | 139 | 63 | 45.3% |
| BR-CNYRUBF | 5 bar | 63 | 28 | 44.4% |
| RI-FF | 20 bar | 9 | 4 | 44.4% |
| Si-ED | 20 bar | 139 | 60 | 43.2% |
| BR-CNYRUBF | 20 bar | 63 | 27 | 42.9% |
| BR-CNYRUBF | 10 bar | 63 | 26 | 41.3% |
| Si-FF | 5 bar | 5 | 2 | 40.0% |
| ED-AU | 10 bar | 5 | 2 | 40.0% |
| AU-CNYRUBF | 10 bar | 5 | 2 | 40.0% |
| AU-CNYRUBF | 20 bar | 5 | 2 | 40.0% |
| BR-AU | 10 bar | 7 | 2 | 28.6% |
| BR-FF | 5 bar | 4 | 1 | 25.0% |
| GL-AU | 10 bar | 8 | 2 | 25.0% |
| RI-FF | 5 bar | 9 | 2 | 22.2% |
| RI-FF | 10 bar | 9 | 2 | 22.2% |
| Si-FF | 10 bar | 5 | 1 | 20.0% |
| Si-AU | 10 bar | 5 | 1 | 20.0% |
| Si-AU | 20 bar | 5 | 1 | 20.0% |
| ED-AU | 5 bar | 5 | 1 | 20.0% |
| GL-AU | 5 bar | 8 | 1 | 12.5% |
| GL-AU | 20 bar | 8 | 1 | 12.5% |
| GL-FF | 5 bar | 1 | 0 | 0.0% |
| ED-FF | 10 bar | 2 | 0 | 0.0% |
| ED-FF | 20 bar | 2 | 0 | 0.0% |
| FF-CNYRUBF | 5 bar | 2 | 0 | 0.0% |
| FF-CNYRUBF | 10 bar | 2 | 0 | 0.0% |
| FF-CNYRUBF | 20 bar | 2 | 0 | 0.0% |

---
*Generated at 2026-06-10 23:55:43*