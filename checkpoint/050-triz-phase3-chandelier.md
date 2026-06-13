# Checkpoint 050: TRIZ 300% Phase 3 — Chandelier Exit, Partial Exit, Score Sizing

## Что сделано

- **Debate Mode (Консерватор vs Радикал)** — 2 раунда с кросс-критикой
- **Синтез**: выбраны 3 идеи для реализации — chandelier exit, partial exit, score sizing
- **OpenCode реализация**: diamond_search_v4.py + portfolio_v4.py + audit.py
- **Аудит**: сравнение 4 конфигураций на 100K и 200K

## Результаты

| Конфиг | Ret (100K) | DD | Calmar | WR | Ann |
|--------|-----------|-----|--------|-----|-----|
| Baseline (fixed SL 1%) | +105.3% | 4.2% | 25.4 | 50% | +42.4% |
| Chandelier (ATR×3.0) | +105.7% | 4.1% | 25.5 | 51% | +42.5% |
| Chandelier + Partial | +86.9% | 4.4% | 19.8 | 52% | +36.0% |
| Chandelier + Partial + ScoreSizing | +30.8% | 2.7% | 11.6 | 59% | +14.6% |

**Вывод:** Chandelier exit даёт marginal improvement (+0.4% ret, -0.1% DD). Partial exit и score sizing не улучшают на этом портфеле. 300% не достигнуто — best result = **+105.7% за 2 года (~42.5% годовых)**.

## Причины

1. Chandelier exit требует больше времени в позиции (>5 дней hold) — trailing stop не успевает сработать
2. При 1-2 контрактах на сигнал partial exit не может разделить позицию
3. Score sizing (overweight GL по Calmar) разрушает диверсификацию
4. OpenCode baseline не совпадает с V2 (+105% vs +168%) — различия в коде

## Новые файлы

- `reports/triz_diamond_v4/diamond_search_v4.py` — chandelier exit + partial exit
- `reports/triz_diamond_v4/portfolio_v4.py` — score sizing + портфельная симуляция
- `reports/triz_diamond_v4/audit.py` — аудит всех конфигов
- `reports/triz_diamond_v4/audit_results.json` — результаты
- `docs/plans/2026-06-14-triz-phase3-chandelier.md` — план

## Что дальше

Для 300% нужны более радикальные изменения:
1. **Per-ticker optimization** — не общие hold/sl, а индивидуальные для каждого тикера
2. **Multi-TF ensemble** — daily паттерн + 5m stacked confirmation (relaxed fiz_z>1.0 вместо 2.0)
3. **Longer hold (10-20 баров)** — дать chandelier время сработать
