# Чекпойнт 189 — Финальный портфель v4 (2026-07-26)

**Состав:** IR Si 10% + Dragon GD 10m 20% + Dragon MM 5m 15% + SH RN 1m 20% + Dragon NG 3m 20%
**Данные:** mt5_continuous, 365 дней (Jul 2025 — Jul 2026)
**GO:** КСУР ПГО (обновляемы через update_go_ksur_pgo.py)
**Комиссии:** Per-ticker из PG (Si=4, GD=44, MM=2, RN=7, NG=4)
**Вход:** Market (1 tick slippage)
**Volume cap:** 20% для всех (Si 50%)
**Max contracts:** 20
**TRIZ:** trend (SMA50) для всех, min_vol_ratio=0.8 для IR/GD

## Результат

```
Capital: 200,000 → 15,897,002 (+7,848.5%)
Trades: 4,331
Cash MDD:  7.07% ✅
MTM MDD:  7.90% ✅
```

## По стратегиям

| Стратегия | Тикер | Detect | Риск | Комиссия | Trades | WR | PF | PnL |
|:----------|:------|:------:|:----:|:--------:|:-----:|:-:|:--:|----:|
| ⚡ IR | Si | 1m | 10% | 4₽ | 488 | 50.2% | 2.57 | +890K |
| 🐉 Dragon | GD | 10m | 20% | 44₽ | 144 | 56.9% | 3.59 | +821K |
| 🐉 Dragon | MM | 5m | 15% | 2₽ | 203 | 52.2% | 1.89 | +25K |
| 🛑 **SH RN** 🏆 | RN | 1m | 20% | 7₽ | 2,653 | 46.8% | **17.91** | +11.7M |
| 🐉 Dragon | NG | 3m | 20% | 4₽ | 843 | 49.7% | 2.47 | +576K |

## Аудит (189)

- Комиссии per-ticker из PG fee_entry (round-trip) ✅
- PnL без *lot ✅
- SL на M1 lo/hi ✅
- Trend filter на hist[-50:] (без look-ahead) ✅
- MTM MDD на каждом M1 баре с unrealised PnL ✅
- Multi-contamination: <0.005% ✅
- Limit entry — не работает (Dragon PF падает до 0.6), откачено
- fiz/yur — не улучшает, отключено
