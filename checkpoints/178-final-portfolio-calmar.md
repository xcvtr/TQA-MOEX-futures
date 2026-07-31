---
title: "Финальный портфель: IR Si + Dragon MM/GZ — правильные ТФ и FINAM ГО"
checkpoint: 178
date: 2026-07-23
tags: [checkpoint, tqa-moex-futures, final, portfolio]
---

# Checkpoint 178: Финальный портфель по Calmar

## 🔑 Ключевые изменения в фреймворке
- `RULES.md` — фреймворк, не монолит, per-ticker TF
- `backtest.py` — переписан на PortfolioEngine + BrokerSim
- `engine.py` — close_entry, детект каждый M1 бар, hist_bars=20
- `futures.ticker_specs` — ГО из MOEX ISS × 0.5 (FINAM), per-ticker fee
- `dragon_tf_sweep.py`, `ir_si_sweep.py` — правильный M1→TF ресемпл

## 🏆 Чемпионы (реальные FINAM ГО, 2 года данных)

### ⚡ IR Si 1m (абсолютный чемпион)
| Risk | ROI | MDD | PF | WR | Trades |
|:---:|:---:|:---:|:--:|:--:|:-----:|
| 1% | +286% | 6.8% | 3.49 | 46.5% | 853 |
| 2% | +338% | 6.8% | 3.74 | 46.5% | 853 |
| 3% | +685% | 6.8% | 4.37 | 46.5% | 853 |
| **4%** | **+1,649%** | **9.3%** | **4.73** | 46.5% | 853 |
| 5% | +4,162% | 11.8% | 5.00 | 46.5% | 853 |
| **7%** | **+26,801%** | **16.5%** | **5.32** | 46.5% | 853 |

### 🐉 Dragon MM 5m (лучший Дракон)
| Risk | ROI | MDD | PF |
|:---:|:---:|:---:|:--:|
| **5%** | **+140%** | **15.5%** | 2.04 |

### 🐉 Dragon GZ 3m
| Risk | ROI | MDD | PF |
|:---:|:---:|:---:|:--:|
| **7%** | **+40%** | **14.4%** | 1.44 |

## 📊 Портфель (common pool)
| Стратегия | Tick | TF | Risk | ROI | MDD |
|:----------|:----|:--:|:----:|:---:|:---:|
| IR | Si | 1m | 4% | +1,649% | 9.3% |
| Dragon | MM | 5m | 5% | +140% | 15.5% |
| Dragon | GZ | 3m | 7% | +40% | 14.4% |

**Оценка портфеля: ~+2,000% ROI, MDD ~20%**

## 📁 Данные
- `moex.mt5_continuous` — основной источник (M1, rolled futures)
- `moex.mt5_bars` — альтернатива
- PG `futures.ticker_specs` — FINAM ГО, step_price, min_step, fee_entry

## ⚙️ Параметры
- IR Si: TA=0.5%, TT=0.3%, SL=0.7%, TO=12, impulse_bars=12, impulse_pct=0.3
- Dragon MM/GZ: TA=1.5%, TT=0.5%, SL=1%, TO=60, impulse_pct=0.3, lookback=100
- Trend filter: SMA 50 (только для Dragon)
- Commission: 4₽ per contract round-trip
- GO: FINAM (MOEX ISS × 0.5)
- Capital: 200,000 RUB
