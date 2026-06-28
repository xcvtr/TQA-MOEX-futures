==================================================================
FIZ/YUR DIVERGENCE TEST — MOEX Futures 5m — FINAL RESULTS
Period: 2024-10-01 to 2026-06-28
==================================================================

DATA SOURCE: ClickHouse 10.0.0.60:8123 → moex.prices_5m_oi + moex.prices_5m

TICKERS TESTED: Si, GZ, BR, NG, CR, SR

METHODOLOGY:
  fiz_net = (fiz_buy - fiz_sell) / total_oi
  yur_net = (yur_buy - yur_sell) / total_oi
  dfiz = diff(fiz_net), dyur = diff(yur_net)
  Signal: dfiz and dyur opposite signs (divergence)
  Trade direction: follow yur (institutions)
  Thresholds tested: raw (no threshold), z05, z10, z15 (z-score thresholds)
  Forward returns: 3, 6, 12 bars

==================================================================
RESULTS PER TICKER
==================================================================

TICKER    BEST WR    METHOD    FWD    SIGNALS    AVG_RET    RESULT
-------   --------   -------   ---    -------    -------    ------
Si        53.6%      z15        6        278     -0.0042%   NO ✅(WR)❌(neg ret)
GZ        49.6%      z05        3       9974     +0.0016%   NO ❌
BR        49.0%      z10       12       5782     -0.0062%   NO ❌
NG        49.3%      z12       12      50873     +0.0155%   NO ❌
CR        51.2%      z05       12       1988     +0.0210%   NO ❌
SR        52.6%      z15       12       2350     +0.0226%   YES ✅ (marginal)

==================================================================
DETAILED NOTES
==================================================================

Si (USD/RUB):
  - Raw divergence: WR ~49% (no signal)
  - z15 threshold (extreme moves): 53.6% WR @ 6 bars
  - BUT avg return is NEGATIVE (-0.0042%) → wins are tiny, losses are large
  - Not a viable signal despite win rate

SR (Sugar):
  - z15 threshold: 52.6% WR @ 12 bars, +0.0226% avg return
  - ~2350 signals from 54721 bars (4.3% of bars)
  - This is the only ticker where WR >= 52% AND avg ret positive
  - Very marginal — barely above 52% threshold

GZ (Natural Gas):
  - All methods below 50% WR
  - Institutions show no edge on divergence signals

BR (Brent):
  - WR consistently 47-49%, below random
  - Negative avg returns — following institutions hurts performance

NG (Natural Gas - London):
  - WR 47-49%, no edge
  - Slight positive avg return but not significant

CR (Crude Oil):
  - WR 47-51%, all below 52%
  - Small sample (~18K rows, starts Dec 2024)

==================================================================
CONCLUSION: NO TRADABLE SIGNAL FOUND
==================================================================

The FIZ/YUR divergence strategy on 5m MOEX futures shows NO reliable
predictive power across Si, GZ, BR, NG, CR, SR.

Key findings:
1. FIZ and YUR are almost ALWAYS on opposite sides (raw divergence on
   ~99% of bars) → signal is too noisy without threshold filtering
2. Even with z-score thresholding (z05/z10/z15), WR never exceeds 52%
   for any ticker except:
   - Si z15 fwd6: 53.6% BUT negative avg return (not viable)
   - SR z15 fwd12: 52.6% — barely passes, very marginal
3. Average returns are near zero across all methods
4. "Follow institutions" shows no edge — yur flow direction has no
   predictive power for 5m forward returns on any tested ticker

Files created:
  /home/user/fiz_yur_test.py      — v1 basic test
  /home/user/fiz_yur_test_v2.py   — v2 with magnitude thresholds
  /home/user/fiz_yur_test_v3.py   — v3 with lag/cooldown/ranking
  /home/user/fiz_yur_final.py     — final clean run with all 4 thresholds
