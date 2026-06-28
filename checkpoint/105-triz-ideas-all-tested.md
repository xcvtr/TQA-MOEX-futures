# Checkpoint 105 — TRIZ Ideas Tested: 11 Signals × Trailing TP

**Дата:** 2026-06-28
**Проект:** TQA-MOEX-futures
**Предыдущий:** #104 — CVD verdict: no edge

---

## 1. Все 11 идей протестированы

Данные: `moex.tradestats_fo`, ClickHouse 10.0.0.60. Период: Oct'2024 — Jun'2026.

| # | Идея | Вердикт | WR бейзлайн | WR + Trailing TP |
|---|------|---------|:-----------:|:----------------:|
| 1 | **Stop Hunt** (ложные пробои) | ✅ | 63-66% | **82-91%** |
| 2 | **Intraday OI Spike** (new_shorts) | ⚠️ | 36-38%* | 58-59% |
| 3 | **Disb_z + OI_z Combo** | ✅ | 58-59% | **83-87%** |
| 4 | **Lunch Reversal** (сессия 13-14) | ✅ | 57-63% | **79-90%** |
| 5 | **Close Sweep** (18:00-18:45) | ✅ редко | 65% | — |
| 6 | **Churn** (OI flat + vol surge) | ✅ | 52-53% | 74-85% |
| 7 | **Volume Profile S/R** (short) | ⚠️ | 52-65% | 82-84% |
| 8 | **Cross-ticker Si/CR** | ⚠️ | 59% | — |
| 9 | **FIZ/YUR Divergence** | ❌ | ~50% | — |
| 10 | **HHI + Price Divergence** | ❌ редко | — | — |
| **11** | **CVD (dcvd_z>0.6)** | ⚠️ | **50%** | **75-84%** |

## 2. Trailing TP — главное открытие

Параметры: activation=0.5%, trail=0.3%, max_bars=96 (8ч)

**Trailing TP превращает любой сигнал в прибыльный.** Даже CVD с корреляцией −0.0029 даёт 75-84% WR.

Сигнал не важен — важен выход. Trailing TP обеспечивает edge, entry — вторичен.

**Топ комбинации по NetP80:**
1. Stop Hunt + Trailing TP на GZ: +10.95
2. Lunch Reversal + Trailing TP на GZ: +10.59
3. Disb+OI + Trailing TP на GZ: +9.47
4. Churn + Trailing TP на GZ: +9.34
5. CVD + Trailing TP на GZ: +9.04

**Ключевой вывод:** Лучший entry = Stop Hunt Detection (ложные пробои). Лучший exit = Trailing TP (0.5/0.3).

## 3. Файлы

| Файл | Описание |
|------|----------|
| `/home/user/stop_hunt_test.py` | Stop Hunt тест |
| `/home/user/oi_spike_test.py` | OI Spike тест |
| `/home/user/test_churn.py` | Churn тест |
| `/home/user/trailing_tp_test.py` | Trailing TP на Stop Hunt |
| `/home/user/test_all_7_signals.py` | Все 7 сигналов × Trailing TP |
| `/home/user/all_7_signals_results.csv` | Полные результаты |
| `/home/user/hhi_price_divergence_test_v4.py` | HHI тест |
| `/home/user/fiz_yur_final.py` | FIZ/YUR тест |
| `/home/user/session_patterns_summary.txt` | Session patterns |
| `/home/user/volume_profile_sr_summary.txt` | Volume Profile |
| `/home/user/corr_divergence.py` | Cross-ticker |

## 4. Что дальше

1. Оформить стратегию: **Stop Hunt Detection + Trailing TP** в `strategies/stop_hunt/`
2. Сохранить Trailing TP параметры в PG
3. Настроить engine, paper_trader, cron
