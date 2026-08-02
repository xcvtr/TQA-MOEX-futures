"""OI engine for MOEX futures — check_signal() only.

Контрарный OI-сигнал: физлица накопили дисбаланс за день (day_net_fiz)
→ цена отскакивает в противоположную сторону в течение ~1-2 часов.

- day_net ≤ -thr → long (физлица панически продают → отскок вверх)
- day_net ≥ +thr → short (физлица панически покупают → откат вниз)

bar_data expects:
    - day_net: float — накопление нетто-позиции физлиц за день в % от OI
      (отрицательное = физлица продают, положительное = покупают)
    - prc: текущая цена (для entry)
"""

DEFAULT_PARAMS = {
    'thr': 3.0,       # порог |day_net| (%), ниже которого нет сигнала
}


def check_signal(bar_data: dict, ticker: str, params: dict = None) -> dict:
    """Detect OI contrarian signal on MOEX futures.

    Сигнал: |day_net| >= thr → long при продажах, short при покупках.
    """
    if params is None:
        params = DEFAULT_PARAMS

    thr = abs(float(params.get('thr', DEFAULT_PARAMS['thr'])))
    day_net = bar_data.get('day_net')
    if day_net is None:
        return None

    if day_net <= -thr:
        direction = 'long'
        reason = f'oi_fiz_sell_{day_net:.1f}%'
        # чем глубже накопление продаж, тем выше score
        score = round(min(abs(day_net + thr) / 10.0, 1.0), 3)
        if score < 0.05:
            score = 0.05
    elif day_net >= thr:
        direction = 'short'
        reason = f'oi_fiz_buy_{day_net:.1f}%'
        # чем глубже накопление покупок, тем выше score
        score = round(min(abs(day_net - thr) / 10.0, 1.0), 3)
        if score < 0.05:
            score = 0.05
    else:
        return None

    return {
        'ticker': ticker,
        'direction': direction,
        'entry_price': float(bar_data.get('prc', 0)),
        'reason': reason,
        'score': score,
        'strategy': 'oi',
    }
