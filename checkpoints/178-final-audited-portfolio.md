---
title: "Финальный портфель MOEX futures — IR Si + Dragon MM/GZ"
checkpoint: 178
date: 2026-07-23
tags: [checkpoint, tqa-moex-futures, final, portfolio]
---

# Checkpoint 178: Финальный аудированный портфель

## 📊 Портфель
| Стратегия | Тикер | TF | Risk | ROI | MDD | PF |
|:----------|:------|:--:|:----:|:---:|:---:|:--:|
| ⚡ IR | **Si** | 1m | 4% | +1,649% | 9.3% | 4.73 |
| 🐉 Dragon | **MM** | 5m | 5% | +140% | 15.5% | 2.04 |
| 🐉 Dragon | **GZ** | 3m | 7% | +40% | 14.4% | 1.44 |

**Common pool: ROI +1,310% | MTM MDD 8.42% | 1,363 сделок**

## ✅ Аудит
- Look-ahead: чисто (close_hist без текущего бара)
- Slippage: 1 tick на entry
- Volume cap: 10% от M1 объёма
- ГО: MOEX ×0.5 (FINAM/КСУР), per-ticker в ticker_specs
- Комиссия: 4₽ per contract (scalper-приближение, Si 3.82, MM 1.52, GZ 1.96)
- Торговые часы: MOEX 10-18:45 MSK = 15-23:45 IRK
- Данные: mt5_continuous (FINAM production)
- KNUR: не используется (фьючерсы, только ГО)

## 🔧 Ключевые изменения сессии
1. `backtest.py` — PortfolioEngine + `--tf-map`, `--strategy`, `--params`
2. `engine.py` — close_entry, detect каждый M1 бар, hist=20
3. `ticker_specs` — ГО из MOEX ISS ×0.5, fee_entry, step_price GD
4. `dragon_tf_sweep.py` — M1→TF resample для правильного детекта
5. `ir_si_sweep.py` — IR sweep с FINAM ГО
6. `portfolio_run.py` — common pool 3 стратегии

## 📁 Файлы
- `strategies/dragon/scripts/dragon_tf_sweep.py` — sweep Dragon по ТФ
- `strategies/dragon/scripts/ir_si_sweep.py` — sweep IR Si
- `strategies/dragon/scripts/portfolio_run.py` — портфельный backtest
- `checkpoints/178-final-portfolio-calmar.md` — чекпойнт
- `RULES.md` — правила фреймворка
