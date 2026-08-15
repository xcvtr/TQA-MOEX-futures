# 228 — MOEX-div верифицирован (фьючерс-only базис = CAGR 24.5%), отказоустойчивость, TQA-moex-div-framework

Дата: 2026-08-15
Статус: ✅ Исследования MOEX-div завершены; прод: OI + dayofweek; watchdog'и работают
Предыдущий: checkpoint/227 (dayofweek в live, MTM DD на M1, Docker-воркер)

## 1. 🚀 Ключевая находка: базис-арбитраж ТОЛЬКО фьючерсами

**Идея пользователя «зачем акции, есть фьючерсы» → работает!**

| Вариант | ROI (3.2г) | CAGR | MTM DD | Комиссия |
|:--------|:---:|:---:|:---:|:---:|
| Акция+фьючерс (их) | +25.7% | 6% | 0.2% | 51.8₽/сделку |
| **Фьючерс-only (наш)** | **+101.5%** | **24.5%** | **1.8%** | **1.8₽/сделку** |

**Почему**: капитал = только ГО (0.38×spot) вместо spot+ГО (1.38×spot) → позиция ×3.6.
Комиссия фьючерса 1.8₽ вместо акции 50₽. Тот же сигнал (базис > нормы+2% → шорт, схлопывание → откуп).

**Конфиг**: дешёвые акции (spot<1000), SL 2% от dev, таймаут 30д, инвест 0.2, исключён 2022 (СВО).
По годам 2023-2026: +18K/+55K/+98K/+32K, WR 93-100%.

⚠️ Это НЕ «синтетическая облигация» (нет дивидендов) — mean-reversion на базuce фьючерса.
Скрипт: TQA-moex-div-framework/scripts/bt_basis_framework.py, sweep_basis.py

## 2. ❌ Дивидендный кэпчур — НЕ edge (честно)

| Год | avg годовая | WR |
|:---:|:---:|:--:|
| 2022 | +95.4% | 91% |
| 2023 | +98.2% | 91% |
| **2024** | **−126.6%** | 77% |
| **2025** | **−43.4%** | 91% |

Средняя годовая −1.7%: редкие крупные убытки съедают всё. WR 87% обманчив.

## 3. 🐳 TQA-moex-div-framework (новый проект)

`~/projects/TQA-moex-div-framework` — верификация MOEX-div через контракт фреймворка:
- strategies/synthetic_bond/{detect,tick}.py + engine/loader (их BondLeg)
- scripts/bt_basis_framework.py (CH 63: mt5_equity M1→D1 + mt5_futures D1)
- scripts/sweep_basis.py (свип порога/таймаута/фильтра нормы)
- Вердикты в AGENTS.md

**Их 64-367% — артефакты**: cash×(1+0.16/365)^(365/N) на каждую сделку (ОФЗ-рост от числа сделок), без комиссии акции, неполные данные (mt5_continuous SBRF только 2024+), пирамида 0.33.

## 4. 🛡 Отказоустойчивость

- **PG**: 60→63 async, lag=0, repmgr failover ✅
- **Watchdog моста** (mt5_bridge_watchdog.sh): авто-рестарт wine-процесса + алерт
- **Freshness** (data_freshness_watchdog.sh): bars_1m/futoi_iss возраст + алерт
- **MT5-дубль на 63 НЕВОЗМОЖЕН**: Xeon E5-2670 без AVX2 (терминал FINAM illegal instruction).
  10.0.0.60 = 10.0.0.64 (один хост). MT5 остаётся SPOF, но данные/PG отказоустойчивы.

## 5. 🔧 Баг фреймворка исправлен

`load_m1_from_ch`: для source=mt5_continuous query потерялся при рефакторинге веток D1/H1 → OI падал. Фикс: ec32967.

## Файлы

- tqa-framework: detect.py (query mt5_continuous фикс), backtester (roll_gap, ch_latest, state)
- TQA-moex-div-framework: NEW (верификация MOEX-div)
- TQA-MOEX-futures: watchdog'и (f60a8ee), AGENTS.md
- ~/.hermes/scripts/: mt5_bridge_watchdog.sh, data_freshness_watchdog.sh

## Состояние для продолжения

Прод: OI + dayofweek (live). Исследования MOEX-div завершены (фьючерс-only базис = кандидат,
CAGR 24.5% DD 1.8% — решение: интегрировать в фреймворк или нет). Docker-воркер актуален.
Отказоустойчивость: MT5 SPOF принят (нет AVX2 на 63), остальное защищено.
