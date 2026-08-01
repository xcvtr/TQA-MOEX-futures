"""OI engine for MOEX futures — check_signal() only.

Контрарный OI-сигнал: физлица накопили продажи за день (day_net_fiz < порога)
→ цена отскакивает вверх в течение ~2 часов.

bar_data expects:
    - day_net: float — накопление нетто-позиции физлиц за день в % от OI
      (отрицательное = физлица продают)
    - prc: текущая цена (для entry)
"""

DEFAULT_PARAMS = {
    'thr': -5.0,       # порог day_net (%), ниже которого сигнал
}


def check_signal(bar_data: dict, ticker: str, params: dict = None) -> dict:
    """Detect OI contrarian signal on MOEX futures.

    Сигнал: day_net < thr → long (физлица панически продают → отскок).
    """
    if params is None:
        params = DEFAULT_PARAMS

    thr = float(params.get('thr', DEFAULT_PARAMS['thr']))
    day_net = bar_data.get('day_net')
    if day_net is None:
        return None

    # Нужен значащий отрицательный перекос
    if day_net >= thr:
        return None

    # Сила сигнала: чем глубже накопление продаж, тем выше score
    score = round(min(abs(day_net - thr) / 10.0, 1.0), 3)
    if score < 0.05:
        score = 0.05

    return {
        'ticker': ticker,
        'direction': 'long',
        'entry_price': float(bar_data.get('prc', 0)),
        'reason': f'oi_fiz_sell_{day_net:.1f}%',
        'score': score,
        'strategy': 'oi',
    }
