---
title: "Финальные чемпионы по Calmar + MTM DD"
checkpoint: 177
date: 2026-07-22
tags: [checkpoint, tqa-moex-futures, final, champions, calmar]
---

# Checkpoint 177: Чемпионы по Calmar с MTM DD

## Чемпионы по Calmar (MTM DD < 20%)

### ⚡ IR Si 1m — Абсолютный чемпион 🏆
| Risk | ROI | PF | MTM DD | Cash DD | Calmar |
|:----:|:---:|:--:|:------:|:-------:|:------:|
| **2%** | **+614%** | **3.44** | **9.2%** | **0%** | **66.4** |

### ⚡ IR Si 3m
| Risk | ROI | PF | MTM DD | Calmar |
|:----:|:---:|:--:|:------:|:------:|
| 1% | +415% | 3.78 | 6.8% | 61.0 |
| **2%** | **+732%** | **3.11** | **13.3%** | **55.0** |

### 🐉 Dragon MM 10m
| Risk | ROI | PF | MTM DD | Calmar |
|:----:|:---:|:--:|:------:|:------:|
| 2% | +342% | 2.52 | 16.2% | 21.1 |
| **2.5%** | **+533%** | **2.33** | **19.9%** | **26.8** ✅ |

### 🐉 Dragon GZ 5m
| Risk | ROI | PF | MTM DD | Calmar |
|:----:|:---:|:--:|:------:|:------:|
| **2%** | **+177%** | **1.84** | **12.4%** | **14.3** |

### 🛑 SH RN 1m
| Risk | ROI | PF | MTM DD | Calmar |
|:----:|:---:|:--:|:------:|:------:|
| **3%** | **+336%** | **2.10** | **17.0%** | **19.8** ✅ |

## Полная таблица sweep (90 комбинаций)
Файл: `checkpoint/sweep_full_results.txt`
- 3 стратегии × все таймфреймы × все тикеры
- close_entry, TC=8, TRIZ, risk=2%

## Параметры тестов
- Комиссия: TC=8 (round-trip)
- Entry: close сигнального бара (лимитка)
- Trend filter: SMA 50, без look-ahead
- Detect ≠ Tick: resample → detect, M1 → tick
- Risk: % от капитала на сделку
- GO лимит: `ct = min(ct_risk, ct_go)`
- MOEX часы: 15:00-23:45 IRKT, только будни
