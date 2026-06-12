# 040 — Volume × OI TRIZ: полный цикл, 0/5 верификация

Дата: 2026-06-12

## Что сделано
Полный TRIZ-цикл по стратегии Volume × OI yur_accumulation:

1. **Скан 64 тикеров** → 6 стабильных по сигнальному WR (NR, CC, IB, GD, SR, PD)
2. **V1: Flat sizing** (1 контракт без реинвеста) → −189% (хвосты SR/NR по −100К+)
3. **V2: ATR-фильтр ≤1.0%** → PD +24K, CC/IB околонулевые
4. **V3: Адаптивный exit по yur_z<0.5** → PD +41K (лучший)
5. **V4: 1080 комбинаций на 8 тикерах** — полный grid search
6. **Walk-forward 4-fold верификация → 0/5 прошли**

Скрипты:
- `scripts/multi_volume_oi_scan.py` — первичный скан 64 тикеров
- `scripts/volume_oi_v1_flat.py` — flat sizing
- `scripts/volume_oi_v2_atr.py` — ATR-фильтр
- `scripts/volume_oi_v3_exit.py` — адаптивный exit
- `scripts/volume_oi_v4_final.py` — полный перебор
- `scripts/volume_oi_v4_verify.py` — walk-forward верификация

## Результат
**Стратегия не работает.** Ни один тикер не прошёл walk-forward. Сигнальный WR 59-62% — артефакт; при bar-level MTM с stop-loss WR падает до 42-50%.

Корень: OI-данные (fiz/yur) на MOEX M5 не дают предсказательной силы.

## Куда двигаться
- Другая логика (не yur_accumulation)
- Другой таймфрейм (H1/D1)
- Другие рынки (forex, crypto)
- Не OI, а price action
