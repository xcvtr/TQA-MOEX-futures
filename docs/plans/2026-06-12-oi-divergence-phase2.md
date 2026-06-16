# OI Divergence — Phase 2: Temporal Asymmetry with Regime Detection

## Контекст
Phase 1 показала что OI divergence в чистом виде — шум (WR ~49% для 792 комбинаций).
Но Phase 1 НЕ учитывала структурные разрывы рынка MOEX:
- До 2022-03: ММ/нерезиденты активны (YUR% ~50-65%)
- 2022-04 до 2024-12: ММ ушли/восстанавливаются (YUR% ~45-50%)
- После 2025-01: возврат ММ (YUR% ~55-60%)

## Задача
Переписать oi_divergence_scan_v2.py с учётом:

### 1. Per-ticker recovery date
Использовать table2_structural_breaks.csv:
- Если recovery_month != "NOT YET" и != "N/A" → брать данные только после recovery_month
- Если recovery_month = "NOT YET" (GD, PT) → исключить тикер
- Если break = "NONE DETECTED" (Si, CR, CNYRUBF, USDRUBF, GLDRUBF) → использовать все данные

### 2. Два режима тестирования
**Режим A: Pre-SVO + Post-recovery** (полноценный рынок)
Тикеры: все кроме GD, PT
Данные: только до 2022-02 + после recovery_month per ticker

**Режим B: Только post-recovery** (2025+)
Тикеры: у которых recovery есть
Данные: только после recovery_date per ticker

### 3. Temporal filter
Проверить гипотезу: divergence работает только в определённое время сессии:
- Открытие (10:00-12:00 МСК)
- Основная сессия (12:00-17:00)
- Закрытие (17:00-18:45)

Сравнить WR по временным окнам.

### 4. Модифицированная логика сигнала
Вместо простого divergence = fiz_net_z - yur_net_z, добавить:
- **Weighted divergence**: yur_net_z * 2 - fiz_net_z (ММ важнее толпы)
- **Directional agreement**: сигнал ТОЛЬКО когда fiz и yur показывают ПРОТИВОПОЛОЖНЫЕ направления (fiz_net_z > 0 и yur_net_z < 0 → SHORT, и наоборот)

### Параметры
Те же: W=[10,20,40], T=[1.0,1.5,2.0,2.5], hold=[5,10,20], SL=5%
Комиссия 2 руб, капитал 100000

### Вывод
- reports/oi_divergence_phase2/SUMMARY_A.csv
- reports/oi_divergence_phase2/SUMMARY_B.csv  
- reports/oi_divergence_phase2/SUMMARY_A_temporal.csv
- reports/oi_divergence_phase2/report.md

ClickHouse: clickhouse_connect.get_client(host="127.0.0.1", port=8123, database="moex")
Venv: /home/user/venvs/tqa/main/bin/python3
