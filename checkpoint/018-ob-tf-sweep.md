# Checkpoint 018 — Order Block TF sweep (H1, H2, H4)

## Что сделано

Per-ticker TF sweep для Order Block (Variant D) — протестированы H1, H2, H4 на всех 15 активных тикерах (2 года данных).

### Результаты sweep

| Тикер | Best TF | WR% | Δ vs H1 | PF | Решение |
|:-----:|:-------:|:---:|:-------:|:--:|:-------:|
| **UC** | **H2** | 80.9 | **+5.1%** | 9.13 | 🛠️ H2 |
| **KC** | **H4** | 80.7 | **+9.2%** 🔥 | 4.14 | 🛠️ H4 |
| **Si** | **H2** | 78.7 | **+4.2%** | 5.26 | 🛠️ H2 |
| **ED** | **H4** | 78.3 | **+4.0%** | 4.02 | 🛠️ H4 |
| **GD** | **H2** | 77.2 | **+7.8%** 🔥 | 4.90 | 🛠️ H2 |
| **GK** | **H4** | 76.5 | **+3.5%** | 6.48 | 🛠️ H4 |
| **YD** | **H4** | 72.9 | **+2.1%** | 4.47 | 🛠️ H4 |
| RM | H1 | 72.9 | — | 4.06 | ✅ H1 |
| RI | H1 | 72.8 | — | 3.45 | ✅ H1 |
| LK | H2 | 74.3 | +2.0% | 3.38 | ✅ H1 (marginal) |
| SBERF | H2 | 74.1 | +1.2% | 3.94 | ✅ H1 (marginal) |
| NA | H4 | 71.7 | +1.0% | 3.02 | ✅ H1 (marginal) |
| RN | H2 | 71.5 | +1.7% | 3.44 | ✅ H1 (marginal) |
| IMOEXF | H2 | 71.5 | +1.3% | 3.29 | ✅ H1 (marginal) |
| MC | H1 | 69.9 | — | 3.29 | ✅ H1 |

**7 из 15 тикеров** с улучшением >2% → переведены на H2/H4.

### Изменения в production

- `trading_bot/ob_engine.py` — `detect_order_block_signals()` читает `config['tf']`, `resample_h1()` принимает параметр `rule`
- `trading_bot/__init__.py` — `OB_TICKERS`: UC→H2, ED→H4, Si→H2, KC→H4, GD→H2, GK→H4, YD→H4
- `trading_bot/cron_scanner.py` — OB использует `days=730`, ob_cutoff=24h (для H4)

### Финальный состав демо (все 5 стратегий)

| # | Стратегия | Тикеры | TF | Entry |
|:-:|:----------|:-------|:--:|:-----:|
| 1 | **Order Block** | UC(H2), ED(H4), Si(H2), RM(H1), KC(H4), NA(H1), GD(H2), RI(H1), LK(H1), SBERF(H1), GK(H4), MC(H1), RN(H1), IMOEXF(H1), YD(H4) | per-ticker | Limit OB level |
| 2 | **VWAP Deviation** | GZ, SR, Si, MC | 5m | Limit extreme |
| 3 | **Mean Reversion** | NM(5m), AF(15m) | per-ticker | Limit extreme |
| 4 | **OI Divergence** | RI, GL, Si | 5m | Limit extreme |
| 5 | **Volume Surge** | HS(5m), DX(15m), HY(H1), BM(H1) | per-ticker | Limit extreme |

Всего: ~28 тикеров, 5 стратегий, 5 разных ТФ (5m, 15m, H1, H2, H4).

### Файлы
- `scripts/tf_sweep_ob.py` — скрипт TF sweep для OB
- `docs/plans/tf_sweep_results/tf_sweep_ob.csv` — полные результаты

### Что дальше
- [ ] Наблюдать сигналы в демо (теперь OB на разных ТФ)
- [ ] Мониторинг: Si пересекается в VWAP + OI + OB
