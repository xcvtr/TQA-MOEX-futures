"""Dragon engine for MOEX futures — check_signal() only.

Паттерн «Дракон»:
1. Шея — сильный импульс (3+ бара)
2. Коррекция — откат 20-50%
3. Горбы — ложный пробой экстремума
4. Хвост — разворот и проход через шею → СДЕЛКА

Адаптировано из TQA-crypto engine/dragon_detect.py для MOEX futures.
"""
import numpy as np


def check_signal(bar_data: dict, ticker: str, params: dict = None) -> dict:
    """Detect Dragon pattern on MOEX futures.

    bar_data expects:
        - bars_list: list of dicts with opn, hi, lo, prc (chronological)
        - prc: current close price
    """
    if params is None:
        params = {
            'impulse_pct': 0.5,       # 0.5% для MOEX (меньше волатильность)
            'retrace_max_pct': 50,
            'hump_extension': 0.2,    # 0.2% пробой
            'lookback': 100,
        }

    bars = bar_data.get('bars_list', [])
    if len(bars) < 30:
        return None

    n = len(bars)
    lookback = min(params['lookback'], n - 20)
    current_bar = bars[-1]

    # --- SHORT DRAGON (impulse UP → fakeout UP → reversal DOWN) ---
    for neck_len in range(3, 12):
        if n - neck_len - 10 < 0:
            continue

        neck_start = n - neck_len - 10
        impulse_bars = bars[neck_start:n - 10]
        pre_impulse = bars[neck_start]['prc']

        impulse_high = max(b['hi'] for b in impulse_bars)
        impulse_up_pct = (impulse_high - pre_impulse) / (pre_impulse + 0.001) * 100

        if impulse_up_pct < params['impulse_pct']:
            continue

        total_range = max(b['hi'] for b in impulse_bars) - min(b['lo'] for b in impulse_bars)
        if total_range <= 0:
            continue

        # Neck peak
        neck_peak = impulse_high
        neck_peak_idx = neck_start + np.argmax([b['hi'] for b in impulse_bars])

        # Retracement
        retrace_found = False
        retrace_low = neck_peak
        for j in range(neck_peak_idx + 1, min(neck_peak_idx + 6, n)):
            if bars[j]['lo'] < retrace_low:
                retrace_low = bars[j]['lo']
                retrace_pct = (neck_peak - retrace_low) / (neck_peak - pre_impulse + 0.001) * 100
                if retrace_pct > 20:
                    retrace_found = True
                    break

        if not retrace_found:
            continue

        impulse_range = neck_peak - pre_impulse
        retrace_depth = neck_peak - retrace_low
        if retrace_depth > impulse_range * params['retrace_max_pct'] / 100:
            continue

        # Humps (false breakout above neck)
        hump_found = False
        for j in range(neck_peak_idx + 6, n):
            if bars[j]['hi'] > neck_peak:
                hump_ext = (bars[j]['hi'] - neck_peak) / neck_peak * 100
                if hump_ext >= params['hump_extension']:
                    hump_found = True
                    break

        if not hump_found:
            continue

        # Tail — close below retrace_low
        if current_bar['prc'] < retrace_low:
            confidence = round(min(impulse_up_pct / 2.0, 1.0), 3)
            return {
                'ticker': ticker, 'direction': 'short',
                'entry_price': current_bar['prc'],
                'reason': f'dragon_short_imp{impulse_up_pct:.1f}%',
                'score': confidence, 'strategy': 'dragon',
            }

    # --- LONG DRAGON (impulse DOWN → fakeout DOWN → reversal UP) ---
    for neck_len in range(3, 12):
        if n - neck_len - 10 < 0:
            continue

        neck_start = n - neck_len - 10
        impulse_bars = bars[neck_start:n - 10]
        pre_impulse = bars[neck_start]['prc']

        impulse_low = min(b['lo'] for b in impulse_bars)
        impulse_down_pct = (pre_impulse - impulse_low) / (pre_impulse + 0.001) * 100

        if impulse_down_pct < params['impulse_pct']:
            continue

        total_range = max(b['hi'] for b in impulse_bars) - min(b['lo'] for b in impulse_bars)
        if total_range <= 0:
            continue

        # Neck low
        neck_low = impulse_low
        neck_low_idx = neck_start + np.argmin([b['lo'] for b in impulse_bars])

        # Retracement up
        retrace_found = False
        retrace_high = neck_low
        for j in range(neck_low_idx + 1, min(neck_low_idx + 6, n)):
            if bars[j]['hi'] > retrace_high:
                retrace_high = bars[j]['hi']
                retrace_pct = (retrace_high - neck_low) / (pre_impulse - neck_low + 0.001) * 100
                if retrace_pct > 20:
                    retrace_found = True
                    break

        if not retrace_found:
            continue

        impulse_range = pre_impulse - neck_low
        retrace_depth = retrace_high - neck_low
        if retrace_depth > impulse_range * params['retrace_max_pct'] / 100:
            continue

        # Humps (false breakdown below neck)
        hump_found = False
        for j in range(neck_low_idx + 6, n):
            if bars[j]['lo'] < neck_low:
                hump_ext = (neck_low - bars[j]['lo']) / neck_low * 100
                if hump_ext >= params['hump_extension']:
                    hump_found = True
                    break

        if not hump_found:
            continue

        # Tail — close above retrace_high
        if current_bar['prc'] > retrace_high:
            confidence = round(min(impulse_down_pct / 2.0, 1.0), 3)
            return {
                'ticker': ticker, 'direction': 'long',
                'entry_price': current_bar['prc'],
                'reason': f'dragon_long_imp{impulse_down_pct:.1f}%',
                'score': confidence, 'strategy': 'dragon',
            }

    return None
