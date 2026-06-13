# Checkpoint 051: TRIZ 300% — Phase 4 metagrid (не завершён), итог дня

## Что сделано за день

### V2 — CBR/ATR/dv_thr фильтры
- 23 тикера × 5 паттернов × per-ticker параметры
- **Лучший портфель: RI+GL+USDRUBF+NM → +168.6% за 2 года (~72% годовых), DD 3.6%, Calmar 47.4**

### V3 — Stacked confirmation (5m fiz_z)
- fiz_z>2.0 на последних 3x 5m барах даёт WR до 88% на BR
- Но сделок в 5-10x меньше → доходность падает

### V4 — Chandelier exit + Partial exit + Score sizing
- Trailing stop ATR×3 от peak (chandelier)
- Partial exit 50% на 0.5×ATR
- Score sizing — капитал по Calmar
- **Best: +105.7% за 2 года (~42.5% годовых)**
- Chandelier marginal improvement (+0.4%), partial и score sizing ухудшают

### Debate (Консерватор vs Радикал)
- Round 1: независимые идеи
- Round 2: кросс-критика
- Консенсус: per-ticker optimization + chandelier → нужен longer hold

### Phase 4 metagrid
- OpenCode начал писать скрипт полного перебора (23 тикера × 7 hold × chandelier × stacked × partial)
- Завис на патчинге после 7 минут
- **Скрипт не завершён, результаты не получены**

## Итоговые результаты

| Версия | Портфель | Ret/2yr | DD | Calmar | Годовых |
|--------|----------|---------|-----|--------|---------|
| V2 (CBR+ATR) | RI+GL+USDRUBF+NM | +168.6% | 3.6% | 47.4 | **~72%** |
| V3 (stacked) | AF+BR+GL+IMOEXF | +16% | 0.5% | 29.3 | ~8% |
| V4 (chandelier) | RI+GL+USDRUBF+NM+AF+BR | +105.7% | 4.1% | 25.5 | **~42.5%** |

**Лучший результат дня: +168.6% за 2 года (~72% годовых), Calmar 47.4**

## Новые файлы

- `reports/triz_diamond_v2/` — scripts + results (73 diamonds)
- `reports/triz_diamond_v3/` — stacked confirmation
- `reports/triz_diamond_v4/` — chandelier + partial + score sizing
- `docs/plans/2026-06-14-triz-phase3-chandelier.md`
- `docs/plans/2026-06-14-triz-phase4-megagrid.md`
- `checkpoint/049-triz-diamond-v2.md`
- `checkpoint/050-triz-phase3-chandelier.md`

## Что не работает / ограничения

- ❌ 300% годовых не достигнуты (best 72%)
- ❌ Phase 4 metagrid не завершён (OpenCode завис)
- ❌ Chandelier exit не даёт прироста на hold<5
- ❌ Partial exit не работает при 1-2 контрактах
- ⚠️ Score sizing на малом капитале разрушает диверсификацию
- ⚠️ OpenCode free модель упирается в лимиты на больших скриптах
- ⚠️ V4 baseline (+105%) отличается от V2 (+168%) из-за разных реализаций

## Что дальше (на завтра)

1. **Per-ticker optimization** — не общие hold/sl, а для каждого тикера свой
2. **Longer hold (10-21)** — дать chandelier время сработать
3. **Multi-TF ensemble** — daily + relaxed 5m stacked (fiz_z>0.5)
4. **Консолидировать V2 как baseline** — нормализовать код V4 под V2
